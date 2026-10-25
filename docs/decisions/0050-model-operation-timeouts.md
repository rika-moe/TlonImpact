# ADR 0050：模型请求与普通工具分别采用超时策略

日期：2026-10-25
状态：已接受

问题：MemberRuntime 将 tool.per_timeout（默认 30 秒）作为所有 Operation 的缺省期限。model-call 未声明自己的期限，因此外层先于 LLM request_timeout（默认 300 秒）取消请求，并留下 unknown 模型结果。

决策：model-call 显式采用宿主成员模型的 request_timeout；普通工具继续使用 tool.per_timeout，已有操作专用超时及 schema 声明的 timeout 覆盖规则保持。复用 Operation.default_timeout 和 OperationExecutor，不按操作名称在 executor 内新增分支、不增加一层配置或兼容路径。

有效超时始终不得超过所属执行域的剩余期限。模型适配层的实际 provider 请求也受当前执行域约束；模型期限不得扩大父执行域，不得重置冻结根期限。递减的剩余时间仅用于实际 transport 请求，不写入持久请求身份，避免重放时身份随时间变化。模型请求无单独执行域时仍采用自身模型超时，既有无 scope 重试规则保留；有 scope 的未知请求不自动重试。

内部模型调用若处于普通工具或维护执行中，仍受该父执行域已经声明的期限约束，不能通过嵌套模型逃逸父操作期限。model-context 等纯历史/估算读取不是 provider 请求，保留普通操作策略。

恢复语义不变：派发前期限耗尽不请求 provider；派发后没有持久响应则保留 unknown，不当作未执行或自动重发；已提交的模型响应/回执优先恢复，即使当前期限已经耗尽。不改真实 ~/.tlon 记录，不清除现有 blocked run。

回归覆盖：模型可超过普通工具超时完成、普通工具仍按原限时终止、模型自身期限生效、较短父执行域优先、派发前拒绝以及已提交响应在过期执行域中的恢复。
