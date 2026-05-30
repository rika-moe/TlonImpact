# 模块架构 (Module Architecture)

> 状态: ✅ 已验证
> 最后验证: 2026-04-19
> 核心理念: **按功能域划分模块，按通信模式定义关系**

---

## 与分层架构的区别

| 维度 | 分层架构 (Layered) | 模块架构 (Modular) |
|------|-------------------|-------------------|
| **组织方式** | 按抽象层次（上→下） | 按功能域（左→右） |
| **通信描述** | 隐式（层间调用） | 显式（EventBus 事件） |
| **依赖关系** | 上层依赖下层 | 模块间通过接口解耦 |
| **扩展方式** | 在特定层添加 | 新增模块或插件 |

**关键洞察**: Tlon 是模块化的、以通信为中心的系统，而非严格的分层系统。

---

## 架构全景图

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           基础设施模块 (Infrastructure)                       │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐ │
│  │  EventBus   │  │   Config    │  │   Logger    │  │      Tracing        │ │
│  │  (消息总线)  │  │  (配置管理)  │  │  (日志系统)  │  │     (链路追踪)       │ │
│  │             │  │             │  │             │  │                     │ │
│  │ • publish() │  │ • Loader    │  │ • 结构化日志 │  │ • TraceContext     │ │
│  │ • subscribe()│ │ • Models    │  │ • 上下文追踪 │  │ • FileTraceStore   │ │
│  └──────┬──────┘  └─────────────┘  └─────────────┘  └─────────────────────┘ │
│         │                                                                   │
│         │ 所有核心模块通过 EventBus 进行间接通信                                │
└─────────┼───────────────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                            核心域模块 (Core Domains)                          │
│                                                                             │
│  ┌─────────────────────┐    ┌─────────────────────┐    ┌─────────────────┐ │
│  │    AgentModule      │    │   MessageModule     │    │  ClientModule   │ │
│  │                     │    │                     │    │                 │ │
│  │  ┌───────────────┐  │    │  ┌───────────────┐  │    │ ┌─────────────┐ │ │
│  │  │     Agent     │  │    │  │MessageRouter  │  │◄───│ │  Session    │ │ │
│  │  │ (ReAct Loop)  │  │    │  │  (路由决策)    │  │    │ │  Manager    │ │ │
│  │  └───────┬───────┘  │    │  └───────┬───────┘  │    │ └─────────────┘ │ │
│  │          │          │    │          │          │    │                 │ │
│  │  ┌───────▼───────┐  │    │  ┌───────▼───────┐  │    │ ┌─────────────┐ │ │
│  │  │ ToolExecutor  │  │    │  │ MessageStore  │  │    │ │ Notification│ │ │
│  │  │   (Direct)    │  │    │  │  (消息存储)    │  │    │ │   Service   │ │ │
│  │  └───────────────┘  │    │  └───────────────┘  │    │ └─────────────┘ │ │
│  │                     │    │                     │    │                 │ │
│  │  ┌───────────────┐  │    │  ┌───────────────┐  │    │ ┌─────────────┐ │ │
│  │  │  LLMClient    │  │    │  │ Participant   │  │    │ │ REST/WebSocket│ │
│  │  │              │  │    │  │  Directory    │  │    │ │   Servers   │ │ │
│  │  └───────────────┘  │    │  └───────────────┘  │    │ └─────────────┘ │ │
│  └──────────┬──────────┘    └─────────────────────┘    └─────────────────┘ │
│             │                                                               │
│             │ 插件接口 (组合关系，非层次依赖)                                    │
└─────────────┼───────────────────────────────────────────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                        功能子模块 (Functional Submodules)                     │
│                                                                             │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐ │
│  │   Sandbox   │  │    Memory   │  │    Tasks    │  │       Tools         │ │
│  │   Module    │  │   Module    │  │   Module    │  │      Module         │ │
│  │             │  │             │  │             │  │                     │ │
│  │DockerRuntime│  │EpisodicStore│  │ TaskManager │  │ • Tool (base)       │ │
│  │(容器执行)   │  │WorkingStore │  │             │  │ • ExecuteBashTool   │ │
│  │             │  │Summarizer   │  │             │  │ • RememberFactTool  │ │
│  └─────────────┘  └─────────────┘  └─────────────┘  │ • CreateTaskTool    │ │
│                                                      │ • ...               │ │
│                                                      └─────────────────────┘ │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                     关键依赖关系说明                                 │    │
│  │                                                                     │    │
│  │  ExecuteBashTool ──► DockerRuntime (实现细节，非模块依赖)            │    │
│  │  RememberFactTool ──► MemoryModule (功能域依赖)                      │    │
│  │  CreateTaskTool ──► TaskModule (功能域依赖)                          │    │
│  │                                                                     │    │
│  │  ⚠️ Sandbox 不是独立层次，只是 executeBashCommand 的实现方式         │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 模块详解

### 1. 基础设施模块 (Infrastructure)

| 模块 | 职责 | 关键接口 | 被谁依赖 |
|------|------|----------|----------|
| **EventBus** | 异步消息总线，模块间解耦通信 | `publish()`, `subscribe()` | 所有核心模块 |
| **Config** | 配置加载、缓存、保存、验证 | `ConfigService`, `AgentConfig` | ServerApp, Agents |
| **Logger** | 结构化日志、上下文追踪 | `get_logger()`, `log_context` | 所有模块 |
| **Tracing** | 分布式链路追踪 | `TraceContext`, `TraceStore` | EventBus, Agents |

**设计原则**: 基础设施模块无业务逻辑，不依赖其他业务模块。

---

### 2. 核心域模块 (Core Domains)

#### 2.1 AgentModule (`tlon/agents/`)

**职责**: 智能体生命周期管理与 ReAct 推理

**组成**:
```
agents/
├── agent.py              # Agent 类 - ReAct 推理与执行
├── tool_executor.py      # 工具注册与直接执行
├── llm_client.py         # LLM API 客户端
├── prompt_builder.py     # 系统提示构建
└── agent_registry.py     # Agent 生命周期管理
```

**依赖关系**:
```python
# Agent 通过构造函数接收依赖（依赖注入）
class Agent(Component, Device):
    def __init__(
        self,
        config: AgentConfig,
        event_bus: EventBus,           # ◄── 基础设施
        llm_client: LLMClient,         # ◄── 同模块
        memory: MemoryManager,         # ◄── Memory 模块
        task_manager: TaskManager,     # ◄── Tasks 模块
        tool_executor: ToolExecutor,   # ◄── 同模块
        team_members: list[AgentConfig] | None = None,
        message_store: MessageStore | None = None,
        ...
    ):
```

**通信模式**:
- 接收: 订阅 `MESSAGE_ROUTED` 事件
- 发送: 发布 `MESSAGE_NEW` 事件

---

#### 2.2 MessageModule (`tlon/organization/messaging/`)

**职责**: 消息路由、存储、参与者管理

**组成**:
```
organization/messaging/
├── router.py       # MessageRouter - 路由决策
├── store.py        # MessageStore - 持久化存储
├── participant.py  # Participant - 参与者抽象
└── message.py      # Message - 消息模型
```

**通信模式**:
- 订阅: `MESSAGE_NEW` → 路由决策
- 发布: `MESSAGE_ROUTED` → 通知目标 Agent

---

#### 2.3 ClientModule (`tlon/app/server/`)

**职责**: 客户端接入与会话管理

**组成**:
```
app/server/
├── core/
│   ├── client_gateway.py       # 客户端门面
│   ├── session_manager.py      # WebSocket 会话管理
│   ├── notification_service.py # 事件推送
│   └── participant_directory.py # 在线参与者聚合
├── api/
│   ├── rest.py                 # REST API
│   └── websocket.py            # WebSocket Server
└── serverapp.py                # DI 容器与组装
```

---

### 3. 功能子模块 (Functional Submodules)

#### 3.1 SandboxModule (`tlon/sandbox/`)

**定位**: `executeBashCommand` 的实现细节

**关键澄清**:
- ❌ **不是**独立架构层次
- ✅ **是** ExecuteBashTool 的依赖，仅被其使用

```python
# tools/shell/execute_bash.py
class ExecuteBashTool(Tool):
    def __init__(self, docker_runtime: DockerRuntime):
        # DockerRuntime 是 Tool 的内部实现细节
        self.docker_runtime = docker_runtime
```

**模块边界**:
```
AgentModule
    └── ToolExecutor (Agent 直接调用)
            └── 注册 ExecuteBashTool
                    └── 内部使用 DockerRuntime (SandboxModule)
```

**规则**: 除 ExecuteBashTool 外，其他模块不应直接导入 `DockerRuntime`。

---

#### 3.2 MemoryModule (`tlon/memory/`)

**职责**: 长期记忆存储与检索

**组成**:
```
memory/
├── manager.py        # MemoryManager - 统一接口
├── episodic_store.py # 情景记忆存储
├── working_store.py  # 工作记忆
├── summarizer.py     # 摘要生成
└── models.py         # 数据模型
```

**依赖**: 被 `RememberFactTool`, `SearchMemoriesTool` 等使用

---

#### 3.3 TasksModule (`tlon/tools/tasks/`)

**职责**: 任务管理

**组成**:
```
tools/tasks/
├── manager.py        # TaskManager
├── models.py         # 任务模型
├── create_task.py    # 创建任务工具
├── list_tasks.py     # 列出任务工具
└── ...
```

---

#### 3.4 ToolsModule (`tlon/tools/`)

**职责**: 工具插件集合与基类定义

**关键设计**: **插件化架构**

```
tools/
├── base.py                    # Tool 抽象基类
├── shell/
│   └── execute_bash.py        # ◄── 依赖 Sandbox
├── memory/
│   ├── remember_fact.py       # ◄── 依赖 MemoryModule
│   ├── search_memories.py     # ◄── 依赖 MemoryModule
│   └── ...
├── tasks/
│   └── ...                    # ◄── 依赖 TasksModule
└── messaging/
    └── send_message.py        # ◄── 无额外依赖
```

**依赖规则**:
- 每个 Tool 自行声明其依赖
- ToolExecutor 不感知具体依赖，只调用 `tool.execute()`

---

## 模块间通信模式

### 模式 1: EventBus 异步通信（模块间解耦）

```python
# Agent 直接调用 ToolExecutor（模块内聚合）
result = await self.tool_executor.execute(
    tool_name="executeBashCommand",
    arguments={"command": "ls -la"},
)
```

**适用场景**: 模块内紧耦合调用，简单直接

---

### 模式 2: 直接调用（模块内聚合）

```python
# Agent 内部直接调用 ToolExecutor（同模块）
result = await self.tool_executor.execute(tool_name, arguments)

# ExecuteBashTool 内部调用 DockerRuntime（实现细节）
result = await self.docker_runtime.execute(command, timeout)
```

**适用场景**: 同一模块内，紧耦合是设计意图

---

### 模式 3: 依赖注入（构造时组装）

```python
# ServerApp 作为 DI 容器，组装所有模块
class ServerApp:
    async def initialize(self) -> None:
        # 1. 基础设施
        event_bus = EventBus()

        # 2. 功能模块
        docker_runtime = DockerRuntime(config, agent_id)
        memory = MemoryManager(...)

        # 3. 工具插件
        tool_executor = ToolExecutor(event_bus)
        tool_executor.register_tool(ExecuteBashTool(docker_runtime))
        tool_executor.register_tool(RememberFactTool(memory))

        # 4. 核心域
        agent = Agent(
            config=config,
            event_bus=event_bus,
            docker_runtime=docker_runtime,
            memory=memory,
            tool_executor=tool_executor,
            ...
        )
```

---

## 架构边界检查清单

### 导入路径规则

```python
# ✅ 正确: 模块间通过 EventBus 通信
from tlon.base.events.event_bus import EventBus

# ✅ 正确: 同模块内直接导入
from tlon.agents.tool_executor import ToolExecutor

# ✅ 正确: 工具声明其依赖
from tlon.sandbox.docker_runtime import DockerRuntime  # 仅 ToolsModule

# ❌ 错误: Agent 直接操作 Sandbox
from tlon.sandbox.docker_runtime import DockerRuntime  # 不应在 agents/agent.py 中

# ❌ 错误: 跨模块直接调用（应通过 EventBus）
from tlon.organization.messaging.router import MessageRouter  # 不应直接实例化
```

### 依赖方向规则

```
✅ 允许:
Infrastructure ◄── CoreDomains
CoreDomains ◄── FunctionalSubmodules
ToolsModule ◄── Sandbox/Memory/Tasks

❌ 禁止:
FunctionalSubmodules ──► CoreDomains (循环)
Sandbox ──► AgentModule (反向)
```

---

## 与旧分层架构的对比

| 旧分层描述 | 新模块描述 | 关键变化 |
|-----------|-----------|---------|
| Sandbox 是独立层 | Sandbox 是 Tool 的实现细节 | 降低抽象级别，明确使用范围 |
| Subsystem 在最底层 | Memory/Tasks 是功能模块 | 与 CoreDomains 平等，通过 DI 集成 |
| 7 层堆叠 | 3 类模块 + 插件 | 更清晰的边界 |
| 隐式调用关系 | 显式 EventBus 通信 | 强调异步、解耦 |

---

## 验证清单

- [x] 每个模块职责单一、边界清晰
- [x] 模块间通过 EventBus 通信（非直接调用）
- [x] Sandbox 仅被 ExecuteBashTool 依赖
- [x] 工具通过依赖注入获取所需功能模块
- [x] ServerApp 作为唯一 DI 容器组装所有模块
- [x] 无模块间循环依赖

---

## 参考

- [DAG Components](./dag-components.md) - 组件依赖图详细说明
- [Data Flow](./data-flow.md) - 数据流向图
- [Explicit Boundaries](./explicit-boundaries.md) - 架构边界规则
