# P5 执行链退役审计

> 后续修订：[ADR 0049](../decisions/0049-retire-agent-session.md) 已删除 agent session 及旧历史读取；下文旧阶段验收记录保留为历史，不代表当前兼容能力。
> 后续清理：宿主查询已转为普通 Operation，旧写入 API、handler 重复投影及独立 effects 路径已删除。本文保留 P5 阶段结论，现行边界见 [退役代码清理](workflow-retirement-cleanup.md)。

基线 main `04e48da`（#212）；对应任务 [#206](https://github.com/rika-moe/TlonImpact/issues/206)。本文记录本阶段实际边界；旧记录操作手册见 [实施计划](programmable-workflows-plan.md#p5-实施记录与旧记录处置)。

## 调用图

删除前：

```text
MemberRuntime -> rules/when/actions -> ProgramDriver
  -> run-loop ActionRequest -> EffectExecutor -> SessionScheduler
    -> LoopDriver -> StageServices/StageOutcome -> stage implementations
    -> AgentSession / recipe-bound ContextBuilder / ContextPressure
    -> SessionJournal append/resume/dispatch/collect
v2 handlers -> ProgramDriver.run_entry -> OperationExecutor -> ProgramStore
```

删除后：

```text
MemberRuntime -> frozen module entry -> ProgramDriver.run_entry
  -> ordinary functions (tlon.agent, conversation, context, workflows)
  -> model-call / tool-execute / domain Operation
  -> OperationExecutor + ExecutionScope -> ProgramStore
SessionJournal -> read-only legacy SessionStore + ProgramStore projection
Arena -> read-only list_pending (no restoration)
```

程序完成、结果与调度意图同事务提交；只在根入口应用调度。嵌套函数、核验和局部 finish-turn 不停止成员。后台维护使用原退避、隔离和活动计数。

## 删除与保留

删除 LoopDriver、全部 stages、EffectExecutor、AgentSession、SessionScheduler、ContextPressure、budget_notice、旧 identity snapshot，及其专属测试。删除 SessionRecipe、stage/section 枚举与验证、ActionRequest、run-loop、条件作用域解释、旧 effects 执行桥、control 元数据与嵌套语法限制、旧自调度工具和声明 helper。quality gate 删除 stage-purity 项及专属脚本。

保留 provider/预算、Operation、领域服务、邮局幂等、通用消息与 PromptBuilder。SessionJournal 不含 append/resume/find_unfinished；历史 SQLite 不转成新程序。保留客户端连接 SessionManager、REST `/sessions`/SSE。少量宿主查询仍显式非纯并记录采样；没有新增通用 FFI。

8 套内置原型、Adam 和报纸示例采用完整 v2 模块。路由/转发直接执行邮件领域操作，无模型；其他工作方法通过普通函数计划、读上下文、执行工具及投递前核验。自动回信去重只认完整地址对应的实际成功回执，覆盖本地/外部网关、CC、同名不同域及投递失败。

播种只向不存在的目标目录原子发布完整包，已有目录不补文件或改源码。模板参数覆盖模块源码，渲染后删除声明。wheel 检查全部 8 套模块及标准库。

## 遗留证据边界

loader 在准入和初始化前以 SQLite mode=ro 扫描旧 session 与非入口 program pending。旧未完成身份、未知格式和读取失败均报告路径并阻塞，不执行、不改状态、不生成迁移。只读测试验证原文件字节保持和旧历史可读。

本机盘点 32 个历史目录：可读库未发现旧 pending，1 个库不可读，故没有宣称全量排空或自动操作真实成员。部署时需再次盘点；不能删库或改根身份绕过 unknown。

## 调用方复核与验证

对 `src/tlon`、`src/tlosure` 和 `scripts` 的 Python 源码检索 `SessionRecipe|STAGE_NAMES|SECTION_NAMES|LoopDriver|StageServices|StageOutcome|AgentSession|SessionScheduler|ActionRequest|run-loop`，删除后的生产调用为零。测试中只允许明确旧格式拒绝与接口不存在断言；历史 ADR 保留并注明替代状态。

真实冒烟使用隔离 TLON_HOME、真实 dream CLI、SDK 底层 submit_mail 与已配置 DeepSeek：

- 两模块组合工具、消息压缩、短回合及核验：6 次模型调用、1 次 time-now、预留 768 输出、一封回信。
- 包内标准 L0 原型：真实 member-context、模型选择 time-now、投递前核验；3 次模型调用、预留 1536 输出、一封回信。
- 三个模块交互与自动蒸馏：4 次模型调用（含蒸馏）、3 个稳定来源、2 条来源可追踪 claims；读失败后重建、重复读取/消费不新增调用或扣费。旧 sessions 数据库未创建。

初次包内原型冒烟因 model-context 使用 LiteLLM schema walker 不能处理布尔 schema 而超时；已统一为预算侧既有的工具 JSON 文本估算，并增加真实装配目录回归覆盖，重跑通过。所有冒烟服务和临时目录均清理；未操作真实成员生命周期。

全量质量门：`scripts/check_all.sh` 14/14 全绿；单元 2413 passed / 15 skipped，集成 40 passed。静态检查含 Ruff、mypy、import-linter、生成 reference、TS 测试/构建与完整 wheel 资源检查。

首次 CI 集成测试 39/40 通过；核验回信测试的 10 秒等待包含了初始社会并发启动及目标成员冷加载，日志显示仅目标唤醒已耗时 9.474 秒，三次模拟响应于等待结束后才完成。该测试验证完成与共享预算，不测启动性能，因此等待上限改为 60 秒，原调用次数、消息内容及预算断言完整保留。


## 合并前 review 修订

现行 README、语言参考、原型设计、模块指南和 API 发布点曾遗漏旧机制描述，本轮逐项纠正；历史 audit 明确标记为历史，生成器、索引和源码注释同步。API 不再把已删除文件列为事件发布者：模型/工具旧事件名仍在枚举中，当前无生产发布点。

补充 operations 层 38 项超时准入测试：拒绝非法 default_timeout/timeout_grace、超时来源优先级、加 grace 后受父/根 deadline 限制，以及内存/SQLite 已提交成功优先、回放不重复执行。原有 dispatch 前拒绝、dispatch 后 unknown 与历史回执优先测试保留；组合 75 项通过，没有放弃活代码契约。

行为确认和升级操作见 [P5 发布说明](../releases/p5.md)：round-limit 改明确失败而非静默标读；计划上限为 10 轮且共享资源约束不变；旧内置目录人工备份并移出后才重新播种，不通配删除自定义包；arena 从新程序已提交的真实 fuel 错误生成红旗，历史归档仍可读。删除无人调用的 ToolSpill 及其 5 项专属测试，两种局部 finish-turn 选择的边界已写明。

本轮全量质量门 14/14：单元 2450 passed / 15 skipped，集成 40 passed。标准原型真实 DeepSeek 冒烟再次通过（3 次模型、一次 time-now、一封回信）；新增真实 round-limit 冒烟确认一次模型和 time-now 后明确失败、一封宿主通知、原邮件未读。冒烟脚本首轮抢在 runs 表初始化前读取，调整为只读等待建表后重跑通过；未改变生产建表机制。服务、隔离目录和端口均清理。
