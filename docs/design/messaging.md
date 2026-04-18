# 消息系统设计

> 状态: ✅ 已验证
> 最后验证: 2026-04-19

---

## 核心理念

1. **EventBus 解耦**: 组件间不直接调用，通过事件通信
2. **代表大会 (Congress) 概念**: 聊天室统一管理消息流（由 MessageRouter + MessageStore 实现，无 `Congress` 类）
3. **MessageRouter**: 智能路由消息到目标 Agent
4. **持久化**: 所有消息实时写入 JSONL

---

## 事件类型

| 事件 | 说明 | 发布者 |
|------|------|--------|
| `MESSAGE_NEW` | 新消息 | ClientGateway |
| `MESSAGE_ROUTED` | 消息已路由 | MessageRouter |
| `AGENT_RESPONSE` | Agent 响应 | Agent |
| `AGENT_THINKING_START` | Agent 开始思考 | Agent |
| `AGENT_THINKING_END` | Agent 思考结束 | Agent |
| `AGENT_LLM_REQUEST` | LLM 请求发出 | Agent |
| `AGENT_LLM_RESPONSE` | LLM 响应收到 | Agent |
| `TOOL_EXECUTE_START` | 工具执行开始 | Agent |
| `TOOL_EXECUTE_END` | 工具执行结束 | Agent |

---

## 数据流向

```
用户输入 → WebSocket → ClientGateway → EventBus(MESSAGE_NEW)
                                              │
                    ┌─────────────────────────┼─────────────────────────┐
                    ▼                         ▼                         ▼
              MessageStore              MessageRouter          NotificationService
              (保存历史)                (路由决策)                (推送客户端)
                                              │
                                              ▼
                                       EventBus(MESSAGE_ROUTED)
                                              │
                    ┌─────────────────────────┼─────────────────────────┐
                    ▼                         ▼                         ▼
                 Agent A                   Agent B                   Agent C
```

---

## 文档索引

| 文档 | 目的 | 深度 |
|------|------|------|
| [architecture/domains/organization.md](../architecture/domains/organization.md) | 组织层架构 | L2 |

---

## 验证清单

- [x] EventBus 实现与代码一致
- [x] MessageRouter 职责边界清晰
- [x] `Congress` 类已移除，由 MessageRouter + MessageStore 承担
- [x] 消息存储格式为 JSONL
