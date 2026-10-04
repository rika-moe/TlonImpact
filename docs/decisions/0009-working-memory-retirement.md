# ADR 0009：Working Memory 退役——session journal 是唯一交互真相，摘要流 journal → episodic 直连

状态：已接受（2026-08-22，方向讨论拍板；同日实施）（2026-09-17 起被 ADR 0017 部分取代：摘要流替换为蒸馏管线）

## 背景

ADR 0007 落地后，系统里存在两份交互真相：session journal（append-only 事件日志，认知过程的唯一真相）与 working memory（`working/interactions.jsonl`，由 `EffectExecutor.save_interaction` 在会话完成时二次落盘）。双写带来三个实锤问题：

1. **重复与漂移**——同一段交互写两处，格式各自演化；journal 是真，working memory 是它的劣质拷贝。
2. **fallback 摘要冒充 bug**——working memory 里存的「用户消息」其实是 agent 自己的最终回复（`EffectExecutor.save_interaction` 用 `final_response` 伪造 message），LLM 不可用时的机械摘要于是把 agent 的回信当用户输入引用。journal 里有真 trigger（`session/start.trigger_content`），working memory 没有。
3. **触发逻辑悬空**——容量触发（working 满）与空闲触发（`idle_timeout_minutes`）都建立在滚动窗口语义上；窗口语义随 working memory 一起失去意义。

## 决策

退役 working memory 层。session journal 成为唯一交互真相；摘要流改为 journal → episodic 直连：（**2026-09-17 注：以下「摘要流」各条已被 ADR 0017 的蒸馏管线实际替换，具体过时点见文末演进补注；退役 working memory 的决策本身与删除清单仍然有效。**）

- **触发点**：scheduler 的 session 回收点（`session.is_completed` 分支，`_ensure_session_end` 之后）。每回收一个 session，检查「已完成且未被任何 Summary 引用的 session 数」；达到 `summary_window`（MemoryConfig 新字段，默认 3）就把这批 session 全部摘要。
- **去重**：扫 `summaries.jsonl` 最近 20 条（= `episodic_max_summaries` 上限）收集全部 `source_session_ids`，与 `SessionJournal.list_completed()`（glob sessions 目录、过滤含 `session/end` 的日志）求差集。
- **摘要输入**：Summarizer 改吃「session 事件投影」（`SessionProjection`）——真 trigger_content + 每轮 `llm/response` 的 content + 每条 `tool/result` 的一行摘要。投影由 `MemoryManager` 从 journal 事件重建；fallback 摘要因此引用的是真 trigger，冒充 bug 随之消除。
- **Summary 记录 `source_session_ids`**（替代 `source_interactions`）；`from_dict` 容忍旧 `summaries.jsonl` 行缺该字段（旧行已在用户磁盘上）——这是本次唯一的兼容豁免。
- **批量语义**：一次摘要产出一条 Summary，覆盖整批 pending session；摘要不删 journal（存储只增不减，同 ADR 0006/0007）。
- **空闲触发整体删除**：`check_idle_timeout` 与 `_last_interaction_time` 移除。摘要只挂在 session 完成节拍上——agent 不活动，记忆就不动，与「事件驱动、睡觉零成本」一致。
- **反思引擎**：`note_interaction` 的调用从 `EffectExecutor.save_interaction` 挪到 scheduler 回收点（语义变为「完成 session 计数」）；digest 的 `working_count` 改为「未摘要 session 数」；删除 `consolidate` 操作与 prompt 里「工作记忆 >= 5 条建议 consolidate」的硬编码——摘要触发已是自动的，不需要反思来催。
- **分层约束**：`tlon.memory` 不能 import `tlon.agents`（import-linter），故 manager 定义 `SessionLogReader` Protocol（`list_completed` + `read_events`），`SessionJournal` 结构化满足，由 scheduler 注入。

### 删除清单

- `memory/working_store.py`（整个文件）、`memory/models.py` 的 `Interaction` 模型、`memory/base.py` 的 `MemoryStore` 抽象（只剩 episodic 一个实现，`EpisodicMemoryStore` 去继承，ABC 委托方法随之删除）。
- `MemoryManager`：`add_interaction` / `_store_interaction` / `set_session_id` / `save` / `get_history_text` / `get_summary_text` / `get_facts_text` / `check_idle_timeout`；knowledge 块的 `recent_working` 部分（`ContextBlock.block_type` 收窄为 `episodic_summaries | key_facts`）。
- `tools/memory/compact.py`（整个文件）+ 注册点 + system prompt 里教模型用 compact 的段落——主动压缩没有存在意义：压缩是系统自动的，且输入不再是「当前工作记忆」。
- spec 字段 `:save-interaction` 全链：`member_spec.py` 解析/校验、`behavior_templates.py` 模板、`write_prototype` 提示、原型库 behavior.tlon、`act.py` 的 outcome 设置、`loop_driver`/`executor` 传递链。
- MemoryConfig 字段：`working_max_items` / `working_summary_window` / `context_max_working` / `idle_timeout_minutes`；新增 `summary_window = 3`。旧 config.json 的 `working`/`triggers` 段读取时自然忽略（未知段不读），无需 migration。

### 偏差记录（实施时发现的设计/代码冲突）

设计删除清单要求 `StageOutcome.save_interaction` 字段全删，但该字段同时承担 on-complete 动作环境 `result`（最终回复文本，dsl-reference 已文档化的 DSL 表面）的传递。实施时将该字段改名为 `final_reply`（语义收窄为「on-complete 的 result 输入」，不再承载任何记忆持久化），`result` 环境绑定保持不变。记忆持久化链路本身（executor.save_interaction 方法、spec 门控）按设计全删。

## 后果

- 交互只有一处真相：journal。摘要、重放、审计读同一份日志。
- fallback 摘要引用真 trigger；LLM 摘要的输入也不再经过「agent 回信冒充用户输入」的污染。
- 摘要从「滚动窗口满/空闲超时」变为「N 个已完成 session 成批」：触发语义与 journal 的完成事实对齐，不再依赖内存态时钟。
- 旧 summaries.jsonl 行可加载（缺 `source_session_ids` 按空集处理），但这些旧摘要覆盖的 session 不参与去重——极端情况下对应 session 会被重新摘要一次，可接受。
- `rememberFact`/反思记录的事实 `session_id` 置空字符串——working memory 退役后 manager 不再持有「当前 session」概念；工具侧的会话归属如需恢复，得从执行上下文另接，当前无消费方。
- working/interactions.jsonl 残留文件成为死数据，不读不删（不保留向后兼容的另一面：不做清理逻辑；用户可手删）。
- 明确不做：journal 的归档/清理（沿用 ADR 0007 的留待治理）；进一步的记忆调研结论另行跟进。

## 演进补注

- **2026-09-17（ADR 0017）**：摘要流被蒸馏管线整体替换，正文以下细节过时——去重机制（扫 summaries.jsonl 判重 → 标量时间戳水位线 `distillation.json`）；`Summary` 模型与 summaries.jsonl（整体删除，旧文件不读不迁，「旧行可加载」的后果条不再成立）；`summary_window`（改名 `distillation_window`）；批量语义（一次摘要一条 Summary → 一次蒸馏多条 claim）；`ContextBlock.block_type`（进一步收窄为单一 `claims`）；rememberFact（语义改为「立即写一条 claim」，`source_session_ids=[]`，走冲突判定）。工作/情景两层记忆的说法同时作废，现状见 docs/design/memory.md。
