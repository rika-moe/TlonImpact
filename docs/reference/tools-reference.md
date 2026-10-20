# Tlon 工具手册

agent 通过 OpenAI function calling 调用的全部工具。所有参数与结果以 [tlon.operations](../../src/tlon/operations) 中的 Operation 定义为准，工具是该定义的协议投影。本文只描述当前实现。

## 总述

### Operation 与 Tool 投影

宿主能力唯一权威是 [tlon.operations](../../src/tlon/operations) 中的 Operation：描述、严格 JSON Schema 输入/输出、效果、重试语义、处理器和控制元数据。领域服务拥有业务逻辑与资源生命周期。

[OperationTool](../../src/tlon/operations/adapters.py) 只把同一定义投影为 OpenAI function calling 协议，并通过 `execute_context(arguments, context)` 调用共享 OperationExecutor。参数和结果统一由 jsonschema 校验，DSL 和模型调用得到同一领域结果；旧 Tool 基类、浅层校验、业务 execute 和空生命周期钩子已删除。

[ToolResult](../../src/tlon/tools/base.py) 保留协议回执 `success/data/error/message`；`ToolExecutor` 负责工具发现、跟踪与异常回执，领域执行权威仍是 OperationExecutor。

### ToolExecutor 执行模型

[agents/tool_executor.py](../../src/tlon/agents/tool_executor.py) 的 `ToolExecutor` 是每 agent 一个的工具注册表：直接 async 调用，不经过 EventBus。`execute(tool_name, arguments)` 包住一层 tracing span（`tool.{name}`，参数截断到 200 字符），三类失败都转成 `ToolResult(success=False)` 而不是抛异常：工具未注册、参数校验失败、`execute` 内部抛异常（记日志后返回 `error="Execution error: ..."`）。

会话循环里的并发分发不在 ToolExecutor，而在引擎侧：

- act stage（[agents/stages/act.py](../../src/tlon/agents/stages/act.py) 的 `act_stage_step`）是纯变换，只把本轮 LLM 的 tool calls 声明进 `StageOutcome.tool_dispatches`，自己不执行。
- [agents/loop_driver.py](../../src/tlon/agents/loop_driver.py) 的 `LoopDriver.execute_one_tool` 为每个 tool call 起一个后台 asyncio task 并发执行，外层套 `asyncio.wait_for`。等待预算按「模型可见的 schema 必须可兑现」解析：工具自己在 schema 里声明了 `timeout` 参数（如 `shell-run`）时，未显式传 timeout 时读取操作的 default_timeout（shell-run 为 60 秒）；引擎等「声明值 + `_TOOL_TIMEOUT_GRACE`（10 秒）宽限」——工具自身是限时权威，wait_for 只兜底工具挂死；未声明 `timeout` 的工具用引擎兜底 `per_tool_timeout`（来自 agent 配置的 `tool.per_timeout`，[base/config.py](../../src/tlon/base/config.py) 的 `ToolConfig`，默认 30 秒）。超时返回 `success=False`，error 指明真正生效的限制来自哪层（工具声明值 + 引擎宽限，或引擎 per-tool 兜底）。
- 超大输出 spill：结果在收割前先过 [tools/spill.py](../../src/tlon/tools/spill.py) 的 `ToolSpill`——`data` 序列化超过 16k 字符即整个落盘到 `workspace/spills/{session_id}/{tool_call_id}.json`（缩进 JSON，可分页），模型与 session 日志只见到 locator（路径/大小/preview/检索提示），可用 `file-read` 的 offset/limit 读回（workspace 即容器工作目录的 bind mount）。写盘失败回退原始结果。
- 收割：`collect_session_results` 把已完成 task 的结果交给 `act_stage_collect`，结果以 `{"role": "tool", ...}` 消息追加回 LLM 上下文（`ToolResult.to_dict()` 的 JSON 序列化）。
- 结束工具 `finish-turn`、`sleep-until`、`exit-agent` 必须各自单独占一轮，混用其他工具整轮拒绝。邮件工具不负责结束工作，发信后检查结果，再单独请求结束或睡眠。

### 工具装配

每个成员经 [assembly.py](../../src/tlon/agents/assembly.py) 绑定私有领域服务并构造 OperationRegistry，再为所有注册操作建立 OperationTool。L0 无装置操作，L1 提供文件操作，L2 增加 Shell；时间与成员领域操作不依赖装置。见习成员不装配 `member-spawn`、`prototype-write`、`behavior-propose`、`capability-propose`；其他操作仍按成员身份绑定。

模型调用、原型创作/评审、成员生命周期也使用同一注册表。`finish-turn`、`sleep-until`、`exit-agent` 的调度声明由控制元数据标识；会话引擎负责完成/睡眠/退出的应用，处理器本身不直接操作 session。

### 注册清单

<!-- BEGIN GENERATED: tool-registry -->
满配成员注册 52 个工具（由共享 Operation 按域列出；L0 不注册 7 个装置操作，L1 注册 5 个文件操作，L2 再增加 2 个 Shell 操作；时间不依赖装置）：（本区块由 `scripts/gen_reference_docs.py` 生成，勿手改；改了会被 `--check` 打回）

| # | 工具名 | 实现 | 域 |
|---|---|---|---|
| 1 | `mail-send` | [operations/mail.py](../../src/tlon/operations/mail.py) 的 `OperationTool` 投影 | mail |
| 2 | `mail-forward` | [operations/mail.py](../../src/tlon/operations/mail.py) 的 `OperationTool` 投影 | mail |
| 3 | `mail-check-inbox` | [operations/mail.py](../../src/tlon/operations/mail.py) 的 `OperationTool` 投影 | mail |
| 4 | `mail-search` | [operations/mail.py](../../src/tlon/operations/mail.py) 的 `OperationTool` 投影 | mail |
| 5 | `mail-mark-read` | [operations/mail.py](../../src/tlon/operations/mail.py) 的 `OperationTool` 投影 | mail |
| 6 | `file-read` | [operations/filesystem.py](../../src/tlon/operations/filesystem.py) 的 `OperationTool` 投影 | filesystem |
| 7 | `file-write` | [operations/filesystem.py](../../src/tlon/operations/filesystem.py) 的 `OperationTool` 投影 | filesystem |
| 8 | `file-edit` | [operations/filesystem.py](../../src/tlon/operations/filesystem.py) 的 `OperationTool` 投影 | filesystem |
| 9 | `file-list` | [operations/filesystem.py](../../src/tlon/operations/filesystem.py) 的 `OperationTool` 投影 | filesystem |
| 10 | `file-search` | [operations/filesystem.py](../../src/tlon/operations/filesystem.py) 的 `OperationTool` 投影 | filesystem |
| 11 | `shell-run` | [operations/shell.py](../../src/tlon/operations/shell.py) 的 `OperationTool` 投影 | shell |
| 12 | `shell-check` | [operations/shell.py](../../src/tlon/operations/shell.py) 的 `OperationTool` 投影 | shell |
| 13 | `time-now` | [operations/time.py](../../src/tlon/operations/time.py) 的 `OperationTool` 投影 | time |
| 14 | `memory-remember` | [operations/memory.py](../../src/tlon/operations/memory.py) 的 `OperationTool` 投影 | memory |
| 15 | `memory-search` | [operations/memory.py](../../src/tlon/operations/memory.py) 的 `OperationTool` 投影 | memory |
| 16 | `memory-forget` | [operations/memory.py](../../src/tlon/operations/memory.py) 的 `OperationTool` 投影 | memory |
| 17 | `session-read` | [operations/memory.py](../../src/tlon/operations/memory.py) 的 `OperationTool` 投影 | memory |
| 18 | `task-create` | [operations/tasks.py](../../src/tlon/operations/tasks.py) 的 `OperationTool` 投影 | tasks |
| 19 | `task-list` | [operations/tasks.py](../../src/tlon/operations/tasks.py) 的 `OperationTool` 投影 | tasks |
| 20 | `task-start` | [operations/tasks.py](../../src/tlon/operations/tasks.py) 的 `OperationTool` 投影 | tasks |
| 21 | `task-pause` | [operations/tasks.py](../../src/tlon/operations/tasks.py) 的 `OperationTool` 投影 | tasks |
| 22 | `task-complete` | [operations/tasks.py](../../src/tlon/operations/tasks.py) 的 `OperationTool` 投影 | tasks |
| 23 | `task-remove` | [operations/tasks.py](../../src/tlon/operations/tasks.py) 的 `OperationTool` 投影 | tasks |
| 24 | `task-current` | [operations/tasks.py](../../src/tlon/operations/tasks.py) 的 `OperationTool` 投影 | tasks |
| 25 | `contacts-read` | [operations/identity.py](../../src/tlon/operations/identity.py) 的 `OperationTool` 投影 | identity |
| 26 | `contacts-upsert` | [operations/identity.py](../../src/tlon/operations/identity.py) 的 `OperationTool` 投影 | identity |
| 27 | `persona-update` | [operations/identity.py](../../src/tlon/operations/identity.py) 的 `OperationTool` 投影 | identity |
| 28 | `metrics-read` | [operations/observability.py](../../src/tlon/operations/observability.py) 的 `OperationTool` 投影 | observability |
| 29 | `mail-stats` | [operations/observability.py](../../src/tlon/operations/observability.py) 的 `OperationTool` 投影 | observability |
| 30 | `reputation-read` | [operations/observability.py](../../src/tlon/operations/observability.py) 的 `OperationTool` 投影 | observability |
| 31 | `social-health` | [operations/observability.py](../../src/tlon/operations/observability.py) 的 `OperationTool` 投影 | observability |
| 32 | `traces-list` | [operations/observability.py](../../src/tlon/operations/observability.py) 的 `OperationTool` 投影 | observability |
| 33 | `trace-read` | [operations/observability.py](../../src/tlon/operations/observability.py) 的 `OperationTool` 投影 | observability |
| 34 | `state-get` | [operations/state.py](../../src/tlon/operations/state.py) 的 `OperationTool` 投影 | state |
| 35 | `state-put` | [operations/state.py](../../src/tlon/operations/state.py) 的 `OperationTool` 投影 | state |
| 36 | `member-spawn` | [operations/lifecycle.py](../../src/tlon/operations/lifecycle.py) 的 `OperationTool` 投影 | lifecycle |
| 37 | `member-list` | [operations/lifecycle.py](../../src/tlon/operations/lifecycle.py) 的 `OperationTool` 投影 | lifecycle |
| 38 | `member-self-state` | [operations/lifecycle.py](../../src/tlon/operations/lifecycle.py) 的 `OperationTool` 投影 | lifecycle |
| 39 | `member-start` | [operations/lifecycle.py](../../src/tlon/operations/lifecycle.py) 的 `OperationTool` 投影 | lifecycle |
| 40 | `member-stop` | [operations/lifecycle.py](../../src/tlon/operations/lifecycle.py) 的 `OperationTool` 投影 | lifecycle |
| 41 | `member-start-all` | [operations/lifecycle.py](../../src/tlon/operations/lifecycle.py) 的 `OperationTool` 投影 | lifecycle |
| 42 | `finish-turn` | [operations/lifecycle.py](../../src/tlon/operations/lifecycle.py) 的 `OperationTool` 投影 | lifecycle |
| 43 | `sleep-until` | [operations/lifecycle.py](../../src/tlon/operations/lifecycle.py) 的 `OperationTool` 投影 | lifecycle |
| 44 | `exit-agent` | [operations/lifecycle.py](../../src/tlon/operations/lifecycle.py) 的 `OperationTool` 投影 | lifecycle |
| 45 | `prototype-write` | [operations/evolution.py](../../src/tlon/operations/evolution.py) 的 `OperationTool` 投影 | evolution |
| 46 | `behavior-propose` | [operations/evolution.py](../../src/tlon/operations/evolution.py) 的 `OperationTool` 投影 | evolution |
| 47 | `capability-propose` | [operations/evolution.py](../../src/tlon/operations/evolution.py) 的 `OperationTool` 投影 | evolution |
| 48 | `prototype-list` | [operations/evolution.py](../../src/tlon/operations/evolution.py) 的 `OperationTool` 投影 | evolution |
| 49 | `prototype-staging-list` | [operations/evolution.py](../../src/tlon/operations/evolution.py) 的 `OperationTool` 投影 | evolution |
| 50 | `prototype-lineage` | [operations/evolution.py](../../src/tlon/operations/evolution.py) 的 `OperationTool` 投影 | evolution |
| 51 | `prototype-review` | [operations/evolution.py](../../src/tlon/operations/evolution.py) 的 `OperationTool` 投影 | evolution |
| 52 | `model-call` | [operations/model.py](../../src/tlon/operations/model.py) 的 `OperationTool` 投影 | model |
<!-- END GENERATED: tool-registry -->

上表是正式成员的满配。见习成员不注册 member-spawn、prototype-write、behavior-propose、capability-propose 四个生殖/演化工具；所有成员均可使用绑定自身的四个调度工具。L0 不注册 shell/filesystem 七个装置工具，L1 不注册 shell 两个工具。

依赖倒置：`tlon.tools` 不许反向依赖 `tlon.agents`/`tlon.prototypes`，agents 域与 prototypes 域工具的端口（`AgentLifecycle` Protocol、`SpawnRejectedError`、`StopRejectedError`）声明在 [lifecycle/ports.py](../../src/tlon/lifecycle/ports.py)，原型端口（`PrototypeError`、`PrototypeSummary`）声明在 [evolution/ports.py](../../src/tlon/evolution/ports.py)；装配时由 `AgentRegistry` 把自身和原型库函数注入工具实例。

shell 与 filesystem 工具共享同一个 `DockerRuntime` 实例（[sandbox/docker_runtime.py](../../src/tlon/sandbox/docker_runtime.py)）。文件柜容器创建后**从不启动**（issue #25），首次文件 I/O 时惰性创建/按名重挂；自 issue #40 起五个 filesystem 工具全部经 get_archive/put_archive 读写（list/search 把目录 tar 拉回 host 侧解包，write 把缺失父目录作为 tar 目录条目带入），容器内零命令执行。Docker daemon 不可用时这些工具返回 `error="Docker runtime not initialized"`，agent 本体照常启动。`docker.level: L0` 的成员（issue #26）完全不装配这两域工具——无执行装置即无工具，不存在「工具有但装置为 None」的状态；`docker.level: L1` 的成员（issue #40）只有 filesystem 五件，shell 工具不注册且 `run()` 由装置层直接拒绝。

## mail

五个邮件操作通过 `OperationTool` 向模型暴露，通过 `DSLAdapter` 向程序暴露，共享 [Operation 定义](../../src/tlon/operations/mail.py) 与成员作用域 [MemberMailService](../../src/tlon/organization/mail/member_mail.py)。只有实际投递提交后才返回成功回执，LoopDriver 不再按工具名二次提交。

- 所有邮箱读取、标读、回复与转发引用都限定为调用成员自己的邮箱；发件身份由宿主绑定。
- 回复显式传 `in_reply_to`，线程从本成员邮箱中的引用推导；不从触发邮件隐式补收件人。
- 转发显式传 `mail_id`；自动生成 Fwd 主题、归因头与原文，外部邮件附不可信提醒；另起线程。不接受主题覆盖。
- `endorsing` 验证本成员邮箱引用并禁止自我致谢；mentor cc 在共享邮件服务处理。
- 接受证明本地入箱；gateway 回执不保证外部最终送达。拒收返回 `RejectedError`，结果未知返回 `UnknownOutcomeError`；两者保留结构化 data，unknown 携带 request_id。结果未知必须用同一 request_id 重试，同键不同内容拒绝。
- 发信、转发和标读均不结束工作；完成后下一轮单独调用 finish-turn、sleep-until 或 exit-agent。

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

## lifecycle

<!-- BEGIN GENERATED: lifecycle-operations -->
Operation 单一契约；输入与输出字段保留 snake_case。（本区块由 `scripts/gen_reference_docs.py` 生成，勿手改；改了会被 `--check` 打回）

### member-spawn

从可信原型创建并启动成员；见习成员不可调用，受注册表预算限制。

输入：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `prototype` | string | 是 | minLength=1 |
| `name` | string | 是 | minLength=1 |
| `params` | object | 否 | — |

输出：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `agent_id` | string | 是 | — |
| `name` | string | 是 | — |
| `prototype` | string | 是 | — |
| `spawn_depth` | integer | 是 | — |

### member-list

查询公开成员寻址黄页，仅包含id、名字与邮件地址。

输入：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|

输出：

```json
{
  "type": "array",
  "items": {
    "type": "object",
    "properties": {
      "id": {
        "type": "string"
      },
      "name": {
        "type": "string"
      },
      "address": {
        "type": "string"
      }
    },
    "required": [
      "id",
      "name",
      "address"
    ],
    "additionalProperties": false
  }
}
```

### member-self-state

查看自己的调度状态与唤醒时间。

输入：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|

输出：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|

### member-start

请求启动成员；普通成员只能管理自己，adam可代管。

输入：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `agent_id` | string | 是 | — |
| `reason` | string | 否 | — |

输出：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `action` | string | 是 | enum=['start', 'stop'] |
| `agent_id` | string | 是 | — |
| `reason` | string | 是 | — |

### member-stop

请求停止成员；普通成员只能管理自己，adam可代管。

输入：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `agent_id` | string | 是 | — |
| `reason` | string | 否 | — |

输出：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `action` | string | 是 | enum=['start', 'stop'] |
| `agent_id` | string | 是 | — |
| `reason` | string | 是 | — |

### member-start-all

adam请求启动所有声明boot规则的成员。

输入：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `reason` | string | 否 | — |

输出：

```json
{
  "type": "array",
  "items": {
    "type": "object",
    "properties": {
      "action": {
        "type": "string",
        "enum": [
          "start",
          "stop"
        ]
      },
      "agent_id": {
        "type": "string"
      },
      "reason": {
        "type": "string"
      }
    },
    "required": [
      "action",
      "agent_id",
      "reason"
    ],
    "additionalProperties": false
  }
}
```

### finish-turn

结束本轮工作，排空后进入等待；只能独立调用。

输入：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|

输出：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `scheduling_request` | object | 是 | — |

### sleep-until

结束本轮并睡至未来带时区时间，期间来信只入箱；只能独立调用。

输入：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `wake_at` | string | 是 | — |
| `reason` | string | 否 | — |

输出：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `scheduling_request` | object | 是 | — |

### exit-agent

永久退出并拒收新邮件，保留历史；只能独立调用。

输入：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `reason` | string | 否 | — |

输出：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `scheduling_request` | object | 是 | — |
<!-- END GENERATED: lifecycle-operations -->

## evolution

<!-- BEGIN GENERATED: evolution-operations -->
Operation 单一契约；输入与输出字段保留 snake_case。（本区块由 `scripts/gen_reference_docs.py` 生成，勿手改；改了会被 `--check` 打回）

### prototype-write

创作一个新的 behavior prototype（纯行为规约，无人格），写入 staging 区。规则统一使用 on、when、actions；会话通过 run-loop 动作调用。新 prototype 默认不可信：需人类批准（ops/WebUI）后才能被 member-spawn 使用。behavior 是 v1 schema 的纯数据 EDN（只描述行为，无法授予工具）。可声明 :parameters 以支持 spawn 时参数化。可选 parent 记录谱系（从哪个既有原型派生），generation 由服务端推导。

输入：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `behavior` | string | 是 | 行为规约（EDN 字符串）。统一 on/when/actions；会话使用 run-loop 动作。可选 :parameters，引用变量用 ${name}。 |
| `display_name` | string | 是 | 必填行为标签，描述怎样行动；不赋予职业、职位或社会身份 |
| `description` | string | 否 | 一句话描述这个原型的行为定位 |
| `parent` | string | 否 | 父原型系统 ID（本原型从哪个原型派生而来；留空表示新的根原型）。父原型必须已存在（trusted 库或 staging 区皆可），generation 由服务端自动推导为父代 + 1 |
| `proposal_note` | string | 否 | 本代变更说明：相对父原型改了什么、为什么 |
| `lessons` | string | 否 | 随包教训：退休成员蒸馏出的经验，落盘为原型的 LESSONS.md；实例化时拷贝给新成员，由 spec 的 (lessons) context section 注入对话 |

输出：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | string | 是 | — |
| `parent` | string / null | 是 | — |
| `generation` | integer | 是 | — |
| `trusted` | boolean | 是 | — |
| `status` | string | 是 | enum=['pending_approval'] |
| `display_name` | string | 是 | — |

### behavior-propose

基于自己的个体经验提出下一代行为原型（世代通道的经验入口）。只需给出变更意图与行为显示名：工具内部读你当前的 behavior.tlon 与记忆（claims 与蒸馏教训），由一次独立的起草调用产出完整的新 spec，并以你自己的 prototype 为父原型写入 staging 区（proposal_note 记意图、lessons 随包）。与 prototype-write 的区别：那是裸写通道（自带全文），这里由经验驱动起草。提案不可信：需人类批准（ops/WebUI）后才能 member-spawn 派生新世代。

输入：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `intent` | string | 是 | 变更意图：想改什么、为什么（自然语言，记为 proposal_note） |
| `display_name` | string | 是 | 必填行为标签，描述怎样行动；不赋予职业、职位或社会身份 |
| `description` | string | 否 | 一句话描述这个原型的行为定位 |

输出：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | string | 是 | — |
| `parent` | string / null | 是 | — |
| `generation` | integer | 是 | — |
| `trusted` | boolean | 是 | — |
| `status` | string | 是 | enum=['pending_approval'] |

### capability-propose

基于自己的个体经验提出一个新能力词（capability，突变晋升通道的经验入口）。只需给出能力意图与词名：工具内部读你的记忆（claims 与蒸馏教训），由一次独立的起草调用产出完整的 capability artifact（tlosure (fn [...] ...) 实现），经 staging 闸写入（proposal_note 记意图）。同名即拒——想改进既有词请换个新名字。提案不可信：需人类批准（ops/WebUI）批准后，全社会成员下次 wake 才能在自己的 spec 里组合它。

输入：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `intent` | string | 是 | 能力意图：想造一个什么词、解决什么问题（自然语言，记为 proposal_note） |
| `name` | string | 是 | 词名（字母起头，后接字母/数字/'-'/'_'；同名即拒，改进既有词请换新名） |

输出：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | string | 是 | — |
| `trusted` | boolean | 是 | — |
| `status` | string | 是 | enum=['pending_approval'] |

### prototype-list

列出可用的 behavior prototype（行为原型）。每个 prototype 是一套预制的行为规约，可作为创建新成员的行为模板；不携带人格，也不赋予职业或职位。返回系统生成的 ID、行为显示名与描述。

输入：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|

输出：

```json
{
  "type": "array",
  "items": {
    "type": "object",
    "properties": {
      "name": {
        "type": "string"
      },
      "display_name": {
        "type": "string"
      },
      "description": {
        "type": "string"
      },
      "trusted": {
        "type": "boolean"
      },
      "parent": {
        "type": [
          "string",
          "null"
        ]
      },
      "generation": {
        "type": "integer"
      }
    },
    "required": [
      "name",
      "display_name",
      "description",
      "trusted",
      "parent",
      "generation"
    ],
    "additionalProperties": false
  }
}
```

### prototype-staging-list

列出原型暂存区（staging）里等待人类批准的 prototype 提案（只读）。这些是 agent 写好但尚未获人类信任批准（:trusted false）的原型提案，需要人类决策：批准后才进入可信原型库、可被 spawn。返回名称、显示名、描述与作者（author）。

输入：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|

输出：

```json
{
  "type": "array",
  "items": {
    "type": "object",
    "properties": {
      "name": {
        "type": "string"
      },
      "display_name": {
        "type": "string"
      },
      "description": {
        "type": "string"
      },
      "author": {
        "type": [
          "string",
          "null"
        ]
      },
      "parent": {
        "type": [
          "string",
          "null"
        ]
      },
      "generation": {
        "type": "integer"
      }
    },
    "required": [
      "name",
      "display_name",
      "description",
      "author",
      "parent",
      "generation"
    ],
    "additionalProperties": false
  }
}
```

### prototype-lineage

查询一个 prototype 的谱系：沿 :parent 链向上走到根原型，返回每一代的名称、代数（generation）、作者、变更说明（proposal_note）、trusted 标志，以及相邻两代 behavior.tlon 的 unified diff。父原型已被删除/改名时链在断点处截断并标注 broken。

输入：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | string | 是 | prototype 名称（trusted 库或 staging 区皆可） |

输出：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `lineage` | array[object] | 是 | — |

### prototype-review

评审一个 staging 区的原型提案（异质评审）。只点名提案——结论不由你当场给：工具内部用你自己的模型做一次专项评审调用，读取提案的 behavior 全文、随包教训与谱系链（含每代 diff）后产出 verdict（通过/不通过/弃权）与理由，追加记入提案目录的 REVIEW.md（含时间、你的 id 与你的模型）。评审是咨询性的：不自动晋级，人类审批时参考这些记录做决定。提案不存在或不在 staging 时失败。

输入：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | string | 是 | staging 区提案名称（trusted 库原型不可评审） |

输出：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | string | 是 | — |
| `verdict` | string | 是 | enum=['通过', '不通过', '弃权'] |
| `reviewer` | string | 是 | — |
| `model` | string | 是 | — |
<!-- END GENERATED: evolution-operations -->

## model

<!-- BEGIN GENERATED: model-operations -->
Operation 单一契约；输入与输出字段保留 snake_case。（本区块由 `scripts/gen_reference_docs.py` 生成，勿手改；改了会被 `--check` 打回）

### model-call

使用自己的模型与系统提示词生成文本，费用计入自己；不执行工具调用。

输入：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `prompt` | string | 是 | — |
| `max_tokens` | integer | 是 | minimum=1 |

输出：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `content` | string | 是 | — |
| `usage` | object | 是 | — |
<!-- END GENERATED: model-operations -->
