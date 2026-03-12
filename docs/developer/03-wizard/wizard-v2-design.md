# Minions Wizard 2.0 设计方案

> 适配 Position-Agent 解耦架构的全新配置向导

---

## 一、架构背景

### 旧架构问题
- 单文件 `config/agents.yaml` 管理所有 Agent
- Agent 配置嵌套 `role` 对象（职责、性格、规则混在一起）
- Position（岗位）与 Agent（人员）概念混淆

### 新架构设计
- **Position（岗位）**: 标准化 JD，定义职责、规则、Prompt 模板
- **Agent（人员）**: 个性化配置，通过 `position_id` 关联岗位
- 配置文件分离：`config/positions/*.yaml` + `config/agents/*.yaml`

---

## 二、功能模块设计

### 2.1 主菜单

```
📋 主菜单
├── 🚀 快速开始（初始化完整团队）
├── 🏢 岗位管理（Position CRUD）
├── 👤 人员管理（Agent CRUD + 换岗）
├── 🔍 配置诊断（验证引用完整性）
├── ⚙️  系统设置（API / 存储 / 导入导出）
└── ❌ 退出
```

### 2.2 岗位管理模块

```
🏢 岗位管理
├── 📋 列出所有岗位
│   └── 表格：type | title | department | proactive | 引用数
├── 🔍 查看岗位详情
│   ├── 基本信息：type, title, department, proactive
│   ├── 职责描述（JD）
│   ├── 协作规则（rules）
│   └── Prompt 模板预览（Jinja2）
├── ➕ 创建岗位
│   ├── 从预设模板创建（developer/operator/pm/cs 等）
│   └── 完全自定义
│       ├── type（唯一标识）
│       ├── title（显示名称）
│       ├── department（部门）
│       ├── proactive（常驻/延迟）
│       ├── JD（职位描述）
│       ├── rules（协作规则列表）
│       └── prompt_template（Jinja2 模板，支持变量预览）
├── ✏️  编辑岗位
│   ├── 修改所有字段
│   └── ⚠️  警告：修改会影响所有关联 Agent
├── 🗑️  删除岗位
│   └── ❌ 拒绝删除：如果有 Agent 引用该 position_id
└── 📥 导入/导出岗位模板
    ├── 导出为 YAML 文件
    └── 从 YAML 文件导入
```

#### 岗位创建交互流程

```
选择创建方式:
  ▸ 从预设模板创建
    ▸ 开发工程师 → 基于 developer 模板
    ▸ 运维工程师 → 基于 operator 模板
    ▸ ...
  ▸ 完全自定义

岗位标识 (type): data_engineer
显示名称 (title): 数据工程师
部门: 数据平台部
启动模式: [ ]常驻启动 [✓]延迟启动

职责描述 (JD):
> 负责数据管道建设、ETL流程开发和数据质量管理

协作规则 (逐条添加):
  1. 数据处理任务必须先在测试环境验证
  2. 涉及生产数据的变更需要双重确认
  [+ 添加规则]

Prompt 模板 (Jinja2):
> 你是 {{ agent.name }}，一名 {{ position.title }}...
> 你的职责是: {{ position.jd }}
> [预览实际效果]
```

### 2.3 人员管理模块

```
👤 人员管理
├── 📋 列出所有 Agent
│   └── 表格：id | name | position_id | 岗位名称 | 模型 | 状态
├── 🔍 查看 Agent 详情
│   ├── 个人信息：id, name, personality
│   ├── 关联岗位：position_id → 显示岗位 title
│   ├── LLM 配置：model, temperature
│   ├── Docker 配置：image, memory_limit
│   └── 技能列表
├── ➕ 招聘新人（创建 Agent）
│   ├── Step 1: 选择岗位（从现有 position 中选择）
│   ├── Step 2: 个性化设置（姓名、personality）
│   ├── Step 3: LLM 配置（模型、temperature）
│   ├── Step 4: Docker 配置（内存限制）
│   ├── Step 5: 技能选择（根据岗位推荐默认技能）
│   └── Step 6: 确认生成（预览 YAML 结构）
├── ✏️  编辑 Agent
│   ├── 修改姓名
│   ├── 调整个人的 personality
│   ├── 更换 LLM 配置
│   ├── 调整 Docker 配置
│   └── 修改技能列表
├── 🔄 换岗（更换 Position）
│   ├── 选择新岗位
│   ├── ⚠️  风险提示（职责、规则、Prompt 变更）
│   ├── 预览变更（对比表）
│   └── 确认换岗
├── 🗑️  删除 Agent
│   └── 确认后删除 YAML 文件及数据目录
└── 📥 批量导入/导出
```

#### 换岗功能详细交互

```
选择 Agent: 林知遥 (当前岗位: 开发工程师)

选择新岗位:
  ▸ 运维工程师
  ▸ 项目经理 [常驻启动]
  ▸ 客服 [常驻启动]
  ▸ ...

⚠️  换岗确认

变更预览:
┌──────────────┬──────────────────┬──────────────────┐
│ 项目         │ 当前             │ 变更后           │
├──────────────┼──────────────────┼──────────────────┤
│ 岗位         │ 开发工程师       │ 运维工程师       │
│ 部门         │ 技术部           │ 运维部           │
│ 启动模式     │ 延迟启动         │ 延迟启动         │
│ Prompt模板   │ 你是开发工程师...│ 你是运维工程师...│
│ 默认规则     │ 2条              │ 3条              │
└──────────────┴──────────────────┴──────────────────┘

确认换岗? [Y/n]
```

### 2.4 快速开始（重构）

```
🚀 快速开始向导

步骤 1/4: 环境检查
  ✓ Python 3.11+
  ✓ Docker 守护进程
  ✓ API 连接

步骤 2/4: API 配置
  [同现有实现]

步骤 3/4: 团队配置
  选择初始化方式:
    ▸ 使用标准团队（推荐）
      └── 自动创建 6 个 Position + 6 个 Agent
          ├── developer → 林知遥
          ├── operator → 江予白
          ├── project_manager → 沈清晏
          ├── customer_service → 顾言蹊
          ├── product_manager → 苏晚晴
          └── data_analyst → 谢云舒
      
    ▸ 自定义团队
      ├── 选择需要的岗位（多选，从 positions/*.yaml 加载）
      └── 为每个岗位配置 Agent
          ├── 是否启用该岗位
          ├── Agent 姓名
          └── 模型选择

步骤 4/4: 确认创建
  显示生成的文件列表:
    config/positions/
      ├── developer.yaml
      ├── operator.yaml
      └── ...
    config/agents/
      ├── lin-zhiyao.yaml
      ├── jiang-yubai.yaml
      └── ...
```

### 2.5 配置诊断（新增模块）

```
🔍 配置诊断

检查项:
├── Position 配置检查
│   ├── YAML 语法有效性
│   ├── 必填字段检查 (type, title, jd)
│   └── Jinja2 模板语法检查
├── Agent 配置检查
│   ├── YAML 语法有效性
│   ├── position_id 引用有效性
│   └── skills 列表有效性
├── 引用完整性
│   ├── 检测孤立的 Position（无 Agent 引用）
│   └── 检测无效的 position_id 引用
└── 系统环境检查
    ├── Docker 连接
    └── API 连通性

输出示例:
  ✓ Position: 6 个岗位配置正常
  ✓ Agent: 6 个 Agent 配置正常
  ✓ 引用检查: 所有 position_id 引用有效
  ⚠ 警告: developer 岗位被 0 个 Agent 引用
```

---

## 三、数据文件格式

### 3.1 Position 配置格式

```yaml
# config/positions/custom-role.yaml
position:
  type: custom-role
  title: 自定义岗位
  jd: "负责..."
  department: "技术部"
  proactive: false
  rules:
    - "规则1"
    - "规则2"
  prompt_template: |
    你是 {{ agent.name }}，{{ position.title }}...
```

### 3.2 Agent 配置格式

```yaml
# config/agents/new-agent.yaml
agent:
  id: new-001
  name: 新员工
  personality: "描述..."
  position_id: custom-role  # 引用 Position
  llm:
    model_name: kimi-k2.5
    temperature: 0.7
  docker:
    image: ubuntu:22.04
    memory_limit: 512m
  skills:
    - file-operations
```

---

## 四、关键设计决策

| 决策点 | 方案 | 理由 |
|--------|------|------|
| Position 删除限制 | 有引用时禁止删除 | 保持数据完整性，避免 Agent 指向无效 position_id |
| 换岗实现 | 只修改 Agent 的 position_id | 不修改 Position 定义，实现真正的解耦 |
| 预设模板来源 | 从 positions/*.yaml + 内置 fallback | 允许用户自定义模板，同时提供开箱即用体验 |
| 配置文件命名 | `agents/{agent-id}.yaml` | 清晰、易管理、避免 ID 冲突 |
| 批量操作 | 仅支持导入/导出 | 简化实现，复杂批量编辑建议直接编辑文件 |
| Prompt 模板预览 | 使用示例数据渲染 Jinja2 | 帮助用户理解模板变量效果 |

---

## 五、与现有代码的兼容

### 保留内容
- API 配置交互（`_configure_api_interactive`）
- 环境检查（`EnvironmentChecker`）
- API 验证（`APIValidator`）

### 需要重构
- `ROLE_TEMPLATES` → 从 YAML 加载 Position
- `_create_agents_yaml` → `_create_agent_yaml`（单文件）
- Agent 增删改查逻辑（适配新配置结构）

### 新增模块
- PositionRegistry 集成（读取/验证 Position）
- 配置诊断器（`ConfigDiagnostics`）
- 换岗功能（`transfer_position`）

---

*文档版本: 1.0*
*设计日期: 2026-03-08*
*适配架构: Position-Agent 解耦架构*
