# 消息系统重构 V2：存储与路由分离

## 背景

原架构存在的问题：
1. Agent 无法获取聊天历史（`room_history = []` 被硬编码）
2. Congress 职责不单一（既管理参与者，又存储消息，又被 ClientGateway 直接操作）
3. EventBus 广播风暴（所有 Agent 收到所有消息，自己过滤）

## 新架构

### 核心原则：无状态路由 + 有状态存储

```
┌─────────────────────────────────────────────────────────────────┐
│                      消息流动层（无状态）                          │
│                                                                 │
│   EventBus → MessageRouter → EventBus (MESSAGE_ROUTED)         │
│                                                                 │
│   - MessageRouter: 纯路由决策，不存储状态                         │
│   - 根据消息内容决定路由目标                                       │
│   - 可水平扩展，重启无影响                                         │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                      消息存储层（有状态）                          │
│                                                                 │
│   CongressStore (FileMessageStore)                              │
│   - 追加写入 JSONL 文件                                          │
│   - 内存缓存（最近 1000 条）                                      │
│   - 支持时间序查询、过滤查询                                       │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 组件职责

| 组件 | 职责 | 状态 |
|-----|------|-----|
| **EventBus** | 事件发布订阅基础设施 | 无状态 |
| **MessageRouter** | 路由决策：谁应该收到这条消息 | 无状态 |
| **CongressStore** | 消息持久化，支持历史查询 | 有状态（磁盘+内存） |
| **Congress** | 参与者管理，协调 join/leave | 轻量级协调器 |
| **Agent** | 通过 MessageStore 查询历史 | 业务逻辑 |
| **ClientGateway** | 维护客户端连接，推送消息 | 连接状态 |

### 消息流

```
1. 用户发送消息
   WebSocket → ClientGateway.send_message() → EventBus(MESSAGE_NEW)

2. 路由与存储（并行）
   MessageRouter ─┬─→ CongressStore.append() (存储)
                  └─→ EventBus(MESSAGE_ROUTED, targets=[...])

3. 分发
   ClientGateway ──(MESSAGE_NEW)──► 推送给所有客户端（显示）
   Target Agents ──(MESSAGE_ROUTED)──► 处理消息（业务）

4. Agent 回复
   Agent._send_message() → EventBus(MESSAGE_NEW)
   
5. 路由存储
   MessageRouter ─┬─→ CongressStore.append()
                  └─→ EventBus(MESSAGE_ROUTED, targets=["@clients"])
   
6. 推送
   ClientGateway ──► 推送给客户端
```

## 关键改进

### 1. Agent 可以获取历史了

```python
# Before
room_history = []  # 硬编码为空

# After
if self.message_store:
    room_history = await self.message_store.get_history(
        limit=20,
        before_id=current_message.id
    )
```

### 2. 路由决策集中化

```python
class MessageRouter:
    def _decide_targets(self, message: Message) -> List[str]:
        if message.sender_type == "human":
            if message.mentions:
                return message.mentions  # 路由给指定 Agent
            else:
                return self._get_proactive_agents()  # 路由给主动 Agent
        elif message.sender_type == "agent":
            return ["@clients"]  # 路由给客户端
```

### 3. Congress 变为协调 Facade

```python
class Congress:
    """只负责参与者管理，存储委托给 MessageStore"""
    
    def __init__(self, ..., message_store: Optional[MessageStore] = None):
        self._message_store = message_store  # 委托存储
    
    async def get_history(self, ...):
        # 委托给 Store
        return await self._message_store.get_history(...)
```

### 4. Agent 订阅路由后的事件

```python
# Before: Agent 订阅所有 MESSAGE_NEW，自己过滤
self._event_subscription = self.event_bus.subscribe(
    EventType.MESSAGE_NEW,
    self._on_message
)

# After: Agent 只收到路由给自己的消息
self._event_subscription = self.event_bus.subscribe(
    EventType.MESSAGE_ROUTED,
    self._on_message_routed  # 只处理给自己的消息
)
```

## 文件变更

### 新增文件
- `src/tlon/organization/messaging/store.py` - MessageStore 抽象和文件实现
- `src/tlon/organization/messaging/router.py` - MessageRouter 实现

### 修改文件
- `src/tlon/organization/messaging/congress.py` - 提取 CongressStore，变为 Facade
- `src/tlon/organization/messaging/__init__.py` - 更新导出
- `src/tlon/base/events/event.py` - 添加 MESSAGE_ROUTED 事件类型
- `src/tlon/agents/agent.py` - 注入 MessageStore，订阅 MESSAGE_ROUTED
- `src/tlon/agents/agent_registry.py` - 传递 MessageStore 给 Agent
- `src/tlon/app/server/serverapp.py` - 重新组装依赖关系

## 依赖关系

```
ServerApp
├── EventBus
├── CongressStore (MessageStore)
├── Congress ──uses──► CongressStore
├── MessageRouter ──uses──► EventBus, CongressStore
├── PositionRegistry
└── AgentRegistry ──creates──► Agent
                               └── message_store: CongressStore
```

## 向后兼容性

- Congress 接口保持不变（`get_history`, `join`, `leave` 等）
- ClientGateway 继续订阅 MESSAGE_NEW（无需修改）
- REST API 和 WebSocket API 保持不变

## 未来扩展

1. **CQRS 模式**: 当历史数据量大时，可引入读模型优化查询
2. **分布式路由**: MessageRouter 可水平扩展，通过一致性哈希分配 Agent
3. **消息索引**: 添加全文搜索、相似度匹配等高级查询
4. **流处理**: 引入 Kafka/Redis Streams 处理高并发场景
