# ADR 0010：Skill 能力——开放标准 SKILL.md，三层目录，DSL `(skills)` section，模型自读正文

状态：已接受（2026-08-22，方向讨论拍板；同日实施）

## 背景

业界（Anthropic Agent Skills，2025-12 起为开放标准，Claude Code / Codex / Cursor 均已支持同一格式）的 skill 模式已被验证：一个文件夹 + 一个 `SKILL.md`（YAML frontmatter 的 `name`/`description` 是触发依据 + Markdown 正文指令，可带 `scripts/`、`references/`），靠 **progressive disclosure** 控制上下文开销——平时只有索引（name + description）进上下文，模型判断匹配后才读正文。

Tlon 已有"常驻人格"层（SOUL.md / IDENTITY.md / RULES.md 经 `(persona)` 进 system prompt），但没有"按需技能"层。原型体系（`behavior.tlon`）的 `:context` section 向量是现成的注入点；agent 已有 `read_file` / `execute_bash` 工具，skill 正文的读取与脚本执行可零新机制复用。

## 决策

引入 skill 能力，完整采用 Agent Skills 开放标准，不自创格式：

- **三层目录**，同名冲突时 **项目 > agent > 全局**（越具体越赢，与 Claude Code 一致）：
  - 项目级：`~/.tlon/{agent_id}/workspace/.agents/skills/`——即容器内 `/workspace/.agents/skills/`（workspace bind-mount 进容器，`docker_runtime.py`），**宿主机直读 bind-mount，不 exec 进容器扫描**。
  - agent 级：`~/.tlon/{agent_id}/skills/`——该 agent 的私人技能。
  - 全局级：`~/.tlon/skills/`——跨 agent 共享。
  - 三个路径解析器挂 `base/paths.py`，只读解析（不存在即空，不 mkdir）。
- **格式**：每个 skill 是 `<dir>/SKILL.md`；frontmatter `name`、`description` 必填，缺失或解析失败则跳过该 skill 并记 warning。扫描只认各 skills 目录的直接子目录，不递归。
- **注入**：DSL 新增 `(skills)` context section（无参数），与 `knowledge`/`mail-history` 平级，prototype 作者显式启用。注入的只是**索引**：每条一行 `name (scope): description — SKILL.md 宿主机绝对路径`，渲染为 `[可用技能]` 用户消息，按 section 向量顺序落位。
- **加载**：模型看到索引后用现有 `read_file` 自读 SKILL.md 正文，需要跑脚本用 `execute_bash`（容器内执行）。**不加 `use_skill` 工具，不做引擎侧正文注入**——最少机制，行为对齐业界已验证模式。
- **缓存**：索引按全部 SKILL.md 的 mtime 签名缓存（与 system prompt 的 persona mtime 缓存同手法），skill 文件一改下一 session 生效。
- **信任面**：项目级 skill 来自 workspace（可能是 agent 自己 clone 的仓库），SKILL.md 内容视为**不可信输入**，风险与 prompt injection 同级。第一版不做任何自动执行类能力，只有"索引 + 模型自读"。
- ~~标准行为模板（`behavior_templates.py`）与原型库全部 behavior.tlon 的 `:sections` 加入 `(skills)`，位置在 `(knowledge)` 之后、`(mail-history)` 之前。~~（**2026-09 修订**：实际落地为——`(skills)` 声明在 `(knowledge)` **之前**（全认知原型统一 `(persona) (protocol) (lessons) (skills) (knowledge) (current-task) (mail-history 20) (thread-recap)`）；并非「全部」原型——fast-responder（cost routing 刻意不带）与 relay（无 `:loop`）无 `(skills)`；`behavior_templates.py` 从未存在，写侧模板是另一物 `behavior_template.py`（单数，`${var}` 参数层，与 skill 无关）。另注意 ADR 0017 阶段 4 起 `(knowledge)` 的注入位置语义已变，见 ADR 0008 状态行。）

### 明确不做（第一版）

- skill 市场 / 安装器 / 依赖解析
- frontmatter `allowed-tools` 白名单（工具面不随 skill 收窄）
- 用户侧触发命令（`/skill-name` 之类）
- skill 的执行结果缓存、版本管理、签名验证

## 后果

- 第三方按开放标准写的 skill 包拷进任一层的 `skills/` 目录即可用，无需注册。
- `(skills)` 是 DSL 显式项：不加该 section 的成员（如确定性 script 成员）完全没有 skill 开销。
- 索引是固定上下文开销（每条 ~60 token），靠"只注入索引、正文按需自读"控制规模；索引缓存使无变更时零磁盘重读。
- 项目级 skill 带来提示注入面，文档（design/skills.md）写明信任前提；缓解手段（白名单、确认门）留给后续决策。
- 实现落点：`base/paths.py` 三个路径解析器、`agents/skills.py`（扫描 + frontmatter 解析 + 索引渲染 + mtime 缓存）、`member_spec.py` 的 `SECTION_NAMES` + 校验、`context_builder.py` 的 section 解释、~~`behavior_templates.py` 与~~原型库模板。

## 演进补注

- **2026-09（issue ）**：内置 skill 随包发布（`src/tlon/skills/`，如 explain-decision），启动时 `seed_skills` 幂等播种到全局层——三层目录之外多了一条「出厂自带」供给通道，优先级仍按全局层规则。详见 docs/design/skills.md。
