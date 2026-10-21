# Operation 调用契约（P1–P4b）

当前状态：P1 至 P3c 已通过 #184–#188 合入 main。P4a 已通过 #189 合并，将 DSL 与模型 Tool 的调用记录、指纹及恢复收敛至同一边界。P4b 实现共享账本、取消/截止时间、成员写排序及 MemberServices 改名，契约见 [execution-scope](../design/execution-scope.md)。Capability artifact 尚未转换为复合 Operation。当前恢复契约见 [invocation-recovery](../design/invocation-recovery.md)，目标设计见 [operations](../design/operations.md)，实施顺序见 [operations-plan](../design/operations-plan.md)。

## 定义和执行

`tlon.operations.core.Operation` 接收 name、description、input_schema、output_schema、handler、effects、retry_safe、default_timeout、control。schema 在准入时自检并保存为不可变 JSON 字符串；公开属性每次返回独立副本。名称满足 `[A-Za-z_][A-Za-z0-9_-]*`，最长 64 字符，不得使用 nil/true/false 或覆盖语言特殊形式、通用内置函数。

空 effects 表示纯计算：handler 是同步 `(CallContext, dict[str, Any]) -> Any`。非空 effects 是 read/write 集合：handler 必须是 async，并由异步执行边界等待结果。纯 handler 返回 awaitable 会被拒绝，不能把异步 IO 谎报为纯计算。注册表固定定义集合，同名拒绝；宿主给不同成员构造 scoped handler 和 `CallContext(member_id, run_id, call_id)`，上下文不在业务参数或 DSL env 内。

`OperationExecutor.call_pure` 仅执行纯操作；`call` 支持两类操作。纯操作直接执行；效果操作依次执行输入严格 JSON 复制与 schema 校验、稳定身份与历史核对、持久化 intent、handler、输出校验及回执提交。已有回执在进入当前 handler 前还原；参数错误不写 intent。修改 handler 参数、返回值或 Tool schema 投影都不能污染原调用数据或权威 schema。

当前不提供通用授权名单、动态工具视图或复合依赖；共享执行作用域已覆盖现有模型调用入口。真实 runtime 为两入口注入同一成员 ProgramStore；session journal 保留会话协议历史，PostOffice 保留邮件领域幂等回执，不另建第三权威日志。上下文隔离不是完整授权系统。输出不合约报 `OutputError`，不得据此假定效果未发生或自动重试。

## 调用记录与指纹 P4a

宿主上下文包含 member_id、run_id、call_id、call_path、origin、冻结输入及 Invocation；业务参数不能覆盖它们。输入以键排序的 canonical JSON 固定身份，输出回执保留对象键顺序，因为 DSL 的 keys/str 能观察该顺序。恢复同时核对路径、参数、操作契约指纹和宿主构建指纹；构建身份包含宿主代码、锁文件摘要、已安装依赖身份与已加载 Capability 闭包。当前数据库格式为 user_version=2；旧数据库或无构建身份的会话拒绝恢复，不迁移或清库。

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

已删除旧五邮件 Tool 及旧 DSL 邮件词，不提供别名或迁移。旧 Behavior、Capability 和未完成运行若引用旧名，将不兼容；仓库示例同步切换，用户数据不自动修改。DAG 和动态工具视图仍属于后续阶段；根调用记录与共享额度已由 P4a/P4b 收敛。

## 文件、Shell 与时间 P3a

[file Operation](../../src/tlon/operations/filesystem.py)、[Shell Operation](../../src/tlon/operations/shell.py) 和[时间 Operation](../../src/tlon/operations/time.py) 各自保有唯一 schema，参数与结果由两入口共同使用。名称及完整字段见[工具参考](tools-reference.md#filesystem)。旧 Tool 类和旧名字已删除，无别名、迁移或 fallback。

`file-read`、`file-list`、`file-search`、`shell-check`、`time-now` 属于 read。`file-write`、`file-edit`、`shell-run` 属于 write。时间为外部读取，不能进入纯条件。本域中 L0 装配时间，L1 另有文件，L2 再有 Shell；其他非装置域保持各自既有约束。领域服务执行时重新检查等级及所需装置，工具可见性不是唯一防线。

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

[lifecycle](../../src/tlon/operations/lifecycle.py)、[evolution](../../src/tlon/operations/evolution.py)、[model](../../src/tlon/operations/model.py) 共用成员注册表。旧 camelCase 名称删除；模型调用为 `(model-call {:prompt "任务" :max_tokens 256})`，输出使用 snake_case，session recipe 的 `:max-tokens` 配置保持原有含义。

`control` 是 Operation 的控制声明元数据，和 read/write 效果分开。完成、睡眠、退出由同一契约返回声明，LoopDriver 或程序根执行器应用调度；DSL 嵌套组合拒绝控制操作，模型的一轮也只允许独立控制调用，不能夹带普通工具。动态 ToolView 与复合 Capability 仍留在后续阶段。

程序直接模型调用使用持久 output 根，额度来自成员 llm.max_tokens；获准的输出预留成功后不退款，实际输出上限还受请求值和剩余额度限制。run-loop 显式创建 recipe.max_tokens 的 total 根，session 内 stage、上下文压缩和 Tool/提案/评审内部模型调用共享它。total 成功按可靠的实际 usage 结算；缺少 usage 或提交未知时保留预留，实际超额如实记账并阻止后续请求，token 估算不是硬上界。

模型响应与额度结算原子提交，已提交历史只还原，不重新扣额或请求 provider；未知提交不自动重发。后台反思和蒸馏另建维护根。宿主 ContextVar 传播作用域，业务参数不能重置额度；取消只作用于当前进程任务树，冻结 deadline 和预算跨重启保留。详情见 [执行作用域](../design/execution-scope.md)。


## EPIC A 审计收尾边界

spawn 的配额归属取冻结 CallContext.run_id：Tool 与恢复使用 session 根，直接 DSL 使用程序根，日志 ContextVar 不参与授权。演化和记忆的领域包装透传确定的预算/截止拒绝，provider 明确拒绝为 rejected，未知提交仍为 unknown。成员同一 write gate 显式注入 OperationExecutor 与 MemoryManager，反思/蒸馏与普通调用共享写边界。超时先返回已提交回执；未派发的新调用明确拒绝，不把旧未知 intent 覆写为失败。
