# Tlon 系统架构

> 统一成员模型（ADR 0006，2026-08 实施）：成员只有一种；排空一律是睡眠、档案归档、成员永不删除。模型总览见 [docs/design/unified-member-model.md](./design/unified-member-model.md)。

Tlon 是一个单进程 asyncio 多智能体协作系统：一组 agent 以邮件为唯一通信方式，模拟一个软件工程团队；人类用户以普通成员身份加入同一个邮件网络。本文描述当前实现的整体结构、依赖方向、装配顺序与运行时数据流。

## 总览

```
┌──────────────────────────── 客户端层 ────────────────────────────┐
│  apps/operator   TypeScript/Ink TUI（tlon ops 拉起的 Node 进程）  │
│  app/clients/sdk TlonClient —— 纯协议层 SDK，只依赖 tlon.base     │
│  任意 HTTP 客户端（REST + SSE 即可接入）                          │
└──────────────┬───────────────────────────────┬───────────────────┘
               │ HTTP REST                     │ SSE（GET /events）
┌──────────────▼───────────────────────────────▼───────────────────┐
│ 服务端装配层 app/server                                           │
│   ServerApp —— 唯一装配者，按拓扑序构造全部组件（DI）             │
│   api/rest/ RESTServer —— aiohttp，REST + SSE，无 WebSocket       │
│   core/ —— ClientGateway / SessionManager / NotificationService / │
│            MemberDirectory                                        │
├──────────────────────────── 领域层 ──────────────────────────────┤
│ agents —— AgentRegistry（机制）/ Adam（策略）/ MemberRuntime /    │
│           SessionScheduler / AgentSession / LoopDriver / stages / │
│           EffectExecutor                                          │
│ organization —— mail（Mail / Mailbox / MailDelivery / Resolver）、│
│           messaging（HumanAdapter）                               │
│ tools —— mail / filesystem / shell / memory / tasks / agents /    │
│          prototypes / system（经 ports.py 依赖倒置）               │
├────────────────────────── 基础设施层 ────────────────────────────┤
│ base —— Component / EventBus / ConfigService / TlonPaths /        │
│         LLMClient / tracing / metrics / logger                    │
│ memory —— 工作记忆 + 情景记忆（MemoryManager）                     │
│ sandbox —— DockerRuntime                                          │
└───────────────────────────────────────────────────────────────────┘
         EventBus 横贯各层：组件间通信的唯一通道（发布/订阅）
```

整个系统只有一个服务端进程（`tlon dream`），入口 [src/tlon/app/launcher/main.py](../src/tlon/app/launcher/main.py) 的 `main`，最终由 [app/server/serverapp.py](../src/tlon/app/server/serverapp.py) 的 `run_server` 创建并运行 `ServerApp`。对外只暴露一个 aiohttp HTTP 端口：REST + SSE，默认 `127.0.0.1:8080`，路由集中在 [app/server/api/rest/](../src/tlon/app/server/api/rest) 包的 `RESTServer`（`rest/app.py` 的 `create_app` 注册全部路由，handler 按域拆在 `rest/` 各模块），没有 WebSocket。

REST 接口一览（均在 `rest/app.py` 的 `create_app` 注册）：

- 消息与邮箱：`POST /messages`（人类发信）、`GET /inbox`（读自己的邮箱）
- 实时推送：`GET /events`（SSE 长连接）
- 成员与会话：`GET /agents`、`GET /agents/{agent_id}`、`GET /participants`、`GET /sessions`、`POST /sessions`
- 配置：`GET /config`、`GET /admin/config/agents`、`POST /admin/config/agents/{agent_id}/reload`（热重载）
- 可观测性：`GET /traces`、`GET /traces/{trace_id}`、`GET /metrics`、`GET /agents/{agent_id}/logs`、`GET /logs`
- 其他：`GET /health`；`GET /test/ping`、`POST /test/echo` 仅在 debug 模式注册

客户端侧有两类接入方式：`tlon ops`（[app/launcher/commands/ops.py](../src/tlon/app/launcher/commands/ops.py)）定位仓库内 [apps/operator/](../apps/operator) 并以 Node 拉起这个 TypeScript/Ink TUI——优先用构建产物 `dist/cli.js`，未构建时回退 tsx 开发模式；TUI 与任意第三方客户端一样，只经 REST + SSE 与服务端通信。[app/clients/sdk/](../src/tlon/app/clients/sdk) 是给外部 Python 客户端用的 SDK（`client.py` 的客户端、`adapter.py` 的事件适配），契约上只依赖 `tlon.base`，不触碰服务端内部。

成员配置的创建与审批走服务端管理面：首启只播种原型库与技能（[agents/bootstrap.py](../src/tlon/agents/bootstrap.py)），**空社会不再自动初始化标准团队**（ADR 0016）——成员由人类经管理面创建（创建时选模型，成员级 LLM 配置），成员/原型管理经 ops 的管理 API（`POST/PATCH/DELETE /admin/config/agents`、`/admin/prototypes/staging` 审批），`POST /admin/config/agents/{agent_id}/reload` 热重载单个 agent 配置。

## 分层与依赖方向

依赖方向由两道关卡强制：pyproject.toml 里的 import-linter 契约（`uv run lint-imports`）和 [scripts/](../scripts) 下的静态检查脚本。各包的职责与允许依赖：

| 包 | 职责 | 允许依赖 | 强制手段 |
|---|---|---|---|
| `tlon.base` | 组件基类、事件总线、配置、路径、日志、LLM 客户端、tracing/metrics | 不依赖任何上层 | import-linter「base 不依赖任何上层域」 |
| `tlon.memory` | 工作/情景记忆存储与摘要 | 仅 base | import-linter |
| `tlon.organization` | 邮件模型、邮箱、投递、地址解析、人类适配器 | 仅 base | import-linter |
| `tlon.sandbox` | Docker 沙箱运行时 | 仅 base | import-linter |
| `tlon.tools` | agent 可用工具（邮件/文件/Shell/记忆/任务/生命周期/原型） | base、sandbox；不依赖 agents/app/prototypes | import-linter；生命周期与原型工具经 [tools/agents/ports.py](../src/tlon/tools/agents/ports.py)、[tools/prototypes/ports.py](../src/tlon/tools/prototypes/ports.py) 声明端口，由装配层注入实现（依赖倒置） |
| `tlon.agents` | 领域核心：注册表、成员运行时、认知循环 | base/memory/organization/tools/sandbox；不依赖 app/prototypes | import-linter |
| `tlon.prototypes` | 内置行为原型库（随包携带，启动时播种到 `~/.tlon/prototypes/`） | —（无独立契约） | — |
| `tlon.app.clients.sdk` | 外部客户端用的纯协议层 SDK | 仅 tlon.base | import-linter「app.clients.sdk 只依赖 tlon.base」 |
| `tlon.app`（server/launcher） | 装配层与命令行入口 | 下层全部 | 无显式契约，靠分层惯例 |

除 import-linter 外，[scripts/](../scripts) 下还有三个 AST 检查脚本：

- [scripts/check_architecture.py](../scripts/check_architecture.py)：组件依赖只能构造器注入，禁止 setter/property 注入、运行时替换组件、隐式全局依赖。
- [scripts/check_no_reexport.py](../scripts/check_no_reexport.py)：`__init__.py` 禁止 re-export，防止 IDE 重命名重构跨包静默断裂。
- [scripts/check_stage_purity.py](../scripts/check_stage_purity.py)：认知循环的 stage 模块必须是纯变换，禁止直接改 session 状态（见下文「effects-as-data」）。

组件层的规则本身写在基类文档里：[base/component.py](../src/tlon/base/component.py) 的 `Component` 要求所有依赖构造器注入、组件间只经 EventBus 通信、依赖图必须是 DAG；生命周期分 `__init__`（只存依赖）→ `initialize()`（订阅事件、起后台任务）→ `shutdown()`（反序清理）三段。

事件总线是 [base/events/event_bus.py](../src/tlon/base/events/event_bus.py) 的 `EventBus`：每个订阅者一条有界 `asyncio.Queue`（上限 `SUBSCRIBER_QUEUE_MAX_SIZE = 1000`）加一个长驻消费 task，`publish` 只做入队、从不阻塞；慢订阅者只会积压自己的队列，溢出时丢新事件并记 `event_queue_overflow` 警告。事件类型枚举在 [base/events/event.py](../src/tlon/base/events/event.py) 的 `EventType`。语义细节：单个订阅者内严格按发布顺序逐个处理（FIFO）；订阅者之间不保证顺序；处理器抛异常只记日志，不影响本订阅者的后续事件和其他订阅者；`unsubscribe` 会取消消费 task 并丢弃队列里未消费的事件；对未运行的总线 `publish` 直接抛 `RuntimeError`（fail fast）。

## 启动与装配顺序

`ServerApp.initialize()`（[app/server/serverapp.py](../src/tlon/app/server/serverapp.py)）是唯一的装配点，按依赖拓扑序构造组件，此阶段不开任何网络端口：

1. **可观测性先行**：`init_tracing` + `init_metrics`，先于任何 span 创建。
2. **基础设施**：`seed_prototypes()`（幂等地把内置原型写入 `~/.tlon/prototypes/`，须在任何 agent 启动前）→ `EventBus` → `ConfigService` → `MetricsService` → `FileTraceStore`。
3. **领域组件**：`AgentRegistry`（此时只按 ConfigService 里的配置 `register`，不启动 agent）→ Adam（[agents/adam.py](../src/tlon/agents/adam.py) 的 `make_adam`，在注册表之后初始化并随后发出 boot 启动请求）→ `AdapterManager`（人类适配器生命周期）→ `MemberDirectory`（成员通讯录）→ `MailDelivery`（邮件投递）。
4. **客户端网关**：`SessionManager` → `NotificationService` → `ClientGateway`（协调各组件的门面）。
5. **API 层**：构造 `RESTServer`（注入 ClientGateway 及各核心组件），尚未开始监听。
6. 发布 `SYSTEM_STARTUP` 事件。

随后 `ServerApp.start()` 调用 `RESTServer.start()` 才真正监听端口，然后阻塞在 shutdown 信号上。`shutdown()` 严格反序：先停 RESTServer，Adam（PID 1）先于它驱动的注册表停，EventBus 几乎最后停，tracing/metrics 收尾。

组件实例本身就是 DI 容器：`ServerApp._components` 字典持有全部组件，没有额外的框架。装配阶段不开任何端口；装配失败时 `run_server` 返回非零退出码，并在 `finally` 里走一遍 `shutdown()` 做清理。

## 运行时数据流：一封邮件的全链路

以「人类从客户端发一封邮件给 agent，agent 处理后回邮」为例：

1. **人类发信（REST 入口）**。客户端 `POST /messages`，由 `rest/mail.py` 的 `handle_send_message` 处理，字段校验的唯一副本在 [app/server/api/send_mail.py](../src/tlon/app/server/api/send_mail.py) 的 `parse_send_mail_fields`。发件人身份取自请求体的 `username`；若该人类没有在线适配器，`AdapterManager` 会建一个临时的 `HumanAdapter`（[organization/messaging/human_adapter.py](../src/tlon/organization/messaging/human_adapter.py)）。适配器的 `speak` 要求显式 `to`/`cc`，零可解析收件人的邮件直接拒绝；合法则构造 `Mail`（[organization/mail/mail.py](../src/tlon/organization/mail/mail.py)）并向 EventBus 发布 `MAIL_NEW`。

   **agent 发信只有一个出口**：[tools/mail/send_email.py](../src/tlon/tools/mail/send_email.py) 的 `SendEmailTool`。认知循环里 act stage 把工具调用声明进 `StageOutcome`，由 `EffectExecutor`（[agents/executor.py](../src/tlon/agents/executor.py)）执行其中的 `MailEffect`——构造 `Mail`、发布 `MAIL_NEW`，与人类发信汇入同一条管道。这一步失败（`Mail` 构造被拒绝或 `MAIL_NEW` 发布失败）时，`EffectExecutor` 会把一封来自 `postmaster@tlon.internal` 的发送失败通知直接写进发件 agent 自己的收件箱并发布 `MAIL_DELIVERED`——与投递层退信同风格，但不经过 `MAIL_NEW`（通知路径不能依赖刚刚失败的那次发布），agent 在下一轮认知循环里读到它。

2. **投递落盘**。`MailDelivery`（[organization/mail/delivery.py](../src/tlon/organization/mail/delivery.py)）订阅 `MAIL_NEW`，对每封邮件：把 `all@tlon.internal` 展开为全部已注册 agent 加全部已知人类；用 [organization/mail/resolver.py](../src/tlon/organization/mail/resolver.py) 的 `RecipientResolver` 把每个地址解析成成员；写入发件人的 `sent.jsonl` 和每个有效收件人的 `inbox.jsonl`（邮箱目录经 [base/paths.py](../src/tlon/base/paths.py) 的 `TlonPaths.member_mail_dir` 解析）；最后按收件人逐个发布 `MAIL_DELIVERED`。进程内幂等：重复投递同一 `mail_id` 不会写第二份副本。解析不到任何人的地址会收到一封来自 `postmaster@tlon.internal` 的退信，落在发件人收件箱里——LLM 发件人可以自己读退信改地址。

3. **收件人是 agent：唤醒或入队**。
   - 若收件人未在运行，`AgentRegistry._on_mail_delivered`（[agents/agent_registry.py](../src/tlon/agents/agent_registry.py)）检查其 spec 是否声明了 `:on :mail-delivered`，是则发布 `AGENT_START_REQUEST{reason="wake"}` 将其唤醒。唤醒不带触发事件——邮件已持久化在收件箱，成员启动时自行扫描未读邮件。
   - 若收件人在运行，`MemberRuntime`（[agents/member_runtime.py](../src/tlon/agents/member_runtime.py)）收到 `MAIL_DELIVERED`：先过 `:when` 门控，命中认知规则则把邮件排入 `SessionScheduler` 的有界队列（队列满时丢新邮件、保持未读，由重扫任务补拾）；自己发给自己的邮件直接标记已读、永不处理。
   - 启动未读扫描对积压邮件做节流（issue ）：同一 thread 的未读合并为一个 session（线程内最新一封为代表，更早的标记已读、由该 session 的 mail-history 上下文覆盖）；有界队列装不下的溢出邮件保持完全未读（确定性规则也一并推迟），由重扫在调度器腾出队列容量后分批补拾，直到无延期为止——积压只排队，不丢信。
   - 每个认知成员有且只有一个后台重扫任务，两种节拍合一：有延期时按 5 秒轮询、随调度器排空分批补拾溢出；无延期时按 `mail_rescan_interval_seconds`（默认 15 分钟）周期重扫未读。boot 成员（`:on :boot`）从不睡眠、从不重新 boot，live 路径被丢的邮件靠这个周期重扫拾起，不用等进程重启。扫描本身幂等且不调 LLM：只 enqueue，`_scan_pending` 跳过已有 session 认领的邮件，无未读时什么都不做。

4. **认知循环**。`SessionScheduler`（[agents/scheduler.py](../src/tlon/agents/scheduler.py)）把队列里的邮件排成 `AgentSession`（[agents/session.py](../src/tlon/agents/session.py)），交给 `LoopDriver`（[agents/loop_driver.py](../src/tlon/agents/loop_driver.py)）逐步推进：think / plan / act / critique / revise 五个 stage（[agents/stages/](../src/tlon/agents/stages) 下各文件）是纯函数 `Session × StageServices → StageOutcome`，循环有 `max_iterations` 硬上限，耗尽时不丢中间成果：引擎用一次额外的 LLM 调用把 session 已完成的工作蒸馏成给发件人的回信（说明做了什么、做到哪、为何没做完），蒸馏失败则回落固定文案。stage 声明的外部效果由 `EffectExecutor` 真正执行：发邮件（回到第 2 步的 `MAIL_NEW`，形成 agent 间多轮通信）、保存交互记忆、推进收件箱 cursor。stage 跑哪些、以什么顺序跑，由 spec 里 loop 规则的 `stages` 向量决定；`act` 只声明工具分发列表，并发（每个工具调用一个后台 task）是引擎的。

   一个 agent 同时可以有多个进行中的 session（多封邮件、多个线程），上限 `max_concurrent_sessions`（默认 4），超出的邮件留在有界队列里等 session 完成再逐个补位；`SessionScheduler` 负责逐个推进、回收完成与过期的 session；session 因空闲超时或 stage 异常被终止时，调度器会给触发邮件的发件人回一封固定文案的失败通知（经 `LoopDriver` 的 MailEffect → `EffectExecutor` 管道，与正常回信同一出口），保证「邮件必有回音」。agent 的整体状态在读取时从 session 集合派生，不靠回调维护。反思引擎（[agents/reflection.py](../src/tlon/agents/reflection.py) 的 `ReflectionEngine`）按交互计数触发，由调度器在循环间隙经注入的 `check_reflection` 委托检查；反思跑在引擎持有的后台 task 里——进行中计入成员活跃（不会被判空闲 drain 掉），成员 shutdown 时统一取消。

   每封邮件开一个新 session，但同一线程的往复不是失忆重逢：`ContextBuilder`（[agents/context_builder.py](../src/tlon/agents/context_builder.py)）组装上下文时，`(thread-recap)` section 从 session journal（ADR 0007）确定性重建本线程最近一个已完成 session 的简报（触发、调了哪些工具、结论是什么），不调 LLM、不等批触发摘要；与 `mail-history` 的邮件原文互补——原文给「说了什么」，简报给「我做了什么」。只注入最近一轮，成本不随线程长度膨胀（issue ）。

5. **收件人是人类：推送或拉取**。`HumanAdapter` 与其他成员一样订阅 `MAIL_DELIVERED`，把发给自己成员 id 的邮件 fan-out 给 attach 上来的客户端会话；客户端通过 `GET /events` 的 SSE 长连接实时收信（推送侧由 [app/server/core/notification_service.py](../src/tlon/app/server/core/notification_service.py) 的 `NotificationService` 管理），也可随时 `GET /inbox` 直接读邮箱文件。人类离线时邮件留在其持久邮箱里，上线后再读。

全链路的背压策略一致：EventBus 的 per-subscriber 队列、SessionScheduler 的邮件队列都是有界的，溢出时丢新保旧并保未读，绝不阻塞总线。

链路追踪贯穿全程：发信时注入当前 trace，唤醒请求加入投递的 trace 树（`AgentRegistry._fulfill_wake` 续接同一 trace），span 由 [base/tracing/trace_store.py](../src/tlon/base/tracing/trace_store.py) 的 `FileTraceStore` 落盘到 `~/.tlon/shared/traces/` 的 JSONL 文件，经 `GET /traces` 查询；也可配置 OTLP endpoint 外发（可选依赖 `opentelemetry-exporter-otlp-proto-http`）。

## 关键设计要点

### 策略/机制分离

`AgentRegistry`（[agents/agent_registry.py](../src/tlon/agents/agent_registry.py)）是纯机制：装配与运行表、启动/停止请求的幂等执行（wake lock、`_waking` 去重）、空闲*事实*检测（发布 `AGENT_IDLE`，不决定后果）、成员的 spawn/stop（统一成员模型，ADR 0006：spawn 出的成员与手建成员同构，停止/排空一律是睡眠，永不删除）。spawn 预算是硬编码默认值：全局同时运行上限 `max_activations = 8`、spawn 深度 `max_spawn_depth = 3`、每 (spawner, session) 配额 `session_spawn_quota = 2`。三者只有 (spawner, session) 配额是账本，且落盘在 `~/.tlon/shared/spawn-quota.json`（原子替换写入，损坏时告警并从头开始，绝不阻碍启动）：session 本身经 journal 重放跨重启存活（ADR 0007），纯内存账本会让 spawner 靠重启刷满配额，防失控闸失效。`max_activations` 是运行时事实（重启后运行表本来就空，持久化反而把死成员的激活数带进新进程）、`max_spawn_depth` 从成员 `agent.yaml` 里已持久化的 lineage（`spawn_depth`）派生，两者无需也不应落盘。

策略只有一个实例：Adam（[agents/adam.py](../src/tlon/agents/adam.py)），agent 世界的内置 init 进程（PID 1）。策略是数据而不是代码——`ADAM_SPEC_EDN` 是一段内置 MemberSpec 文本，四条规则：`:on :boot` 时对候选集合发 `AGENT_START_REQUEST{reason="boot"}`，`:on :agent-idle` 时把空闲事实转成 `AGENT_STOP_REQUEST{reason="drain"}`，`:on :agent-spawned` 时给新成员发出生欢迎信（issue ：出生社会化是 spec 描述的行为，不是专门机制——名册由宿主查询 `member-roster` 现算，惯例是静态文本），`:on :mail-delivered` 时对寄给 Adam 的信回固定确认信（issue  守护者信箱：`:when` 用 `addressed-to-me?` 收窄到本人、用 `from?` 排除 postmaster 防退信循环；Adam 带真实邮箱 `~/.tlon/adam/mail/`，已读标记与 boot 未读重放免费获得）。Adam 是一个普通的 `MemberRuntime` 实例，不进注册表、不出现在 MemberDirectory；依赖单向（Adam import 注册表，反之永不），DAG 保持无环。

wake-on-mail 不是策略线而是机制：注册表从成员 spec 派生唤醒资格（声明了 `:on :mail-delivered` 的睡眠收件人会被唤醒），不需要 Adam 参与。

### spec 是唯一权威

每个成员的行为完全由一份 spec 描述：[agents/member_spec.py](../src/tlon/agents/member_spec.py) 的 `MemberSpec`（规则 = `:on` + `:when` + 可选 `:loop` + `:actions`），落盘为每成员唯一的 `config/behavior.tlon` 文件。[agents/member_runtime.py](../src/tlon/agents/member_runtime.py) 的 `MemberRuntime` 是统一解释器；认知引擎（LoopDriver + SessionScheduler + 记忆 + 反思）只对带 `:loop` 规则的成员挂载，无 `:loop` 的规则只跑 `:actions`。boot 集合、lazy 集合、wake 门控全部从 spec 的 `:on` 规则派生。

### effects-as-data

认知循环里每个 stage 是纯变换，所有对外部状态的意图都声明在返回的 `StageOutcome`（[agents/stages/outcome.py](../src/tlon/agents/stages/outcome.py)）里。`LoopDriver` 负责应用 session 本地字段，外部效果（`MailEffect` 发信、交互记忆保存、inbox cursor 推进、工具并发分发）由 `EffectExecutor` 执行。stage 不允许碰 session 的 mutator 方法，这条纯度由 [scripts/check_stage_purity.py](../scripts/check_stage_purity.py) 静态强制——引擎与策略的边界是机器可检查的。

### mailbox 即队列

邮箱是磁盘上的三个文件（`inbox.jsonl` 追加写、`sent.jsonl` 追加写、`inbox.cursor` 已读 id 集合），实现见 [organization/mail/mailbox.py](../src/tlon/organization/mail/mailbox.py) 的 `Mailbox`。邮件先持久化、后处理：唤醒不携带事件载荷，成员启动时扫描未读；运行中队列溢出丢的是「新事件的拷贝」，磁盘上的未读事实不变，由成员的后台重扫任务周期补拾（下次启动也会补扫）。同一目录的多个 `Mailbox` 实例（投递缓存、REST `/inbox`、agent 运行时）共享一把按目录的 asyncio 锁（有界 LRU），进程内写互斥。未读扫描是增量的：按目录的扫描缓存（有界 LRU，与锁表同纪律）记住已解析的字节偏移与解析结果，重复扫描只解析追加部分；文件被替换（inode 变化）或截断时回落全量扫描，崩溃留下的半行不消费、下次重试。

### DAG 组件装配

全部组件继承 `Component`，依赖只走构造器，通信只走 EventBus；`ServerApp` 是唯一装配者。这条规则同时由 [base/component.py](../src/tlon/base/component.py) 的约定文档、[scripts/check_architecture.py](../scripts/check_architecture.py) 的静态检查和 import-linter 的依赖方向契约三层维持。

### 记忆与沙箱

`tlon.memory` 的 `MemoryManager`（[memory/manager.py](../src/tlon/memory/manager.py)）为每个 agent 管理两层存储：工作记忆（[memory/working_store.py](../src/tlon/memory/working_store.py)，`interactions.jsonl`）与情景记忆（[memory/episodic_store.py](../src/tlon/memory/episodic_store.py)，`summaries.jsonl` + `facts.jsonl`），摘要由 [memory/summarizer.py](../src/tlon/memory/summarizer.py) 生成。agent 侧通过 `tlon.tools.memory` 下的工具（`remember_fact`、`search_memories`、`forget_memory`、`compact`）读写自己的记忆。`tlon.sandbox` 的 `DockerRuntime`（[sandbox/docker_runtime.py](../src/tlon/sandbox/docker_runtime.py)）封装 Docker 容器执行，供 Shell 类工具使用。

### 工具层与依赖倒置

`tlon.tools` 按域分包：`mail`（`sendEmail`，agent 唯一发信出口）、`filesystem`（读/写/编辑/列目录/检索文件）、`shell`（`execute_bash` 及后台任务查询）、[memory](../src/tlon/memory)（记忆读写）、`tasks`（`TaskManager` 及任务生命周期工具）、[agents](../src/tlon/agents)（`spawnAgent` / `stopAgent` / `listAgents` / `writePrototype`）、`prototypes`（`listPrototypes`）、`system`（`getCurrentTime`，宿主机本地时间，不依赖沙箱）。工具的实例化与注册发生在装配成员能力时（[agents/assembly.py](../src/tlon/agents/assembly.py) 的 `assemble_cognitive`，由 `AgentRegistry._start_agent` 调用）。

生命周期与原型工具天然需要触碰 agents/prototypes 层，但契约禁止 tools 反向依赖；解法是依赖倒置——端口与异常（`SpawnRejectedError`、`StopRejectedError` 等）声明在 [tools/agents/ports.py](../src/tlon/tools/agents/ports.py) 和 [tools/prototypes/ports.py](../src/tlon/tools/prototypes/ports.py)，装配层（注册表）把自身实现注入工具实例，import 方向保持 tools → 端口，不倒流。

## 存储布局

所有状态落盘在 `~/.tlon/`（可用 `$TLON_HOME` 覆盖），路径的唯一权威是 [base/paths.py](../src/tlon/base/paths.py) 的 `TlonPaths`（模块级单例 `paths`）；任何模块不应硬编码目录结构。仓库根目录下的 [data/](../data) 是历史遗留，当前代码不引用它。

`$TLON_HOME` 只在指向一个已存在的目录时才生效，否则回落到 `~/.tlon`（不存在则创建）——见 `paths.py` 的 `_resolve_tlon_home`。

```
~/.tlon/
├── shared/
│   ├── traces/
│   │   └── trace-{uuid}.jsonl
│   └── logs/
│       └── server-{date}.jsonl
├── prototypes/              # 行为原型库（启动时从 tlon.prototypes 播种）
├── prototypes-staging/      # LLM 撰写、待审批的原型
├── {agent_id}/
│   ├── config/
│   │   ├── agent.yaml
│   │   ├── behavior.tlon    # 成员 spec（唯一权威）
│   │   ├── IDENTITY.md / SOUL.md / RULES.md
│   ├── memory/
│   │   ├── config.json
│   │   ├── working/interactions.jsonl
│   │   └── episodic/summaries.jsonl、facts.jsonl
│   ├── tasks.jsonl
│   ├── workspace/
│   └── mail/
│       ├── inbox.jsonl      # 追加写
│       ├── sent.jsonl       # 追加写
│       └── inbox.cursor     # 已读邮件 id，每行一个
└── human-{username}/
    └── mail/                # 人类的持久邮箱，布局同上
```

人类成员 id 为 `human:<username>`，映射到目录时冒号替换为连字符（只发生在 `TlonPaths.member_mail_dir` 一处）。

容器视角：`{agent_id}/workspace/` 以读写方式 bind-mount 为容器工作目录（默认 `/workspace`），整个 `{agent_id}/` 家目录再以**只读**方式挂载到容器的 `/tlon-home`（issue ，挂载定义在 [sandbox/docker_runtime.py](../src/tlon/sandbox/docker_runtime.py) 的 `_create_container`）——agent 经 readFile/listDirectory/searchFiles 读自己的行为定义、记忆与邮箱（行为即数据，自我感知是自进化的前提），写/编辑工具的白名单不含 `/tlon-home`，行为变更走原型提案通道。每个容器只挂自己的家目录，其他成员的家目录从不进入容器，跨 agent 读取在结构上不可能。注意：容器跨睡眠/重启保留（ADR 0006），已存在的旧容器要带新挂载需先移除重建。

`config/` 下的三个 Markdown 人格文件（`IDENTITY.md`、`SOUL.md`、`RULES.md`）在构造 prompt 时由 [agents/context_builder.py](../src/tlon/agents/context_builder.py) 的 `ContextBuilder` 读入；`behavior.tlon` 则经 [agents/loader.py](../src/tlon/agents/loader.py) 的 `load_member_spec` 解析为 `MemberSpec` 并在加载时校验。

## 架构约束检查工具

| 工具 | 检查内容 |
|---|---|
| `uv run lint-imports` | pyproject.toml 中 import-linter 契约：各包依赖方向（base 不依赖上层、memory/organization/sandbox 只依赖 base、tools 不反向依赖 agents/app/prototypes、agents 不依赖 app/prototypes、sdk 只依赖 base） |
| `uv run python scripts/check_architecture.py` | 组件 DAG 规则：只许构造器注入，禁止 setter/property 注入与运行时替换 |
| `uv run python scripts/check_no_reexport.py` | `__init__.py` 禁止 re-export |
| `uv run python scripts/check_stage_purity.py` | [agents/stages/](../src/tlon/agents/stages) 下 stage 纯度：禁止直接修改 session 状态 |

通用质量工具（ruff、mypy strict、pytest）见 [AGENTS.md](../AGENTS.md) 的验证质量门，不属于架构约束本身。