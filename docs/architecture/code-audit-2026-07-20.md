# Tlon 代码架构审计报告（2026-07-20）

> 本文基于对 `src/tlon` 实际代码的逐文件分析，**不依赖 `docs/` 中的设计文档**。
> 凡文档与代码不符之处，均以代码为准并单独标注。
>
> **修复进度（2026-07-20 更新）**：P0 全部 8 项已修复并合并（PR ）；P1 的 – 已修复（/// 于 PR ，// 于 PR ）；P2 的 14 项易修项已修复（同批次， 为本地清理）， 由 PR （TS/Ink Operator）销项；web/ 前端已彻底删除（确认不再做）。下文中已过时的描述已就地修订，Issue 清单保留原始记录并标注状态。

---

## 一、实际架构

```
┌─ 进程 1: dream (server) ─────────────────────────────────────────┐
│  aiohttp ×2 站点: REST :8080（只读查询）+ WS :8888（写入/事件推送）  │
│  ServerApp（手写 DI 容器，serverapp.py:58）                        │
│    ├─ EventBus（单进程 asyncio pub/sub，易失不持久化）              │
│    ├─ ConfigService（~/.tlon/{agent}/config/agent.yaml）           │
│    ├─ FileMessageStore（JSONL append-only）                        │
│    ├─ MessageRouter（@mention / proactive 广播路由）                │
│    ├─ AgentRegistry → N × Agent                                    │
│    │    每个 Agent：asyncio.Queue + _main_loop 协程                 │
│    │    step/collect 模型：每条触发消息一个 AgentSession，           │
│    │    轮询推进 ACTIVE(session 思考一步) / WAITING(收割工具结果)     │
│    │    ├─ LLMClient（AsyncOpenAI，非流式，function calling）       │
│    │    ├─ ToolExecutor（dict 注册表，17 个工具直接 await）          │
│    │    ├─ DockerRuntime（每 agent 一容器，shell/文件工具沙箱）       │
│    │    └─ MemoryManager（working/episodic JSONL + 关键词检索）      │
│    └─ FileTraceStore（trace context 注入 structlog）               │
└──────────────────────────────────────────────────────────────────┘
        ▲ WS (auth/message/event)      ▲ REST (history/agents/traces)
┌─ 进程 2: chat (Congress TUI) ─┐  ┌─ 进程 3: ops (Operator，TS/Ink) ─┐
│ SDK auto_reconnect（ 收敛）  │  │ apps/operator，真实连接（）   │
└───────────────────────────────┘  └──────────────────────────────────┘
┌─ mirror (wizard)：纯本地配置工具，不连服务端 ─┐
└─ web/ 已删除（2026-07-20）；apps/tui 已删除 ─┘
```

### 1.1 服务端与 Agent 核心

- **框架**：aiohttp（非 FastAPI），REST（默认 8080）+ WebSocket（默认 8888，路径 `/ws`）两个独立站点。**REST 只读，写操作全走 WebSocket**；无 SSE。
- **入口链**：`uv run tlon server`（别名 `dream`）→ `src/tlon/app/launcher/commands/dream.py:33` → `src/tlon/app/server/serverapp.py:354` `run_server()`。`ServerApp`（`serverapp.py:58`）是手写 DI 容器，按 EventBus → ConfigService → Store → Router → AgentRegistry → Gateway → API 顺序装配，shutdown 逆序。
- **REST 端点**（`api/rest.py`）：`/health`、`/config`、`/history`、`/agents`、`/agents/{id}`、`/agents/{id}/logs`（占位假实现）、`/sessions`、`/participants`、`/admin/config/agents`、`/traces`、`/traces/{id}`。（`/ui/*` WebUI 静态服务已随 web/ 一并删除，2026-07-20）
- **WS 协议**（`api/websocket.py`）：JSON 文本帧，首条须为 `auth`（协议版本协商，仅 `1.0.0`，10s 超时），之后支持 `message`/`mode`/`filter`/`ping`；下行 `auth_success`/`event`/`error`/`pong` + 初始 `participants`/`history`。
- **Agent 循环**（`agents/agent.py`）：纯 asyncio 单事件循环。每 Agent 一个 `asyncio.Queue`（`agent.py:173`）+ `_main_loop` 协程。订阅 `MESSAGE_ROUTED` → 目标过滤 / 防自回复 / `_should_respond`（mention 或 proactive）→ 入队。
- **step/collect 调度**（`agent.py:237-299`）：每轮 ① 检查 idle/reflection 触发 → ② 取新消息建 `AgentSession`（sid = `{agent_id}-{msg.id}`）→ ③ ACTIVE 态 `_step_session`（一次 think + 并发派发工具，`asyncio.create_task`）→ WAITING 态 `_collect_session_results` 收割 → ④ 清理 COMPLETED。轮询间隔 10–50ms。
- **LLM client**（`agents/llm_client.py:67`）：`AsyncOpenAI`，OpenAI 兼容协议，**非流式**，标准 function calling；提取 `reasoning_content`；指数退避重试。
- **工具执行**（`agents/tool_executor.py:32`）：dict 注册表 + JSON Schema 参数校验 + 直接 `await tool.execute()`。17 个工具分四类：shell、文件、记忆、任务 + SendMessage。`SendMessageTool` 不发消息，只返回 action 由 Agent 循环转发 EventBus。
- **Prompt 组装**（`agents/prompt_builder.py`）：从 `~/.tlon/{id}/config/` 读 `IDENTITY.md`/`SOUL.md`/`RULES.md`，拼接 identity → 团队成员 SOUL → RULES → 硬编码交互协议（`_PROTOCOL`，强制一切输出走 `sendMessage` 工具）。系统 prompt 按人格文件 mtime 签名缓存，文件变更即重建（PR ；原为构建一次后永久缓存）。
- **动态上下文**（`agent.py:635-689`）：记忆知识块 + 聊天室最近 20 条历史 + 当前消息。
- **两种 session**（同名不同物）：
  - `ClientSession`（`session_manager.py:35`）：WS 连接句柄，内存态，无持久化；
  - `AgentSession`（`agents/session.py:31`）：Agent 的 ReAct 执行上下文，每触发消息一个。
- **消息持久化**：`FileMessageStore`（`organization/messaging/store.py:132`），append-only JSONL（`~/.tlon/shared/chat_history/messages.jsonl`）+ 内存缓存 + asyncio.Lock。人类是伪参与者（id 固定 `"human"`）。

### 1.2 基础设施层

- **配置两层分离**：
  - `settings.py`：进程级，环境变量 / `.env`（python-dotenv），管密钥与端点；
  - `config.py`：业务级，Pydantic `AgentConfig` 组合 `LLMConfig`/`DockerRuntimeConfig`/`MemoryConfig`/`ToolConfig`；`LLMConfig` 敏感字段 `Field(exclude=True)` 不进 YAML，运行时合并。
- **per-agent 目录布局**（`paths.py`，`TlonPaths`）：

  ```
  ~/.tlon/
  ├── shared/chat_history/messages.jsonl, shared/traces/trace-{uuid}.jsonl
  └── {agent_id}/
      ├── config/{agent.yaml, IDENTITY.md, SOUL.md, RULES.md}
      ├── memory/{config.json, working/interactions.jsonl, episodic/{summaries,facts}.jsonl}
      ├── tasks.jsonl
      └── workspace/        # 挂载进容器 /workspace
  ```

- **事件系统**（`base/events/`）：单进程 async pub/sub `EventBus`。约 25 种 `EventType`，事件带 trace/span id。**事件不持久化**，仅消息和 trace 落盘。未运行时 publish 静默丢弃。
- **Tracing**（`base/tracing/`）：`ContextVar` 传播 trace/span id，structlog processor 注入每条日志；`FileTraceStore` 每 trace 一个 JSONL 文件，写盘走 `to_thread` + `threading.Lock`。
- **记忆系统**（`memory/`）：**全部 JSONL + 内存缓存，无 SQLite、无向量库**。
  - Working memory：滚动窗口（默认 10 条）；
  - Episodic memory：summaries + facts，fact 支持 supersede（命中时原子重写 `facts.jsonl` 落盘，PR ；原仅改内存、重启后旧事实复活）；
  - 检索：纯关键词子串匹配，默认实现会拉 1000 条全量过滤；
  - Summarizer 触发：working memory 满 / 空闲超时（默认 30min）/ LLM 主动调 `generateSummary`；流程为「快照 → LLM 调用（不持锁）→ delta 清理」。
- **消息路由**（`organization/messaging/router.py`）：human 消息有 mention → 路由给被 @ 的 agent，无 mention → 广播给所有 proactive agent；agent 消息 → `@clients` + 被 @ 的 agent。store 失败仅 log 不阻断路由。
- **DockerRuntime**（`sandbox/docker_runtime.py`）：每 agent 一容器 `tlon-agent-{id}`，`sleep infinity` 常驻，workspace 卷挂载，mem/cpu 限额，network 默认 `host`。客户端连接多级回退（from_env → Colima/Docker Desktop socket）。同一实例注入 shell/文件四个工具。**无 Docker 降级 = 工具可用但永远失败，无本地执行回退**。
- **Tools 框架**（`tools/base.py`）：`Tool` ABC + `to_openai_definition()` + 参数校验 + 统一 `ToolResult`。无全局注册表，AgentRegistry 手工注册。文件工具有 `allowed_paths` 目录白名单（`path_guard.py`，默认 `/workspace`；2026-07-20 新增，此前无权限模型）；shell 工具不设限。

### 1.3 客户端与协议

- **SDK**（`clients/sdk/`）：`BaseClient`（连接/心跳/重连）+ `TerminalAdapter`（声明有效过滤器，随 auth/set_mode 上报）+ `TlonClient` 组合对外。事件过滤由服务端统一执行（2026-07-20 起为单一过滤点，此前 SDK 本地二次过滤）。WebAdapter/TelegramAdapter 已删除（无真实消费者），`ClientType` 枚举保留为协议词汇。
- **Congress TUI**（`tlon chat`）：textual 应用 + `CongressWebSocketClient`（已收敛回 SDK auto_reconnect，PR ；恢复动作挂 `on_reconnect` 回调）。
- **Operator**（`tlon ops`）：TypeScript + Ink 实现（`apps/operator/`，PR ），真实连接服务器，Agents/Events/Traces/Config 四视图。（原为从不连服务器的 Python 空壳，已删除）
- **Launcher**：server / chat / ops / mirror 均为独立 OS 进程，argparse 分发；textual 延迟导入。
- ~~**web/**：React 脚手架~~ 已彻底删除（2026-07-20，确认不再做 Web 前端）；服务端 `/ui/*` 静态服务、`scripts/build.sh`、pyproject webui 打包配置一并移除。
- ~~**apps/tui/**：废弃 Node TUI 构建产物~~ 已删除（2026-07-20）。
- **测试**：pytest + pytest-asyncio，测试在 `src/tests/`。SDK、agents、tools、wizard、congress client、notification_service 有真实单测；服务端 rest/websocket/gateway、launcher 无测试；无集成测试。

---

## 二、架构评价

### 合理之处

- **进程边界清晰**：server / TUI / wizard 三进程分离，协议版本协商、能力上报齐备，客户端扩展（Web/Telegram adapter）预留了位置。
- **step/collect 异步循环方向正确**：单事件循环内多 session 并发推进、工具调用并发派发，避免了"一个 Agent 思考时全系统阻塞"，是全系统最像样的设计。
- **Component/Device 生命周期正交**、per-agent 目录布局、配置密钥与业务配置分离（settings vs config）都是干净的决策。
- **可观测性思路对**：trace context 用 `ContextVar` 贯穿日志和事件。
- **轻量取舍得当**：当前阶段用 JSONL + 单进程事件总线，而非 SQLite/向量库/独立 MQ，符合项目规模。

### 总体判断

架构方向（骨架）没有方向性错误，但**执行质量配不上骨架**：多个会导致实际故障的 bug 表明代码未经过真实运行检验（pytest 配置错误佐证了这一点）；上帝类、抽象漏洞、死代码和假端点说明近期重构是"摊大饼式"推进，每轮重构（、、）都留下未清理的残留（`serverapp.py:137` 甚至留着一个字面 `...`）。

---

## 三、Issue 清单

### P0 — 会实际出故障的 bug

> ✅ **全部 8 项已修复并合并**（PR ，2026-07-20）。下表保留为原始审计记录。

| # | 问题 | 位置 | 说明 |
|---|------|------|------|
| 1 | `DockerRuntime.execute` 阻塞事件循环，timeout 失效 | `src/tlon/sandbox/docker_runtime.py:204` | 同步 `exec_run` 未包 `to_thread`，长命令卡死所有 Agent；`containers.get`/`start`/`run` 同样未包（:126/:130/:165） |
| 2 | `ConfigService` 多重继承初始化断裂 | `src/tlon/base/config.py:245` | MRO 不走 `Device.__init__`，`shutdown()` 调 `unsubscribe_all()` 必抛 `AttributeError`；对照 `MessageRouter`（`router.py:118-119`）的正确做法 |
| 3 | facts 的 supersede 标记不落盘 | `src/tlon/memory/episodic_store.py:262-270` | 只改内存 cache，重启后旧事实复活，记忆自相矛盾 |
| 4 | 写文件工具依赖容器内 python3，默认镜像没有 | `src/tlon/sandbox/docker_runtime.py:352-355` | 默认 ubuntu:22.04 无 python3，`writeFile`/`editFile` 默认配置下必失败 |
| 5 | SDK 重连后不重新认证 | `src/tlon/app/clients/sdk/base_client.py:353` | auto-reconnect 后 `send()` 全部失败；Congress 被迫自造第二套重连（`congress/client.py:134`） |
| 6 | pytest 配置指向不存在的目录 | `pyproject.toml:103` | `testpaths = ["tests"]`，实际在 `src/tests/`，裸跑 `uv run pytest` 失败 |
| 7 | WebUI 静态路径算错 | `src/tlon/app/server/api/rest.py:49` | 指向 `~/.tlon/src/tlon/webui`，而 `build.sh` 拷到包内 `src/tlon/webui/`，WebUI 实际已死 |
| 8 | 认证竞态 | `src/tlon/app/clients/sdk/base_client.py:452-462` | 先 send 再建 `_auth_event`，响应先到时被 `hasattr` 守卫丢弃 → 必然 timeout |

### P1 — 设计缺陷

>  /  /  /  /  /  /  已修复（/// 于 PR ，// 于 2026-07-20 同批次修复），行内已标 ✅；其余待处理。

| # | 问题 | 位置 | 说明 |
|---|------|------|------|
| 9 | `agent.py` 上帝类（947 行） | `src/tlon/agents/agent.py` | ReAct 循环 + 路由判断 + prompt 组装 + 记忆写入 + reflection 子系统（:748-905）+ tracing 全在一个类 |
| 10 | Facade 名不副实 | `src/tlon/app/server/serverapp.py:210,218` | REST/WS 绕过 `ClientGateway` 直取 `AgentRegistry`；`client_gateway.py:112` 的 `config_manager: Any` 无类型 |
| 11 | 抽象漏洞成片 | `base/config.py:148-150`、`memory/manager.py:75`、`working_store.py:77`、`episodic_store.py:65` | `get_data_dir(base_dir)` 等参数被忽略、硬用全局 `paths` 单例，测试隔离只能靠替换单例 |
| 12 | `paths` 单例 getter 带 `mkdir` 副作用 | `src/tlon/base/paths.py:86-158` | 读操作产生写副作用；`$TLON_HOME` 不存在时静默回退（:63-67） |
| 13 | ✅ 层级反向依赖 | `src/tlon/memory/summarizer.py:24` | 基础设施层 import `agents.llm_client`，违反自身宣称的 DAG。**已修**：`llm_client.py` 移至 `base/`（它本就只依赖 base 层），全部 import 更新（2026-07-20） |
| 14 | ✅ 事件处理无序且 fire-and-forget | `src/tlon/base/events/event_bus.py:215` | 异步 handler `create_task` 无顺序保证、无并发上限；未运行时 publish 静默丢事件（:152-156）。**已修**：`_dispatch` 按订阅顺序逐个 await（PR ）；publish 丢弃行为保留 |
| 15 | ✅ 系统 prompt 永久缓存 | `src/tlon/agents/agent.py:711` | 改 SOUL.md 必须重启进程，与"热编辑人格"的文档宣称矛盾。**已修**：按人格文件 mtime 签名失效重建（PR ） |
| 16 | ✅ 过滤逻辑双份 | `clients/sdk/client.py:228-248` + `adapter.py:232` | filter 既发服务端又本地过滤，两处规则可能漂移。**已修**：服务端成为唯一过滤点（notification_service 订阅全部事件、支持 mode/trace_id/event_types/exclude/agent 维度），adapter 只声明 `get_effective_filters()` 上报；顺带修复服务端只推送 `message.new` 的缺陷（2026-07-20） |
| 17 | ✅ 工具无沙箱权限模型 | `src/tlon/tools/filesystem/*` | 无目录白名单，绝对路径可读写容器内任意位置。**已修**：`ToolConfig.allowed_paths`（默认 `/workspace`）+ `path_guard.py` 共享校验，越界/`..` 逃逸在触达 Docker 前拒绝；遗留：容器内 symlink 可绕过字符串校验（2026-07-20） |
| 18 | ✅ Agent 级状态被多 session 互相覆盖 | `src/tlon/agents/agent.py:329` | 对外暴露的 `AgentState` 不可信。**已修**：`_derive_state()` 由存活 session 派生（PR ） |
| 19 | ✅ 孤儿后台任务 | `src/tlon/agents/agent.py:289,393` | session 异常结束后，其派发的工具任务不会被取消。**已修**：`AgentSession.cancel_pending()` 所有结束路径统一取消（PR ） |

### P2 — 代码质量 / 清理项

> //–/– 已修复（2026-07-20 同批次，行内标 ✅）；//// 待处理（/ 建议立项，// 需先定方向）。

| # | 问题 | 位置 | 说明 |
|---|------|------|------|
| 20 | ✅ 字面 `...` 残留在 initialize() | `src/tlon/app/server/serverapp.py:137` | 重构残留。已删除（2026-07-20） |
| 21 | ✅ 死代码 | `serverapp.py:234-265`（`_get_initial_agents`/`_create_default_configs`）、`clients/tui/common/utils/state.py`（StateManager 无人引用）、`adapter.py:358`（TelegramAdapter 无调用方） | 已全部删除（2026-07-20）；`ClientType.TELEGRAM` 枚举保留为协议词汇 |
| 22 | ✅ Operator TUI 未接线 | 原 `src/tlon/app/clients/tui/operator/` | 展示占位/随机数据，不应作为可用功能外露。**已销项**：Python 空壳删除，改用 TypeScript + Ink 实现（`apps/operator/`，真实连接服务器，PR ） |
| 23 | `/agents/{id}/logs` 假端点 | `src/tlon/app/server/api/rest.py:469-480` | 永远返回空（TODO） |
| 24 | ✅ `FileMessageStore._id_index` 坏且无效 | `src/tlon/organization/messaging/store.py:186,214,322-326` | load 存行号、append 存计数；`get_by_id` 仍全文件线性扫描；多处 `__import__("json")` 补丁式写法。已删除假索引（get_by_id 显式线性扫描）并改为正常 import（2026-07-20） |
| 25 | ✅ tool arguments JSON 解析失败静默降级为 `{}` | `src/tlon/base/llm_client.py:200-205` | LLM 拿到空参数结果，难以排查。已在 warning 中补 tool call id / function name（2026-07-20） |
| 26 | ✅ `APIConnectionError` 重试分支是死代码 | `src/tlon/base/llm_client.py:156-165` | `APIError` 的 except 在前，子类分支永远捕获不到。已调整顺序（2026-07-20）；两分支重试逻辑一致，仅日志文案变化 |
| 27 | ✅ 消息路由死代码 | `src/tlon/organization/messaging/router.py:154` | `event.trace_id or str(uuid.uuid4())` 结果被丢弃。已删除（赋值反而会写入与实际 span 不一致的错误 trace_id，2026-07-20） |
| 28 | ✅ 文档与实现不符 | `memory/base.py:70-79`（get_recent 声称 newest first，实现正序）等 | 已对齐 docstring 与实现（时间正序），全部调用方已排查无依赖（2026-07-20） |
| 29 | ✅ 假时间戳 | `clients/sdk/base_client.py:407,582,654` | 用单调时钟 `loop.time()` 当协议 timestamp，跨进程无意义。已全部改 `time.time()`（PR  + 2026-07-20 补 heartbeat ping） |
| 30 | ✅ 重复的降级摘要逻辑 | `memory/manager.py:554` 与 `summarizer.py:343` | 两份实现。已去重：manager 委托 summarizer 的 `_generate_fallback_summary`（2026-07-20） |
| 31 | ✅ 同步 IO 混入异步路径 | `tools/tasks/manager.py:242,247,279`（阻塞 open + print）、`agents/agent.py:698`（read_text） | TaskManager 出错用 `print` 告警。已改 logger；IO 抽为 `_write_lines`/`_read_lines` 内部函数，async 化切入点已在 docstring 注明（约 30 处同步调用点，级联改造留作后续，2026-07-20） |
| 32 | JSONL 文件只增不减 | `memory/working_store.py:171`、`episodic_store.py:191,284` | 裁剪只在 load()，运行期文件无界增长 |
| 33 | 硬编码 | `base/config.py:99`（network_mode="host"，macOS 无效）、`tools/shell/execute_bash.py:30`（全局后台任务 dict 无清理）、`memory/summarizer.py:99,207`（timeout=30.0）、summarizer prompt 全中文硬编码 | — |
| 34 | ✅ settings 的 .env 路径算错 | `src/tlon/base/settings.py:17-18` | 解析到 `src/` 而非项目根，靠裸 `load_dotenv()` 兜底。已加一级 parent 修正到项目根（2026-07-20） |
| 35 | ✅ memory 工具缺类型注解 | `src/tlon/tools/memory/*.py` 各 `__init__` | 违反项目自身规范。已补 `memory_manager: MemoryManager`（TYPE_CHECKING import，2026-07-20） |
| 36 | ✅ WS 文档声明支持 `wake` 但未实现 | `src/tlon/app/server/api/websocket.py:31` vs `:289-309` | 已从协议注释删除该条（只改注释，2026-07-20） |
| 37 | ✅ `apps/tui/dist/` 遗留物 | `apps/tui/` | 废弃 Node TUI 构建产物，未 git 跟踪。已本地删除（2026-07-20，无需 PR） |
| 38 | 测试覆盖空洞 | `src/tests/` | 服务端 rest/websocket/gateway、launcher 无测试；无集成测试。~~congress client 无测试、test_operator_tui 冒烟~~（已分别于 PR  /  解决） |

### 修复过程中新发现的问题（2026-07-20 登记）

| # | 问题 | 位置 | 说明 |
|---|------|------|------|
| 39 | trace 传播断点 | `src/tlon/organization/messaging/router.py`（`_on_message_new`） | 未用 `continue_trace` 延续事件已有 trace_id，`start_span` 在无 contextvar 时另起新 trace，消息 → 路由 → Agent 的链路 trace 断裂。P2  修复时发现 |
| 40 | 容器 symlink 可绕过路径白名单 | `src/tlon/tools/filesystem/path_guard.py` | 校验在字符串层面，`/workspace` 内指向 `/etc` 的 symlink 可逃逸（ 修复时记录的遗留）。根治方向：容器内 `readlink` 校验或根文件系统只读挂载 |
| 41 | TaskManager 同步 IO 未 async 化 | `src/tlon/tools/tasks/manager.py` |  部分修复的遗留：IO 已抽为 `_write_lines`/`_read_lines`，但 `save()`/`load()` 仍在事件循环内被同步调用；async 化涉及约 30 处调用点级联 |

---

## 四、建议修复顺序

> 进度：第 1–3、5 步已完成（PR  / ，2026-07-20）。

1. ~~**修 pytest 配置**（）~~ ✅ PR ；
2. ~~**修 `DockerRuntime.execute` 阻塞**（）~~ ✅ PR ；
3. ~~**修 `ConfigService` MRO**（）与 facts supersede 落盘（）~~ ✅ PR ；
4. **清理对外露出的半成品**（、、~~~~）—— 已随 PR  修复；Operator TUI、假端点仍待处理；
5. ~~**统一 SDK 重连/认证**（、）~~ ✅ PR ；注意 Congress 的第二套重连逻辑仍存在，可后续收敛回 SDK；
6. 然后才轮到结构性重构：拆 `agent.py` 上帝类（）、补 gateway 抽象（）、解全局单例（、）。
