# 统一宿主操作

当前设计见 [ADR 0046](../decisions/0046-programmable-workflows.md) 与 [普通工作程序](programmable-workflows.md)。Capability、复合 Operation、旧规则与 stage 引擎已删除，不提供兼容入口或迁移。

Operation 是宿主操作的单一契约：名称、描述、输入输出 schema、效果和 Python 实现。DSLAdapter 与 OperationTool 分别提供语言和模型入口，共享 OperationExecutor，不重复维护业务实现。普通模块函数负责组合，不注册成 Tool，也不要求宿主理解工作流程的阶段名称。

操作在宿主构造的成员上下文中执行，校验输入输出并遵守权限、预算、截止、取消与成员写 gate。模型、邮件、文件、注册表读取和状态等外部效果由宿主执行；纯计算不发起外部读取或写入。

Behavior 声明事件到模块入口的映射与执行级别，Prototype 携带 Behavior 和模块源码以创建 Agent。Agent 绑定私有服务、人格、模型与状态。入口返回值携带可选调度请求；不存在 run-loop 会话配方解释器。

member-sleeping、member-running、member-lazy 与 member-roster 是普通只读 Operation，具名输入与其他操作一致，采样结果进入 invocation 回执。旧宿主 builtin 和独立 effects 执行路径不保留，具体边界见 [退役代码清理](workflow-retirement-cleanup.md)。

恢复使用冻结程序包与输入、宿主构建身份及已记录的操作回执；外部结果未知时不自动重做非幂等写入。详情见 [调用恢复](invocation-recovery.md)、[执行预算](execution-scope.md) 与 [操作参考](../reference/operations-reference.md)。

[实施计划](operations-plan.md) 和 [审计记录](operations-audit.md) 保留历史证据，不代表已删除机制仍有效。
