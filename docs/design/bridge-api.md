# 宿主接口与普通程序

当前边界见 [ADR 0046](../decisions/0046-programmable-workflows.md)、[DSL](dsl.md) 和 [Operation reference](../reference/operations-reference.md)。Bridge 不提供通用 Python FFI，也没有 stage 配方解释器。

## 普通调用

DSLAdapter 把 Operation 投影为可暂停的 builtin。ProgramDriver 在调用处执行宿主调用，记录回执，将实际值交回表达式。模型调用只返回数据，工具执行通过 tool-execute 逐叶分派；模块函数不注册成 Tool。

ProgramStore 记录逐调用身份、契约、冻结依赖及结果。state-put 与其回执原子提交；邮件返回真实邮局回执；不确定效果阻塞。嵌套函数共享根预算、取消和写 gate。

## 数据与权限

入口接收物化事件数据，域查询通过显式只读操作采样。`member-context` 返回固定结构资料；工作程序决定怎么构造上下文。宿主冻结人格/协议、模型与工具定义，程序不能通过业务输入改写成员身份或扩大授权。

Adam 的少量注册表查询仍由 ScriptHostQueries 注入，明确为非纯且按调用记录读取值；它们不是确定性常量。社会策略保持独立纯求值，输入在宿主调用点物化，不执行工作流程或外部读取。

## 已退役

ActionRequest、RuleEnv/LoopEnv/StageCondEnv、run-loop、effects collector 和 stage 条件作用域已删除。v1 行为拒绝，无兼容层。新执行和恢复只有模块程序路径；SessionJournal 保留只读历史，不写入或恢复运行。

reference 中事件、工具、配置与词汇清单由 `scripts/gen_reference_docs.py` 生成；静态校验不保证运行时无类型错误或预算耗尽，错误按运行边界持久化和上浮。
