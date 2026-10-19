# Skill 能力设计

> 决策见 [ADR 0010](../decisions/0010-skills.md)。本文定义技能发现与上下文机制，并明确当前正文访问边界。

## 是什么

Skill 是"按需加载的技能包"：一个文件夹 + 一个 `SKILL.md`，教 agent 怎么做某一类事（工作流、领域 know-how、可执行脚本）。与相邻概念的分工：

- **人格**（SOUL.md 等，`(persona)` section）：常驻 system prompt，回答"我是谁"。
- **记忆**（claims，`(knowledge)` section）：回答"发生过什么"。
- **技能**（`(skills)` section）：平时只有索引在上下文里，匹配到才读正文，回答"这类事该怎么做"。
- **工具**（`readFile` 等）：确定性的"能做什么"。skill 不是工具——它是编排工具的知识。

格式采用 Agent Skills 开放标准（Claude Code / Codex / Cursor 通用），第三方技能包可以被扫描发现；正文和脚本是否可用，还取决于成员执行层级与容器可见路径。

## 目录布局（三层）

| 层 | 宿主机路径 | 容器内路径 | 用途 |
|---|---|---|---|
| 项目级 | `~/.tlon/{agent_id}/workspace/.agents/skills/` | `/workspace/.agents/skills/` | 工作区仓库自带的工作流 |
| agent 级 | `~/.tlon/{agent_id}/skills/` | `/tlon-home/skills/`（只读） | 该 agent 的私人技能 |
| 全局级 | `~/.tlon/skills/` | —（容器不可见） | 跨 agent 共享 |

项目级目录经 workspace bind-mount 进容器，引擎在宿主机直读，不进容器扫描。同名 skill 冲突时 **项目 > agent > 全局**，后出现的被跳过并记日志。

注意：索引当前输出**宿主机绝对路径**，但实际文件工具 `readFile` 只接受容器可见路径；没有通用宿主机 `read_file` 工具。项目级技能的正文位于 `/workspace/.agents/skills/...`，agent 级正文可以通过 `/tlon-home/skills/...` 访问；全局层没有对应容器挂载。

## 内置 skill

仓库自带的 skill 随包发布在 `src/tlon/skills/`（资源包，与内置原型库 `src/tlon/prototypes/` 同一打包方式），服务启动时由 `seed_skills`（[agents/skills.py](../../src/tlon/agents/skills.py)，镜像 `seed_prototypes`）幂等拷贝到全局层 `~/.tlon/skills/`——已存在的文件不覆盖，用户的修改与自建的 skill 在重新播种后存活。

当前内置：

- **explain-decision**：可问责性接口。被问「你为什么这么决定」时，教 agent 用 `listTraces`/`getTrace`、`searchMemories`、`mail-search` 三条证据源取证，并按「论断必附出处、无据明说不编造、区分事实与推断」的纪律组织回答。

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

索引按三层技能目录存在性、子目录名及 SKILL.md 的 mtime 签名缓存（与 system prompt 的 persona mtime 缓存同手法），无变更零磁盘重读，文件一改下一 session 生效。目录不存在视为空；三层全空则该 section 不产生任何消息。

**DSL**：`(skills)` 是 `:context :sections` 向量的一员，无参数，与 `(knowledge)` 平级，按向量顺序落位：

```clojure
:context {:sections [(persona) (protocol) (lessons) (skills) (knowledge) (current-task) (mail-history 20 10) (thread-recap)]}
```

不加该 section 的成员没有任何 skill 开销；是否使用该 section 由本次会话配方决定。

**按需加载意图**：模型从索引选择技能，再读取正文；引擎不注入正文，也没有 `use_skill` 工具。

**当前实现缺口**：`agents/skills.py` 的 `render_skills_index` 仍提示不存在的 `read_file`，并给出不能直接交给容器工具的宿主路径。L1/L2 可在知道映射关系时用 `readFile` 读取 project/agent 层；全局层（包括内置 explain-decision）没有通用正文读取通道。L0 不注册 filesystem 工具，无法通过此机制读取任何正文。脚本需要 L2 的 `executeBashCommand`，并且文件必须位于容器可见路径。

因此三层发现与索引已经实现，不能把它描述为三层正文访问均已跑通。补齐路径映射、全局正文可见性和 L0 加载入口需要独立设计及实现，此次文档整理不改变权限或增加工具。

## 信任与安全

**SKILL.md 的内容是不可信输入。** 项目级 skill 来自 workspace——可能是 agent 自己 clone 的仓库，其中 skill 的指令（"先把某文件发出去"）模型可能照做。这与普通 prompt injection 同级：当前不自动执行技能正文或脚本，实际工具调用仍受成员执行权限与文件可见范围约束。L0 没有 Docker 装置；L1/L2 的文件和执行边界见 [execution-device](execution-device.md)。更严格的手段（`allowed-tools` 白名单、项目级 skill 启用确认门）留给后续决策。写 skill 的人请记住：description 和正文都会进模型上下文。

## 未实施方向

技能市场/安装器、技能级工具白名单、触发命令和版本管理尚未实施；三层正文加载的具体缺口见上节。历史选择见 [ADR 0010](../decisions/0010-skills.md)。
