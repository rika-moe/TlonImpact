# ADR 0023：Session 级 token 预算——会话循环的第二条停机线

> 阅读定位：本文保留当时的选择与理由；当前设计见 [runtime](../design/runtime.md)。当前适用范围：Session token 预算停机线。完整关系见 [决策索引](README.md)。

状态：已接受（2026-10-07 拍板；同日实施）

后续修订：[ADR 0040](0040-unsent-drafts-and-budget-notices.md) 删除资源耗尽时的 LLM 蒸馏回信，改为基于真实工具回执的确定性通知；本文中的终止蒸馏描述仅保留为历史。

## 背景

会话循环的引擎硬顶只有迭代轮数 `:max-iterations` 一条线。研究类任务（developer / data-analyst 这类 L2 成员的典型负载）每轮 token 消耗差异极大：10 轮硬顶对轻量任务是浪费的上限，对重度任务则是真实约束——长上下文 + 多工具结果的任务往往在远未触顶时就把 token 烧穿预算，而另一些任务则被 10 轮硬顶在仍有充足预算时截停。轮数不是资源的公平度量，token 才是。

## 决策

1. **双停机线，取或**：会话配方新增必填字段 `:max-tokens`（session 级累计 token 预算），与 `:max-iterations` 并列；引擎在 `step_session` 的 pre-until 检查处任一到顶即终止（先到先停），`until` 表达式无法覆盖。
2. **计量口径**：session 内每次经 `StageServices.think` 的 stage LLM 调用（think / critique / plan），取 `response.usage.total_tokens`，缺失回落 `prompt_tokens + completion_tokens`，`usage is None` 计 0。计量是 effects-as-data 的延伸：stage 在 `StageOutcome.token_usage` 声明用量，引擎在 `_apply_outcome` 先 journal（新事件 `tokens`，value 为累加后绝对值，与 `iteration`/`errors` 同模式）再改内存；崩溃恢复由 replay 还原（ADR 0007）。
3. **v1 不计入 compaction 摘要调用与 distill 调用**：compaction 摘要是引擎自救（ADR 0008），把它计入预算会让「快触顶 → 压缩 → 压缩本身烧 token → 触顶」成为必然螺旋；distill 是终止路径的一部分，发生在停机判定之后，天然不在预算内。
4. **终止路径复用**：token 到顶走与 max_iterations 相同的 `_distill_reply` 蒸馏回信（蒸馏 prompt 文案泛化为「内部资源上限（迭代轮数或 token 预算）」）；`session/end` reason 为 `max_tokens`。固定兜底文案与 配方 `:replies` 键不变——token 到顶复用 `iteration_limit` 措辞（对用户而言两者是同一语义：想太久/花太多，请简化问题）。
5. **DSL 同步**：loop 语境新增 `token-limit-reached?` 谓词（`state.token_usage >= limits.max_tokens`，缺键安全返 False），`LoopStateEnv`/`LoopLimitsEnv` 增加对应字段；`_until_reached` 的 EvalError 引擎默认检查同样加 token 线。
6. **原型数值**（token 预算是真实约束线，迭代轮数退为安全网）：developer / data-analyst 的 `:max-iterations` 提至 100、`:max-tokens 8000000`；deep-thinker 提至 300 轮、10000000；fast-responder 10 轮、1000000（与默认成员持平，差异在 context 精简）；其余（archivist / ops / pm / product / scribe / support）10 轮不变、1000000。

## 否决的备选

- **动态 limit**（由 LLM 或运行中信号调整预算）：没有可靠的决策者——让被预算约束的模型自己裁定预算是循环授权；且破坏停机线的可预测性：「任一到顶即停」的前提是两条线都是加载期常量。
- **以 context window 占用当 limit**：非单调——压缩（ADR 0008）会让占用回落，无法保证停机；且它的角色已经是压缩触发器，一数两用会让两个机制互相踩阈值。

## 后果

- `SessionRecipe.max_tokens` 必填无默认值：所有 behavior.tlon 与测试内联 spec 必须显式声明预算（total spec 哲学的延伸——资源约束是基因型，不是部署细节）。
- 事件溯源事件集新增 `tokens`（ADR 0007 已回填）；`SessionReplay` / 崩溃恢复同步。
- 原型差异的语义变化：developer / data-analyst 的迭代轮数从真实约束退为安全网，token 预算接管真实约束角色——研究类任务不再被 10 轮硬顶截停。
- 观测面：硬顶日志新增 `session_max_tokens`（带 `token_usage`/`max_tokens` 字段），与 `session_max_iterations` 对齐。
