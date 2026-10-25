# 普通函数组合的标准工作方式

这个 v2 两模块包先调用 plan-then-act，再调用 reviewed；两个方法分别返回给调用方。tlon.workflows/route 还可选择 short-turn、plan-then-act、evidence-check 或 reviewed。evidence-check 分别检索后比较两份证据报告，实际任务需要在 tools 提供相应检索工具。每个行动循环都提供 time-now 和 finish-turn；模型必须单独成功调用 finish-turn 才能返回。内部规划、自评、修订可以返回文本，截断输出明确失败。外层将修订文本交给最后一个行动循环，由模型调用 mail-send，再单独调用 finish-turn，成功结束后标读原邮件。原信转发工具 forward(recipient, mail-id) 只接受真实邮件 ID，不能用于发送正文。

上下文 assemble 组合 prefix/history/task。conversation/run 经宿主 model-context 检查窗口，压力下调用普通 compact 函数。压缩保留精确 prefix_count（默认 cache_prefix_len）和最近 recent_groups 个完整交换，逐组结合前摘要生成新摘要；每次先检查窗口，单组仍无法容纳则明确拒绝。切断工具交换的前缀、孤立结果和不完整交换拒绝。摘要区分未执行草稿/建议与已执行结果，附历史摘要标签，不冒充新回执。摘要共享原工作程序总预算和恢复权威，可单独设置 summary_tokens，压缩不足时明确失败。省略单次输出上限时，按模型输出能力、剩余上下文空间和 run 可用预算取最小值；program_token_budget 是独立的整个工作程序总预算。模型能力来自 LiteLLM 元数据，无法解析时明确失败，不猜测默认额度。

领域拒绝作为模型可修正数据返回；unknown、预算、取消、截止和基础设施错误传播给运行器，不自动重发未知效果。P5 已将内置原型全面切换为普通模块程序；本示例展示可替换的组合方式。
