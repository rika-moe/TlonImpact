# P5 执行链退役审计

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
