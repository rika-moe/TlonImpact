# DAG Component Architecture

> **TL;DR**: ServerApp components form a Directed Acyclic Graph (DAG). Dependencies are injected via constructor only. **Component communication happens through EventBus (indirect)**. This ensures no circular dependencies and clear component boundaries.

---

## 架构定位

本文档描述**模块内部**的组件组织方式。配合 [modules.md](./modules.md) 阅读：
- `modules.md`: 模块间关系（宏观）
- `dag-components.md`: 模块内组件关系（微观）

---

## Core Principles

### 1. DAG Structure (有向无环图)

```
┌─────────────────────────────────────────────────────────────┐
│                     ServerApp (DI Container)                 │
│                    (The only place assembly happens)         │
└─────────────────────────────────────────────────────────────┘
                              │
    ┌─────────────┬───────────┼───────────┬─────────────┐
    ▼             ▼           ▼           ▼             ▼
┌───────┐    ┌───────┐  ┌────────┐  ┌──────────┐  ┌─────────┐
│Config │    │EventBus│  │Message │  │Session  │
│Manager│    │        │  │Store   │  │Manager  │
└───────┘    └───┬───┘  └───┬────┘  └────┬────┘
    │            │          │            │
    │            │          ▼            │
    │            │    ┌──────────┐       │
    │            └───►│Message   │       │
    │                 │Router    │       │
    │                 └────┬─────┘                       │
    │                      │                             │
    │            ┌─────────┘                             │
    │            ▼                                        │
    │       ┌──────────┐    ┌─────────────────┐          │
    └──────►│Agent     │◄───│Notification     │◄─────────┘
            │Registry  │    │Service          │
            └────┬─────┘    └────────┬────────┘
                 │                   │
                 │         ┌─────────┘
                 │         ▼
                 │    ┌──────────┐
                 └───►│Client    │
                      │Gateway   │
                      └──────────┘
```

**Rules:**
- Arrows point from dependency to dependent
- No cycles allowed
- ServerApp is the sole assembler

### 2. Constructor Injection Only (仅构造函数注入)

**✅ DO:**
```python
class MessageRouter(Component):
    def __init__(
        self,
        event_bus: EventBus,      # Constructor injection
        message_store: MessageStore,
    ):
        super().__init__(name="MessageRouter")
        self._event_bus = event_bus   # Store as attribute
```

**❌ DON'T:**
```python
# Setter injection
class MessageRouter(Component):
    def set_event_bus(self, event_bus: EventBus):  # FORBIDDEN
        self._event_bus = event_bus

# Property injection  
class MessageRouter(Component):
    @property
    def event_bus(self) -> EventBus:
        return self._event_bus
    
    @event_bus.setter
    def event_bus(self, value: EventBus):  # FORBIDDEN
        self._event_bus = value

# Runtime replacement
class MessageRouter(Component):
    def replace_event_bus(self, new_bus: EventBus):  # FORBIDDEN
        self._event_bus = new_bus
```

### 3. EventBus for Communication (通过 EventBus 间接通信)

**✅ DO:**
```python
# Publishing
await self._event_bus.publish(Event(
    type=EventType.MESSAGE_NEW,
    payload=message,
    source=self.id,
))

# Subscribing (in initialize())
async def initialize(self) -> None:
    self._subscription_id = self._event_bus.subscribe(
        EventType.MESSAGE_ROUTED,
        self._on_message
    )
```

**❌ DON'T:**
```python
# Direct method calls between components (unless Facade pattern)
other_component.do_something()  # AVOID

# Implicit dependencies (global state)
from some_module import global_bus  # FORBIDDEN
global_bus.publish(...)
```

---

## Component Lifecycle

All components inherit from `Component` base class:

```python
from tlon.base.component import Component

class MyComponent(Component):
    def __init__(self, dependency: OtherComponent):
        """Step 1: Construction
        
        - Receive dependencies via constructor
        - Store as attributes
        - NO EventBus subscription here
        """
        super().__init__(name="MyComponent")
        self._dependency = dependency
    
    async def initialize(self) -> None:
        """Step 2: Initialization
        
        - Subscribe to EventBus events
        - Start background tasks
        - Connect to external resources
        """
        self._subscription_id = self._event_bus.subscribe(...)
        self._initialized = True  # Set by base class
    
    async def shutdown(self) -> None:
        """Step 3: Shutdown
        
        - Unsubscribe from EventBus
        - Stop background tasks
        - Release resources
        """
        if self._subscription_id:
            self._event_bus.unsubscribe(self._subscription_id)
        self._initialized = False  # Set by base class
```

**Lifecycle managed by ServerApp:**

```python
class ServerApp:
    async def initialize(self) -> None:
        # 1. Create components in topological order
        event_bus = EventBus()
        message_store = FileMessageStore(...)
        message_router = MessageRouter(event_bus, message_store)
        
        # 2. Initialize all components
        await event_bus.initialize()
        await message_store.initialize()
        await message_router.initialize()
    
    async def shutdown(self) -> None:
        # Shutdown in reverse order
        await message_router.shutdown()
        await message_store.shutdown()
        await event_bus.shutdown()
```

---

## Component Registry

For managing multiple components, use `ComponentRegistry`:

```python
from tlon.base.component import ComponentRegistry

class ServerApp:
    def __init__(self):
        self._registry = ComponentRegistry()
    
    async def initialize(self) -> None:
        # Create and register components
        event_bus = EventBus()
        self._registry.register(event_bus)
        
        message_router = MessageRouter(event_bus, ...)
        self._registry.register(message_router)
        
        # Initialize all in registration order
        await self._registry.initialize_all()
    
    async def shutdown(self) -> None:
        # Shutdown all in reverse order
        await self._registry.shutdown_all()
```

---

## Current Components

| Component | Dependencies | Responsibility |
|-----------|--------------|----------------|
| `EventBus` | None | Event publish/subscribe infrastructure |
| `ConfigService` | `EventBus` | Configuration loading, caching, saving, and EventBus notifications |
| `MessageStore` | File path | Message persistence |
| `MessageRouter` | `EventBus`, `MessageStore`, `AgentInfoProvider` | Message routing decisions |
| `AgentRegistry` | `EventBus`, `AgentInfoProvider`, `MessageStore` | Agent lifecycle management |
| `SessionManager` | None | Client session lifecycle |
| `NotificationService` | `EventBus`, `SessionManager` | Push events to clients |
| `ParticipantDirectory` | `AgentRegistry`, `SessionManager` | Participant aggregation |
| `ClientGateway` | Multiple | Facade for client-facing operations |

---

## Benefits

1. **No Circular Dependencies**: Constructor injection + EventBus communication prevents cycles
2. **Clear Boundaries**: Each component's dependencies are explicit
3. **Testability**: Easy to mock dependencies in tests
4. **Maintainability**: Changes are localized to components
5. **Visibility**: Architecture is explicit in code

---

## Migration Guide

### Making an existing class a Component

```python
# Before
class MyService:
    def __init__(self):
        self._event_bus = EventBus()  # Created internally
    
    async def start(self):
        self._event_bus.start()
    
    async def stop(self):
        self._event_bus.stop()

# After
from tlon.base.component import Component

class MyService(Component):
    def __init__(self, event_bus: EventBus):  # Injected
        super().__init__(name="MyService")
        self._event_bus = event_bus
    
    async def initialize(self):
        self._subscription = self._event_bus.subscribe(...)
        self._initialized = True
    
    async def shutdown(self):
        self._event_bus.unsubscribe(self._subscription)
        self._initialized = False
```

### ServerApp integration

```python
# Before
my_service = MyService()  # Created internally
await my_service.start()

# After  
event_bus = EventBus()  # Created by ServerApp
my_service = MyService(event_bus)  # Injected
await my_service.initialize()
```

---

## Enforcement

Architecture rules are enforced by:

1. **Code Review**: Check for setter injection, direct calls
2. **Type Hints**: Constructor dependencies are explicit
3. **Base Class**: `Component` provides standard interface
4. **Documentation**: This document as reference

---

## 与模块架构的关系

DAG 组件架构是**模块内部**的组织原则：

```
┌─────────────────────────────────────────────────────────────────┐
│  AgentModule (模块边界)                                         │
│                                                                 │
│  ┌─────────────┐      EventBus      ┌─────────────┐            │
│  │    Agent    │ ◄────────────────► │ ToolExecutor│            │
│  │  (Device)   │   (跨模块通信)      │ (Component) │            │
│  └──────┬──────┘                    └──────┬──────┘            │
│         │                                  │                    │
│         │ 直接调用                          │ 工具注册           │
│         ▼                                  ▼                    │
│  ┌─────────────┐                    ┌─────────────┐            │
│  │  LLMClient  │                    │ExecuteBash  │            │
│  │  (Component)│                    │    Tool     │            │
│  └─────────────┘                    └──────┬──────┘            │
│                                            │                    │
│                                            │ 实现细节           │
│                                            ▼                    │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │              SandboxModule (外部模块)                    │   │
│  │                 DockerRuntime                            │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

**关键理解**:
1. **Agent ↔ ToolExecutor**: 模块内组件，通过构造函数组装
2. **ToolExecutor → Tool**: 插件注册，Tool 声明自己的依赖
3. **ExecuteBashTool → DockerRuntime**: 跨模块依赖（允许，但仅限于 Tool 实现）
4. **Agent ×× DockerRuntime**: Agent 不直接依赖 Sandbox（架构规则）

参见: [modules.md](./modules.md) 了解模块间通信规则。

---

## References

- [Component Base Class](../../../src/tlon/base/component.py)
- [ServerApp](../../../src/tlon/app/server/serverapp.py) - See DI assembly
- [Modules Architecture](./modules.md) - Module-level organization
