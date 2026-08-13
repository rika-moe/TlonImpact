# 基础层 (Base)

> 状态: ✅ 已验证
> 最后验证: 2026-04-19

---

## 职责

提供基础设施服务：配置管理、组件生命周期、事件系统、日志、链路追踪、指标（metrics）、全局设置

---

## 模块结构

```
base/
├── component.py           # Component 基类
├── device.py              # Device 基类 (EventBus 交互能力)
├── config.py              # 配置模型 (AgentConfig, LLMConfig, DockerRuntimeConfig) + ConfigService
├── metrics.py             # Metrics 支柱 (init_metrics + MetricsService)
├── events/                # 事件系统
│   ├── event.py           # Event、EventType 定义
│   └── event_bus.py       # EventBus 实现
├── tracing/               # 分布式链路追踪
│   ├── trace_context.py   # TraceContext, start_span
│   └── trace_store.py     # FileTraceStore
├── logger.py              # 日志配置
└── settings.py            # 全局设置
```

---

## 关键类

| 类 | 职责 | 路径 |
|----|------|------|
| `Component` | 组件基类（initialize/shutdown 生命周期） | `base/component.py` |
| `Device` | EventBus 交互能力（subscribe/emit/publish） | `base/device.py` |
| `ConfigService` | 配置加载、缓存、保存、EventBus 通知 | `base/config.py` |
| `EventBus` | 异步事件总线 | `base/events/event_bus.py` |
| `Event` | 事件数据模型（含 trace_id/span_id） | `base/events/event.py` |
| `MetricsService` | 事件→指标映射（纯事件消费者） | `base/metrics.py` |
| `settings` | 全局配置实例 | `base/settings.py` |

---

## ConfigService

统一的配置服务，实现 `AgentInfoProvider` 接口：

```python
class ConfigService(Component, Device, AgentInfoProvider):
    def __init__(self, event_bus: EventBus, config_dir: Path | None = None):
        ...
```

职责：
- 从 `config/agents/*.yaml` 加载 Agent 配置
- 维护内存缓存
- 通过 EventBus 通知配置变更 (`CONFIG_UPDATED`)
- 保存配置到 YAML 文件

---

## AgentConfig 模型

```python
class AgentConfig(BaseModel):
    id: str
    name: str
    identity: str = ""      # 自我身份叙事
    profile: str = ""       # 公开档案（队友可见）
    behavior: str = ""      # 行为守则
    llm: LLMConfig
    docker: DockerRuntimeConfig
    memory: Any = None
```

**注意**: `RoleConfig` 已完全移除，角色信息合并入 `identity`/`profile`/`behavior` 叙事文本。

---

## Metrics 支柱

可观测性三支柱的最后一块（traces / logs / metrics），落在 `base/metrics.py`。

**选型**：复用项目已有的 OpenTelemetry SDK（`opentelemetry-sdk` 已是依赖，trace 在用）——不引新依赖、不手搓。`init_metrics()`（幂等，参照 `tracing/otel.py` 的 `init_tracing`）构建进程级 `MeterProvider` + `InMemoryMetricReader`：进程内快照经 `GET /metrics` 给 operator；将来接 OTLP metric exporter 无需改业务代码。

**架构：metrics 作为事件消费者**。业务代码零埋点——`MetricsService(Component, Device)` 订阅 EventBus，把既有事件映射为指标，全部埋点集中在这一个组件里。事件处理异常被 try/except 包裹（记 warning），绝不影响 bus dispatch。

事件→指标映射：

| 事件 | 指标 | 属性 |
|------|------|------|
| `MAIL_NEW` | `tlon.mail.sent` (counter) | `agent=source` |
| `MAIL_NEW` 且 from=postmaster | `tlon.mail.bounced` (counter) | 无 |
| `MAIL_DELIVERED` | `tlon.mail.delivered` (counter) | `kind=agent\|human` |
| `AGENT_STARTED` / `WOKEN` / `SLEEPING` / `STOPPED` | `tlon.agent.started` / `woken` / `sleeping` / `stopped` (counter) | `agent_id` |
| `AGENT_LLM_RESPONSE` | `tlon.llm.calls` (counter) + `tlon.llm.duration_ms` (histogram) | `agent_id` |
| `TOOL_EXECUTE_END` / `TOOL_ERROR` | `tlon.tool.executions` / `tlon.tool.errors` (counter) | `tool_name` |

**Cardinality 纪律**：attribute 只允许低基数值（agent id、tool 名、recipient 种类、事件 source），严禁 mail_id / session_id / trace_id 入 attribute。recipient 只记 `kind`（`human:` 前缀判断），不记全名。

**snapshot() 结构**：`{"metrics": {name: {"type": "counter"|"histogram"|"gauge", "description", "unit", "points": [...]}}}`——counter/gauge 点带 `{attributes, value}`，histogram 点带 `{attributes, count, sum, min, max}`。REST `GET /metrics` 直接返回该 JSON；service 不可用时 503。

---

## 文档索引

| 文档 | 目的 | 深度 |
|------|------|------|
| [../../design/config.md](../../design/config.md) | 配置系统设计 | L1 |
| [../../design/narrative-prompt.md](../../design/narrative-prompt.md) | Prompt 架构设计 | L2 |

---

## 验证清单

- [x] 配置模型与代码一致（`identity`/`profile`/`behavior`）
- [x] `RoleConfig` 已完全移除
- [x] `ConfigService` 统一了加载/缓存/保存/通知
- [x] EventBus 为单例模式
- [x] 设置可从环境变量覆盖
