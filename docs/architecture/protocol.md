# 进程间通信协议

> dream 作为服务端，通过 REST + WebSocket 与 chat / ops 通信。
> mirror 独立运行，不经过网络，直接读写文件。

---

## REST API（端口 8080）

| 端点 | 方法 | 用途 |
|------|------|------|
| `/health` | GET | 健康检查 |
| `/api/agents` | GET | Agent 列表 |
| `/api/chatroom/messages` | GET | 消息历史 |
| `/api/agents/{id}/logs` | GET | Agent 日志 |
| `/traces/{trace_id}` | GET | 追踪链路 |
| `/traces` | GET | 追踪列表 |

---

## WebSocket（端口 8888）

### 事件格式

```json
{
  "type": "event_type",
  "timestamp": "2026-03-18T12:00:00Z",
  "source": "agent-id",
  "payload": { }
}
```

### 事件类型（Client 订阅）

| 事件 | 含义 |
|------|------|
| `message.new` | 新消息 |
| `message.routed` | 消息已路由到 Agent |
| `agent.thinking_start` | Agent 开始思考 |
| `agent.llm_request` | LLM 调用开始 |
| `agent.llm_response` | LLM 返回 |
| `agent.tool_execute_start` | Agent 开始执行工具 |
| `agent.tool_execute_end` | 工具执行完成 |
| `agent.respond` | Agent 回复 |

### Client → Server

```json
{
  "type": "send_message",
  "payload": {
    "content": "Hello",
    "target_agent": "agent-id"  // 可选：@ 特定 Agent
  }
}
```

---

## 协议版本协商

WebSocket 握手时，Client 发送 `protocol_version` 参数：

```
ws://host:8888?protocol_version=1.0.0
```

Server 根据版本号决定事件格式。旧的 Client 可以用旧的协议版本继续运行。

---

## Trace 数据格式

一条消息从输入到回复的完整链路：

```json
{
  "trace_id": "trace-abc-123",
  "spans": [
    {
      "span_id": "span-1",
      "parent_id": null,
      "name": "message-received",
      "start_time": "2026-03-18T12:00:00Z",
      "duration_ms": 5000,
      "attributes": {"message_id": "msg-1", "sender": "human"}
    },
    {
      "span_id": "span-2",
      "parent_id": "span-1",
      "name": "llm-request",
      "duration_ms": 2000
    }
  ]
}
```

---

## 参考

- [dream.md](./dream.md) — 服务端如何发布事件
- [client.md](./client.md) — 客户端如何订阅事件
- [design/client-server-protocol.md](../design/client-server-protocol.md) — 完整规范
