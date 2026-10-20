# Operation 调用契约（P1–P3b）

当前状态：P1、P2、P3a 已分别通过 #184/#185/#186 合入 main。P3b 将记忆、任务、人格/联系人、观测及私有状态接入相同边界，待完整验收与 PR；生命周期/提案/模型调用仍待 P3c，Capability artifact 尚未转换为复合 Operation。目标设计见 [operations](../design/operations.md)，实施顺序见 [operations-plan](../design/operations-plan.md)。

## 定义和执行

`tlon.operations.core.Operation` 接收 name、description、input_schema、output_schema、handler、effects、retry_safe、default_timeout。schema 在准入时自检并保存为不可变 JSON 字符串；公开属性每次返回独立副本。名称满足 `[A-Za-z_][A-Za-z0-9_-]*`，最长 64 字符，不得使用 nil/true/false 或覆盖语言特殊形式、通用内置函数。

空 effects 表示纯计算：handler 是同步 `(CallContext, dict[str, Any]) -> Any`。非空 effects 是 read/write 集合：handler 必须是 async，并由异步执行边界等待结果。纯 handler 返回 awaitable 会被拒绝，不能把异步 IO 谎报为纯计算。注册表固定定义集合，同名拒绝；宿主给不同成员构造 scoped handler 和 `CallContext(member_id, run_id, call_id)`，上下文不在业务参数或 DSL env 内。

`OperationExecutor.call_pure` 仅执行纯操作；`call` 支持两类操作。执行顺序为输入严格 JSON 复制与 schema 校验、handler、输出严格 JSON 复制与 schema 校验。修改 handler 参数、返回值或 Tool schema 投影都不能污染原调用数据或权威 schema。

当前 **不提供**通用授权名单、动态工具视图、复合依赖、统一预算或全局调用日志；P2 邮件通过现有 ProgramStore/session journal 记录和 PostOffice 幂等回执实现恢复，不另建存储。上下文隔离不是完整授权系统。输出不合约报 `OutputError`，不得据此假定效果未发生或自动重试。

## Schema 子集

采用 jsonschema 的 Draft202012Validator。输入根为闭合 object（additionalProperties=false）；所有 object 显式声明 properties/required/additionalProperties，additionalProperties 仅接受 boolean；array 必须声明 items。除 JSON Schema 原生 boolean schema（true 接受任意严格 JSON、false 拒绝所有值）外，每个 schema 节点必须声明 type，支持 object、array、string、number、integer、boolean、null，也支持 type 数组表达 nullable。

准入关键字仅支持：type、properties、required、additionalProperties、items、enum、minimum、maximum、exclusiveMinimum、exclusiveMaximum、multipleOf、minLength、maxLength、minItems、maxItems、uniqueItems、description、title、$schema。$schema 若提供必须为 `https://json-schema.org/draft/2020-12/schema`。不支持 default（不隐式补值）、$ref（本地和远程均拒绝）、format、组合 schema 或任意扩展关键字；未知关键字拒载。嵌套校验、bool/number 区分由成熟校验器执行。

## 入口与错误

DSL：`DSLAdapter.builtins()` 提供实际 purity 的 Builtin；调用形式为 `(operation-name {:parameter value})`，严格一个对象参数。纯操作直接由 Evaluator 调用；read/write 经 `eval_async(..., adapter.handle)` 悬挂并恢复。同步 Evaluator 不得执行效果操作。普通语言函数 API 不变。

Tool：`OperationTool(executor, name)` 从同一契约投影协议定义，可注册到既有 ToolExecutor。它覆盖旧浅层 validate_arguments，把校验一次性留给共享调用边界；不另写校验规则。业务参数 `self` 也可使用。协议投影修改不改变操作定义。

JSON 边界只接受 None、精确 bool/int/有限 float/str/list/dict；最大嵌套 100，循环数据拒绝。DSL 额外允许 Keyword map key，按原拼写转换为 string；返回对象键中能由现有 EDN parser 完整往返的键转为 Keyword；邮箱地址、空串、空格、Unicode 等不能表示为关键字的键保留字符串，字符串值不变。Keyword 值、Symbol、Closure、Form、tuple、NaN/Infinity 均拒绝。解析 EDN map 与 assoc/conj 构造 map 时，Keyword/string/Symbol 等相等但类型不同的键在写入 dict 前拒绝，避免静默覆盖；同型重复键沿既有语义保留末值。已经被外部 Python dict 覆盖的信息无法恢复，不宣称能够检测。

输入错误为 InputError，输出错误为 OutputError，均继承 OperationError/EvalError，DSL 保留结构化异常类型；Tool 转为失败 ToolResult。领域的明确拒收使用 RejectedError，结果未知使用 UnknownOutcomeError；二者继承 OperationError 并携带 data，unknown 保留 request_id。Tool 入口保留这些结构化信息。该结构化错误同样用于文件和 Shell；不宣称已有通用重试机制。

Tool 协议根参数必须是 JSON object 的字符串键。OperationTool 在 ToolExecutor 中直接接收参数对象，根级非字符串键和嵌套非 JSON 值均由共享边界返回 InputError；尚未切换的旧 Tool 保留原关键字参数派发。

## 邮件 P2

五个操作 `mail-send`、`mail-forward`、`mail-check-inbox`、`mail-search`、`mail-mark-read` 共用 [权威定义](../../src/tlon/operations/mail.py)，输入与输出均为 snake_case；DSL 也使用 `:mail_id`、`:request_id` 等原拼写键。schema 与参数表见 [工具参考](tools-reference.md#mail)。所有服务绑定成员身份，不接收可伪造的 owner/from 参数。

程序使用冻结 run 和调用序号，工具使用 session 和 tool call ID 生成稳定 call_id。默认 request_id 来自该 ID，显式重试可以提交原 request_id。输入或输出错误不代表效果一定未发生；超时、中断和无法确认提交必须保留 unknown 及原 request_id。复用邮局回执不会重复入箱；会话恢复不自动重发孤立 dispatch。

已删除旧五邮件 Tool 及旧 DSL 邮件词，不提供别名或迁移。旧 Behavior、Capability 和未完成运行若引用旧名，将不兼容；仓库示例同步切换，用户数据不自动修改。DAG、动态工具视图、统一预算和一般根调用日志仍属于 P4-P7。

## 文件、Shell 与时间 P3a

[file Operation](../../src/tlon/operations/filesystem.py)、[Shell Operation](../../src/tlon/operations/shell.py) 和[时间 Operation](../../src/tlon/operations/time.py) 各自保有唯一 schema，参数与结果由两入口共同使用。名称及完整字段见[工具参考](tools-reference.md#filesystem)。旧 Tool 类和旧名字已删除，无别名、迁移或 fallback。

`file-read`、`file-list`、`file-search`、`shell-check`、`time-now` 属于 read。`file-write`、`file-edit`、`shell-run` 属于 write。时间为外部读取，不能进入纯条件。本域中 L0 装配时间，L1 另有文件，L2 再有 Shell；其他非装置域保持各自既有约束。领域服务执行时重新检查等级及所需装置，工具可见性不是唯一防线。

文件服务只使用成员的 ExecutionDevice，沿用容器工作目录、读写路径白名单与 archive-only 文件柜。读取额外允许自身只读 `/tlon-home`；写入仅可到配置的 allowed_paths。没有通用宿主文件系统后门。归档内容仅在内存读取，成员路径和归档条目仍需独立校验。

`default_timeout` 是宿主操作声明的默认执行秒数；Shell handler 和引擎等待预算引用同一默认值，显式 timeout 参数仍优先，schema 不因此引入 default 关键字。

`retry_safe` 是宿主对中断后重新调用是否安全的最小声明。程序沿用同一 ProgramStore 数据库：非幂等 write 先写 intent，再调用 handler，再记录结果；已有结果重放，只有 intent 而无结果时返回 unknown，禁止自动重新执行。取消同样不能当作无副作用。邮件仍使用 PostOffice 的稳定 request_id 幂等重试。这没有引入第三份日志，也未完成 P4 的通用调用身份、预算或并发写入排序。

会话保留 session journal 的 dispatch/result/collect：非幂等写入的孤立 dispatch 标记 unknown 且不可安全重试；工具适配器不伪造回执。Shell 后台任务只由成员 ShellService 持有，查询不复制任务表；成员关闭先取消并等待后台任务清理，再关闭装置。后台句柄不跨进程恢复，不自动重启命令。


## 记忆、任务、人格/联系人、观测与状态 P3b

各域定义见[工具参考](tools-reference.md#memory)。记忆与会话读取绑定成员 MemoryManager/SessionJournal，任务绑定自己的 TaskManager，人格与联系人只接触自身文件；identity/soul 可以修改，RULES 不可自改。观测为 read，保留原有公开声誉/trace 与自身邮箱统计的边界。旧 Tool 类、名称和任务 manager 的旧包路径直接删除。

state-get/state-put 使用一个对象参数，例如 `(state-get {:key "edition"})`、`(state-put {:key "edition" :value {:period "001"}})`，直接返回严格 JSON 值，缺失返回 null。输入根始终是闭合对象，value 和返回值用原生 boolean schema 表达动态 JSON；DSL 键不做 snake/kebab 隐式转换。

state-put 将状态与该次调用结果放在 ProgramStore 的同一事务；程序使用宿主 run/ordinal，工具使用宿主 call_id 的独立效果记录。同调用相同参数重试返回原值，不覆盖后来的状态；同调用不同参数拒绝。普通领域写入仍按非幂等 intent 保护，结果不确定保留 unknown 和 call_id，不能把失败当作未产生副作用。

任务落盘通过临时文件、fsync 和原子 replace；写失败不再伪报成功。人格文件写成功而账本追加失败时，保留成功回执并显式告警。保持原有 TaskManager 单实例同步调用模型；未扩展为跨进程任务写入协调器，也未修改其原有损坏文件读取策略。
