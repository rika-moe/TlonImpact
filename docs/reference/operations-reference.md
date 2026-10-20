# Operation 调用契约（P1 + 邮件 P2）

当前状态：P1 已通过 #184 合并 main。P2 已将五个邮件操作接入生产成员并通过本地验收，待 PR 合并；其余 41 个工具仍走既有 Tool 路径，Capability artifact 也未转换为复合 Operation。目标设计见 [operations](../design/operations.md)，实施顺序见 [operations-plan](../design/operations-plan.md)。

## 定义和执行

`tlon.operations.core.Operation` 接收 name、description、input_schema、output_schema、handler、effects。schema 在准入时自检并保存为不可变 JSON 字符串；公开属性每次返回独立副本。名称满足 `[A-Za-z_][A-Za-z0-9_-]*`，最长 64 字符，不得使用 nil/true/false 或覆盖语言特殊形式、通用内置函数。

空 effects 表示纯计算：handler 是同步 `(CallContext, dict[str, Any]) -> Any`。非空 effects 是 read/write 集合：handler 必须是 async，并由异步执行边界等待结果。纯 handler 返回 awaitable 会被拒绝，不能把异步 IO 谎报为纯计算。注册表固定定义集合，同名拒绝；宿主给不同成员构造 scoped handler 和 `CallContext(member_id, run_id, call_id)`，上下文不在业务参数或 DSL env 内。

`OperationExecutor.call_pure` 仅执行纯操作；`call` 支持两类操作。执行顺序为输入严格 JSON 复制与 schema 校验、handler、输出严格 JSON 复制与 schema 校验。修改 handler 参数、返回值或 Tool schema 投影都不能污染原调用数据或权威 schema。

当前 **不提供**通用授权名单、动态工具视图、复合依赖、统一预算或全局调用日志；P2 邮件通过现有 ProgramStore/session journal 记录和 PostOffice 幂等回执实现恢复，不另建存储。上下文隔离不是完整授权系统。输出不合约报 `OutputError`，不得据此假定效果未发生或自动重试。

## Schema 子集

采用 jsonschema 的 Draft202012Validator。输入根为闭合 object（additionalProperties=false）；所有 object 显式声明 properties/required/additionalProperties，additionalProperties 仅接受 boolean；array 必须声明 items。每个 schema 节点必须声明 type，支持 object、array、string、number、integer、boolean、null，也支持 type 数组表达 nullable。

准入关键字仅支持：type、properties、required、additionalProperties、items、enum、minimum、maximum、exclusiveMinimum、exclusiveMaximum、multipleOf、minLength、maxLength、minItems、maxItems、uniqueItems、description、title、$schema。$schema 若提供必须为 `https://json-schema.org/draft/2020-12/schema`。不支持 default（不隐式补值）、$ref（本地和远程均拒绝）、format、组合 schema 或任意扩展关键字；未知关键字拒载。嵌套校验、bool/number 区分由成熟校验器执行。

## 入口与错误

DSL：`DSLAdapter.builtins()` 提供实际 purity 的 Builtin；调用形式为 `(operation-name {:parameter value})`，严格一个对象参数。纯操作直接由 Evaluator 调用；read/write 经 `eval_async(..., adapter.handle)` 悬挂并恢复。同步 Evaluator 不得执行效果操作。普通语言函数 API 不变。

Tool：`OperationTool(executor, name)` 从同一契约投影协议定义，可注册到既有 ToolExecutor。它覆盖旧浅层 validate_arguments，把校验一次性留给共享调用边界；不另写校验规则。业务参数 `self` 也可使用。协议投影修改不改变操作定义。

JSON 边界只接受 None、精确 bool/int/有限 float/str/list/dict；最大嵌套 100，循环数据拒绝。DSL 额外允许 Keyword map key，按原拼写转换为 string；返回对象键转 Keyword，字符串值不变。Keyword 值、Symbol、Closure、Form、tuple、NaN/Infinity 均拒绝。解析 EDN map 与 assoc/conj 构造 map 时，Keyword/string/Symbol 等相等但类型不同的键在写入 dict 前拒绝，避免静默覆盖；同型重复键沿既有语义保留末值。已经被外部 Python dict 覆盖的信息无法恢复，不宣称能够检测。

输入错误为 InputError，输出错误为 OutputError，均继承 OperationError/EvalError，DSL 保留结构化异常类型；Tool 转为失败 ToolResult。邮件领域的明确拒收使用 RejectedError，结果未知使用 UnknownOutcomeError；二者继承 OperationError 并携带 data，unknown 保留 request_id。Tool 入口保留这些结构化信息。其他领域尚未切换，不宣称已有通用重试机制。

Tool 协议根参数必须是 JSON object 的字符串键。OperationTool 在 ToolExecutor 中直接接收参数对象，根级非字符串键和嵌套非 JSON 值均由共享边界返回 InputError；尚未切换的旧 Tool 保留原关键字参数派发。

## 邮件 P2

五个操作 `mail-send`、`mail-forward`、`mail-check-inbox`、`mail-search`、`mail-mark-read` 共用 [权威定义](../../src/tlon/operations/mail.py)，输入与输出均为 snake_case；DSL 也使用 `:mail_id`、`:request_id` 等原拼写键。schema 与参数表见 [工具参考](tools-reference.md#mail)。所有服务绑定成员身份，不接收可伪造的 owner/from 参数。

程序使用冻结 run 和调用序号，工具使用 session 和 tool call ID 生成稳定 call_id。默认 request_id 来自该 ID，显式重试可以提交原 request_id。输入或输出错误不代表效果一定未发生；超时、中断和无法确认提交必须保留 unknown 及原 request_id。复用邮局回执不会重复入箱；会话恢复不自动重发孤立 dispatch。

已删除旧五邮件 Tool 及旧 DSL 邮件词，不提供别名或迁移。旧 Behavior、Capability 和未完成运行若引用旧名，将不兼容；仓库示例同步切换，用户数据不自动修改。DAG、动态工具视图、统一预算和一般根调用日志仍属于 P4-P7。
