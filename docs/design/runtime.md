# 成员 Runtime

当前工作程序运行边界由 [ADR 0046](../decisions/0046-programmable-workflows.md) 与 [目标设计](programmable-workflows.md) 定义；P5 已移除旧 stage 执行链。

## 装配与执行

注册表负责成员加载、四态资格、邮局和生命周期。MemberServices 装配模型、任务、工具、沙箱及领域服务。MemberRuntime 订阅事件并串行受理对应模块入口，驱动 ProgramDriver；普通函数控制自身的模型与工具调用。

受理事务冻结程序源码闭包、事件输入、资源约束和宿主模型上下文。ProgramStore 保存运行身份、逐调用 intent/回执、模型预留与结算、完成结果及待应用调度；程序完成和调度意图同事务提交。恢复只执行冻结程序，不读取当前行为替换旧入口。

只有入口结果驱动下一次唤醒、等待或死亡。嵌套函数和 `finish-turn` 返回只结束局部工作。排空等待程序及已派发工具完成；长期笔记读写属于显式文件操作，没有后台蒸馏或反思任务。

## 模型与工具

model-call 经 ModelService 使用宿主绑定身份、系统输入、成员模型与合法工具目录，只产生数据。tool-execute 对每个叶调用分配稳定身份；写入按声明顺序通过可重入成员 gate，允许独立读取并发。取消、截止、预算、构建失配与 unknown 不转成模型可以任意重试的业务结果。

邮件、任务、文件和生命周期各由领域服务维护自己的语义；长期笔记复用普通文件操作。邮件成功以真实回执为证；自然语言和未发送草稿不能代替投递事实。程序可显式组合投递前审阅；标准行动循环只通过工具发送邮件，发送后继续行动，直到独立的 finish-turn 成功。纯文本和空输出不能结束行动循环，也不会自动投信；截断明确失败且不自动标读。内部规划、显式审阅与压缩允许文本结果，不默认增加自评调用。

## 上下文与历史

ContextBuilder 只提供成员资料读取及人格缓存，不解释工作流程。member-context 返回固定字段；标准模块自行组合、裁剪或压缩 messages。所有模型请求及压缩共用根预算。长期笔记不由 ContextBuilder 或 member-context 自动读取，也不注入 system/user 消息；成员可以通过 file-list/search/read/write/edit 按需维护，或完全不读写，见 [记忆设计](memory.md)。

RunJournal 仅从 ProgramStore 提供原始 run_id 的只读查询、摘要与导出；事件为 run/start、run/end，来源字段为 source_run_ids。run-read 只读回溯，不恢复执行。Agent session 及旧 SQLite 历史查询已删除；客户端连接 SessionManager、REST `/sessions` 和 SSE 仍由 serving 层管理。

## 旧记录处置

不读取旧 agent session 数据库，不迁移历史或记忆引用，已有归档不被运行时修改。启动前仍只读检查非入口 program 的未完成记录。发现旧 pending、未知数据库格式或读取失败，成员阻塞并显示路径与身份；不重放、不迁移、不标记成功、不清库。ProgramStore 中的 run 历史可读取。操作员保留证据、确认原外部效果后选择处置；完整说明见 [P5 实施计划](programmable-workflows-plan.md)。

按 [ADR 0049](../decisions/0049-retire-agent-session.md)，程序总预算及派生额度归 run；is_busy 覆盖程序及其外部效果，排空不以虚构 session 数量判断。
