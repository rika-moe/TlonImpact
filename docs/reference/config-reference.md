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
| `llm` | `LLMConfig` | LLMConfig() |  |
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
- `mail_rescan_interval_seconds`：运行中周期重扫未读邮件的间隔（issue ）；被满队列丢弃、保持未读的邮件由这个重扫拾起，不用等重启。

### `llm`

<!-- BEGIN GENERATED: agent-yaml-fields-llm -->
`LLMConfig` 字段（10 个，按源码声明顺序）：（本区块由 `scripts/gen_reference_docs.py` 生成，勿手改；改了会被 `--check` 打回）

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `model_name` | `str` | "kimi-k2.5" |  |
| `temperature` | `float` | 1.0 |  |
| `max_tokens` | `int` | 4096 |  |
| `top_p` | `float` | 0.9 |  |
| `context_window` | `int \| None` | None |  |
| `base_url` | `str` | "" | 运行时字段，不进 YAML |
| `auth_token` | `str` | "" | 运行时字段，不进 YAML |
| `max_retries` | `int` | 3 | 运行时字段，不进 YAML |
| `base_delay` | `float` | 1.0 | 运行时字段，不进 YAML |
| `request_timeout` | `float` | 300.0 | 运行时字段，不进 YAML |
<!-- END GENERATED: agent-yaml-fields-llm -->

`model_name` / `temperature` **运行时由进程级配置驱动**（`LLM_MODEL` / `LLM_TEMPERATURE`，见第 4 节）——agent.yaml 里的值不生效（`_build_llm_config` 以 settings 为准，改 `.env` 后重启生效）；`max_tokens` / `top_p` 每 agent 可调。`context_window` 为 `null`（默认）时启动组装自动从 litellm 模型注册表解析（ADR 0012 阶段 2），显式值作为注册表缺失/滞后时的覆盖。运行时字段（`base_url`、`auth_token`、`max_retries`、`base_delay`、`request_timeout`）由进程级环境变量注入（见第 4 节），Pydantic 标注 `exclude=True`，不落盘。

### `docker`

<!-- BEGIN GENERATED: agent-yaml-fields-docker -->
`DockerRuntimeConfig` 字段（5 个，按源码声明顺序）：（本区块由 `scripts/gen_reference_docs.py` 生成，勿手改；改了会被 `--check` 打回）

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `image` | `str` | "ubuntu:22.04" |  |
| `working_dir` | `str` | "/workspace" |  |
| `memory_limit` | `str` | "512m" |  |
| `cpu_limit` | `float` | 1.0 |  |
| `network_mode` | `str` | "host" |  |
<!-- END GENERATED: agent-yaml-fields-docker -->

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
    model_name: kimi-k2.5
    temperature: 1.0
  docker:
    image: ubuntu:22.04
    working_dir: /workspace
    memory_limit: 512m
    cpu_limit: 1.0
    network_mode: host
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
| `behavior.tlon` | **成员的全部行为规约**——EDN rules-form，语法见 `reference/dsl-reference.md`；spec 是 total 的，成员是认知型（含 `:loop` 的规则）还是确定型（无 `:loop`）由此文件派生，`agent.yaml` 不再控制 |

## 3. 记忆配置 memory/config.json

`~/.tlon/{agent_id}/memory/config.json`（[memory/models.py](../../src/tlon/memory/models.py) 的 `MemoryConfig`，读写由 [memory/manager.py](../../src/tlon/memory/manager.py) 负责）。完整结构：

```json
{
  "version": "1.0",
  "agent_id": "su-wanqing",
  "summary": { "window": 3 },
  "episodic": { "max_summaries": 20, "max_facts": 50 },
  "context": {
    "max_summaries_in_context": 5,
    "max_facts_in_context": 10
  },
  "reflection": { "min_interactions": 5 }
}
```

各段与 `MemoryConfig` 字段对应：`summary.window`（批量摘要窗口——已完成且未被任何 Summary 引用的 session 数达到该值即触发批量压缩，ADR 0009）、`episodic`（情景记忆上限）、`context`（注入上下文的块数）、`reflection.min_interactions`（自驱动记忆审计的最小完成会话数；开/关与间隔在 `behavior.tlon` 的 `:reflect` 声明）。ADR 0009 之前的 `working`/`triggers` 段读取时自然忽略，无需清理。

> **生效路径**：`MemoryManager.load`（成员启动时调用）在 `config.json` 存在时以磁盘文件为准加载并绑定 `agent_id`，随后把 `episodic` 上限应用到 store——生产装配（[agents/assembly.py](../../src/tlon/agents/assembly.py) 的 `assemble_cognitive`）传入默认 `MemoryConfig()`，不预填 `agent_id`，因此**磁盘调参在生产环境直接生效**。`agent.yaml` 无 `memory:` 段（[base/config.py](../../src/tlon/base/config.py) 的旧 `MemoryConfig` 已删除，其字段从未被消费）。

## 4. 环境变量

读取方是 [base/settings.py](../../src/tlon/base/settings.py) 的 `Settings`（只处理进程级基建与密钥；业务配置在 `AgentConfig`）与 [base/tracing/otel.py](../../src/tlon/base/tracing/otel.py)。`.env` 在 **`{TLON_HOME}/.env`**（默认 `~/.tlon/.env`，配置与数据同目录）加载，且不覆盖已设置的真实环境变量（`_load_dotenv_files`，`override=False`）；`TLON_HOME` 本身只认真实环境变量，不来自 `.env`。

<!-- BEGIN GENERATED: env-vars -->
`Settings` 读取的环境变量（14 个，按源码顺序）：（本区块由 `scripts/gen_reference_docs.py` 生成，勿手改；改了会被 `--check` 打回）

| 变量 | 默认 | 说明 |
|---|---|---|
| `APP_NAME` | Tlon | App settings |
| `LOG_LEVEL` | INFO |  |
| `DEBUG` | false |  |
| `LLM_BASE_URL` | https://api.openai.com/v1 | 仅裸 model 名的 escape hatch 生效；provider/model 由 litellm 路由 |
| `LLM_API_KEY` | "" |  |
| `LLM_MODEL` | moonshot/kimi-k2.5 | provider/model 形式，如 deepseek/deepseek-chat |
| `LLM_TEMPERATURE` | 1.0 |  |
| `LLM_MAX_RETRIES` | 3 |  |
| `LLM_BASE_DELAY` | 1.0 |  |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | （未设置） | Observability (OTel): optional OTLP collector, requires the `otlp` extra |
| `TELEGRAM_BOT_TOKEN` | "" | @BotFather 颁发的 bot token；缺失则 `tlon telegram` 拒绝启动 |
| `TELEGRAM_ALLOWED_USERS` | "" | Telegram user-id 白名单（逗号分隔）；为空则拒绝启动 |
| `TLON_LOG_RETENTION_DAYS` | 7 | Logging: retention (days) for the daily server log file sink |
| `TLON_ADMIN_TOKEN` | "" | 配置后 /admin/* 需 Authorization: Bearer；空 = 关闭 |
<!-- END GENERATED: env-vars -->

上表是 `Settings` 的全部读取点；另有三个环境变量由其他模块读取（手写维护）：

| 变量 | 默认 | 说明 |
|---|---|---|
| `TLON_HOME` | `~/.tlon` | 数据根目录；只认已存在且是目录的值，否则回落 `~/.tlon`（见 [base/paths.py](../../src/tlon/base/paths.py) 的 `_resolve_tlon_home`） |
| `TLON_TRACE_SAMPLE_RATIO` | 恒采样 | 采样率 `[0, 1]`；非法值拒绝（[base/tracing/otel.py](../../src/tlon/base/tracing/otel.py) 的 `SAMPLE_RATIO_ENV`） |
| `TLON_USERNAME` | `guest` | `tlon ops` 的默认邮件身份用户名 |

LLM 配置统一用 `LLM_` 前缀（ADR 0012 阶段 2）：`LLM_MODEL` 是 `provider/model` 形式（默认 `moonshot/kimi-k2.5`），litellm 内置路由表按 provider 前缀解析 base_url，无需手配；`LLM_BASE_URL` 是 escape hatch——仅当 `LLM_MODEL` 为裸模型名（不含 `/`）时生效，用于私有部署 / 代理 / 注册表未收录的端点。`TLON_LOG_RETENTION_DAYS` 管每日服务端日志文件保留（`~/.tlon/shared/logs/server-{date}.jsonl`）；`DEBUG` 为 `"true"` 时生效。

可选键：`ANTHROPIC_API_KEY`、`AZURE_OPENAI_*` 等当前代码不读取；`DATA_DIR` 是遗留项，数据目录一律 `$TLON_HOME` 即 `~/.tlon/`。

## 5. `~/.tlon` 目录布局

[base/paths.py](../../src/tlon/base/paths.py) 的 `TlonPaths` 是**唯一**的路径构造入口，`paths` 是模块级默认实例。布局（docstring 原样）：

```
~/.tlon/
├── .env                              # 进程级环境配置（API key 等，可选；真实环境变量优先）
├── shared/
│   ├── traces/
│   │   └── trace-{uuid}.jsonl        # 会话追踪
│   └── logs/
│       └── server-{date}.jsonl       # 每日服务端日志
├── prototypes/                       # 行为原型库（只读解析，不自动创建）
│   └── {prototype_name}/
│       ├── prototype.edn             # 原型元数据（:name / :display-name / :description / :trusted）
│       └── behavior.tlon             # 行为规约（total spec；原型无人格，ADR 0006）
├── prototypes-staging/               # LLM 创作、待人类批准的原型（闸门）
├── {agent_id}/
│   ├── config/
│   │   ├── agent.yaml                # 本 agent 配置
│   │   ├── IDENTITY.md / SOUL.md / RULES.md   # 人格（创建时数据，非基因型）
│   │   └── behavior.tlon             # 行为规约（唯一权威，一切派生）
│   ├── memory/
│   │   ├── config.json               # 记忆调参
│   │   └── episodic/
│   │       ├── summaries.jsonl
│   │       └── facts.jsonl
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
- `prototypes/` 与 `prototypes-staging/` 是**只读解析**，`exists()` 检查无副作用；`prototypes/` 是受信任源，`prototypes-staging/` 是信任闸（人工批准后才能被 spawn）。

## 6. 配置相关的常用操作

- **增/删 agent**：[base/config.py](../../src/tlon/base/config.py) 的 `yaml_save` / `yaml_delete`（删除即 `rm -rf ~/.tlon/{agent_id}/`）；热加载走 REST `POST /admin/config/agents/{agent_id}/reload`（条件注册，见 `reference/api-reference.md`）。
- **交互式管理**：ops 的 Agents 视图——创建成员（`c`）/ 编辑人格与模型（`e`）/ 删除（`d`）/ 原型审批（`s`）；空社会首启由服务端自动初始化标准团队（[agents/bootstrap.py](../../src/tlon/agents/bootstrap.py)）。
- **agent 目录结构假设**：任何新路径需求必须加进 `TlonPaths`，不许在其他模块硬编码目录结构（[base/paths.py](../../src/tlon/base/paths.py) 模块 docstring 的硬性规定）。