# 基础层 (Base)

> 状态: ✅ 已验证
> 最后验证: 2026-04-19

---

## 职责

提供基础设施服务：配置管理、组件生命周期、事件系统、日志、链路追踪、全局设置

---

## 模块结构

```
base/
├── component.py           # Component 基类, ComponentRegistry
├── device.py              # Device 基类 (EventBus 交互能力)
├── config.py              # 配置模型 (AgentConfig, LLMConfig, DockerRuntimeConfig) + ConfigService
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
    proactive: bool = False
```

**注意**: `RoleConfig` 已完全移除，角色信息合并入 `identity`/`profile`/`behavior` 叙事文本。

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
