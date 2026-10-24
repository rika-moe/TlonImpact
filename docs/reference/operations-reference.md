# Operation 调用契约（P1–P6）

当前状态：EPIC A #184–#192、P5 #193 与 P6 #194 已合并。两入口共用调用记录、预算、写序及恢复边界，Capability 与复合操作已删除；P5 工作流程退役旧规则与 stage 条件。P7 暂停，tools 尽量保持固定；本次 P8a 只收口独立文档与旧机制审计。当前恢复契约见 [invocation-recovery](../design/invocation-recovery.md)，设计决策见 [operations](../design/operations.md)，实施状态见 [operations-plan](../design/operations-plan.md)。

## 定义和执行

`tlon.operations.core.Operation` 接收 name、description、input_schema、output_schema、handler、effects、retry_safe、default_timeout。schema 在准入时自检并保存为不可变 JSON 字符串；公开属性每次返回独立副本。名称满足 `[A-Za-z_][A-Za-z0-9_-]*`，最长 64 字符，不得使用 nil/true/false 或覆盖语言特殊形式、通用内置函数。

空 effects 表示纯计算：handler 是同步 `(CallContext, dict[str, Any]) -> Any`。非空 effects 是 read/write 集合：handler 必须是 async，并由异步执行边界等待结果。纯 handler 返回 awaitable 会被拒绝，不能把异步 IO 谎报为纯计算。注册表固定定义集合，同名拒绝；宿主给不同成员构造 scoped handler 和 `CallContext(member_id, run_id, call_id)`，上下文不在业务参数或 DSL env 内。

`OperationExecutor.call_pure` 仅执行纯操作；`call` 支持两类操作。纯操作直接执行；宿主效果操作依次执行输入严格 JSON 复制与 schema 校验、稳定身份与历史核对、持久化 intent、handler、输出校验及回执提交。已有回执在进入当前 handler 前还原；参数错误不写 intent。修改 handler 参数、返回值或 Tool schema 投影都不能污染原调用数据或权威 schema。

尚不提供通用授权名单或动态工具视图；共享执行作用域已覆盖现有模型调用入口。真实 runtime 为两入口注入同一成员 ProgramStore；SessionJournal 提供历史及程序交互只读投影，PostOffice 保留邮件领域幂等回执，不另建第三权威日志。member-sleeping、member-running、member-lazy 与 member-roster 均为普通只读 Operation，通过同一 invocation 回执记录采样值，旧宿主 builtin 及独立 effects 路径已删除。受限宿主失败通知仍属于宿主故障处理出口，详见 [退役代码清理](../design/workflow-retirement-cleanup.md)。上下文隔离不是完整授权系统。输出不合约报 `OutputError`，不得据此假定效果未发生或自动重试。

## 调用记录与指纹 P4a

宿主上下文包含 member_id、run_id、call_id、call_path、origin、冻结输入及 Invocation；业务参数不能覆盖它们。输入以键排序的 canonical JSON 固定身份，输出回执保留对象键顺序，因为 DSL 的 keys/str 能观察该顺序。恢复同时核对路径、参数、操作契约指纹和宿主构建指纹；构建身份包含宿主代码、锁文件摘要、已安装依赖身份与宿主 builtin 定义。当前数据库格式为 user_version=2；旧数据库或无构建身份的会话拒绝恢复，不迁移或清库。

每个调用的成功、明确拒绝、输出契约错误以及未知结果均有明确记录。输出不合约时保留原 JSON 回执；非 JSON 对象只保存类型与 repr 诊断，不能声称恢复任意 Python 对象。记录失败不返回成功；领域已原子提交回执时，对账返回该真实结果。同一进程内相同调用合流，独立根调用不共享完成序号。完整边界见 [调用恢复](../design/invocation-recovery.md)。

## Schema 子集

采用 jsonschema 的 Draft202012Validator。输入根为闭合 object（additionalProperties=false）；所有 object 显式声明 properties/required/additionalProperties，additionalProperties 仅接受 boolean；array 必须声明 items。除 JSON Schema 原生 boolean schema（true 接受任意严格 JSON、false 拒绝所有值）外，每个 schema 节点必须声明 type，支持 object、array、string、number、integer、boolean、null，也支持 type 数组表达 nullable。

准入关键字仅支持：type、properties、required、additionalProperties、items、enum、minimum、maximum、exclusiveMinimum、exclusiveMaximum、multipleOf、minLength、maxLength、minItems、maxItems、uniqueItems、description、title、$schema。$schema 若提供必须为 `https://json-schema.org/draft/2020-12/schema`。不支持 default（不隐式补值）、$ref（本地和远程均拒绝）、format、组合 schema 或任意扩展关键字；未知关键字拒载。嵌套校验、bool/number 区分由成熟校验器执行。

## 入口与错误

DSL：`DSLAdapter.builtins()` 提供实际 purity 的 Builtin；调用形式为 `(operation-name {:parameter value})`，严格一个对象参数。纯操作直接由 Evaluator 调用；read/write 经 `eval_async(..., adapter.handle)` 悬挂并恢复。同步 Evaluator 不得执行效果操作。普通语言函数 API 不变。

Tool：`OperationTool(executor, name)` 从同一契约投影协议定义，可注册到既有 ToolExecutor。它只提供协议定义与 execute_context，校验一次性留给共享调用边界；不另写校验规则。业务参数 `self` 也可使用。协议投影修改不改变操作定义。

JSON 边界只接受 None、精确 bool/int/有限 float/str/list/dict；最大嵌套 100，循环数据拒绝。DSL 额外允许 Keyword map key，按原拼写转换为 string；返回对象键中能由现有 EDN parser 完整往返的键转为 Keyword；邮箱地址、空串、空格、Unicode 等不能表示为关键字的键保留字符串，字符串值不变。Keyword 值、Symbol、Closure、Form、tuple、NaN/Infinity 均拒绝。解析 EDN map 与 assoc/conj 构造 map 时，Keyword/string/Symbol 等相等但类型不同的键在写入 dict 前拒绝，避免静默覆盖；同型重复键沿既有语义保留末值。已经被外部 Python dict 覆盖的信息无法恢复，不宣称能够检测。

输入错误为 InputError，输出错误为 OutputError，均继承 OperationError/EvalError，DSL 保留共享边界的结构化异常类型；Tool 转为失败 ToolResult。领域自定义 OperationError 子类在持久边界统一为 OperationError 并保留 data，首次调用与恢复的协议形状相同。领域的明确拒收使用 RejectedError，结果未知使用 UnknownOutcomeError；二者继承 OperationError 并携带 data，unknown 保留 request_id。Tool 入口保留这些结构化信息。该结构化错误同样用于文件和 Shell；不宣称已有通用重试机制。

Tool 协议根参数必须是 JSON object 的字符串键。OperationTool 在 ToolExecutor 中直接接收参数对象，根级非字符串键和嵌套非 JSON 值均由共享边界返回 InputError。旧 Tool 基类与关键字参数派发入口已删除。

## 邮件 P2

五个操作 `mail-send`、`mail-forward`、`mail-check-inbox`、`mail-search`、`mail-mark-read` 共用 [权威定义](../../src/tlon/operations/mail.py)，输入与输出均为 snake_case；DSL 也使用 `:mail_id`、`:request_id` 等原拼写键。schema 与参数表见 [工具参考](tools-reference.md#mail)。所有服务绑定成员身份，不接收可伪造的 owner/from 参数。

程序使用冻结 run 和调用位置，工具使用 session、轮次与 tool call ID 生成稳定 call_id。默认 request_id 来自该 ID，显式重试可以提交原 request_id。输入或输出错误不代表效果一定未发生；超时、中断和无法确认提交必须保留 unknown 及原 request_id。复用邮局回执不会重复入箱；会话恢复会先查统一调用记录：未写 intent 的 dispatch 可以新执行，已提交回执直接还原；只有不安全 intent 时报告 unknown，安全写同键对账。

已删除旧五邮件 Tool 及旧 DSL 邮件词，不提供别名或迁移。旧 Behavior 和未完成运行若引用旧名，将不兼容；仓库示例同步切换，用户数据不自动修改。P7 动态工具视图暂停，根调用记录与共享额度已由 P4a/P4b 收敛。

## 文件、Shell 与时间 P3a

[file Operation](../../src/tlon/operations/filesystem.py)、[Shell Operation](../../src/tlon/operations/shell.py) 和[时间 Operation](../../src/tlon/operations/time.py) 各自保有唯一 schema，参数与结果由两入口共同使用。名称及完整字段见[工具参考](tools-reference.md#filesystem)。旧 Tool 类和旧名字已删除，无别名、迁移或 fallback。

`file-read`、`file-list`、`file-search`、`shell-check`、`time-now` 属于 read。`file-write`、`file-edit`、`shell-run` 属于 write。时间为外部读取，不能进入纯条件。本域中 L0/L1/L2 均装配时间及五个文件操作，自己的 workspace 无需装置；L2 再有 Shell；workspace 外访问仍受装置权限约束；其他非装置域保持各自既有约束。领域服务执行时重新检查等级及所需装置，工具可见性不是唯一防线。

文件服务只使用成员的 ExecutionDevice，沿用容器工作目录、读写路径白名单与 archive-only 文件柜。读取额外允许自身只读 `/tlon-home`；写入仅可到配置的 allowed_paths。没有通用宿主文件系统后门。归档内容仅在内存读取，成员路径和归档条目仍需独立校验。

`default_timeout` 是宿主操作默认执行秒数，覆盖成员的 tool.per_timeout；schema 声明的显式 timeout 参数再优先。`timeout_grace` 是操作声明的清理宽限（默认 0，Shell 为 10 秒），与继承根剩余期限取小。两入口均由 OperationExecutor 实施，LoopDriver 不再维护第二套计时。局部期限向内部模型传播，但不改写预算根冻结期限。

`retry_safe` 是宿主对中断后重新调用是否安全的最小声明。程序沿用同一 ProgramStore 数据库：非幂等 write 先写 intent，再调用 handler，再记录结果；已有结果重放，只有 intent 而无结果时返回 unknown，禁止自动重新执行。取消同样不能当作无副作用。邮件仍使用 PostOffice 的稳定 request_id 幂等重试。P4a 将这些 intent 和回执统一到 ProgramStore 的 invocations；P4b 新增共享额度根，并让新写入按成员 gate 及模型声明顺序执行。

会话保留 session journal 的 dispatch/result/collect，操作提交事实只查 ProgramStore。孤立 dispatch 不等于已执行；尚无 intent 可以开始执行，非幂等写只有 intent 而无回执时才报告 unknown；工具适配器不伪造回执。Shell 后台任务只由成员 ShellService 持有，查询不复制任务表；成员关闭先取消并等待后台任务清理，再关闭装置。后台句柄不跨进程恢复，不自动重启命令。

## 记忆、任务、人格/联系人、观测与状态 P3b

各域定义见[工具参考](tools-reference.md#memory)。记忆与会话读取绑定成员 MemoryManager/SessionJournal，任务绑定自己的 TaskManager，人格与联系人只接触自身文件；identity/soul 可以修改，RULES 不可自改。观测为 read，保留原有公开声誉/trace 与自身邮箱统计的边界。旧 Tool 类、名称和任务 manager 的旧包路径直接删除。

state-get/state-put 使用一个对象参数，例如 `(state-get {:key "edition"})`、`(state-put {:key "edition" :value {:period "001"}})`，直接返回严格 JSON 值，缺失返回 null。输入根始终是闭合对象，value 和返回值用原生 boolean schema 表达动态 JSON；DSL 键不做 snake/kebab 隐式转换。

state-put 将状态与该次调用结果放在 ProgramStore 的同一事务；两入口都使用宿主创建的 Invocation，不再分别记录旧 effects 或状态专用调用元数据。同调用相同参数重试返回原值，不覆盖后来的状态；同调用不同参数拒绝。普通领域写入仍按非幂等 intent 保护，结果不确定保留 unknown 和 call_id，不能把失败当作未产生副作用。

任务落盘通过临时文件、fsync 和原子 replace；写失败不再伪报成功。人格文件写成功而账本追加失败时，保留成功回执并显式告警。保持原有 TaskManager 单实例同步调用模型；未扩展为跨进程任务写入协调器，也未修改其原有损坏文件读取策略。

## 生命周期、演化与模型 P3c

[lifecycle](../../src/tlon/operations/lifecycle.py)、[evolution](../../src/tlon/operations/evolution.py)、[model](../../src/tlon/operations/model.py) 共用成员注册表。旧 camelCase 名称删除；模型调用为 `(model-call {:messages [{:role "user" :content "任务"}] :tools [] :max_tokens 256})`，输出使用 snake_case，session recipe 的 `:max-tokens` 配置保持原有含义。

Operation 没有 control 分类或 sole/direct/literal 嵌套限制。`finish-turn` 返回普通局部信号，调度由根入口结果及宿主生命周期决定。

程序直接模型调用使用持久 output 根，额度来自成员 program_token_budget（默认 100000）；llm.max_tokens 仅为单次输出上限，model-call/model-context 未指定 max_tokens 时使用该成员配置；获准的输出预留成功后不退款，实际输出上限还受请求值和剩余额度限制。旧 run-loop/recipe total 根入口已删除。既有 total 预算账本语义仍供相关宿主调用使用；普通程序及其压缩、提案、评审共享冻结根。total 成功按可靠的实际 usage 结算；缺少 usage 或提交未知时保留预留，实际超额如实记账并阻止后续请求，token 估算不是硬上界。

模型响应与额度结算原子提交，已提交历史只还原，不重新扣额或请求 provider；未知提交不自动重发。后台反思和蒸馏另建维护根。宿主 ContextVar 传播作用域，业务参数不能重置额度；取消只作用于当前进程任务树，冻结 deadline 和预算跨重启保留。详情见 [执行作用域](../design/execution-scope.md)。

## EPIC A 审计收尾边界

spawn 的配额归属取冻结 CallContext.run_id：Tool 与 DSL 使用所属冻结程序根，日志 ContextVar 不参与授权。演化和记忆的领域包装透传确定的预算/截止拒绝，provider 明确拒绝为 rejected，未知提交仍为 unknown。成员同一 write gate 显式注入 OperationExecutor 与 MemoryManager，反思/蒸馏与普通调用共享写边界。超时先返回已提交回执；未派发的新调用明确拒绝，不把旧未知 intent 覆写为失败。

## 纯计算与调用

模块初始化及社会策略只接受纯计算；普通工作程序通过显式调用采样外部数据，再用普通条件和函数组合。旧 when/until/stage-if 语境与条件求值接口已删除。

## 可编程工作流程 P2：模型请求与恢复

`model-call` 必填 `messages`，可选正整数 `max_tokens`（默认使用冻结成员的单次输出上限）；`tools` 是工具名列表（默认空），可选 `cache_prefix_len`。输入消息允许 user/assistant/tool，system 由宿主绑定；prompt 与任意工具 schema 拒绝。返回 content（可为 null）、tool_calls（id/name/arguments）、finish_reason、usage、reasoning_content，不执行工具建议。工具执行由 tool-execute 显式完成。

普通程序通过 ModelService 使用统一模型调用账本。v2 受理原子冻结 host（系统输入及工具定义）与 config（有效成员模型配置）；已有结果重放不受后来系统词或模型配置变化影响，新请求仍校验当前工具目录。已提交模型结果缺外层回执时，恢复 hook 核对稳定路径和有效请求，只读补回执；unknown 不自动重发。output 模式维持获准输出上限预留，total 模式维持现有实际用量结算与缺失用量的保守处理。

输入历史中的 assistant `tool_calls` 使用 provider 消息结构：`{:id "..." :type "function" :function {:name "..." :arguments "JSON字符串"}}`，tool 消息用 `tool_call_id` 关联。它与输出的规范化 `{id,name,arguments}` 不同，P2 不自动把建议执行成消息历史；工具执行和完整协议拼接在 P3 提供。

## 普通程序工具编排（P3）

`tool-execute` 不是叶子 Operation，不进入模型 Tool 目录，也不记录批量父 intent。它接受受控工具名称列表和规范化建议，按程序调用位置、输入索引派生稳定叶子身份，逐项使用 OperationExecutor。完整批次参数参加身份校验，完成顺序不决定身份。已完成项先返回旧回执，新效果才检查当前工具目录；撤权和定义变化阻塞。

OperationTool 与 ToolExecutor 只将 InputError/RejectedError 变为普通失败，其他错误向运行器传播。RejectedError 子类的持久分类统一为 RejectedError，保留领域数据。提交故障的 unknown 使用真实业务参数生成诊断，显式 request_id 不因批次身份元数据而丢失。

## 模块创作和窗口机制（P4a）

prototype-write 必填完整 v2 behavior/modules，发布后返回 pending_approval。program-check 是只读静态校验，返回 valid/digest/diagnostics，不执行候选；原 behavior-propose 操作已删除，起草改由普通 tlon.authoring/draft 使用文件工具、模型与校验反馈组合。prototype-review 读取完整包，谱系 diff 覆盖每个作者文件，实际批准仍由人类执行。

model-context 接受与 model-call 相同的 messages/tools/max_tokens 受控输入，返回 input_tokens/context_window/output_tokens/fits。它是持久只读估算，不请求 provider 或消费模型额度，使用冻结系统/工具/模型配置；token 估算不保证 provider 实际计数。普通上下文库据此组织压缩，模型请求和额度仍由原回执负责。

P4b 的 session-read 接受旧会话 ID 或 program:<run_id>。SessionJournal 只读联合旧历史与 ProgramStore 权威快照，返回的摘要带可追踪来源、模型/工具结果和程序返回值；查询不调用恢复入口。客户端连接 REST /sessions 的契约不变。
