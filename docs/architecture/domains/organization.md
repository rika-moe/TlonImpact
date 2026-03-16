# 组织层 (Organization)

> 状态: ✅ 已验证
> 最后验证: 2026-03-17

---

## 职责

管理多 Agent 协作的组织基础设施：消息系统和岗位系统

---

## 模块结构

```
organization/
├── messaging/              # 消息系统
│   ├── congress.py        # Congress (代表大会) 聊天室
│   ├── message.py         # Message 类
│   └── __init__.py
└── positions/             # 岗位系统
    ├── models.py          # Position 模型
    ├── registry.py        # PositionRegistry
    └── prompt_renderer.py # Prompt 渲染
```

---

## 关键类

| 类 | 职责 | 路径 |
|----|------|------|
| `Congress` | 聊天室状态管理 | `organization/messaging/congress.py` |
| `Message` | 消息协议 | `organization/messaging/message.py` |
| `PositionRegistry` | 岗位注册表 | `organization/positions/registry.py` |

---

## Congress 职责

- 参与者管理
- 消息历史持久化
- 消息路由协调

---

## 文档索引

| 文档 | 目的 | 深度 |
|------|------|------|
| [../../design/messaging.md](../../design/messaging.md) | 消息系统设计 | L1 |

---

## 验证清单

- [x] Congress 使用 EventBus 通信
- [x] Message 格式与代码一致
- [x] Position 配置字段正确
