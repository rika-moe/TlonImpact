# HTTP + SSE 协议（v2）设计与迁移计划

> 状态：已落地（2026-08-12，PR-1 ~ PR-4 全部完成，WS 已退役）
> 取代：WS 双协议形态（[client-server-protocol.md](./client-server-protocol.md) v1.0.0，已删除线归档）

---

## 1. 背景与动机

### 现状

服务端对客户端暴露两套协议：

- **REST（:8080）**：15 个 GET + 1 个 POST（`/admin/config/agents/{id}/reload`），全部只读查询 + 热加载。
- **WebSocket（:8888/ws，协议 v1.0.0）**：承担四类职责——
  1. 事件推送（EventBus → NotificationService → 各 session，按 mode/filters 服务端过滤）；
  2. 人类邮件投递（HumanAdapter 扇出到 attached session sink，单轨投递；"在线人类"的 presence 由 WS attach 派生）；
  3. 发信入口（`message` 帧，请求-响应语义硬塞在 WS 里，**无 ack**——TUI 被迫用 400ms 窗口做乐观上屏，issue ）；
  4. 会话状态（auth 握手、协议版本协商、mode/filter、ping/pong 心跳）。

### 问题

- 两套协议、两个端口、两种连接心智，维护成本高（websocket.py 699 行 + SDK BaseClient 825 行状态机 + TUI wsClient 273 行）。
- 发信无 ack 是 WS 单向帧模型的结构性缺陷，修补（加 request-id 关联）等于在 WS 上重新发明 HTTP。
- **变更窗口期**：SDK 无生产用户（issue ），真实客户端只有 Operator TUI 一个——协议破坏性变更的爆炸半径处于历史最小值。

### 决策

统一到 HTTP 协议族：请求-响应走 REST，服务端推送走 **SSE（Server-Sent Events）**。单端口（8080），WS 端口（8888）退役。

不选纯 HTTP 轮询的理由：本系统是事件密集型（llm_request/thinking/tool 事件高频），轮询要么丢实时性要么制造请求风暴；且"在线人类"presence 模型需要连接语义，HTTP 轮询下只能重新发明心跳租约。SSE 是 HTTP 上的单向推送，aiohttp 原生支持，localhost 场景无代理/超时障碍。

---

## 2. 目标协议形态（v2.0.0）

### 端点清单

| 端点 | 方法 | 职责 | 备注 |
|---|---|---|---|
| `/sessions` | POST | 建立会话：声明 `username`/`client_type`/`mode`/`filters`/`protocol_version` | 返回 `session_id` + 初始快照（participants/agents，替代 WS 的 `participants`/`agents` 主动帧） |
| `/sessions/{id}` | PATCH | 更新 mode / filters | 替代 `mode`/`filter` 帧 |
| `/sessions/{id}` | DELETE | 显式断开（可选；SSE 断开即离线） | 幂等 |
| `/events` | GET (SSE) | 事件流，header 或 query 携带 `session_id` | 事件帧格式沿用现有 `{"type":"event","event_type","payload","trace_id","span_id"}` |
| `/messages` | POST | 发信（`content`/`to`/`cc`/`subject`/`in_reply_to`/`thread_id`） | **同步回执**：202 + `mail_id`；4xx + `{"error","reason"}`（如 `recipient_unresolved`）——消解  |
| 现有 15 个 GET + admin reload | 不变 | — | — |

### 关键语义

- **事件过滤**：服务端仍是唯一过滤执行点，`MODE_EVENT_PATTERNS`（chat/agent/event）与 session filters 逻辑原样复用（notification_service.py:63），SSE sink 只是另一种 event handler 注册形式。
- **人类投递单轨**：chat 模式 session 不收 `mail.delivered`（走 HumanAdapter 扇出）的语义不变，仅触发点从 WS attach 改为 SSE 连接建立。
- **presence**：SSE 连接数即在线；HumanAdapter 的 attach/detach（含多连接 refcount）由 SSE 连接生命周期驱动，语义与现状等价。
- **心跳**：SSE 注释行（`: ping\n\n`）每 25s 一行，替代 WS ping/pong。
- **断线重连**：客户端自动重连 + 重新 `POST /sessions` 拿快照。重连窗口内事件丢失——与现状 WS 重连行为一致，不劣化。**初版不做事件回填**；`Last-Event-ID` + 环形缓冲 replay 列为未来扩展。
- **认证**：维持现状的身份声明模型（username 自选，无凭证）， 的安全决策独立推进，不在本迁移内夹带。

---

## 3. 分阶段实施计划

行为不变先行，每个 PR 独立可回滚；每个 PR 过六件套 + 真实冒烟（起 dream + 发邮件 + 收件箱验证 + 清理痕迹释放端口）。

### PR-1：REST 发信端点（纯增量，不动 WS）

> 状态：✅ 已落地（2026-08-11）——`POST /messages` + TUI 发信切换，
> 字段校验抽为 `api/send_mail.py` 供 WS/REST 共享；AdapterManager 新增
> `publish_as`（无 session 发信，瞬时适配器首发建持久邮箱）。

- 新增 `POST /messages`：复用 `websocket.py:378 _handle_send_message` 的字段校验与 `AdapterManager.publish` 路径，同步返回 mail_id 或 4xx+reason。
- TUI：MailView 发信切到 `POST /messages`，删除 400ms `SUBMIT_ACK_MS` 乐观上屏 hack（ 随之关闭）。
- WS `message` 帧保留，协议文档标注 deprecated。
- 验收：TUI 发信/回复/抄送全路径冒烟；4xx 时表单原样保留并提示 reason。

### PR-2：SSE 事件通道 + sessions 端点

> 状态：✅ 已落地（2026-08-12）——`POST /sessions`（201 + 快照，
> protocol_version 仅接受 "2.0.0"）、`PATCH/DELETE /sessions/{id}`
> （DELETE 幂等）、`GET /events` SSE（25s `: ping` 心跳，断连即
> detach + 关 session）；ClientGateway 新增 `open_session` /
> `set_event_handler`；TUI 切 sseClient 并删除 wsClient，完全脱离 WS。
>
> 落地偏差：PATCH 不推 `mode_changed` 帧/快照（响应体即最新状态，
> TUI 本地更新）；同 session 的重复 SSE 流返回 409；SSE 断连即关闭
> session（重连须重新 `POST /sessions`，与"重新拿快照"的既定重连
> 语义一致）。

- 服务端：新增 `POST /sessions`、`PATCH/DELETE /sessions/{id}`、`GET /events`（SSE）；NotificationService 支持 SSE sink（过滤管线零改动）；心跳注释行。
- HumanAdapter：新增 SSE 驱动的 attach/detach 路径，与 WS 路径**并存**（双轨期）。
- TUI：新增 sseClient（Node 22 全局 fetch + ReadableStream 解析），事件流切换；wsClient 仅留发信之外已无职责——本 PR 后 TUI 完全脱离 WS。
- 验收：TUI 六视图实时性冒烟（mail 上屏、agents 状态刷新、events 流）；双 SSE 连接 refcount；断网自动重连。

### PR-3：SDK 重写

> 状态：✅ 已落地（2026-08-12）——`BaseClient` 重写为 REST + SSE
> 薄客户端（825 → 774 行：状态机/心跳/auth 帧机器全删，但 handler
> 分发与重连机器保留，规模未如预估减半）：连接 = `POST /sessions`（201 拿
> session_id + participants/agents 快照），事件订阅 = `GET /events`
> SSE（`data:` 帧分发、`: ping` 注释忽略），断线指数退避自动重连
> （重连 = 重新 `POST /sessions` 拿新会话与快照，on_reconnect 随后
> 触发）；`set_mode`/`set_filters` 走 `PATCH /sessions/{id}`，
> `disconnect` 走 `DELETE /sessions/{id}` + 中止 SSE 流；
> `send_message` 走 `POST /messages`（202 回执 → True，4xx → False）。
> 构造参数 ws_url/rest_url 合并为单 `base_url`（默认
> http://localhost:8080）；handler 注册 API 与事件分发形状
> （{"type", "payload", "source", ...}）保持不变。test_sdk.py 重写为
> aiohttp TestServer in-process 模拟（36 个测试）。
>
> 落地偏差：`authenticate` 仍接受 `capabilities`/`metadata` 参数
> （API 兼容）但 v2 会话契约无对应字段，不发送；`set_mode` 合并为
> 单次 PATCH（mode + filters 同请求），不再像 WS 时代发两帧。

- BaseClient（825 行连接状态机）重写为薄客户端：REST 调用 + SSE 订阅 + 自动重连；预计规模减半以上。
- TerminalAdapter 的 filters 声明语义不变。
- AGENTS.md 冒烟流程（"SDK 发邮件"）改走新 SDK。
- 验收：SDK 单测更新 + 冒烟脚本全路径。

### PR-4：WS 退役

> 状态：✅ 已落地（2026-08-12）——`api/websocket.py` 与
> `test_websocket.py` 删除；`serverapp.py` 移除 WebSocketServer 装配与
> `ws_port`（`run_server`/`ServerApp` 签名同步收窄，ClientGateway 的
> `port` 改传 rest_port）；`dream` 的 `--ws-port` 移除；`ops`/`intrude`
> 的 `--port`（WS 端口）移除，`ops.py` 不再透传；TUI `args.ts` 删除
> `wsPort`/`--port`/`TLON_PORT`，只认 host + `--rest-port`；
> ClientGateway 删除仅 WS 使用的 `connect_client`（REST/SSE 走
> `open_session` + `set_event_handler` + `disconnect_client`）；
> `client-server-protocol.md` 重写为 v2（v1 压缩归档于文末"历史版本"）。
>
> 落地偏差：HumanAdapter 无需删 WS 分支——PR-2 落地的 attach/detach
> 本身是传输无关的（sink 回调语义），双轨期只存在于调用方而非
> Adapter 内部，故本 PR 无 human_adapter.py 改动。

- ~~删除 websocket.py、8888 端口、WS 相关帧处理与 `pong`/`auth_*` 机器；launcher `ops.py` 端口参数简化。~~ ✅
- ~~HumanAdapter 删 WS attach 路径（PR-2 双轨期结束）。~~ ✅（见上方偏差说明）
- ~~协议文档：client-server-protocol.md 全文替换为 v2，v1 内容删除线归档；AGENTS.md 端口表（8080/8888）更新。~~ ✅
- 验收：全仓库 grep 无 `websocket`/`8888` 残留（历史文档除外）；六件套 + 完整冒烟。

### 里程碑依赖

PR-1 ∥ PR-2 可并行（发信与事件通道解耦）；PR-3 依赖 PR-2；PR-4 依赖 PR-1/2/3 全部落地。

---

## 4. 改造点清单（按文件）

### 服务端（src/tlon/app/server/）

| 文件 | 改动 |
|---|---|
| `api/rest.py` | PR-1 加 `POST /messages`；PR-2 加 sessions 三端点 + `GET /events` SSE |
| `api/websocket.py` | PR-1/2 不动，PR-4 删除 |
| `core/notification_service.py` | PR-2 支持 SSE sink（handler 注册机制不变，过滤管线零改动） |
| `core/session_manager.py` | PR-2 session 建立改走 REST，ID 语义（`{client_type}-{序号}`）保留 |
| `core/client_gateway.py` | PR-2 connect/disconnect 改由 REST 触发 |
| `organization/messaging/human_adapter.py` | PR-2 双轨 attach；PR-4 删 WS 路径 |
| `serverapp.py` | PR-4 移除 WebSocketServer 装配与 8888 端口 |
| `app/launcher/commands/ops.py` | PR-4 端口参数简化 |

### 客户端

| 文件 | 改动 |
|---|---|
| `apps/operator/src/client/wsClient.ts` | PR-2 退役，替换为 sseClient（fetch + ReadableStream）；PR-1 发信先切 REST |
| `apps/operator/src/views/MailView.tsx` | PR-1 删乐观上屏，按 ack 上屏 |
| `src/tlon/app/clients/sdk/base_client.py` | PR-3 重写为 REST + SSE 薄客户端 |
| `src/tlon/app/clients/sdk/client.py` / `adapter.py` | PR-3 接口语义保持，实现适配 |

### 文档

| 文件 | 改动 |
|---|---|
| `docs/design/client-server-protocol.md` | PR-4 重写为 v2，v1 删除线归档 |
| `AGENTS.md` | PR-4 端口表与冒烟流程更新 |

---

## 5. 风险与对策

| 风险 | 对策 |
|---|---|
| SSE 长连接的资源泄漏（aiohttp response 未释放） | SSE handler 用 `async with` 管理生命周期；断连检测依赖写心跳失败回调；单测覆盖连接/断开路径 |
| PR-2 双轨期 WS/SSE 两套 attach 并存的状态不一致 | HumanAdapter refcount 语义已在现状中存在（多 session attach），双轨只是多一种来源；冒烟覆盖混合连接场景 |
| 事件回填缺失导致重连丢事件被用户感知 | 与现状 WS 行为一致（不劣化）；TUI 重连后重拉快照（agents/traces/inbox）兜底；replay 列未来扩展 |
| SDK 重写破坏冒烟脚本 | PR-3 内同步迁移冒烟流程，验收项强制 |
| TUI dist/ 与 src 脱节（） | 每个涉及 TUI 的 PR 强制 rebuild dist 并提交（在  解决前作为临时纪律） |

---

## 6. 明确不做

- 不引入真实认证（ 独立决策）；
- 不做事件 replay / Last-Event-ID（未来扩展）；
- 不改 EventBus、NotificationService 过滤管线、Mail 模型等内核——本迁移只动传输层与会话建立方式；
- 不动 REST 现有端点的语义（纯增量）。

---

*创建：2026-08-11。实施进度在各 PR 中回填。*
