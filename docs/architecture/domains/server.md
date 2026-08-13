# Server 层

> 状态: ✅ 已验证
> 最后验证: 2026-08-13

---

## 职责

服务端进程（`tlon dream`）的组装层与接入层：DI 装配全部组件并管理生命周期，
对外提供 HTTP+SSE 单端口服务（协议 v2），管理客户端会话与事件推送。

---

## 模块结构

```
app/server/
├── serverapp.py                  # ServerApp：DI 组装器 + 生命周期（initialize → start → shutdown）+ run_server 入口
├── core/
│   ├── client_gateway.py         # ClientGateway：客户端侧 Facade（session/通知/成员发现三个组件的统一入口）
│   ├── session_manager.py        # SessionManager + ClientSession dataclass：会话生命周期/查找/mode/filters
│   ├── notification_service.py   # NotificationService：EventBus→session 事件推送 + mode 语义 + filters 过滤管道
│   └── member_directory.py       # MemberDirectory：成员通讯录（agents 来自 AgentRegistry + humans 来自 AdapterManager，实时聚合）
└── api/
    ├── rest.py                   # RESTAPIHandler（全部路由+处理器）+ RESTServer（aiohttp 生命周期封装）
    └── send_mail.py              # parse_send_mail_fields：POST /messages 字段校验的唯一副本（SendMailFields/SendMailError）
```

所有 `__init__.py` 均只有 license 头，无 re-export（`check_no_reexport` 约束）。

> 边界提醒：邮件投递实体（`MailDelivery`、`AdapterManager`、`Mailbox`）在
> `tlon.organization` 域，server 只是注入和消费；邮件模型见
> [../../design/mail.md](../../design/mail.md)。

---

## 关键类

| 类 | 职责 | 路径 |
|----|------|------|
| `ServerApp` | DI 组装器 + 进程生命周期 | `app/server/serverapp.py:61` |
| `RESTAPIHandler` / `RESTServer` | 全部 HTTP 路由/处理器 + aiohttp 生命周期封装 | `api/rest.py:187, 1110` |
| `ClientGateway` | 客户端侧 Facade（会话/通知/成员发现统一入口） | `core/client_gateway.py` |
| `SessionManager` / `ClientSession` | 会话生命周期与状态 | `core/session_manager.py:35` |
| `NotificationService` | EventBus→session 事件推送 + 过滤管道 | `core/notification_service.py:76` |
| `MemberDirectory` | 成员通讯录（agents + humans 实时聚合） | `core/member_directory.py` |

---

## ServerApp 装配与生命周期（`serverapp.py`）

`_components: dict[str, Any]`（serverapp.py:97），键：`event_bus, config_service,
metrics_service, trace_store, agent_registry, adam, adapter_manager, member_directory,
mail_delivery, session_manager, notification_service`；`client_gateway` 与 `rest`
单独持引用（:100-101）。

`initialize()` 四步顺序（:107-259）：

- **Step 0**（:116-121）：`init_tracing` + `init_metrics`（必须先于任何 span 创建）；
- **Step 1 核心基础设施**：`seed_prototypes()`（:125，幂等）→ EventBus（:129）→
  ConfigService（需 EventBus，:135）→ MetricsService（纯事件消费者，:143）；
- **Step 2 领域组件**：FileTraceStore（:151）→ AgentRegistry（:157，EventBus +
  ConfigService 作 AgentInfoProvider；先按 `config_service.agents` 逐个 `register`
  再 `initialize`）→ Adam（`make_adam`，PID 1，不入 registry/directory，:175）→
  AdapterManager（:181）→ MemberDirectory（:190）→ MailDelivery（:200）；
- **Step 3 客户端接入**：SessionManager（:211）→ NotificationService（event_bus +
  session_manager，:216）→ ClientGateway（三组件注入，:224）；
- **Step 4 API 层**：RESTServer（注入 gateway + registry + config + trace_store +
  metrics + logs_dir + paths + adapter_manager，:236-247）；末尾发 `SYSTEM_STARTUP`
  事件（:250）。

`start()`（:263-287）：只起 `rest.start()` 然后阻塞等 `_shutdown_event`——
agent boot 不在此等待（boot 是异步请求，见 [agents.md](./agents.md)）。

`shutdown()`（:291-350）：发 `SYSTEM_SHUTDOWN` → rest.stop → gateway.shutdown →
**逆序** adam → agent_registry → mail_delivery → metrics_service → adapter_manager →
event_bus → 最后 `shutdown_tracing` / `shutdown_metrics`。

`run_server()`（:362-408）：进程入口——配日志（stdout +
`~/.tlon/shared/logs/server-{date}.jsonl`）、注册 SIGINT/SIGTERM handler。

---

## HTTP+SSE 单端口（协议 v2）

单端口 8080（默认 `host=127.0.0.1`，serverapp.py:76-82），aiohttp 单
`web.Application`，路由全部注册在 `RESTAPIHandler.create_app`（rest.py:253-305）：

| 路由 | 处理器位置 | 说明 |
|---|---|---|
| `GET /health` | rest.py:339 | 健康检查 |
| `GET /config` | rest.py:354 | 脱敏配置快照 |
| `GET /inbox` | rest.py:375 | 人类收件箱（username→`Mailbox(paths.member_mail_dir("human:"+u))`，有路径穿越防护） |
| `POST /messages` | rest.py:415 | 人类发邮件，202 + mail_id；经 `AdapterManager.publish_as`；字段校验在 `api/send_mail.py:49` |
| `POST /sessions` | rest.py:581 | 建会话；强制 `protocol_version=="2.0.0"`（rest.py:57 `SESSION_PROTOCOL_VERSION`）；201 带 participants+agents 快照 |
| `PATCH /sessions/{id}` | rest.py:691 | 改 mode/filters |
| `DELETE /sessions/{id}` | rest.py:747 | 幂等断连；SSE 队列塞 None 哨兵 + adapter detach + gateway disconnect |
| `GET /events` | rest.py:772 | SSE 流（详见下文） |
| `GET /agents`、`GET /agents/{id}` | rest.py:501, 515 | agent 列表/详情 |
| `GET /sessions` | rest.py:541 | 活动会话列表 |
| `GET /participants` | rest.py:872 | 经 MemberDirectory |
| `GET /admin/config/agents`、`POST /admin/config/agents/{id}/reload` | rest.py:897, 920 | 仅 config_manager 存在时注册；reload→`AgentRegistry.adopt_agent` |
| `GET /traces`、`GET /traces/{trace_id}` | rest.py:944, 976 | 仅 trace_store 存在时注册 |
| `GET /metrics` | rest.py:1000 | `MetricsService.snapshot()`，服务缺失 503 |
| `GET /agents/{id}/logs`、`GET /logs` | rest.py:1014, 1051 | 读 `server-{date}.jsonl`（今天+昨天），过滤管道 `_filter_log_entries`（rest.py:138） |
| `GET /test/ping`、`POST /test/echo` | rest.py:1085, 1095 | 仅 debug 模式 |
| `OPTIONS /{path:.*}` | rest.py:328 | CORS 全放开 |

SSE 事件帧序列化在 `_event_frame`（rest.py:80-106），含
trace_id/span_id/parent_span_id 透传。`RESTServer`（rest.py:1110-1205）只是
AppRunner/TCPSite 封装，`start/stop` 在 rest.py:1170/1186。

---

## 会话管理

- `ClientSession` dataclass（`core/session_manager.py:35-52`）：
  id/client_type/connected_at/mode（默认 `"chat"`）/filters/metadata；session id
  格式 `{client_type}-{counter:04d}`（:102），全局 `_sessions` dict +
  `asyncio.Lock`。
- mode 三值 `"chat"/"event"/"agent"`（rest.py:60 `SESSION_MODES`）；**mode 语义
  唯一权威是 `MODE_EVENT_PATTERNS`**（`core/notification_service.py:63-73`）：
  chat→`mail.*, agent.response, agent.started/stopped/error, system.*`，
  agent→`agent.*`，event 无限制（未列出的 mode 收全部事件类型）。
- 过滤管道 `_should_send_to_session`（notification_service.py:222-282）：
  (0) chat 模式不收 `mail.delivered`（单轨投递：人类邮件走 HumanAdapter 而非
  本服务）；(1) mode 语义；(2) filters 的 `trace_id` / `event_types` /
  `exclude_event_types`（支持 `prefix.*` 通配）/ `agent`（source 匹配）。
- **两段式会话（v2）**：`POST /sessions` 只建会话（`ClientGateway.open_session`，
  client_gateway.py:163）；event handler 在 `GET /events` 开流时才注册
  （`set_event_handler`→`NotificationService.register_handler`，
  client_gateway.py:190；rest.py:823），同时
  `AdapterManager.attach(username, session_id, handler)`（rest.py:826）——
  同一 handler 兼作人类邮件投递 sink。

## SSE 推送（`GET /events`，rest.py:772-870）

- session_id 走 query 参数或 `X-Session-Id` 头；会话不存在 404；
- 每会话一条流：`_sse_queues: dict[session_id, asyncio.Queue]`（rest.py:251），
  重复开流返回 409（`reason: "stream_active"`）；队列在首个 await 前注册，
  防止并发双流竞态；
- 心跳：`": ping"` 注释行每 25s（`SSE_HEARTBEAT_SECONDS`，rest.py:63）——
  队列 25s 无事件即写心跳，写失败（含心跳）视为客户端已断开；
- **断流即离线**：流结束（客户端断开 / 写失败 / DELETE 塞 None 哨兵）在 finally
  里统一 detach adapter + gateway disconnect（rest.py:863-868）；客户端重连需
  重新 `POST /sessions`。

---

## 文档索引

| 文档 | 目的 | 深度 |
|------|------|------|
| [../../design/client-server-protocol.md](../../design/client-server-protocol.md) | Client-Server 协议 v2（HTTP+SSE 单端口） | L1 |
| [../../design/http-sse-protocol.md](../../design/http-sse-protocol.md) | HTTP+SSE 迁移设计（WS 已退役） | L1 |
| [../../design/mail.md](../../design/mail.md) | 邮件模型（投递实体在 organization 域） | L1 |

---

## 验证清单

- [x] `initialize()` 四步装配顺序与 `shutdown()` 逆序与代码一致
- [x] 端点表与 `RESTAPIHandler.create_app` 路由注册一致（含条件注册项）
- [x] mode 三值语义以 `MODE_EVENT_PATTERNS` 为唯一权威
- [x] SSE 心跳/409/断流即离线语义与 `handle_events` 实现一致
