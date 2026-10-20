# Tlon 工具手册

agent 通过 OpenAI function calling 调用的全部工具。工具实现位于 [src/tlon/tools/](../../src/tlon/tools)，按域分包；已切换域的参数与结果以 `tlon.operations` 中的 Operation 定义为准，其余工具以构造器 JSON schema 为准。本文只描述当前实现。

## 总述

### Tool 基类契约

[tools/base.py](../../src/tlon/tools/base.py) 的 `Tool`：每个工具有 `name`（LLM 可见的名字）、`description`、`parameters`（JSON schema）三要素，`to_openai_definition()` 输出 OpenAI function calling 格式（`{"type": "function", "function": {...}}`）。旧 Tool 的 `execute(**kwargs)` 为业务执行入口，返回 `ToolResult(success, data, error, message)`；`message` 是给人看的摘要，`data` 是给 LLM 的结构化结果。可选生命周期钩子 `initialize()` / `shutdown()`，由执行器启动/停止时调用；后台命令的生命周期由成员绑定的 `ShellService` 负责。

`Tool.validate_arguments` 在执行前按 schema 校验：缺必填、未知参数、类型不符都会拒绝执行，返回 `success=False, error="Argument validation failed: ..."`；数值参数的 `minimum`/`maximum` 也在此强制（被拒时错误含真实界限值），`enum` 等其余约束不进 `validate_arguments`，靠各工具自己 clamp 或报错。

已切换邮件、文件、Shell 与时间由 `OperationTool` 投影，严格嵌套 JSON Schema 校验在共享 OperationExecutor 完成；DSL 与模型得到同一领域结果。旧 Tool 的浅层校验只描述尚未切换的域。

### ToolExecutor 执行模型

[agents/tool_executor.py](../../src/tlon/agents/tool_executor.py) 的 `ToolExecutor` 是每 agent 一个的工具注册表：直接 async 调用，不经过 EventBus。`execute(tool_name, arguments)` 包住一层 tracing span（`tool.{name}`，参数截断到 200 字符），三类失败都转成 `ToolResult(success=False)` 而不是抛异常：工具未注册、参数校验失败、`execute` 内部抛异常（记日志后返回 `error="Execution error: ..."`）。

会话循环里的并发分发不在 ToolExecutor，而在引擎侧：

- act stage（[agents/stages/act.py](../../src/tlon/agents/stages/act.py) 的 `act_stage_step`）是纯变换，只把本轮 LLM 的 tool calls 声明进 `StageOutcome.tool_dispatches`，自己不执行。
- [agents/loop_driver.py](../../src/tlon/agents/loop_driver.py) 的 `LoopDriver.execute_one_tool` 为每个 tool call 起一个后台 asyncio task 并发执行，外层套 `asyncio.wait_for`。等待预算按「模型可见的 schema 必须可兑现」解析：工具自己在 schema 里声明了 `timeout` 参数（如 `shell-run`）时，未显式传 timeout 时读取操作的 default_timeout（shell-run 为 60 秒）；引擎等「声明值 + `_TOOL_TIMEOUT_GRACE`（10 秒）宽限」——工具自身是限时权威，wait_for 只兜底工具挂死；未声明 `timeout` 的工具用引擎兜底 `per_tool_timeout`（来自 agent 配置的 `tool.per_timeout`，[base/config.py](../../src/tlon/base/config.py) 的 `ToolConfig`，默认 30 秒）。超时返回 `success=False`，error 指明真正生效的限制来自哪层（工具声明值 + 引擎宽限，或引擎 per-tool 兜底）。
- 超大输出 spill：结果在收割前先过 [tools/spill.py](../../src/tlon/tools/spill.py) 的 `ToolSpill`——`data` 序列化超过 16k 字符即整个落盘到 `workspace/spills/{session_id}/{tool_call_id}.json`（缩进 JSON，可分页），模型与 session 日志只见到 locator（路径/大小/preview/检索提示），可用 `file-read` 的 offset/limit 读回（workspace 即容器工作目录的 bind mount）。写盘失败回退原始结果。
- 收割：`collect_session_results` 把已完成 task 的结果交给 `act_stage_collect`，结果以 `{"role": "tool", ...}` 消息追加回 LLM 上下文（`ToolResult.to_dict()` 的 JSON 序列化）。
- 结束工具 `finishTurn`、`sleepUntil`、`exitAgent` 必须各自单独占一轮，混用其他工具整轮拒绝。邮件工具不负责结束工作，发信后检查结果，再单独请求结束或睡眠。

### 工具装配

每个成员都经 `assemble_member_capabilities`（[agents/assembly.py](../../src/tlon/agents/assembly.py)）装配 LLM 客户端、记忆、TaskManager、Docker 运行时和全套工具（懒装配：对象便宜、加载按需）；声明 `docker.level: L0` 的成员例外（issue #26）——无执行装置，不创建 DockerRuntime，也不注册 shell/filesystem 七个装置工具；`docker.level: L1` 的文件层成员（issue #40）创建运行时、注册 filesystem 五个工具，但不注册 shell 两个（结构性零代码执行）。程序只在实际请求模型或工具时产生相应调用成本；不存在按是否声明会话给成员分类的装配分支。`proposeBehavior` 是首个拿到 `llm_client` / `memory` 的 agents 域工具（此前这对依赖只喂给记忆域工具与反思引擎；它的 journal 以 `SessionLogReader` 端口注入，保持 `tlon.tools` 不反向依赖 `tlon.agents`），`proposeCapability` 同型（能力通道的经验入口）；`reviewPrototype` 是拿到 `llm_client` 的 prototypes 域工具——评审调用用评审者自己的模型，异质性来自评审者的模型血统。

### 注册清单

<!-- BEGIN GENERATED: tool-registry -->
满配成员注册 48 个工具（尚未切换的 Tool 按 assembly 顺序，共享 Operation 按域列出；L0 不注册 7 个装置操作，L1 注册 5 个文件操作，L2 再增加 2 个 Shell 操作；时间不依赖装置）：（本区块由 `scripts/gen_reference_docs.py` 生成，勿手改；改了会被 `--check` 打回）

| # | 工具名 | 实现 | 域 |
|---|---|---|---|
| 1 | `finishTurn` | [tools/agents/scheduling.py](../../src/tlon/tools/agents/scheduling.py) 的 `FinishTurnTool` | agents |
| 2 | `sleepUntil` | [tools/agents/scheduling.py](../../src/tlon/tools/agents/scheduling.py) 的 `SleepUntilTool` | agents |
| 3 | `exitAgent` | [tools/agents/scheduling.py](../../src/tlon/tools/agents/scheduling.py) 的 `ExitAgentTool` | agents |
| 4 | `getSelfState` | [tools/agents/scheduling.py](../../src/tlon/tools/agents/scheduling.py) 的 `GetSelfStateTool` | agents |
| 5 | `listPrototypes` | [tools/prototypes/list_prototypes.py](../../src/tlon/tools/prototypes/list_prototypes.py) 的 `ListPrototypesTool` | prototypes |
| 6 | `listStagingPrototypes` | [tools/prototypes/list_staging.py](../../src/tlon/tools/prototypes/list_staging.py) 的 `ListStagingPrototypesTool` | prototypes |
| 7 | `getPrototypeLineage` | [tools/prototypes/get_lineage.py](../../src/tlon/tools/prototypes/get_lineage.py) 的 `GetPrototypeLineageTool` | prototypes |
| 8 | `reviewPrototype` | [tools/prototypes/review_prototype.py](../../src/tlon/tools/prototypes/review_prototype.py) 的 `ReviewPrototypeTool` | prototypes |
| 9 | `spawnAgent` | [tools/agents/spawn_agent.py](../../src/tlon/tools/agents/spawn_agent.py) 的 `SpawnAgentTool` | agents |
| 10 | `listMembers` | [tools/agents/list_members.py](../../src/tlon/tools/agents/list_members.py) 的 `ListMembersTool` | agents |
| 11 | `writePrototype` | [tools/agents/write_prototype.py](../../src/tlon/tools/agents/write_prototype.py) 的 `WritePrototypeTool` | agents |
| 12 | `proposeBehavior` | [tools/agents/propose_behavior.py](../../src/tlon/tools/agents/propose_behavior.py) 的 `ProposeBehaviorTool` | agents |
| 13 | `proposeCapability` | [tools/agents/propose_capability.py](../../src/tlon/tools/agents/propose_capability.py) 的 `ProposeCapabilityTool` | agents |
| 14 | `mail-send` | [operations/mail.py](../../src/tlon/operations/mail.py) 的 `OperationTool` 投影 | mail |
| 15 | `mail-forward` | [operations/mail.py](../../src/tlon/operations/mail.py) 的 `OperationTool` 投影 | mail |
| 16 | `mail-check-inbox` | [operations/mail.py](../../src/tlon/operations/mail.py) 的 `OperationTool` 投影 | mail |
| 17 | `mail-search` | [operations/mail.py](../../src/tlon/operations/mail.py) 的 `OperationTool` 投影 | mail |
| 18 | `mail-mark-read` | [operations/mail.py](../../src/tlon/operations/mail.py) 的 `OperationTool` 投影 | mail |
| 19 | `file-read` | [operations/filesystem.py](../../src/tlon/operations/filesystem.py) 的 `OperationTool` 投影 | filesystem |
| 20 | `file-write` | [operations/filesystem.py](../../src/tlon/operations/filesystem.py) 的 `OperationTool` 投影 | filesystem |
| 21 | `file-edit` | [operations/filesystem.py](../../src/tlon/operations/filesystem.py) 的 `OperationTool` 投影 | filesystem |
| 22 | `file-list` | [operations/filesystem.py](../../src/tlon/operations/filesystem.py) 的 `OperationTool` 投影 | filesystem |
| 23 | `file-search` | [operations/filesystem.py](../../src/tlon/operations/filesystem.py) 的 `OperationTool` 投影 | filesystem |
| 24 | `shell-run` | [operations/shell.py](../../src/tlon/operations/shell.py) 的 `OperationTool` 投影 | shell |
| 25 | `shell-check` | [operations/shell.py](../../src/tlon/operations/shell.py) 的 `OperationTool` 投影 | shell |
| 26 | `time-now` | [operations/time.py](../../src/tlon/operations/time.py) 的 `OperationTool` 投影 | time |
| 27 | `memory-remember` | [operations/memory.py](../../src/tlon/operations/memory.py) 的 `OperationTool` 投影 | memory |
| 28 | `memory-search` | [operations/memory.py](../../src/tlon/operations/memory.py) 的 `OperationTool` 投影 | memory |
| 29 | `memory-forget` | [operations/memory.py](../../src/tlon/operations/memory.py) 的 `OperationTool` 投影 | memory |
| 30 | `session-read` | [operations/memory.py](../../src/tlon/operations/memory.py) 的 `OperationTool` 投影 | memory |
| 31 | `task-create` | [operations/tasks.py](../../src/tlon/operations/tasks.py) 的 `OperationTool` 投影 | tasks |
| 32 | `task-list` | [operations/tasks.py](../../src/tlon/operations/tasks.py) 的 `OperationTool` 投影 | tasks |
| 33 | `task-start` | [operations/tasks.py](../../src/tlon/operations/tasks.py) 的 `OperationTool` 投影 | tasks |
| 34 | `task-pause` | [operations/tasks.py](../../src/tlon/operations/tasks.py) 的 `OperationTool` 投影 | tasks |
| 35 | `task-complete` | [operations/tasks.py](../../src/tlon/operations/tasks.py) 的 `OperationTool` 投影 | tasks |
| 36 | `task-remove` | [operations/tasks.py](../../src/tlon/operations/tasks.py) 的 `OperationTool` 投影 | tasks |
| 37 | `task-current` | [operations/tasks.py](../../src/tlon/operations/tasks.py) 的 `OperationTool` 投影 | tasks |
| 38 | `contacts-read` | [operations/identity.py](../../src/tlon/operations/identity.py) 的 `OperationTool` 投影 | identity |
| 39 | `contacts-upsert` | [operations/identity.py](../../src/tlon/operations/identity.py) 的 `OperationTool` 投影 | identity |
| 40 | `persona-update` | [operations/identity.py](../../src/tlon/operations/identity.py) 的 `OperationTool` 投影 | identity |
| 41 | `metrics-read` | [operations/observability.py](../../src/tlon/operations/observability.py) 的 `OperationTool` 投影 | observability |
| 42 | `mail-stats` | [operations/observability.py](../../src/tlon/operations/observability.py) 的 `OperationTool` 投影 | observability |
| 43 | `reputation-read` | [operations/observability.py](../../src/tlon/operations/observability.py) 的 `OperationTool` 投影 | observability |
| 44 | `social-health` | [operations/observability.py](../../src/tlon/operations/observability.py) 的 `OperationTool` 投影 | observability |
| 45 | `traces-list` | [operations/observability.py](../../src/tlon/operations/observability.py) 的 `OperationTool` 投影 | observability |
| 46 | `trace-read` | [operations/observability.py](../../src/tlon/operations/observability.py) 的 `OperationTool` 投影 | observability |
| 47 | `state-get` | [operations/state.py](../../src/tlon/operations/state.py) 的 `OperationTool` 投影 | state |
| 48 | `state-put` | [operations/state.py](../../src/tlon/operations/state.py) 的 `OperationTool` 投影 | state |
<!-- END GENERATED: tool-registry -->

上表是正式成员的满配。见习成员不注册 spawnAgent、writePrototype、proposeBehavior、proposeCapability 四个生殖/演化工具；所有成员均可使用绑定自身的四个调度工具。L0 不注册 shell/filesystem 七个装置工具，L1 不注册 shell 两个工具。

依赖倒置：`tlon.tools` 不许反向依赖 `tlon.agents`/`tlon.prototypes`，agents 域与 prototypes 域工具的端口（`AgentLifecycle` Protocol、`SpawnRejectedError`、`StopRejectedError`）声明在 [tools/agents/ports.py](../../src/tlon/tools/agents/ports.py)，原型端口（`PrototypeError`、`PrototypeSummary`）声明在 [tools/prototypes/ports.py](../../src/tlon/tools/prototypes/ports.py)；装配时由 `AgentRegistry` 把自身和原型库函数注入工具实例。

shell 与 filesystem 工具共享同一个 `DockerRuntime` 实例（[sandbox/docker_runtime.py](../../src/tlon/sandbox/docker_runtime.py)）。文件柜容器创建后**从不启动**（issue #25），首次文件 I/O 时惰性创建/按名重挂；自 issue #40 起五个 filesystem 工具全部经 get_archive/put_archive 读写（list/search 把目录 tar 拉回 host 侧解包，write 把缺失父目录作为 tar 目录条目带入），容器内零命令执行。Docker daemon 不可用时这些工具返回 `error="Docker runtime not initialized"`，agent 本体照常启动。`docker.level: L0` 的成员（issue #26）完全不装配这两域工具——无执行装置即无工具，不存在「工具有但装置为 None」的状态；`docker.level: L1` 的成员（issue #40）只有 filesystem 五件，shell 工具不注册且 `run()` 由装置层直接拒绝。

## mail

五个邮件操作通过 `OperationTool` 向模型暴露，通过 `DSLAdapter` 向程序暴露，共享 [Operation 定义](../../src/tlon/operations/mail.py) 与成员作用域 [MemberMailService](../../src/tlon/organization/mail/member_mail.py)。只有实际投递提交后才返回成功回执，LoopDriver 不再按工具名二次提交。

- 所有邮箱读取、标读、回复与转发引用都限定为调用成员自己的邮箱；发件身份由宿主绑定。
- 回复显式传 `in_reply_to`，线程从本成员邮箱中的引用推导；不从触发邮件隐式补收件人。
- 转发显式传 `mail_id`；自动生成 Fwd 主题、归因头与原文，外部邮件附不可信提醒；另起线程。不接受主题覆盖。
- `endorsing` 验证本成员邮箱引用并禁止自我致谢；mentor cc 在共享邮件服务处理。
- 接受证明本地入箱；gateway 回执不保证外部最终送达。拒收返回 `RejectedError`，结果未知返回 `UnknownOutcomeError`；两者保留结构化 data，unknown 携带 request_id。结果未知必须用同一 request_id 重试，同键不同内容拒绝。
- 发信、转发和标读均不结束工作；完成后下一轮单独调用 finishTurn、sleepUntil 或 exitAgent。

<!-- BEGIN GENERATED: mail-operations -->
Operation 单一契约；输入与输出字段保留 snake_case。（本区块由 `scripts/gen_reference_docs.py` 生成，勿手改；改了会被 `--check` 打回）

### mail-send

发送邮件；accepted 表示本地已入箱，不代表已阅读或处理。网关只尝试投递。

输入：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `request_id` | string | 否 | 稳定投信标识；结果未知时用原值重试，同键不同内容会拒绝。; minLength=1 |
| `to` | array[string] | 是 | — |
| `cc` | array[string] | 否 | — |
| `content` | string | 是 | minLength=1 |
| `subject` | string | 否 | — |
| `in_reply_to` | string | 否 | — |
| `endorsing` | string | 否 | — |

输出：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `status` | string | 是 | enum=['accepted'] |
| `mail_id` | string | 是 | — |
| `request_id` | string | 是 | — |
| `local_recipients` | array[string] | 是 | — |
| `gateways` | array[object] | 是 | — |
| `message` | string | 是 | — |

### mail-forward

转发自己邮箱中的邮件；保留原文和归因，外部原信带不可信标记。

输入：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `request_id` | string | 否 | 稳定投信标识；结果未知时用原值重试，同键不同内容会拒绝。; minLength=1 |
| `to` | array[string] | 是 | — |
| `cc` | array[string] | 否 | — |
| `mail_id` | string | 是 | — |
| `comment` | string | 否 | — |

输出：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `status` | string | 是 | enum=['accepted'] |
| `mail_id` | string | 是 | — |
| `request_id` | string | 是 | — |
| `local_recipients` | array[string] | 是 | — |
| `gateways` | array[object] | 是 | — |
| `message` | string | 是 | — |

### mail-check-inbox

查看自己的未读邮件，按最旧在先返回完整正文；读取不标读。

输入：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `limit` | integer | 否 | minimum=1; maximum=50 |

输出：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `mails` | array[object] | 是 | — |
| `count` | integer | 是 | — |
| `total_unread` | integer | 是 | — |

### mail-search

检索自己邮箱；query 空格分词 AND 匹配并返回节选，mail_id 精确读取完整邮件。

输入：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `query` | string | 否 | — |
| `mail_id` | string | 否 | — |
| `limit` | integer | 否 | minimum=1; maximum=20 |

输出：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `mail` | object | 否 | — |
| `mails` | array[object] | 否 | — |
| `count` | integer | 否 | — |
| `query` | string | 否 | — |

### mail-mark-read

显式标读自己的收件箱；分别报告新标读、已读和不存在的 id。

输入：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `mail_ids` | array[string] | 是 | minItems=1 |

输出：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `marked` | array[string] | 是 | — |
| `already_read` | array[string] | 是 | — |
| `not_found` | array[string] | 是 | — |
<!-- END GENERATED: mail-operations -->

## 调度

### finishTurn

无参数。结束自己的本次工作，不发信、不隐式标读。成功意图通过 journal 记录，调度器完成收割后回到等待；新工作可以随后激活。必须单独一轮。需要标读或回信时先分别调用对应工具。

### sleepUntil

参数 wake_at 必填，必须是未来的带时区 ISO 8601 时间；reason 可选。归一为 UTC 并持久保存，结束本次工作后进入睡眠。期间邮件正常入箱但不唤醒，到期检查邮箱；重启不延长期限。管理员可通过独立管理接口提前唤醒；普通邮件和成员工具不能提前唤醒他人。必须单独一轮。

### exitAgent

reason 可选。只退出自己，不接受 agent_id。当前工作收尾后永久死亡，拒收新邮件，历史保留，不可通过启动或重启复活。必须单独一轮。不能用它终止其他成员。

### getSelfState

无参数。查询调用者的四态与 wake_at 等诊断，不改变状态。普通成员没有管理其他成员的状态工具；人类管理端可请求排空后终止。

## filesystem

五个工具都在 Docker 沙箱容器内操作，agent 不接触宿主机文件系统。路径统一经 [sandbox/path_guard.py](../../src/tlon/sandbox/path_guard.py) 的 `resolve_container_path` 校验：

- 相对路径先拼到容器工作目录（`DockerRuntimeConfig.working_dir`，默认 `/workspace`），再按 POSIX 语义规范化（折叠 [.](./.)/[..](./..)/重复分隔符，[..](./..) 越出根时在 `/` 处截断）。
- 解析结果必须等于或位于 `allowed_paths`（agent 配置 `tool.allowed_paths`，默认 `["/workspace"]`）之下，否则抛 `ValueError`，工具返回 `success=False` 和越界说明。读侧三工具（file-read/file-list/file-search）在配置白名单之外额外放行 `/tlon-home`——agent 自己的家目录（`~/.tlon/{agent_id}/`）以只读 bind-mount 挂进容器（见 docs/design/execution-device.md 的纵深防御），可读取自己的行为定义、记忆与邮箱；file-write/file-edit 不含此路径，且挂载本身是 `ro`，双保险。其他成员的家目录不挂载进本容器，无法跨 agent 读取。
- 空路径直接拒绝。词法检查后由装置逐级检查目标及祖先，拒绝 symlink；不解析宿主路径。stat 和 archive 之间仍存在并发修改窗口，边界与限制见[执行装置设计](../design/execution-device.md#挂载与文件通道)。

文件操作的字段与输出统一由下方权威定义生成，DSL 和 Tool 使用同一名称及 snake_case 参数。`file-read` 只读 UTF-8 文本，支持分页；`file-write` 整体覆盖并创建缺失父目录；`file-edit` 要求 old_string 唯一匹配。`file-list` 非递归列目录，`file-search` 提供内容正则与文件名 glob 两种模式，跳过 `.git`，保留截断上限。

<!-- BEGIN GENERATED: filesystem-operations -->
Operation 单一契约；输入与输出字段保留 snake_case。（本区块由 `scripts/gen_reference_docs.py` 生成，勿手改；改了会被 `--check` 打回）

### file-read

读取沙箱UTF-8文件；offset默认1，limit默认100，最多500行。/tlon-home只读。

输入：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `file_path` | string | 是 | minLength=1 |
| `offset` | integer | 否 | minimum=1 |
| `limit` | integer | 否 | minimum=1; maximum=500 |

输出：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `file_path` | string | 是 | — |
| `content` | string | 是 | — |
| `total_lines` | integer | 是 | — |
| `read_lines` | integer | 是 | — |
| `offset` | integer | 是 | — |
| `message` | string | 是 | — |

### file-write

创建或覆盖沙箱文件，自动创建父目录。结果未知不可自动重试。

输入：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `file_path` | string | 是 | minLength=1 |
| `content` | string | 是 | — |

输出：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `file_path` | string | 是 | — |
| `bytes_written` | integer | 是 | — |
| `existed_before` | boolean | 是 | — |
| `message` | string | 是 | — |

### file-edit

唯一匹配old_string后替换；零个或多个匹配拒绝。结果未知不可自动重试。

输入：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `file_path` | string | 是 | minLength=1 |
| `old_string` | string | 是 | minLength=1 |
| `new_string` | string | 是 | — |

输出：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `file_path` | string | 是 | — |
| `replaced` | boolean | 是 | — |
| `old_length` | integer | 是 | — |
| `new_length` | integer | 是 | — |
| `message` | string | 是 | — |

### file-list

列目录；目录优先再按名称排序。path默认.，max_entries默认200、最多1000。

输入：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `path` | string | 否 | minLength=1 |
| `max_entries` | integer | 否 | minimum=1; maximum=1000 |

输出：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `path` | string | 是 | — |
| `entries` | array[object] | 是 | — |
| `count` | integer | 是 | — |
| `truncated` | boolean | 是 | — |
| `hint` | string | 否 | — |
| `message` | string | 是 | — |

### file-search

按内容正则或filename glob搜索；忽略.git和二进制内容。path默认.，mode默认content，max_results默认50、最多200。

输入：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `pattern` | string | 是 | — |
| `path` | string | 否 | minLength=1 |
| `mode` | string | 否 | enum=['content', 'filename'] |
| `max_results` | integer | 否 | minimum=1; maximum=200 |

输出：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `mode` | string | 是 | enum=['content', 'filename'] |
| `pattern` | string | 是 | — |
| `path` | string | 是 | — |
| `matches` | array[string / object] | 是 | — |
| `count` | integer | 是 | — |
| `truncated` | boolean | 是 | — |
| `hint` | string | 否 | — |
| `message` | string | 是 | — |
<!-- END GENERATED: filesystem-operations -->

## time

`time-now` 读取宿主机带时区时间，不依赖 Docker，是 read 操作，不能用于纯条件。

<!-- BEGIN GENERATED: time-operations -->
Operation 单一契约；输入与输出字段保留 snake_case。（本区块由 `scripts/gen_reference_docs.py` 生成，勿手改；改了会被 `--check` 打回）

### time-now

Read current host local time with timezone and Unix timestamp.

输入：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|

输出：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `iso` | string | 是 | — |
| `unix` | integer | 是 | — |
| `timezone` | string | 是 | — |
| `weekday` | string | 是 | — |
<!-- END GENERATED: time-operations -->

## shell

`shell-run` 在当前成员的 Docker 沙箱执行 bash。每次使用一个独立执行容器，结束后删除；跨命令持久文件写入 `/workspace`。后台模式立即返回 task_id，`shell-check` 查询同一 ShellService 持有的任务。任务只属于当前成员，已完成结果保留最近 50 个，运行中最多 20 个。任务表不跨进程重启；需长期保存的命令输出应自行写文件，再用 `file-read` 读取。

前台、后台命令都受其 timeout 约束。成员 shutdown 先取消并等待服务任务清理，再关闭执行装置。Shell 属于非幂等 write；中断后无法确认结果时保留 unknown，不能自动重跑。后台任务句柄只能查询当前服务，不能作为重启后的执行证明。

<!-- BEGIN GENERATED: shell-operations -->
Operation 单一契约；输入与输出字段保留 snake_case。（本区块由 `scripts/gen_reference_docs.py` 生成，勿手改；改了会被 `--check` 打回）

### shell-run

在一次性容器执行命令，仅 /workspace 文件持久保留；background 默认为 false，timeout 默认为 60 秒。后台执行返回本成员 task_id。

输入：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `command` | string | 是 | minLength=1 |
| `background` | boolean | 否 | — |
| `timeout` | integer | 否 | minimum=1; maximum=300 |

输出：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `task_id` | string | 是 | — |
| `status` | string | 是 | enum=['running', 'completed'] |
| `exit_code` | integer / null | 是 | — |
| `stdout` | string | 是 | — |
| `stderr` | string | 是 | — |

### shell-check

查询本成员后台任务的状态及输出。

输入：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `task_id` | string | 是 | minLength=1 |

输出：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `task_id` | string | 是 | — |
| `status` | string | 是 | enum=['running', 'completed'] |
| `exit_code` | integer / null | 是 | — |
| `stdout` | string | 是 | — |
| `stderr` | string | 是 | — |
<!-- END GENERATED: shell-operations -->

## memory

<!-- BEGIN GENERATED: memory-operations -->
Operation 单一契约；输入与输出字段保留 snake_case。（本区块由 `scripts/gen_reference_docs.py` 生成，勿手改；改了会被 `--check` 打回）

### memory-remember

记录一条重要记忆，保留现有冲突判断。

输入：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `fact` | string | 是 | minLength=1 |
| `importance` | number | 否 | minimum=0; maximum=1 |

输出：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `claim_id` | string | 是 | — |

### memory-search

搜索自己的长期记忆，来源会话可用 session-read 回读。

输入：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `query` | string | 是 | minLength=1 |
| `limit` | integer | 否 | minimum=1; maximum=100 |

输出：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `results` | string | 是 | — |

### memory-forget

预览匹配记忆，confirm 为 true 时删除。

输入：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `query` | string | 是 | minLength=1 |
| `confirm` | boolean | 否 | — |

输出：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `result` | string | 是 | — |

### session-read

回读自己的历史会话，返回有界原文摘要。

输入：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `session_id` | string | 是 | minLength=1 |

输出：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `session_id` | string | 是 | — |
| `digest` | string | 是 | — |
| `event_count` | integer | 是 | — |
<!-- END GENERATED: memory-operations -->

## tasks

<!-- BEGIN GENERATED: tasks-operations -->
Operation 单一契约；输入与输出字段保留 snake_case。（本区块由 `scripts/gen_reference_docs.py` 生成，勿手改；改了会被 `--check` 打回）

### task-create

创建自己的任务。

输入：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `title` | string | 是 | minLength=1 |
| `description` | string | 否 | — |

输出：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `task` | object | 是 | — |
| `auto_paused` | object / null | 是 | — |

### task-list

列出自己的任务，可按状态筛选。

输入：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `filter` | string | 否 | enum=['all', 'todo', 'doing', 'paused', 'done'] |

输出：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `tasks` | array[object] | 是 | — |
| `stats` | object | 是 | — |

### task-start

开始或恢复任务，自动暂停其他进行中的任务。

输入：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `task_id` | string | 是 | — |

输出：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `task` | object | 是 | — |
| `auto_paused` | object / null | 是 | — |

### task-pause

暂停正在进行的任务并记录原因。

输入：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `task_id` | string | 是 | — |
| `reason` | string | 否 | — |

输出：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `task` | object | 是 | — |
| `auto_paused` | object / null | 是 | — |

### task-complete

完成已开始的任务并记录结果。

输入：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `task_id` | string | 是 | — |
| `result` | string | 否 | — |

输出：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `task` | object | 是 | — |
| `auto_paused` | object / null | 是 | — |

### task-remove

移除已经完成的任务。

输入：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `task_id` | string | 是 | — |

输出：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `task` | object | 是 | — |
| `auto_paused` | object / null | 是 | — |

### task-current

读取当前进行中或最近暂停的任务。

输入：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|

输出：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `task` | object / null | 是 | — |
<!-- END GENERATED: tasks-operations -->

## identity

<!-- BEGIN GENERATED: identity-operations -->
Operation 单一契约；输入与输出字段保留 snake_case。（本区块由 `scripts/gen_reference_docs.py` 生成，勿手改；改了会被 `--check` 打回）

### contacts-read

读取自己的私有联系簿全文，包含自由文本与条目数。

输入：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|

输出：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `content` | string | 是 | — |
| `entries` | integer | 是 | — |
| `message` | string | 是 | — |

### contacts-upsert

按名字添加或更新自己的联系簿条目，保留其他自由文本；description为空则删除。修改在下次组装生效。

输入：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | string | 是 | minLength=1 |
| `address` | string | 否 | — |
| `description` | string | 是 | — |

输出：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | string | 是 | — |
| `address` | string | 否 | — |
| `action` | string | 是 | enum=['added', 'updated', 'deleted'] |
| `message` | string | 是 | — |

### persona-update

全文替换自己的IDENTITY或SOUL；RULES仅人类可改。修改下次组装生效，记录内容哈希；记账失败仍报告已写入及警告。

输入：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `file` | string | 是 | enum=['identity', 'soul'] |
| `content` | string | 是 | minLength=1 |

输出：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `file` | string | 是 | — |
| `path` | string | 是 | — |
| `bytes` | integer | 是 | — |
| `message` | string | 是 | — |
<!-- END GENERATED: identity-operations -->

## observability

<!-- BEGIN GENERATED: observability-operations -->
Operation 单一契约；输入与输出字段保留 snake_case。（本区块由 `scripts/gen_reference_docs.py` 生成，勿手改；改了会被 `--check` 打回）

### metrics-read

读取进程指标快照（重启清零）。sent按封、delivered按收件人成功计数，不等于已读或执行；指标标签展示公开成员ID。

输入：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|

输出：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `metrics` | object | 是 | — |

### mail-stats

读取自己邮箱的收发、首次回复时长和从未回复对象；不读取他人私有邮箱。

输入：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|

输出：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `agent_id` | string | 是 | — |
| `address` | string | 是 | — |
| `total_sent` | integer | 是 | — |
| `total_received` | integer | 是 | — |
| `correspondents` | object | 是 | — |
| `never_replied` | array[string] | 是 | — |
| `never_replied_total` | integer | 是 | — |

### reputation-read

查询公开成员的邮件行为派生声誉，默认自己；致谢按不同成员去重，最多五封高致谢邮件。

输入：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `member_id` | string | 否 | — |

输出：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `member_id` | string | 是 | — |
| `address` | string | 是 | — |
| `endorsements_given` | integer | 是 | — |
| `endorsements_received` | integer | 是 | — |
| `top_endorsed_mails` | array[object] | 是 | — |
| `mail_behavior` | object | 是 | — |

### social-health

查询社会孤儿邮件报告，返回成功及失败扫描成员与最久的前20封候选；未回复不能据此判定未送达或失职。

输入：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|

输出：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `scanned_members` | array[string] | 是 | — |
| `scanned_count` | integer | 是 | — |
| `failed_members` | array[string] | 是 | — |
| `orphan_count` | integer | 是 | — |
| `orphans` | array[object] | 是 | — |

### traces-list

列出公开执行轨迹，默认自己；source允许其他成员或组件，按trace起始来源筛选。默认20条，最大100条。

输入：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `limit` | integer | 否 | minimum=1; maximum=100 |
| `source` | string | 否 | — |

输出：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `traces` | array[object] | 是 | — |
| `count` | integer | 是 | — |
| `source` | string | 是 | — |

### trace-read

按trace_id读取公开完整轨迹及span树；LLM span的usage属性记录token消耗。

输入：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `trace_id` | string | 是 | minLength=1 |

输出：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `trace` | object | 是 | — |
| `span_tree` | object | 是 | — |
<!-- END GENERATED: observability-operations -->

## state

<!-- BEGIN GENERATED: state-operations -->
Operation 单一契约；输入与输出字段保留 snake_case。（本区块由 `scripts/gen_reference_docs.py` 生成，勿手改；改了会被 `--check` 打回）

### state-get

读取自身的结构化状态；不存在返回 null。

输入：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `key` | string | 是 | minLength=1 |

输出：

任意严格 JSON 值。

### state-put

写入自身的严格 JSON 状态并返回该值。

输入：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `key` | string | 是 | minLength=1 |
| `value` | JSON | 是 | — |

输出：

任意严格 JSON 值。
<!-- END GENERATED: state-operations -->

## agents

生命周期工具。实现注入自 `AgentRegistry`（端口见 [tools/agents/ports.py](../../src/tlon/tools/agents/ports.py) 的 `AgentLifecycle`）。

### spawnAgent

**见习成员不可用（信任阶梯，ADR 0022）**——spawn 出生且未获 endorse 的成员装配时不注册此工具；DSL 的 spawn 动作由 SpawnManager 的见习配额闸兜底（见 `session_quota` 拒绝行）。

从 behavior prototype 派生一个新 agent 并立即启动（统一成员模型，ADR 0006：新 agent 是普通社会成员，永不删除——空闲睡眠、档案归档；是否开机启动由原型 spec 的 `:on :boot` 规则派生）。behavior 文件原样拷贝（spec 是 total 的，无 overrides），spawn 不能授予新工具。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `prototype` | string | 是 | prototype 名称（先 `listPrototypes` 查可用项） |
| `name` | string | 是 | 显示名，他人 @ 此名即可对话 |
| `params` | object | 否 | 模板参数，仅当 prototype 声明 `:parameters`；`:string` 传字符串，`:string-list` 传字符串数组 |

成功返回 `agent_id`（注册表生成，形如 `m-<uuid4 hex>`）、`name`、`prototype`、`spawn_depth`。

拒绝行为（`SpawnRejectedError`，`success=False`，`data.reason` 为机器可读 token）：

| reason | 含义 |
|---|---|
| `untrusted_prototype` | 信任闸：staging 区未批准的 prototype 不能 spawn，需人类在 ops 中批准；先于一切预算检查 |
| `insolvent_lineage` | 偿付门槛：目标血脉失去偿付能力（默认政策：总消耗 ≥ 10 万 token 且效率 < 全体均值 × 0.25，由 `agents/policy.py` 的 `solvent?` 政策 hook 判定，阈值可在 `~/.tlon/policy.tlon` 调整，fail-open），血脉靠手上成员挣回 endorse 前不能 spawn；紧随信任门、先于预算检查 |
| `max_activations` | 全局同时运行 agent 数到顶（默认 8） |
| `max_spawn_depth` | spawn 链深度超限（默认 3，非 spawn 成员深度 0） |
| `session_quota` | 同一 (spawner, session) 配额耗尽（默认 2；账本落盘 `~/.tlon/shared/spawn-quota.json`，跨重启存活）。配额按目标血脉适应度倾斜：有效配额 = `ceil(quota × tilt)`，tilt ∈ [0.5, 2.0] 由血脉效率（价值/总 token）相对全体均值派生，冷启动恒 1.0；spawner 是见习成员时（按 spawner_id 判定）再收紧为 `min(effective, 1)`；拒绝 detail 注明实际 tilt 与有效配额（见习 cap 时注明 probation） |
| `config_reload_failed` | 新配置未能被 config provider 加载 |

prototype 不存在或参数校验失败时抛 `PrototypeError`，同样以 `success=False` 返回。session 归属经 structlog contextvars 里的 `session_id` 绑定，用于配额记账。



### listMembers

无参数。返回统一成员寻址黄页（[ADR 0034](../decisions/0034-member-address-directory.md)）：包含当前可投递且未退休的配置成员、Adam 和 SQLite 已登记的通信成员；休眠、离线不影响入选。按真实地址归属去重并按地址排序，外部来源、网关与 postmaster 系统地址不列出。每项恰为 `id` / `name` / `address`，其中 id 是地址 local-part（例如 rika，不是内部 human:rika），不携带人类／agent 标签、职务、组织、可信等级、运行状态或血缘。

黄页是所有成员可查询、引用和转告的公开寻址信息，不认证任何社会身份，也不授权读取、披露他人的私人联系簿、人格文件、邮件正文或记忆。工具与 DSL `member-roster` 使用同一邮局目录。生命周期操作仍由相应工具检查真实目标及权限；黄页不授予启停权限。旧 `listAgents` 已删除，无兼容别名。

### writePrototype

**见习成员不可用（信任阶梯，ADR 0022）**——装配时不注册；`reviewPrototype`（咨询性）对见习成员保留。

创作新 behavior prototype，写入 `~/.tlon/prototypes-staging/`。新 prototype 一律 `trusted=false`（`status=pending_approval`），需人类在 ops 的 Agents 视图（`s` 审批）批准后才能被 `spawnAgent` 使用——LLM 写行为、人类把关的信任闸。原型不带人格（统一成员模型，ADR 0006）：人格在成员创建时播种、之后是成员自主数据（`persona-update` 自改，ADR 0024），不属基因库。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `behavior` | string | 是 | — | 行为规约 EDN 字符串，v1 schema 的 rules-form |
| `display_name` | string | 是 | — | 非空行为显示名，描述步骤、预算、上下文或反思，不赋予职业 |
| `description` | string | 否 | `""` | 行为定位一句话描述 |
| `parent` | string | 否 | — | 父原型名称（谱系；须已存在，trusted 或 staging 皆可）；`generation` 服务端推导为父代 + 1 |
| `proposal_note` | string | 否 | — | 本代变更说明（相对父原型改了什么、为什么） |
| `lessons` | string | 否 | — | 随包教训文本（退休成员蒸馏的经验），落盘为原型的 `LESSONS.md`；实例化时拷贝给新成员，由其 spec 的 `(lessons)` section 注入对话 |

失败行为：EDN 校验失败抛 `PrototypeError`，返回 `success=False` 且 `data.hint` 携带统一的 rules/action schema 提示，供 LLM 修正后重试；未写入任何文件。behavior 是纯数据 spec，无法授予工具。EDN 字段细节见 docs/reference/dsl-reference.md。

另有血脉偿付闸（仅 agent 侧，ADR 0022 政策 hook）：作者血脉失去偿付能力时提案在写入前被拒（`success=False`，staging 无残留，提示先挣回 endorse 再提案）；偿付派生异常时 fail-open 放行。无 prototype 的 manual 成员无血脉可判，不受闸。

另有近亲拒绝闸（仅 agent 侧，ADR 0014 非收敛选择压）：`behavior` 与两区既有原型做规范化文本相似度比较（`find_near_kin`，阈值 0.9），跨血脉命中（换皮级雷同）时提案在写入前被拒（`success=False`，error 指明最相似原型名与相似度，message 指引「做出实质差异或改为提案改进它」，staging 无残留）；提案的声明父本豁免（显式 `parent` 优先，缺省回退作者自身 prototype）——沿血脉的代际微改放行；相似度扫描异常时 fail-open 放行。

成功返回原型 `name` 为系统生成的 p-UUID，后续 parent、评审与批准引用该 ID；携带旧 name 参数明确拒绝，不保留别名。

### proposeBehavior

**见习成员不可用（信任阶梯，ADR 0022）**——装配时不注册。

世代通道的经验入口：基于成员自己的个体经验提出下一代行为原型。与 `writePrototype`（裸写通道，调用方自带全文）的区别在于调用方只给**变更意图与行为显示名**——工具内部读成员自己的 `config/behavior.tlon` 底稿、`get_memory_digest` 记忆快照（claims）与 `memory/lessons.md` 蒸馏教训（存在才带），连同意志喂给一次独立的起草 LLM 调用（`purpose="propose"`、timeout 60s；采样参数不配置，供应商默认生效），产出完整的新 spec 后走同一个 `write_staging_prototype` 闸落盘：父原型固定为成员自己的 prototype（`AgentConfig.prototype`，无则为新根），`proposal_note` 记意图，lessons 随包落 `LESSONS.md`。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `intent` | string | 是 | — | 变更意图：想改什么、为什么（自然语言，记为 `proposal_note`） |
| `display_name` | string | 是 | — | 人类审批时看到的显示名 |
| `description` | string | 否 | `""` | 行为定位一句话描述 |

成功返回 `{name, parent, generation, trusted=false, status="pending_approval"}`——与 `writePrototype` 同一信任闸：人类在 ops/WebUI 审批后才能 `spawnAgent` 派生新世代。

失败行为（均 `success=False`，工具内不重试——外层 ReAct 循环即重试机制）：血脉偿付闸拒绝（与 `writePrototype` 同一道闸，排在起草调用之前——被闸时不消耗起草 token，staging 无残留；fail-open）；起草响应剥离 ``` 代码围栏后找不到 EDN map；近亲拒绝（与 `writePrototype` 同一道闸，排在起草**之后**——被比较的是草稿文本；父本恒为成员自己的 prototype 且豁免，跨血脉命中时 staging 无残留；fail-open）；起草产出非法 spec（`PrototypeError` 原文回喂 + `data.hint` schema 提示，staging 无残留）；系统生成的 ID 冲突；成员无 `behavior.tlon` 底稿。

原型技术 ID 由系统分配，name 不再是输入；display_name 必须是非空行为标签，不能把原型当作职业或人格模板。

### proposeCapability

**见习成员不可用（信任阶梯，ADR 0022）**——装配时不注册。

能力通道的经验入口（issue #30，docs/design/capabilities.md「#30：突变晋升通道」）：基于成员自己的个体经验提出一个新能力词——突变出生即 tlosure artifact，`proposeBehavior` 的能力版。调用方只给**能力意图与词名**——工具内部读 `get_memory_digest` 记忆快照（claims）与 `memory/lessons.md` 蒸馏教训（存在才带；**不读 behavior.tlon 底稿**，能力起草不需要行为规约），加上既有受信词清单（assembly 侧渲染注入，非空才拼进 prompt——防重复造词 + 风格示范），连同意图喂给一次独立的起草 LLM 调用（`purpose="propose"`、timeout 60s），产出完整的 capability artifact 后走 `write_staging_capability` 闸落盘：schema 校验 + 命名空间/纯度准入 + `:examples` 沙盒断言，`proposal_note` 记意图、`:author` 记成员。无 near-kin 检查（与 proposeBehavior 不同）：能力词是几十词的小词汇表，同名即硬拒、评审见全文与断言，不另造相似度子系统。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `intent` | string | 是 | — | 能力意图：想造什么词、解决什么问题（自然语言，记为 `proposal_note`） |
| `name` | string | 是 | — | 词名（字母起头，后接字母/数字/`-`/`_`；同名即拒——改进既有词请换新名） |

成功返回 `{name, trusted=false, status="pending_approval"}`——与原型通道同一信任模型：人类在 ops/WebUI 的能力审批批准后，经词汇桥全社会成员下次 wake 即可在自己的 spec 里组合它。

失败行为（均 `success=False`，工具内不重试——外层 ReAct 循环即重试机制）：血脉偿付闸拒绝（与 `proposeBehavior` 同一道闸，排在起草调用之前；fail-open）；起草响应剥离 ``` 代码围栏后找不到 EDN map；起草产物的 `:name` 与指定词名不一致（回喂重起草）；artifact 校验失败（`CapabilityError` 原文回喂 + `data.hint` artifact schema 提示，staging 无残留——含 `:examples` 断言失败）；与受信库/staging 同名冲突。

## prototypes

### listPrototypes

无参数。列出 `~/.tlon/prototypes/`（启动时播种、用户可编辑的原型库）里的全部 behavior prototype，返回每项的 `name`/`display_name`/`description`/`trusted`/`parent`/`generation`（后两个是谱系字段）。空库时 `success=True, data=[]`。

### getPrototypeLineage

查询一个 prototype 的谱系：沿 `:parent` 链向上走到根，谱系是派生的、不另落盘。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | string | 是 | prototype 名称（trusted 库或 staging 区皆可） |

成功时 `data.lineage` 为根在前的代际列表，每代 `{name, generation, author, proposal_note, trusted, parent, diff_from_parent, broken}`：`diff_from_parent` 是相邻两代 `behavior.tlon` 的 unified diff（difflib 文本 diff，根代为 `null`）；父原型已删除/改名时链在断点处截断，最老可达的一代标 `broken=true`。prototype 不存在返回 `success=False`。

### reviewPrototype

评审一个 staging 区的原型提案（异质评审，审批社会化的第一步）。调用方只点名提案——verdict 不由调用方给：工具内部用评审者自己的 `llm_client` 做一次专项评审调用（`purpose="review"`、timeout 60s；采样参数不配置，供应商默认生效），材料是提案的 behavior.tlon 全文、随包 `LESSONS.md`（如有）与谱系链（含每代 diff、`proposal_note`）；响应首行必须是 `通过`/`不通过`/`弃权` 三者之一，其后为理由。合法 verdict 追加记入提案目录的 `REVIEW.md`（每条含时间、评审者 id、评审者模型——`llm_client.config.model_name`，异质性来源），多次评审累积不覆盖，approve 时随整目录晋级 trusted。评审是咨询性的：不自动晋级，人类在审批详情（`reviews` 字段）里读到记录再做决定。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | string | 是 | staging 区提案名称（trusted 库原型不可评审） |

成功返回 `{name, verdict, reviewer, model}`。失败行为（均 `success=False`，工具内不重试——外层 ReAct 循环即重试机制）：提案不存在或不在 staging（评审调用不发生）；评审响应首行不是合法 verdict（不写 `REVIEW.md`）。