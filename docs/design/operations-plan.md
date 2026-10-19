# 统一操作：分阶段实施计划

状态：2026-10-19 完整方案已认可；用户已授权创建分支、提交设计 PR 并开工 EPIC A。全部阶段尚未实施，合并仍由人类确认。

设计权威：[operations](operations.md)；决策：[ADR 0044](../decisions/0044-unified-operations.md)。代码基线为 `d35c3a546a5e3dacef331b5d39d3e1cc7efd4e9a`。GitHub 跟踪链接见本文末尾；Issue/PR 负责验收证据，不复制一份持续漂移的设计。

## 1. 施工原则

- 两个 EPIC 严格串行：A 收敛宿主操作与执行；B 开放复合依赖和动态工具视图。
- 每个下面的任务项为一个独立 PR；P3 按域拆成三个任务。若实施前发现单任务过大，先在 Issue 改切片与依赖，不能在代码中留下未接通的半套运行时。
- P1 建最小契约，P2 立即跑通真实双入口发信；不先建设抽象平台。P1-P3 保持领域行为，P4 明确收紧并发写语义，P5 后新增组合能力。
- 操作逐域切换时，同一操作只有一个权威实现；某域切换后立即删除该域旧路径。不同域暂时处于不同施工阶段不构成回退层，不写“新失败再试旧”的分支。
- 保留现有服务与存储；不自动转换用户 artifact/数据库，不删除用户历史。切换格式时明确报不兼容及受影响对象，由人类决定处理旧运行；不为通过测试清空真实 ~/.tlon。
- 变更前先记录场景与现有回执，验收关注行为/故障/隔离，不用类字段或实现镜像测试充数。旧测试仅在其对应机制明确废止时删除；无关失败应报告原因。

## 2. 各 PR 共同完成标准

1. `scripts/check_all.sh` 无参全量通过（包含 ruff、mypy、import-linter、reference 新鲜度、TS、单元/集成及打包）；不在计划或 workflow 复制检查项清单作为第二权威。缺 TS 依赖的本地跳过不等于该项通过，CI 必须实跑。
2. 对任务特有的验收用例给出命令、真实结果与失败注入点；新增检查仍接入现有质量门，不单建旁路门禁。
3. 亲自审 diff，确认所有入口/调用者已切换、旧机制确实删除、领域约束未丢失；subagent 结果不代替主会话复核。
4. 起 dream，通过 SDK 发邮件并核对收件箱；用显式注入的隔离 TlonPaths，验证默认 8080 不被遗留占用，清理测试成员/邮件/后台进程，不伤真实数据。模型相关验收分可重复 fake/故障测试与真实模型冒烟，明确 provider 配置与实际 token 消耗；无凭据不声称真实模型验证通过。
5. 当前机制随代码进入 architecture/design/reference，未实施目标继续单列；更新 EPIC checklist 并附 PR 与证据。默认等待人类合并。

## 3. EPIC A：统一宿主操作与调用执行

### P1 — 最小操作契约、校验与成员作用域

依赖：方案评审完成并获代码施工授权。

主要位置：`tools/base.py::Tool`、`tools/base.py::ToolResult`、`agents/tool_executor.py::ToolExecutor`、`agents/assembly.py`、`pyproject.toml`/`uv.lock` 的现有依赖及 import-linter 契约。

实施：引入最小 `tlon.operations` 契约、单一目录、host handler 和两种薄入口；统一命名对象输入、JSON 边界与输入/输出 schema；将已锁定 jsonschema 提升为直接依赖；宿主注入成员执行上下文和 scoped 端口。只建 P2 必需结构，不实现动态工具策略、DAG 引擎或新存储。

保留：未切换工具正常工作，语言内置函数不改。删除：新契约中不得引入旧 Tool 浅校验副本；旧基类待 P3 全域切换后删除。

验收：

- 同一个纯操作通过 DSL/Tool 得到相同值及相同输入/输出拒绝；只定义一份 schema。
- bool/int、嵌套数组、空值、额外字段、Keyword 键碰撞、Symbol/Closure/NaN、输出不合约均有可重复测试。
- 模型提供的 member_id/run_id 不可覆盖宿主上下文；两成员资源隔离。
- import-linter 明确 operations 不反向依赖 agents/server，tlosure 不依赖 tlon。

### P2 — 邮件双入口最小端到端切换

依赖：P1。

主要位置：`tools/mail/*`、`agents/loop_driver.py::_execute_tool_and_mail`、`agents/executor.py::EffectExecutor.send_mail`、`agents/program_effects.py`、`agents/vocabulary.py`、`organization/mail/post_office.py`。

实施：以现有邮局为底座，统一发信/转发/读取/标读操作及 DSL、Tool 契约；转发、回复线程、endorsement、mentor cc 的规则只保留在邮件领域执行路径。稳定调用 ID 贯穿 request_id/程序记录/session 引用。仓库示例及调用者在本 PR 同步切换到确定的新操作名，不加旧名称别名。

删除：LoopDriver 对 sendEmail/forwardMail 的专属提交与超时分支，旧 Tool 的“请求构造成功即成功”路径、ProgramEffects 中第二份邮件组装；超时未知结果统一由宿主操作返回语义驱动。

验收：

- DSL 和 Tool 发信得到同等的真实投递回执、拒收原因；回复/转发/致谢/mentor cc 不退化。
- SDK→dream→成员处理→收件箱真实邮件链跑通；成员不能读/标读另一成员邮箱或伪造发件身份。
- 投递已提交但 journal 前崩溃、超时后用同 request_id 恢复，收件箱仅一份邮件；未确定结果保持 unknown，不声称无副作用。
- 邮件领域业务实现只有一份；程序与会话仍分别使用原生命周期和日志权威。

### P3a — 文件、Shell、时间操作切换

依赖：P2。

主要位置：`tools/filesystem/*`、`tools/shell/execute_bash.py`、`tools/system/current_time.py`、`sandbox/*`、`agents/assembly.py`。

实施：将各操作的 schema、handler、结果及资源生命周期接入共享调用边界。时间归外部读取，Shell 执行归写入且保留后台任务句柄/查询；读写路径与 L0/L1/L2 检查在宿主实际执行边界保留。

删除：本域旧 Tool 执行业务与重复参数定义；协议适配不持有后台任务的第二份状态。

验收：L0 无文件/Shell 执行，L1 文件可用但不能 Shell，L2 维持既有能力；路径逃逸拒绝；后台任务可查询且关闭/wake 重装不泄漏；取消/重启后无法确定的非幂等命令不自动重跑。

### P3b — 记忆、任务、人格、联系人与观测切换

依赖：P3a。

主要位置：`tools/memory/*`、`tools/tasks/*`、`tools/agents/contacts.py`、`tools/agents/update_persona.py`、`tools/observability/*`、`agents/assembly.py`。

实施：复用现有 manager/provider，逐项给出输出 schema 和 read/write 分类，保持身份绑定与隐私边界；私有结构化 state-get/state-put 纳入同一契约，保留 state-put 与结果原子提交。

删除：本域旧 Tool 业务实现和结果转换副本。

验收：两成员记忆/联系人/任务隔离；只有自身可改人格；观测只读；state-put 崩溃恢复既不漏结果也不重复修改；现有查询的分页/大小限制不因统一而丢失。

### P3c — 生命周期、演化、模型操作与控制边界切换

依赖：P3b。

主要位置：`tools/agents/*`、`tools/prototypes/*`、`agents/program_effects.py`、`agents/member_runtime.py` 的动作执行、`agents/stages/act.py`、`agents/assembly.py`。

实施：spawn/启停、模型调用、原型/能力提案评审进入共享调用；保留见习限制、偿付政策、近亲检查、人工审批和作者归属。finish/sleep/exit 保留独占控制语义；run-loop 只作为 Behavior 编排边界，禁止复合封装或暴露给模型。

删除：旧 Tool 基类的业务执行/手写 schema 校验及旧 ToolExecutor 直接执行业务职责；不保留旧操作名称解析。保留必要的 LLM 协议结果适配。

验收：见习成员不能绕复合封装 spawn/提案；模型调用按原成员扣费；调度控制混批/深层封装被拒、排空后四态转换正确；proposal 校验错误回传、staging 不产生半残 artifact、审批不等于执行授权。

### P4 — 调用记录、预算和恢复契约收敛

依赖：P3c。P2 已完成邮件恢复，本阶段覆盖所有宿主操作及一般根调用。

主要位置：`agents/program_driver.py`、`agents/program_store.py`、`agents/loop_driver.py`、`agents/session.py`、`agents/stages/services.py`、`agents/member_runtime.py`。

实施：统一根调用/父子路径、请求标识、结果与错误语义，冻结宿主实现身份（代码/依赖/契约构建指纹）；重用 ProgramStore 与 session journal，不建第三权威日志。read 采样重放，写入 intent/回执与 unknown 处理按宿主能力区分。共享预算账本和取消/截止时间。成员写操作按声明顺序串行，独立读取仍可并发；同一调用上下文可重入，禁止等待自身锁。MemberCapabilities 改名 MemberServices。

删除：ProgramEffects 的重复宿主业务分发、_HOST_QUERIES 的 pure 重写、Tool/DSL 不一致的超时/结果提交处理及旧容器名别名。只保留两类驱动生命周期与薄适配。

验收：

- dispatch/result/collect 任意边界崩溃可恢复；已提交效果不重复执行，宿主实现身份不符明确阻止恢复。
- 两个并发根调用完成顺序颠倒仍能按各自稳定 ID 定位，不共享 ordinal。
- 并发预算预留不超额，取消阻止后续效果，恢复不重置或二次扣预算。
- 状态/授权撤销与提交的检查有明确串行边界；写序列可重复验证，无锁重入死锁。
- 非幂等执行后记录前崩溃报告 unknown；不承诺任意操作 exactly-once。

EPIC A 完成标准：全部既有可调用宿主能力经一份权威实现服务两入口，现有领域语义除明确收紧的并发写顺序外保持；旧业务分发删除、全质量门及真实邮件冒烟通过。尚不宣称已支持复合依赖或动态视图。

## 4. EPIC B：可组合能力、纯语境与动态工具视图

### P5 — 复合操作 DAG、统一 artifact 与冻结依赖

依赖：EPIC A 全部合并。

主要位置：`agents/capabilities/spec.py`、`bridge.py`、`staging.py`、`catalog.py`、`agents/agent_registry.py::_member_builtins`、tlosure AST/validate 的宿主无关分析接口、对应管理 facade/API/UI。

实施：capability.tlon 定义复合 Operation，统一 input/output schema、单参数 impl；静态具名调用可引用其他复合操作；提取直接依赖、检测缺失/环、推导 read/write 闭包，保护控制操作边界。批准持久绑定传递依赖指纹，加载发现依赖变化拒载上层直至重新批准，运行存完整传递定义快照；同一复合操作可投影为 Tool。纯 examples 真执行，效果 examples 在显式 fake 沙盒验证，审批不运行真实写入。

删除：旧 :args 位置参数 schema、作者 pure 声明作为权威、能力仅 DSL 的独立注册执行体系、v1 禁止能力依赖；更新管理端输入与错误展示，无旧格式读取 fallback。

验收：

- summarize-thread 被 send-thread-summary 引用；Behavior 与 LLM 调用同一个复合实现，无 Python 包装副本。
- 缺依赖、依赖环、碰撞、闭包内隐藏效果、动态不明操作目标、输出违约均明确拒绝或报错；坏定义只影响依赖闭包。
- 第一个子效果成功后、第二个效果中途崩溃，恢复不重发第一个；运行中修改/删除能力文件不改变冻结运行。
- 宿主实现身份变化拒绝恢复；已批准依赖被修改不能静默改变上层操作；嵌套预算不重置。
- 不能通过新 artifact 新增宿主原语、读取隐式 self/env 或包装控制操作。

### P6 — 所有纯条件使用统一函数准入

依赖：P5。

主要位置：`agents/vocabulary.py`、`agents/member_spec.py::validate_member_spec`、`agents/loop_driver.py::_until_reached/_stage_condition_met`、相关 trigger/loop/stage 条件与行为示例。

实施：when/until/stage-if 均可调用统一目录的纯操作；每处上下文作为明确数据传参。将依赖隐式 env 的领域谓词改为显式输入纯函数或在调用点用核心函数表达，外部查询先物化记录再进入条件。不改语言 map/filter/reduce，也不重建 stage/section 实现。

删除：TRIGGER/LOOP/STAGE_COND 的分裂纯词白名单与旧隐式谓词入口；更新所有仓库原型、测试及文档，不保留旧语义别名。

验收：同一个 has-actionable-facts 在三处得到一致结果；作用域数据缺失加载/执行明确报错；纯条件不能经局部 fn 或复合依赖读时钟、模型/邮箱或写状态；现有预算/终止条件和完成通知语义保持。

### P7 — 每成员、每请求的动态工具视图与执行约束

依赖：P6。

主要位置：`base/config.py::AgentConfig`、`agents/assembly.py`、`agents/member_spec.py::SessionRecipe`、`agents/stages/services.py::think`、`agents/loop_driver.py`、session journal、管理 facade 与对应 UI。

实施：宿主成员授权名单和现有资源/信任约束决定可执行能力；Behavior :tools 纯表达式选择候选。每次模型请求记录视图/schema/定义指纹/上下文版本；所有工具描述和 prompt 工具列表来自该同一视图。执行核对根调用属于请求快照，并在每个叶子效果前检查当前权限/状态。提供宿主维护的只读/允许写授权阶段约束，与可由 state-put 更新的业务 phase 分开，不新增通用策略语言或审批平台。

删除：全量注册即全量暴露、靠 unregister/register 修改共享目录模拟阶段的路径；无配置默认全开放；混用 scheduler 与业务 phase。

验收：

- A 只读、B 可写；相同 Behavior 在二者生成不同工具视图；A 无法直接或复合间接发送。
- 同一成员 reading→drafting→sending 改变下一次请求视图；同一冻结请求/相邻请求的视图互不污染。遵守现有单成员 session 约束，不为验收新造多 session 架构。
- 模型返回未出现在该请求的操作名即拒绝；生成响应期间撤销权限/切回只读阶段，叶子发送被拒且不投递。
- 读取/复合依赖无需全部暴露给模型；时间先记录后参与选择，恢复不按当前时间改写旧视图。
- Behavior/模型不能改宿主授权或受控阶段；只读时宿主仍可通过受信事件切换授权阶段；业务 state-put 的 approved=true 不构成批准；不要求本阶段新建草稿审批系统。

### P8 — 原型依赖、公开契约与最终验收收尾

依赖：P7。

主要位置：`agents/prototypes/*`、包内默认原型、相关管理 facade/API/UI、`docs/architecture.md`、design/reference/guides、reference 生成器和 import-linter。

实施：原型声明所需操作但不授予权限，实例化校验依赖并复制完整 Behavior；完善操作/依赖/效果/被过滤工具原因的可观测展示；同步公共接口与文档；审计全仓旧入口、旧 schema、旧语义和 fallback 均删除。

验收：

- 一个原型创建 A/B，两者有独立 Behavior/人格/模型/状态；原型更新不改变已出生成员；公共操作不复制进每个 Behavior。
- 完整业务场景覆盖“复合依赖 + 两入口 + 多成员隔离 + 阶段视图 + 运行撤权 + 预算耗尽 + 重启恢复 + unknown”。fake 故障测试之外保留真实 dream/SDK/PostOffice 验收，具备模型配置时跑一次真实复合模型调用。
- 新手仅读 architecture、operations 和相应 reference 能解释运行链；旧文档“能力不可 LLM/IO”等欠账修正，历史 ADR 仅标明替代范围。
- 全量质量门与 CI 绿，真实冒烟痕迹清理、无残留服务占端口；验收记录如实区分通过、未测和阻塞。

EPIC B 完成标准：目标设计完成；工具只是入口，复合能力可继续组合，动态视图与执行授权分离；没有遗留第二套能力执行机制。

## 5. 总体验收矩阵

| 性质 | 最早验收 | 最终证据 |
|---|---|---|
| 一份契约、两个入口 | P1/P2 | P8 同一复合实现两入口运行 |
| 不丢邮件/不伪造成功 | P2 | 回执、unknown、故障恢复收件箱数量 |
| 资源与成员隔离 | P1/P3 | A/B 越权与装置测试 |
| 记录与预算共享 | P4 | 并发/取消/恢复注入证据 |
| 复合继续组合 | P5 | 两层依赖、环/缺失拒载、冻结源码 |
| 条件统一 | P6 | 同一纯操作三条件，效果逃逸拒绝 |
| 动态视图与撤权 | P7 | 请求视图日志与无投递断言 |
| 旧机制真正退出 | 各切换 PR | P8 全仓引用/调用点审计及文档一致性 |

## 6. GitHub 跟踪

- [总 EPIC #179](https://github.com/rika-moe/TlonImpact/issues/179)：完整设计文字与两阶段跟踪。
- [EPIC A #180](https://github.com/rika-moe/TlonImpact/issues/180)：P1、P2、P3a/P3b/P3c、P4，六个串行 PR。
- [EPIC B #181](https://github.com/rika-moe/TlonImpact/issues/181)：依赖 A，P5-P8，四个串行 PR。

当前全部未实施；issues 的未勾选项不是完成声明。关联的 [#173](https://github.com/rika-moe/TlonImpact/issues/173) 继续保留为长期工作方式/习惯学习方向，本计划不将其一并关闭。
