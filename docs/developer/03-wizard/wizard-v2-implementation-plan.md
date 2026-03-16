# Wizard 2.0 实施计划

> 基于 Position-Agent 架构的配置向导重构

---

## 项目概览

- **目标**: 重构 Tlon Wizard，支持 Position-Agent 解耦架构
- **预计工期**: 3-4 天
- **优先级**: P1（核心配置工具）

---

## 阶段一：基础设施准备（Day 1）

### 任务 1.1: Position 数据源适配
**状态**: 🔲 未开始  
**耗时**: 4h  
**文件**: `minions/wizard/position_store.py`

- [ ] 创建 `PositionStore` 类，封装 Position 文件操作
- [ ] 实现 `load_all_positions()` - 从 `config/positions/*.yaml` 加载
- [ ] 实现 `get_position(type)` - 获取单个岗位
- [ ] 实现 `save_position(position)` - 保存岗位到 YAML
- [ ] 实现 `delete_position(type)` - 删除岗位（检查引用）
- [ ] 实现 `get_position_usage_count(type)` - 获取引用数
- [ ] 内置默认模板（当目录为空时提供 fallback）

**验收标准**:
```python
store = PositionStore("config/positions")
positions = store.load_all_positions()  # 返回 Position 对象列表
store.save_position(new_position)       # 写入 YAML 文件
```

---

### 任务 1.2: Agent 配置存储适配
**状态**: 🔲 未开始  
**耗时**: 3h  
**文件**: `minions/wizard/agent_store.py`

- [ ] 创建 `AgentStore` 类，封装 Agent 文件操作
- [ ] 实现 `load_all_agents()` - 从 `config/agents/*.yaml` 加载
- [ ] 实现 `get_agent(agent_id)` - 获取单个 Agent
- [ ] 实现 `save_agent(agent)` - 保存 Agent 到独立 YAML
- [ ] 实现 `delete_agent(agent_id)` - 删除 Agent 及数据目录
- [ ] 实现 `get_agents_by_position(position_id)` - 按岗位筛选

**验收标准**:
```python
store = AgentStore("config/agents")
agents = store.load_all_agents()
store.save_agent(agent)  # 写入 {agent_id}.yaml
```

---

### 任务 1.3: 配置诊断器
**状态**: 🔲 未开始  
**耗时**: 3h  
**文件**: `minions/wizard/diagnostics.py`

- [ ] 创建 `ConfigDiagnostics` 类
- [ ] 实现 `validate_positions()` - Position 配置检查
- [ ] 实现 `validate_agents()` - Agent 配置检查
- [ ] 实现 `check_references()` - 引用完整性检查
- [ ] 实现 `run_all_checks()` - 完整诊断并返回报告

**诊断项**:
- YAML 语法有效性
- 必填字段检查
- Jinja2 模板语法检查
- position_id 引用有效性
- 孤立 Position 检测

---

## 阶段二：核心功能实现（Day 2）

### 任务 2.1: 岗位管理菜单
**状态**: 🔲 未开始  
**耗时**: 4h  
**文件**: `minions/wizard/menus/position_menu.py`

- [ ] `list_positions()` - 列表显示（含引用数）
- [ ] `view_position_detail()` - 详情查看（含 Prompt 预览）
- [ ] `create_position()` - 创建向导
  - 预设模板选择
  - 表单输入（type, title, department, proactive）
  - JD 多行输入
  - rules 列表编辑（逐条添加/删除）
  - prompt_template 编辑 + 预览
- [ ] `edit_position()` - 编辑向导
- [ ] `delete_position()` - 删除（检查引用）

**依赖**: 任务 1.1

---

### 任务 2.2: 人员管理菜单
**状态**: 🔲 未开始  
**耗时**: 4h  
**文件**: `minions/wizard/menus/agent_menu.py`

- [ ] `list_agents()` - 列表显示（含岗位名称）
- [ ] `view_agent_detail()` - 详情查看
- [ ] `create_agent()` - 创建向导（5 步骤）
- [ ] `edit_agent()` - 编辑向导
- [ ] `transfer_position()` - 换岗功能（核心）
- [ ] `delete_agent()` - 删除

**换岗功能细节**:
- 选择新岗位
- 显示变更对比表
- 风险提示
- 确认后修改 `position_id`

**依赖**: 任务 1.2, 2.1

---

### 任务 2.3: 快速开始向导重构
**状态**: 🔲 未开始  
**耗时**: 4h  
**文件**: `minions/wizard/menus/quickstart.py`

- [ ] 保留环境检查步骤
- [ ] 保留 API 配置步骤
- [ ] 重构团队配置步骤
  - 标准团队选项（一键创建 6 Position + 6 Agent）
  - 自定义团队选项（选择岗位 → 配置 Agent）
- [ ] 重构确认创建步骤
  - 显示生成的文件清单
  - 批量创建 Position YAML
  - 批量创建 Agent YAML

**依赖**: 任务 1.1, 1.2, 2.1, 2.2

---

## 阶段三：集成与优化（Day 3）

### 任务 3.1: 主菜单重构
**状态**: 🔲 未开始  
**耗时**: 2h  
**文件**: `minions/wizard/core.py`

- [ ] 重构主菜单结构
  - 🚀 快速开始
  - 🏢 岗位管理
  - 👤 人员管理
  - 🔍 配置诊断
  - ⚙️  系统设置
  - ❌ 退出
- [ ] 集成各子菜单
- [ ] 添加菜单间导航

---

### 任务 3.2: 配置诊断菜单
**状态**: 🔲 未开始  
**耗时**: 2h  
**文件**: `minions/wizard/menus/diagnostics_menu.py`

- [ ] 显示诊断结果表格
- [ ] 错误项红色标注
- [ ] 警告项黄色标注
- [ ] 提供一键修复建议（如删除孤立配置）

**依赖**: 任务 1.3

---

### 任务 3.3: 系统设置菜单
**状态**: 🔲 未开始  
**耗时**: 3h  
**文件**: `minions/wizard/menus/settings_menu.py`

- [ ] API 设置（复用现有逻辑）
- [ ] 存储设置（复用现有逻辑）
- [ ] 导入/导出功能
  - 导出所有 Position（打包为 zip）
  - 导出所有 Agent（打包为 zip）
  - 从 zip 导入配置

---

### 任务 3.4: UI/UX 优化
**状态**: 🔲 未开始  
**耗时**: 3h  

- [ ] 添加操作成功/失败提示
- [ ] 添加确认对话框（危险操作）
- [ ] 优化表格显示（使用 rich Table）
- [ ] 添加加载动画（长时间操作时）
- [ ] 统一错误处理

---

## 阶段四：测试与文档（Day 4）

### 任务 4.1: 单元测试
**状态**: 🔲 未开始  
**耗时**: 3h  
**文件**: `tests/wizard/`

- [ ] `test_position_store.py` - Position 存储测试
- [ ] `test_agent_store.py` - Agent 存储测试
- [ ] `test_diagnostics.py` - 诊断器测试
- [ ] 测试数据准备（临时配置目录）

---

### 任务 4.2: 集成测试
**状态**: 🔲 未开始  
**耗时**: 2h  

- [ ] 端到端测试：创建 Position → 创建 Agent → 换岗 → 删除
- [ ] 测试边界情况
  - 删除被引用的 Position（应拒绝）
  - 无效的 position_id 引用（诊断应检测）
  - 重复的 type（应拒绝）

---

### 任务 4.3: 用户文档
**状态**: 🔲 未开始  
**耗时**: 2h  
**文件**: `docs/wizard-guide.md`

- [ ] 快速开始指南
- [ ] 岗位管理教程
- [ ] 人员管理教程
- [ ] 常见问题解答

---

## 代码结构规划

```
minions/wizard/
├── __init__.py
├── core.py                 # 主入口和菜单路由
├── position_store.py       # Position 数据操作（新增）
├── agent_store.py          # Agent 数据操作（新增）
├── diagnostics.py          # 配置诊断器（新增）
├── validators.py           # 输入验证（保留）
├── templates.py            # 预设模板（保留，调整）
└── menus/                  # 菜单模块（新增目录）
    ├── __init__.py
    ├── position_menu.py    # 岗位管理
    ├── agent_menu.py       # 人员管理
    ├── quickstart.py       # 快速开始
    ├── diagnostics_menu.py # 配置诊断
    └── settings_menu.py    # 系统设置
```

---

## 风险评估与应对

| 风险 | 概率 | 影响 | 应对措施 |
|------|------|------|----------|
| 与现有配置不兼容 | 中 | 高 | 保留旧配置读取逻辑作为 fallback |
| Position 被误删 | 低 | 高 | 强制检查引用数，非零时禁止删除 |
| Jinja2 模板语法错误 | 中 | 中 | 保存前强制验证模板语法 |
| 文件权限问题 | 低 | 中 | 添加异常处理，友好提示 |

---

## 里程碑检查点

### Milestone 1: 基础设施完成（Day 1 结束）
- [ ] PositionStore 可正常读写
- [ ] AgentStore 可正常读写
- [ ] 配置诊断器可运行

### Milestone 2: 核心功能完成（Day 2 结束）
- [ ] 可创建/编辑/删除 Position
- [ ] 可创建/编辑/删除 Agent
- [ ] 可实现换岗功能
- [ ] 快速开始向导可用

### Milestone 3: 集成完成（Day 3 结束）
- [ ] 所有菜单集成完毕
- [ ] 配置诊断集成
- [ ] UI/UX 优化完成

### Milestone 4: 发布就绪（Day 4 结束）
- [ ] 所有测试通过
- [ ] 文档齐全
- [ ] 手动验收通过

---

## 附录：保留与废弃清单

### 保留（复用）
| 组件 | 位置 | 说明 |
|------|------|------|
| EnvironmentChecker | validators.py | 环境检查 |
| APIValidator | validators.py | API 验证 |
| _configure_api_interactive | core.py | API 配置交互 |
| ConfigValidator | validators.py | 配置验证 |

### 废弃（删除或重构）
| 组件 | 位置 | 处理方式 |
|------|------|----------|
| ROLE_TEMPLATES | templates.py | 改为从 YAML 加载 |
| _create_agents_yaml | core.py | 改为 _create_agent_yaml |
| _load_agents | core.py | 改为 AgentStore |
| _save_agents | core.py | 改为 AgentStore |
| AgentProfile dataclass | core.py | 改为 AgentConfig |

---

*计划版本: 1.0*  
*创建日期: 2026-03-08*  
*预计完成: 2026-03-12*
