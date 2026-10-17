# ADR 0019：tlon runtime / server / client 包级拆分

> 阅读定位：本文保留当时的选择与理由；当前设计见 [infrastructure](../design/infrastructure.md)、[server-protocol](../design/server-protocol.md)。当前适用范围：包边界保留；facade 数量等历史快照以当前 architecture 为准。完整关系见 [决策索引](README.md)。

状态：已接受（2026-09-20，方向讨论拍板）

## 背景

ADR 0018 把 DSL 抽成 tlosure 后，三层模型（语言核心 / tlon runtime / 应用软件）的第二、三层仍同居 `src/tlon/`。现状地基良好（app → runtime 严格单向，import-linter 固话；装配集中在 `ServerApp.initialize()`），但边界不是窄接口：REST 层直接摸约 10 个 runtime 对象（`AgentRegistry` 7+ 方法、`ConfigService` 内部 dict、`AdapterManager`、`prototypes` 11 个函数、`base.config` YAML 助手、`FileTraceStore`、`MetricsService`、`paths` 单例），且直接读写 `~/.tlon` 文件系统（mailbox jsonl、agent.yaml、日志 jsonl、原型目录）。

一个改变讨论走向的事实：**tlon 没有传统意义上的 webserver 业务层**——REST/SSE 端点（`/messages`/`/inbox`/`/sessions`/`/events`/`/admin/*`/`/traces` 等）无一 web 专属，它们就是 runtime 的北向协议，已有四个独立客户端实现（Python SDK、telegram bot、operator TUI、webui SPA）。真正 web 专属的只有 webui 静态托管（部署便利）。因此拆分是**包级**工作：把边界从惯例变成强制；进程级分离（daemon 化、独立部署）不做，等真实部署需求出现再议。

## 决策

### 1. 四包结构（同仓库、同分发、单 pyproject）

```
src/
├── tlosure/        语言核心（ADR 0018）
├── tlon/           runtime：base/memory/organization/sandbox/tools/agents/prototypes
│   └── api/        runtime 的公共北向 API（domain facades + build_runtime）
├── tlon_server/    REST/SSE handlers + 客户端连接管理 + WebUI 静态托管
├── tlon_client/    协议客户端：SDK（TlonClient 等）+ telegram bot
└── tlon_launcher/  `tlon` CLI（dream/ops/telegram），composition root
```

依赖方向（import-linter 固话）：`tlosure` ← `tlon` ← `tlon_server`（只许 `tlon.api` + `tlon.base`）、`tlon` ← `tlon_client` ~~（只许 `tlon.base`，沿用现 sdk 契约）~~ ← `tlon_launcher` 居顶。CLI 入口不变：`uv run tlon dream|ops|telegram`。

> 演进：`tlon_client` 对 `tlon` 的唯一依赖是 `tlon.base.logger.get_logger`（10 行 structlog 薄封装），已改为直接用 `structlog.get_logger`，契约收紧为「client 不依赖 tlon 包」——无需拆 `tlon.base`。tlon_client 现在只依赖第三方库（aiohttp / structlog / python-telegram-bot），具备独立轻量分发的前提。

### 2. 按域 facade（`tlon/api/`，六个 + bootstrap）

| facade | 收编的现有触点 |
|---|---|
| `members` | AgentRegistry status/config/state、adopt/remove、成员创建（`instantiate` + persona 文件写入，现由 server 直接做） |
| `mail` | inbox 读（server 直接构造 `Mailbox` 读 jsonl，收编）、发信（`adapter_manager.publish_as`） |
| `prototypes` | `tlon.agents.prototypes` 的 11 个函数 |
| `observability` | `FileTraceStore`、`MetricsService`、日志 jsonl 读 |
| `config` | `config_manager.agents` 直读、`agent.yaml` 读写 |
| `events` | 事件订阅端口（供 NotificationService 挂接）+ github webhook ingress |

facade 是小类，构造器注入 runtime 对象，由 `build_runtime()` 装配。`build_runtime()` 收编 `ServerApp.initialize()` 的 runtime 装配步骤（bus/config/registry/delivery/adam 等），tlon_server 只装配自己的 session/notification/HTTP 生命周期。`guardian_provider`（实际服务 runtime 的 MailDelivery，误置 app 侧）归位 runtime。

### 3. SessionManager / NotificationService / ClientGateway 归 tlon_server

远程客户端的连接管理（session 生命周期、mode/filter、SSE fan-out）是 serving 关注点，不是 runtime 关注点。它们通过 `events` facade 的订阅端口挂 runtime 事件，不直接摸 EventBus。

### 4. 无兼容层

物理拆分时原模块路径直接删除，import 全部改向；不留 re-export shim。

## 后果

- 边界从惯例变强制：tlon_server 直碰 runtime 内脏会被 import-linter 打回；facade 方法群同时成为未来任何协议演进的候选操作集。
- runtime 可独立理解/测试；webui 静态托管仍在 tlon_server（同进程便利，非架构分层）。
- 施工分两 PR：先 facade（同包内收编触点 + 契约），后物理拆分（移包 + 打包/工具链/测试/文档）。
- ~~`paths` 单例留在 `tlon.base`（runtime 拥有文件系统）；tlon_server 不再直接读 mailbox/日志/agent.yaml——经 facade。~~ 已演进：`paths` 模块级单例已删除，`TlonPaths` 全面改为显式注入（进程入口构造一次，`build_runtime(runtime_paths)` 贯穿 runtime 各层）；同进程可并存多个 runtime 实例（各有独立 tlon home），tracing/metrics 支柱上移到 ServerApp（进程编排层）。tlon_server 不直接读 mailbox/日志/agent.yaml——经 facade。
