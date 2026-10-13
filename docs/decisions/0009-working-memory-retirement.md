# ADR 0009：Working Memory 退役——session journal 是唯一交互真相

状态：已接受（2026-08-22，方向讨论拍板；同日实施）（2026-09-17 起被 ADR 0017 部分取代：摘要流替换为蒸馏管线）

## 背景与决策

ADR 0007 落地后，系统里存在两份交互真相：session journal（append-only 事件日志，认知过程的唯一真相）与 working memory（`working/interactions.jsonl`，由 `EffectExecutor.save_interaction` 在会话完成时二次落盘）。双写重复漂移（journal 是真，working memory 是劣质拷贝），且 working memory 里存的「用户消息」其实是 agent 自己的最终回复——fallback 摘要把 agent 的回信当用户输入引用（journal 里有真 trigger，working memory 没有）。

决策：退役 working memory 层，session journal 成为唯一交互真相，一切记忆产物从 journal 派生（当时定为「journal → episodic 摘要直连」，该摘要流随后被 ADR 0017 的蒸馏管线整体替换，见文末补注）。容量/空闲触发（`idle_timeout_minutes`）随滚动窗口语义一并删除——记忆只挂在 session 完成节拍上，agent 不活动，记忆就不动，与「事件驱动、睡觉零成本」一致。

## 删除清单

- `memory/working_store.py`（整个文件）、`Interaction` 模型、`MemoryStore` 抽象（只剩 episodic 一个实现）。
- `MemoryManager` 的交互存取方法族（`add_interaction` / `save` / `get_history_text` / `check_idle_timeout` 等）；`tools/memory/compact.py`（整个文件）+ 注册点 + system prompt 里教模型用 compact 的段落——压缩是系统自动的，主动压缩工具没有存在意义。
- spec 字段 `:save-interaction` 全链（解析/校验/模板/原型/执行传递链）；`StageOutcome.save_interaction` 改名 `final_reply`，语义收窄为 on-complete 的 `result` 输入，不再承载记忆持久化。
- MemoryConfig 的 `working_*` 与 `idle_timeout_minutes` 字段；旧 config.json 的对应段读取时自然忽略，无需 migration。

## 后果

- 交互只有一处真相：journal。摘要、重放、审计读同一份日志。
- working/interactions.jsonl 残留文件成为死数据，不读不删（不做清理逻辑；用户可手删）。
- 明确不做：journal 的归档/清理（沿用 ADR 0007 的留待治理）。

## 演进补注

- **2026-09-17（ADR 0017）**：摘要流被蒸馏管线整体替换——去重从「扫 summaries.jsonl 判重」改为标量时间戳水位线 `distillation.json`；`Summary` 模型与 summaries.jsonl 整体删除（旧文件不读不迁）；批量语义从「一次摘要一条 Summary」改为「一次蒸馏多条 claim」；rememberFact 语义改为「立即写一条 claim」。工作/情景两层记忆的说法同时作废，现状见 docs/design/memory.md。退役 working memory 的决策本身与删除清单不受取代影响。
