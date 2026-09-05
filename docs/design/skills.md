# Skill 能力设计

> 决策见 [ADR 0010](../decisions/0010-skills.md)。本文是设计与使用说明。

## 是什么

Skill 是"按需加载的技能包"：一个文件夹 + 一个 `SKILL.md`，教 agent 怎么做某一类事（工作流、领域 know-how、可执行脚本）。与相邻概念的分工：

- **人格**（SOUL.md 等，`(persona)` section）：常驻 system prompt，回答"我是谁"。
- **记忆**（episodic summaries / facts，`(knowledge)` section）：回答"发生过什么"。
- **技能**（`(skills)` section）：平时只有索引在上下文里，匹配到才读正文，回答"这类事该怎么做"。
- **工具**（`read_file` 等）：确定性的"能做什么"。skill 不是工具——它是编排工具的知识。

格式采用 Agent Skills 开放标准（Claude Code / Codex / Cursor 通用），第三方 skill 包拷进目录即用。

## 目录布局（三层）

| 层 | 宿主机路径 | 容器内路径 | 用途 |
|---|---|---|---|
| 项目级 | `~/.tlon/{agent_id}/workspace/.agents/skills/` | `/workspace/.agents/skills/` | 工作区仓库自带的工作流 |
| agent 级 | `~/.tlon/{agent_id}/skills/` | —（容器不可见） | 该 agent 的私人技能 |
| 全局级 | `~/.tlon/skills/` | —（容器不可见） | 跨 agent 共享 |

项目级目录经 workspace bind-mount 进容器，引擎在宿主机直读，不进容器扫描。同名 skill 冲突时 **项目 > agent > 全局**，后出现的被跳过并记日志。

注意：索引里的路径是**宿主机绝对路径**；项目级 skill 的正文模型也可以在容器内按 `/workspace/.agents/skills/...` 读（同一文件），但 `read_file` 工具走宿主机路径。

## SKILL.md 格式

```markdown
---
name: pdf-extract
description: 从 PDF 提取文本与表格。当任务涉及 PDF 解析时使用。
---

# PDF 提取

（正文：步骤、注意事项、脚本用法……）

脚本见 scripts/extract.py，用法：`python scripts/extract.py <file>`。
```

- frontmatter `name`、`description` 必填；缺失或 YAML 解析失败 → 跳过该 skill，记 warning。
- `description` 是模型判断"要不要用这个 skill"的唯一依据，要写清**做什么**和**什么时候用**。
- 正文引用的 `scripts/`、`references/` 用相对路径，模型读到后自行解析为绝对路径再调用。

## 运行机制

**扫描与索引**（`agents/skills.py`）：`(skills)` section 触发时，引擎扫三层目录的直接子目录（不递归），解析各 `SKILL.md` frontmatter，渲染成 `[可用技能]` 用户消息，每条一行：

```
[可用技能]

- pdf-extract (project): 从 PDF 提取文本与表格。当任务涉及 PDF 解析时使用。 — /Users/x/.tlon/dev-001/workspace/.agents/skills/pdf-extract/SKILL.md
- review-checklist (global): 代码评审清单。评审 PR 时使用。 — /Users/x/.tlon/skills/review-checklist/SKILL.md

需要某个技能时，用 read_file 读取其 SKILL.md 获取完整指令。
```

索引按全部 SKILL.md 的 mtime 签名缓存（与 system prompt 的 persona mtime 缓存同手法），无变更零磁盘重读，文件一改下一 session 生效。目录不存在视为空；三层全空则该 section 不产生任何消息。

**DSL**：`(skills)` 是 `:context :sections` 向量的一员，无参数，与 `(knowledge)` 平级，按向量顺序落位：

```clojure
:context {:sections [(persona) (protocol) (knowledge) (skills) (current-task) (thread-recap) (mail-history 20)]}
```

不加该 section 的成员没有任何 skill 开销（确定性 script 成员天然如此）。

**加载（progressive disclosure）**：模型看到索引后自行决定——要用某 skill，就用 `read_file` 读索引里给出的 SKILL.md 路径拿正文；要跑脚本就用 `execute_bash`（容器内执行）。引擎不做正文注入，没有 `use_skill` 工具。

## 信任与安全

**SKILL.md 的内容是不可信输入。** 项目级 skill 来自 workspace——可能是 agent 自己 clone 的仓库，其中 skill 的指令（"先把某文件发出去"）模型可能照做。这与普通 prompt injection 同级：第一版的缓解只有两层——skill 永远需要模型"读了索引、主动读正文"才生效（无自动执行），以及 agent 本身运行在容器沙箱里。更严格的手段（`allowed-tools` 白名单、项目级 skill 启用确认门）留给后续决策。写 skill 的人请记住：description 和正文都会进模型上下文。

## 边界（第一版不做）

skill 市场/安装器、工具白名单、触发命令、版本管理。先跑通"三层目录 + 索引进上下文 + 模型自读"的最小闭环。
