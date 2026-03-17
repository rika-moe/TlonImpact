# Client-Server 架构设计

> 状态: 🚧 设计中
> 最后验证: 待验证

---

## 架构概览

Tlon Client-Server 架构采用**分层设计 + 适配器模式**，支持 Terminal、Web、Telegram 三种 Client 形态统一接入。

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         Client Ecosystem                                 │
├────────────────────┬─────────────────────┬──────────────────────────────┤
│    Terminal        │    Web Client       │    Telegram Bot              │
│   (Rich TUI)       │   (React + TS)      │   (Python)                   │
│                    │                     │                              │
│  ┌──────────────┐ │  ┌──────────────┐   │  ┌──────────────────────┐   │
│  │  Mode Switch │ │  │  Real-time   │   │  │  Polling/Webhook     │   │
│  │  chat/event/ │ │  │  Dashboard   │   │  │  Message Handler     │   │
│  │  agent       │ │  │  + History   │   │  └──────────┬───────────┘   │
│  └──────┬───────┘ │  └──────┬───────┘   │             │               │
└─────────┼─────────┴─────────┼───────────┴─────────────┼───────────────┘
          │                   │                         │
          └───────────────────┼─────────────────────────┘
                              │
                   ┌──────────▼──────────┐
                   │   Protocol Layer    │
                   │  ┌───────────────┐  │
                   │  │  Tlon Protocol│  │  v1.0.0
                   │  │   (JSON/WS)   │  │
                   │  └───────────────┘  │
                   └──────────┬──────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        │                     │                     │
   ┌────▼─────┐         ┌────▼─────┐         ┌────▼────────┐
   │   REST   │         │ WebSocket│         │  Webhook    │
   │   API    │         │  Server  │         │  Adapter    │
   │  8080    │         │  8888    │         │  (Telegram) │
   └────┬─────┘         └────┬─────┘         └────┬────────┘
        │                    │                    │
        └────────────────────┼────────────────────┘
                             │
                  ┌──────────▼──────────┐
                  │   Client Gateway    │  ◄── Facade Pattern
                  │  ┌───────────────┐  │
                  │  │ SessionManager│  │
                  │  │ Notification  │  │
                  │  │ EventFilter   │  │  ◄── NEW
                  │  └───────────────┘  │
                  └──────────┬──────────┘
                             │
    ┌────────────────────────┼────────────────────────┐
    │                        │                        │
┌───▼────┐          ┌───────▼────────┐      ┌───────▼────────┐
│EventBus│          │   MessageStore │      │  TraceStore    │  ◄── NEW
│        │          │   (File-based) │      │  (File-based)  │
└───┬────┘          └────────────────┘      └────────────────┘
    │
    ├──────────┬──────────┬─────────────────┬─────────────────┐
    │          │          │                 │                 │
┌───▼───┐ ┌───▼───┐ ┌────▼────┐ ┌─────────▼────────┐ ┌──────▼──────┐
│Agent  │ │Agent  │ │  Tool   │ │   AgentLogger    │ │  System     │
│Dev-001│ │PM-001 │ │Executor │ │   (NEW)          │ │  Events     │
└───────┘ └───────┘ └─────────┘ └──────────────────┘ └─────────────┘
                                          │
                              ┌───────────┼───────────┐
                              ▼           ▼           ▼
                         ┌────────┐ ┌────────┐ ┌────────┐
                         │stdout  │ │stderr  │ │trace   │
                         │.log    │ │.log    │ │.log    │
                         └────────┘ └────────┘ └────────┘
```

---

## 组件职责

### API Layer

| 组件 | 职责 | 端口 | Client 类型 |
|------|------|------|-------------|
| `RESTServer` | HTTP API，历史查询，健康检查 | 8080 | 所有 Client |
| `WebSocketServer` | 实时双向通信，事件推送 | 8888 | Terminal, Web |
| `WebhookAdapter` | Telegram Bot 接入，消息转换 | - | Telegram |

### Client Gateway (Facade)

统一入口，协调以下子组件：

```python
class ClientGateway:
    - SessionManager      # 会话生命周期管理
    - NotificationService # 事件推送服务
    - EventFilter         # 事件过滤（按 Client 类型）
    - ParticipantDirectory# 参与者聚合
```

### Storage Layer

| 组件 | 职责 | 存储格式 |
|------|------|----------|
| `MessageStore` | 消息历史持久化 | JSONL |
| `TraceStore` | 追踪数据存储 | JSONL (按 trace_id 分文件) |
| `AgentLogger` | Agent 日志捕获 | 文本文件 (stdout.log, stderr.log) |

---

## 数据流

### 场景：用户发送消息，Agent 思考并执行工具

```
┌─────────┐    send_message()    ┌───────────────┐
│  TUI    │ ───────────────────► │ ClientGateway │
│ Client  │                      │               │
└─────────┘                      └───────┬───────┘
                                         │
                        ┌────────────────┼────────────────┐
                        ▼                ▼                ▼
                ┌──────────────┐  ┌──────────┐    ┌──────────┐
                │  EventBus    │  │ TraceStore│    │ Message  │
                │  publish     │  │ start_trace│   │  Store   │
                └──────┬───────┘  └──────────┘    └──────────┘
                       │
          ┌────────────┼────────────┐
          ▼            ▼            ▼
    ┌──────────┐ ┌──────────┐ ┌──────────┐
    │Message   │ │ Agent    │ │ Notification│
    │Router    │ │ Dev-001  │ │ Service     │
    └────┬─────┘ └────┬─────┘ └─────┬───────┘
         │            │             │
         │     ┌──────┴──────┐      │
         │     │ thinking_start    │
         │     │ span: thinking    │
         │     └──────┬──────┘      │
         │            │             │
         │     ┌──────┴──────┐      │
         │     │ llm_request       │
         │     │ span: llm         │
         │     └──────┬──────┘      │
         │            │             │
         │     ┌──────┴──────┐      │
         │     │ tool_execute_start │
         │     │ span: tool    │     │
         │     └──────┬──────┘      │
         │            │             │
         ▼            ▼             ▼
    ┌──────────────────────────────────┐
    │     NotificationService          │
    │  ┌────────────────────────────┐  │
    │  │  EventFilter (per session) │  │
    │  │  - TUI: 全量事件           │  │
    │  │  - Web: 过滤 tool.detail   │  │
    │  │  - Telegram: 仅 message.*  │  │
    │  └────────────────────────────┘  │
    └──────────────┬───────────────────┘
                   │
        ┌──────────┼──────────┐
        ▼          ▼          ▼
    ┌───────┐ ┌───────┐ ┌─────────┐
    │  TUI  │ │  Web  │ │Telegram │
    └───────┘ └───────┘ └─────────┘
```

---

## Client Adapter 模式

### 接口定义

```python
class ClientAdapter(ABC):
    """Client 适配器基类"""
    
    @abstractmethod
    async def connect(self, endpoint: str) -> bool: ...
    
    @abstractmethod
    async def disconnect(self) -> None: ...
    
    @abstractmethod
    async def send_message(self, content: str, **kwargs) -> str: ...
    
    @abstractmethod
    def on_event(self, handler: Callable[[EventMessage], None]) -> None: ...
    
    @property
    @abstractmethod
    def event_filter(self) -> EventFilter: ...
```

### 具体实现

| 适配器 | 事件过滤策略 | 特殊处理 |
|--------|-------------|----------|
| `TerminalAdapter` | 无过滤，接收全量 | 支持 mode 切换 |
| `WebAdapter` | 排除 `agent.llm_request/response` | payload 简化 |
| `TelegramAdapter` | 仅 `message.*` 事件 | 内容长度限制 4096 |

---

## 追踪系统设计

### Trace 模型

```
Trace: trace-abc-123 (一条用户消息的完整生命周期)
├── Span: message-received (MessageRouter)
│   ├── timestamp: 2026-03-18T12:00:00Z
│   └── attributes: {message_id: "msg-1", sender: "human"}
│
├── Span: agent-thinking (Agent Dev-001)
│   ├── parent: message-received
│   ├── timestamp: 2026-03-18T12:00:01Z
│   ├── duration: 5000ms
│   │
│   ├── Span: llm-request
│   │   ├── parent: agent-thinking
│   │   ├── attributes: {model: "gpt-4", tokens: 2048}
│   │   └── duration: 2000ms
│   │
│   └── Span: tool-execute
│       ├── parent: agent-thinking
│       ├── attributes: {tool: "shell", command: "git diff"}
│       └── duration: 500ms
│
└── Span: message-sent (NotificationService)
    ├── parent: message-received
    └── timestamp: 2026-03-18T12:00:06Z
```

### TraceStore 接口

```python
class TraceStore:
    async def start_trace(self, trace_id: str, root_span: Span) -> None
    async def add_span(self, trace_id: str, span: Span) -> None
    async def get_trace(self, trace_id: str) -> Optional[Trace]
    async def query_traces(
        self,
        start_time: datetime,
        end_time: datetime,
        source: Optional[str] = None,
    ) -> List[TraceSummary]
```

---

## Agent 日志系统

### AgentLogger 设计

```python
class AgentLogger:
    """Agent 日志管理器"""
    
    def __init__(self, agent_id: str, log_dir: Path):
        self.agent_id = agent_id
        self.stdout_file = log_dir / f"{agent_id}.stdout.log"
        self.stderr_file = log_dir / f"{agent_id}.stderr.log"
        
    def setup(self, process: subprocess.Popen) -> None:
        """设置进程 stdout/stderr 重定向"""
        
    async def get_logs(
        self,
        stream: Literal["stdout", "stderr", "both"] = "both",
        lines: int = 100,
        since: Optional[datetime] = None,
    ) -> str:
        """获取日志内容"""
```

### 日志文件结构

```
data/agents/
├── dev-001/
│   ├── stdout.log          # 标准输出
│   ├── stderr.log          # 标准错误
│   └── trace.log           # 追踪日志（可选）
├── pm-001/
│   ├── stdout.log
│   └── stderr.log
```

---

## REST API 扩展

### 新增端点

| 端点 | 方法 | 说明 | 模式 |
|------|------|------|------|
| `/traces/{trace_id}` | GET | 获取完整追踪树 | 所有模式 |
| `/traces` | GET | 查询追踪列表 | 所有模式 |
| `/agents/{id}/logs` | GET | 获取 Agent 日志 | 所有模式 |
| `/events/stream` | GET | SSE 事件流（Web 备用） | 所有模式 |
| `/test/reset` | POST | 重置测试状态 | test_mode  only |
| `/test/inject` | POST | 注入测试事件 | test_mode only |
| `/test/state` | GET | 获取内部状态 | test_mode only |

### 测试模式

```python
class ServerApp:
    def __init__(self, test_mode: bool = False, ...):
        if test_mode:
            # 使用内存存储
            self.message_store = InMemoryMessageStore()
            self.trace_store = InMemoryTraceStore()
            # Mock LLM
            self.llm_client = MockLLMClient()
            # 禁用 Docker
            self.sandbox = None
```

---

## 关键决策记录 (ADR)

### ADR-1: 协议版本协商机制
- **问题**: 如何支持协议演进而不破坏旧 Client？
- **方案**: WebSocket 握手时协商 `protocol_version`
- **理由**: 允许逐步升级，旧 Client 可继续使用旧协议
- **风险**: 版本矩阵测试复杂度增加

### ADR-2: 统一事件追踪
- **问题**: 如何追踪消息从发送到 Agent 工具执行的完整链路？
- **方案**: 引入 `trace_id` 和 `span_id` 到所有事件
- **理由**: 可观测性需求强烈，便于调试 Agent 行为
- **风险**: 事件体积增大 ~20%

### ADR-3: Client Adapter 模式
- **问题**: 如何支持 Terminal/Web/IM 三种 Client 的差异需求？
- **方案**: Adapter 模式，不同 Client 不同事件过滤策略
- **理由**: 不同 Client 的连接模型和事件需求不同
- **风险**: Server 需要维护多种推送逻辑

### ADR-4: 文件存储优先
- **问题**: 是否需要引入数据库存储消息和追踪？
- **方案**: 保持文件存储（JSONL），按 trace_id 分文件
- **理由**: 满足当前需求，简单可靠，易于备份
- **风险**: 数据量大时查询性能下降

---

## 文档索引

| 文档 | 目的 | 深度 |
|------|------|------|
| [design/client-server-protocol.md](../design/client-server-protocol.md) | 协议规范 | L2 |
| [roadmap/milestone-7-client-expansion.md](../roadmap/milestone-7-client-expansion.md) | 实施路线图 | L3 |

---

## 验证清单

- [ ] Client Gateway Facade 实现
- [ ] WebSocket 协议版本协商
- [ ] EventFilter 按 Client 类型过滤
- [ ] TraceStore 文件存储实现
- [ ] AgentLogger stdout/stderr 捕获
- [ ] REST API 新增端点
- [ ] Test Mode 实现
- [ ] TerminalAdapter 迁移
- [ ] WebAdapter 实现
- [ ] TelegramAdapter 实现

---

*设计完成时间: 2026-03-18*
