# 统一宿主操作

当前决定见 [ADR 0045](../decisions/0045-remove-capabilities.md)：Capability、复合 Operation、提案审批与目录加载已删除，不提供兼容入口或迁移。本次未引入 module，也未改变现有会话配方。

Operation 是宿主操作的单一契约：名称、描述、输入输出 schema、效果、控制标记与 Python 实现。DSLAdapter 与 OperationTool 分别提供语言和模型入口，共享 OperationExecutor，不重复维护业务实现。

操作在宿主构造的成员上下文中执行，校验输入输出并遵守权限、预算、截止、取消与成员写 gate。模型、邮件、文件和状态等外部效果由宿主执行；纯条件仅允许无效果且非控制的操作。

Behavior 编排事件规则与会话，Prototype 渲染并复制 Behavior 以创建 Agent。Agent 绑定私有服务、人格、模型与状态。run-loop 继续作为会话边界，旧宿主查询仍按既有桥接机制执行。

恢复使用冻结的程序与输入、宿主构建身份及已记录的操作回执；外部结果未知时不自动重做非幂等写入。详情见 [调用恢复](invocation-recovery.md)、[执行预算](execution-scope.md) 与 [操作参考](../reference/operations-reference.md)。

[实施计划](operations-plan.md) 和 [审计记录](operations-audit.md) 保留历史证据，不代表已删除机制仍有效。
