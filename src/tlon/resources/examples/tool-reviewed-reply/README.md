# 可组合工具核验回信

`work.writing/generate` 调用普通 `tlon.conversation/run`：模型先调用 `time-now`，看到对应工具结果后返回草稿。子对话返回后，外层仍执行核验、修订和发送邮件。这两个作者模块可以被其他普通函数调用，不注册为 Tool。

`max_rounds` 限制子对话的模型请求次数，所有请求、工具及外层函数共享入口的 fuel、预算、取消与写 gate。对话返回 `content/messages/reason`；`reason` 是 `complete`、`finish-turn` 或 `round-limit`。`finish-turn` 仅在显式提供该工具名且模型单独选择它时局部返回；混合控制请求拒绝，绝不调用宿主调度控制。

工具错误仍追加一条与原 ID 配对的 tool 消息；基础设施中断则由宿主阻塞，不交给模型重试。示例中的核验只是模型审阅，不代表事实已得到独立证明。
