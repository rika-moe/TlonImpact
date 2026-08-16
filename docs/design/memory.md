# 记忆系统设计

Tlon 的记忆不是检索增强的附件，而是 agent 认知循环的一部分：交互落盘为工作记忆，工作记忆满了就压缩成摘要与事实进入情景记忆，下一轮对话再把它们拼回上下文。本文解释这套系统为什么长现在这样，以当前实现为准（存储层在 [src/tlon/memory/](../../src/tlon/memory)，agent 侧工具在 [src/tlon/tools/memory/](../../src/tlon/tools/memory)，反思在 [src/tlon/agents/reflection.py](../../src/tlon/agents/reflection.py)）。

## 定位：可审计的个体记忆

[docs/vision.md](../vision.md) 要求「经验沉淀为知识」。在 Tlon 的语境里这条要求有两个约束：

- **行为即数据**——记忆也是数据。agent 能读、能写、能改自己的记忆，人类也能直接打开文件审计。
- **书信共和国**——社会的知识在邮件里积累，个体记忆只服务个体。记忆是 agent 私有的，共享靠发信，不靠共享存储。

这两条决定了记忆系统的基本形态：每个 agent 一个私有目录（`~/.tlon/{agent_id}/memory/`，路径由 [base/paths.py](../../src/tlon/base/paths.py) 的 `TlonPaths` 解析），全部内容是 JSONL 文本文件，没有数据库、没有向量索引。

还有一条隐含约束来自认知循环的架构：`tlon.memory` 在分层上只依赖 `tlon.base`（import-linter 强制），不认识 agent、邮件、工具。它提供的是纯粹的存储与摘要能力，「什么时候存、什么时候取、取多少拼进上下文」的决策都在 agents 层（spec 的 `:loop` 声明与 `EffectExecutor` / `ContextBuilder` 的装配）。记忆系统是被编排的机制，不自带策略——这与全系统「策略/机制分离」的原则一致。

## 双层结构

[memory/manager.py](../../src/tlon/memory/manager.py) 的 `MemoryManager` 是统一入口，协调两层存储：

- **工作记忆**——[memory/working_store.py](../../src/tlon/memory/working_store.py) 的 `WorkingMemoryStore`，单个文件 `working/interactions.jsonl`，存原始交互（收到的邮件、agent 的回复、工具调用与结果），上限 `working_max_items`（默认 10）。这是一个滚动窗口：超出上限裁掉最旧的。
- **情景记忆**——[memory/episodic_store.py](../../src/tlon/memory/episodic_store.py) 的 `EpisodicMemoryStore`，两个文件 `episodic/summaries.jsonl`（会话摘要，上限 20）与 `episodic/facts.jsonl`（结构化事实，上限 50）。这是长期层，由压缩与 agent 主动记录共同填充。

分层的理由是两种数据的生命周期不同：原始交互量大、时效短、格式规整，适合滚动窗口；摘要与事实量小、长期有效、需要被搜索，适合独立存储。两层共用一个目录、各自一把 `asyncio.Lock`，写入是追加优先——`_persist`（临时文件 + `os.replace` 原子重写）只在裁剪、删除或追加失败时兜底。

两层的填充渠道不对称，这也是设计的一部分。工作记忆只有一个入口：认知循环结束时系统自动落交互，agent 无法直接写——它是「刚刚发生了什么」的客观记录，不接受主观筛选。情景记忆有三个入口：系统自动压缩（`generate_summary`）、agent 主动记录（`rememberFact`）、反思补记（`record_fact`）——长期层允许多方填充，因为它本来就是筛选后的判断而不是原始记录。读侧也对应分开：工作记忆按窗口取最近，情景记忆按配额取最近加关键词搜索。

### 压缩：snapshot + delta-clear

压缩由 [memory/summarizer.py](../../src/tlon/memory/summarizer.py) 的 `Summarizer` 完成：一次 LLM 调用生成中文摘要（含 `key_decisions` 与 `action_items`），再一次提取结构化事实（subject/predicate/object + 置信度），入口是 `process_interactions`。两次调用都是 30 秒超时，prompt 要求模型以 JSON 返回，`_extract_json` 容忍 markdown 代码块包裹——对 LLM 输出的鲁棒性做在解析层。事实提取时管理器先取最近 20 条已有事实传入，提取器把其中最近 10 条写进 prompt（「不要重复提取」），把去重职责交给模型而不是存储层。LLM 不可用时（未配置或调用失败）回退到 `_generate_fallback_summary`——只统计交互数与首尾消息的机械摘要。降级是设计意图：记忆压缩不能因 LLM 抖动而丢失。

触发有两条路径，都在 `MemoryManager` 上：

1. **容量触发**——`add_interaction` 之后 `_check_summary_trigger` 检查工作记忆是否满，满则 `generate_summary`。
2. **空闲触发**——`check_idle_timeout` 由 [agents/scheduler.py](../../src/tlon/agents/scheduler.py) 的 `SessionScheduler` 在循环间隙调用，距上次交互超过 `idle_timeout_minutes`（默认 30）且有未压缩内容时强制摘要。

`generate_summary` 的并发处理值得单独说明：先在 store 锁内对工作记忆做快照，**释放锁**再做 LLM 调用（可能很慢），完成后 `remove_by_ids` 只删快照里的条目——LLM 调用期间新进的交互被保留。这是「快照 + 增量清除」，避免了长调用持锁阻塞认知循环，也避免了摘要完成后误删新交互。

### 事实的替代语义

事实不是不可变日志。`EpisodicMemoryStore._add_fact_locked` 发现同（subject, predicate, object）的旧事实时，给旧事实打 `superseded_by` 标记指向新事实 id——新事实替代旧事实，历史保留。因为标记修改了已有行，这次写入必须整文件重写而不是追加（代码里有专门注释说明）。`get_recent_facts` 与搜索都跳过被替代的事实。这让「用户改主意了」「配置变了」这类演化有原生的表达：不删历史，但当前视图只看最新。

## 数据模型与存储

全部模型在 [memory/models.py](../../src/tlon/memory/models.py)，都是纯数据 dataclass：

- `Interaction`——一次完整回合：收到的消息、agent 的回复、`tool_calls` 与 `tool_results`、时间戳与 session id。id 是 uuid，由模型默认生成。
- `Summary`——一次压缩的产出：摘要文本、`source_interactions`（来源交互的 id 列表，可追溯）、`key_decisions` / `action_items` / `participants` / `keywords`。
- `Fact`——一条结构化事实：`fact_type`（`USER_PREFERENCE` / `PROJECT_STATE` / `TECH_STACK` / `DECISION` / `GENERAL`）、subject/predicate/object 三元组、自然语言 `description`、`confidence`、`superseded_by`。
- `ContextBlock`——上下文注入的单元：类型（`episodic_summaries` / `key_facts` / `recent_working`）、优先级、渲染好的文本、`source_ids`（进上下文的每一块都能指回 JSONL 行）。

存储格式统一是 JSONL，每行一条记录。写入策略是**追加优先**：`add` 只追加新行，整文件重写（临时文件 + `os.replace` 原子替换）只发生在三种情况——加载时裁剪超限、删除/替代标记修改了已有行、追加失败后的兜底。追加优先的理由：JSONL 追加是崩溃安全的最低成本形态，进程死在两次写之间最多损失最后半行（加载时逐行解析、坏行跳过告警），而全量重写一旦中途崩溃就丢整个文件——所以重写必须走原子替换。

每个 store 一把 `asyncio.Lock`，缓存即真相（`_cache`），磁盘是缓存的持久化。进程内并发只有协作式抢占一个维度，单锁足够。`MemoryManager.load` 还有一层配置加载语义：`memory/config.json` 是**唯一的**记忆调参面——存在时以磁盘文件为准（绑定 `agent_id`），不存在时用构造传入的 `MemoryConfig`（生产装配传默认值）落盘初始化；合并后的 `working`/`episodic` 上限在 store 加载前重新应用到两个 store（上限在构造时快照，不重应用则调参不生效）。早期版本曾有两套配置（`agent.yaml` 的 `memory:` 段 + 按 `agent_id` 是否预填决定是否合并的 `config.json`），两套在生产都不生效，已收敛为单一磁盘文件。

`MemoryConfig` 的默认值定义了整个系统的量级：工作记忆 10 条、摘要窗口 10 条、摘要上限 20、事实上限 50；进上下文的配额是摘要 5 / 事实 10 / 工作记忆 5；空闲 30 分钟触发压缩；反思至少间隔 5 条新交互。这些数字是容量与 token 成本之间的手工平衡点，集中在 [memory/models.py](../../src/tlon/memory/models.py) 一处。

## 上下文注入

记忆的回流通道是 `MemoryManager.build_context_blocks`：按固定配额取最近的内容拼成三个 `ContextBlock`（[memory/models.py](../../src/tlon/memory/models.py)）——情景摘要 5 条（priority 1）、事实 10 条（priority 2）、工作记忆 5 条（priority 3），按优先级排序返回。`ContextBlock.to_openai_message` 把每块渲染成带中文标签（「过往会话摘要」「已知事实」「最近工作记录」）的文本。

注入点是 [agents/context_builder.py](../../src/tlon/agents/context_builder.py) 的 `ContextBuilder`：spec 的 `:context` 段向量里出现 `(knowledge)` 时，`build_context` 调 `build_knowledge_context` 把这些块拼成一条 `[已知信息]` 开头的 user 消息，按 section 顺序插入对话；当前邮件永远最后追加（引擎拥有这个位置，DSL 只描述它之前的内容）。是否注入知识因此是 spec 层的声明——fast-responder 原型的 `:context` 里没有 `(knowledge)`，它就不带记忆上场，这是 cost routing 的一部分。

两个细节体现实用主义：一是**截断**——工作记忆块里工具结果只留 stdout 前 200 字符、回复前 200 字符，防单条交互吃掉整个上下文窗口；二是**降级**——`build_knowledge_context` 把记忆构建的异常整个兜住（记告警、返回空串），记忆损坏不阻断对话，空记忆等价于没有 `(knowledge)` 段。记忆是增强，不是依赖。

写入侧的对称入口是 `EffectExecutor.save_interaction`（[agents/executor.py](../../src/tlon/agents/executor.py)）：认知循环结束时，若 spec 的 loop 声明了 `save-interaction true`，就把本轮邮件、LLM 回复与工具结果交给 `MemoryManager.add_interaction` 落工作记忆，同时喂给反思引擎的计数器。是否保存交互因此也是 spec 声明的——确定性成员（无 `:loop`）整个没有记忆管线，`MemoryManager` 只在 `_assemble_cognitive` 装配认知成员时创建。

配额是硬编码在 `MemoryConfig`（[memory/models.py](../../src/tlon/memory/models.py)）里的默认值，落盘为 `memory/config.json`，与 spec 无关——记忆策略（存多少、取多少）目前是配置而不是行为数据，这是一个有意的边界（见「方向」）。

## agent 侧接口

agent 通过 [src/tlon/tools/memory/](../../src/tlon/tools/memory) 下的四个工具读写自己的记忆，全部在 `AgentRegistry._assemble_cognitive`（[agents/agent_registry.py](../../src/tlon/agents/agent_registry.py)）装配认知成员时注册，直接持有本 agent 的 `MemoryManager` 实例：

- `rememberFact`（`remember_fact.py` 的 `RememberFactTool`）——主动记录一条事实进 `facts.jsonl`。
- `searchMemories`（`search_memories.py` 的 `SearchMemoriesTool`）——关键词搜索摘要与事实，纯子串匹配（`search_summaries_by_keyword` / `search_facts_by_keyword`），无 embedding。
- `compact`（`compact.py` 的 `CompactTool`）——立即强制压缩工作记忆，`instructions` 参数指定保留重点，透传给 Summarizer 的 prompt。
- `forget`（`forget_memory.py` 的 `ForgetMemoryTool`）——删除记忆；默认预览模式（只报匹配数），`confirm: true` 才真正删。两步确认是给 LLM 的防误删闸门。

工具的读写都只落在本 agent 的目录——`MemoryManager` 构造时绑定 `agent_id`，工具层没有跨 agent 访问的入口。记忆私有性在这一层是结构性保证，不是约定。

工具之外，`MemoryManager` 还有一组面向人类的展示操作——`get_history_text` / `get_summary_text` / `get_facts_text`，把工作记忆、摘要、事实渲染成 Markdown 文本。它们与 LLM 工具共享同一份存储但走不同的格式化路径：给 LLM 的讲配额与截断，给人类的讲完整可读。这也再次体现「JSONL 落盘即可审计」——人类看到的就是 agent 记得的。

## 反思：记忆的自我审计

[agents/reflection.py](../../src/tlon/agents/reflection.py) 的 `ReflectionEngine` 是 agent 对自己记忆的定期审计。触发门控是双条件：距上次反思超过 `rule.loop.reflect.interval_minutes`（来自 spec 的 `:reflect` 段），**且**期间新增交互数达到 `reflection_min_interactions`（默认 5，在 memory config 里）。两个条件一个防过频、一个防空转。计数由 `EffectExecutor.save_interaction`（[agents/executor.py](../../src/tlon/agents/executor.py)）在每次落交互后喂给 `note_interaction`；检查由 `SessionScheduler` 在循环间隙经注入的 `check_reflection` 委托——反思不是独立的后台定时器，而是挂在调度器的节拍上，进程里没有第二条时钟。

审计本身是「快照 → LLM 分析 → 应用操作」三步：`get_memory_digest` 取记忆状态快照（工作记忆计数、最近 20 条摘要、最近 50 条事实），交给 LLM 按 `REFLECTION_SYSTEM_PROMPT` 分析（低温度 0.3、60 秒超时——审计要稳定不要发散），返回的操作列表（`record_fact` / `delete_fact` / `consolidate` / `noop`）逐个应用回 `MemoryManager`。反思的开关与间隔来自 spec 的 `:reflect` 段（`rule.loop.reflect`），因此它是行为数据的一部分——原型可以声明不同的反思频率（archivist 5 分钟、deep-thinker 10 分钟、fast-responder 15 分钟），但最小交互数阈值仍在 memory config 里，这是 spec 与配置分管反思策略的现状。

应用操作时单个失败只记日志不影响其余；整个过程跑在后台 task 里（`_running` 标志防重入），不阻塞认知循环。原则写进了 prompt：多数情况下 `noop` 是正常的——反思的默认产出是「什么都不做」，这与双门控一起压住 LLM 自我修改的频率。

值得注意的是反思能改的只有记忆：它可以记事实、删事实、触发压缩，但碰不到 spec。这与「记忆只进 context」是同一个边界的两面（见「方向」）。

## 一次交互的完整链路

把上面各节串起来，一封邮件从进入到沉淀的全链路是：

1. 认知循环跑完，`EffectExecutor.save_interaction` 按 spec 的 `save-interaction` 声明把本轮交互写入工作记忆，同时 `note_interaction` 喂反思计数。
2. `add_interaction` 之后检查工作记忆是否满（默认 10 条），满则立即压缩：快照 → Summarizer 生成摘要与事实 → 写入情景记忆 → delta-clear 快照条目。
3. 调度器在循环间隙另做两件周期检查：`check_idle_timeout`（空闲 30 分钟且有未压缩内容则强制压缩）、`check_reflection`（间隔与计数双门控通过则后台跑反思）。
4. 下一封邮件进来时，`ContextBuilder` 按 spec 的 `(knowledge)` 段把三类记忆块拼成 `[已知信息]` 注入对话。

整条链路里没有定时器、没有后台守护进程：压缩与反思全部挂在认知循环的事件节拍上。agent 不活动，记忆就不动——这与整个系统「事件驱动、睡觉零成本」的运行模型一致。

## 设计取舍

### 为什么是「工作/情景」两层而不是向量库

主流方案是给 agent 挂一个向量数据库做语义检索。Tlon 选了滚动窗口 + 摘要/事实两层 JSONL，理由是：

- **可解释**——每一块进上下文的记忆都能指回具体的 JSONL 行（`ContextBlock.source_ids` 保留了来源 id）。向量检索的「为什么召回这条」没有答案，分层窗口有。
- **可编辑**——JSONL 是纯文本，人类可以直接打开审计、改错、删除；`forget` 工具的两步确认也建立在「记忆是可枚举条目」之上。向量库里的 embedding 不可读、不可定点修改。
- **可审计**——落盘即事实。`~/.tlon/{agent_id}/memory/` 下的文件就是 agent 记得的全部，没有第二个真相。
- **够用**——单 agent 的记忆量级（几十条摘要、几十条事实）下，关键词子串搜索与「取最近 N 条」的精度损失可以接受，换来的是零外部依赖。

代价同样明确：没有语义召回，「换个说法就搜不到」；配额窗口意味着旧记忆会被裁剪而非归档检索。这在当前规模下是可接受的交换。

### 为什么压缩用 LLM 而不是规则

摘要与事实提取是理解任务——哪句话是决策、哪个偏好值得长期保留，规则写不出来。但 LLM 压缩有两个固有风险，设计上分别兜住：**丢内容**靠 snapshot + delta-clear（压缩期间新进的交互不被误删）与 fallback 摘要（压缩失败不等于交互白丢）；**编内容**靠 prompt 约束（「只提取明确提及、不要推测」）加 `source_interactions` 溯源——每条摘要都记着自己从哪些交互压缩而来，可疑的摘要可以回到原始交互核对。反思引擎是这个信任模型的第二层：压缩时没提取到的、提取错的，由反思在事后审计中补记或删除。

### 为什么记忆是 agent 私有的

书信共和国的结构决定了这一点：社会的公共知识在邮件里（持久、可检索、可流传），个体记忆是个体的「脑内状态」。如果记忆可以共享，就绕开了邮件这个唯一公共通道——agent 之间会开始用共享存储交换信息，「平等、开放、沉淀」三条全部漏风。记忆私有 + 邮件公共，是把「想告诉别人的知识必须写成信」变成结构性约束：一个 agent 想让同伴知道什么，只能发信。

## 方向

与愿景的差距主要有两处：

- **个体学习未闭环**——记忆的出口只有 context：摘要与事实拼进 prompt 影响下一轮行为，但行为本身（spec）从不因记忆而改变。「经验沉淀为知识，知识能改写行为」目前只完成了前半；从记忆到 spec 的回流（比如反思产出原型修订、经 staging 闸进入库）尚未建设。
- **无社会级公共记忆**——每个 agent 各自摘要、各自记事实，社会层面没有可检索的公共层。邮件提供了沉淀的原材料，但「把书信提炼成社会知识」的机制（存档、共享摘要、同行评议筛选后的公共记忆）还不存在。