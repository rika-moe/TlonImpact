# 调用记录与恢复权威（P4a）

状态：P3c 已通过 #188 合并；本文描述 P4a 当前实现。总体设计见 [operations](operations.md)，施工边界见 [operations-plan](operations-plan.md)。共享预算、取消、截止时间和成员写排序属于后续 P4b，当前不提供这些保证。

## 所有权

Operation 定义输入、输出、效果和宿主实现；DSL 与模型 Tool 只负责入口投影。OperationExecutor 在调用实际 handler 前后统一处理持久化。ProgramStore 是叶子操作结果的唯一权威，继续使用成员原有 SQLite，不增加另一套数据库。程序 runs 保存冻结规则与输入；effects 仅保留 run-loop 和现有非 Operation 宿主查询的编排记录。SessionJournal 保留模型请求、工具分发和交互结果，恢复时通过 invocation 引用查询同一 ProgramStore。

纯计算不写叶子记录。运行时始终给 executor 注入成员 ProgramStore；低层隔离测试可以使用无存储 executor，但该模式不提供恢复保证。私有状态写入与 invocation 成功回执在同一 SQLite 事务提交，不再另建 state 专用 effects 回执。

## 稳定身份与冻结

每个调用持久化 call_id、root_id、结构路径、操作名、规范化输入 JSON、契约 definition_id 和宿主 build_id。程序路径由求值顺序产生；会话路径包含 round 序号和模型 ToolCall.id，因此不同轮重复的模型 ID 不会共用调用记录。完成先后不能改变身份。文件溢出回执的文件名取完整 call_id 的哈希，模型协议仍保留原 ToolCall.id。

root 首次创建时冻结宿主身份。宿主身份覆盖 tlon/tlosure Python 运行代码、打包的 uv.lock 摘要、Python 版本、已安装 distribution 的版本，以及成员加载的闭包定义。契约另有内容指纹。恢复先核对冻结身份，再进入程序或构建会话上下文；已存调用同样核对名称、路径、输入和契约。任何不一致都报告 RecoveryMismatchError，不能将旧位置解释成新工作。不同根独立校验，一个旧根失配不阻止其他成员或新根运行。

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

调用在 handler 前写 intent，之后提交成功或结构化错误。取消、超时或提交窗口故障不能证明效果未发生；没有终态回执时保留 unknown 语义。输出 schema 违约也是已执行调用的结果，保存违约错误与可序列化原回执，不能用重试修补一次写入。无法 JSON 编码的原始返回只保留诊断描述。

同一存储的同一 root/path 在进程内用共享异步锁合并并发重试；SQLite 唯一约束保护调用位置。该锁不等于成员全部写入的顺序保证，也不是多进程执行租约。普通领域写入和 SQLite 回执不能自动形成跨系统事务；只有 state-put 在同一事务中原子保存状态与结果，邮件等重试安全操作依赖领域幂等键。

## 程序与会话协作

ProgramDriver 不再为 Operation 另写 effects 和 intents：它构造稳定调用上下文并交给 executor。已提交模型调用先重放结果，再应用原有预算计量，不能因为当前剩余额度变化而重新请求模型。

LoopDriver 的 tool/dispatch 保存 root/path/call_id/build_id/definition_id。SessionJournal 恢复缺少 tool/result 的 dispatch 时，让已冻结会话配方重建的运行时查询或执行同一调用：提交过的效果返回原回执；尚无 intent 的分发可以首次执行；不安全写入的孤立 intent 保持 unknown。已有 journal 结果也必须先通过根身份校验。未注册 Tool 的分发同样保存完整身份，definition_id 为 null；同构建且仍未注册时恢复确定性的不存在错误，缺失元数据或定义改变则拒绝。journal 不再把所有孤立 dispatch 一律伪造成 interrupted。

例如 mail-send 已投递、invocation 已提交，但 tool/result 尚未写入时进程退出：重启后 journal 提供同一引用，executor 返回 ProgramStore 中的投递回执，收件箱不增加第二封邮件。若 shell-run 已写 intent，但退出前没有保存结果，则无法判断命令是否执行；恢复明确返回 unknown，不自动重跑。

## 存储格式与下一阶段

新 ProgramStore 使用明确的 SQLite user_version。包含旧表而没有新版本标记的数据库直接拒绝打开，不转换、不清库，不改写原历史。旧 session 缺少冻结身份时同样拒绝恢复；只读浏览已有 session 事件不受影响。使用者需要自行决定旧运行的处理方式，不能为通过恢复测试删除真实 ~/.tlon。

P4a 保留现有预算计量、并发策略、_HOST_QUERIES 的 pure 重写和 MemberCapabilities 名称。P4b 将处理共享预算、取消、截止时间、成员写排序与重入，再删除这些既有机制。只有两部分都验收合并后，才能将 P4 标记完成。
