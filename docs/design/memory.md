# 记忆系统设计

Tlon 的记忆不是检索增强的附件，而是 agent 认知循环的一部分：session journal（ADR 0007）是交互的唯一真相，已完成的 session 成批压缩成摘要与事实进入情景记忆，下一轮对话再把它们拼回上下文。本文解释这套系统为什么长现在这样，以当前实现为准（存储层在 [src/tlon/memory/](../../src/tlon/memory)，agent 侧工具在 [src/tlon/tools/memory/](../../src/tlon/tools/memory)，反思在 [src/tlon/agents/reflection.py](../../src/tlon/agents/reflection.py)）。

## 定位：可审计的个体记忆

[docs/vision.md](../vision.md) 要求「经验沉淀为知识」。在 Tlon 的语境里这条要求有两个约束：

- **行为即数据**——记忆也是数据。agent 能读、能写、能改自己的记忆，人类也能直接打开文件审计。
- **书信共和国**——社会的知识在邮件里积累，个体记忆只服务个体。记忆是 agent 私有的，共享靠发信，不靠共享存储。

这两条决定了记忆系统的基本形态：每个 agent 一个私有目录（`~/.tlon/{agent_id}/memory/`，路径由 [base/paths.py](../../src/tlon/base/paths.py) 的 `TlonPaths` 解析），全部内容是 JSONL 文本文件，没有数据库、没有向量索引。

还有一条隐含约束来自认知循环的架构：`tlon.memory` 在分层上只依赖 `tlon.base`（import-linter 强制），不认识 agent、邮件、工具。它提供的是纯粹的存储与摘要能力，「什么时候存、什么时候取、取多少拼进上下文」的决策都在 agents 层（spec 的 `:loop` 声明与 `ContextBuilder` / `SessionScheduler` 的装配）。记忆系统是被编排的机制，不自带策略——这与全系统「策略/机制分离」的原则一致。也是这条约束决定了摘要触发入口的形状：manager 不能 import journal，于是定义 `SessionLogReader` Protocol（`list_completed` + `read_events` 两个方法），`SessionJournal` 结构化满足它，由 scheduler 在回收点注入（ADR 0009）。

## 单层结构：journal 是真相，episodic 是沉淀

ADR 0009 之前系统有两层：working memory（滚动窗口存原始交互）与 episodic memory（摘要与事实）。ADR 0007 的 session journal 落地后，working memory 沦为 journal 的劣质拷贝——同一交互双写、格式各自漂移，fallback 摘要甚至把 agent 自己的回信当用户输入引用（working memory 里没有真 trigger，只有 `EffectExecutor` 伪造的 message）。ADR 0009 把 working memory 整层退役：**交互的原始记录就是 journal，记忆层只剩 episodic**。

[memory/manager.py](../../src/tlon/memory/manager.py) 的 `MemoryManager` 是统一入口，持有唯一的存储：

- **情景记忆**——[memory/episodic_store.py](../../src/tlon/memory/episodic_store.py) 的 `EpisodicMemoryStore`，两个文件 `episodic/summaries.jsonl`（批量会话摘要，上限 20）与 `episodic/facts.jsonl`（结构化事实，上限 50）。这是长期层，由批量压缩与 agent 主动记录共同填充。

情景记忆有两个入口：系统自动批量压缩（`maybe_summarize_completed_sessions`）、agent 主动记录（`rememberFact`）与反思补记（`record_fact`）——长期层允许多方填充，因为它本来就是筛选后的判断而不是原始记录。原始记录的「层」没有消失，而是上移到了 journal：它在 agents 层、随 session 生命周期管理，本就不该在 memory 包里复制一份。

存储格式统一是 JSONL，每行一条记录。写入策略是**追加优先**：`add` 只追加新行，整文件重写（临时文件 + `os.replace` 原子替换）只发生在三种情况——加载时裁剪超限、删除/替代标记修改了已有行、追加失败后的兜底。追加优先的理由：JSONL 追加是崩溃安全的最低成本形态，进程死在两次写之间最多损失最后半行（加载时逐行解析、坏行跳过告警），而全量重写一旦中途崩溃就丢整个文件——所以重写必须走原子替换。

store 一把 `asyncio.Lock`，缓存即真相（`_cache`），磁盘是缓存的持久化。进程内并发只有协作式抢占一个维度，单锁足够。`MemoryManager.load` 还有一层配置加载语义：`memory/config.json` 是**唯一的**记忆调参面——存在时以磁盘文件为准（绑定 `agent_id`），不存在时用构造传入的 `MemoryConfig`（生产装配传默认值）落盘初始化；合并后的 `episodic` 上限在 store 加载前重新应用（上限在构造时快照，不重应用则调参不生效）。早期版本曾有两套配置（`agent.yaml` 的 `memory:` 段 + 按 `agent_id` 是否预填决定是否合并的 `config.json`），两套在生产都不生效，已收敛为单一磁盘文件。ADR 0009 之前的 config.json 里 `working`/`triggers` 段在读取时自然忽略（未知段不读），无 migration。

`MemoryConfig` 的默认值定义了整个系统的量级：批量摘要窗口 3 个 session、摘要上限 20、事实上限 50；进上下文的配额是摘要 5 / 事实 10；反思至少间隔 5 个新完成 session。这些数字是容量与 token 成本之间的手工平衡点，集中在 [memory/models.py](../../src/tlon/memory/models.py) 一处。

### 批量压缩：journal → episodic

压缩由 [memory/summarizer.py](../../src/tlon/memory/summarizer.py) 的 `Summarizer` 完成：一次 LLM 调用生成中文摘要（含 `key_decisions` 与 `action_items`），再一次提取结构化事实（subject/predicate/object + 置信度），入口是 `process_sessions`。两次调用都是 30 秒超时，prompt 要求模型以 JSON 返回，`_extract_json` 容忍 markdown 代码块包裹——对 LLM 输出的鲁棒性做在解析层。事实提取时管理器先取最近 20 条已有事实传入，提取器把其中最近 10 条写进 prompt（「不要重复提取」），把去重职责交给模型而不是存储层。LLM 不可用时（未配置或调用失败）回退到 `_generate_fallback_summary`——统计会话数与工具调用数、引用首尾 session 的真 trigger 的机械摘要。降级是设计意图：记忆压缩不能因 LLM 抖动而丢失；且因为输入来自 journal，fallback 引用的也是真实用户请求而不是 agent 的回信（ADR 0009 修掉的冒充 bug）。

Summarizer 的输入是 **session 事件投影**（`SessionProjection`，[memory/models.py](../../src/tlon/memory/models.py)）：`trigger_content`（`session/start` 的真触发内容）+ 每轮 `llm/response` 的 content + 每条 `tool/result` 的一行摘要（工具名 + message/error/data 截断）。投影由 `MemoryManager._build_projection` 从 journal 事件重建——摘要流不经过任何中间存储。

触发链条在 [agents/scheduler.py](../../src/tlon/agents/scheduler.py) 的回收点：session 完成（`is_completed` 分支，`_ensure_session_end` 落盘之后），scheduler 先给反思引擎计数（`note_interaction`），再调 `MemoryManager.maybe_summarize_completed_sessions(journal)`。后者求差集——`journal.list_completed()`（glob sessions 目录、过滤含 `session/end` 的日志）减去最近 20 条 Summary 记录的 `source_session_ids`——pending 数达到 `summary_window`（默认 3）就把整批投影交给 Summarizer，产出一条 Summary 记录全部 `source_session_ids`。整个触发永不抛进调度循环：LLM 失败回退机械摘要，落盘失败记日志、下次回收点重试（pending 集合由 Summary 记录推导，没写成自然还是 pending）。

与旧设计相比，两点刻意改变：一是**摘要不删除任何输入**——journal 只增不减（同 ADR 0006/0007 的存储哲学），旧设计的 snapshot + delta-clear 随之消失；二是**没有空闲触发**——`check_idle_timeout` 与 `_last_interaction_time` 整体移除，摘要只挂在 session 完成节拍上。

### 事实的替代语义

事实不是不可变日志。`EpisodicMemoryStore._add_fact_locked` 发现同（subject, predicate, object）的旧事实时，给旧事实打 `superseded_by` 标记指向新事实 id——新事实替代旧事实，历史保留。因为标记修改了已有行，这次写入必须整文件重写而不是追加（代码里有专门注释说明）。`get_recent_facts` 与搜索都跳过被替代的事实。这让「用户改主意了」「配置变了」这类演化有原生的表达：不删历史，但当前视图只看最新。

## 数据模型与存储

全部模型在 [memory/models.py](../../src/tlon/memory/models.py)，都是纯数据 dataclass：

- `Summary`——一次批量压缩的产出：摘要文本、`source_session_ids`（来源 session 的 id 列表，可追溯、也是去重依据）、`key_decisions` / `action_items` / `keywords`。`from_dict` 容忍旧 `summaries.jsonl` 行缺 `source_session_ids`（旧行已在用户磁盘上，按空集读——这是 ADR 0009 唯一的兼容豁免）。
- `Fact`——一条结构化事实：`fact_type`（`USER_PREFERENCE` / `PROJECT_STATE` / `TECH_STACK` / `DECISION` / `GENERAL`）、subject/predicate/object 三元组、自然语言 `description`、`confidence`、`superseded_by`。
- `SessionProjection`——一个已完成 session 的摘要视图：session id、真 trigger、各轮回复 content、工具结果摘要行。从 journal 重建，不落盘。
- `ContextBlock`——上下文注入的单元：类型（`episodic_summaries` / `key_facts`）、优先级、渲染好的文本、`source_ids`（进上下文的每一块都能指回 JSONL 行）。

## 上下文注入

记忆的回流通道是 `MemoryManager.build_context_blocks`：按固定配额取最近的内容拼成两个 `ContextBlock`——情景摘要 5 条（priority 1）、事实 10 条（priority 2），按优先级排序返回。`ContextBlock.to_openai_message` 把每块渲染成带中文标签（「过往会话摘要」「已知事实」）的文本。

注入点是 [agents/context_builder.py](../../src/tlon/agents/context_builder.py) 的 `ContextBuilder`：spec 的 `:context` 段向量里出现 `(knowledge)` 时，`build_context` 调 `build_knowledge_context` 把这些块拼成一条 `[已知信息]` 开头的 user 消息，按 section 顺序插入对话；当前邮件永远最后追加（引擎拥有这个位置，DSL 只描述它之前的内容）。是否注入知识因此是 spec 层的声明——fast-responder 原型的 `:context` 里没有 `(knowledge)`，它就不带记忆上场，这是 cost routing 的一部分。

一个细节体现实用主义：`build_knowledge_context` 把记忆构建的异常整个兜住（记告警、返回空串），记忆损坏不阻断对话，空记忆等价于没有 `(knowledge)` 段。记忆是增强，不是依赖。

配额是硬编码在 `MemoryConfig`（[memory/models.py](../../src/tlon/memory/models.py)）里的默认值，落盘为 `memory/config.json`，与 spec 无关——记忆策略（存多少、取多少）目前是配置而不是行为数据，这是一个有意的边界（见「方向」）。

## agent 侧接口

agent 通过 [src/tlon/tools/memory/](../../src/tlon/tools/memory) 下的三个工具读写自己的记忆，全部在 `assemble_cognitive`（[agents/assembly.py](../../src/tlon/agents/assembly.py)）装配认知成员时注册，直接持有本 agent 的 `MemoryManager` 实例：

- `rememberFact`（`remember_fact.py` 的 `RememberFactTool`）——主动记录一条事实进 `facts.jsonl`。
- `searchMemories`（`search_memories.py` 的 `SearchMemoriesTool`）——关键词搜索摘要与事实，纯子串匹配（`search_summaries_by_keyword` / `search_facts_by_keyword`），无 embedding。
- `forget`（`forget_memory.py` 的 `ForgetMemoryTool`）——删除记忆；默认预览模式（只报匹配数），`confirm: true` 才真正删。两步确认是给 LLM 的防误删闸门。

ADR 0009 删掉了第四个工具 `compact`：主动压缩的输入（当前工作记忆）已不存在，压缩本身是系统按窗口自动做的，模型没有需要催的东西。

工具的读写都只落在本 agent 的目录——`MemoryManager` 构造时绑定 `agent_id`，工具层没有跨 agent 访问的入口。记忆私有性在这一层是结构性保证，不是约定。

## 反思：记忆的自我审计

[agents/reflection.py](../../src/tlon/agents/reflection.py) 的 `ReflectionEngine` 是 agent 对自己记忆的定期审计。触发门控是双条件：距上次反思超过 `rule.loop.reflect.interval_minutes`（来自 spec 的 `:reflect` 段），**且**期间新完成的 session 数达到 `reflection_min_interactions`（默认 5，在 memory config 里）。两个条件一个防过频、一个防空转。计数由 `SessionScheduler` 在 session 回收点喂给 `note_interaction`（ADR 0009：从 EffectExecutor 挪到回收点，语义从「落交互」变为「完成 session」）；检查由调度器在循环间隙经注入的 `check_reflection` 委托——反思不是独立的后台定时器，而是挂在调度器的节拍上，进程里没有第二条时钟。

审计本身是「快照 → LLM 分析 → 应用操作」三步：`get_memory_digest` 取记忆状态快照（未摘要 session 数、最近 20 条摘要、最近 50 条事实），交给 LLM 按 `REFLECTION_SYSTEM_PROMPT` 分析（低温度 0.3、60 秒超时——审计要稳定不要发散），返回的操作列表（`record_fact` / `delete_fact` / `noop`）逐个应用回 `MemoryManager`。反思的开关与间隔来自 spec 的 `:reflect` 段（`rule.loop.reflect`），因此它是行为数据的一部分——原型可以声明不同的反思频率（archivist 5 分钟、deep-thinker 10 分钟、fast-responder 15 分钟），但最小 session 数阈值仍在 memory config 里，这是 spec 与配置分管反思策略的现状。

ADR 0009 删掉了反思的 `consolidate` 操作（连同 prompt 里「工作记忆 >= 5 条建议 consolidate」的硬编码）：摘要触发已经全自动，不需要反思来催；digest 的 `working_count` 相应改为「未摘要 session 数」，让反思能看到压缩积压的事实，但不再能干预它。

应用操作时单个失败只记日志不影响其余；整个过程跑在后台 task 里（`_running` 标志防重入），不阻塞认知循环。原则写进了 prompt：多数情况下 `noop` 是正常的——反思的默认产出是「什么都不做」，这与双门控一起压住 LLM 自我修改的频率。

`delete_fact` 的删除按事实 id 定点执行：prompt 里事实以 id 前 8 位列出，模型回传的 `fact_id` 交给 `MemoryManager.forget_fact`，先精确匹配、再退到唯一前缀解析，未命中或歧义都记 warning、不删任何东西——删除失败必须可见，不允许静默 no-op（issue ：旧实现把 uuid 塞进关键词子串匹配，永远匹配不到任何事实文本）。模型只给 `description` 时回退到 `forget` 的关键词匹配路径。

值得注意的是反思能改的只有记忆：它可以记事实、删事实，但碰不到 spec。这与「记忆只进 context」是同一个边界的两面（见「方向」）。

## 退休蒸馏：记忆的代际出口（issue ）

反思是生者的自我审计；退休蒸馏是死者的遗产整理。[agents/lessons.py](../../src/tlon/agents/lessons.py) 的 `LessonsDistiller` 是纯事件消费者（MetricsService 同款模式，但放 agents 层——它依赖 memory + agents）：订阅 `AGENT_STOPPED`（蓄意停止：`stopAgent` 工具 / DSL stop 动作 → `AgentRegistry.stop_member` 完成后发布），对退休成员做一次离线蒸馏。

三个关键设计：

- **离线路径**。蒸馏发生在 member runtime shutdown 之后——在飞反思已被取消（member_runtime.shutdown 的顺序保证），所以蒸馏器**离线重建**归档数据：用显式 `data_dir` 重建 `MemoryManager`、用 `SessionJournal` 满足 digest 需要的 journal 读面，不与任何活着的运行态共享对象。
- **只挂蓄意停止**。`AGENT_SLEEPING`/`AGENT_IDLE`（drain 是日常 scale-to-zero 睡眠，反复发生）不触发蒸馏——退休才有遗产，午睡没有。
- **失败不阻断**。非认知成员（无 `:loop` 的 spec）或 digest 为空直接跳过；LLM 失败只记日志不 raise——停止流程已经完成，这只是事后簿记。

蒸馏调用沿用反思的参数先例（temperature 0.3、timeout 60 秒），`purpose="lessons"`，并且**用退休成员自己的 agent_id 构造 LLMClient**——最后一笔账记在它头上（ 的 per-member 账本自然覆盖），LLMConfig 从其 `agent.yaml` 的 `llm` 段 + settings 构造（与 `assemble_cognitive` 同一条合并路径）。产出落盘为 `~/.tlon/{agent_id}/memory/lessons.md`：不是全量记忆的复述，而是「能传给下一代的教训」的提炼（prompt 明确约束）。

`lessons.md` 只是原材料。它进入基因库的通道在原型侧：经 `writePrototype` / admin API 的 `lessons` 参数包装成原型的 `LESSONS.md` 随包传递，`instantiate` 拷贝到新成员的 `config/LESSONS.md`，spec 的 `(lessons)` context section 声明后才注入对话（见 [prototypes.md](./prototypes.md) 与 [unified-member-model.md](./unified-member-model.md) 决策 9 补注）。退休交接信（）已落地为 `RetirementCeremony`（见 [mail.md](./mail.md)「退休交接信」）——蒸馏沉淀给下一代，交接信盘点给同代的接管者；把 lessons 自动写成提案（）仍是后续项。

## 一次交互的完整链路

把上面各节串起来，一封邮件从进入到沉淀的全链路是：

1. 认知循环跑完，journal 里留下完整的 session 事件流（ADR 0007：模型可见 ⇒ 已入日志）——不再有第二份交互拷贝。
2. scheduler 在回收点做两件事：`note_interaction` 喂反思计数；`maybe_summarize_completed_sessions` 检查 pending 窗口——满 3 个已完成 session 就从各自 journal 重建投影、批量摘要写 episodic。
3. 调度器在循环间隙另做周期检查：`check_reflection`（间隔与计数双门控通过则后台跑反思）。
4. 下一封邮件进来时，`ContextBuilder` 按 spec 的 `(knowledge)` 段把两类记忆块拼成 `[已知信息]` 注入对话。

整条链路里没有定时器、没有后台守护进程：压缩与反思全部挂在认知循环的事件节拍上。agent 不活动，记忆就不动——这与整个系统「事件驱动、睡觉零成本」的运行模型一致。

## 设计取舍

### 为什么是「journal + 摘要/事实」而不是向量库

主流方案是给 agent 挂一个向量数据库做语义检索。Tlon 选了 journal 真相源 + 摘要/事实 JSONL，理由是：

- **可解释**——每一块进上下文的记忆都能指回具体的 JSONL 行（`ContextBlock.source_ids` 保留了来源 id；`Summary.source_session_ids` 指回具体 session 日志）。向量检索的「为什么召回这条」没有答案，journal 溯源有。
- **可编辑**——JSONL 是纯文本，人类可以直接打开审计、改错、删除；`forget` 工具的两步确认也建立在「记忆是可枚举条目」之上。向量库里的 embedding 不可读、不可定点修改。
- **可审计**——落盘即事实。`~/.tlon/{agent_id}/memory/` 与 `sessions/` 下的文件就是 agent 经历与记得的全部，没有第二个真相。
- **够用**——单 agent 的记忆量级（几十条摘要、几十条事实）下，关键词子串搜索与「取最近 N 条」的精度损失可以接受，换来的是零外部依赖。

代价同样明确：没有语义召回，「换个说法就搜不到」；配额窗口意味着旧记忆会被裁剪而非归档检索。这在当前规模下是可接受的交换。

### 为什么压缩用 LLM 而不是规则

摘要与事实提取是理解任务——哪句话是决策、哪个偏好值得长期保留，规则写不出来。但 LLM 压缩有两个固有风险，设计上分别兜住：**丢内容**靠摘要输入永不删除（journal 只增不减，压缩失败的内容下次窗口仍在 pending 集合里）与 fallback 摘要（压缩失败不等于 session 白跑）；**编内容**靠 prompt 约束（「只提取明确提及、不要推测」）加 `source_session_ids` 溯源——每条摘要都记着自己从哪些 session 压缩而来，可疑的摘要可以回到 journal 逐事件核对。反思引擎是这个信任模型的第二层：压缩时没提取到的、提取错的，由反思在事后审计中补记或删除。

### 为什么记忆是 agent 私有的

书信共和国的结构决定了这一点：社会的公共知识在邮件里（持久、可检索、可流传），个体记忆是个体的「脑内状态」。如果记忆可以共享，就绕开了邮件这个唯一公共通道——agent 之间会开始用共享存储交换信息，「平等、开放、沉淀」三条全部漏风。记忆私有 + 邮件公共，是把「想告诉别人的知识必须写成信」变成结构性约束：一个 agent 想让同伴知道什么，只能发信。

## 方向

与愿景的差距主要有两处：

- **个体学习未闭环**——记忆的出口曾只有 context； 补上了代际出口（退休蒸馏 → `lessons.md` → 原型 `LESSONS.md` → 新成员的 `(lessons)` section）， 补上了同代出口（退休交接信把未竟事项送达接管者），经验由此能随出生包传给下一代、随告别信交给同代。仍缺的一环是「经验改写行为」本身：lessons 进的是对话上下文，spec 不因它改变——从记忆/教训到 spec 修订提案的自动回流（）尚未建设。
- **无社会级公共记忆**——每个 agent 各自摘要、各自记事实，社会层面没有可检索的公共层。邮件提供了沉淀的原材料，但「把书信提炼成社会知识」的机制（存档、共享摘要、同行评议筛选后的公共记忆）还不存在。
