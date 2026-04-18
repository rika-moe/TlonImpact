# 组织层 (Organization)

> 状态: ✅ 已验证
> 最后验证: 2026-04-19

---

## 职责

管理多 Agent 协作的组织基础设施：消息路由、存储、参与者管理

---

## 模块结构

```
organization/
└── messaging/              # 消息系统
    ├── message.py         # Message 数据模型
    ├── participant.py     # Participant 参与者抽象
    ├── router.py          # MessageRouter 消息路由
    └── store.py           # MessageStore 消息存储
```

---

## 关键类

| 类 | 职责 | 路径 |
|----|------|------|
| `Message` | 消息数据模型 | `organization/messaging/message.py` |
| `Participant` | 参与者抽象 | `organization/messaging/participant.py` |
| `MessageRouter` | 消息路由决策（MESSAGE_NEW → MESSAGE_ROUTED） | `organization/messaging/router.py` |
| `MessageStore` | 消息持久化存储（JSONL） | `organization/messaging/store.py` |
| `RoutedMessage` | 路由后的消息（含 target_ids） | `organization/messaging/router.py` |

---

## MessageRouter 职责

- 订阅 `MESSAGE_NEW` 事件
- 将消息持久化到 `MessageStore`
- 决定消息应该发送给哪些 Agent（路由决策）
- 发布 `MESSAGE_ROUTED` 事件

路由逻辑：
- 人类消息 + @mention → 路由给被提及的 Agent
- 人类消息 + 无 mention → 路由给所有 proactive Agent
- Agent 消息 → 路由给 @clients + 被提及的 Agent

---

## 文档索引

| 文档 | 目的 | 深度 |
|------|------|------|
| [../../design/messaging.md](../../design/messaging.md) | 消息系统设计 | L1 |

---

## 验证清单

- [x] MessageRouter 使用 EventBus 通信
- [x] Message 格式与代码一致
- [x] `Congress` 类已移除，职责由 MessageRouter + MessageStore 承担
