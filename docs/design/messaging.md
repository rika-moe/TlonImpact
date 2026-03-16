# 消息系统设计

> 状态: ✅ 已验证
> 最后验证: 2026-03-17

---

## 核心理念

1. **EventBus 解耦**: 组件间不直接调用，通过事件通信
2. **Congress 管理**: 聊天室（代表大会）统一管理消息流
3. **MessageRouter**: 智能路由消息到目标 Agent
4. **持久化**: 所有消息实时写入 JSONL

---

## 事件类型

| 事件 | 说明 | 发布者 |
|------|------|--------|
| `MESSAGE_NEW` | 新消息 | ClientGateway |
| `MESSAGE_ROUTED` | 消息已路由 | MessageRouter |
| `AGENT_RESPONSE` | Agent 响应 | Agent |

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
- [x] Congress 职责边界清晰
- [x] 消息存储格式为 JSONL
