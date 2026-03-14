# ServerApp 重构设计：完整 DI + 动态重载

> 将 ServerApp 重构为依赖注入容器，TlonServer 变为纯协调者

---

## 背景

当前架构存在以下问题：
1. **职责不清**：TlonServer 既创建组件又协调组件
2. **初始化顺序不一致**：PositionRegistry 在 ServerApp 填充，AgentRegistry 在 TlonServer 填充
3. **难以支持热重载**：组件生命周期分散，运行时无法安全更新配置

---

## 目标

1. **完整依赖注入（DI）**：所有组件在 ServerApp 创建，通过构造函数注入
2. **TlonServer 纯协调者**：不创建任何组件，只接收依赖进行协调
3. **支持运行时热重载**：通过 ConfigManager 和回调机制实现配置动态更新
4. **清晰的生命周期**：谁创建，谁管理；谁注入，谁使用

---

## 架构设计

### 组件关系图

```
ServerApp (组件组装器 + 生命周期管理)
    │
    ├──► ConfigLoader
    │
    ├──► ConfigManager ─────────────────────┐
    │   ├── positions: Dict[str, Position]  │
    │   ├── agents: Dict[str, AgentConfig]  │
    │   ├── reload_positions()              │ 触发回调
    │   └── reload_agents()                 │
    │                                         │
    ├──► EventBus ◄─────────────────────────┼── 广播事件
    │                                         │
    ├──► Congress ──────────────────────────┼── 依赖 EventBus
    │                                         │
    ├──► PositionRegistry ──────────────────┼── 存储 Positions
    │                                         │
    ├──► AgentRegistry ─────────────────────┼── 依赖 EventBus, Congress, PositionRegistry
    │   ├── register(agent_config)            │
    │   ├── unregister(agent_id)              │
    │   └── update_config(agent_id, config)   │
    │                                         │
    └──► TlonServer (纯协调者)               │
            │                                 │
            ├── 注入: EventBus                │
            ├── 注入: Congress                │
            ├── 注入: PositionRegistry        │
            ├── 注入: AgentRegistry           │
            ├── 注入: ConfigManager ◄─────────┘
            │
            ├── on_positions_changed() ──► 广播客户端
            └── on_agents_changed() ─────► 广播客户端
```

### 启动流程

```
ServerApp.initialize()
    │
    ├── 1. 配置层
    │      ├── ConfigLoader 创建
    │      └── ConfigManager 创建 + initial_load()
    │
    ├── 2. 核心组件层（全部在 ServerApp 创建）
    │      ├── EventBus 创建 + start()
    │      ├── Congress 创建（依赖 EventBus）+ initialize()
    │      ├── PositionRegistry 创建 + 填充数据
    │      └── AgentRegistry 创建（依赖上面所有）+ 填充数据 + initialize()
    │
    ├── 3. TlonServer 创建（纯协调者）
    │      └── 接收所有组件注入 + initialize()
    │
    ├── 4. 注册配置重载回调
    │      └── ConfigManager 回调绑定到 ServerApp 方法
    │
    └── 5. API 层创建（DI）
           ├── RESTServer（注入 TlonServer, ConfigManager）
           └── WebSocketServer（注入 TlonServer, EventBus）
```

### 热重载流程

```
用户调用 POST /admin/reload/agents
    │
    ├── RESTServer.handle_reload_agents()
    │
    ├── ConfigManager.reload_agents()
    │      ├── 从磁盘重新加载配置
    │      └── 触发 on_agents_changed 回调
    │
    ├── ServerApp._on_agents_reloaded()
    │      ├── 获取 AgentRegistry（ServerApp 拥有它）
    │      ├── 对比新旧配置差异
    │      ├── 删除不存在的 Agents
    │      ├── 更新修改的 Agents
    │      └── 注册新增的 Agents
    │
    └── TlonServer.on_agents_changed()
           └── 广播 CONFIG_RELOADED 事件给所有客户端
```

---

## 组件职责

### ServerApp

| 职责 | 说明 |
|------|------|
| **组件创建** | 创建所有核心组件（EventBus, Congress, Registries） |
| **依赖注入** | 通过构造函数将依赖传递给下游组件 |
| **生命周期管理** | 初始化时顺序启动，关闭时逆序停止 |
| **配置重载处理** | 接收 ConfigManager 回调，操作组件更新状态 |
| **协调关闭** | 优雅关闭所有组件 |

### ConfigManager

| 职责 | 说明 |
|------|------|
| **配置加载** | 使用 ConfigLoader 加载配置 |
| **配置缓存** | 维护当前加载的 positions 和 agents |
| **重载触发** | 提供 reload_positions() / reload_agents() 方法 |
| **变更通知** | 通过回调机制通知订阅者配置变化 |

### TlonServer（重构后）

| 职责 | 说明 |
|------|------|
| **协调初始化** | 验证依赖，订阅事件，设置逻辑 |
| **客户端管理** | 管理 WebSocket 连接，会话状态 |
| **事件广播** | 将内部事件转发给连接的客户端 |
| **重载协调** | 接收重载通知，广播给客户端 |
| **不创建组件** | 所有依赖从外部注入 |

---

## 接口设计

### ConfigManager

```python
class ConfigManager:
    def __init__(self, config_loader: ConfigLoader)
    
    async def initial_load(self) -> None
    
    # 订阅
    def on_positions_changed(self, callback: Callable[[Dict], None])
    def on_agents_changed(self, callback: Callable[[Dict], None])
    
    # 重载
    async def reload_positions(self) -> bool
    async def reload_agents(self) -> bool
    
    # 查询
    @property
    def positions(self) -> Dict[str, PositionConfig]
    @property
    def agents(self) -> Dict[str, AgentConfig]
```

### TlonServer（新构造函数）

```python
class TlonServer:
    def __init__(
        self,
        *,
        event_bus: EventBus,
        congress: Congress,
        position_registry: PositionRegistry,
        agent_registry: AgentRegistry,  # 新增：从外部注入
        config_manager: ConfigManager,
        data_dir: Path,
        host: str,
        port: int,
    )
    
    async def initialize(self) -> None  # 纯协调，不创建组件
    
    # 重载回调
    async def on_positions_changed(self, new_positions: Dict) -> None
    async def on_agents_changed(self, new_agents: Dict) -> None
```

### ServerApp（重载处理）

```python
class ServerApp:
    async def _on_positions_reloaded(self, new_positions: Dict) -> None
    async def _on_agents_reloaded(self, new_agents: Dict) -> None
```

---

## 关键变更

### 文件变更清单

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `app/server/config_manager.py` | 新增 | 配置管理器，支持热重载 |
| `app/server/serverapp.py` | 重写 | 完整 DI 组装，管理所有组件生命周期 |
| `app/server/core/server.py` | 修改 | TlonServer 改为纯协调者，接收所有依赖注入 |
| `app/server/api/rest.py` | 修改 | 新增 /admin/reload/* 端点 |
| `agents/agent_registry.py` | 可能修改 | 支持 update_config, update_position_references |

### 删除的代码

- `TlonServer` 中创建 `AgentRegistry` 的代码（移到 ServerApp）
- `TlonServer` 中创建 `EventBus`/`Congress` 的代码（移到 ServerApp）
- 分散的 YAML 读取逻辑（由 ConfigManager 统一处理）

### 新增的能力

- 运行时热重载配置（Positions/Agents）
- 组件依赖关系清晰可追踪
- 易于单元测试（可 Mock 依赖）
- 支持未来扩展其他组件类型

---

## 风险与缓解

| 风险 | 缓解措施 |
|------|----------|
| 启动顺序错误导致依赖缺失 | 在 TlonServer.initialize() 中验证所有依赖非空 |
| 热重载时 Agents 状态不一致 | 先停止旧 Agent，再启动新 Agent，确保平滑过渡 |
| 循环依赖 | 严格单向依赖：ServerApp → Components → TlonServer |
| 关闭时组件未正确清理 | ServerApp 负责逆序关闭，确保依赖先关 |

---

## 向后兼容性

- `TlonServer` 的公共接口保持不变（agent_registry, congress 等属性）
- `ServerApp` 的公共接口保持不变（initialize, start, shutdown）
- CLI 命令保持不变（`tlon dream`, `tlon mirror` 等）

---

## 测试策略

1. **单元测试**：Mock 依赖测试 TlonServer 协调逻辑
2. **集成测试**：验证完整启动/关闭流程
3. **热重载测试**：修改配置文件，验证运行时更新
4. **异常测试**：依赖缺失时的错误处理

---

## 参考

- [依赖注入模式](https://en.wikipedia.org/wiki/Dependency_injection)
- [控制反转（IoC）](https://en.wikipedia.org/wiki/Inversion_of_control)

---

*设计日期: 2026-03-14*
*状态: 已批准，待实施*
