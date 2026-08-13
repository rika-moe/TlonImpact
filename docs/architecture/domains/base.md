# 基础层 (Base)

> 状态: ✅ 已验证
> 最后验证: 2026-08-13

---

## 职责

提供基础设施服务：文件系统布局解析（`~/.tlon/`）、配置管理、组件生命周期、事件系统、日志、链路追踪、指标（metrics）、全局设置、LLM 客户端

---

## 模块结构

```
base/
├── paths.py               # TlonPaths：~/.tlon/ 布局唯一解析中心（模块级单例 paths）
├── settings.py            # Settings：进程级环境变量配置（secrets/endpoint/日志）+ 仓库根 .env 加载
├── config.py              # 配置模型（AgentConfig 等）+ 独立 YAML CRUD 函数 + ConfigService
├── component.py           # Component 抽象基类（构造注入唯一 / DAG / 两阶段生命周期）
├── device.py              # Device 基类（EventBus 交互能力，与 Component 正交）
├── events/
│   ├── event.py           # EventType（StrEnum）+ Event dataclass（含 trace 字段，面向 SSE 线格式）
│   └── event_bus.py       # EventBus：asyncio.Queue + 单消费者顺序派发
├── logger.py              # structlog 配置（stdout + 每日滚动 JSONL 文件 sink；OTel trace context 注入）
├── llm_client.py          # LLMClient：OpenAI 兼容调用 + 指数退避重试 + llm.request span
├── metrics.py             # Metrics 支柱：init_metrics + MetricsService（纯事件消费者）
├── default_team.py        # 内置 6 人示例团队叙事数据（wizard quickstart 专用）
└── tracing/
    ├── otel.py            # init_tracing（幂等 TracerProvider，可选 OTLP extra，采样比 env 控制）
    ├── jsonl_exporter.py  # JsonlSpanExporter：span 追加写入 trace-{id}.jsonl
    ├── trace_context.py   # OTel 门面：start_span / continue_trace / get_current_trace
    └── trace_store.py     # TraceStore ABC + FileTraceStore（只读查询，供 REST /traces）
```

---

## 关键类

| 类 | 职责 | 路径 |
|----|------|------|
| `TlonPaths` / `paths` | `~/.tlon/` 布局唯一权威（`TLON_HOME` 可覆盖） | `base/paths.py:78,251` |
| `Settings` / `get_settings()` | 进程级 infra 与 secrets（惰性单例） | `base/settings.py:57,96` |
| `AgentConfig` | per-agent 业务配置模型（YAML 持久化） | `base/config.py:147` |
| `AgentInfoProvider` | 配置读接口 + 可选写能力（reload/delete） | `base/config.py:46` |
| `ConfigService` | 配置加载/缓存/保存 + reload 时 EventBus 通知 | `base/config.py:280` |
| `Component` | 组件基类（initialize/shutdown 两阶段生命周期） | `base/component.py:38` |
| `Device` | EventBus 交互能力（subscribe/emit/publish） | `base/device.py:66` |
| `EventBus` | 异步事件总线（单队列单消费者，非单例） | `base/events/event_bus.py:36` |
| `Event` / `EventType` | 事件数据模型（含 trace_id/span_id/parent_span_id） | `base/events/event.py:31,88` |
| `LLMClient` | LLM API 调用（非 Component，装配期按 agent 新建） | `base/llm_client.py:70` |
| `init_tracing` / `JsonlSpanExporter` / `FileTraceStore` | 链路追踪三件套（写 / 落盘 / 只读查） | `base/tracing/` |
| `init_metrics` / `MetricsService` | 指标支柱（事件→指标映射，纯消费者） | `base/metrics.py:63,115` |

---

## 文件系统布局（TlonPaths）

`~/.tlon/` 的全部路径由 `TlonPaths` 解析，任何模块不应硬编码目录假设
（`paths.py:18-21` 的 docstring 即布局图）。`TLON_HOME` 环境变量覆盖根目录
（`paths.py:66-75`）；多数 accessor 有 mkdir 副作用，`agent_behavior_file` /
`prototypes_dir` / `prototypes_staging_dir` / `member_mail_dir_existing` 显式只读
无副作用（`paths.py:189-215`）。要点：

- per-agent：`{id}/config/agent.yaml`、人格文件 `IDENTITY.md`/`SOUL.md`/`RULES.md`
  （`paths.py:177-187`）、行为规约 `config/behavior.tlon`、memory/workspace/mail；
- 人类邮箱：`member_mail_dir` 把 `human:<name>` 映射为 `human-<name>/mail/`——
  冒号替换只发生在这一处（`paths.py:132-144`）；`known_human_ids` 反查持久邮箱；
- 共享：`shared/traces/`（trace JSONL）、`shared/logs/`（每日滚动 server log）、
  `shared/chat_history/`；
- prototype 库：`prototypes/` 与 `prototypes-staging/`（信任闸，见 agents 域文档）。

---

## 全局设置与 .env 加载（Settings）

`Settings`（`settings.py:57-91`）只管进程级 infra 与 secrets：app/log 级别、
tlon_home、LLM endpoint/key/model/重试参数、OTLP endpoint、
`TLON_LOG_RETENTION_DAYS`。业务配置（docker/memory/tool/韧性参数）走 per-agent
YAML（AgentConfig），两者在装配时合流（agents 域 `_build_llm_config`）。
`get_settings()` 是惰性单例（`settings.py:96-101`）；另有 4 个 legacy 委托函数
（`settings.py:29-54`），新代码直接用 `paths`。

**.env 加载语义**（`settings.py:15-26`）：只加载**仓库根** `.env`（由 `__file__`
上溯定位，与 cwd 无关）；`override=False`——真实环境变量恒赢，.env 只补缺。
触发点是 `get_settings()` 首次调用。测试锁定：
`src/tests/base/test_settings_dotenv.py`（real env 不被覆盖 / .env 补缺 /
cwd 的 .env 被忽略）。

---

## 配置模型（AgentConfig）

`AgentConfig`（`config.py:147-200`）真实字段：

- `id`（校验：alnum + `-_`）、`name`；
- `llm: LLMConfig`、`docker: DockerRuntimeConfig`、`memory: MemoryConfig`、
  `tool: ToolConfig`（均 default_factory；`ToolConfig.allowed_paths` 是绝对路径
  白名单）；
- `activation: Literal["always","lazy"] = "always"` —— **deprecated ignored**：
  字段保留仅为旧 agent.yaml 可加载，启动集合由 spec 的 `:on :boot` 派生
  （语义见 agents 域文档）；
- `idle_timeout_seconds = 600`（lazy member 空闲回收阈值）；
- spawn/stop 身份模型：`prototype`、`role: Literal["resident","contractor"]`、
  `spawned_by`、`spawn_depth`；
- 构造期 infra：`session_timeout_seconds = 1800`、`message_queue_size = 100`。

**没有** `identity`/`profile`/`behavior`/`proactive`——叙事人格在
`IDENTITY.md`/`SOUL.md`/`RULES.md`，行为在 `behavior.tlon`（MemberSpec，agents 域）。

`LLMConfig`（`config.py:87-107`）：持久化字段 `model_name`/`temperature`（0–2
校验）/`max_tokens`/`top_p`；`base_url`/`auth_token`/`max_retries`/`base_delay`/
`request_timeout` 是 `Field(exclude=True)` 的 runtime-only 字段，**不写 YAML**，
装配时从 `Settings` 合并。

---

## ConfigService 与 YAML CRUD

`ConfigService(Component, Device, AgentInfoProvider)`（`config.py:280-384`），
构造签名 `(event_bus, tlon_home=None)`：

- `initialize()` 全量加载；内存缓存 `_agents`；
- `reload_agent` 成功才发 `CONFIG_UPDATED`；`save_agent`/`delete_agent`
  只写盘 + 更新缓存，**不发事件**；
- `__init__` 显式双调用 `Component.__init__`/`Device.__init__`（MRO super 会
  跳过 Device，`config.py:295-297`）——MetricsService 同一模式；
- `AgentInfoProvider`（`config.py:46-79`）：读接口 `get_config`/`get_all_ids` +
  可选写能力（`reload_agent` 默认 False=只读，`delete_agent` 默认 raise）。
  消费方：AgentRegistry、MailDelivery、AdapterManager。

**YAML CRUD 独立函数**（`config.py:208-272`）：`yaml_load_file` /
`yaml_load_all`（扫 `~/.tlon/*/config/agent.yaml`，跳过 `shared`）/
`yaml_save`（`{"agent": ...}` 包装 + 确保 workspace/memory 目录）/
`yaml_delete`（rmtree 整个 agent home）/`yaml_exists`。三方共用：
ConfigService、wizard 的 `AgentStore`（薄包装）、agents 域 registry/prototypes
直接调 `yaml_save`。

**配置读写两条路径**：server 进程走 ConfigService（内存缓存 + reload 通知）；
wizard/mirror 是独立进程，走 AgentStore 直接读写同一批 YAML + persona .md +
behavior.tlon；热生效靠 `POST /admin/config/agents/{id}/reload`（端点细节见
server 域）。

---

## 组件与生命周期（Component / Device / ServerApp）

`Component`（`component.py:38`）三原则：构造注入唯一 / EventBus 通信 /
DAG 无环；两阶段生命周期——`__init__` 不订阅，`initialize()` 订阅 +
起后台任务，`shutdown()` 退订 + 释放。反模式清单在 docstring。

`Device`（`device.py:66`）与 Component 正交（仅 Component 如 FileTraceStore、
仅 Device、两者兼具三种组合）；`emit()` 便捷造 Event（source 默认 device 名）。

**装配现状**：`ComponentRegistry` 已彻底删除（全仓零引用）。ServerApp 是唯一
装配者——`_components: dict[str, Any]`（`app/server/serverapp.py:97`）手工按
拓扑序逐个 `await initialize()`（`serverapp.py:107-247`：tracing/metrics 初始化
→ EventBus → ConfigService → MetricsService → TraceStore → AgentRegistry → Adam →
AdapterManager → MemberDirectory → MailDelivery → gateway → REST），shutdown 手工
逆序，tracing/metrics 最后 flush。

---

## 事件系统（EventBus / Event）

`EventBus(Component)`（`event_bus.py:36`）：**不是单例**——由 ServerApp 构造
（`serverapp.py:129`）。进程内、单 `asyncio.Queue`、单消费者 `_process_events`
循环；`_dispatch` 按订阅序逐个 await（保序、同时只有一个 handler 在跑、单
handler 异常隔离）；同步 handler 进线程池（`event_bus.py:241`）。

`Event` dataclass（`event.py:88`）自带 `trace_id`/`span_id`/`parent_span_id`，
`to_dict`/`from_dict` 面向 SSE 线格式——是 SSE 推送与 trace carrier 的统一体。
`EventType`（`event.py:31-85`）：mail 两个（`MAIL_NEW`/`MAIL_DELIVERED`）、agent
生命周期一族（`AGENT_STARTED/STOPPED/WAKING/WOKEN/WAKE_FAILED/SLEEPING/
START_REQUEST/STOP_REQUEST/IDLE/SPAWNED`）、可观测性一族（`AGENT_THINKING_*/
LLM_REQUEST/LLM_RESPONSE/TOOL_EXECUTE_START/END`）、system、`CONFIG_UPDATED`。

---

## LLMClient

`LLMClient`（`llm_client.py:70`）不是 Component/Device——每 agent 装配时由
agents 域 registry 新建，memory 的 summarizer 也自建。OpenAI 兼容 API，重试
4 类 openai 异常（RateLimit/Timeout/Connection/APIError），指数退避
`base_delay * 2^attempt`；未配置 `auth_token` 直接抛错提示 `OPENAI_API_KEY`。
每次调用包一个 `llm.request` span（重试含在内），usage 写 span attributes。
`ToolCall`/`LLMResponse` dataclass（`llm_client.py:39-67`）。

---

## Tracing 三件套

`init_tracing`（幂等，`tracing/otel.py:52`）→ `JsonlSpanExporter` 同步把结束的
span 追加到 `~/.tlon/shared/traces/trace-{trace_id}.jsonl`（trace_start/
span_start/span_end/trace_end 行格式）→ `FileTraceStore`（`trace_store.py:204`）
只读回供 REST `/traces`。采样比 env `TLON_TRACE_SAMPLE_RATIO`（默认 ALWAYS_ON）；
OTLP 是 optional extra（`uv sync --extra otlp`），endpoint 来自
`Settings.otel_exporter_otlp_endpoint`，缺包时告警跳过。
`trace_context.py` 是 OTel 门面：`start_span` / `continue_trace`（从事件 carrier
接续远端 trace）/ `get_current_trace`。

---

## 日志

`configure_logging`（`logger.py:42`）：structlog，stdout JSON/彩色 + 可选每日
滚动 JSONL 文件 sink（`shared/logs/server-{date}.jsonl`，保留天数
`TLON_LOG_RETENTION_DAYS`）；OTel trace context 注入日志（trace/span id 进每条
记录）。`get_logger(__name__)` 取 logger。

---

## Metrics 支柱

可观测性三支柱的最后一块（traces / logs / metrics），落在 `base/metrics.py`。

**选型**：复用项目已有的 OpenTelemetry SDK——不引新依赖、不手搓。
`init_metrics()`（幂等，`metrics.py:63`）构建进程级 `MeterProvider` +
`InMemoryMetricReader`：进程内快照经 `GET /metrics` 给 operator；将来接 OTLP
metric exporter 无需改业务代码。

**架构：metrics 作为事件消费者**。业务代码零埋点——
`MetricsService(Component, Device)`（`metrics.py:115`）订阅 EventBus，把既有事件
映射为指标，全部埋点集中在这一个组件里。事件处理异常被 try/except 包裹
（记 warning），绝不影响 bus dispatch。9 个事件订阅（`metrics.py:196-208`）：

| 事件 | 指标 | 属性 |
|------|------|------|
| `MAIL_NEW` | `tlon.mail.sent` (counter) | `agent=source` |
| `MAIL_NEW` 且 from=postmaster | `tlon.mail.bounced` (counter) | 无 |
| `MAIL_DELIVERED` | `tlon.mail.delivered` (counter) | `kind=agent\|human` |
| `AGENT_STARTED` / `WOKEN` / `SLEEPING` / `STOPPED` | `tlon.agent.started` / `woken` / `sleeping` / `stopped` (counter) | `agent_id` |
| `AGENT_LLM_RESPONSE` | `tlon.llm.calls` (counter) + `tlon.llm.duration_ms` (histogram) | `agent_id` |
| `TOOL_EXECUTE_END` / `TOOL_ERROR` | `tlon.tool.executions` / `tlon.tool.errors` (counter) | `tool_name` |

`_POSTMASTER_ADDRESS`/`_HUMAN_ID_PREFIX` 在本文件复制常量，保持 base 无向上
依赖（`metrics.py:54-57`）。

**Cardinality 纪律**：attribute 只允许低基数值（agent id、tool 名、recipient
种类、事件 source），严禁 mail_id / session_id / trace_id 入 attribute。
recipient 只记 `kind`（`human:` 前缀判断），不记全名。

**snapshot() 结构**：`{"metrics": {name: {"type": "counter"|"histogram"|"gauge",
"description", "unit", "points": [...]}}}`——counter/gauge 点带
`{attributes, value}`，histogram 点带 `{attributes, count, sum, min, max}`。
REST `GET /metrics` 直接返回该 JSON；service 不可用时 503
（`app/server/api/rest.py:1000-1012`）。

---

## 文档索引

| 文档 | 目的 | 深度 |
|------|------|------|
| [../../design/config.md](../../design/config.md) | 配置系统设计（含 ~/.tlon/ 目录结构） | L1 |
| [../../design/narrative-prompt.md](../../design/narrative-prompt.md) | Prompt 架构设计 | L2 |
| [agents.md](./agents.md) | Agent 域（spec 语义、装配、生命周期） | L1 |

---

## 验证清单

- [x] `AgentConfig` 字段与代码一致（无 identity/profile/behavior/proactive；activation deprecated ignored）
- [x] `ConfigService` 构造签名 `(event_bus, tlon_home)`；`CONFIG_UPDATED` 仅在 reload_agent 发出
- [x] EventBus 由 ServerApp 构造（非单例），单消费者顺序派发、异常隔离
- [x] `ComponentRegistry` 已删除，ServerApp 手工按拓扑序装配
- [x] .env 只加载仓库根、真实环境变量优先、与 cwd 无关（有测试锁定）
- [x] `~/.tlon/` 布局全部由 `TlonPaths` 解析
- [x] Metrics 事件→指标映射与 `metrics.py` 一致，`GET /metrics` 503 分支存在
- [x] Tracing 写路径（init_tracing → JsonlSpanExporter）与读路径（FileTraceStore）分离
