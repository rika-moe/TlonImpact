# ServerApp DI 重构实施路线图

> 将 ServerApp 重构为完整依赖注入容器

---

## 状态概览

| 阶段 | 状态 | 说明 |
|------|------|------|
| Phase 0 | ✅ 已完成 | 前期 Config 层重构 |
| Phase 1 | ✅ 已完成 | 创建 ConfigManager |
| Phase 2 | ✅ 已完成 | 重构 ServerApp 为 DI 容器 |
| Phase 3 | ✅ 已完成 | 重构 TlonServer 为纯协调者 |
| Phase 4 | ✅ 已完成 | 实现热重载 API |
| Phase 5 | ✅ 已完成 | 测试通过 (254 passed) |
| Phase 6 | ✅ 已完成 | 运行时验证通过 |
| Phase 7 | ✅ 已完成 | Bug 修复 (AgentRegistry 方法、WebSocketServer 参数) |

---

## Phase 1: 创建 ConfigManager

**目标**: 新建配置管理器，支持热重载回调机制

### 任务清单

- [ ] **1.1** 创建 `src/tlon/app/server/config_manager.py`
  - 实现 `ConfigManager` 类
  - 实现 `initial_load()` 方法
  - 实现回调订阅机制 (`on_positions_changed`, `on_agents_changed`)
  - 实现重载方法 (`reload_positions`, `reload_agents`)

- [ ] **1.2** 添加单元测试
  - 测试 `ConfigManager` 初始化
  - 测试回调触发机制
  - 测试配置重载逻辑

### 验收标准
- [ ] `ConfigManager` 可以独立加载配置
- [ ] 回调机制正常工作
- [ ] 所有测试通过

---

## Phase 2: 重构 ServerApp 为 DI 容器

**目标**: ServerApp 创建所有组件，管理完整生命周期

### 任务清单

- [ ] **2.1** 重写 `ServerApp.__init__()`
  - 添加 `_components` DI 容器字典
  - 移除 `_config_loader` 直接引用

- [ ] **2.2** 重写 `ServerApp.initialize()`
  - Step 1: 创建 ConfigLoader 和 ConfigManager
  - Step 2: 创建 EventBus
  - Step 3: 创建 Congress（注入 EventBus）
  - Step 4: 创建 PositionRegistry（填充数据）
  - Step 5: 创建 AgentRegistry（注入所有依赖，填充数据，调用 initialize）
  - Step 6: 创建 TlonServer（注入所有组件）
  - Step 7: 注册配置重载回调
  - Step 8: 创建 API 服务器（注入依赖）

- [ ] **2.3** 实现 `_on_positions_reloaded()` 回调
  - 更新 PositionRegistry
  - 通知 AgentRegistry

- [ ] **2.4** 实现 `_on_agents_reloaded()` 回调
  - 对比配置差异
  - 增删改 Agents

- [ ] **2.5** 重写 `ServerApp.shutdown()`
  - 逆序关闭组件（AgentRegistry → Congress → EventBus）

### 验收标准
- [ ] ServerApp 创建并管理所有组件
- [ ] 组件通过构造函数注入
- [ ] 启动流程正常
- [ ] 关闭流程正常
- [ ] 所有测试通过

---

## Phase 3: 重构 TlonServer 为纯协调者

**目标**: TlonServer 不再创建任何组件，只接收依赖

### 任务清单

- [ ] **3.1** 修改 `TlonServer.__init__()` 签名
  - 添加 `agent_registry: AgentRegistry` 参数（必需）
  - 修改其他参数为必需（移除默认值，强制 DI）

- [ ] **3.2** 重写 `TlonServer.initialize()`
  - 移除创建 EventBus 的代码
  - 移除创建 Congress 的代码
  - 移除创建 AgentRegistry 的代码
  - 添加依赖验证（断言所有依赖非空）
  - 保留事件订阅逻辑

- [ ] **3.3** 移除组件创建相关代码
  - 删除 EventBus 创建逻辑
  - 删除 Congress 创建逻辑
  - 删除 PositionRegistry 创建逻辑（改为使用注入的）

- [ ] **3.4** 保留/修改对外接口
  - `agent_registry` 属性（返回注入的实例）
  - `congress` 属性
  - `position_registry` 属性

- [ ] **3.5** 实现重载回调方法
  - `on_positions_changed()` - 广播事件
  - `on_agents_changed()` - 广播事件

- [ ] **3.6** 修改 `TlonServer.shutdown()`
  - 移除关闭组件的代码（由 ServerApp 管理）
  - 只保留客户端断开和状态设置

### 验收标准
- [ ] TlonServer 不创建任何组件
- [ ] 所有依赖通过构造函数注入
- [ ] 依赖验证正常工作
- [ ] 现有功能不受影响
- [ ] 所有测试通过

---

## Phase 4: 实现热重载 API

**目标**: 添加 REST API 端点支持运行时重载

### 任务清单

- [ ] **4.1** 修改 `RESTServer.__init__()`
  - 添加 `config_manager: ConfigManager` 参数

- [ ] **4.2** 添加热重载端点
  - `POST /admin/reload/positions`
  - `POST /admin/reload/agents`

- [ ] **4.3** 添加查询端点（可选）
  - `GET /admin/config/positions` - 列出当前 Positions
  - `GET /admin/config/agents` - 列出当前 Agents

- [ ] **4.4** 添加权限检查（可选，未来扩展）

### 验收标准
- [ ] 端点可以触发热重载
- [ ] 重载后配置生效
- [ ] 返回适当的成功/错误信息
- [ ] 所有测试通过

---

## Phase 5: 更新测试和验证

**目标**: 确保重构后系统稳定可靠

### 任务清单

- [ ] **5.1** 更新现有测试
  - 修复因构造函数变化而失败的测试
  - 更新 Mock 对象

- [ ] **5.2** 添加集成测试
  - 测试完整启动/关闭流程
  - 测试热重载流程

- [ ] **5.3** 添加单元测试
  - `ConfigManager` 测试
  - `ServerApp` 组件管理测试
  - `TlonServer` 协调逻辑测试

- [ ] **5.4** 手动验证
  - 启动服务器
  - 测试基本功能
  - 测试热重载
  - 测试优雅关闭

### 验收标准
- [ ] 所有现有测试通过
- [ ] 新增测试覆盖关键路径
- [ ] 手动验证无问题

---

## Phase 6: 文档更新（可选）

- [ ] **6.1** 更新架构文档
- [ ] **6.2** 更新 API 文档
- [ ] **6.3** 添加热重载使用指南

---

## 依赖关系

```
Phase 1 (ConfigManager)
    │
    ▼
Phase 2 (ServerApp DI)
    │
    ▼
Phase 3 (TlonServer 协调者)
    │
    ▼
Phase 4 (热重载 API)
    │
    ▼
Phase 5 (测试验证)
```

---

## 实施建议

1. **逐个 Phase 实施**：完成一个 Phase 后再进入下一个
2. **保持测试通过**：每个 Phase 结束后运行全部测试
3. **小步提交**：每个任务完成后提交，方便回滚
4. **及时同步**：完成每个 Phase 后更新 roadmap 状态

---

## 风险应对

| 风险 | 应对策略 |
|------|----------|
| 测试大面积失败 | 逐个修复，必要时暂时跳过测试 |
| 循环依赖 | 立即暂停，重新设计接口 |
| 性能下降 | 对比基准测试，找出瓶颈 |
| 功能回归 | 增加集成测试覆盖 |

---

*Roadmap 创建日期: 2026-03-14*
*预计实施周期: 2-3 天*
*状态: 已批准，等待实施指令*
