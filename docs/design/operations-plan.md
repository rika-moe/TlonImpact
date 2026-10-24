# 统一操作：分阶段实施计划

> 后续修订：[ADR 0047](../decisions/0047-workspace-contact-files.md) 将联系人改为 workspace 普通文件，删除专用工具与正文注入；所有等级的 workspace 文件操作无需 Docker。下文阶段记录保留历史。
> Capability 及复合 Operation 已由 [ADR 0045](../decisions/0045-remove-capabilities.md) 删除。本文保留历史实施记录，不再描述现行能力；宿主 Operation、Tool 与 Behavior 继续保留。

状态：2026-10-21，EPIC A #184–#192 与 EPIC B P5 #193、P6 #194 已合并 main。用户决定 tools 尽量保持固定，P7 暂停；独立推进 P8a 文档收口与旧机制审计，其余 P8 范围待重评。合并仍由人类确认。

设计权威：[operations](operations.md)；决策：[ADR 0044](../decisions/0044-unified-operations.md)。代码基线为 `d35c3a546a5e3dacef331b5d39d3e1cc7efd4e9a`。GitHub 跟踪链接见本文末尾；Issue/PR 负责验收证据，不复制一份持续漂移的设计。

## 1. 施工原则

- 两个 EPIC 严格串行：A 收敛宿主操作与执行；B 已开放复合依赖与统一纯条件；原动态工具视图 P7 暂停，P8a 独立文档审计。
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

主要位置：`tools/memory/*`、`tools/tasks/*`、`identity/contacts.py`、`identity/persona.py`、`tools/observability/*`、`agents/assembly.py`。

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

## 4. EPIC B：可组合能力、纯语境与收尾（P7 暂停）

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

删除：TRIGGER/LOOP/STAGE_COND 的分裂纯词白名单、旧隐式谓词入口与无消费者的 Builtin.read_only 字段；更新所有仓库原型、测试及文档，不保留旧语义别名。

P6 实施决策：

- 三处条件共享同一纯函数准入：语言核心函数、显式输入的领域纯函数，以及统一目录中 effects 为空且 control=false 的 Operation（含复合操作）。非确定性的只读宿主操作不属于纯条件；不能通过局部 fn、复合依赖或嵌套调用绕过准入。
- 函数准入相同，数据作用域各自明确：when 提供 msg、event、self；until 提供 self、state、limits；stage-if 提供 state、scratch、round。静态校验拒绝引用未提供的符号，执行缺少必需作用域数据必须明确报错。复合操作只接收自己的单对象 schema 输入，不继承调用方 env；同一纯操作在三处由调用方分别构造输入。
- 旧隐式谓词直接删除，使用核心函数读取显式数据。例如迭代上限写为 `(>= (get state :iteration) (get limits :max_iterations))`；完成状态写为 `(get round :completing)`；评审通过写为 `(= (get (get scratch :critique) :passed) true)`；首轮为 iteration=0。预算计数、终止边界与宿主完成通知保持原有语义。
- 邮件访问保留显式参数的 mail-field、from?、content-matches?；收件人判断写为 `(contains? (or (mail-field msg :to) []) (get self :address))`。sleeping?、running?、lazy?、member-roster 等外部查询只能在 actions 中取得已记录结果，再由核心表达式作决定，不在 when 内发起 I/O。
- 不改语言 map/filter/reduce，不增加另一套复合执行器；本阶段不实现 P7 的授权名单和动态 ToolView。


验收：同一个 has-actionable-facts 在三处得到一致结果；作用域数据缺失加载/执行明确报错；纯条件不能经局部 fn 或复合依赖读时钟、模型/邮箱或写状态；现有预算/终止条件和完成通知语义保持。

### P7 — 每成员、每请求的动态工具视图与执行约束（暂停）

状态：2026-10-21 用户明确暂停，未提交运行时代码。以下实施与验收保留为原方案记录，不是当前施工承诺。tools 优先保持稳定；未来执行授权可独立讨论，不与动态工具面捆绑。

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

### P8a — 独立文档收口与旧机制审计（任务 [#195](https://github.com/rika-moe/TlonImpact/issues/195)）

依赖：P5/P6 已合并，不依赖 P7。仅文档与审计记录，不新增运行时能力。

实施：同步 architecture/design/reference/guides 与 ADR 的当前状态；记录 tools 尽量保持固定的决策及缓存考虑；审计已切换的 Tool/Capability 执行、旧 schema/名称/条件名单，区分活跃代码、明确保留例外和历史文档。完整证据见 [operations-audit](operations-audit.md)。

验收：

- 新读者能从 architecture、operations 与 reference 解释同一契约的 DSL/Tool 两入口、复合批准/冻结、Agent/Behavior/Prototype 关系。
- 明确 P7 暂停，当前无 :tools 表达式、统一 allowlist、逐请求视图或受控授权阶段；稳定 tools 不等于永久授权，未声明缓存收益实测。
- 对旧机制给出可复现检索及代码/测试证据；保留内部通知、非纯 DSL 宿主查询等真实例外，不为“零命中”删除现有功能或真实历史。
- 生成参考文档无漂移，完整 scripts/check_all.sh 与真实隔离 dream/SDK 邮件冒烟通过；痕迹清理，不改变 runtime 行为。

### P8b — 剩余原型依赖与综合验收（未实施，待重新定范围）

原 P8 的 Prototype 显式操作依赖声明、依赖/权限/工具过滤原因管理展示，以及完整跨阶段授权/撤权综合验收没有由 P8a 实现。原型依赖可另行独立设计；其中依赖 P7 的动态工具/受控阶段验收随 P7 暂停。它们不是本次文档 PR 的隐含任务。

EPIC B 状态：P5/P6 已交付；P7 暂停，P8a 独立收尾，P8b 待定。P8a 完成不等同于原 EPIC B 所有目标完成，不关闭或勾选未实现的原型依赖和授权目标。

## 5. 总体验收矩阵

| 性质 | 最早验收 | 最终证据 |
|---|---|---|
| 一份契约、两个入口 | P1/P2 | P5/P6 已验收；P8a 审计证据 |
| 不丢邮件/不伪造成功 | P2 | 回执、unknown、故障恢复收件箱数量 |
| 资源与成员隔离 | P1/P3 | A/B 越权与装置测试 |
| 记录与预算共享 | P4 | 并发/取消/恢复注入证据 |
| 复合继续组合 | P5 | 两层依赖、环/缺失拒载、冻结源码 |
| 条件统一 | P6 | 同一纯操作三条件，效果逃逸拒绝 |
| 动态视图与撤权 | P7 暂停 | 未实施、未验收，不计入 P8a |
| 旧机制真正退出 | 各切换 PR | P8a 全仓引用/调用点审计及保留例外 |

## 6. GitHub 跟踪

- [总 EPIC #179](https://github.com/rika-moe/TlonImpact/issues/179)：完整设计文字与两阶段跟踪。
- [EPIC A #180](https://github.com/rika-moe/TlonImpact/issues/180)：P1、P2、P3a/P3b/P3c、P4a/P4b 及审计收尾。
- [EPIC B #181](https://github.com/rika-moe/TlonImpact/issues/181)：P5/P6 已合并；P7 暂停；P8a 独立文档审计，P8b 剩余范围待定。

P1 已通过 [#184](https://github.com/rika-moe/TlonImpact/pull/184) 合并 main。P2 已通过 [#185](https://github.com/rika-moe/TlonImpact/pull/185) 合并 main。P3a 已通过 #186 合并 main；P3b 已通过 #187 合入 main；P3c 已通过 #188 合并 main；P4a/P4b 与审计收尾已通过 #189/#190/#191 合并；P5 已通过 #193 合并；P6 已通过 #194 合并；P7 暂停，P8a 正在收尾，P8b 未实施。关联的 [#173](https://github.com/rika-moe/TlonImpact/issues/173) 继续保留为长期工作方式/习惯学习方向，本计划不将其一并关闭。

## P3a 实施细化（2026-10-20）

P2 #185 已合入 main。P3a 使用 `file-read`、`file-write`、`file-edit`、`file-list`、`file-search`、`shell-run`、`shell-check`、`time-now` 作为两入口的同一操作名，参数继续使用 snake_case；旧 Tool 类和名字直接删除，无别名。文件领域服务复用现有 ExecutionDevice、路径检查与归档读取，不引入宿主文件系统后门；Shell 服务独占后台任务表，成员 shutdown 时先取消任务再关闭装置。

时间和文件/任务查询属于 read，文件写入/编辑与 Shell 执行属于 write。非幂等 write 在现有 ProgramStore 中先持久化调用 intent，再执行；已记录结果直接重放，未记录结果的既有 intent 明确报告 unknown，禁止自动重跑。邮件沿用 PostOffice 幂等回执，可用原 request_id 重试。该最小重放安全声明不扩张成 P4 的统一预算或调用框架。模型会话仍由原 session journal 负责恢复，非幂等操作的孤立 dispatch 明确不可安全重试。

L0 仅装配时间；L1 装配文件和时间；L2 加 Shell。无装置的已声明 L1/L2 操作明确报错，执行边界仍检查等级和路径。仓库原型、提示词、测试和生成参考在同一 PR 更新，用户文件和历史数据库不自动迁移或清理。


### P3a 本地验收（2026-10-20）

主会话复核并执行完整 `scripts/check_all.sh`：14/14 全绿，单元 2463 passed / 15 skipped，集成 34 passed。真实 Docker 的 L1 文件柜不启动即可完成五种文件操作；L2 命令成功、非零退出、后台结果查询、取消清理通过，祖先 symlink 越界读写拒绝。真实 dream + SDK 给 Adam 发信，回复线程及标读持久化通过。测试容器、临时 TLON_HOME 和服务均清理，8080 无残留。SDK 冒烟使用服务端身份和显式 X-Tlon-WebUI 标头，未修改既有默认认证问题。

迁移时统一 schema 会明确拒绝越界参数；目录改为先排序再截断，搜索每行展示保留原 200 字符。Docker 路径检查与 I/O 仍有 TOCTOU，未声称原子路径安全。非幂等 intent 是 P3a 验收所需的最小防重跑记录，通用调用恢复与预算仍留给 P4；P3b 及以后未开工。

## P3b 实施细化（2026-10-20）

P3a #186 已合入 main。本阶段将记忆、任务、人格/联系人和观测接入同一 Operation 契约，删除所属旧 Tool 实现与旧名，不加别名。操作名为 memory-remember/search/forget、session-read；task-create/list/start/pause/complete/remove/current；persona-update、contacts-read/upsert；metrics-read、mail-stats、reputation-read、social-health、traces-list、trace-read。参数保持 snake_case，复用成员绑定的 MemoryManager、TaskManager、SessionJournal 和只读观测 provider，不允许覆盖拥有者身份；公开声誉/trace 查询仍可显式指定观察对象。

state-get/state-put 保留名字，改为同一具名对象输入（key，以及写入时的 value），没有位置参数兼容。状态值是严格 JSON，DSL 对象键按原拼写投影；通过 JSON Schema 原生 boolean 子 schema 表达任意 JSON 值，仍由现有 JSON 边界拒绝闭包、Symbol、循环、非有限数等。输入根仍必须为闭合 object，不引入 $ref、远程 schema 或隐式转换。旧状态 ActionRequest 分发删除。

程序 state-put 复用 ProgramStore 中“状态 + 该次调用结果”的同一事务；宿主调用上下文携带程序效果位置，模型入口不能伪造。模型入口以宿主 call_id 在同一 ProgramStore 的 effects 表中保留事务回执，复用原子状态写入；同调用重试不会覆盖后来的状态，不另建状态调用日志。模型会话仍由原 journal 记录分发与结果，不自动重放未知调用。普通领域写入沿用 P3a 的非幂等 intent 保护；不在本阶段建设 P4 的通用预算/并发排序框架。人格文件仍仅限自身 identity/soul，RULES 不可写；联系人和记忆/任务保持私有，社会观测保留原有公开口径，不能借统一入口扩权。

P3b 持久化复核：任务与 ClaimStore 均先原子保存私有快照，再发布内存状态；写失败向调用方传播 unknown，提交后丢失回执则重新读取磁盘事实。ClaimStore 保留逻辑追加语义，物理存储改为完整 JSONL 快照原子替换，不再采用物理 append 或失败后 fallback。任务暂停原因与状态在同一次提交保存。状态写复用事务回执，并以类型化 ReplayConflictError 区分重放冲突和未知写入结果。

### P3b 本地验收（2026-10-20）

主会话复核后完整 `bash scripts/check_all.sh` 14/14 全绿：单元 2500 passed / 15 skipped，集成 34 passed，前端检查及构建均通过。真实隔离 dream + SDK 邮件链覆盖新领域操作并核对实际文件、状态与收件箱，资源已清理；确定性 DSL 冒烟未调用真实模型，SDK 显式携带服务端要求的标头。Markdown 本地链接/围栏与 diff 检查通过。修复与 #186 同类的 scheduling 测试 SQLite 表初始化等待竞态；P3c/P4 未开工，默认仍由人类合并。

## P3c 实施细化（2026-10-20）

P3b #187 已合入 main。剩余宿主能力继续沿用 Operation 的唯一 schema、宿主身份绑定与双入口：member-spawn/list/start/stop/start-all/self-state；prototype-list、prototype-staging-list、prototype-lineage、prototype-review、prototype-write、behavior-propose、capability-propose；model-call。实际名称以本阶段生成 reference 为准，原有 camelCase 工具和旧位置参数 DSL 入口删除，无兼容别名。原型/能力 artifact 格式仍留待 EPIC B，不在本阶段重建。

finish-turn/sleep-until/exit-agent 是控制请求，使用共享契约但保留控制标记；模型混批和 DSL 深层封装均拒绝，只有合法独占请求在排空后由 runtime 提交四态转换。run-loop 只保留 Behavior 编排请求，不注册为模型工具或可封装宿主操作。生命周期权限、见习限制、偿付门槛、近亲检查、作者归属、人工批准由宿主服务实际执行边界保证，不依赖工具是否展示。模型调用复用成员 LLMClient 与系统提示词，费用归原成员；不允许输入覆盖模型身份或预算归属。

删除剩余旧 Tool 业务类及手写参数校验，保留协议 ToolResult 与薄投影。ToolExecutor 只负责模型协议调用、跟踪及结果传递，所有业务交给 OperationExecutor；复用既有 ProgramStore/session journal，不提前扩张 P4 的一般预算与恢复框架。验收覆盖双入口相同返回/拒绝、见习绕路拒绝、模型归属/费用、控制混批/深层封装、staging 失败不残留及人工批准边界；最终亲自跑全量质量门与真实 dream/SDK 冒烟，PR 面向 main 等待人类合并。

P3c 边界细化：DSL 控制请求仅允许规则唯一的直接动作，参数必须为字面量命名 JSON 对象；加载与程序执行均拒绝混批、嵌套及 when 中的控制请求，避免参数效果先行。模型入口保留唯一工具调用约束。见习成员只禁止 spawn 和三类提案创建，原型目录、谱系与咨询评审仍可用。staging 先在同文件系统 catalog 外构建完整包，以短目录锁保护同名检查和原子 rename；提案在批准前保持 untrusted。主会话真实装配复核补齐原型 writer 的 TlonPaths 绑定，并以隔离落盘测试与真实 SDK 冒烟核验。

### P3c 本地验收（2026-10-20）

主会话亲自审 diff，独立交叉审查无新增阻塞。完整 `bash scripts/check_all.sh` 14/14 全绿：单元 2533 passed / 15 skipped，集成 34 passed，前端检查/构建、类型、依赖、打包及文档新鲜度通过。最后同步 Capability 提示词的可封装词表，额外 48 项起草/控制回归通过；提示词锚定真实准入注册表。真实隔离 dream + SDK 邮件链核对生命周期目录、原型写入/谱系、作者、未审批状态、回信及持久化 sleep，finish 通过，数据与进程清理、8080 无残留。模型双入口通过真实 LLMClient ledger 路径加 fake provider 验证原成员归属，未调用真实模型服务；SDK 显式携带服务端标头。初轮类型标注与测试接线问题已修复，以最终质量门为准。默认仍由人类合并，P4 尚未开工。

## P4 实施切片（2026-10-20，#188 合并后）

代码审计确认 P4 同时跨操作提交、程序重放、会话恢复，以及三处模型调用/多种预算计量。按第 1 节允许的实施前切片原则，拆为两个严格串行 PR，不把未落地的一半称为 P4 完成：

- **P4a：调用记录与恢复权威**（已通过 #189 合并）：OperationExecutor 统一两入口的叶子 lookup/begin/commit；ProgramStore 保存稳定 root/path/call_id、规范化输入、契约与宿主构建指纹、结果或结构化错误；state-put 与同一 invocation 行原子提交。session journal 仍记录交互，dispatch 保存调用引用，恢复查询 ProgramStore，修复操作已提交但 tool/result 未写入时误报 interrupted。程序与会话在恢复之前核对构建身份。已提交 read 重放原采样；未提交 read 可重采样；幂等写只使用原键重试，其他 intent 无回执保持 unknown。输出契约错误保留原回执及错误，不重做效果。旧调用格式明确拒绝，不做 migration 或清库。
- **P4b：共享预算、取消与并发边界**（P4a 合并后）：根调用统一预算/取消/截止时间，覆盖 stage、压缩与嵌套 model-call，预留原子化、恢复不重置；成员写按声明顺序串行并允许同一持锁上下文重入；删除 _HOST_QUERIES 的 pure 重写，MemberCapabilities 改名 MemberServices。P4a 保留当前预算、并发和服务名称，不提前声称这些保证。

P4a 的记录位于原 ProgramStore SQLite，不增加另一套交互日志。纯计算不写叶子记录；run-loop 的程序生命周期结果仍由原 effects 记录负责，不和操作回执重复。宿主指纹保守覆盖 tlon/tlosure 运行代码、锁定依赖指纹、已安装依赖版本；操作契约另存内容指纹。根创建时冻结、恢复先核对，失配明确拒绝；不加载旧宿主实现。相同 root/path 不能换参数、名称或契约，编号不能依赖完成先后。

P4a 验收：DSL/Tool 对同一协议都以 ProgramStore 的真实回执恢复；dispatch/result/collect 各故障窗口不重复已提交写入；两个根反序完成互不污染；state 与回执同事务；已提交历史不重新授权；普通失败与 unknown、输出违约原回执均可重放；宿主/契约失配在任何新执行前拒绝。完整质量门与真实 dream/SDK 冒烟通过后交付独立面向 main 的 PR。P4 总清单仅在 P4a/P4b 均合并验收后勾选。

预算审计另确认：session 现按实际 total usage 计量，程序按 requested output allowance 计量；现有 tokenizer 估算不能证明 prompt token 的硬上界。P4b 必须明确预留与实际结算的关系，不宣称 provider 实际 total 永远不会越过未精确测量的预留。


### P4a 本地验收（2026-10-20）

主会话审 diff 并亲自重跑完整 `bash scripts/check_all.sh`：15/15 全绿，单元 2594 passed / 15 skipped，集成 34 passed。新增依赖指纹新鲜度检查接入同一质量门及打包流程；前端测试/build、类型、依赖、reference 与 wheel 检查通过。交叉审查发现未知 Tool 的孤立 dispatch 缺少冻结元数据，已修复并新增 7 项回归。报纸工作流重放暴露输出 map 排序改变后续 prompt 的问题，已改为只规范化输入身份、保留输出顺序，并通过 8 项真实工作流集成测试。初轮导入排序失败已修正，以最终全量全绿结果为准。

真实隔离 dream + SDK 邮件链覆盖生命周期目录、原型提案、作者与未审批状态、回信、finish 与持久化 sleep；直接核对 SQLite 的 12 条唯一 invocation 回执、根/契约指纹和状态值，确认没有重复的 Operation effects 记录。服务、临时数据清理且 8080 无残留。故障/恢复覆盖由 fake provider 与可重复提交窗口注入验证，未调用真实模型服务；SDK 显式携带服务端要求的标头。当前只交付 P4a，P4b 及 P4 总项保持未完成，PR 面向 main，仍由人类合并。

## P4b 实施细化（2026-10-20，#189 合并后）

P4a #189 已合入 main。本阶段采用 Python asyncio 的任务取消、上下文传播及锁机制，不新增工作流框架或第三份结果日志。实现宿主 ExecutionScope，携带预算根、稳定调用路径、取消信号与 UTC 截止时间；模型参数不能覆盖。程序直接调用维持 output 额度口径（成员 llm.max_tokens），run-loop 明确创建 recipe.max_tokens 的 total 预算根，继承父取消与更早截止时间；不把整个会话误限为程序默认输出额度。同一套账本显式保存 measure，删除驱动按操作名维护的局部计数。会话内部 stage、上下文压缩、Tool 内嵌的提案/评审/model-call 全经成员 LLMClient 入口继承同一预算；后台反思/蒸馏另建宿主维护根，不继承已结束会话的额度。

模型请求先在 ProgramStore 原子检查余额并预留；请求自己的完整响应仍存同一 invocations，响应与预算结算同事务提交。历史响应直接还原，不发第二次请求、不重复记额度。output 根保持原请求额度预留不退款；total 根按实际 total 结算，缺 usage 或提交未知保留预留；实际高于估算的消费如实记入并拒绝后续请求。输入估算是准入依据，不能证明 provider 实际 total 的硬上界。未知提交不自动重试，明确的 provider 拒绝才允许释放该次预留。冻结额度、计量口径和截止时间，恢复不得重置。

成员共用 task-owner 可重入写 gate；仅同一 asyncio task 的嵌套 await 可重入，同根的不同并行 task 仍互斥。同一批 Tool 的写操作显式按声明顺序建立等待链，读操作不入链。先登记全部子任务再发布分发事件，取消时等待已登记任务清理。历史调用先重放，新效果在等锁后、写 intent 前及 handler 前检查取消/截止时间；不声称取消能撤销已提交的外部效果。正常 session 结束不取消父程序延续。

P4b 当时删除 _HOST_QUERIES 的运行时 pure 重写：四个宿主查询直接声明非 pure 与 read_only，规则 when 按确定性或只读准入；其他独立词表不扩张。此条件准入随后由 P6 替代，并删除 Builtin.read_only 字段：所有纯条件禁止外部查询，查询在 actions 中物化并记录。MemberCapabilities/assemble_member_capabilities 改为 MemberServices/assemble_member_services，不留别名，artifact 与 API facade 的 capability 概念不改。

验收覆盖并发预留、历史响应与结算原子性、usage 缺失/超额、同进程及重启不换 ID 绕过未知模型请求、stage/压缩/内嵌调用共享额度、写顺序/读并发/同 task 重入、等锁取消与分发事件取消窗口、冻结截止恢复，以及查询采样与各词表权限边界。最终完整质量门、真实 dream + SDK 冒烟及 main PR 均完成后交付，仍由人类合并。


### P4b 本地验收（2026-10-20）

主会话亲自审 diff，并重跑最终完整 `bash scripts/check_all.sh`：15/15 全绿，单元 2665 passed / 15 skipped，集成 34 passed；相比 P4a 新增 71 项回归。前端测试/build、222 个源文件类型检查、依赖、生成 reference 和 wheel 检查通过。独立交叉组 84 项通过，无新增代码阻塞；文档已限定 fuel 仍由现有 Evaluator 管理，不将复合 fuel 协作称为本阶段已完成。

真实隔离 dream + SDK + 本地 OpenAI 兼容 fake provider 跑通程序直接模型请求、run-loop、stage 与 Tool 内嵌模型请求、跨轮重复 ToolCall.id、实际回信/标读/状态及父程序延续；共 5 次 provider 请求，程序 output 记 8，会话 4 次请求 total 记 60，核对 SQLite 没有未结预留。模型服务为本地 fake，未调用真实外部模型；SDK 显式携带服务端要求的标头。进程、临时 home、provider 和端口均清理，8080 无残留。

真实冒烟发现 LiteLLM 工具计数器不支持合法 boolean 子 schema，已改用同一库分别统计消息和完整工具 JSON，并以真实完整装配工具表补回归；修复维护轮询没有任务也落空预算的问题。故障审查另修锁序死锁、模型未知被预算/until 误终止、调用位置按输入 hash 绕过身份检查，以及先创建任务后登记的取消窗口。最终集成遇到已有管理测试把文件存在当作 schema 就绪的竞态，改为明确查询 sqlite_master 等待 runs 表提交，保留原断言，3 项针对性及最终 34 项集成均通过；未删除失败测试。

本阶段保留两个既有 token 计量口径并统一账本与内部调用入口；旧数据库版本明确拒绝，无迁移或清库。PR 面向 main 等待人类合并；P4 与 EPIC A 只有在 P4b 合并后才能标记完成，EPIC B 未开工。

## EPIC A 审计收尾（2026-10-21，#190 合并后）

#190 已合并 main；独立审计确认旧业务 Tool 已全部投影为 Operation，但发现执行契约仍有遗漏。本次先修复，不开启 EPIC B：

- 超时统一由 OperationExecutor 决定和执行：成员配置 tool.per_timeout 作为共享默认，操作 default_timeout 优先，已声明的显式 timeout 参数再优先；Shell 保留 10 秒结果清理宽限并声明为操作元数据。取该次调用期限与继承根期限的较早者，DSL 与 Tool 共用。LoopDriver 删除独立 wait_for 和凭工具名/效果合成超时回执的路径。先查询历史；计时覆盖等待写 gate、持久化与 handler，已提交回执优先，未进入 handler 的本次调用不能谎报已派发未知；不得将旧未知 intent 或另一并发调用的 intent 覆写成确定拒绝。
- 调用的局部截止时间向内部模型请求传播，但不改写预算根的冻结截止时间；恢复不能通过局部超时延长根期限。外部取消继续传播，不把 shutdown 吞成正常完成，已知提交事实保留。
- spawn 配额身份改为宿主 CallContext.run_id，模型会话和恢复使用原 session 根，直接 DSL 使用程序根；日志 ContextVar 不再承担配额授权。恢复前该会话已达配额时仍须拒绝，不能换入无会话桶。
- 演化及其他写服务包装透传确定的预算/截止拒绝，明确的 provider 拒绝保持 rejected，真正模型未知仍保持 unknown；内部冲突判断/蒸馏不能吞掉执行控制错误后继续落盘。
- 成员装配一把 task-owner 可重入 write gate，显式注入 executor 与 MemoryManager。普通操作、反思、蒸馏和直接记忆修改共享它；不在父任务持锁后启动等待同锁的后台任务。读操作仍可并发。
- 删除无人使用的 tools/prototypes/ports.py 与当前旧说明，领域类型仅保留 evolution.ports 的权威定义，无旧别名。

针对性验收重现审计中的已提交却被报 unknown、等锁未执行却报 unknown、两入口超时不一致、恢复 spawn 绕配额、确定预算错误被永久未知化、维护写越过 gate。每项加入回归，最终亲自审 diff、完整质量门与隔离 dream/SDK 冒烟通过后用 gh 创建 main PR，合并仍由人类执行。EPIC A 在这些遗漏修复验收合并前不关闭。

### 审计收尾本地验收

主会话复核最终 diff、超时/取消/历史回执及共享 gate 的调用点，重跑完整 `bash scripts/check_all.sh`：15/15 全绿，单元 2733 passed / 15 skipped，集成 34 passed，221 个源文件类型检查通过。首次单元检查的两项失败来自旧截止错误文案断言，已改为验证 ExecutionDeadlineError 类型及零写入，未删除测试。原审计脚本确认恢复 spawn 仍使用原 session 配额桶，演化预算拒绝在 DSL/Tool 均保持 BudgetExceededError。

真实隔离 dream + SDK + 本地 OpenAI 兼容 fake provider 冒烟通过：5 次 provider 请求，程序 output 记 8，会话 4 次请求 total 记 60；重复 ToolCall.id、真实回信/标读/状态与父程序延续均正确。未调用外部模型，隔离 home、进程及端口已清理，8080 无残留。本次无数据库格式升级或迁移；EPIC B 未开工，收尾 PR 面向 main，等待人类合并。

首轮 GitHub CI 的单元组在恢复测试末尾等队列排空时超出 2 秒；日志确认恢复已投递，worker 仍需完成持久回执与下一事件。恢复本身按 1 秒轮询，CI 上的线程 I/O 与错误日志让两轮工作挤进 2 秒的假设不成立。仅该恢复阶段改为最多等待 10 秒，仍断言恢复只投递一次、旧任务完成前新状态不写入、新状态最终提交；不修改运行时轮询或删除测试。集成与静态首轮已通过，修正后重跑完整质量门与 CI。

## EPIC A 外部审查修复（2026-10-21）

基线 main `dbda656`，#182–#191 已合并，P1–P4 的操作切换验收完成。外部审查确认恢复隔离承诺尚有活性缺陷，本次修复后再推进 EPIC B。

恢复按根处理：枚举待恢复程序不触发所有根的全局校验；每根单独验证冻结身份。失配或不可自动重试的效果未知（含模型请求）进入持久 recovery_blocked，保留冻结输入、intent、未知结果与预算预留，不伪造成功、不重发未知请求。程序使用既有 runs.error 记录明确隔离原因；会话追加 session/recovery-blocked 事件并写入既有生命周期元数据，隔离关联程序根；不算作正常完成，不进入蒸馏。隔离是停止该旧延续，不是跨版本恢复；它不能在新工作开始后再自动复活。其他根、后续邮件和新会话继续运行，重启不重复热重放隔离根。可用宿主幂等回执对账的暂时邮件故障仍保留既有恢复，不一概跳过所有 pending。

验收必须覆盖程序/会话构建失配、启动模型未知、健康和新根继续工作、再次重启仍隔离、未知 provider 不重发、旧数据和费用事实保留，保留现有邮件恢复与串行状态测试。明确已废止的提示词、配置和无消费者接口随本次清理，里程碑文档同步当前状态。

全局闭包身份、控制操作名单、全量占位词的拆解列入 EPIC B P5/P7 前置验收；本次不删除安全指纹或提前实现半套授权/依赖图。内部终止通知维持宿主管理通知语义，需在 P7 明确其与普通发送授权的边界。

### EPIC B 开工前的现存机制拆解

| 阶段 | 当前限制与必须替换的机制 | 验收标准 |
|---|---|---|
| P5 | registry_identity 目前将所有加载的闭包 params/body/env 纳入成员 build_id；不能直接删除这层保护 | 宿主构建身份与实际使用的传递依赖内容分别冻结；批准/修改无关能力不影响旧根，实际依赖变更不能静默替换，宿主变化仍拒绝恢复 |
| P5 | CONTROL_NAMES、ENDING_TOOLS 与 capability 排除词分别维护 | 从统一定义的 control 元数据推导控制准入；不同名称但 control=true 也受独占和不可复合保护；run-loop 继续是宿主编排边界 |
| P5/P7 | 全量操作的 unavailable 占位使成员无关加载校验放过未装配依赖 | 分清定义不存在、成员未获执行授权、服务未装配；必需依赖不满足时拒载并说明原因，视图过滤可观测，运行时逐叶再授权 |
| P7 | 终止/失败回信是宿主通知，当前不经 Operation invocation/gate | 普通邮件服从发送授权；宿主通知仅给责任集合中的人类、固定内容来源与稳定 request_id，不允许模型指定任意收件人；宿主只读业务阶段仍可通知，取消/截止约束保留 |

当前内部通知复用 MemberMailService/PostOffice 幂等提交，不是业务 Operation；它不触发模型消费，因而没有 token 消费可记。不能声称它已拥有 invocation 原子回执或成员 write gate，也不为补文档提前创建第二套通知运行时。

### EPIC A 已合并验收矩阵

| 验收范围 | 合并证据 | 当前结论 |
|---|---|---|
| 最小契约、schema、身份与分层 | #184 | 完成 |
| 邮件真实投递与恢复 | #185 | 完成 |
| 文件/Shell/时间及装置边界 | #186 | 完成 |
| 记忆/任务/人格/联系人/观测与状态回执 | #187 | 完成 |
| 生命周期/演化/模型与控制独占 | #188 | 完成 |
| invocation、指纹与重放 | #189 | 已交付；外审补齐坏根隔离活性 |
| 共享额度/取消/截止/写序 | #190 | 完成 |
| 两入口超时、配额、维护写 gate | #191 | 完成 |

上述矩阵描述已合并代码，不等同于本次外审修复已合并。新增坏根隔离与同轮 ID 拒绝必须单独通过质量门和真实冒烟后交付；P5–P8 的复合能力与动态视图仍未实现。

### 外部审查修复本地验收

主会话复核恢复写序、真实 ToolResult 转换、两类 continuation、调用身份与任务跟踪，并亲自重跑最终完整 `bash scripts/check_all.sh`：15/15 全绿，单元 2751 passed / 15 skipped（比 #191 新增 18 项），集成 34 passed，222 个源文件类型检查通过。独立只读复核重点组 117 项通过，无剩余阻塞发现。

真实隔离 dream + SDK + 本地 OpenAI 兼容 fake provider 注入 previous-deployment 的旧程序根：原根以 recovery_blocked 保留，后续新邮件、直接模型、内嵌模型、状态、回信/标读与父程序延续完成。共 5 次 provider 请求，程序 output 8，会话 total 60；未调用外部模型，临时 home、进程与端口均清理。变更 Markdown 本地链接与 diff 检查通过。

过程记录：首轮全量发现两个旧断言仍要求 pending 全局失败和旧模型未知数据形状，已保留测试并改为逐根恢复拒绝及明确 unknown_source，18 项针对性通过。独立审查补出的 metadata 普通异常、恢复 ToolResult unknown、成功历史误隔离及已结束会话安全重试漏洞均已加真实路径回归。最终数据以本节最后一次质量门为准，未删除失败测试。此修复无数据库格式升级、迁移或真实用户数据清理；等待独立 main PR 合并后再开始 EPIC B。

## P5 实施细化（2026-10-21，#192 合并后）

基线 main `b0e0349`。先拆全局闭包身份与控制名单，再接复合操作，是为了避免依赖冻结和控制保护建立在即将删除的机制上；两者在同一 P5 PR 中完成端到端接线，不交付半套机制。

- 共享 `CompositeSpec` 位于 operations，artifact 使用 version=2、input-schema/output-schema 与固定单参数 impl；不读取旧 args/pure 格式。静态具名调用分析复用语言作用域规则，依赖拓扑编译，效果从叶子传递推导；局部函数不能传递注册操作值或动态逃过分析。control 来自 Operation 元数据，run-loop 仍为宿主编排语法。
- 批准记录保存当前定义及传递依赖内容身份；受信加载校验批准绑定，坏定义只影响依赖它的闭包。修改依赖必须重新批准受影响定义，不能自动升级批准。宿主定义目录复用现有各域 definition factory，业务实现不复制。
- Composite 进入同一 OperationExecutor 与 DSL/Tool 投影；复合边界不创建非幂等父 intent 阻断叶子重放。稳定 child 序号路径与调用者身份传到叶子，共享写 gate、根预算、取消/截止和语言 fuel/depth；输出仍按同一 schema 验证。
- 全局宿主 build_id 保留代码/依赖与宿主 builtin 身份，剔除已不再注册为全局闭包的能力定义。ProgramStore 增加 root_definitions 的 create-once 快照：程序执行前冻结所需定义闭包，会话发模型前冻结所暴露复合定义；恢复以原快照重建目录、验证宿主依赖，不从当前磁盘重新拼旧运行。新增表由既有初始化创建，无数据迁移、清库或旧 artifact 解析。根快照与已有 build/回执校验共同工作，缺快照的旧复合运行不得猜测恢复。
- 实际成员装配时区分未定义和缺失成员服务，必需依赖不可执行则拒载；本阶段不新增 P7 allowlist/动态视图或受控阶段。静态目录只用于存在性分析，不把占位词当成可执行服务。
- 管理端展示 input/output、依赖、推导效果与批准绑定。纯 examples 真实求值；效果 examples 仅在显式 fake-results 队列的隔离宿主执行，缺 fake 即失败，审批和详情接口绝不执行真实写入。

验收覆盖两层复合经 DSL/Tool 同值同拒绝、缺失/环/碰撞/闭包隐藏效果/动态目标/control 拒绝、叶子成功后崩溃恢复、能力修改删除仍用原快照、批准依赖变化拒载、无关能力变化不破坏旧根、宿主变化拒绝、共享 fuel/token 与真实 dream/SDK 冒烟。完整质量门全绿及 main PR 交付后由人类合并。

恢复与新准入分开：当前 Behavior 因依赖删除/失效而不再可加载时，持有冻结待恢复根的成员允许进入明确的“仅恢复”激活，旧根从原快照继续；当前新规则、boot 和邮件工作不被准入，诊断说明 Behavior 不可用，修正后显式重载。没有冻结待恢复事实时仍严格拒载。不能通过当前目录失效阻止原快照恢复，也不能拿旧快照为新工作补权或补定义。

宿主批准边界：artifact 的依赖绑定记录宿主契约身份与复合源码内容；宿主实现由受信代码部署负责，运行根另外冻结完整 runtime build_id。契约相同的宿主部署不要求逐一重新批准全部 artifact，但旧宿主根必须拒绝跨构建恢复；复合依赖源码变化仍必须重新批准上层。这避免再次把整个全局构建混入每个能力批准记录。

复合的派生结果缓存不代替叶子权威。所有子回执已提交、父缓存尚未落盘就崩溃时，允许在已过期/取消的作用域内仅从历史重建结果；任何缺失叶子仍禁止新分发，真实任务取消仍向上传播。没有为恢复重新开放宿主写入或预算。

静态宿主定义目录只缓存不可变的 contract 投影，避免控制/加载查询重复创建并校验 52 份 schema；受信 artifact 目录与批准指纹不缓存，仍在每次加载时重新核对。原始宿主目录构造实测约 59ms/次，缓存后的投影读取不再重复 schema 准入；实际成员 handler 始终由自己的服务装配，未缓存成员资源或路径。

### P5 本地验收（2026-10-21）

主会话复核最终 diff、静态作用域分析、批准指纹、源码/快照原子受理、根级 ToolExecutor、子回执与控制元数据接线，亲自重跑完整 `bash scripts/check_all.sh`：15/15 全绿；单元 2762 passed / 15 skipped，集成 34 passed；227 个源文件 mypy 通过。29 项复合核心回归覆盖纯/效果、两层组合、缺失/环/碰撞/隐藏效果/动态调用/control、schema 输出违约、共享 fuel/depth/token、叶子崩溃、父缓存窗口与过期/取消历史重建；冻结/成员准入与原型入口另有真实装配回归。旧 v1 位置参数、作者 pure 与独立闭包执行测试随机制删除，由 v2 契约与端到端覆盖替换。

真实隔离 dream + SDK + 本地 OpenAI 兼容 fake provider：先经 REST 提案并批准 summarize-thread 与 send-thread-summary，再让 DSL 直接调用摘要、LLM Tool 调用发送复合，验证一次真实邮箱投递、标读/状态和父程序延续。共 5 次 provider 请求；程序 output 记 8，会话 4 次请求 total 记 60，内部调用未重置额度，跨轮重复 ToolCall.id 保持独立。未调用外部模型，临时 home、进程与端口已清理。Markdown 本地链接、生成 reference 与 diff 检查通过。

过程补齐：早期全仓诊断暴露测试替身未提供控制元数据/邮局服务，已修正装配并保留原断言；真实冒烟发现原型仍按宿主词表拒绝已批准操作，现已接通全部行为准入入口。独立复核指出冻结受理窗口、父缓存前崩溃与保留名碰撞，均加回归。没有通过扩大旧格式读取或放宽服务准入规避失败。最终以本节质量门和冒烟为验收依据。

P5 已通过 [#193](https://github.com/rika-moe/TlonImpact/pull/193) 合并 main，P6 从合并提交 `c18bc8d` 开工；P7/P8 未开工。审批绑定依赖、实际成员服务校验已落实本阶段前置拆解；P7 的统一授权和动态视图仍单列待办。


## P6 历史交付与验收（2026-10-21，现已通过 #194 合并）

从 main `c18bc8d`（#193 合并提交）新建独立分支。when、until、stage-if 共享纯目录准入，数据作用域独立显式；删除三套固定白名单、隐式谓词和已无消费者的 Builtin.read_only。纯 Operation 必须 effects 为空且 control=false。邮件先物化为记录，动态与字面量 session recipe 都使用当前运行目录校验，会话条件使用该根冻结目录。没有新增执行器，没有实施 P7/P8；预算硬上限与受限宿主终止通知保持。

主会话亲自审 diff、复核调用方与独立审查结论，并重跑完整 `bash scripts/check_all.sh`：15/15 全绿，单元 2806 passed / 15 skipped，集成 34 passed，227 个源文件类型检查通过；import-linter 13 kept / 0 broken，TS 门实跑。新增 45 项纯条件验收，覆盖同一 has-actionable-facts 在三处的值与 schema 拒绝、局部函数和传递依赖隐藏效果拒绝、缺作用域与非数据对象错误、纯 control 排除、动态 recipe、共享 fuel 与旧根保留旧定义。

真实隔离 dream + SDK + 本地 OpenAI 兼容 fake provider：REST 批准同一个 has-actionable-facts 后用于 when、until、stage-if，继续执行两层模型/发信复合；验证一次真实邮箱投递、标读、状态与父程序延续。共 5 次 provider 请求，程序 output 记 8，会话 4 次请求 total 记 60；未调用外部模型。临时 home、dream、provider 和端口清理。

过程记录：首轮全量仅一项旧夹具绕过 rule_env 直接给 mail-field 传 Python Mail 对象；保留该测试并改为物化记录，同时补上旧对象明确拒绝断言。60 项针对性回归通过后重跑全量门得到上述结果，没有删除失败测试，也没有增加旧行为兼容或迁移。旧 Behavior 若引用删除的隐式词必须更新为显式数据表达式；不自动改写用户文件。

P6 当时直接向 main 交付，现已通过 #194 合并并可勾选。之后用户暂停 P7，拆出 P8a，当前范围见本文顶部与修订记录。


## 2026-10-21 范围修订：tools 稳定与 P8a

用户明确决定 tools 尽量保持固定，以避免频繁变动工具定义破坏缓存前缀复用；P7 整体暂停，不提前实现其中 allowlist、请求级 ToolView 或受控阶段。不同成员仍可因装置/服务/信任有不同稳定工具集合。缓存收益未在本次测量；将来授权变更是否独立实施需另行讨论。

原 P7 工作分支仅追加了未提交的实施草案，没有运行时变更；该草案保存后移出当前计划。本次从 main `f5a8faf`（#194）建立独立 P8a 分支。P8a 只交付文档收口与旧机制审计；P8b 未实现项单列，不以暂停阶段阻止已完成能力的准确记录。


P8a 本地验证：主会话复核源码、文档差异与保留例外，完整质量门及隔离 dream/SDK 投递通过，连续三次会话请求的序列化 tools 一致；未测 provider 缓存命中率。Python 源码无差异，过时源码注释单独登记，避免仅注释改动也改变恢复构建身份。详细数量、过程与 PR 见任务 [#195](https://github.com/rika-moe/TlonImpact/issues/195)。合并前 P8a 保持未勾选，P7 暂停，P8b 未实施。
