> ⚠️ **注意**: Skill 系统当前实现已废弃，计划重新设计。本文档仅供参考。

# Tlon 技能系统架构设计

> 版本: 1.0  
> 更新日期: 2026-03-07  
> 说明: 本文档记录 Tlon 项目中技能（Skills）机制的设计规范（已废弃）

---

## 1. 概述

技能系统允许 AI Agent 在需要时加载和使用预定义的专业知识和工具。技能采用**渐进式披露**设计，按需加载内容以优化上下文使用。

### 1.1 设计原则

- **路径统一**: 所有技能路径以 `./skills/` 为前缀，相对于项目根目录
- **三阶段生命周期**: Discovery（发现）→ Activation（激活）→ Execution（执行）
- **专门工具管理**: 使用专用工具控制技能的生命周期
- **复用现有能力**: 脚本执行复用 `executeBashCommand`，在 **🔒 Sandbox 层**（Docker 容器）执行

### 1.2 在整体架构中的位置

```
┌─────────────────────────────────────────────────────────────┐
│                         Agent 层                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │                 Skill Manager                        │   │
│  │  - Discovery: 扫描 skills/ 目录                       │   │
│  │  - Activation: 加载 SKILL.md 内容                    │   │
│  │  - Execution: 调用脚本/读取资源                       │   │
│  └─────────────────────────────────────────────────────┘   │
└───────────────────────────────┬─────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────┐
│                         工具层 (tools/)                      │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐ │
│  │ activate_   │  │ load_skill_ │  │ execute_bash.py     │ │
│  │ skill.py    │  │ resource.py │  │ ExecuteBashTool     │ │
│  └─────────────┘  └─────────────┘  └─────────────────────┘ │
└───────────────────────────────┬─────────────────────────────┘
                                │ docker exec
┌───────────────────────────────▼─────────────────────────────┐
│                    🔒 Sandbox 层 (Docker 容器)                │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  skills/ 目录挂载到容器内 /workspace/skills           │   │
│  │  - SKILL.md (技能定义)                               │   │
│  │  - scripts/ (可执行脚本)                             │   │
│  │  - references/ (参考文档)                            │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

技能系统位于 **Agent 层**的 **Skill Manager**，通过**工具层**的 `executeBashTool` 在 **🔒 Sandbox 层**执行脚本。

---

## 2. 目录结构

```
skills/
├── ARCHITECTURE.md         # 本文档：技能系统架构设计
├── csv-pipeline/           # 示例技能：CSV 处理流程
│   ├── SKILL.md
│   └── references/
│       └── pandas-api.md
├── frontend-design/        # 示例技能：前端设计指南
│   └── SKILL.md
├── plan-i/                 # 示例技能：规划文档创建
│   ├── SKILL.md
│   └── scripts/
│       └── pl-init.sh
└── doc/                    # 官方规范文档（参考）
    ├── specification.mdx
    └── what-are-skills.mdx
```

---

## 3. 系统提示（Discovery）

在系统提示中包含技能列表，供 Agent 了解可用技能：

```
可用技能列表:
┌─────────────────┬──────────────────────────────────────────────────────┬──────────────────────────┐
│ 技能名称        │ 描述                                                 │ 根路径                   │
├─────────────────┼──────────────────────────────────────────────────────┼──────────────────────────┤
│ csv-pipeline    │ 使用 pandas 处理 CSV 文件的完整流程                   │ ./skills/csv-pipeline    │
│ frontend-design │ 前端开发设计指南，包含技术栈、项目结构和最佳实践      │ ./skills/frontend-design │
│ plan-i          │ 创建结构化规划文档（PLAN.md），用于指导开发           │ ./skills/plan-i          │
└─────────────────┴──────────────────────────────────────────────────────┴──────────────────────────┘

技能使用流程:
1. 调用 activateSkill(skillName) 激活技能，加载 SKILL.md 到上下文
2. 如需引用技能内文件，调用 loadSkillResource(skillName, resourceName)
3. 执行完成后，调用 deactivateSkill(skillName) 释放资源
4. 脚本执行使用 executeBashCommand，路径格式: ./skills/{skillName}/scripts/xxx.sh
```

---

## 4. 工具设计

### 4.1 activateSkill

**功能**: 激活一个技能，将其 SKILL.md 内容加载到当前上下文中

**参数**:
```yaml
skillName:
  type: string
  description: 技能名称（如 "plan-i"）
```

**返回值**:
- 成功: 返回 SKILL.md 的完整内容
- 失败: 返回错误信息（技能不存在、已激活等）

**注意事项**:
- 可同时激活多个技能
- 激活状态在对话期间保持

---

### 4.2 loadSkillResource

**功能**: 加载技能目录下的资源文件（references/、assets/ 等）

**参数**:
```yaml
skillName:
  type: string
  description: 技能名称
resourceName:
  type: string
  description: 资源相对路径（如 "references/pandas-api.md"）
```

**返回值**:
- 成功: 返回文件内容
- 失败: 返回错误信息（文件不存在等）

**示例**:
- `loadSkillResource("csv-pipeline", "references/pandas-api.md")`
- `loadSkillResource("plan-i", "templates/default.md")`

---

### 4.3 deactivateSkill

**功能**: 关闭技能，释放上下文资源

**参数**:
```yaml
skillName:
  type: string
  description: 技能名称
```

**返回值**:
- 成功: 确认技能已关闭
- 失败: 技能未激活或不存在

**注意事项**:
- 调用为可选：如不显式关闭，对话结束时自动清理
- 显式关闭有助于 Agent 管理上下文窗口

---

## 5. 执行流程

### 5.1 场景 A：纯指南型技能（csv-pipeline）

**场景描述**: SKILL.md 只包含操作指南和代码示例，没有 scripts/

**流程**:
```
用户: 帮我分析 data.csv 的重复项
  ↓
Agent 识别: 需要处理 CSV 文件，使用 csv-pipeline 技能
  ↓
1. [activateSkill: csv-pipeline]
   → 返回 SKILL.md 内容（包含 pandas 代码示例）
  ↓
2. Agent 根据指南生成并执行 Python 代码
   [executeBashCommand: python3 -c "...pandas code..."]
  ↓
3. [deactivateSkill: csv-pipeline] （可选）
```

---

### 5.2 场景 B：带脚本型技能（plan-i）

**场景描述**: SKILL.md 包含脚本引用，位于 scripts/ 目录

**流程**:
```
用户: 创建"用户管理模块"的开发规划
  ↓
Agent 识别: 需要创建规划文档，使用 plan-i 技能
  ↓
1. [activateSkill: plan-i]
   → 返回 SKILL.md 内容（包含 ./scripts/pl-init.sh 说明）
  ↓
2. Agent 根据指南执行初始化脚本
   [executeBashCommand: bash ./skills/plan-i/scripts/pl-init.sh 用户管理模块]
  ↓
3. Agent 继续根据指南编辑生成的 PLAN.md
   [writeToFile: PLAN.md, content: ...]
  ↓
4. [deactivateSkill: plan-i] （可选）
```

---

### 5.3 场景 C：引用 reference 文件

**场景描述**: 需要加载 skill 的 references/ 目录下的额外文档

**流程**:
```
用户: 查看 pandas 处理大数据的优化方法
  ↓
1. [activateSkill: csv-pipeline]
   → 返回 SKILL.md 内容（提到 references/optimization.md）
  ↓
2. [loadSkillResource: csv-pipeline, references/optimization.md]
   → 返回优化指南内容
  ↓
3. Agent 根据指南执行操作...
```

---

## 6. 设计说明

### 6.1 关键决策

| 设计决策 | 理由 |
|----------|------|
| **统一路径格式** `./skills/{name}/...` | 简化 Agent 认知，不依赖绝对路径，便于配置 |
| **activateSkill 返回 SKILL.md 内容** | 直接加载到上下文，无需二次读取文件 |
| **单独 loadSkillResource 工具** | 按需加载 references，避免一次性加载过多内容 |
| **deactivateSkill 可选** | 给 Agent 控制权，同时支持自动清理 |
| **脚本仍用 executeBashCommand** | 复用现有能力，避免功能重复 |

### 6.2 路径对照表

| 类型 | SKILL.md 中的写法 | Agent 实际使用的路径/调用 |
|------|-------------------|---------------------------|
| 脚本执行 | `./scripts/pl-init.sh` | `executeBashCommand("bash ./skills/plan-i/scripts/pl-init.sh")` |
| 引用文件 | `references/xxx.md` | `loadSkillResource("csv-pipeline", "references/xxx.md")` |
| 模板文件 | `assets/template.md` | `loadSkillResource("plan-i", "assets/template.md")` |

### 6.3 与官方标准的对应关系

| 官方标准 | 本设计实现 |
|----------|------------|
| 渐进式披露（Progressive Disclosure） | Discovery → Activation → Execution 三阶段 |
| 目录结构（SKILL.md + 可选目录） | 完全兼容 |
| 文件引用使用相对路径 | 使用 `./skills/{name}/` 前缀，相对于项目根目录 |
| 脚本执行由 Agent 实现决定 | 复用现有 `executeBashCommand` |

---

## 7. 示例技能说明

### 7.1 csv-pipeline
- **类型**: 纯指南型
- **用途**: CSV 文件处理流程
- **特点**: 只有 SKILL.md 和 references/，Agent 根据指南生成代码

### 7.2 frontend-design
- **类型**: 纯指南型
- **用途**: 前端开发设计规范
- **特点**: 只有 SKILL.md，提供设计原则和最佳实践

### 7.3 plan-i
- **类型**: 带脚本型
- **用途**: 创建结构化规划文档
- **特点**: 包含 scripts/pl-init.sh，Agent 调用脚本初始化 PLAN.md

---

## 8. 后续扩展建议

1. **技能版本管理**: 在 SKILL.md frontmatter 中增加 version 字段
2. **技能依赖**: 支持一个技能声明依赖其他技能
3. **技能市场**: 从远程仓库下载和更新技能
4. **自动发现**: 扫描 skills/ 目录自动构建系统提示
5. **使用统计**: 记录技能使用频率，优化系统提示排序

---

## 9. 参考文档

- [官方技能规范](doc/specification.mdx)
- [什么是技能](doc/what-are-skills.mdx)
