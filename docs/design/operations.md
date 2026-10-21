# 统一操作与可组合行为：目标设计

状态：完整方案已于 2026-10-19 认可；EPIC A 的 P1–P4 与审计收尾已通过 #184–#191 合并 main。宿主操作双入口、回执、预算与写序已落地；外部审查的恢复根隔离修复独立交付。EPIC B P5–P8 尚未开工。实施与验收见 [operations-plan](operations-plan.md)，决策见 [ADR 0044](../decisions/0044-unified-operations.md)。合并由人类确认。

当前落地范围：宿主 Operation、单份 schema、成员上下文与 DSL/Tool 薄入口，见 [当前操作契约](../reference/operations-reference.md)。当前调用记录与恢复边界见 [invocation-recovery](invocation-recovery.md)。当前共享预算和并发边界见 [execution-scope](execution-scope.md)。复合依赖和动态工具视图仍是后续阶段目标，不能视为当前保证。

## 1. 目标与范围

统一“能力定义”，分离 DSL 与 LLM 的调用入口：同一操作只定义一次输入、输出、实现和效果，可以被 Behavior 直接调用，也可以按需作为 Tool 暴露给模型。复合操作能够引用其他复合操作。不同成员及同一成员的不同会话阶段，可以有不同工具视图。

本次不重写 tlosure、不更换 SQLite/PostOffice、不引入外部工作流引擎，不增加第二种流程语言，不改变邮件作为成员通信通道的选择。先让一个真实操作贯通两种入口，再逐域收敛；不能先拆掉可工作的程序或会话驱动。

“统一”指共享操作定义、授权和效果提交语义，不指把所有领域服务并入一个巨型 executor，也不要求纯语言内置函数逐个走异步日志。

## 2. 代码基线与问题证据

基线：`d35c3a546a5e3dacef331b5d39d3e1cc7efd4e9a`（2026-10-19，本地 HEAD 与 GitHub main 相同）。以下路径按符号引用，避免行号漂移；GitHub issues 应保留基线永久链接。

| 当前机制与位置 | 已核实事实 | 目标处理 |
|---|---|---|
| [Tool / ToolResult](../../src/tlon/tools/base.py)、[ToolExecutor](../../src/tlon/agents/tool_executor.py) | Python Tool 同时定义 schema、实现和 LLM 协议；参数校验是手写浅层子集；返回 ToolResult | 拆出 Operation 契约和实现；Tool 只做协议适配；删除手写参数校验 |
| [assemble_member_services](../../src/tlon/agents/assembly.py) | 满配 46 个工具，按装置/信任/服务装配；LLM 工具面来自注册列表 | 保留装置和信任约束，工具视图从操作集合投影，不再靠复制注册代码区分成员 |
| [CapabilitySpec](../../src/tlon/agents/capabilities/spec.py)、[词汇桥](../../src/tlon/agents/capabilities/bridge.py) | 一个 artifact 一个闭包；只见参数；纯度准入；不能依赖其他能力；只进入 DSL | artifact 成为复合 Operation；保留零隐式 env 和审批；开放显式可分析依赖及 Tool 投影 |
| [MemberSpec](../../src/tlon/agents/member_spec.py)、[vocabulary](../../src/tlon/agents/vocabulary.py) | when/actions、until、stage-if 使用不同注册表，复合词不能自然用于所有纯条件 | 统一纯函数准入；保留各位置明确的数据作用域 |
| [ProgramDriver](../../src/tlon/agents/program_driver.py)、[ProgramStore](../../src/tlon/agents/program_store.py) | 冻结规则/输入；按调用序号及名称/参数核对重放；state-put 与结果可同事务提交 | 保留并扩展定义快照、嵌套调用标识；不重做另一套执行数据库 |
| [LoopDriver](../../src/tlon/agents/loop_driver.py)、[ProgramEffects](../../src/tlon/operations/model.py)、[EffectExecutor](../../src/tlon/agents/executor.py) | Tool 邮件返回数据后由 LoopDriver 按工具名提交；DSL 邮件走 ProgramEffects；底层均已使用 PostOffice | 邮件领域逻辑和提交统一；两种驱动仍各自负责程序与会话生命周期 |
| [宿主查询注册](../../src/tlon/agents/vocabulary.py) | 外部查询直接注册为非 pure、read_only；驱动不改写 | read_only 仅声明规则条件准入；外部读取采样与重放独立于纯计算 |
| [StageServices.think](../../src/tlon/agents/stages/services.py) | 每次模型请求读取工具定义；尚无请求级工具快照与状态准入契约 | 在该边界生成并记录请求快照，执行时重新授权 |
| [instantiate](../../src/tlon/agents/prototypes/instantiate.py)、[MemberServices](../../src/tlon/agents/member_runtime.py) | 原型复制完整 Behavior；MemberServices 实际为成员服务装配容器 | 保留复制；装配对象改名 MemberServices，避免与能力 artifact 混淆 |

事实纠正：旧 [capabilities](capabilities.md) 中“能力不能 LLM/IO”的笼统描述与代码不符。`CAPABILITY_ACTION_BUILTINS` 已来自全部 `MEMBER_ACTION_NAMES`，包含 model-call/state-get/state-put。当前真正限制是只组合准入的既有动作、不新增宿主原语；目标方案沿用这一权限边界。

## 3. 概念与所有权

| 概念 | 唯一职责 | 不负责 |
|---|---|---|
| Operation | 可调用工作单元；输入/输出契约、实现、效果、依赖 | 成员身份、会话阶段、模型工具协议 |
| Behavior | 事件规则、流程、会话配方、业务阶段、工具视图选择 | 授予成员权限；复制宿主操作实现 |
| Prototype | 打包 Behavior、参数、依赖说明、可选 lessons 与谱系 | 运行实例、自动更新已出生成员、授予权限 |
| Agent | 身份、部署配置、授权、预算、邮箱/记忆及运行状态 | 操作定义的另一份副本 |
| Tool | 某次模型请求中 Operation 的协议投影 | 独立业务实现和另一份 schema |
| tlosure | 语言、求值、静态作用域校验、fuel/depth | Agent、Tool、操作目录与宿主权限 |

“Capability”保留为对外的能力概念及现有提案通道术语；技术契约收敛到 Operation，不能保留第二套 Capability 执行器。现有 `capability.tlon` 可继续作为复合操作文件名，文件名不是兼容层。`capability-propose` 的职责变成提交复合 Operation 提案。

## 4. 单一操作契约

每项定义包含：`name`、`description`、`input_schema`、`output_schema`、实现（host 或 tlosure 二选一）。宿主声明效果，复合操作从依赖推导效果与依赖闭包；无需作者重复维护一份依赖清单。需要缓存时保存推导产物而不是另设权威。

### 4.1 参数与数据边界

- 新操作统一接收一个命名参数对象；DSL 为 `(mail-send {:to [...] :content "..."})`，Tool arguments 为同一对象。复合 impl 是固定一个参数的 `(fn [input] ...)`。语言普通函数仍可多参数，不修改 `+`、`get` 等语言函数。
- schema 采用 JSON Schema 2020-12 的受支持子集；根输入为 object，嵌套对象明确 properties/required/additionalProperties，支持数组、标量、enum、空值及数值/长度边界。首版不接受远程 `$ref`、递归 schema、自定义代码 validator；未支持关键字在准入时拒绝，不能静默忽略。
- `jsonschema` 已在 uv.lock 中（4.26.0），实施时提升为直接依赖，用其成熟校验器做 schema 自检、输入与输出验证。Pydantic 已为直接依赖，用于内部 typed 结构；不再手写一套 JSON Schema 验证器，也不声称 Pydantic 能直接验证任意 JSON Schema。
- schema 只保存一份。Tool definition 只转换协议要求的字段；供应商不支持的约束仍由执行端验证，无法无歧义表达的结构不暴露，不能生成另一套更宽的业务契约。所有路径拒绝 bool 当整数、未知参数、错误嵌套类型；缺省值仅在统一调用边界应用一次，不能由两种入口分别补值。
- 操作边界使用 JSON 兼容值。DSL 入口只将 map 的 Keyword 键按原字面名称转换为字符串键；不改 kebab/snake 拼写；检测键碰撞（如 `:to` 与 `"to"`）、Symbol、Closure、Keyword 值、NaN/Infinity 和不可序列化值并拒绝。返回 DSL 时对象键转 Keyword，字符串值保持字符串。语言内部 EDN 类型及 `:a` 与 `"a"` 的区别仍然保留；若业务需要 EDN 代码，显式传文本，不偷渡 Form。
- tlosure 无静态类型系统，不能承诺加载时证明任意表达式的 schema 相容性。加载期查定义、作用域、依赖和效果；每次操作调用查实际输入/输出，examples 提供额外动态证据。

### 4.2 实现、依赖与效果

宿主操作通过现有领域端口执行，只能由宿主代码增加；复合操作由 tlosure 实现，可以调用宿主操作或其他复合操作。

效果为集合而非一个布尔值：`read`（读取非确定外部状态）与 `write`（修改外部状态）；空集为纯计算。一个既读又写的复合操作保留两项。模型请求会产生不可撤销的消费，当前按 write 调度并保存非幂等 intent，同时计 token/费用预算；“不写业务状态”不意味着免费、确定或无需记录。时间、随机数、目录查询同样不得藏在纯计算里。

首版操作间调用必须静态具名。允许语言局部 fn/defn 和受 fuel 约束的递归，但不允许把注册操作值作为参数传递或通过字符串动态寻址来逃避依赖分析；分析必须覆盖局部闭包 body 和所有分支，遇到不能确定目标的操作调用拒载。纯语境不能通过局部闭包隐藏 read/write。

加载器分析作用域、收集直接操作依赖、拓扑装配并推导传递效果；缺失、同名、操作依赖环均拒载，并显示依赖链。坏操作连同依赖它的操作/Behavior 拒载，其他成员和无关操作可用。审批时展示依赖与效果闭包，并持久保存传递依赖的内容指纹；加载发现依赖内容不再匹配批准记录时拒载受影响上层，直到人类重新批准，不能自动绑定新定义。运行快照不能替代批准绑定；不增加语义版本解析器。

静态分析要复用 tlosure 的 AST/作用域规则，不用正则扫词。tlosure 保持宿主无关，必要扩展以通用符号解析信息提供，不 import tlon。

## 5. 两个入口与一个调用边界

拟新增 `tlon.operations` 承载共享契约、注册表、调用执行和 DSL/Tool 适配，不依赖 agents/server；具体领域服务经 Protocol/Callable 注入。agents 是组装层并驱动会话，organization/sandbox/memory 保留领域实现。依赖方向通过现有 import-linter 固化，不把整个 tools 目录机械搬家。

每次调用先按稳定 ID、冻结定义指纹与规范化参数查询已提交记录：命中则核对一致性并还原原结果/消费，不重新授权、不重新预留预算、不重新执行。仅无已提交记录的新调用经过：定义解析 → 参数规范化与校验 → 当前身份/权限/状态检查 → 预算预留 → 执行 → 输出校验 → 记录结果。未解决的 intent 按领域对账规则处理，不能当成全新调用。领域原子写入与幂等规则仍在相应服务内；执行器不能制造跨域事务。

调用上下文由宿主构造，包含 member_id、run/session/model-request/call 标识、定义快照、父调用、预算/截止时间和取消信号。上下文不进入用户参数，LLM 与 artifact 无法伪造 member_id、授权或 journal ID。复合 impl 仍只见 input；内部调用继承原调用者身份，不以 artifact 作者身份执行。

内部区分参数错误、禁止、预算耗尽、领域拒绝、结果未知、输出契约错误。DSL 得到成功值或结构化执行错误；Tool 适配成现有 ToolResult/模型协议。领域“拒收”等已知结果可作为 schema 内业务数据，不能把网络超时当作确定拒绝，也不能把未提交请求当成功。首版不添加新的 DSL 异常处理语法。

副作用已经发生但输出校验失败时保留原始回执与契约错误，禁止自动重做。只有执行端明确声明可幂等重试的调用才可自动重试；复合操作没有默认事务、回滚或补偿保证。

### 5.1 持久化与恢复

保留 ProgramStore 与 session journal 的不同职责；统一调用记录字段，不再建立第三份全量交互日志。ProgramStore 管程序和叶子操作提交结果；session journal 管模型请求/工具视图/交互事实，并引用调用标识，不用“有没有 tool/result”猜副作用是否已提交。

启动运行时冻结 Behavior、传递依赖中的复合源码/schema/效果及宿主实现身份；宿主实现身份由运行包代码内容、锁定依赖及操作契约的构建指纹标识，不只 hash schema，也不 hash 文档或可由作者任意填写的版本号。首版可保守按整个 runtime 构建标识失配拒绝恢复，无需多版本宿主加载。快照是本次运行的执行输入，不是旧 API 兼容层。恢复不得从当前能力目录重新拼接旧程序。宿主实现身份不符时停止恢复并明确报错，不暗中跑新实现，不维护多版本宿主实现。

叶子 read/write 操作以 `run_id + 根调用标识 + 嵌套确定路径` 定位；并发完成顺序不能决定编号。复合边界可有 span/摘要，但叶子效果结果才是重放权威；已有记录必须核对定义指纹、参数和调用路径。普通纯计算重新计算，外部读取/模型结果从记录恢复。重放已提交历史不重新执行；未提交的新效果一律检查当前授权和撤销状态。

PostOffice 的持久提交与 request_id 去重保留；state-put 与记录同事务保留。没有幂等协议的 shell/外部调用在“执行后、记录前”崩溃只能标为 unknown 并停止自动重试，不能宣称 exactly-once。非幂等写入开始前持久记录 intent，恢复见 intent 无回执时按领域对账，无法对账则 unknown。

宿主 token 预算与截止时间属于对应执行根，内部模型请求不重置；并发子调用预留/扣减必须原子化。run-loop 是显式会话预算根，保留 recipe 的 total 额度，程序直接请求保留 output 额度。fuel 仍由语言 Evaluator 管理：显式 rule.fuel 在门控和全部动作间共享，未声明时每个动作使用既有默认额度；复合操作跨调用的 fuel 协作在 P5 验收，不将其称为 P4b 已完成。恢复按历史账本重建消费，不二次收费，也不因重启获得新预算。取消会阻止后续调用，无法保证撤销已经提交的效果；结果未知必须保持未知。

### 5.2 并发和调度

保留成员串行规则 worker、session 既有等待/收集机制；共享调用层不再自行启动另一套调度器。各根调用有稳定 ID 和父子路径。

无副作用的独立读取可并发；同成员写操作按模型声明的调用顺序串行执行，复合写操作作为同一串行域；子调用重入当前执行上下文，不再抢自己持有的锁。原有并发读写行为的收紧在 P4 明确验收，不能偷偷变化。

finish-turn/sleep-until/exit-agent 仍是独占的调度请求，等本轮排空后由 scheduler 提交；不得与普通操作混批，不得封装进复合操作后隐藏结束/睡眠/死亡。run-loop 是 Behavior 的会话控制边界，允许 Behavior 调用但不开放给复合 Operation 或 LLM，防止持有成员写执行域时再开启会话造成重入/死锁。这是控制流语义，不是按业务名称写在 LoopDriver 中的例外。

当前宿主例外：终止/失败回信由内部 EffectExecutor 委托 MemberMailService，收件人限会话责任集合中的人类，内容由宿主生成，使用固定 request_id 去重。它不经过业务 Operation 的 invocation/write gate，不消耗模型 token；这是通知通道，不是授予模型第二个任意发信入口。P7 实施授权时保留宿主通知在只读业务阶段可达，普通邮件继续受授权闸约束；取消/截止仍检查。验收见实施计划。

## 6. 权限、工具视图与状态

### 6.1 三种不同事实

1. **定义存在**：目录中的宿主/受信复合操作。
2. **执行获准**：宿主维护的成员授权，及装置/信任/资源/当前状态约束。
3. **本次可见**：Behavior 为本次模型请求选择的工具视图。

执行授权放在宿主管理的成员配置，不能通过 Behavior、人格或 artifact 改写。初始实现用明确的 operation allowlist 加已有装置/信任约束；资源级限制沿用路径守卫、邮箱归属、生命周期配额等领域检查，不发明通用 ABAC 语言。涉及邮件/私有状态等现有成员基础操作，随装配明确生成初始授权，而非缺配置默认允许所有。

授权以最终宿主操作为执行底线，复合依赖不会产生新权限；成员可以禁止某个复合入口，但批准该入口不自动授予其依赖权限。工具视图投影先过滤不可执行的依赖闭包，运行时仍逐叶检查实际资源。复合依赖不需要全部出现在 LLM 工具列表中。

工具视图 = Behavior 本阶段候选操作 ∩ 当前成员可执行操作 ∩ 本入口可暴露操作。默认空列表；未知名称在加载时拒绝；与权限求交而被过滤的项给出可观测原因，避免把成员级差异误报成无效 Behavior。视图只是发现与选择面，不是授权。

### 6.2 动态工具视图

Behavior 的 SessionRecipe 增加一个 `:tools` 纯表达式，返回操作名向量。它从显式的上下文数据选择，不新建状态机 DSL。每次 StageServices.think 发模型请求前求值，按名称稳定排序，冻结名称/schema/定义指纹/上下文版本和 request_id 并写入 journal；排序不采用随机目录扫描顺序。

LLM 返回的每个调用必须属于该请求冻结视图，不能因为工具刚被新增就接受伪造名称。执行前再查当前授权、装置、业务状态及撤销，权限已收紧即拒绝；下一次模型请求才使用新的视图。重放模型请求沿用已记录视图与响应，不重新问模型或按现在时间重算过去的选择。

时间先作为记录过的外部读取或宿主事件传入视图上下文，纯选择表达式不直接读时钟。同一成员的 business phase（如 reading/drafting）与 scheduler 的 active/waiting/sleeping/dead 不混用。业务 phase 可通过既有私有结构化状态保存，用于选择视图，本身不构成授权；模型不能伪造 runtime context。

仅改变视图不会禁止 DSL 绕路执行。需要强制“当前不可发送”时，管理员为成员声明宿主执行约束；首版提供既有成员调度/信任约束与明确的只读/允许写阶段约束，默认拒绝未知受控阶段。受控授权阶段与撤权元数据由宿主单独维护，不允许通用 state-put、Behavior 或模型修改；受信事件/管理动作触发宿主转换，在只读阶段也能执行这一宿主控制转换，不依赖普通业务 write。业务 phase 不得代替该授权事实。Behavior 可选择更窄的视图，但不能移除宿主约束。每个叶子写入在提交前检查；检查与状态变更须在同成员串行域内完成，已经开始的外部调用不声称可撤回。

### 6.3 需要人工批准的业务

本 EPIC 不新建通用审批平台。示例中的发送批准，使用持久草稿/记录和宿主验证过的人类动作；批准绑定 member_id、内容哈希、收件人及用途，修改正文/收件人即失效。不能依据 LLM 在 state-put 写的 approved=true 授权。

首版动态视图验收采用“管理员撤销发送权限”和“宿主只读阶段”即可完成，不以新的草稿审批系统为依赖。将来需要批准流程时按以上对象绑定约束单独设计。

## 7. 端到端例子

以下是目标调用示意，不是当前可运行格式；schema 完整字段由实施阶段 reference 定稿。

宿主提供 `mail-read-thread`、`model-complete`、`mail-send`。复合操作定义：

```clojure
; summarize-thread 的 impl，input/output schema 在同一 artifact 定义
(fn [input]
  (let [thread (mail-read-thread {:thread-id (get input :thread-id)})
        response (model-complete {:prompt (str "请摘要：" (get thread :content))})]
    {:summary (get response :content)}))

; 再组合已有复合操作，不展开/复制 summarize-thread
(fn [input]
  (let [summary (summarize-thread {:thread-id (get input :thread-id)})]
    (mail-send {:to (get input :to)
                :content (get summary :summary)})))
```

第二个操作命名 `send-thread-summary`，其效果为 read+write；上下游输入/输出在实际调用时校验，模型费用计入原成员预算。

1. Prototype 打包 Behavior、默认参数和上述操作依赖说明，创建独立 Agent A/B；不复制操作源码进入每份 Behavior。
2. A 只有读邮件/模型权限，B 还有发送权限；A 能使用 summarize-thread，不能执行 send-thread-summary。
3. Behavior 可直接调用 summarize-thread；同一操作也能投影成工具供 LLM 调用，无需再写 Python SummaryTool。
4. B 在 reading 阶段视图为 mail-read-thread/summarize-thread；sending 阶段可增加 send-thread-summary；内部 model-complete 不必暴露。
5. B 的模型响应生成后管理员撤销发送权限，执行时 mail-send 仍被拒绝；如果摘要步骤已经完成，其消费和结果保留，不伪造整体原子回滚。
6. 发送成功后、工具结果入会话前崩溃，恢复用稳定调用 ID 与 PostOffice 回执对账；不得再次投递。宿主无去重能力的调用保持 unknown。

所有纯条件位置另用纯操作 `has-actionable-facts`：同一函数可用于规则 when、会话 until、stage if 和 tools 选择表达式，只见调用点显式传入的数据。

## 8. 保留、删除与统一

| 机制 | 决策 | 删除/替换时点 |
|---|---|---|
| tlosure、闭包、fuel/depth、核心算术/集合函数 | 保留 | 本 EPIC 不改 map/filter/reduce 语法，不机械改纯函数 API |
| PostOffice、SQLite mailbox、领域归属检查、request_id | 保留 | 邮件宿主操作复用，不另造发送存储 |
| ProgramStore、session journal、StageOutcome、四态 scheduler | 保留并扩展契约 | 不合并程序/会话生命周期，不新增第三日志权威 |
| 原型复制、参数渲染、谱系、人格/模型分离 | 保留 | 原型仅增加依赖校验；不变为动态父类继承 |
| staging/人工批准、examples、作者/评审记录 | 保留并扩展 | examples 用新统一契约；效果测试只能显式隔离，不执行真实写入 |
| Tool 内 schema+业务实现+协议的绑定 | 拆分 | P1/P2 建立契约，P3 完成各域切换后删旧执行职责 |
| Capability 独立注册/执行、位置参数 :args 与 pure 作者声明 | 统一/删除旧格式 | P5 以共享 Operation artifact 替换，直接拒绝旧格式，无兼容解析 |
| Tool.validate_arguments 手写浅校验 | 删除 | P3 全域切换后删除；P1 起新契约使用成熟校验器 |
| LoopDriver 按 sendEmail/forwardMail 名称提交、重复邮件参数规则 | 删除/统一 | P2 邮件垂直切换，同 PR 删除旧路径；统一邮件操作涵盖转发、回复、endorsement、mentor cc |
| ProgramEffects/ToolExecutor 中重复业务分发与结果转换 | 删除/收敛 | P4 只留入口适配/驱动职责；领域执行进入共享操作实现 |
| 纯条件的多张词汇白名单 | 统一 | P6 统一纯操作准入，保留不同数据作用域；读取状态先物化，不注入隐藏 env |
| 全量工具注册列表就是模型工具列表 | 删除 | P7 使用请求级动态视图；注册表不再可变 unregister 来模拟阶段 |
| 旧 MemberCapabilities 装配容器名称 | 改名 MemberServices | P4b 一次改调用点，无旧别名 |
| 旧名称/旧 schema/旧存储的自动兼容 | 不增加 | 每阶段原子切换所属操作及调用者，旧入口直接删除；已有不兼容运行明确拒绝，禁止静默清库 |

行为不变先行不是同时维护两份执行器：P2-P3 可以按不同操作域逐 PR 切换，某个操作任何时刻只有一份权威实现。旧域在其切换前继续运行，不能回退调用新域旧路径。schema/存储不兼容时发布说明明确新格式与受影响数据；仅改仓库内受管理的示例/测试，不写用户数据迁移程序、不自动删除用户记录。

## 9. 参考模式与取舍

- [MCP tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)：借鉴带输入/输出 schema 的工具描述与发现面；不引入 MCP 服务或假设 Tlon 的阶段视图等同 MCP 列表协议。
- [Temporal workflow definition](https://docs.temporal.io/workflow-definition)：借鉴确定性程序与外部调用结果记录的分离；沿用 Tlon SQLite 实现，不引入 Temporal。重放机制不等于外部调用 exactly-once。
- [jsonschema 校验](https://python-jsonschema.readthedocs.io/en/stable/validate/)：复用已锁定成熟依赖的 schema/数据校验，不手写验证器。统一的是操作边界契约，不给 tlosure 加静态类型系统。

代价：所有宿主操作需要明确输出契约；动态工具暴露增加请求快照；依赖与恢复需要定义快照和效果分析。这些直接支撑组合与恢复，不增加远程插件、版本协商、通用审批或分布式调度等未要求能力。

## 10. 完成标准

不能以“有 Operation 类”或“工具数没有变化”为完成。必须由同一个复合操作同时跑通 DSL 与 LLM 入口，复合调用复合、纯函数跨条件复用、不同成员/阶段的工具视图、运行时撤权、嵌套预算及崩溃恢复；旧路径确实删除，完整质量门及真实邮件冒烟通过。逐阶段证据见 [实施计划](operations-plan.md)。
