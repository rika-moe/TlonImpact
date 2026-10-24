# 记忆系统设计

当前执行边界以 [ADR 0049](../decisions/0049-retire-agent-session.md) 为准；原 agent session 历史与来源读取已退役，历史 ADR 保留为决策记录，client session 不受影响。

Tlon 的记忆不是检索增强的附件，而是 agent 会话循环的一部分：ProgramStore 保存程序执行与调用回执事实，RunJournal 提供只读视图，已完成的 program run 成批蒸馏成 claim（自然语言断言）进入长期记忆，下一轮对话再把它们拼回上下文。本文解释这套系统为什么长现在这样，以当前实现为准（存储层在 [src/tlon/memory/](../../src/tlon/memory)，agent 侧工具在 [src/tlon/operations/memory.py](../../src/tlon/operations/memory.py)，反思在 [src/tlon/agents/reflection.py](../../src/tlon/agents/reflection.py)）。设计选择的历史见 [ADR 0017](../decisions/0017-claim-based-memory.md)；program run 的事实与恢复契约见 [runtime.md](./runtime.md)。

## 定位：可审计的个体记忆

[docs/vision.md](../vision.md) 要求「经验沉淀为知识」。在 Tlon 的语境里这条要求有两个约束：

- **行为即数据**——记忆也是数据。agent 能读、能写、能改自己的记忆，人类也能直接打开文件审计。
- **书信共和国**——社会的知识在邮件里积累，个体记忆只服务个体。记忆是 agent 私有的，共享靠发信，不靠共享存储。

这两条决定了记忆系统的基本形态：每个 agent 一个私有目录（`~/.tlon/{agent_id}/memory/`，路径由 [base/paths.py](../../src/tlon/base/paths.py) 的 `TlonPaths` 解析），内容以 JSONL/JSON 文本文件为主体，外加一个 sqlite-vec 向量索引库（`claims_vec.db`）——索引是 `claims.jsonl` 的**纯派生视图**，可删可重建，不是第二个真相。

还有一条隐含约束来自会话循环的架构：`tlon.memory` 在分层上只依赖 `tlon.base`（import-linter 强制），不认识 agent、邮件、工具。它提供的是纯粹的存储、蒸馏与检索能力，「什么时候存、什么时候取、取多少拼进上下文」的决策都在 agents 层（成员工作程序与 `ContextBuilder` 的装配）。记忆系统是被编排的机制，不自带策略——这与全系统「策略/机制分离」的原则一致。也是这条约束决定了蒸馏触发入口的形状：manager 定义 RunLogReader Protocol，以 list_completed/read_events 读取来源。RunJournal 只读取 ProgramStore，只读视图不参与运行恢复。来源 ID 为原始 `run_id`，无 `program:` 前缀；消费进度按来源集合判断，不能按开始时间跳过迟完成运行。

## 单一产物：claim

长期记忆的统一产物是 **claim**：一句自然语言断言 + 元数据（[memory/models.py](../../src/tlon/memory/models.py) 的 `Claim`）：

- `source_run_ids`——来源 run 引用，可回读 journal 原文核对；
- `valid_from` / `valid_to`——有效时间窗；
- `status`（`active` / `superseded`）与 `superseded_by`——取代即失效、不删除的生命周期字段（写入逻辑见下文「claim 生命周期」）；
- `importance`——检索打分因子（JIT 混合打分使用，见「上下文注入」）。

摘要与事实通过 importance 和粒度区分，共用 claim 的写入、检索与生命周期。

[memory/manager.py](../../src/tlon/memory/manager.py) 的 `MemoryManager` 是统一入口，持有唯一的存储 [memory/claim_store.py](../../src/tlon/memory/claim_store.py) 的 `ClaimStore`：`memory/claims.jsonl`，每行一条 claim。

记忆有三个入口：系统自动批量蒸馏（`maybe_distill_completed_runs`）与 agent 主动记录（`memory-remember`，立即写一条 claim）与反思补记（`record_claim`）——长期层允许多方填充，因为它本来就是筛选后的判断而不是原始记录。

存储格式是 JSONL，写入策略是**逻辑追加、物理原子快照**。`add_claim` 添加新条目；supersede 更新旧 claim 的生命周期字段，`forget` / 反思可显式删除，所有变更都以临时文件 + `os.replace` 原子重写，提交后才发布缓存。写失败向调用方传播，若提交后丢失回执则重读磁盘事实。加载逐行解析，坏行告警后跳过；不能把这种容错理解为零数据丢失保证。claims 没有容量淘汰上限，自动失效保留旧条目，显式删除仍会删除。

store 一把 `asyncio.Lock`，以缓存服务运行时读写，`claims.jsonl` 承担持久内容；写入失败由存储层记录日志，不能将内存更新等同于已持久提交。`MemoryManager.load` 还有一层配置加载语义：`memory/config.json` 是**唯一的**记忆调参面——存在时以磁盘文件为准（绑定 `agent_id`），不存在时用构造传入的 `MemoryConfig`（生产装配传默认值）落盘初始化。配置字段见 [配置参考](../reference/config-reference.md)。

`MemoryConfig` 的默认值定义了整个系统的量级：蒸馏窗口 3 个 run、进上下文的 claim 10 条；反思至少间隔 5 个新完成 run。这些数字是成本与效果之间的手工平衡点，集中在 [memory/models.py](../../src/tlon/memory/models.py) 一处。

### 批量蒸馏：journal → claims

蒸馏由 [memory/distiller.py](../../src/tlon/memory/distiller.py) 的 `Distiller` 完成：一次 LLM 调用（30 秒超时），从整批 run 投影蒸馏出若干条 claim（每条 `text` + `importance`），prompt 要求模型以 JSON 返回，`_extract_json` 容忍 markdown 代码块包裹。LLM 不可用时（未配置、调用失败或产出为空）回退到 `fallback_claims`——每个 run 一条机械 claim（「处理过程序运行（触发语：…）」+ 回复要点拼接）。降级是设计意图：记忆蒸馏不能因 LLM 抖动而丢失；且因为输入来自 journal，fallback 引用的是真触发语（来自 run 冻结输入），而不是 agent 自己的回信。

Distiller 的输入是 **run 事件投影**（`RunProjection`，[memory/models.py](../../src/tlon/memory/models.py)）：`trigger_content`（`run/start` 的触发语，来自 run 冻结输入）+ 每轮 `llm/response` 的 content + 每条 `tool/result` 的一行摘要。投影由 `build_run_projection` 从只读事件视图重建，也包含无模型程序的返回值和结束状态。来源 ID 随 claim 保存。

### 来源消费进度与恢复

`memory/distillation.json` 保存 `consumed_source_ids` 和一个固定的 pending 批次。完成来源集合减去已消费 ID 得到 pending，较早开始、较晚完成或较晚读取的运行都能进入后续批次。消费状态只认当前 checkpoint，不读取旧 `last_distilled_at`，也不从历史 claim 推断消费回执。旧 agent session 来源不迁移、不提供回读兼容。消费记录独立于 claim 产物，之后删除 claim 不会让已消费来源再次进入蒸馏。

调用模型前先保存批次 ID 和输入投影；维护根为来源集合 hash，对应独立预算。模型结果、稳定 claim ID、冲突候选和应用计划在写入前保存。原子提交进度失败时重读或恢复原批次，复用模型回执并跳过已写 claim；unknown 保留阻塞证据，不发起新的模型请求。蒸馏在串行运行器的维护检查点触发；维护活动计入 `is_busy` 与排空，不能因读取或维护失败重新执行已完成入口。

输入事实不删除。只读交互视图可以随时从 ProgramStore 重建，无投影数据库、游标追赶服务或独立的 agent session 恢复。

### claim 生命周期：冲突判定，失效不删除

**两条写入路径过同一套冲突判定**——批量蒸馏落盘前与 `memory-remember` 写 claim 前，都先走 `MemoryManager._resolve_conflicts`：

1. **候选检索**：新 claim 文本 embed 后查向量索引，只取相似度 ≥ 0.75（余弦）的 **active** 旧 claim 作为候选（每条约 3 条）。阈值用于缩小需要 LLM 判断的候选范围，低于阈值直接 ADD 以控制成本；相似度不是同一事实或矛盾关系的逻辑保证。阈值与候选数是 `memory/config.json` 的 `conflict` 段调参（`similarity_threshold` / `candidate_limit`，默认值 0.75 / 3 即上述数字）。
2. **一次批量判定**：有候选才把整批新 claim（编号）+ 全部候选旧 claim（id + 日期 + 文本）交给 `Distiller.judge_conflicts` **一次** LLM 调用判定（借鉴 Mem0 的 ADD/UPDATE 路由）——不是每条一次调用。
3. **应用 verdict**：`ADD`（含并存）直接追加；`SUPERSEDE` 携带被取代的旧 claim id 列表，manager 调 `ClaimStore.supersede_claim` 把旧 claim 置 `status="superseded"` + `superseded_by=新claim id` + `valid_to=now`，新 claim 正常追加。

**失效不删除**：被取代的 claim 留在 `claims.jsonl` 里可审计（借鉴 Zep 双时态，与「信件永存」一致）——supersede 是原地改三个生命周期字段 + 全量原子重写，与删除同模式但语义相反。所有读取面（`get_recent_claims` / `get_all_claims` / 关键词搜索 / JIT 检索 / 冲突候选）都只出 active，superseded 只在 `include_superseded=True` 的审计视角出现；向量索引里被取代 claim 的残留向量无害（检索候选本来就用 active 集合过滤），启动时的 backfill 会顺手清掉。

**降级是一等公民**：无向量索引（未配 embedding）、无 LLM（`distiller=None`——此时 fallback 机械 claim 也走同一追加路径，判定自然同样不可用）、embedding/检索失败、判定调用失败、判定结果解析失败、单条 verdict 畸形——一律退化为纯追加（ADD），只记日志。冲突判定是增强，绝不能阻断写入。判定 prompt 约束模型只能引用给出的旧 claim id，幻觉 id 在解析层被丢弃（无有效目标的 SUPERSEDE 视为 ADD——绝不在猜测上失效旧记忆）。

## 向量索引：claims 的派生视图

当前采用 [memory/vector_index.py](../../src/tlon/memory/vector_index.py) 的 `VectorIndex`：嵌入式 sqlite-vec 库（`memory/claims_vec.db`），claim_id → embedding，cosine 距离 KNN。embedding 走成员级 LLM 配置的延伸——`agent.yaml` 的 `llm.embedding_model_name`（`provider/model` 形式，同 litellm 路由与 `{PROVIDER}_API_KEY` 约定），薄封装在 [base/embedding_client.py](../../src/tlon/base/embedding_client.py) 的 `EmbeddingClient`（memory 层只依赖 base，litellm 不下渗）。

索引的三条不变量：

- **纯派生视图**。真相只有 `claims.jsonl`；索引可整个删掉，下次启动从 active claims 全量重建。索引库的 `vec_meta` 表记录 embedding 模型标识 + 维度，`MemoryManager.load` 与当前配置比对——换模型（向量空间互不兼容）即自动全量重建，重建就是 N 次 embedding 调用，无数据迁移。
- **降级是一等公民**。未配置 `embedding_model_name`、embedding 调用失败、索引重建失败，检索一律降级为「最近 N 条」时间序注入——部分 provider 没有 embedding 端点，记忆与会话绝不能因此挂掉。写入路径同样宽容：`add_claim` 后同步尝试 embed 入库，失败只记日志（claim 仍可被时间序检索到）；load 时对缺向量的 active claims 做 backfill、对已被删 claim 的残留向量做清理。
- **同步尝试入索引**。蒸馏落盘与 `memory-remember` 之后尝试 embed 入库；成功后下一轮可走向量检索，失败仍按前述规则降级，不能保证任何故障下都立即命中。

## 上下文注入

记忆的回流通道是 `MemoryManager.build_context_blocks(current_message)`，以当前内容为检索输入。两条路径：

- **JIT 相关性检索**（有 query 且索引可用）：query（责任集全部邮件的主题 + 正文，ADR 0028——收件箱驱动模型下没有单一的「当前邮件」）embed 成向量 → vec KNN 取候选池（`context_max_claims` 的 4 倍）→ 复合打分 `score = 1.0·similarity + 0.3·recency + 0.5·importance` 取 top-k active claims。similarity 由 cosine 距离归一化（`1 - distance`，截断到 [0,1]）；recency 是指数衰减（半衰期 7 天，Generative Agents 衰减的半衰期重参数化）；三个权重、半衰期与候选池倍数都是 `memory/config.json` 的 `retrieval` 段调参（默认值 1.0/0.3/0.5、7 天、4 倍，同原 [memory/manager.py](../../src/tlon/memory/manager.py) 模块常量）——相似度占主导（JIT 的意义就是相关性），importance 让关键断言赢下近似平局，recency 最弱（「最新优先」已有降级路径兜底，旧但相关的断言不能被新鲜噪声淹掉）。
- **时间序降级**（无 query / 未配置 embedding / 检索任何环节失败）：取最近 `context_max_claims` 条 active claims。

两种路径的渲染一致：`- [YYYY-MM-DD] {text}` 的日期标记行，拼成一个 `ContextBlock`（类型 `claims`，标签「记忆」）。

注入点是 [agents/context_builder.py](../../src/tlon/agents/context_builder.py) 的 `ContextBuilder`：spec 的 `:context` 段向量里出现 `(knowledge)` 时，`build_context` 把责任集全部邮件的主题 + 正文作为 query 传给 `build_knowledge_context`，把记忆块拼成一条 `[已知信息]` 开头的 user 消息。注入位置不随声明浮动：knowledge 消息固定追加为整个 context 的最后一条（触发语之后），落在压缩锚点（anchor）之外——每 run JIT 变化的内容不再冻结进 anchor，context pressure 可以正常压缩它，KV-cache 前缀也随之更小更稳（ADR 0015）。`:sections` 里的 `(knowledge)` 声明只控制「是否注入」——fast-responder 原型的 `:context` 里没有 `(knowledge)`，它就不带记忆上场，这是 cost routing 的一部分。

一个细节体现实用主义：`build_knowledge_context` 把记忆构建的异常整个兜住（记告警、返回空串），记忆损坏不阻断对话，空记忆等价于没有 `(knowledge)` 段。记忆是增强，不是依赖。

## agent 侧接口

agent 通过 [src/tlon/operations/memory.py](../../src/tlon/operations/memory.py) 下的四个工具读写自己的记忆，全部在 `assemble_member_services`（[agents/assembly.py](../../src/tlon/agents/assembly.py)）装配成员服务时注册；前三个直接持有本 agent 的 `MemoryManager` 实例，`run-read` 持有本 agent 的 run journal（`RunLogReader` 端口）：

- `memory-remember`——立即写一条 claim（参数为 `fact` 文本与 `importance`）。新 claim 不在当前 run 重新注入（注入只在 run 首步构建一次），但经 `memory-search` 立即可查，下个 run 进 JIT 注入。
- `memory-search`——向量检索：query embed 后按相似度取 top-k active claims，每条结果带日期与 `source_run_ids` 来源引用；索引不可用时降级关键词子串匹配。
- `run-read`——claim → 来源的回读通道：按原始 run_id 读取程序历史投影，渲染为有界摘要（触发语 + 各轮回复 + 工具活动；触发截 300 字、每轮回复截 600 字、最多列 12 轮、总量硬上限 12000 字符）。claim 是压缩指针，journal 是原文真相（ReadAgent 模式）。
- `forget`——删除记忆；默认预览模式（只报匹配），`confirm: true` 才真正删。两步确认是给 LLM 的防误删闸门。

工具的读写都只落在本 agent 的目录——`MemoryManager` 构造时绑定 `agent_id`，工具层没有跨 agent 访问的入口。记忆私有性在这一层是结构性保证，不是约定。

## 反思：记忆的自我审计

[agents/reflection.py](../../src/tlon/agents/reflection.py) 的 ReflectionEngine 按间隔和未消费完成来源数触发审计；默认最少 5 个来源。使用宿主维护默认值（启用、30 分钟间隔），不引入 stage 配方。

`memory/reflection.json` 保存已消费来源、固定批次、digest、操作和应用进度。模型在稳定批次维护根运行，record_claim 使用稳定 ID 和来源引用；delete_claim 在应用前解析为固定目标 ID，恢复不会误删后来新增的记忆。失败保留原批次，重放不重复计数或重新生成操作。后台 task 的活动和关闭由运行器管理。

get_memory_digest 提供最近 50 条 active claims、未蒸馏来源数及最近 10 个有界交互投影。反思、教训和退休清点因此能读取尚未达到蒸馏窗口的新交互，不必先触发另一次模型调用。

值得注意的是反思能改的只有记忆：它可以记 claim、删 claim，但碰不到 spec——行为变更走的是另一条通道：成员把经验显式交给普通 `tlon.authoring/draft`，通过查文档、编写与校验修订产出完整模块包，落到 staging 信任闸等人类批准，反思本身仍不直接改写行为。

## 退休蒸馏：记忆的代际出口

反思是生者的自我审计；退休蒸馏是死者的遗产整理。[agents/lessons.py](../../src/tlon/agents/lessons.py) 的 `LessonsDistiller` 是纯事件消费者：订阅 `AGENT_STOPPED`（蓄意停止），对退休成员做一次离线蒸馏。

三个关键设计：

- **离线路径**。蒸馏发生在 member runtime shutdown 之后，所以蒸馏器**离线重建**归档数据：用显式 `data_dir` 重建 `MemoryManager`、用 `RunJournal` 满足 digest 需要的 journal 读面，不与任何活着的运行态共享对象。
- **只挂蓄意停止**。`AGENT_SLEEPING`/`AGENT_IDLE`（drain 是日常 scale-to-zero 睡眠，反复发生）不触发蒸馏——退休才有遗产，午睡没有。
- **失败不阻断**。没有可用记忆服务或 digest 为空直接跳过；LLM 失败只记日志不 raise——停止流程已经完成，这只是事后簿记。

蒸馏调用沿用反思的参数先例（timeout 60 秒；采样参数不配置，供应商默认生效），`purpose="lessons"`，并且**用退休成员自己的 agent_id 构造 LLMClient**——最后一笔账记在它头上（per-member 账本自然覆盖）。产出落盘为 `~/.tlon/{agent_id}/memory/lessons.md`：不是全量记忆的复述，而是「能传给下一代的教训」的提炼（prompt 明确约束）。

`lessons.md` 只是原材料。它进入基因库的通道在原型侧：经 `prototype-write` / admin API 的 `lessons` 参数包装成原型的 `LESSONS.md` 随包传递，`instantiate` 拷贝到新成员的 `config/LESSONS.md`，spec 的 `(lessons)` context section 声明后才注入对话（见 [prototypes.md](./prototypes.md) 与 [unified-member-model.md](./unified-member-model.md)）。死亡归档清点由 `RetirementCeremony`（见 [mail.md](./mail.md)「死亡归档清点」）——蒸馏沉淀给下一代，归档清点给同代的接管者。

## 一次交互的完整链路

把上面各节串起来，一封邮件从进入到沉淀的全链路是：

1. 入口将完成状态、结果及调度意图提交到唯一执行权威 ProgramStore。
2. RunJournal 只读取 ProgramStore，运行也只由 ProgramStore 恢复；完成计数和最近交互由元数据与回执派生。
3. 蒸馏和反思按稳定来源 ID 与固定批次独立消费，不重复调用模型或追加相同 claim。
4. thread recap、run-read、退休归档和教训读取同一视图；来源 ID 可回读，客户端连接 `/sessions` 无变动。

## 回归集

记忆系统的独立回归评测是：[scripts/eval_memory.py](../../scripts/eval_memory.py) + 场景数据集 [scripts/memory_eval/](../../scripts/memory_eval/README.md)（14 个中文邮件场景，对齐 LongMemEval 的四个维度——事实召回、知识更新、时序、拒答）。评测跑真实链路：每个场景在隔离的临时目录里重建 MemoryManager，假 journal 灌入脚本化 run（`run/start` 时间戳控制蒸馏顺序，window=1 逐 run 蒸馏），然后用场景 query 打两个检索面——JIT 注入面（`build_context_blocks`）与检索面（`search_memories`）——断言「该出现的出现、不该出现的（被取代的旧事实、无关 claim、编造内容）不出现」。知识更新与时序维度实际验证的就是冲突判定 supersede 的端到端效果：旧 claim 失效不删除、可审计，但绝不能进检索结果。

这是需要真实模型的手动评测，独立于常规质量门：评测需要真实 LLM（蒸馏 + 冲突判定）与 embedding 调用——花钱、要 API key，且 LLM 产出有固有波动（脚本允许单场景重试一次再判 FAIL）。运行：

```bash
uv run python scripts/eval_memory.py \
    --model deepseek/deepseek-chat \
    --embedding-model openai/text-embedding-3-small
```

embedding 模型必须显式指定（JIT 检索无从降级评测，缺失直接报错）；API key 按 `{PROVIDER}_API_KEY` 约定读 `~/.tlon/.env`。逐题 PASS/FAIL、失败打印实际检索内容、按维度汇总，退出码可脚本化。场景格式与加新场景的方法见 [scripts/memory_eval/README.md](../../scripts/memory_eval/README.md)。

## 设计取舍

### 为什么是「journal + claims」而不是外挂记忆产品

ADR 0017 的业界调研结论：主流现成 memory 产品无可直接引入者（生态绑死、停更或需外挂图数据库），且 Anthropic/Manus/LangChain 三家独立收敛到「文件 + 笔记 + 周期摘要 + 选择性注入」——Tlon 的范式方向没错，缺的是检索层与事实生命周期。于是整体替换自研：

- **可解释**——每条 claim 都记着 `source_run_ids`，可疑的断言可以回到 journal 逐事件核对。
- **可编辑**——JSONL 是纯文本，人类可以直接打开审计、改错、删除；`forget` 工具的两步确认也建立在「记忆是可枚举条目」之上。
- **可审计**——落盘即事实。`~/.tlon/{agent_id}/memory/` 下的文件与 `program.sqlite3` 分别保存记忆产物与执行事实；旧 `sessions.sqlite3` 不再读取，也不迁移或删除用户磁盘归档（ADR 0049）。
- **有边界的重建**——journal 能重新提供自动蒸馏的输入，但重跑 LLM 不保证相同产物；主动 `memory-remember` 与反思写入也会产生 claim，不能宣称全部 claims 可由 journal 无损重建。可完全丢弃并重建的是向量索引。

### 为什么蒸馏用 LLM 而不是规则

哪句话是决策、哪个偏好值得长期保留，规则写不出来。但 LLM 蒸馏有两个固有风险，设计上分别兜住：**丢内容**靠蒸馏输入永不删除（journal 只增不减，失败时固定批次保留，后续维护恢复原任务）与 fallback 机械 claim（蒸馏失败不等于 run 白跑）；**编内容**靠 prompt 约束（「不要推测」）加 `source_run_ids` 溯源。反思引擎是这个信任模型的第二层：蒸馏时没提取到的、提取错的，由反思在事后审计中补记或删除。

### 为什么记忆是 agent 私有的

书信共和国的结构决定了这一点：社会的公共知识在邮件里（持久、可检索、可流传），个体记忆是个体的「脑内状态」。如果记忆可以共享，就绕开了邮件这个唯一公共通道——agent 之间会开始用共享存储交换信息，「平等、开放、沉淀」三条全部漏风。记忆私有 + 邮件公共，是把「想告诉别人的知识必须写成信」变成结构性约束：一个 agent 想让同伴知道什么，只能发信。

## 演化接口与未实施方向

记忆到行为的回流由普通起草程序组合：调用方提供当前行为、记忆和教训，经资料查询与校验修订得到原型候选，再用 prototype-write 提交，经 staging 和人类批准后由新世代采用。它不直接修改当前成员的行为。代际教训与同代交接的边界见上文及 [prototypes.md](./prototypes.md)。

社会级公共记忆仍未实施：当前每个成员各自蒸馏和检索，邮件提供共享原材料，但没有社会级存档提炼、共享摘要和公共知识检索机制。

新 claim 的 origin 标识 remember 或 distillation；其来源引用用于追踪，不能当成蒸馏完成证据。反思引用交互后，蒸馏仍以自己的消费记录判断进度，两个消费域互不替代。

## 维护失败、诊断与人工处置

v2 蒸馏和反思的临时故障按 30、60、120、240、300 秒退避，后续上限为 300 秒；等待期间不再调用模型、写 checkpoint 或发布维护活动。固定根上的额度耗尽、截止、provider 已拒绝、模型/外部效果 unknown、恢复失配及损坏 checkpoint 会阻塞本进程内的对应维护任务。错误通过成员 diagnostic_error 上浮，正常收发邮件和另一项独立维护不因此停止。进程重启只重新核对原批次与回执，不给原根新额度，也不绕过 unknown。

checkpoint 保存冻结输入、claims/operations 和应用进度。损坏 JSON、截断或错误结构因此不能当成空进度：文件保持原字节，蒸馏损坏仅隔离蒸馏，普通 claims 读取与记忆写入仍可用；反思损坏只隔离反思。缺少文件仍表示尚无维护进度。只含旧 `last_distilled_at` 的 checkpoint 不受支持，按不符合当前结构的 checkpoint 隔离，不进行兼容读取或迁移。

操作员处置顺序：

1. 从 GET /agents/{id} 的 error 字段（运行时 diagnostic_error）记录维护类别、失败类型、文件路径和日志中的固定根；先备份该成员的 program.sqlite3、memory/distillation.json、memory/reflection.json 与 claims.jsonl，保留已冻结产物。
2. 临时 I/O 故障修复后等待退避即可。checkpoint 损坏时从可信备份恢复原批次和进度；无法确认的进度保持隔离，不删除文件让系统重新生成。实例重建后重新加载，例如关闭并重启 dream；仅调用 resident 成员的 config reload 不会重建运行实例。
3. rejected、额度耗尽或已过截止的原批次不能靠重启继续请求模型。若人工明确放弃，先保留上述完整备份，再将该 pending 的来源加入对应 consumed_source_ids（蒸馏）或 seen（反思），并将 pending 设为 null，然后重建实例。这是人工跳过，已应用的记忆不回滚，未应用的候选不会再执行；备份保留被冻结的 claims/operations 与来源证据。
4. unknown 或恢复失配先核对权威回执及外部事实，不能通过换根、删除账本或重置 checkpoint 强行重跑。缺少可信证据时保持阻塞，当前没有自动猜测或补发流程。

当前固定批次一次包含全部待消费来源，长 backlog 仍可能形成较大 checkpoint；一个无法读取的来源会阻塞这一维护消费者，需恢复其权威数据后继续。批次大小和逐来源隔离尚未扩展，本修复不引入另一套维护队列。

退休成员首次执行教训模型维护时，如果此前没有 program.sqlite3，会为独立额度和模型回执建立该库；路径由 TlonPaths.agent_program_database_file 提供。单纯读取 run 交互不会创建程序库，也不会转换历史。
