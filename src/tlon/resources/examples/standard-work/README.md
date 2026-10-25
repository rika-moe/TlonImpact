# 普通函数组合的标准工作方式

这个 v2 两模块包先调用 plan-then-act，再显式调用作者自己的 review 与 revise 函数；两个方法分别返回给调用方。tlon.workflows/route 还可选择 short-turn、plan-then-act或 evidence-check。evidence-check 沿已有完整消息继续检索反面证据，再追加比较请求，实际任务需要在 tools 提供相应检索工具。每个行动循环都提供 time-now 和 finish-turn；模型必须单独成功调用 finish-turn 才能返回。内部规划、自评、修订可以返回文本，截断输出明确失败。外层将修订文本交给最后一个行动循环，由模型调用 mail-send，再单独调用 finish-turn，成功结束后标读原邮件。原信转发工具 forward(recipient, mail-id) 只接受真实邮件 ID，不能用于发送正文。

上下文 assemble 组合 prefix/history/task。conversation/run 经 model-context 检查窗口和预算，fit 只核查资源，不会压缩或改写此前消息。标准策略由 Lisp 为边界交接预留窗口与预算各十分之一，超限明确失败。显式 handoff 沿完整历史追加摘要请求与响应，保留原文，并返回供下一 run 使用的带来源摘要。省略单次输出上限时按模型能力和可用资源决定；program_token_budget 是整个工作程序的独立总预算，包含交接消耗。

领域拒绝作为模型可修正数据返回；unknown、预算、取消、截止和基础设施错误传播给运行器，不自动重发未知效果。P5 已将内置原型全面切换为普通模块程序；本示例展示可替换的组合方式。
