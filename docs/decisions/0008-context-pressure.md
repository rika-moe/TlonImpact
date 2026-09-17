# ADR 0008：上下文压力管理——存储不动，视图分级减压

状态：已接受（2026-08-22，方向讨论拍板；issue  记录调研报告）；2026-09-17 经 ADR 0017（阶段 4）修订 anchor 语义：`(knowledge)` 段移出 anchor——knowledge 消息固定为整个 context 的最后一条（当前邮件之后），anchor 只含其余 sections + 触发邮件，`session/start` 事件显式携带 `anchor_len` 字段

## 背景

`session.context`（ReAct 循环的模型历史）在 session 内只增不减：无 token 计数、无截断、无压缩。唯一的间接上限是 `max_iterations` 轮数。超限的实际结局很糟：provider 的 context-length 4xx 被 `llm_client` 当作瞬时错误重试 3 次，然后 until 触发 error_limit，回一句「抱歉，处理邮件时出现了错误」——浪费 4 次请求后会话失败，无恢复手段。

业界调研（issue ）的共性结论：

1. **存储与取回分离**：compact 只改发给模型的视图，绝不动持久层（Letta「eviction ≠ deletion」、OpenAI Agents SDK 取回侧裁剪）。Tlon 的 session 事件溯源（ADR 0007）天然满足——journal 是全量真相，compact 只是 context 的一次变换。
2. **减压分梯度**：tool result 清理是最便宜最安全的第一级（Claude Code）；LLM 摘要是最后手段（compact 本身是一次大请求）。
3. **token 估算 + 显式阈值**，触发点在每次 LLM 调用前。
4. **锚点保留**：任务定义（对 Tlon = 初始构建块，含触发邮件原文）永远保留；tool_call 与其 result 消息永不拆对。

## 决策

### 阈值

`LLMConfig` 新增 `context_window: int = 262144`（kimi-k2.5 的窗口，随 agent.yaml 持久化、可按模型覆盖）。触发阈值（token）：

```
threshold = max(context_window - 40_000, context_window * 0.8)
```

与 Cline 的 `max(w-40k, 0.8w)` 一致：固定预留 40k 给 system prompt、工具 schema 与输出，百分比兜底大窗口。

### token 估算

不引 tiktoken 依赖，用字符启发式：CJK 字符（\u4e00-\u9fff、\u3000-\u303f、\uff00-\uffef）按 1 token/字，其余按 4 字符/token。宁高勿低的偏差是可接受的（早触发比晚触发安全）。

### 触发点与减压梯度

引擎层 `LoopDriver.step_session` 在每次 step 的 stage 向量运行前检查（等价于 dsh 的 `agent/pre-step`、LangGraph 的 `@before_model`）。超阈值时按梯度减压：

1. **prune（零 LLM 成本）**：保留窗口之外的所有 `role=tool` 消息内容原位替换为短占位符（保留 `tool_call_id`，配对不破）。重新估算，达标即停。
2. **summary（LLM 兜底）**：把「锚点之后、保留窗口之前」的中段消息摘要成一条 `[前文摘要]` user 消息。running summary：若中段开头已有上一轮的 `[前文摘要]` 消息，其内容并入本次摘要输入，生成的新摘要原位替换——摘要滚动更新，不无限堆叠。

锚点 = 初始构建块（lessons + skills + current-task + mail-history + thread-recap + 触发邮件，`session/start` 事件显式携带的 `anchor_len`，记为 session 的 `anchor_len`）：永不压缩、永不删除。**修订（ADR 0017 阶段 4，2026-09-17）**：`(knowledge)` 段不再属于锚点——它曾冻结进 anchor（本段原文「knowledge + mail-history + 触发邮件」），记忆越积 anchor 越大，超阈值时中段为空、压缩无路可退（）；现 knowledge 消息固定为整个 context 的最后一条（当前邮件之后），是普通可压缩中段。旧日志没有 `anchor_len` 字段，重放时回退为 `session/start.context` 全长（旧行为）。保留窗口 = 最近 20 条消息，且不切断 tool_call/tool 配对（窗口首条若是 tool 消息则向前扩展到其 assistant 消息）。

### 落日志（不变式不破）

compact 是 context 的一次变换，按 ADR 0007 必须先落日志再改内存。新事件类型：

```
context/compact {kind: "prune"|"summary", reason, tokens_before, tokens_after, context: <压缩后全量>}
```

重放规则：`context/compact` 将已累计的 context 整体替换为事件携带的全量——重放后仍是压缩态，与运行态逐字节一致。取全量快照而非增量指令，换取重放逻辑的平凡性（压缩后的 context 由定义小于阈值，体积可控）。

### 紧急路径

`llm_client` 识别 context-length 错误（`BadRequestError` 且 code/消息含 context length 特征）为独立的 `ContextWindowExceededError`，**不重试**直接抛出。think/plan/critique stage 不吞此异常；`step_session` 捕获后强制执行一次完整梯度（prune + summary，保留窗口收紧到 10 条）并重试该 stage 一次；再失败才走原有 record_error 路径。

## 后果

- 超长会话不再以「重试 3 次 + 报错邮件」收场；prune 一级覆盖大多数场景且零 LLM 成本。
- journal 仍是唯一真相：压缩历史可从 `context/compact` 事件审计（tokens_before/after），重放保真。
- 摘要质量依赖 prompt 调优，遵循 Anthropic 建议「先保 recall 再调 precision」；摘要器失败的回退是 prune 占位 + 保留窗口原文。
- 明确不做：token 精确计数（依赖不值得）、memory 层正名（PR-B 另行）、摘要触发挪到空闲时（邮件系统 idle 期利用，后续优化）。
