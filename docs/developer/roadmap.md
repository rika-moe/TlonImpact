# Agent 消息发送修复 - Roadmap

## 目标
修复 Agent 消息发送链路断裂问题：Agent 调用 completeDialog 后，消息能通过 EventBus 广播给 Congress（存历史）和 Client（展示给用户）。

## 架构设计
采用完全基于 EventBus 的 Pub-Sub 架构：

```
Agent(completeDialog) → EventBus(MESSAGE_NEW)
                           │
           ┌───────────────┼───────────────┐
           ▼               ▼               ▼
       Congress        TlonServer        Agents
     (存 history)     (推给 Client)   (检查 mention)
```

## Task 列表

### Task 1: Congress 改造 - 移除 Client Handler 机制 ✅
**状态**: 已完成

### Task 2: TlonServer 改造 - 订阅 EventBus 推送给 Client ✅
**状态**: 已完成

### Task 3: Agent 改造 - 通过 EventBus 发送消息 ✅
**状态**: 已完成

---

### Task 1: Congress 改造 - 移除 Client Handler 机制 (历史)
**文件**: `src/tlon/organization/messaging/congress.py`

- [x] 删除 `_client_handlers`、`_handler_counter`、`_handler_lock`
- [x] 删除 `register_client_handler()`、`unregister_client_handler()`
- [x] 删除 `_broadcast_to_clients()`、`_run_client_handler()`
- [x] `send_message()` 只保留：存 history + publish EventBus，删除 broadcast 逻辑
- [x] `join()`/`leave()` 改为 publish 系统消息（不再调用 send_message）
- [x] 新增 `_on_message_new()` 订阅回调，只处理 Agent 消息存入 history

### Task 2: TlonServer 改造 - 订阅 EventBus 推送给 Client
**文件**: `src/tlon/app/server/core/server.py`

- [x] `send_message()` 改为 publish EventBus，不再调用 congress.send_message()
- [x] `initialize()` 订阅 `MESSAGE_NEW`，回调里推给所有 Client sessions
- [x] 移除 `_congress_handler_ids` 相关逻辑
- [x] `connect_client()` 不再注册 Congress handler，改为等待 EventBus 消息
- [x] 添加 `_session_handlers` 存储每个 session 的 event handler

### Task 3: Agent 改造 - 通过 EventBus 发送消息
**文件**: `src/tlon/agents/agent.py`

- [x] `_process_message()` completeDialog 时，构造 Message 并 `event_bus.publish(MESSAGE_NEW)`
- [x] 修改 `_send_message()` 方法使用 EventBus 发送消息

### Task 4: ServerApp - 确保启动顺序正确
**文件**: `src/tlon/app/server/serverapp.py`

- [ ] 确保 Congress 在 AgentRegistry 之前初始化（订阅者优先）
- [ ] 确认事件订阅在组件初始化时完成

## 验证方式
1. 启动 server: `tlon server`
2. 启动 tui: `tlon tui`
3. 发送消息给 Agent
4. 验证：Agent 的回复出现在 TUI 中（证明消息通过 EventBus 广播给了 Client）

## 依赖关系
```
Task 1 (Congress)  ──┐
Task 2 (TlonServer) ─┼──► 可同时开始
                     │
Task 3 (Agent) ◄─────┘  (依赖 Task 1, 2 的事件订阅就绪)
```

## 实施完成总结

### 修改的文件
1. `src/tlon/organization/messaging/congress.py` - 移除 Client Handler，订阅 MESSAGE_NEW 存 Agent 消息
2. `src/tlon/app/server/core/server.py` - 订阅 MESSAGE_NEW 推送给 Client，send_message 改为 publish
3. `src/tlon/agents/agent.py` - completeDialog 时 publish MESSAGE_NEW

### 消息流向（修复后）

**Human 输入 -> Agent 接收**:
```
Human -> TlonServer.send_message() 
    -> EventBus.publish(MESSAGE_NEW)
    -> Congress._on_message_new() [存 history]
    -> Agent._on_message() [响应]
```

**Agent 响应 -> Human 接收**:
```
Agent._process_message() [completeDialog]
    -> EventBus.publish(MESSAGE_NEW)
    -> Congress._on_message_new() [存 history]
    -> TlonServer._on_message_new() [推给 Client]
    -> Client (TUI/Web)
```

### 架构变化
- **Congress**: 不再直接广播给 Client，只负责存储和持久化
- **TlonServer**: 通过 EventBus 接收消息，推送给所有连接的 Client
- **Agent**: 通过 EventBus 发送消息，不再依赖 Congress
- **组件间通信**: 完全通过 EventBus，实现解耦
