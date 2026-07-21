# dream 进程：ServerApp 架构

> 命令：`tlon dream`（代码中类名为 `ServerApp`）
> dream 是 Tlon 的核心进程。它承载 Agent、路由消息、执行工具、存储记忆。
> 结构：**EventBus 解耦 + DAG 组件树 + 依赖注入组装**

---

- [架构全景](#架构全景)
- [组件设计原则](#组件设计原则)
- [ServerApp：唯一的组装点](#serverapp唯一的组装点)
- [消息全生命周期](#消息全生命周期)

---

## 架构全景

```
┌─────────────────────────────────────────────────────────────┐
│              ServerApp（DI 容器，唯一组装点）                 │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │      基础设施 + 消息总线                              │   │
│  │  EventBus (publish/subscribe) ← 所有模块间通信经过   │   │
│  │  Config | Logger | Tracing                           │   │
│  └────────────────────┬────────────────────────────────┘   │
│                       │                                    │
│                       ▼                                    │
│  ┌─────────────────────────────────────────────────────┐   │
│  │                  核心域模块                          │   │
│  │                                                     │   │
│  │  AgentModule        MessageModule     ClientModule  │   │
│  │  ┌────────────┐    ┌────────────┐    ┌────────────┐ │   │
│  │  │ Agent      │    │Message     │    │ Session    │ │   │
│  │  │            │    │Router      │    │ Manager    │ │   │
│  │  │ ToolExecutor│   │MessageStore│    │ Gateway    │ │   │
│  │  │ LLMClient  │    │Participant │    │ REST/WS    │ │   │
│  │  └────────────┘    └────────────┘    └────────────┘ │   │
│  │  src/tlon/agents/  org/messaging/  app/server/      │   │
│  └──────────────────────┬──────────────────────────────┘   │
│                         │ DI                               │
│  ┌──────────────────────▼──────────────────────────────┐   │
│  │                  功能子模块                          │   │
│  │                                                     │   │
│  │  Sandbox       Memory          Tasks      Tools     │   │
│  │  DockerRuntime EpisodicStore   TaskMgr    ExecuteBash│   │
│  │                WorkingStore               Remember  │   │
│  │                Summarizer                  Fact     │   │
│  │  sandbox/      memory/         tools/     tools/    │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

三类模块：

| 类别 | 模块 | 位置 | 职责 |
|------|------|------|------|
| **基础设施** | EventBus / Config / Logger / Tracing | `base/` | 零业务逻辑，所有模块通过 EventBus 间接通信 |
| **核心域** | AgentModule / MessageModule / ClientModule | `agents/`, `org/`, `app/server/` | 业务逻辑：Agent、消息路由、会话管理 |
| **功能子模块** | Sandbox / Memory / Tasks / Tools | `sandbox/`, `memory/`, `tools/` | 实现细节：Docker 执行、记忆存储、工具插件 |

---

## 组件设计原则

### 构造函数注入唯一

```python
class MessageRouter(Component):
    def __init__(self, event_bus: EventBus, message_store: MessageStore):
        super().__init__(name="MessageRouter")
        self._event_bus = event_bus
        self._message_store = message_store
```

```python
# ❌ 以下全部禁止：
# class MessageRouter:
#     def set_event_bus(self, bus): ...     # setter 注入
#     @event_bus.setter                      # 属性注入
#     def replace_event_bus(self, bus): ...  # 运行时替换
```

### EventBus 通信

```python
# 模块间：发布/订阅
await self._event_bus.publish(Event(
    type=EventType.MESSAGE_NEW, payload=message, source=self.id,
))

# 模块内：直接调用（Agent → ToolExecutor 同模块）
result = await self.tool_executor.execute("executeBashCommand", {...})
```

### 依赖方向规则

```text
✅ 允许：
  Infrastructure ← CoreDomains ← FunctionalSubmodules
  ToolsModule ← Sandbox / Memory / Tasks

❌ 禁止：
  FunctionalSubmodules → CoreDomains（反向 → 循环）
  Sandbox → AgentModule（反向）
```

---

## ServerApp：唯一的组装点

```python
class ServerApp:
    async def initialize(self):
        # 1. 基础设施
        event_bus = EventBus()
        config = ConfigService(event_bus)
        init_tracing(paths.traces_dir())   # OTel + JSONL exporter
        trace_store = FileTraceStore(...)  # 纯读，供 REST /traces
        # 2. 功能模块
        docker = DockerRuntime(config)
        memory = MemoryManager(event_bus)
        # 3. 工具插件
        executor = ToolExecutor(event_bus)
        executor.register(ExecuteBashTool(docker))
        executor.register(RememberFactTool(memory))
        # 4. 核心域
        agent = Agent(config, event_bus, memory, executor, ...)
        router = MessageRouter(event_bus, message_store)
        gateway = ClientGateway(session_manager, notification_service)
```

DAG 组件树的拓扑序保证无循环依赖。初始化顺序即依赖顺序。

---

## 消息全生命周期

一条消息从用户输入到 Agent 回复的完整调用链：

```text
1. WebSocket → ClientGateway.on_message()
     → EventBus.publish(MESSAGE_NEW)

2. MessageRouter (subscribe MESSAGE_NEW)
     → 解析 @mentions, 确定 target_agent
     → _should_respond()? 检查：
         sender 不是自己
         proactive（客服）无条件响应
         passive（开发/PM）仅被 @ 时响应
     → 保存到 MessageStore
     → EventBus.publish(MESSAGE_ROUTED)

3. Agent (subscribe MESSAGE_ROUTED)
     → _should_respond()? 再次检查（防循环）
     → build_prompt()
         ├── System Prompt（静态配置）
         ├── Episodic Memory（相关摘要）
         ├── Working Memory（最近 10 条）
         └── 当前消息
     → 创建 AgentSession → 进入主循环调度
     → _step_session: LLM.think() → 派发工具调用（asyncio.create_task）
     → _collect_session_results: 收集后台工具结果
         ExecuteBash → DockerRuntime.run()
         RememberFact → MemoryManager.store()
         SearchMemories → MemoryManager.search()
     → sendMessage(is_complete=true) → Session COMPLETED → 保存记忆
     → EventBus.publish(MESSAGE_NEW)

4. NotificationService (subscribe MESSAGE_NEW)
     → EventFilter（按 Client 类型过滤）
         TUI: 全量 | Web: 排除 llm_request
     → WebSocket.send() → TUI Client

5. Memory 归档（异步）
     → Working 满 10 条 → 自动摘要
     → 空闲 30 分钟 → 自动归档
```

### 常见排查点

| 现象 | 检查位置 |
|------|---------|
| Agent 不响应 | `_should_respond()` → 是否被 @？proactive 配置？ |
| 消息没到 Agent | MessageRouter → @mention 解析正确吗？ |
| Agent 回复了但用户看不到 | EventFilter → 被过滤了吗？ |
| 工具执行失败 | ToolExecutor → 工具注册了吗？参数对吗？ |
| 记忆没保存 | MemoryManager → working 满了吗？摘要触发了？ |

---

## 参考

- [explicit-boundaries.md](./explicit-boundaries.md) — 架构边界规则
- [protocol.md](./protocol.md) — 进程间通信协议
- [dag-components.md 旧版](.archive/dag-components.md) — 历史设计文档
- [modules.md 旧版](.archive/modules.md) — 历史设计文档
