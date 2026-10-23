# 可编程工作流程：分阶段实施计划

状态：2026-10-23，设计已接受，P0 为本次文档与 issue 建立；P1 已合并（#208），P2 本阶段实现待 PR 审核合并，P3–P5 未实施。设计基线 `9fe00817bd75944e49a05cfd8e0149fa240dc757`（#198）；P1 施工基线 `0650c9d`（#207），P2 以已合并 #208 为基线。设计权威：[目标设计](programmable-workflows.md)；选择理由：[ADR 0046](../decisions/0046-programmable-workflows.md)。当前能力以各阶段实施记录及 reference 为准。

## 施工与共同完成标准

两个 EPIC 严格串行：A 建立可恢复的普通工作程序（P1–P3）；B 替换标准会话并退役 stage（P4a、P4b、P5）。每项一个 PR；若切片过大，先调整 issue 与本文，不先堆半套平台。默认等待人类合并；本轮文档通过独立分支和 PR 发布，不自动开始代码施工。

每阶段必须：主会话复核 diff 和调用方；运行 `scripts/check_all.sh` 全量且全绿；给出场景/故障注入/实际结果；同步当前设计与生成 reference；隔离 TLON_HOME 起真实 dream，经 SDK 发信并核对持久收件箱，清理临时数据、进程与端口。质量检查项只定义在脚本中。模型阶段区分确定性 fake/provider 故障测试与真实模型冒烟；没有凭据或未实际调用不声称真实模型通过。已知 SDK 高层旧身份字段问题另案处理，使用底层 submit_mail 的验证必须明确标注。

阶段只删本阶段被替代的机制与对应专属测试，不删无关失败来凑绿。无兼容入口、迁移、失败 fallback 或用户历史清库；建设期根据入口格式明确选择执行器，最终一次删除旧路径。

## P0 — 决策和跟踪（本次）

范围：ADR 0046、目标设计、本文、文档索引及相关现行文档的目标链接；两个 EPIC 与阶段子任务。删除项：无代码删除。验收：阶段依赖、变更/保留/删除、故障验收与非目标完整；链接可解析；issue 号回填，全部代码阶段保持未勾选。

## EPIC A：可恢复的普通工作程序

### P1 — 模块、入口、冻结程序包与最小发信链路

依赖：P0；获得阶段施工授权。

范围：`src/tlosure/{edn,eval,validate}.py` 及新的模块语义；`agents/{member_spec,loader,program_driver,program_store,member_runtime,scheduler}.py`；`agents/prototypes/{management,staging,instantiate}.py`，必要的 api/prototypes 与 server 管理接线。新增模块包只实现最小源码映射，不动态加载或做包管理器。

实施：命名空间、导入导出/私有绑定、模块初始化无效果；Behavior 导出入口；人工完整程序包提案、校验、批准和实例化；首次受理事务冻结源码闭包/输入/约束；ProgramDriver 返回值；完成/结果/调度意图原子提交与待应用恢复。模块函数共享求值及运行预算。新格式选新执行器，现有配方继续工作。

删除项：新路径不接入 run-loop、SessionRecipe、StageOutcome 或 Capability 注册表；不删仍服务旧原型的 stage 引擎。本阶段不实现机器自动起草模块，不能宣称完整 Agent 自编写闭环已完成。

验收：

- 两个模块共同完成真实事件→调用辅助函数→发信；可嵌套/传递函数；私有导出、同名冲突、缺依赖/依赖环、路径穿越与加载期效果明确拒绝。
- 修改/删除磁盘模块不影响既有运行重放；新实例只使用完整获批内容，模块初始化不能调用宿主 Operation；宿主包/元数据读取不触发用户代码或外部业务效果。
- 发信提交后中断、返回前中断，恢复不重复投递；源码/依赖与输入同事务冻结。
- 完成提交后、调度应用前中断可恢复；重复应用幂等，子函数返回调度形状不影响 Agent；根完成结果缺字段/非法调度不冒充成功。
- 当前 boot/邮件/idle 与四态/死亡约束保留；新旧路径不会相互接管或失败回退。

### P2 — 完整模型 Operation 与函数式核验

依赖：P1。

范围：`operations/model.py`、`base/{llm_client,execution}.py`、`agents/{program_store,stages/services,prompt_builder}.py`；程序运行输入/系统上下文快照；原有模型调用方与测试。

实施：单份 model-call messages/tools/结构化响应契约；宿主绑定系统输入与成员模型；标准库纯消息构造函数及生成/核验/修订最小程序；复用 ExecutionScope.model_call 与现有预留/结算，不另建模型日志。旧 stage 的模型请求也转用统一服务，再删除该域重复实现。

删除项：替换旧 prompt-only model-call 契约及其示例/专属测试，不保留别名；删除 StageServices 中已转移的独立 provider/请求计费路径，stage 本身暂留。不得把输出预留计费悄悄改为按实际 usage 计费。

验收：

- 普通函数生成→核验→修订得到明确结果；空工具请求与带工具请求使用相同边界，模型调用不自动执行工具。
- 伪造工具 schema/身份不能提权；系统输入与工具定义变化不改写旧有效请求；成员配置隔离。
- 模型响应/额度已提交、外层 Operation 回执未提交时崩溃，恢复不再请求、不重复收费；核验与压缩不得重置根预算。
- 明确拒绝、上下文超限、额度耗尽、取消、超时未知分别测试；unknown 不被普通失败包装后自动重试。保留 output/total 现行计费语义并给出证据。

### P3 — 受控工具分派与可复用对话循环

依赖：P2。这是 EPIC A 完成判据。

范围：`agents/tool_executor.py`、`operations/{core,adapters,invocation}.py` 及新的宿主分派端口；`agents/program_driver.py`；标准消息/对话函数；相关 program 与 integration 测试。

实施：工具名白名单取请求范围与当前成员目录交集，逐叶调用身份/回执；纯会话消息构造与响应/工具结果关联；模型局部完成信号返回给对话调用方，外层继续决策；错误按领域拒绝与基础设施阻塞分类。保持宿主写 gate、声明顺序与既有并发约束。

删除项：移除新路径对 ToolExecutor“所有异常转普通失败”的依赖并修正共享边界；不新增批量非幂等父 intent；不把模块函数注册成 Tool。旧 LoopDriver 的 dispatch/collect 待旧消费者退出再删，不提前破坏可运行版本。

验收：

- 两模块工作程序完成“模型选择工具→执行→核验→修订→发送”，可被另一个函数调用；子循环结束不结束整个事件入口。
- 多工具部分完成后中断，完成项只返回旧回执，未完成项按状态处理；相同参数/路径稳定，完成顺序不影响子身份或输出顺序。
- 取消/截止/预算/恢复失配不会变成模型可自行重试错误；不确定邮件/模型结果阻塞，不重复写入；非法工具名、重复 ID、越权及混合控制请求拒绝。
- 一条完整 assistant tool_calls 响应与对应结果合法配对；失败消息不会使下一次模型请求缺失必需结果。
- 嵌套函数及批量工具共享根预算/取消/写 gate，不另建隐式会话或重置 fuel。

## EPIC B：标准工作程序与 stage 退役

### P4a — Agent 编写模块与标准工作方式

依赖：P3。

范围：`evolution/{write_prototype,propose_behavior}.py`、`agents/prototypes/*`、`api/prototypes.py`、server 原型管理与 WebUI 原型审阅；`agents/{context_builder,context_pressure,prompt_builder}.py`；标准程序库与内置原型数据。

实施：机器提案输出完整 Behavior+模块包及依赖，审批展示全部源文件差异；复用 P1 的包验证/发布。将计划、核验、修订、消息上下文、压缩、错误策略改写为普通函数；宿主保留系统约束、窗口校验和 provider 协议。保留短回合、计划后执行、证据核验、路由/转发等已存在行为差异。

删除项：对已切换新包，删除 stage/section 专属配置依赖与只为其服务的提示词；不保留新库内部对 scratch/StageOutcome/AgentSession 的桥接。旧包仍用的引擎留至 P5。Capabilities 不恢复。

验收：

- Agent 从任务/经验起草至少两个可复用模块，经 staging→完整依赖审阅→批准→创建新成员真实运行，证明不是仅人工创建模块。
- 审批后改模块不得影响批准内容、新实例或旧运行；依赖差异、错误路径与缺源码拒绝；实例化完整复制包。
- 现有主要行为以普通函数重现，新增“检索后交叉核验”等方法无需 Python stage；嵌套两段工作方法分别返回。
- 压缩前后历史与未执行草稿/已提交工具事实清楚；压缩模型结果可恢复，额度共享、前缀缓存组织不无故退化。
- 程序错误/资源终止保留责任邮件与人类失败通知、草稿证据及调度约束；无模型真实调用则明确验收局限。

### P4b — 单一恢复权威与交互消费者

依赖：P4a。

范围：`agents/{session_journal,session_store,scheduler,member_runtime,reflection,lessons,retirement}.py`、`operations/memory.py`、`memory/{manager,session_projection}.py`（以实际引用为准）、`context_builder.py`、`evolution/propose_behavior.py` 及真实消费的 observability/API。客户端连接 `/sessions` 与 WebUI SessionsView 不属于 AgentSession，不迁移。

实施：新运行交互消息、结束状态和完成索引来自 ProgramStore 权威事实；SessionJournal 作为读取/投影接口，支持原历史读取；投影可追赶重建。迁移 digest、thread recap、反思、蒸馏、教训、行为提案与退休归档消费者；完成计数、水位与维护任务按稳定来源身份去重。不给新运行建设第二套 session resume。

删除项：新运行对 SessionJournal.resume/find_unfinished 与 stage 执行字段的恢复依赖；已退出消费者的重复完成计数/额度来源。旧历史读取及尚在排空的旧恢复保留到 P5。

验收：

- 权威记录提交后投影失败、追赶/重复消费、全量重建均不重新调用模型/工具；不重复计完成次数或触发重复维护任务。
- 新运行可被蒸馏、反思、memory-read、thread recap、教训、提案与退休归档消费；来源引用可追溯；混合旧/新来源、较早开始但投影延迟的运行均无蒸馏水位遗漏。
- 原交互历史可读，不转换/清库；旧/新来源查询边界明确，同一运行不被两次恢复。
- 客户端连接会话、REST/SSE 不受替换影响；不借本阶段新增对话轨迹 UI。

### P5 — 切换内置程序并删除旧引擎

依赖：P4b；旧运行排空与遗留处置清单得到确认。默认等人类合并，不自动操作真实成员生命周期。

范围：所有旧执行链调用者、内置原型、播种/打包、文档/生成 reference 及专属测试。实施前以 rg 与调用图复核清单，不能只删除五个 stage 文件。

删除项：

- `agents/loop_driver.py`、`stages/*` 中阶段实现/StageOutcome/StageServices；通用服务先归位再删。
- SessionRecipe、STAGE_NAMES/SECTION_NAMES 专属解释与校验、run-loop builtin/ActionRequest 及 `_run_session_effect` 桥接。
- AgentSession 的 iteration/scratch/pending tasks 等执行状态；SessionScheduler 的 stage drive、journal resume、dispatch/collect 与双重完成桥。
- `operations/control.py` 中 sole/direct/literal 形式限制，以及提示词中要求模型直接结束 Agent 的旧控制协议；保留入口结果校验和宿主调度实现。
- ContextBuilder/ContextPressure 对 recipe 的配置绑定；已由普通程序替代的 stage/section 配置、原型内容与专属单元/集成测试。
- 不删除客户端 SessionManager、REST `/sessions`/SSE，历史 SessionStore 读取、通用 provider、预算/Operation/领域服务。

验收：

- 全部内置原型使用模块入口和普通程序；转发/路由等无需模型的程序仍能执行；干净安装与 wheel 包含所需模块/标准库。
- 旧未完成工作先清单化并排空；不可排空者明确阻塞并保留证据，不自动成功、不重放旧 stage 日志；重启可报告遗留状态，历史可读。
- 旧格式/词汇明确拒绝，不自动转换；rg 旧符号仅允许历史文档和明确拒绝测试；运行代码只剩一套程序执行与恢复路径。
- 端到端真实发信、完整模型/工具循环、嵌套核验、崩溃恢复、预算与四态全量质量门通过；附删除前后调用图与残留审计。

## GitHub 跟踪与状态

issue 用于进度、PR 与证据，目标契约仍以本设计为准。阶段必须串行；#173 的长期习惯/自主热更新不在本轮关闭范围。设计文档随独立 PR 发布，issue 评论回填文档 PR 与可访问源码链接。

<!-- WORKFLOW-ISSUES -->
- [x] P0：ADR、目标设计、计划及 issue 已建立；发布与合并状态见文档 PR。
- [ ] [EPIC A：可编程工作流程——模块、模型与工具的统一持久执行](https://github.com/rika-moe/TlonImpact/issues/199)
- [x] [P1 — 模块、入口、冻结程序包与最小发信链路](https://github.com/rika-moe/TlonImpact/issues/201)
- [x] [P2 — 完整模型 Operation 与函数式核验](https://github.com/rika-moe/TlonImpact/issues/202)
- [ ] [P3 — 受控工具分派与可复用对话循环](https://github.com/rika-moe/TlonImpact/issues/203)
- [ ] [EPIC B：可编程工作流程——标准程序、交互投影与 stage 退役](https://github.com/rika-moe/TlonImpact/issues/200)
- [ ] [P4a — Agent 编写模块与标准工作方式](https://github.com/rika-moe/TlonImpact/issues/204)
- [ ] [P4b — 单一恢复权威与交互消费者](https://github.com/rika-moe/TlonImpact/issues/205)
- [ ] [P5 — 切换内置程序并删除旧引擎](https://github.com/rika-moe/TlonImpact/issues/206)
<!-- END WORKFLOW-ISSUES -->

## P1 实施细化

P1（#208）实现独立 v2 handlers、tlosure 源码模块、人工完整包发布/审批/实例化及模块运行冻结和调度恢复。v1 仍服务旧原型，不进行格式迁移或执行 fallback。P1 不提供机器自动起草、完整对话循环或新交互投影。

v2 源码包采用不可变发布：staging/trusted 源码 PATCH 均拒绝，改源码创建带 parent 的新包；metadata 可编辑。这样复用目录原子发布，避免原位替换的崩溃缺席窗口，也不会把元数据修改变为重新批准。创建人工 staging 是新增管理入口；模块审阅在 API 和 WebUI 只读展示，现有人工 v1 编辑保持。

ProgramStore 新增 entry_programs 表，不改旧 runs 列、不迁移/清库；输入和源码同事务，完成和调度意图同事务。调度库另存 run_id 接纳回执，与调度意图同事务提交，再由程序库确认已交付；跨库恢复靠幂等交付，不依赖分布式事务。

首次全量验证在调度接口收敛过程中命中了新测试的旧 mock；改为实际新接口并增加确定性恢复等待后重新执行全量。最终命令与数量见 #208；该 PR 已合并，P1 已勾选。

## P2 实施细化

本阶段统一 messages/tools 名称列表模型契约及结构化响应；StageServices 复用 ModelService，不删除仍服务 v1 的 stage。增加纯 tlon.messages 和两模块生成/核验/修订示例；工具分派和对话循环仍属 P3，机器自动起草仍属 P4a，交互投影仍属 P4b。

v2 在入口受理事务冻结 host/config，标准库源码和作者源码一起冻结，作者包不被标准库污染。模型内层响应与预算已提交、外层回执缺失时，从现有 ProgramStore 模型调用记录补回执；不新增账本，不重发 unknown。旧 prompt-only 契约、示例调用及对应专属契约测试已替换，无兼容字段。output/total 计量语义保持。

确定性验收覆盖普通函数三请求共享根预算、空工具与带工具响应、宿主输入冻结、拒绝/未知/超时/取消及回执提交窗口。全量质量门与真实 provider 冒烟的最终证据记录在本阶段 PR 和 #202；未合并前不勾选 P2。

P2 本地最终验证：`scripts/check_all.sh` 15/15 全绿，单元 2754 passed / 15 skipped，集成 37 passed。真实 dream + staging/approve + SDK submit_mail + DeepSeek 三次调用完成生成/核验/修订，同根 output 占用 384，SQLite 收件箱一封回信，运行成功结束。服务与隔离目录清理完成。初次冒烟脚本误等待不存在的调度意图，修正后重跑通过；沙箱限制 Node 管道的检查批次停止后，在具备本地通信权限的环境重新全量验证。

## P3 实施细化

新增 tool-execute 编排端口和普通 tlon.conversation/run；逐叶按程序位置和批次索引派生身份，整个批次参数参与身份核对，provider ID 仅作协议关联。端口本身不创建父 intent 或 effects；当前采用声明顺序逐叶 await，复用现有写 gate，不增加语言并发。历史回执先返回，新效果才检查当前工具目录；撤权或定义变化阻塞运行，不能作为未记录的普通失败继续请求模型。

纯 tlon.messages 将规范化工具建议转换为 provider 消息，一次追加完整 assistant 响应，再严格配对全部结果；保留 reasoning_content 和可空 content。conversation/run 要求正整数 max_rounds，返回 content/messages/reason；无工具建议、单独 finish-turn 或轮数上限仅结束子函数，外层仍可核验/修订/发信。finish-turn 不执行宿主控制操作，混合控制请求和非空参数拒绝。conversation 仅在作者显式 require 时链接，纯程序无需模型服务。

共享适配层删除兜底普通失败转换，只有 InputError/RejectedError 可反馈；明确文件/路径/命令非零等领域错误同步分类，设备丢失、IO、unknown、输出违约、预算、截止和恢复失配传播。提交回执失败使用真实业务输入保留 request_id，不将含批次元数据的身份快照误当业务参数。内置 stage/旧收集器保留至 P5，P4 的自动起草和交互投影尚未实现。

P3 最终本地验证：`scripts/check_all.sh` 15/15 全绿；单元 2825 passed / 15 skipped，集成 38 passed。真实 dream + SDK + DeepSeek 工具对话、核验、修订和回信通过：4 次模型调用、1 次 time-now、同根 output 占用 512、一封回信。服务和隔离目录已清理。P3 待人类合并后勾选。
