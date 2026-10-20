# Bridge API：宿主机制与 tlosure 策略的接口

> 后续方案（邮件 P2 与文件/Shell/时间 P3a 已分别通过 #185/#186 合并；P3b 已接入，后续仍分阶段实施）：[统一操作与可组合行为](operations.md) 已认可能力定义与调用入口分离的方向，施工见 [分阶段计划](operations-plan.md)。本页仍描述当前机制，不能将目标当作已实现。

Bridge API 定义宿主向策略暴露哪些数据和操作，以及每个求值语境能使用哪些词汇。它由 Python 协议、builtin 注册表、环境契约和 `ActionRequest` 组成；不是通用 FFI。语言及行为模型见 [DSL 设计](dsl.md)，字段与词汇见 [DSL reference](../reference/dsl-reference.md)。

## 领域约束

- 调度、并发、持久化、LLM 调用、Docker 和权限检查留在宿主机制层；策略组合已有机制。
- 查询只暴露受控视图，不向 DSL 开放任意 Python 属性或方法调用。
- 宿主效果统一进入 Operation，仅 `run-loop` 保留 `ActionRequest` 配方声明；异步求值在调用处交给宿主执行，记录实际结果后继续表达式。参数形状在构造请求时校验；配额、信任和实时状态仍由执行侧检查。
- 每个语境有自己的词汇注册表、符号作用域和 fuel 预算；新增词汇必须明确适用语境。
- 函数可以返回动作意图，也可以在函数体内调用效果词。动作位置不再靠语法分区枚举；纯语境通过可见词汇约束效果。
- tlosure 核心保持宿主无关；领域端口位于 tlon。纯度标记描述效果权限，不能将“读取宿主状态”误解成可重放的常量计算。

## 四类接口

### 查询

[agents/vocabulary.py](../../src/tlon/agents/vocabulary.py) 的 `ScriptHostQueries` 声明宿主查询协议，`make_member_builtins(host)` 通过闭包注入查询。实现由 [agent_registry.py](../../src/tlon/agents/agent_registry.py) 的 `RegistryQueries` 提供。Adam 获得注册表状态查询，普通成员不因此获得同样权限。

查询词登记为 `pure=True`，表示不执行外部写入；结果来自当前宿主视图，未必在不同时刻相同。生产程序驱动器将这些动态查询作为需记录的读取，恢复时复用原结果。能力 artifact 不能直接引用这些依赖隐式上下文的查询，必须由调用方显式传入数据。

### 效果

生命周期、邮件、模型、状态与其他领域操作通过 DSLAdapter 的 BuiltinCall 进入同一 OperationExecutor，结果由 ProgramStore 记录。只有 `run-loop` 保留 ActionRequest，交由宿主会话执行器运行配方。旧 spawn/start/stop/start-all 入口已由 member-spawn/start/stop/start-all 命名参数操作替代。

生产程序使用可暂停求值内核，函数、条件和传统循环中的效果都在各自调用处执行，返回值可绑定到 `let` 或用于 `if`。同步入口和 collector 可分析效果意图；ADR 0042 中先完整求值再执行的生产流程由 [ADR 0043](../decisions/0043-newspaper-member-workflow.md) 替代。完整恢复边界见 [调用恢复](invocation-recovery.md) 与 [runtime](runtime.md)。P4a 统一两入口叶子操作回执到 ProgramStore，run-loop 和旧宿主查询仍沿用 effects；不将这些桥接查询误称为已经完成 Operation 化。

| 效果 | 返回值与边界 |
| --- | --- |
| `(state-get {:key "key"})` | 成员私有状态值，缺失为 `nil`；读取结果参与重放 |
| `(state-put {:key "key" :value value})` | 返回写入值；状态与该效果结果同事务提交 |
| `(model-call {:prompt "任务数据" :max_tokens 256})` | `{:content "..." :usage {...}}`；不附工具，保留人格、RULES 与协议，额度受成员与规则预算约束 |
| `(mail-send {:to ["地址"] :content "正文" :request_id "稳定键"})` | 真实邮局回执；拒收抛 RejectedError，未知抛 UnknownOutcomeError，均带结构化 data |

`mail-send` 可选 `:subject` 和 `:request_id`；邮件参数/结果键保留 snake_case，如 `:mail_id`、`:request_id`、`:local_recipients`。`model-call` 的 usage 保留 provider 的 snake_case 键，如 `:completion_tokens`。`mail-field` 是纯的字段适配器，从真实触发邮件读取 ID、发件地址、主题、正文等白名单字段；不开放对象属性访问。

### 环境

类型声明集中在 `agents/vocabulary.py`，运行时由相应执行点构造：

| 语境 | 环境契约 | 可见内容 |
|---|---|---|
| 规则门控和动作 | `RuleEnv` | `msg`、`event`、`self` |

| 循环终止条件 | `LoopEnv` | `self`、`state`、`limits` |
| stage 条件 | `StageCondEnv` | `state`、`scratch`、`round` |

规则始终保留原始事件与邮件快照。效果完成后继续原表达式；返回值由程序显式命名，不新增完成动作求值环境，也不隐式绑定 `result`。

社会政策是独立的 pull 型求值语境，其 hook 数据由调用点物化，见 [society-policy](society-policy.md)。

### 注册表

成员动作、会话终止条件和 stage 条件分别使用 `make_member_builtins`、`LOOP_BUILTINS`、`STAGE_COND_BUILTINS`。加载校验与执行须使用相同词汇；受信能力注入成员词汇，暂不扩展到固定的会话条件注册表，详见 [capabilities](capabilities.md)。

## 当前政策数据的归属

桥已经支持下列政策配置；它们不是待施工清单：

| 政策 | 当前来源 | 机制归属 |
|---|---|---|
| 启停、噪声熔断 | Adam 内置 spec | 注册表与健康事实生产 |
| 检索权重、冲突阈值、蒸馏与反思参数 | 成员 `memory/config.json`、spec `:reflect` | memory / runtime |
| 终止回信措辞 | 会话配方 `:replies`，未声明时使用引擎默认措辞 | runtime 的终止路径 |
| fitness 与 trust | `policy.tlon` hook | society-policy |
| 复合能力 | 受信能力 artifact | 加载准入与效果执行 |

`AGENT_NOISY` 有 Adam 消费政策；`MAIL_ORPHAN_SCAN` 不因为存在就必须产生反应，避免周期事实转成周期噪声。迁移政策的理由应是让这部分规则可编辑、可评审，而非单纯减少 Python 行数。

## 演进与检查

`:version` 标识 spec 格式，不建立运行时词汇版本矩阵。变更词汇与语境时，同步修改校验、执行环境和 reference；不能保留文档中已经删除的词汇。既有演进取舍见 [ADR 0020](../decisions/0020-bridge-api.md)；能力的元数据退役机制见 [capabilities](capabilities.md)。ADR 中设想的通用加载期 deprecation warning 不应被当作已经具备的机制。

[scripts/gen_reference_docs.py](../../scripts/gen_reference_docs.py) 对其负责的 reference 区块做生成与 freshness 检查，包括工具、REST、事件、配置等清单；并非本文全部语境契约均已自动生成，手写部分仍需核对代码。

加载校验能拒绝未注册词和未定义符号，但不能保证合法程序运行时无类型错误、无 fuel 耗尽。失败处理由相应运行语境定义。

## 历史与未实施方向

桥的形式化见 [ADR 0020](../decisions/0020-bridge-api.md)，完整函数能力见 [ADR 0021](../decisions/0021-tlosure-full-functions.md)，社会政策语境见 [ADR 0022](../decisions/0022-society-policy-context.md)。完整函数和复合能力均已实现，不再列为远期阶段。

当前不提供通用 FFI、宏、`eval`、可变全局状态或 DSL 并发。更换语言宿主以及能力进入 LLM 工具面的方案尚未实施，不能从 SCI 兼容方向推导出当前存在这些能力。
