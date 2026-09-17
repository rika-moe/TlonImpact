# ADR 0017：记忆系统重设计——claim 模型 + 向量检索 + JIT 注入，整体替换旧 episodic 记忆

状态：已接受（2026-09-17，方向讨论拍板）；阶段 1（水位线 + claim 数据模型）、阶段 2（sqlite-vec 向量索引 + JIT 注入）、阶段 3（冲突判定 + 失效不删除）、阶段 4（anchor 减肥 + journal 回读工具）已实施（2026-09-17）

修订关系：部分取代 ADR 0009（摘要流被蒸馏管线替换；journal 为唯一真相不变）；修订 ADR 0008 的 anchor 语义（knowledge 段移出 anchor）。

## 背景

旧记忆系统（session journal → LLM 批量摘要 `summaries.jsonl` + 三元组事实 `facts.jsonl`，注入取最近 N 条）存在五个实锤缺陷，逐条经代码验证并立 issue：

-  中文检索失效（按空格分词 + 子串匹配）
-  摘要链断链重摘要（进度判定依赖存留摘要，容量淘汰即断链）
-  注入与当前消息无关（`current_message` 死参数，永远取最近 N 条）
-  记忆与上下文时间轴错开 + anchor 超阈值时压缩无路可退
-  事实 supersede 只在三元组完全相等时发生，矛盾事实堆积

业界调研（2026-09-17，记录于 issue  评论区）结论：主流现成 memory 产品无可直接引入者（Letta 已弃 Python、Memobase 停更、Zep/Graphiti 需外挂图数据库、Mem0 开源/平台分化、框架模块绑死各自生态）；且 Anthropic/Manus/LangChain 三家独立收敛的共识是「文件 + 笔记 + 周期摘要 + 选择性注入」——Tlon 的范式方向没错，缺的是检索层与事实生命周期。拍板：**整体替换自研，不留兼容层**（项目原则 1），借机制不引库。

## 决策

三层结构。第 1 层不动，2、3 层整体替换。

### 第 1 层：真相层（不变）

session journal 是唯一不可变交互真相（ADR 0007）。一切记忆产物都是 journal 的派生视图，可删可重建。

### 第 2 层：蒸馏层（替换摘要 + 事实两套机制）

- **统一产物为 claim（断言）**：自然语言一句话 + 元数据：
  - `source_session_ids`——来源引用，支持回读 journal 原文（ReadAgent 模式）
  - `valid_from` / `valid_to`——有效时间窗
  - `status: active | superseded`——取代即失效，**不删除**（借鉴 Zep 双时态：历史可审计，与书信共和国「信件永存」一致）
  - `importance`——检索打分因子
- **废三元组**：`(subject, predicate, object)` 模型整个删除。自然语言断言 + embedding 检索不需要结构化三元组；精确字符串匹配的 supersede 缺陷（）随之消亡。摘要与事实不再分两个文件两套逻辑——它们只是 importance/粒度不同的 claim。
- **进度水位线独立持久化**：标量时间戳水位线 `last_distilled_at` 存独立小文件 `memory/distillation.json`——session id 为 uuid 无序，不能按 id 做水位线；「哪些 session 待蒸馏」只查水位线（session 的 `session/start` 时间戳 > 水位线者，取不到时间戳的一律视为 pending），不扫描存留产物——产物被淘汰不影响进度（ 消亡）。
- **冲突判定在蒸馏时做**：新 claim 与向量检索到的相似旧 claim 一起交 LLM 判定 新增 / 取代 / 并存（借鉴 Mem0 的 ADD/UPDATE/DELETE 路由，机制公开、不引库）。
- **rememberFact 语义改为「立即蒸馏一条 claim」**：走同一套冲突判定与索引，不再是绕过 journal 时间轴的旁路写；写入即入索引，下一轮检索即可命中（ 的「记了看不到」解除）。
- **蒸馏在 scheduler 回收点/反思时段批量跑**（sleep-time 模式），在线路径零 LLM 成本。

### 第 3 层：检索注入层（全新）

- **索引**：进程内嵌入式向量库 **sqlite-vec**（单依赖、纯本地、数据落 `~/.tlon`，与部署哲学一致）。不引入图数据库，不引入外部服务。
- **embedding 用 LLM provider 的接口**（成员级模型配置的延伸，对齐 ADR 0016）；不跑本地小模型（当前无条件）。
- **换 provider 必须重刷 embedding**：不同模型的向量空间互不兼容。设计保证索引是 claim 文本的**纯派生视图**：索引文件记录 embedding 模型标识（模型名 + 维度），启动时与当前配置比对，不一致即自动全量重建——重刷是 N 次 embedding 调用，无损、无数据迁移。
- **JIT 注入**：每封来信处理时以邮件内容为 query，按 `相似度 × 新近度 × importance` 混合打分（Generative Agents 复合公式，业界事实标准）取 top-k active claims 注入 `(knowledge)` 段，每条带日期标记。`current_message` 从死参数变为核心输入（ 消亡）；embedding 检索天然跨语言，中文分词逻辑整个删除（ 消亡）。
- **回读通道**：`searchMemories` 返回 claim + 来源 session 引用；新增 journal 回读工具，agent 可按引用读对应 session 的原文片段（journal 本就在磁盘上）。

### anchor 语义修订（并入本设计，修订 ADR 0008）

- `(knowledge)` 段不再冻结进 anchor。anchor 只留极小且稳定的身份/状态卡（persona 指针、当前任务），保持前缀稳定（Manus 教训：KV-cache 前缀漂移代价高）。
- 知识注入放在 anchor 之后的普通消息位——可被 context pressure 正常压缩（ 的「anchor 挤死、压缩无路可退」解除）；或按轮 JIT 重算，实施时二选一并在设计文档注明取舍。
- **落地（阶段 4，2026-09-17）按方案 A**：knowledge 消息固定注入到 anchor 之后的最末位（整个 context 的最后一条，当前邮件之后）；DSL `:sections` 里 `(knowledge)` 的声明只控制「是否包含」，**位置语义失效**（统一规则，不随声明位置浮动）。放弃按轮 JIT 重算——它每轮改写 journaled context、毁缓存前缀。`session/start` 事件显式携带 `anchor_len`（journaled context 仍是完整含 knowledge 的——model-visible ⇒ journaled 不变）；重放优先读显式字段，旧事件回退 `len(context)`。效果：knowledge 落在可压缩区，context pressure 的 middle 不再因 knowledge 膨胀而为空；cache 前缀（anchor）更小且跨 session 更稳定（knowledge 每 session JIT 变化，本就在毁前缀，移出后 ADR 0015 的缓存命中反而改善）。固有取舍：session 内 `rememberFact` 的新 claim 不在当前 session 重注入，但经 `searchMemories` 立即可查、下个 session 进 JIT 注入。

### 边界（不动）

记忆仍是成员生命周期内的经验；退休时蒸馏为 LESSONS.md 归原型（世代更替，ADR 0006 / issue  已划界）。记忆层不参与进化层。

### 删除清单

阶段 1 实际落地（2026-09-17）：

- `memory/episodic_store.py` 整个文件（三元组事实 + 摘要双存储、容量淘汰语义）——由 `memory/claim_store.py`（`claims.jsonl`，append-only、无容量上限）替代
- `memory/models.py` 的 `Fact` / `FactType` / `Summary` 删除；`SessionProjection`（蒸馏投影中间体）与 `ContextBlock`（单一 `claims` 类型）按 claim 模型重建保留
- `memory/summarizer.py` 删除，摘要/事实抽取 prompt 并入 `memory/distiller.py` 的蒸馏管线（一次 LLM 调用产出 claim 列表；fallback 为机械 claim）
- `MemoryManager`：`maybe_summarize_completed_sessions` 的扫摘要判重 → `maybe_distill_completed_sessions` 的水位线判定；`forget_fact` → `delete_claim`；`MemoryConfig` 的 `summary_window` / `episodic_max_summaries` / `episodic_max_facts` / `context_max_summaries` / `context_max_facts` 删除，新增 `distillation_window` / `context_max_claims`
- `search_memories` 的关键词提取与子串匹配保留到阶段 2（向量检索落地时随  一起消亡）；`build_context_blocks` 仍是最近 N 条注入（阶段 2 换 JIT 检索）
- 冲突判定/supersede 写入逻辑未做（阶段 3）；模型字段（`status`/`valid_to`/`superseded_by`）已备好
- 旧 `summaries.jsonl` / `facts.jsonl` 成为死数据：不读、不迁移、不写清理逻辑（同 ADR 0009 对 working memory 残留的处置，用户可手删）

阶段 2 实际落地（2026-09-17）：

- 新依赖 sqlite-vec：`memory/vector_index.py` 的 `VectorIndex`（`claims_vec.db`，cosine KNN），`vec_meta` 表记录 embedding 模型 + 维度，模型变更即全量重建；索引是 claims.jsonl 的纯派生视图
- `LLMConfig` 新增成员级 `embedding_model_name`（None = 不做向量检索）；`base/embedding_client.py` 薄封装 `litellm.aembedding`，memory 层仍只依赖 base
- `build_context_blocks(current_message)` 接线 JIT 检索：query embed → 候选池 → 复合打分 `1.0·similarity + 0.3·recency + 0.5·importance`（recency 半衰期 7 天指数衰减）取 top-k；无 query / 无索引 / 调用失败一律降级最近 N 条
- `ContextBuilder.build_knowledge_context` 接收当前邮件主题 + 正文作为 query；`searchMemories` 改向量检索并露出 `source_session_ids`（ 的关键词分词逻辑删除）；`forget` 保持关键词匹配

阶段 3 实际落地（2026-09-17）：

- `ClaimStore.supersede_claim`：原地置 `status="superseded"` + `superseded_by` + `valid_to`，全量原子重写（沿用删除路径的 mutation-then-rewrite 模式）——失效不删除，旧 claim 留在 `claims.jsonl` 可审计
- `Distiller.judge_conflicts`：整批一次 LLM 调用判定（候选旧 claim 以 id + 日期 + 文本入 prompt，verdict 用 id 引用；幻觉 id 解析层丢弃，无有效目标的 SUPERSEDE 视为 ADD），复用 `_extract_json` 容错解析；新增 `ConflictVerdict` 数据类
- `MemoryManager._resolve_conflicts`：批量蒸馏落盘前与 `rememberFact` 写入前共用——新 claim 文本 embed → vec 检索相似度 ≥ 0.75 的 active 旧 claim 候选 → 有候选才调判定 → SUPERSEDE 应用、ADD 追加；无索引 / 无 LLM / 调用失败 / 解析失败一律退化纯追加（ 的同义事实矛盾堆积缺陷消亡）

阶段 4 实际落地（2026-09-17）：

- anchor 减肥（修订 ADR 0008）：`ContextBuilder.build_context` 把 knowledge 消息从 sections 顺序中摘出，锚部（其余 sections + 当前邮件）拼完后作为整个 context 的最后一条追加；返回 `BuiltContext(messages, anchor_len)`，anchor 不含 knowledge 尾消息。DSL 不动——`(knowledge)` 仍是合法 section 名，只改注入位置语义（声明只控制「是否包含」）
- `LoopDriver` 首步：`session.anchor_len` 取 `BuiltContext.anchor_len`，`session/start` 事件显式携带 `anchor_len` 字段；重放（`SessionJournal.resume`）优先读显式字段，旧事件回退 `len(context)`
- journal 回读工具 `readSession`（`tools/memory/read_session.py`）：按 session id 读 journal，渲染触发 + 各轮回复 + 工具活动的有界摘要（截断策略：触发 300 字、每轮回复 600 字、最多 12 轮、总量硬上限 12000 字符），闭环 searchMemories 的 `source_session_ids` → 原文回读（ReadAgent 模式）；经 `SessionLogReader` 端口注入，`tlon.tools` 不反向依赖 `tlon.agents`

### 施工顺序（最小端到端优先，每阶段独立 PR）

1. **水位线 + claim 数据模型**——存储层替换，检索行为不变（行为不变先行）
2. **sqlite-vec 索引 + JIT 注入**——能力扩张，用户可感知差异
3. **冲突判定 + 失效不删除**——claim 生命周期补全
4. **anchor 减肥 + journal 回读工具**——动 ADR 0008 语义的部分
5. **回归集**：10–20 题真实邮件场景（事实召回、知识更新、时序、拒答），此后每次动记忆系统必跑

## 后果

- 记忆产物只有一种（claim），一条蒸馏管线，一套检索——旧系统「摘要/事实两张皮」及其口径不一致（ 中 `get_memory_digest` 硬编码 20 与 `episodic_max_summaries` 的矛盾）随之消除。
- 五个旧缺陷 issue（–）不作单独修复，随整体替换消亡；它们作为新系统的验收对照清单保留。
- 新增依赖：sqlite-vec（及 embedding API 调用成本——每条 claim 一次 embedding，量级可忽略）。
- 换 provider 的成本显性化：索引自动重建，重建期间检索降级为时间序注入（短暂、可接受）。
- 蒸馏管线出错只影响派生视图，journal 真相无损——任何蒸馏 bug 都可删产物重建恢复。
- 明确不做：知识图谱（过度工程）、本地 embedding 小模型（无条件运行）、旧数据迁移（死数据不读不删）、记忆层参与进化（边界不变）。
