# 社会资格政策已退役

[ADR 0052](../decisions/0052-remove-reproduction-social-gates.md) 删除了原型近亲拒绝、血脉配额倾斜、偿付门槛、见习工具限制和自动导师抄送，并删除其专用政策求值系统。`policy.tlon` 不再读取；不保留 hook、默认政策、兼容入口或 fallback，也不迁移或删除既有用户文件。

现行约束见 [原型与资源预算](prototypes.md) 和 [统一成员模型](unified-member-model.md)。来源、谱系、背书及用量仍是事实与审计数据。Adam 的生命周期 Lisp 程序继续存在。

原政策设计仅作为历史记录保留于 [ADR 0022](../decisions/0022-society-policy-context.md)。
