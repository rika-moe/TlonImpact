# Tlon 系统架构

> 当前工作流程已统一为 v2 模块程序；见 [ADR 0046](decisions/0046-programmable-workflows.md)、[Runtime](design/runtime.md) 与 [实施计划](design/programmable-workflows-plan.md)。


本文是当前系统的入口：先定义跨领域必须遵守的边界，再描述当前采用的结构与协作链路。领域内部的规则和机制在对应 design 完整定义；历史选择见 [决策索引](decisions/README.md)，文档维护规则见 [documentation.md](documentation.md)。

## 系统概念

Tlon 让一组成员通过邮件协作，人类也以成员身份收发邮件。成员以原型为行为来源，由宿主运行其行为规则。每个成员执行同一种事件程序，条件、传统循环和模型会话按需组合，不形成不同的成员或规则类别。

| 概念 | 含义与边界 | 领域设计 |
|---|---|---|
| 成员 | 有地址、行为与生命周期的个体；运行状态与主观身份分开 | [统一成员模型](design/unified-member-model.md)、[身份](design/identity.md) |
| 原型 | 可提案、评审、实例化的行为来源；不等于成员档案 | [原型](design/prototypes.md) |
| 行为 spec | 成员规则的权威描述，不包含模型配置、人格、记忆等全部输入 | [DSL](design/dsl.md) |
| 邮件与邮箱 | 成员间通信及每个收件人的持久收件事实 | [邮件](design/mail.md) |
| Session | 一次按配方推进的模型会话；journal 保存事件与生命周期元数据 | [运行时](design/runtime.md) |
| Claim | 成员长期记忆的断言，可由蒸馏、主动记录或反思产生 | [记忆](design/memory.md) |

## 系统约束

以下规则约束跨领域协作。变更须先讨论并同步相关领域设计；实现类名、默认值和技术选型在下一节描述，不自动成为永久规则。

1. **依赖单向、公开边界明确。** tlosure 不依赖 tlon；runtime 不依赖 server/client/launcher；serving 经 runtime 的公开 facade 触达领域能力。工具通过注入端口使用上层能力，不反向 import 调用方。具体包边界见下表。
2. **成员间通信走邮件。** 邮件与进程内事件是不同通道：EventBus 通知内部事实和生命周期意图，不能替代成员投信。显式注入的服务、facade 和查询端口可以直接调用，不存在「所有方法调用都必须改成事件」的要求。
3. **机制与策略分离。** 宿主掌握 IO、调度、预算执行和准入检查；策略通过行为 spec、社会政策等明确入口使用机制，不能绕过宿主的执行边界。策略入口及其权限各有契约，不能统称为可任意修改的数据。
4. **规则权威与派生状态分开。** 成员规则由 spec 定义；唤醒资格等派生属性不能另存为独立可改的规则来源。模型、执行授权、人格与记忆各有自己的归属，不能从「spec 权威」推出成员可自行改写这些配置。
5. **外部效果由宿主执行。** DSL 在操作调用处经异步边界交给共享 OperationExecutor，取得结果后继续求值。纯条件只能执行纯计算，不能读取外部服务。四个 DSL 宿主查询和受限终止通知仍是明确例外，见 [旧机制审计](design/operations-audit.md)。具体契约见 [共享操作](reference/operations-reference.md)。
6. **持久事实先于通知。** 投信必须经过邮局；成功提交后发布通知，通知失败不撤销已提交的邮件。邮箱是待处理邮件的持久依据，唤醒信号不是第二份队列；漏通知后的恢复回到持久邮箱。
7. **成员模型统一，行为演化经过准入。** 所有规则统一执行普通程序，会话只是可组合的效果。成员不能直接修改自身或他人的行为规则；行为提案经原型信任通道形成新成员。休眠、退休、删除是不同操作，自动排空或停止不隐式删除档案，人类显式治理删除另有入口。
8. **依赖显式，装配无环。** 实例依赖经构造器或明确的装配参数传入；业务组件不能依赖隐藏的 runtime 单例。runtime 与 serving 各自负责装配和资源清理，不由一个类包办全部层次。
9. **业务状态按 runtime 隔离。** 路径、注册表、邮箱等实例状态由显式的 `TlonPaths` 和依赖对象限定。进程级观测设施是已知例外，不能据此宣称所有观测数据也完全隔离。
10. **访问权限由宿主控制。** 成员私有数据和执行能力的访问经明确的工具、路径与授权边界；提案和评估结果不自动授权生产执行。具体隔离保证及现有缺口见执行装置、能力和演武场 design。

## 当前实现：包与进程

当前常规部署是一个 asyncio 服务端进程（`tlon dream`），以 aiohttp 提供单端口 REST + SSE，并在 `/ui/` 托管 WebUI。Operator 是独立的 TypeScript/Ink 客户端，Python SDK 和 Telegram 也是协议客户端。默认监听 `127.0.0.1:8080`。这些是当前选择，改变仍需设计评估。

```text
WebUI / Operator / Python SDK / Telegram
                 │ REST + SSE
            tlon_server
                 │ domain facades
              tlon.api
                 │
   agents ── organization / memory / tools / sandbox
                 │
              tlon.base

   tlon ──使用──> tlosure（宿主无关语言核心）
   tlon_launcher 装配进程入口与客户端命令
```

包依赖的执行契约在 [pyproject.toml](../pyproject.toml) 的 import-linter 配置中；图是职责概览，不替代完整依赖表。

| 包 | 职责与依赖边界 |
|---|---|
| `tlosure` | EDN、求值、校验、通用词汇；不依赖 tlon |
| `tlon.base` | 配置、路径、事件与观测基础设施；不依赖上层领域 |
| `tlon.organization` | 邮局、邮箱、地址解析与人类/外部适配；不依赖其他业务域 |
| `tlon.memory` | claim 存储、蒸馏与检索；不依赖 agents/organization/tools/sandbox |
| `tlon.sandbox` | 执行装置与资源监控；不依赖 agents/organization/memory/tools |
| `tlon.operations` | 共享操作契约、成员作用域调用与 DSL/Tool 薄入口，生产宿主操作已统一装配，不依赖 agents/server/client/launcher。见 [reference](reference/operations-reference.md) |
| `tlon.tools` | 模型工具协议、结果与溢出文件适配；业务定义和执行位于 `tlon.operations`，不得反向依赖 agents/server/client/launcher/prototypes |
| `tlon.agents` | 成员装配、调度、会话循环和策略接入；不依赖 server/client/launcher/prototypes |
| `tlon.prototypes` | 随包携带的原型数据，启动时播种；不是原型管理服务 |
| `tlon.api` | 当前有 members/mail/prototypes/observability/configs/events/arena 七个 facade 与 runtime 装配；不依赖 server/client/launcher |
| `tlon_server` | HTTP、客户端会话、通知、WebUI 托管；直接 runtime import 限于 api/base |
| `tlon_client` | SDK 与 Telegram；不依赖 tlon、server、launcher |
| `tlon_launcher` | dream/ops/telegram 命令；通过 server/client 和 base 装配，不直摸 runtime 或 api |

## 当前实现：装配与生命周期

进程入口创建 `TlonPaths` 与 `Settings` 快照。[ServerApp](../src/tlon_server/serverapp.py) 初始化进程级 tracing/metrics，再调用 [build_runtime](../src/tlon/api/bootstrap.py)。runtime 装配包含播种原型与技能、基础设施、邮局、注册表、退休服务、Adam 与通道适配器，并返回 facade。首启不自动创建标准团队。

Adam 作为内置策略成员，持有同一个 `AgentRegistry`，通过共享 `LifecycleService` 查询 boot 候选并发布启停请求；宿主状态查询仍由 `RegistryQueries` 提供。Adam 不进入注册表的激活表和生命周期枚举，但列入公开成员寻址目录。注册表的调度准入不要求成员配置存在；Adam 没有普通成员状态快照时不发布调度状态事件。

serving 侧在 facade 上装配客户端会话、通知服务、通讯录和 HTTP 服务；`start()` 才打开监听端口。关闭时先停止 serving，再关闭 runtime 组件，最后结束进程级观测设施。详细顺序、异常清理及资源所有权见 [基础设施设计](design/infrastructure.md)。成员的启动与退休不是进程启停，见 [运行时](design/runtime.md) 和 [成员模型](design/unified-member-model.md)。

## 当前实现：一封邮件的协作链路

1. 人类客户端、agent 工具、DSL、周期任务及外部适配器通过各自入口调用同一个邮局。
2. 邮局校验收件人并提交邮件、收件归属和请求记录，随后发布变化通知。提交语义与外部网关尝试的结果分开。
3. 注册表检查订阅与四态资格，成员串行工作者受理事件对应的冻结模块入口。
4. ProgramDriver 执行普通函数，Operation 回执和终态写入 ProgramStore；交互由只读投影提供。成员主动读取和标读邮件，开启 session 本身不等于已读或完成任务。
5. 回信再次经过邮局。人类客户端通过 SSE 获得通知，通过邮箱接口读取事实；断线恢复依靠快照补齐，不能只依赖实时流。
6. 已完成 session 可被蒸馏为私有长期记忆；记忆不是邮件，也不自动成为其他成员共享的知识。

邮局事务见 [mail](design/mail.md)，唤醒、责任集、预算与 journal 见 [runtime](design/runtime.md)，客户端同步见 [server-protocol](design/server-protocol.md) 和 [webui](design/webui.md)。

## 数据所有权

以下是当前权威与派生关系，不是完整文件清单。实际路径统一由 [TlonPaths](../src/tlon/base/paths.py) 构造，目录树与环境变量见 [配置参考](reference/config-reference.md)。

| 数据 | 当前归属 | 派生关系与边界 |
|---|---|---|
| 行为规则 | 成员 `behavior.tlon` | 解析为 MemberSpec；规则相关运行属性由它派生 |
| 模型与执行配置 | 成员 `agent.yaml` | 原型实例化可投影初始配置；运行时授权不随成员自改 spec 提升 |
| 主观身份与联系簿 | 成员配置文件 | 与客观地址、行为约束分开；各文件写权限见 identity |
| 邮件、归属、已读、请求幂等 | 共享 `mail.sqlite3`，邮局负责写入 | Mailbox 是成员视图；SSE/EventBus 是通知，不是账本 |
| Session 事件与生命周期 | 每成员 `sessions.sqlite3` | journal 保存模型历史与推进协议；操作提交核对 ProgramStore，旧 JSONL 仅作离线档案 |
| 根调用与操作回执 | 每成员 `program.sqlite3` 的 roots/invocations | DSL 与模型 Tool 共用；核对路径、参数、定义与构建指纹，state 与同次回执原子提交 |
| 长期记忆 | 成员 `claims.jsonl` | 追加优先但允许删除/重写；向量索引可重建，主动写入的 claim 不能笼统视作 journal 的可重建副本 |
| 原型与能力 | 受信库、staging 库 | 提案经准入后使用；评审和演武报告是证据，不自动批准 |
| 社会政策 | `policy.tlon` 与内嵌默认政策 | 宿主物化事实后求值；可写身份与失败处理见 society-policy |
| 运行状态、指标、追踪 | runtime 状态与观测设施 | 是观测或派生结果，不能替代邮件和 journal 的权威事实 |

## 约束如何验证

| 规则 | 验证方式与范围 |
|---|---|
| 包依赖方向 | import-linter；检查 import 边界，不证明所有调用、注入和权限规则 |
| 模块初始化纯度与运行身份 | 静态校验、ProgramStore 冻结与回执测试 |
| runtime 业务隔离 | [多 runtime 集成测试](../src/tests/integration/test_multi_runtime.py)；不包括进程级观测设施完全隔离 |
| 数据与跨域行为契约 | 领域测试、集成测试、隔离 dream + SDK 冒烟与文档复核 |

统一质量门为 `scripts/check_all.sh`。禁止 re-export 属于开发规范，由脚本检查；ruff、类型检查和测试也由同一质量门编排。自动检查覆盖不到的设计边界仍需评审，不能将「检查全绿」等同于所有架构声明均已被机器证明。

## 已落地的共享操作边界

邮件、文件、Shell、时间的 Operation 保存唯一输入/输出 schema，由 DSLAdapter 和 OperationTool 投影为两种入口。成员作用域服务负责领域行为；工具协议层不保存第二份邮件规则或 Shell 任务表。文件与执行等级在领域执行时检查；在本次切换的装置与时间域，L0/L1/L2 均可读时间和读写自己的 workspace 文件（无需 Docker），L2 加 Shell；workspace 外文件访问仍受装置权限约束，邮件及其他非装置操作沿各自既有约束装配。P4a 将两入口的叶子操作 intent 与回执统一到 ProgramStore；session journal 负责会话历史，结果不明保留 unknown。

EPIC A、P5 #193 和 P6 #194 已合并。当前调用契约见 [Operation reference](reference/operations-reference.md)，阶段状态见[实施计划](design/operations-plan.md)，旧机制审计见[操作机制审计](design/operations-audit.md)。


P3b 已将记忆/会话回读、任务、人格/联系人、只读观测及 state-get/state-put 接入相同 Operation 目录。成员身份由宿主绑定，操作输入不能改写拥有者；公开观察与私有资源保持各自边界。state-put 的两入口复用同一 ProgramStore 原子状态及 invocation 回执，TaskManager 已移至 `tlon.tasks`。P3c 已通过 #188 合并，统一生命周期、模型和提案操作并删除旧 Tool 机制。P4a 固定调用身份、契约与构建指纹，恢复拒绝漂移与旧格式；当前边界见 [调用恢复](design/invocation-recovery.md)。P4b 将两入口及其内部模型调用接入同一持久预算账本：程序 output 保留输出预留，session total 覆盖 stage、压缩及嵌套模型并按可靠实际 usage 结算，未知保留预留。取消约束当前进程任务树，deadline 与额度冻结跨重启保留；成员写 gate 按声明顺序执行写入，独立读取可并发。装配容器改名 MemberServices，旧宿主查询直接声明非 pure，详见 [执行作用域](design/execution-scope.md)。数据库格式 user_version=2，旧格式拒绝，无迁移。



## 定义、流程与执行实例

Operation 是宿主操作的单一契约，DSLAdapter 与 OperationTool 共用执行器。Capability 与复合 Operation 已删除；Behavior、Prototype 和 Agent 的现有职责保持不变。

工作程序自行判断普通数据条件；读取时钟、模型、邮箱或状态是明确的宿主调用。模块初始化及社会策略只接受纯计算。

模型 tools 尽量保持固定，以减少请求前缀变化。不同成员仍可因实际服务、执行装置、信任状态和批准依赖得到不同的稳定目录；同一会话根冻结操作定义，阶段或轮次不会主动裁剪 tools。P7 的统一授权名单、请求级动态视图与运行中撤权机制暂停，当前没有这些保证。固定 tools 不表示权限永久不变；未来授权需求应独立设计，也不能以工具可见性代替宿主检查。本次文档收口没有增加缓存实现或测量缓存命中率。
