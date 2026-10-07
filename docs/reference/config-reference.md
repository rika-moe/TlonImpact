# Tlon 配置参考

三块配置，职责严格分开：**每 agent 的行为**在 `~/.tlon/{agent_id}/config/`（`agent.yaml` + 人格 `.md` + `behavior.tlon`），**进程级基础设施与密钥**走环境变量（[base/settings.py](../../src/tlon/base/settings.py) 的 `Settings`），**记忆调参**在 `~/.tlon/{agent_id}/memory/config.json`。本文只描述当前实现，配置项以代码中的字段/默认值为准。

## 1. agent.yaml（每 agent 配置）

位置：`~/.tlon/{agent_id}/config/agent.yaml`（路径由 [base/paths.py](../../src/tlon/base/paths.py) 的 `agent_config_file` 给出）。YAML 顶层是一个 `agent:` 键，其值对 [base/config.py](../../src/tlon/base/config.py) 的 `AgentConfig`（Pydantic v2 `BaseModel`）`model_validate`。未知字段被忽略（旧字段如 `proactive`、[memory](../../src/tlon/memory) 不生效、可清理——记忆调参唯一入口是 `memory/config.json`）。

### 顶层字段

<!-- BEGIN GENERATED: agent-yaml-fields-agent -->
`AgentConfig` 字段（13 个，按源码声明顺序）：（本区块由 `scripts/gen_reference_docs.py` 生成，勿手改；改了会被 `--check` 打回）

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `id` | `str` | —（必填） |  |
| `name` | `str` | —（必填） |  |
| `llm` | `LLMConfig \| None` | None |  |
| `docker` | `DockerRuntimeConfig` | DockerRuntimeConfig() |  |
| `tool` | `ToolConfig` | ToolConfig() |  |
| `idle_timeout_seconds` | `int` | 600 |  |
| `prototype` | `str \| None` | None |  |
| `spawned_by` | `str \| None` | None |  |
| `spawn_depth` | `int` | 0 |  |
| `session_timeout_seconds` | `int` | 1800 |  |
| `message_queue_size` | `int` | 100 |  |
| `max_concurrent_sessions` | `int` | 4 |  |
| `mail_rescan_interval_seconds` | `float` | 900.0 |  |
<!-- END GENERATED: agent-yaml-fields-agent -->

生成表只搬字段形状，语义补充（手写）：

- `id` / `name` 必填；`id` 只允许字母/数字/`-`/`_`（校验器强制），agent 目录名即此 id，`name` 是显示名（他人 `@` 此名可与之对话）。
- `prototype` / `spawned_by` / `spawn_depth`：血缘审计（统一成员模型，ADR 0006）——spawn 出本成员的 agent id 与 spawn 链深度，非 spawn 为 `null` / `0`。
- `idle_timeout_seconds`：lazy 成员空闲多少秒后睡（scale-to-zero；成员永不删除，档案归档）。
- `session_timeout_seconds`：ACTIVE/WAITING 的会话空闲超时即取消。
- `message_queue_size`：入站消息队列容量，溢出丢弃新消息。
- `mail_rescan_interval_seconds`：运行中周期重扫未读邮件的间隔；被满队列丢弃、保持未读的邮件由这个重扫拾起，不用等重启。

### `llm`

<!-- BEGIN GENERATED: agent-yaml-fields-llm -->
`LLMConfig` 字段（9 个，按源码声明顺序）：（本区块由 `scripts/gen_reference_docs.py` 生成，勿手改；改了会被 `--check` 打回）

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `model_name` | `str \| None` | None |  |
| `max_tokens` | `int` | 4096 |  |
| `top_p` | `float` | 0.9 |  |
| `context_window` | `int \| None` | None |  |
| `api_base` | `str \| None` | None |  |
| `embedding_model_name` | `str \| None` | None |  |
| `max_retries` | `int` | 3 | 运行时字段，不进 YAML |
| `base_delay` | `float` | 1.0 | 运行时字段，不进 YAML |
| `request_timeout` | `float` | 300.0 | 运行时字段，不进 YAML |
<!-- END GENERATED: agent-yaml-fields-llm -->

`model_name` 是**必填**的成员级声明（ADR 0016——没有进程级兜底）：`provider/model` 形式（如 `deepseek/deepseek-chat`），创建成员时经 WebUI / admin API 的 `model` 字段落盘，未声明的成员装配期硬报错；API key 不在此配置——litellm 原生读取 `{PROVIDER}_API_KEY` 环境变量（见第 4 节）。采样参数不配置——请求从不携带 temperature，供应商默认生效。`max_tokens` / `top_p` 每 agent 可调。`api_base`（默认 `null`）是成员级的端点覆盖：私有部署 / 代理 / OpenAI 兼容端点配 `model_name: openai/<model>` + `api_base: <url>`，`null` 时走 litellm 注册表路由。`context_window` 为 `null`（默认）时启动组装自动从 litellm 模型注册表解析（ADR 0012 阶段 2），显式值作为注册表缺失/滞后时的覆盖。`embedding_model_name`（默认 `null`，ADR 0017 阶段 2）是成员级 embedding 模型声明，同为 `provider/model` 形式、同走 `{PROVIDER}_API_KEY` 约定；配置后记忆检索启用 sqlite-vec 向量索引 + JIT 相关性注入，`null`（或 provider 无 embedding 端点）时检索降级为「最近 N 条」——降级是一等行为，不影响记忆与会话。运行时字段（`max_retries`、`base_delay`、`request_timeout`）只有代码默认值（不再有环境变量来源），Pydantic 标注 `exclude=True`，不落盘。

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

`level`（issue #26，L1 落地于 #40）是执行装置能力阶梯声明：`"L0"` = 无执行装置——装配时不创建 DockerRuntime，也不注册依赖装置的 shell/filesystem 工具（executeBashCommand/checkBashTask/readFile/writeFile/editFile/listDirectory/searchFiles）；`"L1"`（默认，issue #31 于 2026-10-05 拍板翻转）= 文件层——archive-only，创建文件柜容器并注册 5 个 filesystem 工具，但结构性零代码执行（容器从不启动、exec 单元从不存在，run() 由装置层直接拒绝）；`"L2"` = Docker 完整 userland。声明即数据、随原型提案一起评审。`docker.level` 也可由原型 spec 的顶层 `:execution` 段（见 [dsl-reference.md](./dsl-reference.md) 1.1 节）在实例化时一次性投影而来——此后运行时权威是本字段，成员自改 behavior.tlon 的 `:execution` 不会重新投影（防自提权）。

### `tool`

<!-- BEGIN GENERATED: agent-yaml-fields-tool -->
`ToolConfig` 字段（3 个，按源码声明顺序）：（本区块由 `scripts/gen_reference_docs.py` 生成，勿手改；改了会被 `--check` 打回）

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `total_timeout` | `float` | 60.0 |  |
| `per_timeout` | `float` | 30.0 |  |
| `allowed_paths` | `list[str]` | ['/workspace'] |  |
<!-- END GENERATED: agent-yaml-fields-tool -->

`per_timeout` 是单个工具超时，见 [agents/loop_driver.py](../../src/tlon/agents/loop_driver.py) 的 `execute_one_tool`；`allowed_paths` 是文件工具只允许触碰的**容器内绝对路径**（writeFile/editFile 的完整白名单），非空、必须 `/` 开头（校验器强制）。readFile/listDirectory/searchFiles 在此之上额外放行 `/tlon-home`（agent 自己家目录的只读挂载，见 tools-reference.md filesystem 一节），该路径不进 `allowed_paths` 配置。

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
    total_timeout: 60.0
    per_timeout: 30.0
    allowed_paths:
      - /workspace
  session_timeout_seconds: 1800
  message_queue_size: 100
```

## 2. 人格文件与行为规约

`~/.tlon/{agent_id}/config/` 目录下除 `agent.yaml` 外还有四个文件（路径见 [base/paths.py](../../src/tlon/base/paths.py) 的 `agent_identity_file` 等）：

| 文件 | 内容 |
|---|---|
| `IDENTITY.md` | 叙事身份：我是谁 |
| `SOUL.md` | 性格/价值观/语气 |
| `RULES.md` | 硬性行为规则 |
| `behavior.tlon` | **成员的全部行为规约**——EDN rules-form，语法见 `reference/dsl-reference.md`；spec 是 total 的，规则带不带 `:loop` 由此文件决定，`agent.yaml` 不再控制 |

## 3. 记忆配置 memory/config.json

`~/.tlon/{agent_id}/memory/config.json`（[memory/models.py](../../src/tlon/memory/models.py) 的 `MemoryConfig`，读写由 [memory/manager.py](../../src/tlon/memory/manager.py) 负责）。完整结构：

```json
{
  "version": "1.0",
  "agent_id": "su-wanqing",
  "distillation": { "window": 3 },
  "context": {
    "max_claims_in_context": 10
  },
  "reflection": { "min_interactions": 5 },
  "retrieval": {
    "alpha_similarity": 1.0,
    "beta_recency": 0.3,
    "gamma_importance": 0.5,
    "recency_half_life_days": 7.0,
    "pool_factor": 4
  },
  "conflict": {
    "similarity_threshold": 0.75,
    "candidate_limit": 3
  }
}
```

各段与 `MemoryConfig` 字段对应：`distillation.window`（批量蒸馏窗口——已完成且开始时间晚于蒸馏水位线（`memory/distillation.json`）的 session 数达到该值即触发批量蒸馏，ADR 0017）、`context.max_claims_in_context`（注入上下文的 claim 条数）、`reflection.min_interactions`（自驱动记忆审计的最小完成会话数；开/关与间隔在 `behavior.tlon` 的 `:reflect` 声明）。`retrieval` 段是 JIT 检索调参（ADR 0017 阶段 2）：`alpha_similarity`/`beta_recency`/`gamma_importance` 是复合打分 `α·similarity + β·recency + γ·importance` 的三个权重，`recency_half_life_days` 是 recency 指数衰减的半衰期（天），`pool_factor` 是 KNN 候选池相对 k 的放大倍数。`conflict` 段是冲突判定调参（ADR 0017 阶段 3）：`similarity_threshold` 是触发 LLM 判定的相似度门限（余弦，定高控制成本），`candidate_limit` 是每条新 claim 拉取的候选旧 claim 数。ADR 0017 之前的 `summary`/`episodic`/旧 `context` 段与 ADR 0009 之前的 `working`/`triggers` 段读取时自然忽略，无需清理。

> **生效路径**：`MemoryManager.load`（成员启动时调用）在 `config.json` 存在时以磁盘文件为准加载并绑定 `agent_id`——生产装配（[agents/assembly.py](../../src/tlon/agents/assembly.py) 的 `assemble_cognitive`）传入默认 `MemoryConfig()`，不预填 `agent_id`，因此**磁盘调参在生产环境直接生效**。`agent.yaml` 无 `memory:` 段（[base/config.py](../../src/tlon/base/config.py) 的旧 `MemoryConfig` 已删除，其字段从未被消费）。

## 4. 环境变量

读取方是 [base/settings.py](../../src/tlon/base/settings.py) 的 `Settings`（只处理进程级基建与密钥；业务配置在 `AgentConfig`）与 [base/tracing/otel.py](../../src/tlon/base/tracing/otel.py)。`.env` 在 **`{TLON_HOME}/.env`**（默认 `~/.tlon/.env`，配置与数据同目录）加载，且不覆盖已设置的真实环境变量（`_load_dotenv_files`，`override=False`）；`TLON_HOME` 本身只认真实环境变量，不来自 `.env`。

<!-- BEGIN GENERATED: env-vars -->
`Settings` 读取的环境变量（13 个，按源码顺序）：（本区块由 `scripts/gen_reference_docs.py` 生成，勿手改；改了会被 `--check` 打回）

| 变量 | 默认 | 说明 |
|---|---|---|
| `APP_NAME` | Tlon | App settings |
| `LOG_LEVEL` | INFO |  |
| `DEBUG` | false |  |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | （未设置） | Observability (OTel): optional OTLP collector, requires the `otlp` extra |
| `TELEGRAM_BOT_TOKEN` | "" | Telegram bot client (optional `telegram` extra) |
| `TELEGRAM_ALLOWED_USERS` | "" |  |
| `TLON_LOG_RETENTION_DAYS` | 7 | Logging: retention (days) for the daily server log file sink |
| `TLON_CONTAINER_MEMORY_BUDGET` | "" | Society-level container resource budget (issue #24): total memory budget across all agent containers (e.g. "4g"); empty = no budget. Warning-only — exceeding it logs container_memory_budget_exceeded, nothing is enforced. |
| `TLON_ARENA_MODEL` | "" | 演武场（docs/design/arena.md 「成本界」）：run 请求未带 model 时的缺省； 空 = 未配置，run 必须显式指定模型（显式优于静默错默认） |
| `TLON_ADMIN_TOKEN` | "" | Admin API auth (ADR 0013): shared Bearer token guarding /admin/*; empty = auth disabled (default, localhost trust boundary) |
| `GITHUB_WEBHOOK_SECRET` | "" | GitHub webhook ingress (see docs/design/mail.md 「外部来信与信任面」): HMAC secret of the webhook; empty = POST /webhooks/github is not registered at all |
| `GITHUB_RECIPIENT` | "" |  |
| `GITHUB_TOKEN` | "" |  |
<!-- END GENERATED: env-vars -->

上表是 `Settings` 的全部读取点；另有三个环境变量由其他模块读取（手写维护）：

| 变量 | 默认 | 说明 |
|---|---|---|
| `TLON_HOME` | `~/.tlon` | 数据根目录；只认已存在且是目录的值，否则回落 `~/.tlon`（见 [base/paths.py](../../src/tlon/base/paths.py) 的 `_resolve_tlon_home`） |
| `TLON_TRACE_SAMPLE_RATIO` | 恒采样 | 采样率 `[0, 1]`；非法值拒绝（[base/tracing/otel.py](../../src/tlon/base/tracing/otel.py) 的 `SAMPLE_RATIO_ENV`） |
| `TLON_USERNAME` | `guest` | `tlon ops` 的默认邮件身份用户名 |

LLM 的 API key 不走 `Settings`（ADR 0016——进程级 `LLM_*` 变量已全部删除）：成员模型为 `provider/model` 形式，litellm 原生按 provider 前缀读取 `{PROVIDER}_API_KEY`（provider 段大写、`-` 换 `_`，如 `DEEPSEEK_API_KEY` / `ANTHROPIC_API_KEY` / `MOONSHOT_API_KEY` / `OPENAI_API_KEY`）；发请求前 LLMClient 有 preflight，缺 key 即报指明变量名的错。模型选择是成员级配置（agent.yaml `llm.model_name`），私有部署 / 代理用成员级 `llm.api_base` 覆盖端点。`TLON_LOG_RETENTION_DAYS` 管每日服务端日志文件保留（`~/.tlon/shared/logs/server-{date}.jsonl`）；`DEBUG` 为 `"true"` 时生效。

可选键：`AZURE_OPENAI_*` 等当前代码不读取；`DATA_DIR` 是遗留项，数据目录一律 `$TLON_HOME` 即 `~/.tlon/`。

## 5. `~/.tlon` 目录布局

[base/paths.py](../../src/tlon/base/paths.py) 的 `TlonPaths` 是**唯一**的路径构造入口——无模块级单例，实例在进程入口构造一次后显式注入贯穿各层。`TlonPaths` 是**纯路径解析器**：所有访问器零副作用，目录创建在真实写入点（各写入方自建父目录）；唯一例外是进程引导的 `_resolve_tlon_home`（默认 `~/.tlon` 根）。布局（docstring 原样）：

```
~/.tlon/
├── .env                              # 进程级环境配置（API key 等，可选；真实环境变量优先）
├── cron.yaml                         # 声明式周期任务邮件（可选；启动时读一次，改动需重启）
├── policy.tlon                       # 社会政策文件（可选；ADR 0022，tilt/solvent?/full-member? hook；缺失即内嵌默认政策）
├── shared/
│   ├── traces/
│   │   └── trace-{uuid}.jsonl        # 会话追踪
│   └── logs/
│       └── server-{date}.jsonl       # 每日服务端日志
├── prototypes/                       # 行为原型库（只读解析，不自动创建）
│   └── {prototype_name}/
│       ├── prototype.tlon             # 原型元数据（:name / :display-name / :description / :trusted）
│       └── behavior.tlon             # 行为规约（total spec；原型无人格，ADR 0006）
├── prototypes-staging/               # LLM 创作、待人类批准的原型（闸门）
├── capabilities/                     # 受信能力库（能力数据化，design/capabilities.md；一词一目录）
│   └── {capability_name}/
│       ├── capability.tlon           # artifact 本体（{:version 1 :name :args :impl (fn ...) :pure :examples?}）
│       └── meta.tlon            # 元数据（:name / :trusted / :author / :proposal-note / :deprecated）
├── capabilities-staging/             # LLM 创作、待人类批准的能力（与原型同一信任闸模式）
├── {agent_id}/
│   ├── config/
│   │   ├── agent.yaml                # 本 agent 配置
│   │   ├── IDENTITY.md / SOUL.md / RULES.md   # 人格（创建时播种，之后成员自主数据 ADR 0024，非基因型）
│   │   └── behavior.tlon             # 行为规约（唯一权威，一切派生）
│   ├── memory/
│   │   ├── config.json               # 记忆调参
│   │   ├── claims.jsonl              # 记忆 claim（ADR 0017，append-only）
│   │   └── distillation.json         # 蒸馏水位线（last_distilled_at）
│   ├── sessions/                     # session 事件日志（ADR 0007/0009：交互唯一真相）
│   │   └── {session_id}.jsonl
│   ├── tasks.jsonl                   # TaskManager 持久化
│   ├── workspace/                    # 沙箱工作目录
│   └── mail/
│       ├── inbox.jsonl
│       ├── sent.jsonl
│       └── inbox.cursor
└── human-{username}/                 # 人类成员（human:{username} 映射到 human-{username}）
    └── mail/                         # 离线人类收信等待投递
```

要点：

- 人类 id `human:{username}` 的邮箱映射到 `human-{username}/mail/`——冒号只在 `member_mail_dir` 里换连字符。
- 人类一旦有邮箱目录（首次 attach 会话、首次投递或首次读 `/inbox`）即"known"；离线人类收到的信在 `inbox.jsonl` 等待。
- `prototypes/` 与 `prototypes-staging/` 不存在即空（`exists()` 检查无副作用——整个 `TlonPaths` 都是纯解析）；`prototypes/` 是受信任源，`prototypes-staging/` 是信任闸（人工批准后才能被 spawn）。`capabilities/` 与 `capabilities-staging/` 同构（[design/capabilities.md](../design/capabilities.md)）：受信是默认（人类手写进受信库即可信），staging 写入显式 `:trusted false`，`approve_capability` 整目录移动并翻转信任位。
- `cron.yaml` 是声明式周期任务（`{name, to, subject, content}` + `interval_seconds` 或 5 字段 `cron` 表达式），由 [agents/cron.py](../../src/tlon/agents/cron.py) 的 `CronScheduler` 在启动时读一次——改动需重启生效；不存在即无声明条目。格式与示例见 [design/mail.md](../design/mail.md)「周期任务：tick 即邮件」。
- `policy.tlon` 是社会政策文件（ADR 0022）：`:policy` form 序列定义的 `tilt` / `solvent?` / `full-member?` hook 在 spawn / 提案 / 装配求值点被宿主调用，宿主每次求值重新读文件——改动即时生效；**人类专写**（不进任何成员的 `allowed_paths`，不走 staging 通道）；缺失或失败即回退 [agents/policy.py](../../src/tlon/agents/policy.py) 的内嵌默认政策（精确复刻迁移前的 Python 公式）。格式与词汇见 [dsl-reference.md](dsl-reference.md) §10。

## 6. 配置相关的常用操作

- **增/删 agent**：[base/config.py](../../src/tlon/base/config.py) 的 `yaml_save` / `yaml_delete`（删除即 `rm -rf ~/.tlon/{agent_id}/`）；热加载走 REST `POST /admin/config/agents/{agent_id}/reload`（条件注册，见 `reference/api-reference.md`）。
- **交互式管理**：ops 的 Agents 视图——创建成员（`c`，需选模型）/ 编辑人格与模型（`e`）/ 删除（`d`）/ 原型审批（`s`）；首启只播种原型库与技能（[agents/bootstrap.py](../../src/tlon/agents/bootstrap.py)），空社会不再自动初始化成员（ADR 0016）。
- **agent 目录结构假设**：任何新路径需求必须加进 `TlonPaths`，不许在其他模块硬编码目录结构（[base/paths.py](../../src/tlon/base/paths.py) 模块 docstring 的硬性规定）。