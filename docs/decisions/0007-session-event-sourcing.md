# ADR 0007：Session 事件溯源——append-only 日志是 session 的唯一真相

状态：已接受（2026-08-22，方向讨论拍板；同日实施）

## 背景

邮件是磁盘事实（mailbox 即队列，先持久化后处理），但认知循环的 `AgentSession` 是纯内存态：进程重启后，进行中的 session 整体蒸发——context、迭代计数、工具结果、后台工具任务全丢。邮件侧靠「未读集合 + 启动扫描」补偿，会话侧没有任何真相源；traces 落盘是观测而非事实。外部调研（deepseek-harness 的 session 子系统）给出已验证的范式：session 是 append-only 事件日志，模型历史由日志推导，崩溃恢复靠重放。

这与「行为即数据」哲学同构：**认知过程也是数据**。

## 决策

每个 agent 一个 append-only SQLite session 数据库，路径 `~/.tlon/{agent_id}/sessions.sqlite3`（ADR 0032 取代原「每 session 一个 JSONL 文件」的物理决策）；`sessions` 表保存 start/end 生命周期元数据与 `last_seq`，`events` 表按 `(session_id, seq)` 保存完整事件。数据库是 session 状态的**唯一真相**，重启后只重放指定 session 的事件恢复。实现为 [src/tlon/agents/session_journal.py](../../src/tlon/agents/session_journal.py) 的异步 `SessionJournal` 与 [src/tlon/agents/session_store.py](../../src/tlon/agents/session_store.py) 的事务存储，只在引擎层（`LoopDriver` / `SessionScheduler` / 装配点）使用——stage 纯度不变（`scripts/check_stage_purity.py` 的适用边界不收口）。session_id = `{agent_id}-{uuid 前缀}`，每 session 一个全新 uuid（ADR 0028 决策 8）。

事件结构仍为 `{seq, ts, type, ...payload}`：seq 在每个 session 内从 0 递增，ts 为 ISO8601。每次 append 在一个事务里插入事件、推进 `last_seq` 并更新 start/end 元数据，提交成功后才允许改内存。

### 事件集

| type | payload | 写入点 |
|---|---|---|
| `session/start` | session_id, agent_id, responsibility（责任集 mail id 快照，ADR 0028——已取代原 `mail_id`）, trigger_content, carrier, context（初始全量消息列表） | `step_session` 首次构建 context 后、首个 stage 前 |
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

context 仅两个写入点（初始构建、`_apply_outcome`），都先 append 并成功提交再改内存；journal 写失败整体回滚并抛异常、不动内存。测试以 `derive_context(session_id) == session.context` 断言这条不变式。

### 重放规则

- context = `session/start.context` + 全部 `context/append.messages` 依序拼接。
- `_all_tool_results` = 出现在某个 `tool/collect` 的 id 对应的 `tool/result`（按收割顺序）。
- **孤儿工具不重演**：dispatch 后无对应 collect 的工具——已落盘的 `tool/result` 保留；dispatch 了但无 result 的，恢复时补一条合成 `ToolResult(success=False, error="interrupted: process restart")` 并把该合成结果 append 进 journal（自愈，二次重放看到已愈合的事件）。这些合并为恢复后首轮 collect 的输入，直接调 `LoopDriver.resume_collect`（即 `act_stage_collect` + `_apply_outcome`），不再执行工具本身。
- 已提交 `session/end` → 视为已完成：绝不重跑（ADR 0028 起引擎侧不再有任何 cursor 推进——标读是 agent 自己的显式行为）。
- **副作用不重演**：重放只重建数据字段；mails / save_interaction / on_complete 的效果不重新执行（崩溃前发没发，journal 不追溯——邮件层自身的至少一次语义兜底）。
- `read_events` / `resume` 只按 seq 读取指定 session；SQLite 事务取代 JSONL 半行、坏行跳过和按全文件行数恢复 seq 的模型。
- 崩溃窗口特判：状态停在 WAITING 但没有任何待收割工具（`tool/collect` 已落盘、state 事件未落盘的窗口），恢复为 ACTIVE 而不是干等到超时。

### 与 mail cursor 的关系

邮件层的「未读集合 + 启动扫描」仍是唤醒的补偿路径；session journal 叠在其上（ADR 0028 起，调度器不再有收件队列）：boot 时先经 `find_unfinished` 只查生命周期元数据——已开始且无 `session/end` 的 session 恢复续跑（责任集从该 session 的事件读回，有未收割工具则经 `resume_collect`），已完成的不读事件、绝不重跑；之后才响应唤醒信号（可唤醒未读 > 0 才开新 session）。两层各自幂等，合起来是「至少一次投递 + 恰好一次执行效果」的近似：效果至多执行一次（已完成的绝不重跑），未完成的从最后一个一致点继续。

## 后果

- **进程崩溃/重启不再丢 session**：认知过程可恢复，孤儿工具以合成 interrupted 结果显式收口，模型上下文中可见。
- **`AgentSession` 仍是易失投影**：内存对象可以随时丢弃，真相在 journal；`runtime.md` 的「不持久化会话」论断被本决策取代。
- **写放大可控**：事件只追加，无快照、无压缩；session 完成即不再增长。清理策略（归档/删除老事件）留待后续治理决策，与 ADR 0006 的「存储只增不减」同源。
- **测试基建**：`derive_context` 为断言而生；崩溃恢复端到端测试用「工具派发后丢弃内存态 → resume → resume_collect 续跑」模拟 kill。
- **引擎层签名变化**：`LoopDriver` / `SessionScheduler` 构造注入同一 `SessionJournal`（永远开启，不做可选）；`CognitiveCapabilities` 增加 `tlon_paths` 字段，由注册表装配。

## 演进补注

- **2026-08（ADR 0008）**：事件集新增 `context/compact`（kind, reason, tokens_before/after, 压缩后全量 context；重放时整体替换 context），上表未回填。
- **2026-09（ADR 0017 阶段 4）**：`session/start` 的 payload 新增显式 `anchor_len` 字段（knowledge 段移出 anchor 后，重放无法再从 context 长度推导出锚界）；重放优先读显式字段，旧事件回退 `len(context)`。
- **2026-09（ADR 0009/0017）**：「只在引擎层使用」的边界已放宽——journal 现有引擎外读者：MemoryManager 蒸馏触发（经 `SessionLogReader` Protocol 结构化注入，import-linter 约束不变）、context_builder 的 thread-recap、退休仪式。
- **2026-10（ADR 0023）**：事件集新增 `tokens`（session 级 token 预算消耗，绝对值）；`session/end` reason 新增 `max_tokens`。上表已回填。
- **2026-10（ADR 0028 决策 8）**：session 身份从 `{agent_id}-{mail.id}` 变为每 session 全新 uuid；`session/start` 去掉 `mail_id`、新增 `responsibility`（唤醒时的可唤醒未读 id 快照，供崩溃恢复后的链式唤醒判定读回）；引擎不再在任何路径推进收件箱 cursor（标读归 agent 显式管理，`markRead` 工具）。上文正文与事件表已回填。
- **2026-10-17（ADR 0032）**：物理存储从每 session 一个 JSONL 改为每 agent 一个 SQLite；事件与 start/end、`last_seq` 同事务提交，生命周期查询不再扫描事件。旧 `sessions/*.jsonl` 原样保留为离线档案，不导入、不双写、不读取 fallback。上文已按现行口径回填。
