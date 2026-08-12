# Client-Server 协议设计

> 状态: ✅ 已验证
> 最后验证: 2026-08-12
> 协议版本: 2.0.0（HTTP + SSE，单端口 8080）
> 取代: v1.0.0 WebSocket 协议（已退役，见文末"历史版本"）

---

## 核心理念

1. **单端口 HTTP 协议族**: 请求-响应走 REST，服务端推送走 SSE（Server-Sent Events），同一端口（默认 8080）
2. **同步回执**: 发信等写操作返回明确的成功/失败响应（消解 v1 WS 发信无 ack 的缺陷，issue ）
3. **事件驱动**: Server → Client 通过 SSE 事件流推送所有状态变更
4. **全链路追踪**: 事件帧携带 `trace_id` / `span_id`，可追踪完整调用链
5. **Client 差异化**: 通过 `client_type` 识别终端类型，应用不同的事件过滤策略
6. **服务端唯一过滤点**: 事件过滤只在服务端执行（NotificationService），客户端只声明不过滤

---

## 端点清单

| 端点 | 方法 | 职责 |
|------|------|------|
| `/sessions` | POST | 建立会话：声明 `username` / `client_type` / `mode` / `filters` / `protocol_version`；返回 `session_id` + participants/agents 快照 |
| `/sessions/{id}` | PATCH | 更新 mode / filters |
| `/sessions/{id}` | DELETE | 显式断开（幂等） |
| `/events` | GET (SSE) | 事件流，`session_id` 走 query 参数或 `X-Session-Id` header |
| `/messages` | POST | 发信（同步回执：202 + `mail_id` 或 4xx + `{"error","reason"}`） |
| `/health` `/config` `/inbox` `/agents` `/agents/{id}` `/sessions`(GET) `/participants` `/traces` `/traces/{id}` `/metrics` `/agents/{id}/logs` `/logs` `/events/history` | GET | 只读查询（语义不变） |
| `/admin/config/agents` `/admin/config/agents/{id}/reload` | GET / POST | 配置热加载（语义不变） |

---

## 会话生命周期

```
POST /sessions  ──→  201 + session_id + 快照
       │
GET /events (SSE) ──→ 注册 event handler + HumanAdapter attach（presence）
       │
PATCH /sessions/{id}（可选，切 mode / filters）
       │
DELETE /sessions/{id} 或 SSE 断连 ──→ detach + handler 注销 + session 关闭
```

- **SSE 断开即离线**：断连（客户端断开、心跳写失败、DELETE）必然触发
  adapter detach + event handler 注销 + session 关闭。
- **重连**：客户端重新 `POST /sessions` 拿新 session_id 与快照；重连窗口内
  事件丢失（与 v1 WS 重连行为一致，不劣化）。事件 replay
  （`Last-Event-ID` + 环形缓冲）列为未来扩展。
- **心跳**：SSE 注释行（`: ping\n\n`）每 25s 一行。
- **认证**：身份声明模型（username 自选，无凭证），与 v1 一致；真实认证
  由  独立决策。

---

## 会话端点详述

### `POST /sessions` — 建立会话

```json
// Request
{
  "username": "alice",            // 可选，缺省 "guest"
  "client_type": "tui",            // 必填，非空字符串
  "mode": "chat",                  // 可选，chat | event | agent
  "filters": {},                   // 可选，初始事件过滤器
  "protocol_version": "2.0.0"      // 必填，仅接受 "2.0.0"
}

// 201 Created
{
  "session_id": "tui-0001",
  "username": "alice",
  "client_type": "tui",
  "mode": "chat",
  "filters": {},
  "connected_at": "2026-08-12T...",
  "participants": [{"id": "dev-001", "name": "...", "type": "agent"}],
  "agents": [{ "id": "dev-001", "state": "running", ... }]
}

// 4xx
{"error": "...", "reason": "unsupported_protocol_version",
 "supported_versions": ["2.0.0"]}
```

`reason`：`invalid_json` / `invalid_username` / `invalid_client_type` /
`invalid_mode` / `invalid_filters` / `unsupported_protocol_version`。

过滤器维度（客户端声明、服务端执行）：
- `event_types`: 包含列表（支持 `prefix.*` 通配）
- `exclude_event_types`: 排除列表（支持 `prefix.*` 通配）
- `trace_id`: 仅推送携带该 trace 的事件
- `agent`: 按事件来源匹配（支持 `*` 通配，如 `dev-*`）

### `PATCH /sessions/{id}` — 更新 mode / filters

请求体 `{"mode": "...", "filters": {...}}`（均可选）。返回更新后的会话
状态（`session_id`/`username`/`client_type`/`mode`/`filters`/
`connected_at`）；session 不存在返回 404。不推确认帧、不重发快照，
响应体即最新状态。

### `DELETE /sessions/{id}` — 断开

幂等（不存在或已断开都返回 200）。触发完整清理：adapter detach +
event handler 注销 + session 关闭；该 session 的活动 SSE 流随之收尾。

### `GET /events`（SSE）— 事件流

`session_id` 走 query 参数或 `X-Session-Id` header；session 无效返回
404，已有活动流返回 409（`reason: "stream_active"`）。连接建立即完成
event handler 注册 + HumanAdapter attach（presence + 邮件扇出）。

每个事件一个 `data:` 行，帧格式：

```json
{
  "type": "event",
  "event_type": "agent.thinking_start",
  "payload": { ... },
  "source": "dev-001",
  "timestamp": "2026-08-12T12:00:01Z",
  "trace_id": "trace-abc-456",
  "span_id": "span-thinking-001",
  "parent_span_id": "span-message-001"
}
```

每 25s 一行 `: ping` 注释心跳。

---

## 发信：`POST /messages`

邮件模型（R2，`docs/design/mail.md`）：发信即发邮件。发信人身份由
请求体的 `username` 声明（`human:<username>`），该人类尚未 attach 时
会瞬时创建适配器——首次接触同样建持久邮箱，语义与首次 session
attach 一致。

```json
// Request
{
  "username": "alice",              // 可选，缺省 "guest"
  "content": "请帮我审查这段代码",     // 必填，非空
  "to": ["dev-001", "顾言溪"],        // 名字 / id / 完整地址，服务端解析去重
  "cc": ["pm-001"],                  // 可选
  "subject": "代码审查请求",           // 可选
  "in_reply_to": "mail-uuid",        // 可选，回复头
  "thread_id": "thread-uuid"         // 可选，线程根 id
}

// 202 Accepted
{"mail_id": "eec20b75ff5a48bf82595acef21de517"}

// 4xx（字段校验失败 / 收件人不可解析等）
{"error": "人类可读的错误信息", "reason": "recipient_unresolved"}
```

- `to` / `cc`：每项可为 agent id、显示名或完整地址
  （`x@tlon.internal`），服务端解析去重；至少一路解析出收件人
- `in_reply_to` 指向上游邮件 id，`thread_id` 继承线程根邮件 id，
  缺省时新邮件自成线程根
- `reason` 为语义化字符串：`invalid_json` / `invalid_username` /
  `empty_content` / `invalid_to` / `invalid_cc` / `invalid_in_reply_to` /
  `invalid_thread_id` / `recipient_unresolved`（一封零收件人的邮件是非法的）
- 拉取邮件走 REST `GET /inbox?username=x&limit=N`（username 缺省
  `guest`，limit 缺省 50 上限 200），返回 `{"mails": [...], "count": N}`
  （inbox + sent 合并，按时间序）

---

## 事件类型规范

> 这些事件同时是 metrics 支柱的数据源：服务端的 MetricsService 订阅 EventBus，把 mail/agent/llm/tool 事件聚合成指标，经 `GET /metrics` 暴露（见 docs/architecture/domains/base.md 的 Metrics 支柱小节）。

### 邮件事件

| 事件类型 | 方向 | payload | 说明 |
|----------|------|---------|------|
| `mail.new` | S→C | Mail 对象 | 新邮件发出（投递前） |
| `mail.delivered` | S→C | `{mail, recipient}` | 邮件落入某收件人邮箱（chat 模式经 HumanAdapter 投递，不经 NotificationService） |

### Agent 生命周期

| 事件类型 | 方向 | payload | 说明 |
|----------|------|---------|------|
| `agent.started` | S→C | `{agent_id}` | Agent 启动 |
| `agent.stopped` | S→C | `{agent_id, reason}` | Agent 停止 |
| `agent.error` | S→C | `{agent_id, error}` | Agent 错误 |
| `agent.state_changed` | S→C | `{agent_id, old_state, new_state}` | 状态变更 |

### Agent 思考过程 (可观测性)

| 事件类型 | 方向 | payload | 说明 |
|----------|------|---------|------|
| `agent.thinking_start` | S→C | `{message_id, context_summary}` | 开始思考 |
| `agent.thinking_end` | S→C | `{message_id, duration_ms}` | 思考结束 |
| `agent.llm_request` | S→C | `{model, messages_count, estimated_tokens}` | LLM 请求 |
| `agent.llm_response` | S→C | `{model, response_tokens, duration_ms}` | LLM 响应 |

### 工具执行 (可观测性)

| 事件类型 | 方向 | payload | 说明 |
|----------|------|---------|------|
| `tool.execute_start` | S→C | `{tool_type, command_preview, working_dir}` | 开始执行 |
| `tool.execute_end` | S→C | `{tool_type, exit_code, duration_ms}` | 执行完成 |
| `tool.error` | S→C | `{tool_type, error, stderr}` | 执行错误 |

### 系统事件

| 事件类型 | 方向 | payload | 说明 |
|----------|------|---------|------|
| `system.startup` | S→C | `{version, start_time}` | 系统启动 |
| `system.shutdown` | S→C | `{reason}` | 系统关闭 |
| `session.join` | S→C | `{session_id, client_type}` | 用户加入 |
| `session.leave` | S→C | `{session_id, duration}` | 用户离开 |

---

## 事件过滤（服务端过滤点）

事件过滤**只在服务端执行**（`NotificationService`）。客户端（SDK Adapter /
TUI）只**声明**过滤器：通过 `POST /sessions` 的 `filters` 字段（初始
过滤器，如 client type 的默认排除集）或 `PATCH /sessions/{id}`（运行时
更新，整体替换）上报，本地不再做二次匹配。

过滤管线（各步骤为 AND 关系）：

0. **人类邮件单轨投递**（R2）：`mail.delivered` 不经 NotificationService
   推给 chat 模式 session——人类收信走 HumanAdapter（订阅 MAIL_DELIVERED，
   按 `recipient` == 自己的 member id 过滤后向 attached sessions 扇出）；
   event / agent 模式不受影响（operator 是运维控制台，需看到全部邮件流量）
1. **模式语义**（`POST /sessions` 的 `mode` 或 PATCH 切换）：
   - `chat`: `mail.*`、`agent.response/started/stopped/error`、`system.*`
   - `agent`: 仅 `agent.*`
   - `event`: 全部事件
2. **会话过滤器**（`session.filters`，均可选）：
   - `event_types`: 包含列表（支持 `prefix.*` 通配）
   - `exclude_event_types`: 排除列表（支持 `prefix.*` 通配）
   - `trace_id`: 仅推送携带该 trace 的事件
   - `agent`: 按事件来源匹配（支持 `*` 通配，如 `dev-*`）

---

## Client 类型差异

### Terminal (Operator TUI)

- **连接方式**: REST + SSE（单端口，sdk/客户端同协议）
- **事件接收**: 全量事件
- **特殊功能**: Event 模式、Agent 模式、命令行交互

### Web Client

- **连接方式**: REST + SSE
- **事件接收**: 过滤部分高频事件（如 `agent.llm_request`）
- **特殊功能**: Trace 可视化、历史消息分页

### Telegram Bot

- **连接方式**: Webhook/轮询 → Server REST + SSE
- **事件接收**: 仅 `mail.*` 相关事件
- **特殊功能**: 命令支持（`/status`, `/agents`）

---

## 错误处理

v2 废弃 v1 的应用层错误码表，统一为 HTTP 语义：

| 形态 | 说明 |
|------|------|
| 2xx + 响应体 | 成功（`POST /messages` 为 202 + `mail_id`） |
| 4xx + `{"error", "reason"}` | 客户端错误；`reason` 为语义化字符串（见各端点） |
| 404 | 资源不存在（session / agent / trace） |
| 409 | 冲突（session 已有活动 SSE 流，`reason: "stream_active"`） |
| 503 | 依赖不可用（mail storage / metrics / config manager） |

---

## 历史版本

### ~~v1.0.0：WebSocket 协议（已退役，2026-08-12）~~

~~双端口形态：REST :8080（只读查询）+ WebSocket :8888/ws（auth 握手与
协议版本协商、`message` 发信帧、`mode`/`filter` 帧、`ping`/`pong` 心跳、
`event` 推送帧、`participants`/`agents` 主动推送帧）。~~

~~退役原因：发信无 ack 是 WS 单向帧模型的结构性缺陷（issue ）；
两套协议、两个端口维护成本高。迁移设计与分阶段落地记录见
[http-sse-protocol.md](./http-sse-protocol.md)（PR-1 ~ PR-4）。~~

v1 帧格式与版本协商机制的完整文档见 git 历史（本文件 2026-08-12 之前
的版本）。

---

*v1 设计完成: 2026-03-18；v2 落地: 2026-08-12（PR-1 ~ PR-4）*
