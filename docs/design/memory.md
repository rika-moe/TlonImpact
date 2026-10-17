# 记忆系统设计

Tlon 的记忆不是检索增强的附件，而是 agent 认知循环的一部分：session journal（ADR 0007）是交互的唯一真相，已完成的 session 成批蒸馏成 claim（自然语言断言）进入长期记忆，下一轮对话再把它们拼回上下文。本文解释这套系统为什么长现在这样，以当前实现为准（存储层在 [src/tlon/memory/](../../src/tlon/memory)，agent 侧工具在 [src/tlon/tools/memory/](../../src/tlon/tools/memory)，反思在 [src/tlon/agents/reflection.py](../../src/tlon/agents/reflection.py)）。整体重设计的决策与阶段规划见 [ADR 0017](../decisions/0017-claim-based-memory.md)；本文描述阶段 5（记忆回归集，五阶段收官）落地后的真实状态。

## 定位：可审计的个体记忆

[docs/vision.md](../vision.md) 要求「经验沉淀为知识」。在 Tlon 的语境里这条要求有两个约束：

- **行为即数据**——记忆也是数据。agent 能读、能写、能改自己的记忆，人类也能直接打开文件审计。
- **书信共和国**——社会的知识在邮件里积累，个体记忆只服务个体。记忆是 agent 私有的，共享靠发信，不靠共享存储。

这两条决定了记忆系统的基本形态：每个 agent 一个私有目录（`~/.tlon/{agent_id}/memory/`，路径由 [base/paths.py](../../src/tlon/base/paths.py) 的 `TlonPaths` 解析），内容以 JSONL/JSON 文本文件为主体，外加一个 sqlite-vec 向量索引库（`claims_vec.db`）——索引是 `claims.jsonl` 的**纯派生视图**，可删可重建，不是第二个真相（ADR 0017 阶段 2）。

还有一条隐含约束来自认知循环的架构：`tlon.memory` 在分层上只依赖 `tlon.base`（import-linter 强制），不认识 agent、邮件、工具。它提供的是纯粹的存储、蒸馏与检索能力，「什么时候存、什么时候取、取多少拼进上下文」的决策都在 agents 层（spec 的 `:loop` 声明与 `ContextBuilder` / `SessionScheduler` 的装配）。记忆系统是被编排的机制，不自带策略——这与全系统「策略/机制分离」的原则一致。也是这条约束决定了蒸馏触发入口的形状：manager 不能 import journal，于是定义 `SessionLogReader` Protocol（`list_completed(started_after)`、`count_completed(started_after)` + `read_events` 三个方法），`SessionJournal` 结构化满足它，由 scheduler 在回收点注入。ADR 0032 起完成集合先按 `started_at` 元数据与 watermark 过滤，蒸馏只对 pending session 调用 `read_events`；digest 只要数量时直接 `count_completed`，不读事件。无 `started_at` 的完成 session 始终 pending。

## 单一产物：claim

ADR 0017 之前，长期记忆分两套机制：批量会话摘要（`summaries.jsonl`）与三元组结构化事实（`facts.jsonl`），各有容量上限与淘汰逻辑。ADR 0017 把它们整体替换为**唯一产物 claim**：一句自然语言断言 + 元数据（[memory/models.py](../../src/tlon/memory/models.py) 的 `Claim`）：

- `source_session_ids`——来源 session 引用，可回读 journal 原文核对；
- `valid_from` / `valid_to`——有效时间窗；
- `status`（`active` / `superseded`）与 `superseded_by`——取代即失效、不删除的生命周期字段（写入逻辑见下文「claim 生命周期」）；
- `importance`——检索打分因子（JIT 混合打分使用，见「上下文注入」）。

摘要与事实不再分两个文件两套逻辑——它们只是 importance/粒度不同的 claim。三元组模型整个删除：自然语言断言不需要结构化三元组，精确字符串匹配的 supersede 缺陷随之消亡。

[memory/manager.py](../../src/tlon/memory/manager.py) 的 `MemoryManager` 是统一入口，持有唯一的存储 [memory/claim_store.py](../../src/tlon/memory/claim_store.py) 的 `ClaimStore`：`memory/claims.jsonl`，每行一条 claim。

记忆有两个入口：系统自动批量蒸馏（`maybe_distill_completed_sessions`）与 agent 主动记录（`rememberFact`，立即写一条 claim）与反思补记（`record_claim`）——长期层允许多方填充，因为它本来就是筛选后的判断而不是原始记录。

存储格式是 JSONL。写入策略是**追加优先**：`add_claim` 只追加新行，整文件重写（临时文件 + `os.replace` 原子替换）只发生在删除（`forget` / 反思的 `delete_claim`）或追加失败后的兜底。追加优先的理由：JSONL 追加是崩溃安全的最低成本形态，进程死在两次写之间最多损失最后半行（加载时逐行解析、坏行跳过告警），而全量重写一旦中途崩溃就丢整个文件——所以重写必须走原子替换。claims **没有容量上限**：append-only，失效不删除，与 journal 的存储哲学一致——旧系统的容量淘汰正是断链 bug 的温床。

store 一把 `asyncio.Lock`，缓存即真相，磁盘是缓存的持久化。`MemoryManager.load` 还有一层配置加载语义：`memory/config.json` 是**唯一的**记忆调参面——存在时以磁盘文件为准（绑定 `agent_id`），不存在时用构造传入的 `MemoryConfig`（生产装配传默认值）落盘初始化。ADR 0017 之前的 `summary`/`episodic`/旧 `context` 段在读取时自然忽略（未知段不读），无 migration。

`MemoryConfig` 的默认值定义了整个系统的量级：蒸馏窗口 3 个 session、进上下文的 claim 10 条；反思至少间隔 5 个新完成 session。这些数字是成本与效果之间的手工平衡点，集中在 [memory/models.py](../../src/tlon/memory/models.py) 一处。

### 批量蒸馏：journal → claims

蒸馏由 [memory/distiller.py](../../src/tlon/memory/distiller.py) 的 `Distiller` 完成：一次 LLM 调用（30 秒超时），从整批 session 投影蒸馏出若干条 claim（每条 `text` + `importance`），prompt 要求模型以 JSON 返回，`_extract_json` 容忍 markdown 代码块包裹。LLM 不可用时（未配置、调用失败或产出为空）回退到 `fallback_claims`——每个 session 一条机械 claim（「处理过会话（触发语：…）」+ 回复要点拼接）。降级是设计意图：记忆蒸馏不能因 LLM 抖动而丢失；且因为输入来自 journal，fallback 引用的是真触发语（ADR 0028 起为固定模板「你有 N 封未读邮件」），而不是 agent 自己的回信。

Distiller 的输入是 **session 事件投影**（`SessionProjection`，[memory/models.py](../../src/tlon/memory/models.py)）：`trigger_content`（`session/start` 的触发语，ADR 0028 起为固定模板）+ 每轮 `llm/response` 的 content + 每条 `tool/result` 的一行摘要。投影由 `MemoryManager._build_projection` 从 journal 事件重建——蒸馏流不经过任何中间存储。

### 进度水位线

「哪些 session 待蒸馏」由**标量时间戳水位线**判定：持久化在 `memory/distillation.json`（`{"last_distilled_at": "..."}`）。session id 是 `{agent_id}-{uuid}`，无时间序，不能按 id 做水位线；判定规则是 completed session 的 `session/start` 事件时间戳 > 水位线者为 pending，取不到时间戳的一律视为 pending（安全方向，绝不静默跳过）。

旧系统的进度判定是「扫描存留摘要里的 `source_session_ids` 求差集」——摘要被容量淘汰，进度就断链重摘要。水位线独立持久化后，产物（claims）被淘汰与否都不影响进度；且 claims 本就不淘汰。

触发链条在 [agents/scheduler.py](../../src/tlon/agents/scheduler.py) 的回收点：session 完成（`is_completed` 分支，`_ensure_session_end` 落盘之后），scheduler 先给反思引擎计数（`note_interaction`），再调 `MemoryManager.maybe_distill_completed_sessions(journal)`。pending 数达到 `distillation_window`（默认 3）就把整批投影交给 Distiller，产出的 claims 落盘（`source_session_ids` 记全部来源 session，`valid_from` 记蒸馏时刻），**成功后**水位线推进到本批 session 的最大开始时间；落盘失败水位线不动，下次回收点重试。整个触发永不抛进调度循环：LLM 失败回退机械 claim，其余失败记日志、下次重试。

与旧设计一致的两点：一是**蒸馏不删除任何输入**——journal 只增不减；二是**没有空闲触发**——蒸馏只挂在 session 完成节拍上。

### claim 生命周期：冲突判定，失效不删除

阶段 3 补全了 claim 的生命周期。旧系统的事实 supersede 只在三元组完全相等时发生，同义事实的矛盾版本无限堆积；现在**两条写入路径过同一套判定**——批量蒸馏落盘前与 `rememberFact` 写 claim 前，都先走 `MemoryManager._resolve_conflicts`：

1. **候选检索**：新 claim 文本 embed 后查向量索引，只取相似度 ≥ 0.75（余弦）的 **active** 旧 claim 作为候选（每条约 3 条）。阈值刻意定高——判定要抓的是「同一事实的矛盾版本」，0.75 以上两条断言几乎必然在说同一件事；低于它就是话题相关而已，直接 ADD，不发 LLM（成本控制）。阈值与候选数是 `memory/config.json` 的 `conflict` 段调参（`similarity_threshold` / `candidate_limit`，默认值 0.75 / 3 即上述数字）。
2. **一次批量判定**：有候选才把整批新 claim（编号）+ 全部候选旧 claim（id + 日期 + 文本）交给 `Distiller.judge_conflicts` **一次** LLM 调用判定（借鉴 Mem0 的 ADD/UPDATE 路由）——不是每条一次调用。
3. **应用 verdict**：`ADD`（含并存）直接追加；`SUPERSEDE` 携带被取代的旧 claim id 列表，manager 调 `ClaimStore.supersede_claim` 把旧 claim 置 `status="superseded"` + `superseded_by=新claim id` + `valid_to=now`，新 claim 正常追加。

**失效不删除**：被取代的 claim 留在 `claims.jsonl` 里可审计（借鉴 Zep 双时态，与「信件永存」一致）——supersede 是原地改三个生命周期字段 + 全量原子重写，与删除同模式但语义相反。所有读取面（`get_recent_claims` / `get_all_claims` / 关键词搜索 / JIT 检索 / 冲突候选）都只出 active，superseded 只在 `include_superseded=True` 的审计视角出现；向量索引里被取代 claim 的残留向量无害（检索候选本来就用 active 集合过滤），启动时的 backfill 会顺手清掉。

**降级是一等公民**：无向量索引（未配 embedding）、无 LLM（`distiller=None`——此时 fallback 机械 claim 也走同一追加路径，判定自然同样不可用）、embedding/检索失败、判定调用失败、判定结果解析失败、单条 verdict 畸形——一律退化为纯追加（ADD），只记日志。冲突判定是增强，绝不能阻断写入。判定 prompt 约束模型只能引用给出的旧 claim id，幻觉 id 在解析层被丢弃（无有效目标的 SUPERSEDE 视为 ADD——绝不在猜测上失效旧记忆）。

## 向量索引：claims 的派生视图

阶段 2 引入 [memory/vector_index.py](../../src/tlon/memory/vector_index.py) 的 `VectorIndex`：嵌入式 sqlite-vec 库（`memory/claims_vec.db`），claim_id → embedding，cosine 距离 KNN。embedding 走成员级 LLM 配置的延伸——`agent.yaml` 的 `llm.embedding_model_name`（`provider/model` 形式，同 litellm 路由与 `{PROVIDER}_API_KEY` 约定），薄封装在 [base/embedding_client.py](../../src/tlon/base/embedding_client.py) 的 `EmbeddingClient`（memory 层只依赖 base，litellm 不下渗）。

索引的三条不变量：

- **纯派生视图**。真相只有 `claims.jsonl`；索引可整个删掉，下次启动从 active claims 全量重建。索引库的 `vec_meta` 表记录 embedding 模型标识 + 维度，`MemoryManager.load` 与当前配置比对——换模型（向量空间互不兼容）即自动全量重建，重建就是 N 次 embedding 调用，无数据迁移。
- **降级是一等公民**。未配置 `embedding_model_name`、embedding 调用失败、索引重建失败，检索一律降级为「最近 N 条」时间序注入——部分 provider 没有 embedding 端点，记忆与会话绝不能因此挂掉。写入路径同样宽容：`add_claim` 后同步尝试 embed 入库，失败只记日志（claim 仍可被时间序检索到）；load 时对缺向量的 active claims 做 backfill、对已被删 claim 的残留向量做清理。
- **写入即入索引**。蒸馏落盘与 `rememberFact` 之后同步 embed 入库，下一轮检索即可命中，「记了看不到」不会复活。

## 上下文注入

记忆的回流通道是 `MemoryManager.build_context_blocks(current_message)`——阶段 2 起 `current_message` 从死参数变为核心输入。两条路径：

- **JIT 相关性检索**（有 query 且索引可用）：query（责任集全部邮件的主题 + 正文，ADR 0028——收件箱驱动模型下没有单一的「当前邮件」）embed 成向量 → vec KNN 取候选池（`context_max_claims` 的 4 倍）→ 复合打分 `score = 1.0·similarity + 0.3·recency + 0.5·importance` 取 top-k active claims。similarity 由 cosine 距离归一化（`1 - distance`，截断到 [0,1]）；recency 是指数衰减（半衰期 7 天，Generative Agents 衰减的半衰期重参数化）；三个权重、半衰期与候选池倍数都是 `memory/config.json` 的 `retrieval` 段调参（默认值 1.0/0.3/0.5、7 天、4 倍，同原 [memory/manager.py](../../src/tlon/memory/manager.py) 模块常量）——相似度占主导（JIT 的意义就是相关性），importance 让关键断言赢下近似平局，recency 最弱（「最新优先」已有降级路径兜底，旧但相关的断言不能被新鲜噪声淹掉）。
- **时间序降级**（无 query / 未配置 embedding / 检索任何环节失败）：取最近 `context_max_claims` 条 active claims。

两种路径的渲染一致：`- [YYYY-MM-DD] {text}` 的日期标记行，拼成一个 `ContextBlock`（类型 `claims`，标签「记忆」）。

注入点是 [agents/context_builder.py](../../src/tlon/agents/context_builder.py) 的 `ContextBuilder`：spec 的 `:context` 段向量里出现 `(knowledge)` 时，`build_context` 把责任集全部邮件的主题 + 正文作为 query 传给 `build_knowledge_context`，把记忆块拼成一条 `[已知信息]` 开头的 user 消息。注入位置不随声明浮动（ADR 0017 阶段 4）：knowledge 消息固定追加为整个 context 的最后一条（触发语之后），落在压缩锚点（anchor）之外——每 session JIT 变化的内容不再冻结进 anchor，context pressure 可以正常压缩它，KV-cache 前缀也随之更小更稳（ADR 0015）。`:sections` 里的 `(knowledge)` 声明只控制「是否注入」——fast-responder 原型的 `:context` 里没有 `(knowledge)`，它就不带记忆上场，这是 cost routing 的一部分。

一个细节体现实用主义：`build_knowledge_context` 把记忆构建的异常整个兜住（记告警、返回空串），记忆损坏不阻断对话，空记忆等价于没有 `(knowledge)` 段。记忆是增强，不是依赖。

## agent 侧接口

agent 通过 [src/tlon/tools/memory/](../../src/tlon/tools/memory) 下的四个工具读写自己的记忆，全部在 `assemble_cognitive`（[agents/assembly.py](../../src/tlon/agents/assembly.py)）装配认知成员时注册；前三个直接持有本 agent 的 `MemoryManager` 实例，`readSession` 持有本 agent 的 session journal（`SessionLogReader` 端口）：

- `rememberFact`（`remember_fact.py` 的 `RememberFactTool`）——立即写一条 claim（参数只有 `fact` 文本与 `importance`；旧的三元组参数已删）。新 claim 不在当前 session 重新注入（注入只在 session 首步构建一次），但经 `searchMemories` 立即可查，下个 session 进 JIT 注入。
- `searchMemories`（`search_memories.py` 的 `SearchMemoriesTool`）——向量检索：query embed 后按相似度取 top-k active claims（embedding 检索天然跨语言，旧系统按空格分词的中文检索缺陷随之消亡），每条结果带日期与 `source_session_ids` 来源引用；索引不可用时降级关键词子串匹配。
- `readSession`（`read_session.py` 的 `ReadSessionTool`，ADR 0017 阶段 4）——claim → 来源的回读通道：按 session id 读该会话的 journal，渲染为有界摘要（触发语 + 各轮回复 + 工具活动；触发截 300 字、每轮回复截 600 字、最多列 12 轮、总量硬上限 12000 字符）。claim 是压缩指针，journal 是原文真相（ReadAgent 模式）。
- `forget`（`forget_memory.py` 的 `ForgetMemoryTool`）——删除记忆；默认预览模式（只报匹配），`confirm: true` 才真正删。两步确认是给 LLM 的防误删闸门。

工具的读写都只落在本 agent 的目录——`MemoryManager` 构造时绑定 `agent_id`，工具层没有跨 agent 访问的入口。记忆私有性在这一层是结构性保证，不是约定。

## 反思：记忆的自我审计

[agents/reflection.py](../../src/tlon/agents/reflection.py) 的 `ReflectionEngine` 是 agent 对自己记忆的定期审计。触发门控是双条件：距上次反思超过 `rule.loop.reflect.interval_minutes`（来自 spec 的 `:reflect` 段），**且**期间新完成的 session 数达到 `reflection_min_interactions`（默认 5，在 memory config 里）。计数由 `SessionScheduler` 在 session 回收点喂给 `note_interaction`。

审计本身是「快照 → LLM 分析 → 应用操作」三步：`get_memory_digest` 取记忆状态快照（未蒸馏 session 数——基于水位线、最近 50 条 active claim），交给 LLM 按 `REFLECTION_SYSTEM_PROMPT` 分析（60 秒超时；prompt 文本是包数据文件 [agents/prompts/reflection_system.md](../../src/tlon/agents/prompts/reflection_system.md)，非代码常量），返回的操作列表（`record_claim` / `delete_claim` / `noop`）逐个应用回 `MemoryManager`。反思的开关与间隔来自 spec 的 `:reflect` 段（`rule.loop.reflect`），因此它是行为数据的一部分——原型可以声明不同的反思频率，但最小 session 数阈值仍在 memory config 里。

应用操作时单个失败只记日志不影响其余；整个过程跑在后台 task 里（`_running` 标志防重入），不阻塞认知循环。原则写进了 prompt：多数情况下 `noop` 是正常的。

`delete_claim` 的删除按 claim id 定点执行：prompt 里 claim 以 id 前 8 位列出，模型回传的 `claim_id` 交给 `MemoryManager.delete_claim`，先精确匹配、再退到唯一前缀解析，未命中或歧义都记 warning、不删任何东西——删除失败必须可见，不允许静默 no-op。模型只给 `description` 时回退到 `forget` 的关键词匹配路径。

值得注意的是反思能改的只有记忆：它可以记 claim、删 claim，但碰不到 spec——行为变更走的是另一条通道：成员经 `proposeBehavior` 把经验交给独立的起草调用产出新 spec，落到 staging 信任闸等人类批准，反思本身仍不直接改写行为。

## 退休蒸馏：记忆的代际出口

反思是生者的自我审计；退休蒸馏是死者的遗产整理。[agents/lessons.py](../../src/tlon/agents/lessons.py) 的 `LessonsDistiller` 是纯事件消费者：订阅 `AGENT_STOPPED`（蓄意停止），对退休成员做一次离线蒸馏。

三个关键设计：

- **离线路径**。蒸馏发生在 member runtime shutdown 之后，所以蒸馏器**离线重建**归档数据：用显式 `data_dir` 重建 `MemoryManager`、用 `SessionJournal` 满足 digest 需要的 journal 读面，不与任何活着的运行态共享对象。
- **只挂蓄意停止**。`AGENT_SLEEPING`/`AGENT_IDLE`（drain 是日常 scale-to-zero 睡眠，反复发生）不触发蒸馏——退休才有遗产，午睡没有。
- **失败不阻断**。非认知成员（无 `:loop` 的 spec）或 digest 为空直接跳过；LLM 失败只记日志不 raise——停止流程已经完成，这只是事后簿记。

蒸馏调用沿用反思的参数先例（timeout 60 秒；采样参数不配置，供应商默认生效），`purpose="lessons"`，并且**用退休成员自己的 agent_id 构造 LLMClient**——最后一笔账记在它头上（per-member 账本自然覆盖）。产出落盘为 `~/.tlon/{agent_id}/memory/lessons.md`：不是全量记忆的复述，而是「能传给下一代的教训」的提炼（prompt 明确约束）。

`lessons.md` 只是原材料。它进入基因库的通道在原型侧：经 `writePrototype` / admin API 的 `lessons` 参数包装成原型的 `LESSONS.md` 随包传递，`instantiate` 拷贝到新成员的 `config/LESSONS.md`，spec 的 `(lessons)` context section 声明后才注入对话（见 [prototypes.md](./prototypes.md) 与 [unified-member-model.md](./unified-member-model.md) 决策 9 补注）。退休交接信已落地为 `RetirementCeremony`（见 [mail.md](./mail.md)「退休交接信」）——蒸馏沉淀给下一代，交接信盘点给同代的接管者。

## 一次交互的完整链路

把上面各节串起来，一封邮件从进入到沉淀的全链路是：

1. 认知循环跑完，journal 里留下完整的 session 事件流（ADR 0007：模型可见 ⇒ 已入日志）。
2. scheduler 在回收点做两件事：`note_interaction` 喂反思计数；`maybe_distill_completed_sessions` 检查水位线 pending 窗口——满 3 个已完成 session 就从各自 journal 重建投影、批量蒸馏写 claims、推进水位线。
3. 调度器在循环间隙另做周期检查：`check_reflection`（间隔与计数双门控通过则后台跑反思）。
4. session 启动构建上下文时，`ContextBuilder` 按 spec 的 `(knowledge)` 段以责任集全部邮件的主题 + 正文为 query 做 JIT 检索（或降级取最近 N 条），把选中的 active claim 拼成 `[已知信息]`，作为 context 的最后一条消息注入（anchor 之外，ADR 0017 阶段 4）。

整条链路里没有定时器、没有后台守护进程：蒸馏与反思全部挂在认知循环的事件节拍上。agent 不活动，记忆就不动——这与整个系统「事件驱动、睡觉零成本」的运行模型一致。

## 回归集

阶段 5 补上了记忆系统的回归门禁：[scripts/eval_memory.py](../../scripts/eval_memory.py) + 场景数据集 [scripts/memory_eval/](../../scripts/memory_eval/README.md)（14 个中文邮件场景，对齐 LongMemEval 的四个维度——事实召回、知识更新、时序、拒答）。评测跑真实链路：每个场景在隔离的临时目录里重建 MemoryManager，假 journal 灌入脚本化 session（`session/start` 时间戳控制蒸馏顺序，window=1 逐 session 蒸馏），然后用场景 query 打两个检索面——JIT 注入面（`build_context_blocks`）与检索面（`search_memories`）——断言「该出现的出现、不该出现的（被取代的旧事实、无关 claim、编造内容）不出现」。知识更新与时序维度实际验证的就是冲突判定 supersede 的端到端效果：旧 claim 失效不删除、可审计，但绝不能进检索结果。

定位是**动记忆系统必跑的手动门禁，不进 CI**：评测需要真实 LLM（蒸馏 + 冲突判定）与 embedding 调用——花钱、要 API key，且 LLM 产出有固有波动（脚本允许单场景重试一次再判 FAIL）。运行：

```bash
uv run python scripts/eval_memory.py \
    --model deepseek/deepseek-chat \
    --embedding-model openai/text-embedding-3-small
```

embedding 模型必须显式指定（JIT 检索无从降级评测，缺失直接报错）；API key 按 `{PROVIDER}_API_KEY` 约定读 `~/.tlon/.env`。逐题 PASS/FAIL、失败打印实际检索内容、按维度汇总，退出码可脚本化。场景格式与加新场景的方法见 [scripts/memory_eval/README.md](../../scripts/memory_eval/README.md)。

## 设计取舍

### 为什么是「journal + claims」而不是外挂记忆产品

ADR 0017 的业界调研结论：主流现成 memory 产品无可直接引入者（生态绑死、停更或需外挂图数据库），且 Anthropic/Manus/LangChain 三家独立收敛到「文件 + 笔记 + 周期摘要 + 选择性注入」——Tlon 的范式方向没错，缺的是检索层与事实生命周期。于是整体替换自研：

- **可解释**——每条 claim 都记着 `source_session_ids`，可疑的断言可以回到 journal 逐事件核对。
- **可编辑**——JSONL 是纯文本，人类可以直接打开审计、改错、删除；`forget` 工具的两步确认也建立在「记忆是可枚举条目」之上。
- **可审计**——落盘即事实。`~/.tlon/{agent_id}/memory/` 下的文件与 `sessions.sqlite3` 就是 agent 经历与记得的全部，没有第二个真相；旧 `sessions/` JSONL 只是不参与运行时的离线档案（ADR 0032）。
- **可重建**——claims 是 journal 的派生视图，蒸馏 bug 可以删产物重建恢复，真相无损。

### 为什么蒸馏用 LLM 而不是规则

哪句话是决策、哪个偏好值得长期保留，规则写不出来。但 LLM 蒸馏有两个固有风险，设计上分别兜住：**丢内容**靠蒸馏输入永不删除（journal 只增不减，失败时水位线不动、下次窗口仍在 pending 集合里）与 fallback 机械 claim（蒸馏失败不等于 session 白跑）；**编内容**靠 prompt 约束（「不要推测」）加 `source_session_ids` 溯源。反思引擎是这个信任模型的第二层：蒸馏时没提取到的、提取错的，由反思在事后审计中补记或删除。

### 为什么记忆是 agent 私有的

书信共和国的结构决定了这一点：社会的公共知识在邮件里（持久、可检索、可流传），个体记忆是个体的「脑内状态」。如果记忆可以共享，就绕开了邮件这个唯一公共通道——agent 之间会开始用共享存储交换信息，「平等、开放、沉淀」三条全部漏风。记忆私有 + 邮件公共，是把「想告诉别人的知识必须写成信」变成结构性约束：一个 agent 想让同伴知道什么，只能发信。

## 方向

整体重设计的方向与阶段规划见 [ADR 0017](../decisions/0017-claim-based-memory.md)：阶段 1（水位线 + claim 数据模型）、阶段 2（sqlite-vec 向量索引 + JIT 注入，中文检索失效与「注入与当前消息无关」两个旧缺陷随之消亡）、阶段 3（冲突判定 + 失效不删除的 claim 生命周期，同义事实矛盾堆积随之消亡）、阶段 4（anchor 减肥——knowledge 移出 anchor 固定末位 + `readSession` journal 回读工具，「anchor 挤死、压缩无路可退」的死路随之消亡）与阶段 5（记忆回归集，见上文「回归集」节）已全部落地，五阶段收官。

与愿景的差距主要有两处：

- **个体学习已闭环（2026-09）**——记忆的出口曾只有 context；代际出口由退休蒸馏补上（`lessons.md` → 原型 `LESSONS.md` → 新成员的 `(lessons)` section），同代出口由退休交接信补上（把未竟事项送达接管者），「经验改写行为」本身由 `proposeBehavior` 补上：它把成员自己的 `behavior.tlon` + 记忆 digest + 蒸馏教训喂给一次独立的起草 LLM 调用，产出完整新 spec 并以成员自己的 prototype 为父写入 staging 信任闸——从记忆/教训到 spec 修订提案的回流通道已通（人类批准后 `spawnAgent` 派生新世代）。
- **无社会级公共记忆**——每个 agent 各自蒸馏、各自记 claim，社会层面没有可检索的公共层。邮件提供了沉淀的原材料，但「把书信提炼成社会知识」的机制（存档、共享摘要、同行评议筛选后的公共记忆）还不存在。
