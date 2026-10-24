# 调用记录与恢复权威（P4a）

> 后续修订：[ADR 0049](../decisions/0049-retire-agent-session.md) 已删除 agent session 及旧历史读取；下文旧阶段验收记录保留为历史，不代表当前兼容能力。
> P5：执行与恢复仅有普通模块路径；旧 stage、run-loop、SessionScheduler 和 SessionJournal.resume 已删除。本文的 P4/P6 等旧操作统一阶段记录保留为历史，当前调用权威仍为 ProgramStore；边界以 [Runtime](runtime.md) 为准。

> 后续覆盖：P1–P5 已完成，退役后的旧 API、宿主查询和存储边界见 [退役代码清理](workflow-retirement-cleanup.md)。以下阶段描述保留施工背景：[ADR 0046](../decisions/0046-programmable-workflows.md) 与 [可编程工作流程](programmable-workflows.md) 将以模块函数替代 stage 引擎，施工见[分阶段计划](programmable-workflows-plan.md)。P1 的当前用法见 [模块程序指南](../guides/writing-a-module-program.md)。本文的 stage 描述仅适用于 v1。


状态：EPIC A、P5 #193 与 P6 #194 已合并，P7 暂停；本文保留当时恢复契约及外部审查后的根隔离决策；已删除机制以文首覆盖说明为准。总体设计见 [operations](operations.md)，验收见 [operations-plan](operations-plan.md)，共享执行见 [execution-scope](execution-scope.md)。

## 所有权

Operation 定义输入、输出、效果和宿主实现；DSL 与模型 Tool 只负责入口投影，分别位于 operations/dsl.py 与 operations/adapters.py。OperationExecutor 在调用实际 handler 前后统一处理持久化。ProgramStore 是叶子操作结果的唯一权威，继续使用成员原有 SQLite，不增加另一套数据库。程序 runs 保存冻结入口声明与输入，package_edn 保存模块包；rule_edn 的旧字段名不表示仍有旧规则解释器。注册表读取现为普通 Operation，旧 effects 执行路径已删除。SessionJournal 保留模型请求、工具分发和交互结果，恢复时通过 invocation 引用查询同一 ProgramStore。

纯计算不写叶子记录。运行时始终给 executor 注入成员 ProgramStore；低层隔离测试可以使用无存储 executor，但该模式不提供恢复保证。私有状态写入与 invocation 成功回执在同一 SQLite 事务提交，不再另建 state 专用 effects 回执。

## 稳定身份与冻结

每个调用持久化 call_id、root_id、结构路径、操作名、规范化输入 JSON、契约 definition_id 和宿主 build_id。程序路径由求值顺序产生；会话路径包含 round 序号和模型 ToolCall.id，因此不同轮重复的模型 ID 不会共用调用记录。同一轮重复 ToolCall.id 在分发前整轮拒绝，不启动任何工具或 intent；恢复遇到这种已歧义的旧 dispatch 也拒绝，不能按最后一个同 ID 结果覆盖。完成先后不能改变身份。文件溢出回执的文件名取完整 call_id 的哈希，模型协议仍保留原 ToolCall.id。

root 首次创建时冻结宿主身份。宿主身份覆盖 tlon/tlosure Python 运行代码、打包的 uv.lock 摘要、Python 版本、已安装 distribution 的版本，以及宿主 builtin 定义。契约另有内容指纹。恢复先核对冻结身份，再进入程序或构建会话上下文；已存调用同样核对名称、路径、输入和契约。任何不一致都报告 RecoveryMismatchError，不能将旧位置解释成新工作。不同根独立校验，一个旧根失配不阻止其他成员或新根运行。

这是保守拒绝策略：不恢复旧宿主代码，不提供跨版本兼容。无关已安装包版本变化也可能使旧根失配。构建脚本与质量门检查打包摘要是否与 uv.lock 一致，wheel 携带摘要模块，运行时无需源码仓库中的锁文件。

输入 JSON 按键规范化，用于身份比较；输出 JSON 保留原 map 插入顺序。DSL 可以通过 str 或键遍历观察顺序，重放必须维持原结果，不能将输出排序后再声称等价。

## 执行与故障窗口

| 持久化状态 | 再次调用的行为 |
|---|---|
| 无 intent | 校验新调用，记录 intent，再执行 handler |
| 已有成功结果 | 返回原结果，不重新采样、不重新授权或执行 |
| 已有确定错误 | 重放原错误及数据；输出违约同时保留原始回执 |
| 只有 read intent 或 unknown | 允许重新采样，成功后保存实际结果 |
| 只有 retry_safe write intent 或 unknown | 使用原 call_id 重试，由领域服务兑现幂等承诺 |
| 只有其他 write intent 或 unknown | 返回 UnknownOutcomeError，禁止自动再执行 |
| 已有身份不一致 | 拒绝恢复，不执行新效果 |

调用在 handler 前写 intent，之后提交成功或结构化错误。取消、超时或提交窗口故障后先查同一调用的真实回执，已提交的成功或确定错误优先。只在可能已派发且没有终态回执时保留 unknown；能证明本次 handler 未开始的超时为确定拒绝，不能把旧未知 intent 或另一并发执行的 intent 覆写成未执行。外部取消继续传播，不能据此撤销已经提交的事实。输出 schema 违约也是已执行调用的结果，保存违约错误与可序列化原回执，不能用重试修补一次写入。无法 JSON 编码的原始返回只保留诊断描述。

同一存储的同一 root/path 在进程内用共享异步锁合并并发重试；SQLite 唯一约束保护调用位置。成员写操作另有 task-owner 可重入 gate，同批 Tool 的写入按声明顺序执行；这些进程内锁不是多进程执行租约。普通领域写入和 SQLite 回执不能自动形成跨系统事务；只有 state-put 在同一事务中原子保存状态与结果，邮件等重试安全操作依赖领域幂等键。

## 程序与会话协作

ProgramDriver 不再为 Operation 另写 effects 和 intents：它构造稳定调用上下文并交给 executor。已提交模型调用先重放结果，额度已经由统一账本提交，不再次扣减；不能因为当前剩余额度变化而重新请求模型。

LoopDriver 的 tool/dispatch 保存 root/path/call_id/build_id/definition_id。SessionJournal 恢复缺少 tool/result 的 dispatch 时，让已冻结会话配方重建的运行时查询或执行同一调用：提交过的效果返回原回执；尚无 intent 的分发可以首次执行；不安全写入的孤立 intent 保持 unknown。已有 journal 结果也必须先通过根身份校验。未注册 Tool 的分发同样保存完整身份，definition_id 为 null；同构建且仍未注册时恢复确定性的不存在错误，缺失元数据或定义改变则拒绝。journal 不再把所有孤立 dispatch 一律伪造成 interrupted。

例如 mail-send 已投递、invocation 已提交，但 tool/result 尚未写入时进程退出：重启后 journal 提供同一引用，executor 返回 ProgramStore 中的投递回执，收件箱不增加第二封邮件。若 shell-run 已写 intent，但退出前没有保存结果，则无法判断命令是否执行；恢复明确返回 unknown，不自动重跑。

## 存储格式与下一阶段

新 ProgramStore 使用明确的 SQLite user_version。包含旧表而没有新版本标记的数据库直接拒绝打开，不转换、不清库，不改写原历史。旧 session 缺少冻结身份时同样拒绝恢复；只读浏览已有 session 事件不受影响。使用者需要自行决定旧运行的处理方式，不能为通过恢复测试删除真实 ~/.tlon。

P4b 使用同一数据库的 budgets/model_allocations 记录额度与预留，模型完整响应仍保存在 invocations；删除驱动局部计数、_HOST_QUERIES pure 重写与旧 MemberCapabilities 装配容器名。当前 ProgramStore 格式版本为 2，旧版本明确拒绝，不转换、不清库。P4a/P4b 及收尾已合并；Capability 的复合依赖快照已删除。P7 动态视图与统一授权尚未实施，现已暂停。


P4b 将 sleeping?、running?、lazy?、member-roster 收敛为非确定性只读查询，ProgramDriver 不再按名称改写 pure；每次外部读取采样并记录，重放返回原值。P6 进一步禁止其进入任何纯条件：读取放在 actions，已记录的返回值再参与计算。when、until 和 stage-if 共用纯函数准入与显式数据，Builtin.read_only 字段随旧条件准入一起删除。装配容器与入口现为 MemberServices / assemble_member_services，构造参数 services，无旧名称别名。

## 坏根隔离与操作员排查

恢复失配和无法自动重试的效果未知（含模型请求）只隔离其原根。程序在 runs.error 保存 recovery_blocked 与原因，completed 不伪装为成功；会话写 session/recovery-blocked，并在既有生命周期元数据标记隔离。它不属于正常完成会话，不进入蒸馏；再次恢复会明确抛出 RecoveryBlockedError，不能返回成功，也不能重新请求模型。相关父程序同样隔离，防止会话以后继续写入新状态。可按幂等回执恢复的临时邮件失败仍等待原根恢复。

操作员可先通过会话事件查看 session/recovery-blocked 的 session_id、原因和关联程序；程序错误及冻结输入保留在成员 program.sqlite3 的 runs 表，invocations 与 model_allocations 保留调用和预留事实。隔离后新邮件与其他根可继续工作，无需删库。没有提供“强制重放未知效果”按钮；需要重做业务时应在确认外部效果后显式发起新的任务，不修改旧根身份、不清空真实 ~/.tlon。数据库整体格式不兼容仍在打开时拒绝，这是与单根身份失配不同的错误。

## Capability 删除后的边界

[ADR 0045](../decisions/0045-remove-capabilities.md) 删除复合定义、目录及 root_definitions 快照。程序源码与输入仍冻结，宿主操作仍通过构建身份、调用参数和持久回执校验恢复。工具目录来自成员实际装配的宿主服务。

## 模块程序模型边界（工作流程 P2）

普通程序与 v1 stage 通过同一 ModelService 调用成员模型。v2 入口受理时将宿主系统提示、工具定义和非秘密模型配置随源码包原子冻结；后续调用使用冻结值。新请求还校验当前工具定义，已提交的历史结果则按冻结契约重放。纯消息模块 `tlon.messages` 随运行源码冻结，不新增模型结果日志。

`model-call` 外层 Operation 与内部模型调用各有稳定路径。模型成功响应与额度结算已提交、外层回执缺失时，executor 在预算、取消和截止检查之前校验原请求并补回外层结果；明确拒绝已记录时补回 RejectedError，保留已退款事实。恢复只读取原模型记录，不派发 provider 请求。内部只有未知 intent 时仍拒绝自动重发，保留预留。output 成功保留输出上限费用，total 按可信实际用量结算，核验函数不会另建预算根。

P4b 新交互阅读视图以 program:<run_id> 引用 ProgramStore，按读取时重建而非写第二套 session journal。只读投影包括已提交模型/工具结果、未决证据和入口终态；它不驱动恢复。蒸馏与反思固定来源批次和输入，使用独立稳定维护根；完成计数和消费进度不再依赖开始时间标量或内存 note_interaction。旧 journal 的 recovery_blocked 仍从旧正常完成集合排除；新失败运行的阅读事件明确携带错误及未决证据，不能冒充成功效果。
