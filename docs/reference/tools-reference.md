# Tlon 工具手册

Tool 是宿主 Operation 的模型协议投影；普通模块函数不注册成 Tool。参数、效果与结果以 `tlon.operations` 中的定义为准。

## 执行与恢复

OperationTool 经 OperationExecutor 执行领域服务；ToolExecutor 提供成员目录、跟踪与协议投影。仅输入错误和明确拒绝转为普通失败；unknown、预算、截止、取消和恢复失配上浮运行器，不能由模型自行重试。

model-call 返回建议，tool-execute 逐叶执行并返回真实结果。每个叶调用拥有稳定身份和持久回执；已有回执优先还原。成员写 gate 保证写序，独立读取可并发。程序与工具共享根预算与授权，不能在业务参数中改身份。

普通标准库构造完整 assistant/tool 消息组，压缩保留提交事实和未执行草稿的区别。没有 stage dispatch/collect、隐式 spill 或 session journal 恢复。`finish-turn` 是局部纯信号；根入口返回值决定成员调度。

## 工具装配

装配目录依成员身份、信任、执行装置和领域服务确定。冻结模型请求保留目录快照；实际新效果仍校验当前目录和领域权限。固定目录不是永久授权或任意外部操作 exactly-once 的保证。

模型、压缩、显式核验和嵌套调用共享程序 output 根，可靠成功用量结算后释放预留差额，未知用量保留预留。长期笔记通过普通文件操作显式维护，无自动记忆模型根；total 账本语义保留。上下文估算将完整工具 JSON 作为文本计数，支持布尔 schema 节点；估算不是 provider 用量保证。

## 注册清单

<!-- BEGIN GENERATED: tool-registry -->
满配成员注册 52 个工具（由共享 Operation 按域列出；L0/L1/L2 均注册 5 个文件操作，自己的 workspace 无需装置；L2 再增加 2 个 Shell 操作；时间不依赖装置）：（本区块由 `scripts/gen_reference_docs.py` 生成，勿手改；改了会被 `--check` 打回）

| # | 工具名 | 实现 | 域 |
|---|---|---|---|
| 1 | `member-context` | [operations/context.py](../../src/tlon/operations/context.py) 的 `OperationTool` 投影 | context |
| 2 | `mail-send` | [operations/mail.py](../../src/tlon/operations/mail.py) 的 `OperationTool` 投影 | mail |
| 3 | `mail-forward` | [operations/mail.py](../../src/tlon/operations/mail.py) 的 `OperationTool` 投影 | mail |
| 4 | `mail-check-inbox` | [operations/mail.py](../../src/tlon/operations/mail.py) 的 `OperationTool` 投影 | mail |
| 5 | `mail-search` | [operations/mail.py](../../src/tlon/operations/mail.py) 的 `OperationTool` 投影 | mail |
| 6 | `mail-mark-read` | [operations/mail.py](../../src/tlon/operations/mail.py) 的 `OperationTool` 投影 | mail |
| 7 | `file-read` | [operations/filesystem.py](../../src/tlon/operations/filesystem.py) 的 `OperationTool` 投影 | filesystem |
| 8 | `file-write` | [operations/filesystem.py](../../src/tlon/operations/filesystem.py) 的 `OperationTool` 投影 | filesystem |
| 9 | `file-edit` | [operations/filesystem.py](../../src/tlon/operations/filesystem.py) 的 `OperationTool` 投影 | filesystem |
| 10 | `file-list` | [operations/filesystem.py](../../src/tlon/operations/filesystem.py) 的 `OperationTool` 投影 | filesystem |
| 11 | `file-search` | [operations/filesystem.py](../../src/tlon/operations/filesystem.py) 的 `OperationTool` 投影 | filesystem |
| 12 | `shell-run` | [operations/shell.py](../../src/tlon/operations/shell.py) 的 `OperationTool` 投影 | shell |
| 13 | `shell-check` | [operations/shell.py](../../src/tlon/operations/shell.py) 的 `OperationTool` 投影 | shell |
| 14 | `time-now` | [operations/time.py](../../src/tlon/operations/time.py) 的 `OperationTool` 投影 | time |
| 15 | `run-read` | [operations/runs.py](../../src/tlon/operations/runs.py) 的 `OperationTool` 投影 | run |
| 16 | `run-previous` | [operations/runs.py](../../src/tlon/operations/runs.py) 的 `OperationTool` 投影 | run |
| 17 | `task-create` | [operations/tasks.py](../../src/tlon/operations/tasks.py) 的 `OperationTool` 投影 | tasks |
| 18 | `task-list` | [operations/tasks.py](../../src/tlon/operations/tasks.py) 的 `OperationTool` 投影 | tasks |
| 19 | `task-start` | [operations/tasks.py](../../src/tlon/operations/tasks.py) 的 `OperationTool` 投影 | tasks |
| 20 | `task-pause` | [operations/tasks.py](../../src/tlon/operations/tasks.py) 的 `OperationTool` 投影 | tasks |
| 21 | `task-complete` | [operations/tasks.py](../../src/tlon/operations/tasks.py) 的 `OperationTool` 投影 | tasks |
| 22 | `task-remove` | [operations/tasks.py](../../src/tlon/operations/tasks.py) 的 `OperationTool` 投影 | tasks |
| 23 | `task-current` | [operations/tasks.py](../../src/tlon/operations/tasks.py) 的 `OperationTool` 投影 | tasks |
| 24 | `persona-update` | [operations/identity.py](../../src/tlon/operations/identity.py) 的 `OperationTool` 投影 | identity |
| 25 | `metrics-read` | [operations/observability.py](../../src/tlon/operations/observability.py) 的 `OperationTool` 投影 | observability |
| 26 | `mail-stats` | [operations/observability.py](../../src/tlon/operations/observability.py) 的 `OperationTool` 投影 | observability |
| 27 | `reputation-read` | [operations/observability.py](../../src/tlon/operations/observability.py) 的 `OperationTool` 投影 | observability |
| 28 | `social-health` | [operations/observability.py](../../src/tlon/operations/observability.py) 的 `OperationTool` 投影 | observability |
| 29 | `traces-list` | [operations/observability.py](../../src/tlon/operations/observability.py) 的 `OperationTool` 投影 | observability |
| 30 | `trace-read` | [operations/observability.py](../../src/tlon/operations/observability.py) 的 `OperationTool` 投影 | observability |
| 31 | `state-get` | [operations/state.py](../../src/tlon/operations/state.py) 的 `OperationTool` 投影 | state |
| 32 | `state-put` | [operations/state.py](../../src/tlon/operations/state.py) 的 `OperationTool` 投影 | state |
| 33 | `member-spawn` | [operations/lifecycle.py](../../src/tlon/operations/lifecycle.py) 的 `OperationTool` 投影 | lifecycle |
| 34 | `member-list` | [operations/lifecycle.py](../../src/tlon/operations/lifecycle.py) 的 `OperationTool` 投影 | lifecycle |
| 35 | `member-self-state` | [operations/lifecycle.py](../../src/tlon/operations/lifecycle.py) 的 `OperationTool` 投影 | lifecycle |
| 36 | `member-start` | [operations/lifecycle.py](../../src/tlon/operations/lifecycle.py) 的 `OperationTool` 投影 | lifecycle |
| 37 | `member-stop` | [operations/lifecycle.py](../../src/tlon/operations/lifecycle.py) 的 `OperationTool` 投影 | lifecycle |
| 38 | `member-start-all` | [operations/lifecycle.py](../../src/tlon/operations/lifecycle.py) 的 `OperationTool` 投影 | lifecycle |
| 39 | `finish-turn` | [operations/lifecycle.py](../../src/tlon/operations/lifecycle.py) 的 `OperationTool` 投影 | lifecycle |
| 40 | `member-sleeping` | [operations/registry.py](../../src/tlon/operations/registry.py) 的 `OperationTool` 投影 | registry |
| 41 | `member-running` | [operations/registry.py](../../src/tlon/operations/registry.py) 的 `OperationTool` 投影 | registry |
| 42 | `member-lazy` | [operations/registry.py](../../src/tlon/operations/registry.py) 的 `OperationTool` 投影 | registry |
| 43 | `member-roster` | [operations/registry.py](../../src/tlon/operations/registry.py) 的 `OperationTool` 投影 | registry |
| 44 | `program-check` | [operations/evolution.py](../../src/tlon/operations/evolution.py) 的 `OperationTool` 投影 | evolution |
| 45 | `prototype-write` | [operations/evolution.py](../../src/tlon/operations/evolution.py) 的 `OperationTool` 投影 | evolution |
| 46 | `prototype-list` | [operations/evolution.py](../../src/tlon/operations/evolution.py) 的 `OperationTool` 投影 | evolution |
| 47 | `prototype-staging-list` | [operations/evolution.py](../../src/tlon/operations/evolution.py) 的 `OperationTool` 投影 | evolution |
| 48 | `prototype-lineage` | [operations/evolution.py](../../src/tlon/operations/evolution.py) 的 `OperationTool` 投影 | evolution |
| 49 | `prototype-read` | [operations/evolution.py](../../src/tlon/operations/evolution.py) 的 `OperationTool` 投影 | evolution |
| 50 | `prototype-review-submit` | [operations/evolution.py](../../src/tlon/operations/evolution.py) 的 `OperationTool` 投影 | evolution |
| 51 | `model-call` | [operations/model.py](../../src/tlon/operations/model.py) 的 `OperationTool` 投影 | model |
| 52 | `model-context` | [operations/model.py](../../src/tlon/operations/model.py) 的 `OperationTool` 投影 | model |
<!-- END GENERATED: tool-registry -->

上表列出完整工具目录；所有成员均注册 member-spawn、prototype-write，实际调用仍受资源硬限制和原型审批约束。所有成员均可使用绑定自身的四个调度工具。L0/L1/L2 均注册五个文件操作，自己的 workspace 无需 Docker，内置作者资料只读；L0/L1 不注册两个 Shell 工具；L2 也仅在成员装配时 Docker 初始化成功后注册。

依赖倒置：`tlon.tools` 不许反向依赖 `tlon.agents`/`tlon.prototypes`，agents 域与 prototypes 域工具的端口（`AgentLifecycle` Protocol、`SpawnRejectedError`、`StopRejectedError`）声明在 [lifecycle/ports.py](../../src/tlon/lifecycle/ports.py)，原型端口（`PrototypeError`、`PrototypeSummary`）声明在 [evolution/ports.py](../../src/tlon/evolution/ports.py)；装配时由 `AgentRegistry` 注入成员作用域领域服务，OperationTool 只投影相应操作。

workspace 的五个文件操作直接访问成员自己的持久目录，无需 Docker；文档与标准库映射只读。workspace 外的 filesystem 访问和 shell 工具共享同一个 `DockerRuntime` 实例（[sandbox/docker_runtime.py](../../src/tlon/sandbox/docker_runtime.py)）。文件柜容器创建后**从不启动**（issue #25），首次文件 I/O 时惰性创建/按名重挂；装置通道经 get_archive/put_archive 读写（list/search 把目录 tar 拉回 host 侧解包，write 把缺失父目录作为 tar 目录条目带入），容器内零命令执行。成员装配时 Docker daemon 不可用则不注册 Shell 工具；依赖装置的文件调用返回 `error="Docker runtime not initialized"`，agent 本体照常启动。`docker.level: L0` 的成员没有 Docker 装置，但可通过普通文件工具访问自己的 workspace 和只读作者资料；`docker.level: L1` 的成员（issue #40）只有 filesystem 五件，shell 工具不注册且 `run()` 由装置层直接拒绝。

## mail

五个邮件操作通过 `OperationTool` 向模型暴露，通过 `DSLAdapter` 向程序暴露，共享 [Operation 定义](../../src/tlon/operations/mail.py) 与成员作用域 [MemberMailService](../../src/tlon/organization/mail/member_mail.py)。只有实际投递提交后才返回成功回执，LoopDriver 不再按工具名二次提交。

- 所有邮箱读取、标读、回复与转发引用都限定为调用成员自己的邮箱；发件身份由宿主绑定。
- 回复显式传 `in_reply_to`，线程从本成员邮箱中的引用推导；不从触发邮件隐式补收件人。
- 转发显式传 `mail_id`；自动生成 Fwd 主题、归因头与原文，外部邮件附不可信提醒；另起线程。不接受主题覆盖。
- `endorsing` 验证本成员邮箱引用并禁止自我致谢；发信仅使用显式 to/cc，不自动抄送创建者。
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

五个工具在自己的 workspace 内由成员绑定的宿主文件通道处理，L0/L1/L2 均无需 Docker；调用者只能使用逻辑工作目录路径，不能选择宿主根或其他成员。`CONTACTS.md` 是其中的普通文件，按需读取和修改，框架不解析联系人格式。`tlon/docs`、`tlon/stdlib` 保持只读，宿主通道拒绝路径逃逸及符号链接。workspace 外的访问仍依赖原装置权限。路径经 [sandbox/path_guard.py](../../src/tlon/sandbox/path_guard.py) 的 `resolve_container_path` 校验：

- 相对路径先拼到逻辑工作目录（`DockerRuntimeConfig.working_dir`，默认 `/workspace`），再按 POSIX 语义规范化（折叠 [.](./.)/[..](./..)/重复分隔符，[..](./..) 越出根时在 `/` 处截断）。
- 解析结果必须等于或位于 `allowed_paths`（agent 配置 `tool.allowed_paths`，默认 `["/workspace"]`）之下，否则抛 `ValueError`，工具返回 `success=False` 和越界说明。读侧三工具（file-read/file-list/file-search）在配置白名单之外额外放行 `/tlon-home`——agent 自己的家目录（`~/.tlon/{agent_id}/`）以只读 bind-mount 挂进容器（见 docs/design/execution-device.md 的纵深防御），装置可用时可读取自己的行为定义、人格与记忆；邮箱通过邮件操作访问；file-write/file-edit 不含此路径，且挂载本身是 `ro`，双保险。其他成员的家目录不挂载进本容器，无法跨 agent 读取。
- 空路径直接拒绝。workspace 宿主通道使用受约束文件访问；装置通道在词法检查后由装置逐级检查目标及祖先，拒绝 symlink；不解析宿主路径。stat 和 archive 之间仍存在并发修改窗口，边界与限制见[执行装置设计](../design/execution-device.md#挂载与文件通道)。

文件操作的字段与输出统一由下方权威定义生成，DSL 和 Tool 使用同一名称及 snake_case 参数。`file-read` 只读 UTF-8 文本，支持分页；`file-write` 整体覆盖并创建缺失父目录；`file-edit` 要求 old_string 唯一匹配。`file-list` 非递归列目录，`file-search` 提供内容正则与文件名 glob 两种模式，跳过 `.git`，保留截断上限。

<!-- BEGIN GENERATED: filesystem-operations -->
Operation 单一契约；输入与输出字段保留 snake_case。（本区块由 `scripts/gen_reference_docs.py` 生成，勿手改；改了会被 `--check` 打回）

### file-read

读取自己的workspace或授权装置UTF-8文件；workspace无需Docker。offset默认1，limit默认100，最多500行。/tlon-home只读。

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

创建或覆盖自己的workspace或授权装置文件，自动创建父目录。结果未知不可自动重试。

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

## run

<!-- BEGIN GENERATED: run-operations -->
Operation 单一契约；输入与输出字段保留 snake_case。（本区块由 `scripts/gen_reference_docs.py` 生成，勿手改；改了会被 `--check` 打回）

### run-read

回读自己的交互来源，原始 run_id，返回有界摘要；只读且不恢复执行。

输入：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `run_id` | string | 是 | minLength=1 |

输出：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `run_id` | string | 是 | — |
| `digest` | string | 是 | — |
| `event_count` | integer | 是 | — |

### run-previous

读取自身同入口、当前运行开始前最近成功运行的原始结果与模型契约指纹；程序自行决定是否延续。

输入：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|

输出：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `run_id` | string | 是 | — |
| `contract` | string | 是 | — |
| `previous` | JSON | 是 | — |
<!-- END GENERATED: run-operations -->

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

从可信原型创建并启动成员；受注册表预算限制。

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

结束当前局部会话；不改变成员调度状态。

输入：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|

输出：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `finished` | boolean | 是 | enum=[True] |
<!-- END GENERATED: lifecycle-operations -->

## evolution

<!-- BEGIN GENERATED: evolution-operations -->
Operation 单一契约；输入与输出字段保留 snake_case。（本区块由 `scripts/gen_reference_docs.py` 生成，勿手改；改了会被 `--check` 打回）

### program-check

静态校验完整 v2 Behavior 与 modules，不执行程序、不发布、不批准。错误以 diagnostics 返回；读取 tlon/docs/README.md 获取作者参考。

输入：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `behavior` | string | 是 | — |
| `modules` | object | 是 | — |

输出：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `valid` | boolean | 是 | — |
| `digest` | string | 是 | — |
| `diagnostics` | array[object] | 是 | — |

### prototype-write

创作一个新的 behavior prototype（纯行为规约，无人格），写入 staging 区。完整 v2 Behavior+模块包；工作流程使用普通函数组合。新 prototype 默认不可信：需人类批准（ops/WebUI）后才能被 member-spawn 使用。behavior 是 v2 EDN handlers 入口，modules 是完整相对路径到源码映射（只描述程序，无法授予工具）。v2 完整模块包不接受参数模板。可选 parent 记录谱系（从哪个既有原型派生），generation 由服务端推导。

输入：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `behavior` | string | 是 | 完整 v2 EDN，handlers 指向导出模块函数。 |
| `modules` | object | 是 | 完整模块包：相对 .tlon 路径到源码字符串 |
| `display_name` | string | 是 | 必填行为标签，描述怎样行动；不赋予职业、职位或社会身份 |
| `description` | string | 否 | 一句话描述这个原型的行为定位 |
| `parent` | string | 否 | 父原型系统 ID（本原型从哪个原型派生而来；留空表示新的根原型）。父原型必须已存在（trusted 库或 staging 区皆可），generation 由服务端自动推导为父代 + 1 |
| `proposal_note` | string | 否 | 本代变更说明：相对父原型改了什么、为什么 |
| `lessons` | string | 否 | 随包教训：成员显式整理并提交的经验，落盘为原型的 LESSONS.md；实例化时拷贝到新成员配置，由普通 Lisp 程序决定是否加入模型消息 |

输出：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | string | 是 | — |
| `parent` | string / null | 是 | — |
| `generation` | integer | 是 | — |
| `trusted` | boolean | 是 | — |
| `status` | string | 是 | enum=['pending_approval'] |
| `display_name` | string | 是 | — |

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

### prototype-read

显式读取原型材料；先返回元数据、文件清单与材料 digest，再按 path 分页读取。后续读取可携带 digest 校验版本；不调用模型，不自动注入谱系或全部文件。

输入：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | string | 是 | — |
| `path` | string | 否 | 清单中的相对文件路径；省略则只返回清单 |
| `offset` | integer | 否 | 字符偏移，默认 0; minimum=0 |
| `limit` | integer | 否 | 每页字符数，默认 12000；按 next_offset 继续读取; minimum=1; maximum=12000 |
| `digest` | string | 否 | 首次读取返回的材料版本；传入后拒绝混读新版本 |

输出：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | string | 是 | — |
| `digest` | string | 是 | — |
| `metadata` | object | 是 | — |
| `files` | array[string] | 是 | — |
| `path` | string / null | 是 | — |
| `content` | string / null | 是 | — |
| `offset` | integer | 是 | — |
| `next_offset` | integer / null | 是 | — |

### prototype-review-submit

提交自己对 staging 原型的咨询性评审；必须携带实际读取材料的 digest、结论和理由。宿主校验版本并绑定成员、run 和调用身份；不调用模型，不自动批准原型。

输入：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | string | 是 | — |
| `digest` | string | 是 | — |
| `verdict` | string | 是 | enum=['通过', '不通过', '弃权'] |
| `reason` | string | 是 | minLength=1 |

输出：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | string | 是 | — |
| `digest` | string | 是 | — |
| `reason` | string | 是 | — |
| `reviewer` | string | 是 | — |
| `run_id` | string | 是 | — |
| `call_id` | string | 是 | — |
| `created_at` | string | 是 | — |
| `verdict` | string | 是 | enum=['通过', '不通过', '弃权'] |
<!-- END GENERATED: evolution-operations -->

## model

<!-- BEGIN GENERATED: model-operations -->
Operation 单一契约；输入与输出字段保留 snake_case。（本区块由 `scripts/gen_reference_docs.py` 生成，勿手改；改了会被 `--check` 打回）

### model-call

使用自己的模型与系统提示词生成结构化响应；不执行工具调用。

输入：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `messages` | array[object] | 是 | minItems=1 |
| `tools` | array[string] | 否 | uniqueItems=True |
| `max_tokens` | integer | 否 | minimum=1 |
| `cache_prefix_len` | integer | 否 | minimum=0 |

输出：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `content` | string / null | 是 | — |
| `tool_calls` | array[object] | 是 | — |
| `finish_reason` | string | 是 | — |
| `usage` | object | 是 | — |
| `reasoning_content` | string / null | 是 | — |

### model-context

Estimate the complete frozen model request size and window; does not call the provider.

输入：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `messages` | array[object] | 是 | minItems=1 |
| `tools` | array[string] | 否 | uniqueItems=True |
| `max_tokens` | integer | 否 | minimum=1 |
| `cache_prefix_len` | integer | 否 | minimum=0 |

输出：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `input_tokens` | integer | 是 | minimum=0 |
| `context_window` | integer | 是 | minimum=1 |
| `output_tokens` | integer | 是 | minimum=0 |
| `fits` | boolean | 是 | — |
| `token_budget_remaining` | integer | 是 | minimum=0 |
| `token_budget_limit` | integer | 是 | minimum=0 |
| `token_budget_measure` | string | 是 | enum=['output', 'total'] |
<!-- END GENERATED: model-operations -->

## context

<!-- BEGIN GENERATED: context-operations -->
Operation 单一契约；输入与输出字段保留 snake_case。（本区块由 `scripts/gen_reference_docs.py` 生成，勿手改；改了会被 `--check` 打回）

### member-context

读取自己的教训、技能索引、当前任务、最近交互、邮件历史与可用工具名；返回普通值，不解释流程配置。

输入：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `history_limit` | integer | 否 | minimum=0; maximum=100 |

输出：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `lessons` | string | 是 | — |
| `skills` | string | 是 | — |
| `current_task` | string | 是 | — |
| `thread_recap` | string | 是 | — |
| `mail_history` | array[object] | 是 | — |
| `tools` | array[string] | 是 | — |
<!-- END GENERATED: context-operations -->


## 注册表读取

<!-- BEGIN GENERATED: registry-operations -->
Operation 单一契约；输入与输出字段保留 snake_case。（本区块由 `scripts/gen_reference_docs.py` 生成，勿手改；改了会被 `--check` 打回）

### member-sleeping

查询成员是否休眠。

输入：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `agent_id` | string | 是 | — |

输出：

```json
{
  "type": "boolean"
}
```

### member-running

查询成员是否正在运行。

输入：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `agent_id` | string | 是 | — |

输出：

```json
{
  "type": "boolean"
}
```

### member-lazy

查询成员是否允许空闲卸载。

输入：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `agent_id` | string | 是 | — |

输出：

```json
{
  "type": "boolean"
}
```

### member-roster

读取格式化的公开成员寻址黄页。

输入：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|

输出：

```json
{
  "type": "string"
}
```
<!-- END GENERATED: registry-operations -->
