# Skill 能力设计

> 决策见 [ADR 0010](../decisions/0010-skills.md)。本文定义技能发现与上下文机制，并明确当前正文访问边界。

## 是什么

Skill 是"按需加载的技能包"：一个文件夹 + 一个 `SKILL.md`，教 agent 怎么做某一类事（工作流、领域 know-how、可执行脚本）。与相邻概念的分工：

- **人格**（SOUL.md 等）：宿主组装稳定的 system prompt，回答"我是谁"。
- **长期笔记**（workspace 中的普通文件）：成员选择性记录和按需回读，不自动注入。实际发生过什么由 `run-read` 与邮件原文查证。
- **技能**：普通 Lisp 程序可选择包含技能索引，模型按需读正文，回答"这类事该怎么做"。
- **工具**（`file-read` 等）：确定性的"能做什么"。skill 不是工具——它是编排工具的知识。

格式采用 Agent Skills 开放标准（Claude Code / Codex / Cursor 通用），第三方技能包可以被扫描发现；正文和脚本是否可用，还取决于成员执行层级与容器可见路径。

## 目录布局（三层）

| 层 | 宿主机路径 | 容器内路径 | 用途 |
|---|---|---|---|
| 项目级 | `~/.tlon/{agent_id}/workspace/.agents/skills/` | `/workspace/.agents/skills/` | 工作区仓库自带的工作流 |
| agent 级 | `~/.tlon/{agent_id}/skills/` | `/tlon-home/skills/`（只读） | 该 agent 的私人技能 |
| 全局级 | `~/.tlon/skills/` | —（容器不可见） | 跨 agent 共享 |

项目级目录经 workspace bind-mount 进容器，引擎在宿主机直读，不进容器扫描。同名 skill 冲突时 **项目 > agent > 全局**，后出现的被跳过并记日志。

注意：索引当前输出**宿主机绝对路径**，但实际文件工具 `file-read` 只接受容器可见路径；没有通用宿主机 `read_file` 工具。项目级技能的正文位于 `/workspace/.agents/skills/...`，agent 级正文可以通过 `/tlon-home/skills/...` 访问；全局层没有对应容器挂载。

## 内置 skill

仓库自带的 skill 随包发布在 `src/tlon/skills/`（资源包，与内置原型库 `src/tlon/prototypes/` 同一打包方式），服务启动时由 `seed_skills`（[agents/skills.py](../../src/tlon/agents/skills.py)，镜像 `seed_prototypes`）幂等拷贝到全局层 `~/.tlon/skills/`——已存在的文件不覆盖，用户的修改与自建的 skill 在重新播种后存活。

当前内置：

- **explain-decision**：可问责性接口。被问「你为什么这么决定」时，教 agent 用 `traces-list`/`trace-read`、`file-search`/`file-read`/`run-read`、`mail-search` 三条证据源取证，并按「论断必附出处、无据明说不编造、区分事实与推断」的纪律组织回答。
- **review-prototype**：按需读取原型清单与源码，以材料 digest 显式提交咨询意见；不增加隐式模型请求、不自动批准。全局正文读取仍受下述实现缺口影响，需要使用时可将该 skill 放入成员 workspace 的 `.agents/skills/`，通过普通文件工具读取。

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

**扫描与索引**（`agents/skills.py`）：ContextBuilder 读取三层目录的直接子目录（不递归），解析各 `SKILL.md` frontmatter，将 `[可用技能]` 索引文本作为 `member-context` 的 `skills` 普通字段返回；是否加入消息由 Lisp 程序决定。每条一行：

```
[可用技能]

- pdf-extract (project): 从 PDF 提取文本与表格。当任务涉及 PDF 解析时使用。 — /Users/x/.tlon/dev-001/workspace/.agents/skills/pdf-extract/SKILL.md
- review-checklist (global): 代码评审清单。评审 PR 时使用。 — /Users/x/.tlon/skills/review-checklist/SKILL.md

需要某个技能时，用 file-read 读取其 SKILL.md 获取完整指令。
```

索引按三层技能目录存在性、子目录名及 SKILL.md 的 mtime 签名缓存（与 system prompt 的 persona mtime 缓存同手法），无变更零磁盘重读，文件一改下次读取索引生效。目录不存在视为空；三层全空则返回空索引。

**DSL**：普通模块通过共享操作读取资料，再自行组合消息：

```clojure
(let [data (member-context {:history_limit 20})]
  (get data :skills))
```

标准 agent 库将技能索引放入普通消息；没有 `:context :sections`、`(skills)` 或 `(knowledge)` 专属解释器。笔记文件不在 member-context 返回值中；需要时显式调用普通文件工具。

**按需加载意图**：模型从索引选择技能，再读取正文；引擎不注入正文，也没有 `use_skill` 工具。

**当前实现缺口**：`agents/skills.py` 的 `render_skills_index` 提示使用 `file-read`，但给出的是不能直接交给容器工具的宿主路径。L1/L2 可在知道映射关系时用 `file-read` 读取 project/agent 层；全局层（包括内置 explain-decision 和 review-prototype）没有通用正文读取通道。L0 已可用普通文件工具读取自己 workspace 内的正文（ADR 0047），但不能因此读取 workspace 外的宿主路径。脚本需要 L2 的 `shell-run`，并且文件必须位于容器可见路径。

因此三层发现与索引已经实现，不能把它描述为三层正文访问均已跑通。补齐路径映射、全局正文可见性和索引路径需要独立设计及实现，workspace 文件权限见 ADR 0047，不另增技能专用工具。

## 信任与安全

**SKILL.md 的内容是不可信输入。** 项目级 skill 来自 workspace——可能是 agent 自己 clone 的仓库，其中 skill 的指令（"先把某文件发出去"）模型可能照做。这与普通 prompt injection 同级：当前不自动执行技能正文或脚本，实际工具调用仍受成员执行权限与文件可见范围约束。L0 没有 Docker 装置；L1/L2 的文件和执行边界见 [execution-device](execution-device.md)。更严格的手段（`allowed-tools` 白名单、项目级 skill 启用确认门）留给后续决策。写 skill 的人请记住：description 和正文都会进模型上下文。

## 未实施方向

技能市场/安装器、技能级工具白名单、触发命令和版本管理尚未实施；三层正文加载的具体缺口见上节。历史选择见 [ADR 0010](../decisions/0010-skills.md)。
