# ADR 0007：Session 事件溯源——append-only 日志是 session 的唯一真相

状态：已接受（2026-08-22，方向讨论拍板；同日实施）

## 背景

邮件是磁盘事实（mailbox 即队列，先持久化后处理），但认知循环的 `AgentSession` 是纯内存态：进程重启后，进行中的 session 整体蒸发——context、迭代计数、工具结果、后台工具任务全丢。邮件侧靠「未读集合 + 启动扫描」补偿，会话侧没有任何真相源；traces 落盘是观测而非事实。外部调研（[docs/design/deepseek-harness-review.md](../design/deepseek-harness-review.md) 第 1 条，deepseek-harness 的 session 子系统）给出已验证的范式：session 是 append-only 事件日志，模型历史由日志推导，崩溃恢复靠重放。

这与「行为即数据」哲学同构：**认知过程也是数据**。

## 决策

每个 session 一个 append-only JSONL 事件日志，路径 `~/.tlon/{agent_id}/sessions/{session_id}.jsonl`（session_id = `{agent_id}-{mail.id}`，文件名天然安全）。日志是 session 状态的**唯一真相**，重启后重放恢复。实现为 [src/tlon/agents/session_journal.py](../../src/tlon/agents/session_journal.py) 的 `SessionJournal`，只在引擎层（`LoopDriver` / `SessionScheduler` / 装配点）使用——stage 纯度不变（`scripts/check_stage_purity.py` 的适用边界不收口）。

每行一个事件：`{seq, ts, type, ...payload}`（seq 为文件内递增序号，ts 为 ISO8601）。

### 事件集

| type | payload | 写入点 |
|---|---|---|
| `session/start` | session_id, agent_id, mail_id, trigger_content, carrier, context（初始全量消息列表） | `step_session` 首次构建 context 后、首个 stage 前 |
| `context/append` | messages | `LoopDriver._apply_outcome`（context_appends 非空时） |
| `scratch/update` | updates | `_apply_outcome` |
| `iteration` | value（绝对值） | `_apply_outcome` 的 advance_iteration；无 act 程序的引擎代推进 |
| `errors` | value | `_apply_outcome` 的 record_error |
| `tokens` | value（累加后绝对值，ADR 0023） | `_apply_outcome` 的 outcome.token_usage > 0 时 |
| `llm/response` | response dict | `_apply_outcome` 的 outcome.response 非 None 时 |
| `tool/dispatch` | tool_calls | `_apply_outcome` dispatch 分支，创建后台 task 前 |
| `tool/result` | tool_call, result（每个工具一条） | `execute_one_tool` 拿到结果即写（在后台 task 内） |
| `tool/collect` | tool_call_ids | `collect_session_results` / `resume_collect` 收割时 |
| `state` | state 值 | mark_waiting / mark_active 经 `_apply_outcome` |
| `session/end` | reason: complete / max_iterations / max_tokens / until / error / timeout | 各完成路径（until 的 detail 带 error_limit / iteration_limit） |

### 不变式：模型可见 ⇒ 已入日志

context 仅两个写入点（初始构建、`_apply_outcome`），都先 append 日志成功再改内存；journal 写失败抛异常、不动内存。测试以 `derive_context(session_id) == session.context` 断言这条不变式。

### 重放规则

- context = `session/start.context` + 全部 `context/append.messages` 依序拼接。
- `_all_tool_results` = 出现在某个 `tool/collect` 的 id 对应的 `tool/result`（按收割顺序）。
- **孤儿工具不重演**：dispatch 后无对应 collect 的工具——已落盘的 `tool/result` 保留；dispatch 了但无 result 的，恢复时补一条合成 `ToolResult(success=False, error="interrupted: process restart")` 并把该合成结果 append 进日志（自愈，二次重放看到已愈合的日志）。这些合并为恢复后首轮 collect 的输入，直接调 `LoopDriver.resume_collect`（即 `act_stage_collect` + `_apply_outcome`），不再执行工具本身。
- 日志含 `session/end` → 视为已完成：scheduler 仅 mark_mail_read，不重跑。
- **副作用不重演**：重放只重建数据字段；mails / save_interaction / on_complete 的效果不重新执行（崩溃前发没发，日志不追溯——邮件层自身的至少一次语义兜底）。
- 读侧坏行容忍：逐行 JSON，坏行（如崩溃留下的截断尾行）log warning 跳过，与 mailbox 读侧同范式。
- 崩溃窗口特判：状态停在 WAITING 但没有任何待收割工具（`tool/collect` 已落盘、state 事件未落盘的窗口），恢复为 ACTIVE 而不是干等到超时。

### 与 mail cursor 的关系

邮件层的「未读集合 + 启动扫描」不变，仍是唤醒与补处理的唯一补偿路径；session 日志叠在其上：同一封邮件再次进入调度器时，先查日志——有 `session/end` 只推进 cursor 不重跑，有未收割工具则恢复续跑，无日志才新建 session。两层各自幂等，合起来是「至少一次投递 + 恰好一次执行效果」的近似：效果至多执行一次（已完成的绝不重跑），未完成的从最后一个一致点继续。

## 后果

- **进程崩溃/重启不再丢 session**：认知过程可恢复，孤儿工具以合成 interrupted 结果显式收口，模型上下文中可见。
- **`AgentSession` 仍是易失投影**：内存对象可以随时丢弃，真相在日志；`runtime.md` 的「不持久化会话」论断被本决策取代。
- **写放大可控**：每个事件一行 JSONL，无快照、无压缩；session 完成即不再增长。清理策略（归档/删除老日志）留待后续治理决策，与 ADR 0006 的「存储只增不减」同源。
- **测试基建**：`derive_context` 为断言而生；崩溃恢复端到端测试用「工具派发后丢弃内存态 → resume → resume_collect 续跑」模拟 kill。
- **引擎层签名变化**：`LoopDriver` / `SessionScheduler` 构造注入同一 `SessionJournal`（永远开启，不做可选）；`CognitiveCapabilities` 增加 `tlon_paths` 字段，由注册表装配。

## 演进补注

- **2026-08（ADR 0008）**：事件集新增 `context/compact`（kind, reason, tokens_before/after, 压缩后全量 context；重放时整体替换 context），上表未回填。
- **2026-09（ADR 0017 阶段 4）**：`session/start` 的 payload 新增显式 `anchor_len` 字段（knowledge 段移出 anchor 后，重放无法再从 context 长度推导出锚界）；重放优先读显式字段，旧事件回退 `len(context)`。
- **2026-09（ADR 0009/0017）**：「只在引擎层使用」的边界已放宽——journal 现有引擎外读者：MemoryManager 蒸馏触发（经 `SessionLogReader` Protocol 结构化注入，import-linter 约束不变）、context_builder 的 thread-recap、退休仪式。
- **2026-10（ADR 0023）**：事件集新增 `tokens`（session 级 token 预算消耗，绝对值）；`session/end` reason 新增 `max_tokens`。上表已回填。
