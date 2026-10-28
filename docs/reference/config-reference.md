# Tlon 配置参考

运行配置与普通笔记职责分开：**每 agent 的行为**在 `~/.tlon/{agent_id}/config/`（`agent.yaml` + 人格 `.md` + `behavior.tlon`），**进程级基础设施与密钥**走环境变量（[base/settings.py](../../src/tlon/base/settings.py) 的 `Settings`）；长期笔记是 workspace 中的普通文件，没有专用配置。本文只描述当前实现，配置项以代码中的字段/默认值为准。

## 1. agent.yaml（每 agent 配置）

位置：`~/.tlon/{agent_id}/config/agent.yaml`（路径由 [base/paths.py](../../src/tlon/base/paths.py) 的 `agent_config_file` 给出）。YAML 顶层是一个 `agent:` 键，其值对 [base/config.py](../../src/tlon/base/config.py) 的 `AgentConfig`（Pydantic v2 `BaseModel`）`model_validate`。未知字段被忽略（旧字段如 `proactive`、`memory`、ADR 0028 退役的 `message_queue_size` / `max_concurrent_sessions` 不生效、可清理；没有自动记忆服务）。

### 顶层字段

<!-- BEGIN GENERATED: agent-yaml-fields-agent -->
`AgentConfig` 字段（11 个，按源码声明顺序）：（本区块由 `scripts/gen_reference_docs.py` 生成，勿手改；改了会被 `--check` 打回）

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `id` | `str` | —（必填） |  |
| `name` | `str` | —（必填） |  |
| `llm` | `LLMConfig \| None` | None |  |
| `program_token_budget` | `int` | 100000 |  |
| `docker` | `DockerRuntimeConfig` | DockerRuntimeConfig() |  |
| `tool` | `ToolConfig` | ToolConfig() |  |
| `idle_timeout_seconds` | `int` | 600 |  |
| `prototype` | `str \| None` | None |  |
| `spawned_by` | `str \| None` | None |  |
| `spawn_depth` | `int` | 0 |  |
| `mail_rescan_interval_seconds` | `float` | 900.0 |  |
<!-- END GENERATED: agent-yaml-fields-agent -->

生成表只搬字段形状，语义补充（手写）：

- `id` / `name` 必填；`id` 只允许字母/数字/`-`/`_`（校验器强制），agent 目录名即此 id，`name` 是显示名（他人 `@` 此名可与之对话）。
- `program_token_budget`：单次工作程序入口的共享输出预算，正整数，默认 100000；规划、行动、自评与压缩共享。请求前原子预留，成功后按可靠 completion_tokens 结算并释放差额；缺少可靠用量则保留预留。恢复使用原冻结预算。
- `prototype` / `spawned_by` / `spawn_depth`：血缘审计（统一成员模型，ADR 0006）——spawn 出本成员的 agent id 与 spawn 链深度，非 spawn 为 `null` / `0`。
- `idle_timeout_seconds`：lazy 成员空闲多少秒后睡（scale-to-zero；成员永不删除，档案归档）。
- `mail_rescan_interval_seconds`：慢速重扫未读邮件的间隔（ADR 0028 决策 4）——agent 搁置未处理的未读与被崩溃吞掉的投递由这个重扫兜底唤醒。

### `llm`

<!-- BEGIN GENERATED: agent-yaml-fields-llm -->
`LLMConfig` 字段（8 个，按源码声明顺序）：（本区块由 `scripts/gen_reference_docs.py` 生成，勿手改；改了会被 `--check` 打回）

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `model_name` | `str \| None` | None |  |
| `max_output_tokens` | `int \| None` | None | 运行时字段，不进 YAML |
| `top_p` | `float` | 0.9 |  |
| `context_window` | `int \| None` | None |  |
| `api_base` | `str \| None` | None |  |
| `max_retries` | `int` | 3 | 运行时字段，不进 YAML |
| `base_delay` | `float` | 1.0 | 运行时字段，不进 YAML |
| `request_timeout` | `float` | 300.0 | 运行时字段，不进 YAML |
<!-- END GENERATED: agent-yaml-fields-llm -->

首装 30 位预设成员使用 `deepseek/deepseek-flash`，明确写入各自配置；后续可在 WebUI 调整，不是进程级兜底。

`model_name` 是**必填**的成员级声明（ADR 0016——没有进程级兜底）：`provider/model` 形式（如 `deepseek/deepseek-chat`），创建成员时经 WebUI / admin API 的 `model` 字段落盘，未声明的成员装配期硬报错；API key 不在此配置——litellm 原生读取 `{PROVIDER}_API_KEY` 环境变量（见第 4 节）。采样参数不配置——请求从不携带 temperature，供应商默认生效。`top_p` 每 agent 可调。单次输出默认按 LiteLLM 元数据中的模型输出能力、剩余上下文和 run 剩余预算取小值（含推理消耗）；不再配置 `llm.max_tokens`，程序显式传入 `max_tokens` 只能进一步收紧。模型输出能力是冻结的运行时字段，不写入成员 YAML；无法解析时明确报错。`api_base`（默认 `null`）是成员级的端点覆盖：私有部署 / 代理 / OpenAI 兼容端点配 `model_name: openai/<model>` + `api_base: <url>`，`null` 时走 litellm 注册表路由。`context_window` 为 `null`（默认）时启动组装自动从 litellm 模型注册表解析（ADR 0012 阶段 2），显式值作为注册表缺失/滞后时的覆盖。运行时字段（`max_retries`、`base_delay`、`request_timeout`）只有代码默认值（不再有环境变量来源），Pydantic 标注 `exclude=True`，不落盘。

### `docker`

<!-- BEGIN GENERATED: agent-yaml-fields-docker -->
`DockerRuntimeConfig` 字段（6 个，按源码声明顺序）：（本区块由 `scripts/gen_reference_docs.py` 生成，勿手改；改了会被 `--check` 打回）

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `level` | `str` | "L1" |  |
| `image` | `str` | "ubuntu:22.04" |  |
| `working_dir` | `str` | "/workspace" |  |
| `memory_limit` | `str` | "512m" |  |
| `cpu_limit` | `float` | 1.0 |  |
| `network_mode` | `str` | "none" |  |
<!-- END GENERATED: agent-yaml-fields-docker -->

`level`（issue #26，L1 落地于 #40）是执行装置能力阶梯声明：`"L0"` = 无执行装置——不创建 DockerRuntime、不注册 Shell，但五个文件工具可读写自己的 workspace 和读取只读作者资料；`"L1"`（默认，issue #31 于 2026-10-05 拍板翻转）= 文件层——archive-only，workspace 外文件访问可使用文件柜容器，但结构性零代码执行（容器从不启动、exec 单元从不存在，run() 由装置层直接拒绝）；`"L2"` = Docker 完整 userland。声明即数据、随原型提案一起评审。`docker.level` 也可由原型 spec 的顶层 `:execution` 段（见 [dsl-reference.md](./dsl-reference.md) 1.1 节）在实例化时一次性投影而来——此后运行时权威是本字段，成员自改 behavior.tlon 的 `:execution` 不会重新投影（防自提权）。

### `tool`

<!-- BEGIN GENERATED: agent-yaml-fields-tool -->
`ToolConfig` 字段（2 个，按源码声明顺序）：（本区块由 `scripts/gen_reference_docs.py` 生成，勿手改；改了会被 `--check` 打回）

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `per_timeout` | `float` | 30.0 |  |
| `allowed_paths` | `list[str]` | ['/workspace'] |  |
<!-- END GENERATED: agent-yaml-fields-tool -->

`per_timeout` 是成员宿主操作的共享默认超时（DSL 与 Tool 相同），见 [OperationExecutor](../../src/tlon/operations/core.py)；操作默认值、已声明的显式 timeout 和根期限可以覆盖或收紧它；`allowed_paths` 是 workspace 之外装置访问的**容器内绝对路径**白名单（写/edit 仅限该白名单）；自己的 workspace 由宿主独立绑定，不依赖此白名单或 Docker，非空、必须 `/` 开头（校验器强制）。file-read/file-list/file-search 在此之上额外放行 `/tlon-home`（agent 自己家目录的只读挂载，见 tools-reference.md filesystem 一节），该路径不进 `allowed_paths` 配置。

### 完整示例

```yaml
agent:
  id: su-wanqing
  name: 苏晚晴
  llm:
    model_name: deepseek/deepseek-chat   # provider/model 形式（必填，ADR 0016）
  docker:
    level: L2   # 执行装置层级（issue #26/#40）：L0 = 无执行装置；L1 = 文件层（仅文件工具，零代码执行）；默认 L1（issue #31），本例显式声明 L2
    image: ubuntu:22.04
    working_dir: /workspace
    memory_limit: 512m
    cpu_limit: 1.0
    network_mode: none   # 默认断网（issue #23）；确需外联改为 "bridge" 等显式声明
  tool:
    per_timeout: 30.0
    allowed_paths:
      - /workspace
```

## 2. 人格文件与行为规约

`~/.tlon/{agent_id}/config/` 目录下除 `agent.yaml` 外还有四个文件（路径见 [base/paths.py](../../src/tlon/base/paths.py) 的 `agent_identity_file` 等）：

| 文件 | 内容 |
|---|---|
| `IDENTITY.md` | 自我定义：名字、自称、身份叙述、当前自认的角色（成员可自改） |
| `SOUL.md` | 价值观、性格、语气、表达与判断倾向（成员可自改） |
| `RULES.md` | 必须遵循的工作规则、流程与明确约束（人类维护；实际权限由代码执行） |
| `behavior.tlon` | **成员的全部行为规约**——EDN rules-form，语法见 `reference/dsl-reference.md`；spec 是 total 的，规则统一通过 actions 组合会话与普通计算；模型、人格和装置配置仍有各自来源 |

## 3. 长期笔记

成员可按需使用普通文件工具读写自己的 `workspace/memory/`，不要求建立目录或索引，也不要求每个 run 读写。笔记不自动进入提示词，没有蒸馏、反思、embedding 或冲突裁决配置。旧 `memory/` 的 claims 和维护 checkpoint 不再读取、迁移或自动删除，详见 [记忆设计](../design/memory.md)。

## 4. 环境变量

读取方是 [base/settings.py](../../src/tlon/base/settings.py) 的 `Settings`（只处理进程级基建与密钥；业务配置在 `AgentConfig`）与 [base/tracing/otel.py](../../src/tlon/base/tracing/otel.py)。`.env` 在 **`{TLON_HOME}/.env`**（默认 `~/.tlon/.env`，配置与数据同目录）加载，且不覆盖已设置的真实环境变量（`load_process_env`，`override=False`）；`TLON_HOME` 本身只认真实环境变量，不来自 `.env`。

<!-- BEGIN GENERATED: env-vars -->
`Settings` 读取的环境变量（12 个，按源码顺序）：（本区块由 `scripts/gen_reference_docs.py` 生成，勿手改；改了会被 `--check` 打回）

| 变量 | 默认 | 说明 |
|---|---|---|
| `APP_NAME` | Tlon |  |
| `LOG_LEVEL` | INFO |  |
| `DEBUG` | false |  |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | （未设置） | Observability (OTel): optional OTLP collector, requires the `otlp` extra |
| `TELEGRAM_BOT_TOKEN` | "" | Telegram bot client (optional `telegram` extra)：@BotFather 颁发的 bot token；缺失则 `tlon telegram` 拒绝启动 |
| `TELEGRAM_ALLOWED_USERS` | "" | Telegram user-id 白名单（逗号分隔）；为空则拒绝启动 |
| `TLON_LOG_RETENTION_DAYS` | 7 | Logging: retention (days) for the daily server log file sink |
| `TLON_CONTAINER_MEMORY_BUDGET` | "" | Society-level container resource budget (issue #24): total memory budget across all agent containers (e.g. "4g"); empty = no budget. Warning-only — exceeding it logs container_memory_budget_exceeded, nothing is enforced. |
| `TLON_ARENA_MODEL` | "" | 演武场（docs/design/arena.md 「成本界」）：run 请求未带 model 时的缺省； 空 = 未配置，run 必须显式指定模型（显式优于静默错默认） |
| `GITHUB_WEBHOOK_SECRET` | "" | GitHub webhook ingress (see docs/design/mail.md 「外部来信与信任面」): HMAC secret of the webhook; empty = POST /webhooks/github is not registered at all |
| `GITHUB_RECIPIENT` | "" | GitHub 来信的默认收件人 member id；空 = 发给全体已知人类 |
| `GITHUB_TOKEN` | "" | GitHub API token（出站：回信 → issue/PR comment）； 空 = 出站禁用，入站不受影响 |
<!-- END GENERATED: env-vars -->

上表是 `Settings` 的全部读取点；另有三个环境变量由其他模块读取（手写维护）：

| 变量 | 默认 | 说明 |
|---|---|---|
| `TLON_HOME` | `~/.tlon` | 数据根目录；显式设置但指向不存在/非目录时**报错拒绝启动**（fail-fast，绝不静默回落真实数据根）；缺省 `~/.tlon` 不存在时自动创建（见 [base/paths.py](../../src/tlon/base/paths.py) 的 `_resolve_tlon_home`） |
| `TLON_TRACE_SAMPLE_RATIO` | 恒采样 | 采样率 `[0, 1]`；非法值拒绝（[base/tracing/otel.py](../../src/tlon/base/tracing/otel.py) 的 `SAMPLE_RATIO_ENV`） |
| `TLON_USERNAME` | `guest` | `tlon ops` 的默认邮件身份用户名 |

LLM 的 API key 不走 `Settings`（ADR 0016——进程级 `LLM_*` 变量已全部删除）：成员模型为 `provider/model` 形式，litellm 原生按 provider 前缀读取 `{PROVIDER}_API_KEY`（provider 段大写、`-` 换 `_`，如 `DEEPSEEK_API_KEY` / `ANTHROPIC_API_KEY` / `MOONSHOT_API_KEY` / `OPENAI_API_KEY`）；发请求前 LLMClient 有 preflight，缺 key 即报指明变量名的错。模型选择是成员级配置（agent.yaml `llm.model_name`），私有部署 / 代理用成员级 `llm.api_base` 覆盖端点。`TLON_LOG_RETENTION_DAYS` 管每日服务端日志文件保留（`~/.tlon/shared/logs/server-{date}.jsonl`）；`DEBUG` 为 `"true"` 时生效。

可选键：`AZURE_OPENAI_*` 等当前代码不读取；`DATA_DIR` 是遗留项，数据目录一律 `$TLON_HOME` 即 `~/.tlon/`。

## 5. `~/.tlon` 目录布局

[base/paths.py](../../src/tlon/base/paths.py) 的 `TlonPaths` 是**唯一**的路径构造入口——无模块级单例，实例在进程入口构造一次后显式注入贯穿各层。`TlonPaths` 是**纯路径解析器**：所有访问器零副作用，目录创建在真实写入点（各写入方自建父目录）；唯一例外是进程引导的 `_resolve_tlon_home`（默认 `~/.tlon` 根）。布局（按用途列示，具体路径以 TlonPaths 为准）：

```
~/.tlon/
├── .env                              # 进程级环境配置（API key 等，可选；真实环境变量优先）
├── webui-identity.json                # 首次 WebUI 设置的实例 username（ADR 0033；非密码/凭据）
├── initial-society.json               # 初始社会地址计划、人格快照与完成/跳过状态（ADR 0036）
├── initial-society-lock.sqlite3       # 初始社会创建互斥锁，不存业务邮件
├── .initial-society-staging/          # 初始化暂存，完成后清理
├── cron.yaml                         # 声明式周期任务邮件（可选；启动时读一次，改动需重启）
├── shared/
│   ├── mail.sqlite3                  # 邮件、邮箱归属、已读状态、幂等与退休记录
│   ├── spawn-quota.json              # 每 spawner/调用根的 spawn 配额账本
│   ├── traces/
│   │   └── trace-{uuid}.jsonl        # 会话追踪
│   └── logs/
│       └── server-{date}.jsonl       # 每日服务端日志
├── prototypes/                       # 行为原型库（只读解析，不自动创建）
│   └── {prototype_name}/
│       ├── prototype.tlon             # 原型元数据（:name / :display-name / :description / :trusted）
│       └── behavior.tlon             # 行为规约（total spec；原型无人格，ADR 0006）
├── prototypes-staging/               # LLM 创作、待人类批准的原型（闸门）
├── {agent_id}/
│   ├── config/
│   │   ├── agent.yaml                # 本 agent 配置
│   │   ├── IDENTITY.md / SOUL.md / RULES.md   # 身份/人格及约束（IDENTITY/SOUL 可自主修改，RULES 不可自改，见 identity design）
│   │   └── behavior.tlon             # 行为规约（唯一权威，一切派生）
│   ├── program.sqlite3               # program run、模型及工具回执唯一权威
│   ├── tasks.jsonl                   # TaskManager 持久化
│   └── workspace/                  # 成员工作目录（各等级普通文件工具均可读写，无需 Docker）
│       ├── memory/                 # 可选普通笔记，不自动读取或写入
│       └── CONTACTS.md              # 私人联系簿，按需读取，缺失时可自行创建；ADR 0047
```

要点：

- `webui-identity.json` 由 `TlonPaths.webui_identity_file` 定位，内容为 `{"username":"alice"}`；首次设置后地址恒定为 `alice@tlon.internal`。跨浏览器和重启复用；只允许初始化一次，不提供改名功能。文件损坏启动报错，不回落 guest。WebUI 不再使用 admin token；本地接入边界见 [ADR 0033](../decisions/0033-local-webui-identity.md)。

- 人类 id `human:{username}` 在共享 SQLite 中拥有邮箱归属，首次 attach 或投信时注册。
- SQLite 已注册人类即 known；离线不影响本地入箱。旧 JSONL 邮箱仅保留为离线档案，不读取、不导入、不双写、不删除。
- `prototypes/` 与 `prototypes-staging/` 不存在即空（`exists()` 检查无副作用——整个 `TlonPaths` 都是纯解析）；`prototypes/` 是受信任源，`prototypes-staging/` 是信任闸（人工批准后才能被 spawn）。
- `cron.yaml` 是声明式周期任务（`{name, to, subject, content}` + `interval_seconds` 或 5 字段 `cron` 表达式），由 [agents/cron.py](../../src/tlon/agents/cron.py) 的 `CronScheduler` 在启动时读一次——改动需重启生效；不存在或为空即无周期任务，没有内置社会文摘。报社等业务由普通 Lisp 成员处理，调度器只投递静态任务。格式与示例见 [design/mail.md](../design/mail.md)「周期任务：tick 即邮件」。

## 6. 配置相关的常用操作

- **增/删 agent**：[base/config.py](../../src/tlon/base/config.py) 的 `yaml_save` / `yaml_delete`（删除即 `rm -rf ~/.tlon/{agent_id}/`）；热加载走 REST `POST /admin/config/agents/{agent_id}/reload`（条件注册，见 `reference/api-reference.md`）。
- **交互式管理**：ops 的 Agents 视图——创建成员（`c`，需选模型）/ 编辑人格与模型（`e`）/ 删除（`d`）/ 原型审批（`s`）；全新实例首启创建 30 位预设成员（[初始社会](../design/initial-society.md)，ADR 0036）；已有实例不补种。
- **agent 目录结构假设**：任何新路径需求必须加进 `TlonPaths`，不许在其他模块硬编码目录结构（[base/paths.py](../../src/tlon/base/paths.py) 模块 docstring 的硬性规定）。

模型请求和普通工具分别限时：model-call 使用模型 request_timeout（当前为运行时默认 300 秒），普通工具使用 tool.per_timeout（默认 30 秒），两者均受当前执行域更早的截止时间约束。嵌套模型调用不能扩大父工具期限。派发后未知请求不会自动重发，详见 [ADR 0050](../decisions/0050-model-operation-timeouts.md)。
