# 普通函数组合的标准工作方式

这个 v2 两模块包先调用 plan-then-act，再调用 reviewed；两个方法分别返回给调用方。tlon.workflows/route 还可选择 short-turn、plan-then-act、evidence-check 或 reviewed。evidence-check 分别检索后比较两份证据报告，实际任务需要在 tools 提供相应检索工具。原信转发用 forward(recipient, mail-id)，实际入口仍在方法返回后显式发信。

上下文 assemble 组合 prefix/history/task。conversation/run 经宿主 model-context 检查窗口，压力下调用普通 compact 函数。压缩保留精确 prefix_count（默认 cache_prefix_len）和最近 recent_groups 个完整交换，逐组结合前摘要生成新摘要；每次先检查窗口，单组仍无法容纳则明确拒绝。切断工具交换的前缀、孤立结果和不完整交换拒绝。摘要区分未执行草稿/建议与已执行结果，附历史摘要标签，不冒充新回执。摘要共享原根模型预算和恢复权威，可单独设置 summary_tokens，压缩不足时明确失败。

领域拒绝作为模型可修正数据返回；unknown、预算、取消、截止和基础设施错误传播给运行器，不自动重发未知效果。P5 已将内置原型全面切换为普通模块程序；本示例展示可替换的组合方式。
