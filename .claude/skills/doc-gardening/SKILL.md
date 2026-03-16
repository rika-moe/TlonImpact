---
name: doc-gardening
description: |
  文档园艺工具 - 当用户提到"整理文档"、"维护 AGENTS.md"、"更新 docs"、
  "文档与代码不一致"、"清理过时文档"、"渐进式文档"、
  "维护 roadmap"、"里程碑归档"或"路线图管理"时触发。

  使用代码为准原则（Code-First Principle）维护和整理项目文档：
  - AGENTS.md 是地图（~100行），不是百科全书
  - 设计文档用于编目和索引，记录验证状态和核心理念
  - 架构文档提供域和包分层的顶层地图
  - Roadmap 文档管理里程碑规划和演进路线
  - 追踪每个领域的文档质量和差距
  - 使用 uv 环境运行检查脚本

  核心原则：代码是源头，文档是映射；AGENTS.md 是入口，详细信息在下级文档中。
metadata:
  author: Tlon Team
  version: "2.0.0"
  tags: ["documentation", "maintenance", "gardening", "uv", "agents-md"]
  prerequisites: ["uv"]
---

# 文档园艺 (Doc Gardening)

> **AGENTS.md 是地图，不是百科全书。**

---

## 文档分层架构

### Layer 0: AGENTS.md - 地图层（~100行）

**职责**：提供入口和导航，不承载详细信息。

```
AGENTS.md
├── 产品一句话定义
├── 核心术语表（5-10个）
├── 文档索引表（链接到各域设计文档）
├── 快速开始（3步内）
└── 状态总览（各领域健康度评分）
```

**质量检查**：
- 行数 < 100
- 所有链接可点击
- 包含状态总览表格

### Layer 1: 设计文档 - 编目层

**位置**：`docs/design/*.md`

**职责**：编目和索引，记录验证状态和核心理念。

每个设计文档包含：
```markdown
# 领域名称

> 状态: ✅已验证 / ⚠️待验证 / ❌已过期
> 最后验证: YYYY-MM-DD

## 核心理念（3-5条）
1. ...
2. ...

## 文档索引
| 文档 | 目的 | 深度 |
|------|------|------|
| [架构](./architecture.md) | 包分层地图 | L2 |
| [API](./api.md) | 接口契约 | L2 |

## 验证清单
- [ ] 代码实现与文档一致
- [ ] 示例可运行
- [ ] 术语最新
```

### Layer 2: 架构文档 - 分层地图

**位置**：`docs/design/architecture.md` 或 `docs/architecture/*.md`

**职责**：提供域和包分层的顶层地图。

内容：
- 包分层图（顶层）
- 每个域的职责边界
- 域间依赖关系
- 质量评分和差距追踪

### Layer 3: Roadmap - 规划层

**位置**：`docs/roadmap/` 或 `docs/design/roadmap.md`

**职责**：记录里程碑、版本规划和演进路线。

结构：
```
roadmap/
├── README.md           # 路线图总览，当前里程碑状态
├── milestone-*.md      # 各里程碑详情
│   ├── 目标
│   ├── 已完成功能
│   ├── 待实现功能
│   └── 风险点
└── completed/          # 已完成的里程碑（归档）
    └── milestone-*.md
```

**状态标记**：
| 标记 | 含义 |
|------|------|
| ✅ | 已完成 |
| 🔄 | 进行中（当前里程碑）|
| ⏳ | 待开始 |
| 🗂️ | 已归档（移至 completed/）|

**与代码的关系**：
- Roadmap 是规划文档，不直接映射代码
- 代码实现后，更新对应里程碑状态
- 已完成的里程碑归档，保留历史

---

## 代码为准原则

> **代码 → 文档（永远如此）**

当发现冲突时：
1. 先验证代码（`uv run python -c "..."`）
2. 更新下级文档
3. 更新 AGENTS.md 索引和状态
4. 标记验证状态

---

## 园艺工具

### 1. 验证 AGENTS.md 简短性

```bash
uv run python .claude/skills/doc-gardening/scripts/check_agents_md.py \
    --max-lines 100
```

**检查项**：
- 总行数 < 100
- 包含文档索引表
- 包含状态总览
- 无详细实现描述

### 2. 评估文档质量

```bash
uv run python .claude/skills/doc-gardening/scripts/assess_quality.py \
    --design-dir ./docs/design
```

**输出示例**：
```
📊 文档质量评估

领域          评分    状态    差距
────────────  ──────  ──────  ──────────────
config        85/100  ✅      缺少示例
events        60/100  ⚠️      待验证，API过时
memory        40/100  ❌      文档缺失

总体健康度: 62%
```

### 3. 检查文档树完整性

```bash
uv run python .claude/skills/doc-gardening/scripts/verify_tree.py
```

### 4. 检查代码一致性

```bash
uv run python .claude/skills/doc-gardening/scripts/check_consistency.py
```

### 5. 验证 Roadmap 结构

```bash
uv run python .claude/skills/doc-gardening/scripts/check_roadmap.py \
    --roadmap-dir ./docs/roadmap
```

**检查项**：
- README.md 存在且包含当前里程碑状态
- 里程碑文件使用统一命名格式
- 状态标记一致性（✅🔄⏳🗂️）
- 已完成的里程碑已归档到 completed/
- 无孤立里程碑文件

**输出示例**：
```
📋 Roadmap 结构检查

总览: README.md ✅
里程碑: 5 个
  ✅ completed/  (3 个)
  🔄 current/    (1 个)
  ⏳ planned/    (1 个)

当前里程碑: milestone-3-agent-system
  进度: 8/12 任务 (67%)
  风险: 1 个 (Docker 沙箱延迟)
```

---

## 维护流程

### 单次维护流程

```
1. 运行质量评估
   └── uv run python scripts/assess_quality.py

2. 检查 AGENTS.md 简短性
   └── uv run python scripts/check_agents_md.py

3. 修复高优先级差距
   └── 代码验证 → 更新文档 → 更新状态

4. 更新 AGENTS.md 状态总览
   └── 反映各领域最新评分
```

### AGENTS.md 维护检查清单

- [ ] 总行数 < 100
- [ ] 包含文档索引表（链接到各设计文档）
- [ ] 包含状态总览（各领域健康度）
- [ ] 无详细实现（实现细节在下级文档）
- [ ] 术语表最新（5-10个核心术语）

### 设计文档维护检查清单

- [ ] 包含验证状态标记
- [ ] 核心理念 <= 5条
- [ ] 文档索引表完整
- [ ] 验证清单已勾选

### Roadmap 维护检查清单

- [ ] README.md 包含当前里程碑状态
- [ ] 里程碑文件命名统一（milestone-N-*.md）
- [ ] 状态标记正确（✅🔄⏳🗂️）
- [ ] 已完成的里程碑已归档到 completed/
- [ ] 进度与代码实现同步

---

## 状态标记系统

| 标记 | 含义 | 使用场景 |
|------|------|----------|
| ✅ | 已验证 | 文档与代码同步，已验证 |
| ⚠️ | 待验证 | 代码可能已变，待确认 |
| ❌ | 已过期 | 已知过时，需更新 |
| 🗂️ | 已归档 | 历史文档，不再维护 |

---

## 快速参考

```bash
# 完整检查
uv run python .claude/skills/doc-gardening/scripts/check_agents_md.py
uv run python .claude/skills/doc-gardening/scripts/assess_quality.py
uv run python .claude/skills/doc-gardening/scripts/verify_tree.py
uv run python .claude/skills/doc-gardening/scripts/check_consistency.py
uv run python .claude/skills/doc-gardening/scripts/check_roadmap.py
```

---

*AGENTS.md 是地图，不是百科全书。详细信息在下级文档中。*
