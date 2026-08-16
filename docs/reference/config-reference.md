# Tlon 配置参考

三块配置，职责严格分开：**每 agent 的行为**在 `~/.tlon/{agent_id}/config/`（`agent.yaml` + 人格 `.md` + `behavior.tlon`），**进程级基础设施与密钥**走环境变量（`base/settings.py` 的 `Settings`），**记忆调参**在 `~/.tlon/{agent_id}/memory/config.json`。本文只描述当前实现，配置项以代码中的字段/默认值为准。

## 1. agent.yaml（每 agent 配置）

位置：`~/.tlon/{agent_id}/config/agent.yaml`（路径由 `base/paths.py` 的 `agent_config_file` 给出）。YAML 顶层是一个 `agent:` 键，其值对 `base/config.py` 的 `AgentConfig`（Pydantic v2 `BaseModel`）`model_validate`。未知字段被忽略（旧字段如 `proactive`、`memory` 不生效、可清理——记忆调参唯一入口是 `memory/config.json`）。

### 顶层字段

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `id` | string | — | **必填**。字母/数字/`-`/`_`，agent 目录名即此 id |
| `name` | string | — | **必填**。显示名，他人 `@` 此名可与之对话 |
| `role` | `"resident" \| "contractor"` | `"resident"` | `resident` 是持久的命名人格（睡眠、保留 home）；`contractor` 由原型 spawn，一次性、停止/空闲回收时删除 home |
| `prototype` | string \| null | `null` | 从原型实例化时的原型名（手建则为 null） |
| `spawned_by` | string \| null | `null` | spawn 出本 contractor 的 agent id（resident 为 null） |
| `spawn_depth` | int | `0` | spawn 链深度：resident 为 0，contractor = spawner.depth + 1 |
| `activation` | `"always" \| "lazy"` | `"always"` | **已废弃并忽略**（phase E）：boot 集与 lazy 集由 `behavior.tlon` 的 `:on :boot` 规则派生，字段仅保留兼容旧文件加载 |
| `idle_timeout_seconds` | int | `600` | lazy 成员空闲多少秒后回收 |
| `session_timeout_seconds` | int | `1800` | ACTIVE/WAITING 的会话空闲超时即取消 |
| `message_queue_size` | int | `100` | 入站消息队列容量，溢出丢弃新消息 |
| `llm` | object | 见下 | LLM 客户端配置 |
| `docker` | object | 见下 | Docker 运行时配置 |
| `tool` | object | 见下 | 工具执行配置 |

### `llm`

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `model_name` | string | `"kimi-k2.5"` | 模型名 |
| `temperature` | float | `1.0` | 采样温度，合法域 `[0, 2]`（校验器强制） |
| `max_tokens` | int | `4096` | 单次生成上限 |
| `top_p` | float | `0.9` | 核采样 |

运行时字段不进 YAML：`base_url`、`auth_token`、`max_retries`、`base_delay`、`request_timeout` 由进程级环境变量注入（见第 3 节），Pydantic 标注 `exclude=True`，不会落盘。

### `docker`

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `image` | string | `"ubuntu:22.04"` | 沙箱镜像 |
| `working_dir` | string | `"/workspace"` | 容器工作目录 |
| `memory_limit` | string | `"512m"` | 容器内存上限 |
| `cpu_limit` | float | `1.0` | CPU 上限 |
| `network_mode` | string | `"host"` | 网络模式 |

### `tool`

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `total_timeout` | float | `60.0` | 一轮工具执行总超时（秒） |
| `per_timeout` | float | `30.0` | 单个工具超时（秒），见 `agents/loop_driver.py` 的 `execute_one_tool` |
| `allowed_paths` | string[] | `["/workspace"]` | read/write/edit 文件工具只允许触碰的**容器内绝对路径**；非空、必须 `/` 开头（校验器强制） |

### 完整示例

```yaml
agent:
  id: su-wanqing
  name: 苏晚晴
  role: resident
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

`~/.tlon/{agent_id}/config/` 目录下除 `agent.yaml` 外还有四个文件（路径见 `base/paths.py` 的 `agent_identity_file` 等）：

| 文件 | 内容 |
|---|---|
| `IDENTITY.md` | 叙事身份：我是谁 |
| `SOUL.md` | 性格/价值观/语气 |
| `RULES.md` | 硬性行为规则 |
| `behavior.tlon` | **成员的全部行为规约**——EDN rules-form，语法见 `reference/dsl-reference.md`；spec 是 total 的，成员是认知型（含 `:loop` 的规则）还是确定型（无 `:loop`）由此文件派生，`agent.yaml` 不再控制 |

## 3. 记忆配置 memory/config.json

`~/.tlon/{agent_id}/memory/config.json`（`memory/models.py` 的 `MemoryConfig`，读写由 `memory/manager.py` 负责）。完整结构：

```json
{
  "version": "1.0",
  "agent_id": "su-wanqing",
  "working": { "max_items": 10, "summary_window": 10 },
  "episodic": { "max_summaries": 20, "max_facts": 50 },
  "context": {
    "max_summaries_in_context": 5,
    "max_facts_in_context": 10,
    "max_working_in_context": 5
  },
  "triggers": { "idle_timeout_minutes": 30 },
  "reflection": { "min_interactions": 5 }
}
```

各段与 `MemoryConfig` 字段对应：`working`（工作记忆）、`episodic`（情景记忆上限）、`context`（注入上下文的块数）、`triggers.idle_timeout_minutes`（空闲自动摘要）、`reflection.min_interactions`（自驱动记忆审计的最小交互数；开/关与间隔在 `behavior.tlon` 的 `:reflect` 声明）。

> **生效路径**：`MemoryManager.load`（成员启动时调用）在 `config.json` 存在时以磁盘文件为准加载并绑定 `agent_id`，随后把 `working`/`episodic` 上限应用到两个 store——生产装配（`agents/agent_registry.py` 的 `_assemble_cognitive`）传入默认 `MemoryConfig()`，不预填 `agent_id`，因此**磁盘调参在生产环境直接生效**。`agent.yaml` 无 `memory:` 段（`base/config.py` 的旧 `MemoryConfig` 已删除，其字段从未被消费）。

## 4. 环境变量

读取方是 `base/settings.py` 的 `Settings`（只处理进程级基建与密钥；业务配置在 `AgentConfig`）与 `base/tracing/otel.py`。`.env` 只在仓库根目录加载，且不覆盖已设置的真实环境变量（`_load_dotenv_files`，`override=False`）。

| 变量 | 默认 | 说明 |
|---|---|---|
| `TLON_HOME` | `~/.tlon` | 数据根目录；只认已存在且是目录的值，否则回落 `~/.tlon`（见 `base/paths.py` 的 `_resolve_tlon_home`） |
| `OPENAI_API_KEY` | `""` | LLM API 密钥。默认 provider 是 Moonshot（OpenAI 兼容接口），因此用 `OPENAI_` 前缀 |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | LLM base URL（示例 `.env.example` 填 `https://api.moonshot.cn/v1`） |
| `OPENAI_MODEL_NAME` | `kimi-k2.5` | 默认模型名 |
| `OPENAI_TEMPERATURE` | `1.0` | 默认温度 |
| `OPENAI_MAX_RETRIES` | `3` | LLM 调用重试次数 |
| `OPENAI_BASE_DELAY` | `1.0` | 重试基础退避（秒） |
| `APP_NAME` | `Tlon` | 应用名 |
| `LOG_LEVEL` | `INFO` | 日志级别 |
| `DEBUG` | `false` | 调试模式（`"true"` 生效） |
| `TLON_LOG_RETENTION_DAYS` | `7` | 每日服务端日志文件保留天数（`~/.tlon/shared/logs/server-{date}.jsonl`） |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | 未设置 | OTLP collector base URL；需要可选依赖 `uv sync --extra otlp` |
| `TLON_TRACE_SAMPLE_RATIO` | 恒采样 | 采样率 `[0, 1]`；非法值拒绝（`base/tracing/otel.py` 的 `SAMPLE_RATIO_ENV`） |
| `TLON_USERNAME` | `guest` | `tlon ops` 的默认邮件身份用户名 |

`.env.example` 里还列了 `ANTHROPIC_API_KEY`、`AZURE_OPENAI_*`、`DATA_DIR`，但当前代码不读取它们（`DATA_DIR` 是遗留项，数据目录一律 `$TLON_HOME/data` 即 `~/.tlon/`）。

## 5. `~/.tlon` 目录布局

`base/paths.py` 的 `TlonPaths` 是**唯一**的路径构造入口，`paths` 是模块级默认实例。布局（docstring 原样）：

```
~/.tlon/
├── shared/
│   ├── chat_history/                 # 共享聊天历史
│   ├── traces/
│   │   └── trace-{uuid}.jsonl        # 会话追踪
│   └── logs/
│       └── server-{date}.jsonl       # 每日服务端日志
├── prototypes/                       # 行为原型库（只读解析，不自动创建）
│   └── {prototype_name}/
│       ├── prototype.edn             # 原型元数据（:name / :display-name / :description / :trusted）
│       ├── behavior.tlon             # 行为规约（total spec）
│       └── IDENTITY.md / SOUL.md / RULES.md   # 人格模板（react 型）
├── prototypes-staging/               # LLM 创作、待人类批准的原型（闸门）
├── {agent_id}/
│   ├── config/
│   │   ├── agent.yaml                # 本 agent 配置
│   │   ├── IDENTITY.md / SOUL.md / RULES.md   # 人格
│   │   └── behavior.tlon             # 行为规约（唯一权威，一切派生）
│   ├── memory/
│   │   ├── config.json               # 记忆调参
│   │   ├── working/
│   │   │   └── interactions.jsonl
│   │   └── episodic/
│   │       ├── summaries.jsonl
│   │       └── facts.jsonl
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

- **增/删 agent**：`base/config.py` 的 `yaml_save` / `yaml_delete`（删除即 `rm -rf ~/.tlon/{agent_id}/`）；热加载走 REST `POST /admin/config/agents/{agent_id}/reload`（条件注册，见 `reference/api-reference.md`）。
- **交互式配置**：`tlon mirror`（别名 `tlon wizard`）启动配置向导。
- **agent 目录结构假设**：任何新路径需求必须加进 `TlonPaths`，不许在其他模块硬编码目录结构（`base/paths.py` 模块 docstring 的硬性规定）。
