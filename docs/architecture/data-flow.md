# 数据流向

> 状态: ✅ 已验证
> 最后验证: 2026-04-19

---

## 用户输入处理流

```
用户输入 → WebSocket → ClientGateway → EventBus(MESSAGE_NEW)
                                                 │
                    ┌────────────────────────────┼────────────────────────────┐
                    │                            │                            │
                    ▼                            ▼                            ▼
            MessageStore                   MessageRouter                  NotificationService
            (保存历史)                      (路由决策)                      (推送给客户端)
                    │                            │                            │
                    │                            ▼                            │
                    │                    EventBus(MESSAGE_ROUTED)             │
                    │                            │                            │
                    │              ┌─────────────┼─────────────┐              │
                    │              │             │             │              │
                    ▼              ▼             ▼             ▼              ▼
            存储到 JSONL      Agent A      Agent B      Agent C        客户端显示
```

---

## Agent 消息处理流

```
┌─────────┐   ┌─────────┐   ┌─────────┐   ┌─────────┐   ┌─────────┐
│  User   │──▶│   CLI   │──▶│  Server │──▶│EventBus │──▶│Message  │
│         │   │         │   │         │   │         │   │ Router  │
└─────────┘   └─────────┘   └─────────┘   └─────────┘   └────┬────┘
                                                             │
                                                             ▼
                                                        ┌─────────┐
                                                        │  Agent  │
                                                        └────┬────┘
                                                             │
                    ┌─────────────────────────────────────────┤
                    │                                         │
                    ▼                                         ▼
             ┌────────────┐                           ┌────────────┐
             │  Memory    │                           │    LLM     │
             │  (Context) │◀─────────────────────────▶│   Think    │
             └────────────┘                           └─────┬──────┘
                                                            │
                              ┌─────────────────────────────┼─────────┐
                              │                             │         │
                              ▼                             ▼         ▼
                       ┌────────────┐                ┌──────────┐ ┌──────────┐
                       │ChatRoom    │◀───────────────│   Tool   │ │  Tool    │
                       │Broadcast   │                │  Result  │ │ Execution│
                       └────────────┘                └──────────┘ └──────────┘
```

---

## 关键流程

### 1. 消息处理流程
1. 接收消息 (`on_message`)
2. 判断是否响应 (`_should_respond`)
3. 构建 Context (`_build_context`)
4. LLM 思考 (`think`)
5. 执行工具 (`execute_tool`)
6. 发送响应 (`send_message`)
7. 保存记忆 (`save_to_memory`)

### 2. Memory 生命周期
1. Load Episodic (启动时)
2. Interaction (Working Memory)
3. Summarization (触发时)
4. Store Episodic

---

## 验证清单

- [x] 数据流与代码一致
- [x] Event 类型定义正确
- [x] 持久化策略明确
