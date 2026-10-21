# ADR 0045：删除 Capability

状态：已决定，2026-10-21。

删除 Capability 的定义、提案、审批、目录加载、管理 API、WebUI 及专属测试；不保留兼容入口或迁移。保留宿主 Operation、Tool 投影与现有 Behavior 执行。本次不引入 module，不改写会话配方，也不实施此前讨论的其他架构调整。

Capability 所承载的复合操作发布与执行机制一并删除，避免留下不可达的第二套程序类型。程序快照与宿主操作恢复仍保留；删除的 Capability 不再参与新程序装配。

本决策覆盖 ADR 0044 及相关设计文档中 Capability/复合 Operation 的当前有效性声明；历史实施记录保留为历史。
