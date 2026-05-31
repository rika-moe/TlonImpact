# Client-Server 协议设计

> 状态: ✅ 已验证
> 最后验证: 2026-03-24
> 协议版本: 1.0.0

---

## 核心理念

1. **协议版本化**: WebSocket 握手时协商协议版本，支持向后兼容
2. **统一消息格式**: 所有消息基于 JSON，包含 `type`, `msg_id`, `timestamp`, `trace_id`
3. **事件驱动**: Server → Client 通过 `event` 类型消息推送所有状态变更
4. **全链路追踪**: 每条消息携带 `trace_id`，可追踪完整调用链
5. **Client 差异化**: 通过 `client_type` 识别终端类型，应用不同的事件过滤策略

---

## 协议版本协商

### 认证流程

```
Client → Server (auth)
{
  "type": "auth",
  "client_type": "tui",              // "tui" | "web" | "telegram"
  "protocol_version": "1.0.0",       // 首选版本
  "supported_versions": ["1.0.0"],   // 支持的版本列表
  "capabilities": ["trace", "filter"],
  "metadata": {
    "client_version": "0.2.0",
    "platform": "macOS"
  }
}

Server → Client (auth_success)
{
  "type": "auth_success",
  "session_id": "sess-uuid",
  "protocol_version": "1.0.0",       // 实际使用的版本
  "server_version": "0.2.0",
  "server_time": "2026-03-18T12:00:00Z",
  "capabilities": ["trace", "filter", "history"]
}

或认证失败:
{
  "type": "auth_failed",
  "error_code": "VERSION_MISMATCH",
  "message": "Protocol version 1.0.0 not supported",
  "supported_versions": ["1.1.0"]
}
```

### 版本策略

| 场景 | 处理 |
|------|------|
| Client 版本 = Server 支持版本 | 正常使用该版本 |
| Client 版本 < Server 最低版本 | 返回错误，要求升级 Client |
| Client 版本 > Server 版本 | 降级到 Server 支持的最高版本 |
| 版本不兼容 | 返回 `VERSION_MISMATCH` 错误 |

---

## 消息类型

### 基础结构

```typescript
interface TlonMessage {
  type: string;           // 消息类型
  msg_id?: string;        // 消息唯一 ID（UUID v4）
  timestamp: string;      // ISO8601 格式
  trace_id?: string;      // 追踪 ID（UUID v4）
}
```

### Client → Server

| 类型 | 说明 | 关键字段 |
|------|------|----------|
| `auth` | 认证请求 | `client_type`, `protocol_version` |
| `message` | 发送消息 | `content`, `mentions`, `reply_to` |
| `mode` | 切换模式 | `mode`: "chat" \| "event" \| "agent" |
| `filter` | 设置过滤器 | `filters`: `{event_types?, sources?}` |
| `ping` | 心跳 | `seq`: 序列号 |

### Server → Client

| 类型 | 说明 | 关键字段 |
|------|------|----------|
| `auth_success` | 认证成功 | `session_id`, `protocol_version` |
| `auth_failed` | 认证失败 | `error_code`, `message` |
| `event` | 事件推送 | `event_type`, `payload`, `source` |
| `pong` | 心跳响应 | `seq`, `latency_ms` |
| `error` | 错误通知 | `code`, `message`, `details` |

---

## 详细消息格式

### 聊天消息 (Client → Server)

```json
{
  "type": "message",
  "msg_id": "msg-uuid-123",
  "timestamp": "2026-03-18T12:00:00Z",
  "trace_id": "trace-abc-456",
  "content": "请帮我审查这段代码",
  "mentions": ["dev-001", "pm-001"],
  "reply_to": "msg-uuid-111"
}
```

### 事件消息 (Server → Client)

```json
{
  "type": "event",
  "msg_id": "evt-uuid-789",
  "timestamp": "2026-03-18T12:00:01Z",
  "event_type": "agent.thinking_start",
  "trace_id": "trace-abc-456",
  "span_id": "span-thinking-001",
  "parent_span_id": "span-message-001",
  "source": "dev-001",
  "payload": {
    "message_id": "msg-uuid-123",
    "context_summary": "用户询问代码审查...",
    "memory_recall_count": 5
  }
}
```

### 心跳消息

```json
// Ping (Client → Server)
{
  "type": "ping",
  "seq": 42,
  "timestamp": "2026-03-18T12:00:30Z"
}

// Pong (Server → Client)
{
  "type": "pong",
  "seq": 42,
  "server_time": "2026-03-18T12:00:30.050Z",
  "latency_ms": 50
}
```

---

## 事件类型规范

### 消息事件

| 事件类型 | 方向 | payload | 说明 |
|----------|------|---------|------|
| `message.new` | S→C | `{id, content, sender, mentions}` | 新消息 |
| `message.delivered` | S→C | `{message_id, delivered_at}` | 消息已送达 |
| `message.edit` | S→C | `{id, new_content, edited_at}` | 消息编辑 |

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

## Client 类型差异

### Terminal (TUI)

- **连接方式**: WebSocket (Stateful)
- **事件接收**: 全量事件
- **特殊功能**: Event 模式、Agent 模式、命令行交互

### Web Client

- **连接方式**: WebSocket (Stateful) + REST API
- **事件接收**: 过滤部分高频事件（如 `agent.llm_request`）
- **特殊功能**: Trace 可视化、历史消息分页

### Telegram Bot

- **连接方式**: Webhook/轮询 → Server WebSocket
- **事件接收**: 仅 `message.*` 相关事件
- **特殊功能**: 命令支持（`/status`, `/agents`）

---

## 错误码

| 错误码 | 说明 | 处理建议 |
|--------|------|----------|
| `AUTH_FAILED` | 认证失败 | 检查 client_type 和版本 |
| `VERSION_MISMATCH` | 版本不匹配 | 升级 Client 或 Server |
| `INVALID_MESSAGE` | 消息格式错误 | 检查 JSON 格式 |
| `RATE_LIMITED` | 请求过于频繁 | 降低发送频率 |
| `SESSION_EXPIRED` | 会话过期 | 重新连接 |
| `INTERNAL_ERROR` | 服务器内部错误 | 稍后重试 |

---

## 文档索引

| 文档 | 目的 | 深度 |
|------|------|------|
| [architecture/protocol.md](../architecture/protocol.md) | Client-Server 架构设计 | L2 |
| [roadmap/milestone-7-client-expansion.md](../roadmap/milestone-7-client-expansion.md) | 实施路线图 | L3 |

---

## 验证清单

- [ ] 协议版本协商机制实现
- [ ] 所有消息包含 msg_id 和 timestamp
- [ ] Event 类型扩展（thinking_start, llm_request, tool_execute_start 等）
- [ ] Client 类型识别和事件过滤
- [ ] 心跳机制（ping/pong 带 latency）
- [ ] 错误码规范实现
- [ ] 向后兼容测试（旧 Client 可连接）

---

*设计完成时间: 2026-03-18*
