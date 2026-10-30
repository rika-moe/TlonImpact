# DSL：工作流程就是普通程序

当前实现遵循 [ADR 0046](../decisions/0046-programmable-workflows.md)。行为文件只声明 v2 事件入口及资源约束；条件判断、计划、模型对话、工具执行、核验和修订都写在普通模块函数里。完整语法见 [DSL reference](../reference/dsl-reference.md)，编写示例见 [模块指南](../guides/writing-a-module-program.md)。

## 分工

`tlosure` 提供 EDN、词法作用域、一等函数、模块、静态校验、fuel 与深度限制，不依赖 tlon。模块初始化必须纯；导入导出显式，私有函数不能跨模块调用。

`tlon` 提供 Operation 目录及宿主服务。调用发生在求值中的异步边界，调用结果成为普通值。模型返回工具建议，程序再明确选择是否执行；函数不是 Tool。宿主绑定成员身份、系统约束、工具目录、预算、写 gate 与恢复身份。

行为文件将事件映射到导出的限定函数。入口收到物化事件，自行读取数据与组合函数；只有根返回值的调度意图作用于成员。内层函数返回相同形状也只是普通值。

## 安全与持久化

静态校验检查完整依赖闭包、符号、路径和模块初始化纯度。fuel/depth 控制计算，根模型预算和截止时间约束外部调用。源码、输入、依赖、约束与模型上下文受理时冻结；恢复不能改用当前磁盘源码。

ProgramStore 是运行、调用回执和终态的唯一恢复权威。不确定外部结果保留 unknown 并阻塞，不能把失败包装成成功值再自动重试。RunJournal 只读 ProgramStore，不再恢复执行。

## 标准程序与上下文

标准库以普通函数实现消息构造、对话、计划、核验与压缩。内置成员调用 `tlon.agent/respond`；路由和转发模块直接调用领域操作，不调用模型。发送前审阅同样是普通程序，没有宿主 stage 名称。

`member-context` 返回固定结构的成员资料，程序组织 messages。它不接受 section 配方。人格/协议为宿主模型上下文；文档和标准库源码通过 workspace 只读映射及现有工具读取。

## 已删除边界

P5 删除 v1 rules/when/actions 解释器、run-loop、SessionRecipe、StageServices/StageOutcome、LoopDriver、AgentSession、SessionScheduler、ContextPressure 及控制操作嵌套限制。不保留兼容执行或迁移。非入口旧 program 未完成工作与读失败仍阻塞成员；按 [ADR 0049](../decisions/0049-retire-agent-session.md)，旧 agent session 数据库不再读取，也不兼容或迁移，用户磁盘归档不自动删除。

EDN 解析和 JSON 编码遵循有限规模契约：保留整数键与共享引用，超限明确失败。源码、节点、map 基数、深度和编码字节硬上限独立于 fuel，见 [语言参考](../reference/tlosure-reference.md#资源规模硬上限adr-0054) 与 [ADR 0054](../decisions/0054-tlosure-resource-limits.md)。

`map`、`filter`、`reduce` 是接收函数值的普通高阶函数；注册表函数与闭包均可传参、返回和绑定。回调经同一求值内核计费并暂停/恢复效果，保留向量的立即求值语义。见 [ADR 0021 修订](../decisions/0021-tlosure-full-functions.md) 与 [语言参考](../reference/tlosure-reference.md)。
