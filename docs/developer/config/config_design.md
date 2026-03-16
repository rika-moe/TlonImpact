# Tlon 配置系统设计文档

> 版本: 1.0  
> 日期: 2026-03-08  
> 状态: 设计中

## 1. 概述

本文档描述 Tlon 的三层配置架构：全局 Prompt 模板、Position（岗位）配置和 Agent 配置。

设计原则：
- **单一职责**: 每种配置只负责自己的领域
- **层级覆盖**: 上层配置可以覆盖下层配置的默认行为
- **显式优于隐式**: 覆盖行为是完整的替换，而非部分修改

---

## 2. 配置层级关系

```
┌─────────────────────────────────────────────────────────────────┐
│  Layer 1: 全局 Prompt 模板 (prompt_templates.yaml)               │
│  - 默认的 workflow、important_notes、discipline_rules          │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼ 被 Position 的 prompt_overrides 覆盖
┌─────────────────────────────────────────────────────────────────┐
│  Layer 2: Position 配置 ({position_id}.yaml)                     │
│  - 岗位基础信息 (id, title, jd, proactive, contact_when)        │
│  - 行为规则 (rules，用于团队表格展示)                            │
│  - 可选的 prompt_overrides (完全覆盖全局配置)                    │
│  - 默认技能 (default_skills)                                    │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼ 被 Agent 继承和扩展
┌─────────────────────────────────────────────────────────────────┐
│  Layer 3: Agent 配置 ({agent_id}.yaml)                           │
│  - Agent 基础信息 (id, name, position_id, personality)          │
│  - 运行时配置 (llm, docker, memory)                             │
│  - 技能列表 (skills，覆盖 position.default_skills)              │
│  - 加载策略 (loading: resident/lazy)                            │
└─────────────────────────────────────────────────────────────────┘
```

---

## 3. 全局 Prompt 模板配置

### 3.1 文件路径
```
config/prompt_templates.yaml
```

### 3.2 Schema

```yaml
type: object
required: [templates]
properties:
  templates:
    type: object
    properties:
      workflow:
        type: string
        description: "默认工作流程描述，用于所有未覆盖的 Position"
      
      important_notes:
        type: array
        items:
          type: string
        description: "默认重要提示列表"
      
      discipline_rules:
        type: array
        items:
          type: string
        description: "默认对话规则列表（支持多行）"
```

### 3.3 示例

```yaml
templates:
  workflow: |
    1. **思考阶段**: 分析任务，决定需要执行哪些操作
    2. **执行阶段**: 使用工具收集信息或执行操作
    3. **完成阶段**: 使用 completeDialog 工具发送最终回复并结束处理
  
  important_notes:
    - "⚠️ **必须使用 completeDialog 结束**"
    - "⚠️ **@mention 机制**: 只有通过 mentions 参数指定的人才会收到通知"
    - "使用表格中'名字'列的完整姓名作为 mentions 参数（如 ['顾言蹊', '江予白']）"
  
  discipline_rules:
    - "1. **收到任务时**: 确认收到即可，不要重复 @发送者"
    - '   - ✅ 正确: "收到，我来处理"'
    - '   - ❌ 错误: "@顾言蹊 收到，我来处理"（不要回@）'
    - ""
    - "2. **任务完成时**: 向任务分派者汇报"
    - "   - 如果 @沈清晏 派的活 → 完成后 @沈清晏"
    - "   - 如果 @人类 直接派的活 → 完成后 @人类"
```

---

## 4. Position 配置

### 4.1 文件路径
```
config/positions/{position_id}.yaml
```

### 4.2 Schema

```yaml
type: object
required: [position]
properties:
  position:
    type: object
    required: [id, type, title, jd, proactive, rules]
    properties:
      # 基础信息
      id:
        type: string
        description: "岗位唯一标识"
      
      type:
        type: string
        description: "岗位类型代码（通常与 id 相同）"
      
      title:
        type: string
        description: "岗位显示名称"
      
      department:
        type: string
        description: "所属部门（可选）"
      
      # 职责描述
      jd:
        type: string
        description: "岗位职责描述（Job Description），用于角色提示"
      
      # 行为控制
      proactive:
        type: boolean
        description: "是否主动响应（true=主动，false=被动）"
      
      contact_when:
        type: string
        description: "何时应该联系该角色（用于团队表格的'何时@他'列）"
      
      # 行为规则（用于团队表格展示）
      rules:
        type: array
        items:
          type: string
        description: "职位特定的行为规则"
      
      # Prompt 覆盖配置（可选）
      prompt_overrides:
        type: object
        description: "完全覆盖全局 prompt 模板。如果提供字段，则完全替换；否则使用全局默认"
        properties:
          workflow:
            type: string
            description: "覆盖默认工作流程（完全替换）"
          
          important_notes:
            type: array
            items:
              type: string
            description: "覆盖重要提示列表（完全替换）"
          
          discipline_rules:
            type: array
            items:
              type: string
            description: "覆盖对话规则列表（完全替换）"
      
      # 技能配置
      default_skills:
        type: array
        items:
          type: string
        description: "该职位默认拥有的技能列表"
```

### 4.3 示例 1：使用默认 Prompt

```yaml
position:
  id: "data_analyst"
  type: "data_analyst"
  title: "数据分析师"
  department: "数据部"
  
  jd: |
    负责数据处理、可视化和统计分析。
    严谨细致、善于发现规律、数据敏感。
  
  proactive: false
  contact_when: "需要数据分析或可视化"
  
  rules:
    - "只在你被@时响应"
    - "完成任务后向给你派活的人汇报"
  
  # prompt_overrides 省略，使用全局默认
  
  default_skills:
    - "file-operations"
    - "csv-pipeline"
```

### 4.4 示例 2：完全覆盖 Workflow

```yaml
position:
  id: "customer_service"
  type: "customer_service"
  title: "客服专员"
  
  jd: |
    用户的第一接触点。
    友好、耐心地接待用户。
  
  proactive: true
  contact_when: "需要首响应或问题分类"
  
  rules:
    - "转派后不要持续跟进，相信同事会处理"
    - "除非用户催促或被明确要求跟进"
  
  prompt_overrides:
    workflow: |
      1. **首响应**: 快速响应用户，进行初步诊断和安抚
      2. **分类**: 判断问题类型（技术问题/需求问题/大问题）
      3. **转派**: 使用 completeDialog 的 mentions 参数指定负责人
      4. **跟进**: 只在用户催促或被明确要求时跟进
  
  default_skills:
    - "file-operations"
    - "plan-i"
```

### 4.5 示例 3：完全覆盖所有 Prompt 模块

```yaml
position:
  id: "project_manager"
  type: "project_manager"
  title: "项目经理"
  
  jd: |
    负责任务协调、进度把控和资源分配。
  
  proactive: false
  contact_when: "需要协调资源或分派任务"
  
  rules:
    - "不要直接执行技术操作，而是通过分派任务"
    - "分派任务时使用 completeDialog 的 mentions 参数指定负责人"
    - "跟踪任务进度并汇总结果向用户汇报"
  
  prompt_overrides:
    workflow: |
      1. **接收**: 接收客服转派的大问题或协调需求
      2. **评估**: 评估任务复杂度，选择合适团队成员
      3. **分派**: 使用 completeDialog 的 mentions 参数分派任务
      4. **跟踪**: 跟踪任务进度，主动询问完成情况
      5. **汇报**: 汇总结果向用户汇报
    
    important_notes:
      - "⚠️ **必须使用 completeDialog 结束**"
      - "⚠️ **@mention 机制**: 只有通过 mentions 参数指定的人才会收到通知"
      - "项目经理注意：分派任务时必须明确 mentions 负责人"
      - "跟踪任务时如果超时未回复，需要主动催促"
    
    discipline_rules:
      - "1. **收到任务时**: 确认收到，评估后决定是否需要分派"
      - "2. **分派任务时**: 使用 mentions 指定明确的负责人"
      - "3. **跟踪任务时**: 主动询问进度，不要等待汇报"
      - "4. **任务完成时**: 向用户汇总汇报所有结果"
  
  default_skills:
    - "file-operations"
    - "plan-i"
```

---

## 5. Agent 配置

### 5.1 文件路径
```
config/agents/{agent_id}.yaml
```

### 5.2 Schema

```yaml
type: object
required: [agent]
properties:
  agent:
    type: object
    required: [id, name, position_id]
    properties:
      # 基础信息
      id:
        type: string
        description: "Agent 唯一标识"
      
      name:
        type: string
        description: "Agent 显示名称"
      
      position_id:
        type: string
        description: "关联的岗位 ID，决定继承哪个 Position 的配置"
      
      personality:
        type: string
        description: "个性特点描述，用于 prompt 中的'个人特点'"
      
      # LLM 配置（可选，覆盖全局）
      llm:
        type: object
        properties:
          model:
            type: string
          temperature:
            type: number
          max_tokens:
            type: integer
      
      # Docker 运行时配置（可选）
      docker:
        type: object
        properties:
          image:
            type: string
          work_dir:
            type: string
          timeout:
            type: integer
      
      # 记忆系统配置（可选）
      memory:
        type: object
        properties:
          working_memory_limit:
            type: integer
          summary_threshold:
            type: integer
      
      # 技能配置（可选，覆盖 position.default_skills）
      skills:
        type: array
        items:
          type: string
        description: "该 Agent 的技能列表"
      
      # 加载策略（可选）
      loading:
        type: string
        enum: [resident, lazy]
        description: "resident=随系统启动，lazy=被@时启动"
```

### 5.3 示例

```yaml
agent:
  id: "gu-yanxi"
  name: "顾言蹊"
  position_id: "customer_service"
  personality: "温和耐心，善于沟通，喜欢帮助用户解决问题"
  
  llm:
    model: "gpt-4o-mini"
    temperature: 0.7
  
  docker:
    image: "tlon-agent:latest"
    work_dir: "/workspace"
    timeout: 300
  
  skills:
    - "file-operations"
    - "plan-i"
    - "frontend-design"
  
  loading: "resident"
```

---

## 6. 覆盖逻辑

### 6.1 Prompt 内容获取逻辑

```python
def get_prompt_component(position, component_name):
    """
    获取 Prompt 组件的统一逻辑
    
    Args:
        position: Position 对象
        component_name: 'workflow' | 'important_notes' | 'discipline_rules'
    
    Returns:
        如果 Position 提供了 prompt_overrides.{component}，返回它
        否则返回全局配置中的对应组件
    """
    # 1. 检查 Position 是否提供了覆盖
    if (position.prompt_overrides 
        and component_name in position.prompt_overrides):
        return position.prompt_overrides[component_name]
    
    # 2. 返回全局默认
    return global_templates[component_name]
```

### 6.2 决策规则

| 配置项 | Position 提供 | 使用来源 |
|--------|--------------|----------|
| workflow | ❌ 未提供 | 全局 templates.workflow |
| workflow | ✅ 提供 | position.prompt_overrides.workflow |
| important_notes | ❌ 未提供 | 全局 templates.important_notes |
| important_notes | ✅ 提供 | position.prompt_overrides.important_notes |
| discipline_rules | ❌ 未提供 | 全局 templates.discipline_rules |
| discipline_rules | ✅ 提供 | position.prompt_overrides.discipline_rules |

**注意**：覆盖是**完全替换**，不是追加或合并。

---

## 7. 系统 Prompt 构建流程

最终的系统 Prompt 由以下部分组成：

```
┌─────────────────────────────────────────────────────────────┐
│ 1. Header（Agent 基础信息）                                   │
│    - "# 你是 {name}"                                         │
│    - "岗位: {position.title}"                                │
│    - "岗位职责: {position.jd}"                               │
│    - "个人特点: {personality}"                               │
├─────────────────────────────────────────────────────────────┤
│ 2. 可用工具列表（代码硬编码）                                  │
│    - sendMessage, executeBashCommand, rememberFact...        │
├─────────────────────────────────────────────────────────────┤
│ 3. 角色职责（从 position.jd 加载）                            │
│    - "岗位职责:\n{position.jd}"                              │
├─────────────────────────────────────────────────────────────┤
│ 4. 团队成员表格（动态构建）                                    │
│    - 从 chatroom participants 构建                           │
│    - 使用 position.contact_when 等信息                       │
├─────────────────────────────────────────────────────────────┤
│ 5. 可用技能（如果配置了 skills）                              │
│    - 技能表格和已激活技能上下文                                │
├─────────────────────────────────────────────────────────────┤
│ 6. 历史记忆（从 memory 加载）                                 │
├─────────────────────────────────────────────────────────────┤
│ 7. 对话规则 ← 从配置获取                                       │
│    - 优先: position.prompt_overrides.discipline_rules         │
│    -  fallback: global.templates.discipline_rules             │
├─────────────────────────────────────────────────────────────┤
│ 8. 工作流程 ← 从配置获取                                       │
│    - 优先: position.prompt_overrides.workflow                 │
│    -  fallback: global.templates.workflow                     │
├─────────────────────────────────────────────────────────────┤
│ 9. 重要提示 ← 从配置获取                                       │
│    - 优先: position.prompt_overrides.important_notes          │
│    -  fallback: global.templates.important_notes              │
├─────────────────────────────────────────────────────────────┤
│ 10. 技能特定提示（如果配置了 skills）                          │
└─────────────────────────────────────────────────────────────┘
```

---

## 8. 相关文件变更

### 8.1 新增文件

| 文件 | 说明 |
|------|------|
| `config/prompt_templates.yaml` | 全局 Prompt 模板配置 |
| `minions/prompt_templates.py` | 模板加载器（PromptTemplateRegistry） |
| `docs/developer/config/config_design.md` | 本文档 |
| `docs/developer/config/config_roadmap.md` | 实施路线图 |

### 8.2 修改文件

| 文件 | 变更 |
|------|------|
| `minions/positions/models.py` | Position 模型添加 `prompt_overrides` 字段 |
| `minions/agents/agent.py` | 重构 `_build_system_prompt()` 使用模板配置 |
| `config/positions/*.yaml` | 删除 `prompt_template` 字段，添加 `contact_when` 和 `prompt_overrides`（可选） |

### 8.3 删除内容

- Position 配置中的 `prompt_template` 字段（不再使用）
- `agent.py` 中的 `_build_role_prompt()` 方法（被 `position.jd` 替代）

---

## 9. 附录

### 9.1 术语表

| 术语 | 说明 |
|------|------|
| Position | 岗位/职位，定义一类角色的通用属性和行为 |
| Agent | 具体的 AI 代理实例，继承自某个 Position |
| prompt_overrides | 覆盖全局 Prompt 模板的配置 |
| proactive | 是否主动响应用户输入（无需被@） |
| resident/lazy | Agent 加载策略：resident=随系统启动，lazy=被@时启动 |

### 9.2 相关文档

- [config_roadmap.md](./config_roadmap.md) - 实施路线图
- `docs/developer/02-roadmap/agent_position_roadmap.md` - 原有路线图
