# Tlon 工具手册

agent 通过 OpenAI function calling 调用的全部工具。工具实现位于 [src/tlon/tools/](../../src/tlon/tools)，按域分包；参数表以各工具构造器里的 JSON schema 为准。本文只描述当前实现。

## 总述

### Tool 基类契约

[tools/base.py](../../src/tlon/tools/base.py) 的 `Tool`：每个工具有 `name`（LLM 可见的名字）、`description`、`parameters`（JSON schema）三要素，`to_openai_definition()` 输出 OpenAI function calling 格式（`{"type": "function", "function": {...}}`）。`execute(**kwargs)` 是唯一执行入口，返回 `ToolResult(success, data, error, message)`；`message` 是给人看的摘要，`data` 是给 LLM 的结构化结果。可选生命周期钩子 `initialize()` / `shutdown()`，由执行器启动/停止时调用（目前只有 `ExecuteBashTool` 用 `shutdown` 取消未完成的后台任务）。

`Tool.validate_arguments` 在执行前按 schema 校验：缺必填、未知参数、类型不符都会拒绝执行，返回 `success=False, error="Argument validation failed: ..."`；数值参数的 `minimum`/`maximum` 也在此强制（被拒时错误含真实界限值），`enum` 等其余约束不进 `validate_arguments`，靠各工具自己 clamp 或报错。

### ToolExecutor 执行模型

[agents/tool_executor.py](../../src/tlon/agents/tool_executor.py) 的 `ToolExecutor` 是每 agent 一个的工具注册表：直接 async 调用，不经过 EventBus。`execute(tool_name, arguments)` 包住一层 tracing span（`tool.{name}`，参数截断到 200 字符），三类失败都转成 `ToolResult(success=False)` 而不是抛异常：工具未注册、参数校验失败、`execute` 内部抛异常（记日志后返回 `error="Execution error: ..."`）。

认知循环里的并发分发不在 ToolExecutor，而在引擎侧：

- act stage（[agents/stages/act.py](../../src/tlon/agents/stages/act.py) 的 `act_stage_step`）是纯变换，只把本轮 LLM 的 tool calls 声明进 `StageOutcome.tool_dispatches`，自己不执行。
- [agents/loop_driver.py](../../src/tlon/agents/loop_driver.py) 的 `LoopDriver.execute_one_tool` 为每个 tool call 起一个后台 asyncio task 并发执行，外层套 `asyncio.wait_for`。等待预算按「模型可见的 schema 必须可兑现」解析：工具自己在 schema 里声明了 `timeout` 参数（如 `executeBashCommand`）时，引擎等「声明值 + `_TOOL_TIMEOUT_GRACE`（10 秒）宽限」——工具自身是限时权威，wait_for 只兜底工具挂死；未声明 `timeout` 的工具用引擎兜底 `per_tool_timeout`（来自 agent 配置的 `tool.per_timeout`，[base/config.py](../../src/tlon/base/config.py) 的 `ToolConfig`，默认 30 秒）。超时返回 `success=False`，error 指明真正生效的限制来自哪层（工具声明值 + 引擎宽限，或引擎 per-tool 兜底）。
- 超大输出 spill：结果在收割前先过 [tools/spill.py](../../src/tlon/tools/spill.py) 的 `ToolSpill`——`data` 序列化超过 16k 字符即整个落盘到 `workspace/spills/{session_id}/{tool_call_id}.json`（缩进 JSON，可分页），模型与 session 日志只见到 locator（路径/大小/preview/检索提示），可用 `readFile` 的 offset/limit 读回（workspace 即容器工作目录的 bind mount）。写盘失败回退原始结果。
- 收割：`collect_session_results` 把已完成 task 的结果交给 `act_stage_collect`，结果以 `{"role": "tool", ...}` 消息追加回 LLM 上下文（`ToolResult.to_dict()` 的 JSON 序列化）。
- 结束工具 `finishTurn`、`sleepUntil`、`exitAgent` 必须各自单独占一轮，混用其他工具整轮拒绝。邮件工具不负责结束工作，发信后检查结果，再单独请求结束或睡眠。

### 工具装配

每个成员都经 `assemble_cognitive`（[agents/assembly.py](../../src/tlon/agents/assembly.py)）装配 LLM 客户端、记忆、TaskManager、Docker 运行时和全套工具（懒装配：对象便宜、加载按需）；声明 `docker.level: L0` 的成员例外（issue #26）——无执行装置，不创建 DockerRuntime，也不注册 shell/filesystem 七个装置工具；`docker.level: L1` 的文件层成员（issue #40）创建运行时、注册 filesystem 五个工具，但不注册 shell 两个（结构性零代码执行）。无 `:loop` 的成员只是从不启动会话引擎，工具不被调用、不消耗 token。`proposeBehavior` 是首个拿到 `llm_client` / `memory` 的 agents 域工具（此前这对依赖只喂给记忆域工具与反思引擎；它的 journal 以 `SessionLogReader` 端口注入，保持 `tlon.tools` 不反向依赖 `tlon.agents`），`proposeCapability` 同型（能力通道的经验入口）；`reviewPrototype` 是拿到 `llm_client` 的 prototypes 域工具——评审调用用评审者自己的模型，异质性来自评审者的模型血统。

### 注册清单

<!-- BEGIN GENERATED: tool-registry -->
`assemble_cognitive`（agents/assembly.py）为满配成员注册 46 个工具（注册顺序即下表顺序；同名注册会覆盖并记 warning；`docker.level: L0` 的无执行装置成员（issue #26）不注册 shell/filesystem 7 个装置工具，`docker.level: L1` 的文件层成员（issue #40）只注册 filesystem 5 个）：（本区块由 `scripts/gen_reference_docs.py` 生成，勿手改；改了会被 `--check` 打回）

| # | 工具名 | 实现 | 域 |
|---|---|---|---|
| 1 | `executeBashCommand` | [tools/shell/execute_bash.py](../../src/tlon/tools/shell/execute_bash.py) 的 `ExecuteBashTool` | shell |
| 2 | `checkBashTask` | [tools/shell/execute_bash.py](../../src/tlon/tools/shell/execute_bash.py) 的 `CheckBashTaskTool` | shell |
| 3 | `readFile` | [tools/filesystem/read_file.py](../../src/tlon/tools/filesystem/read_file.py) 的 `ReadFileTool` | filesystem |
| 4 | `writeFile` | [tools/filesystem/write_file.py](../../src/tlon/tools/filesystem/write_file.py) 的 `WriteFileTool` | filesystem |
| 5 | `editFile` | [tools/filesystem/edit_file.py](../../src/tlon/tools/filesystem/edit_file.py) 的 `EditFileTool` | filesystem |
| 6 | `listDirectory` | [tools/filesystem/list_directory.py](../../src/tlon/tools/filesystem/list_directory.py) 的 `ListDirectoryTool` | filesystem |
| 7 | `searchFiles` | [tools/filesystem/search_files.py](../../src/tlon/tools/filesystem/search_files.py) 的 `SearchFilesTool` | filesystem |
| 8 | `getCurrentTime` | [tools/system/current_time.py](../../src/tlon/tools/system/current_time.py) 的 `GetCurrentTimeTool` | system |
| 9 | `sendEmail` | [tools/mail/send_email.py](../../src/tlon/tools/mail/send_email.py) 的 `SendEmailTool` | mail |
| 10 | `forwardMail` | [tools/mail/forward_mail.py](../../src/tlon/tools/mail/forward_mail.py) 的 `ForwardMailTool` | mail |
| 11 | `finishTurn` | [tools/agents/scheduling.py](../../src/tlon/tools/agents/scheduling.py) 的 `FinishTurnTool` | agents |
| 12 | `sleepUntil` | [tools/agents/scheduling.py](../../src/tlon/tools/agents/scheduling.py) 的 `SleepUntilTool` | agents |
| 13 | `exitAgent` | [tools/agents/scheduling.py](../../src/tlon/tools/agents/scheduling.py) 的 `ExitAgentTool` | agents |
| 14 | `getSelfState` | [tools/agents/scheduling.py](../../src/tlon/tools/agents/scheduling.py) 的 `GetSelfStateTool` | agents |
| 15 | `updatePersona` | [tools/agents/update_persona.py](../../src/tlon/tools/agents/update_persona.py) 的 `UpdatePersonaTool` | agents |
| 16 | `readContacts` | [tools/agents/contacts.py](../../src/tlon/tools/agents/contacts.py) 的 `ReadContactsTool` | agents |
| 17 | `updateContact` | [tools/agents/contacts.py](../../src/tlon/tools/agents/contacts.py) 的 `UpdateContactTool` | agents |
| 18 | `searchMails` | [tools/mail/search_mails.py](../../src/tlon/tools/mail/search_mails.py) 的 `SearchMailsTool` | mail |
| 19 | `checkInbox` | [tools/mail/check_inbox.py](../../src/tlon/tools/mail/check_inbox.py) 的 `CheckInboxTool` | mail |
| 20 | `markRead` | [tools/mail/mark_read.py](../../src/tlon/tools/mail/mark_read.py) 的 `MarkReadTool` | mail |
| 21 | `listPrototypes` | [tools/prototypes/list_prototypes.py](../../src/tlon/tools/prototypes/list_prototypes.py) 的 `ListPrototypesTool` | prototypes |
| 22 | `listStagingPrototypes` | [tools/prototypes/list_staging.py](../../src/tlon/tools/prototypes/list_staging.py) 的 `ListStagingPrototypesTool` | prototypes |
| 23 | `getPrototypeLineage` | [tools/prototypes/get_lineage.py](../../src/tlon/tools/prototypes/get_lineage.py) 的 `GetPrototypeLineageTool` | prototypes |
| 24 | `reviewPrototype` | [tools/prototypes/review_prototype.py](../../src/tlon/tools/prototypes/review_prototype.py) 的 `ReviewPrototypeTool` | prototypes |
| 25 | `spawnAgent` | [tools/agents/spawn_agent.py](../../src/tlon/tools/agents/spawn_agent.py) 的 `SpawnAgentTool` | agents |
| 26 | `listMembers` | [tools/agents/list_members.py](../../src/tlon/tools/agents/list_members.py) 的 `ListMembersTool` | agents |
| 27 | `writePrototype` | [tools/agents/write_prototype.py](../../src/tlon/tools/agents/write_prototype.py) 的 `WritePrototypeTool` | agents |
| 28 | `proposeBehavior` | [tools/agents/propose_behavior.py](../../src/tlon/tools/agents/propose_behavior.py) 的 `ProposeBehaviorTool` | agents |
| 29 | `proposeCapability` | [tools/agents/propose_capability.py](../../src/tlon/tools/agents/propose_capability.py) 的 `ProposeCapabilityTool` | agents |
| 30 | `rememberFact` | [tools/memory/remember_fact.py](../../src/tlon/tools/memory/remember_fact.py) 的 `RememberFactTool` | memory |
| 31 | `searchMemories` | [tools/memory/search_memories.py](../../src/tlon/tools/memory/search_memories.py) 的 `SearchMemoriesTool` | memory |
| 32 | `forget` | [tools/memory/forget_memory.py](../../src/tlon/tools/memory/forget_memory.py) 的 `ForgetMemoryTool` | memory |
| 33 | `readSession` | [tools/memory/read_session.py](../../src/tlon/tools/memory/read_session.py) 的 `ReadSessionTool` | memory |
| 34 | `createTask` | [tools/tasks/create_task.py](../../src/tlon/tools/tasks/create_task.py) 的 `CreateTaskTool` | tasks |
| 35 | `listTasks` | [tools/tasks/list_tasks.py](../../src/tlon/tools/tasks/list_tasks.py) 的 `ListTasksTool` | tasks |
| 36 | `startTask` | [tools/tasks/start_task.py](../../src/tlon/tools/tasks/start_task.py) 的 `StartTaskTool` | tasks |
| 37 | `pauseTask` | [tools/tasks/pause_task.py](../../src/tlon/tools/tasks/pause_task.py) 的 `PauseTaskTool` | tasks |
| 38 | `completeTask` | [tools/tasks/complete_task.py](../../src/tlon/tools/tasks/complete_task.py) 的 `CompleteTaskTool` | tasks |
| 39 | `removeTask` | [tools/tasks/remove_task.py](../../src/tlon/tools/tasks/remove_task.py) 的 `RemoveTaskTool` | tasks |
| 40 | `getCurrentTask` | [tools/tasks/get_current_task.py](../../src/tlon/tools/tasks/get_current_task.py) 的 `GetCurrentTaskTool` | tasks |
| 41 | `getMailStats` | [tools/observability/mail_stats.py](../../src/tlon/tools/observability/mail_stats.py) 的 `GetMailStatsTool` | observability |
| 42 | `getReputation` | [tools/observability/reputation.py](../../src/tlon/tools/observability/reputation.py) 的 `GetReputationTool` | observability |
| 43 | `getSocialHealth` | [tools/observability/social_health.py](../../src/tlon/tools/observability/social_health.py) 的 `GetSocialHealthTool` | observability |
| 44 | `getMetrics` | [tools/observability/get_metrics.py](../../src/tlon/tools/observability/get_metrics.py) 的 `GetMetricsTool` | observability |
| 45 | `listTraces` | [tools/observability/traces.py](../../src/tlon/tools/observability/traces.py) 的 `ListTracesTool` | observability |
| 46 | `getTrace` | [tools/observability/traces.py](../../src/tlon/tools/observability/traces.py) 的 `GetTraceTool` | observability |
<!-- END GENERATED: tool-registry -->

上表是正式成员的满配。见习成员不注册 spawnAgent、writePrototype、proposeBehavior、proposeCapability 四个生殖/演化工具；所有认知成员均可使用绑定自身的四个调度工具。L0 不注册 shell/filesystem 七个装置工具，L1 不注册 shell 两个工具。

依赖倒置：`tlon.tools` 不许反向依赖 `tlon.agents`/`tlon.prototypes`，agents 域与 prototypes 域工具的端口（`AgentLifecycle` Protocol、`SpawnRejectedError`、`StopRejectedError`）声明在 [tools/agents/ports.py](../../src/tlon/tools/agents/ports.py)，原型端口（`PrototypeError`、`PrototypeSummary`）声明在 [tools/prototypes/ports.py](../../src/tlon/tools/prototypes/ports.py)；装配时由 `AgentRegistry` 把自身和原型库函数注入工具实例。

shell 与 filesystem 工具共享同一个 `DockerRuntime` 实例（[sandbox/docker_runtime.py](../../src/tlon/sandbox/docker_runtime.py)）。文件柜容器创建后**从不启动**（issue #25），首次文件 I/O 时惰性创建/按名重挂；自 issue #40 起五个 filesystem 工具全部经 get_archive/put_archive 读写（list/search 把目录 tar 拉回 host 侧解包，write 把缺失父目录作为 tar 目录条目带入），容器内零命令执行。Docker daemon 不可用时这些工具返回 `error="Docker runtime not initialized"`，agent 本体照常启动。`docker.level: L0` 的成员（issue #26）完全不装配这两域工具——无执行装置即无工具，不存在「工具有但装置为 None」的状态；`docker.level: L1` 的成员（issue #40）只有 filesystem 五件，shell 工具不注册且 `run()` 由装置层直接拒绝。

## mail

### sendEmail

发送邮件，agent 的邮件出口。引擎在记录工具结果前调用 `EffectExecutor → PostOffice` 完成真实投信；act 收割结果不会再次发信。地址支持本地成员及已注册网关，不存在组地址；多人逐个列入 to/cc。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `to` | array[string] | 是 | — | 收件人地址列表 |
| `cc` | array[string] | 否 | `[]` | 抄送地址列表 |
| `request_id` | string | 否 | 引擎生成 | 稳定投信标识，结果未知时用原值重试；同键不同内容拒绝 |
| `subject` | string | 否 | — | 邮件主题 |
| `content` | string | 是 | — | 邮件正文 |
| `in_reply_to` | string | 否 | — | 被回复邮件的 id；必须是自己邮箱中真实存在的邮件（幻觉 id 发送失败），回复并入该邮件的线程；留空 = 另起线程（无触发邮件缺省，ADR 0028） |

返回行为：本地提交成功后 `success=True`，data 包含 mail_id/request_id/local_recipients/gateways/message；网关单独报告成功、失败或未知，不保证外部平台收到。无效或已死亡收件人使整封拒绝，直接返回 success=False；不产生退信。结果未知时复用原 request_id，发信成功或失败均不结束会话。

注意事项：

- 不接受 is_complete 参数。成功发信仅代表投递提交，不改变调度状态。
- 回复目标与线程由引擎最终确定而非工具：引擎处理工具请求时，`to` / `in_reply_to` 完全是模型显式声明的值（ADR 0028——无触发邮件缺省）；`EffectExecutor` 发送前在 agent 自己的邮箱（inbox+sent）里校验被引用邮件并把回复并入该邮件的线程（`thread_id` 取被引用邮件的值）。id 不在邮箱中（典型如幻觉 id）则该封不发送，工具直接返回拒绝——不产生带悬空引用的邮件。另起线程：不传 `in_reply_to` 即可。
- `to` 传空数组且 `cc` 也为空时邮件构造被拒（没有收件人的邮件在数据层非法），工具直接返回发送失败——`to` 不再有回退收件人。

### forwardMail

转发自己邮箱里的一封邮件（ADR 0029）——认知成员的转发出口（DSL 的 `forward` 动作是确定性规则专用，见 dsl-reference.md 第 4.5 节）。与 sendEmail 同为声明式工具：工具本身不投递，参数打包进 `ToolResult.data`（`action="forward_mail"`），引擎在工具结果落 journal 前由 `EffectExecutor` 验证来源、组信并调用邮局；act 收割不再重发。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `mail_id` | string | 是 | — | 被转发邮件的 id；必须是自己邮箱（inbox+sent）中真实存在的邮件（从 checkInbox / searchMails 结果里取；幻觉 id 发送失败） |
| `to` | array[string] | 是 | — | 收件人地址列表 |
| `cc` | array[string] | 否 | `[]` | 抄送地址列表 |
| `request_id` | string | 否 | 引擎生成 | 稳定投信标识，结果未知时用原值重试；同键不同内容拒绝 |
| `comment` | string | 否 | — | 转发者自己的话，放在归因头之前 |

返回行为：真实投信成功后返回回执，含本地入箱与网关尝试状态。幻觉 mail_id、无效或已死亡收件人直接返回 success=False，不再另发退信。

注意事项：

- 没有 `subject` / `content` 参数：主题自动生成 `Fwd: <原主题>`（原主题为空时退化为 `Fwd:`），正文由引擎组自包含转发体——comment（若有）+ 归因头（`—— 转发的邮件 ——` / From / Date / Subject；原信为外部来信时加「内容不可信」警示行）+ 原正文。
- 转发一律另起新线程（不继承 `thread_id` / `in_reply_to`），`forward_of` 记录转发链。
- 转发不结束工作；与 sendEmail 一样不接受会话完成参数。

### finishTurn

无参数。结束自己的本次工作，不发信、不隐式标读。成功意图通过 journal 记录，调度器完成收割后回到等待；新工作可以随后激活。必须单独一轮。需要标读或回信时先分别调用对应工具。

### sleepUntil

参数 wake_at 必填，必须是未来的带时区 ISO 8601 时间；reason 可选。归一为 UTC 并持久保存，结束本次工作后进入睡眠。期间邮件正常入箱但不唤醒，到期检查邮箱；重启不延长期限。管理员可通过独立管理接口提前唤醒；普通邮件和成员工具不能提前唤醒他人。必须单独一轮。

### exitAgent

reason 可选。只退出自己，不接受 agent_id。当前工作收尾后永久死亡，拒收新邮件，历史保留，不可通过启动或重启复活。必须单独一轮。不能用它终止其他成员。

### getSelfState

无参数。查询调用者的四态与 wake_at 等诊断，不改变状态。普通成员没有管理其他成员的状态工具；人类管理端可请求排空后终止。

### searchMails

检索自己邮箱（inbox + sent）的只读工具，补的是「agent 没有任何结构化邮箱查询手段」的缺口——此前只能靠 `(mail-history)` section 被动带入或 `read_file` 读原始 JSONL。它是内置 skill `explain-decision`（见 docs/design/skills.md）的邮件证据源。邮箱读取走依赖倒置：端口 `MailboxMailsProvider` 声明在 [tools/mail/ports.py](../../src/tlon/tools/mail/ports.py)，实现在 [agents/mail_search.py](../../src/tlon/agents/mail_search.py)（只读 `Mailbox.inbox()`/`sent()`），装配时按 owner agent 注入——只查自己的邮箱，碰不到他人邮件。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `query` | string | 否 | — | 关键词，空格分隔多词为 AND 关系，匹配主题、正文、收发地址（大小写不敏感） |
| `mail_id` | string | 否 | — | 邮件 id，精确取该邮件的完整正文 |
| `limit` | integer | 否 | `10` | 关键词检索返回条数上限（最大 20） |

返回行为：`query` 与 `mail_id` 至少传一个，都缺返回 `success=False`。关键词检索命中按时间从新到旧，每封返回 `id`/`box`（inbox 或 sent）/`from_address`/`to`/`cc`/`subject`/`timestamp`/`thread_id` + `content_excerpt`（正文截断 500 字符）；`mail_id` 精确取返回完整邮件（含全文），id 不存在返回 `success=False`。

## filesystem

五个工具都在 Docker 沙箱容器内操作，agent 不接触宿主机文件系统。路径统一经 [tools/filesystem/path_guard.py](../../src/tlon/tools/filesystem/path_guard.py) 的 `resolve_container_path` 校验：

- 相对路径先拼到容器工作目录（`DockerRuntimeConfig.working_dir`，默认 `/workspace`），再按 POSIX 语义规范化（折叠 [.](./.)/[..](./..)/重复分隔符，[..](./..) 越出根时在 `/` 处截断）。
- 解析结果必须等于或位于 `allowed_paths`（agent 配置 `tool.allowed_paths`，默认 `["/workspace"]`）之下，否则抛 `ValueError`，工具返回 `success=False` 和越界说明。读侧三工具（readFile/listDirectory/searchFiles）在配置白名单之外额外放行 `/tlon-home`——agent 自己的家目录（`~/.tlon/{agent_id}/`）以只读 bind-mount 挂进容器（见 docs/design/execution-device.md 的纵深防御），可读取自己的行为定义、记忆与邮箱；writeFile/editFile 不含此路径，且挂载本身是 `ro`，双保险。其他成员的家目录不挂载进本容器，无法跨 agent 读取。
- 空路径直接拒绝。全部是纯字符串处理，不做宿主机路径解析。

### readFile

| 参数 | 类型 | 必填 | 默认 | 约束 |
|---|---|---|---|---|
| `file_path` | string | 是 | — | 相对工作目录或白名单内绝对路径 |
| `offset` | integer | 否 | 1 | ≥1，起始行号（1-based） |
| `limit` | integer | 否 | 100 | 1–500；代码再 clamp 到 500 上限 |

返回：`content`（选中行，截断时尾部附 `... (N more lines)`）、`total_lines`、`read_lines`、`offset`。失败：文件不存在、非 UTF-8 文本、路径越界、Docker 不可用。

### writeFile

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `file_path` | string | 是 | — | 目标路径 |
| `content` | string | 是 | — | 完整写入内容 |

行为：文件不存在则创建，存在则整体覆盖；自动创建父目录（缺失的父目录作为目录条目随 tar 一并 put_archive，不再 shell out `mkdir -p`，issue #40）。返回 `bytes_written`（UTF-8 字节数）、`existed_before`。失败：路径越界、容器写入错误。

### editFile

old_string/new_string 精确替换模型。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `file_path` | string | 是 | 目标路径 |
| `old_string` | string | 是 | 被替换的精确文本，必须在文件中唯一出现 |
| `new_string` | string | 是 | 替换后文本 |

行为：`old_string` 出现 0 次 → 失败（提示文本不在文件中）；出现 2 次及以上 → 失败（提示加上下文消歧）；唯一匹配则替换一次写回。返回 `replaced=true`、`old_length`、`new_length`。失败：文件不存在、非 UTF-8、路径越界。

### listDirectory

列目录内容（不递归），结构化返回名称/类型/大小/修改时间，目录在前按名排序。

| 参数 | 类型 | 必填 | 默认 | 约束 |
|---|---|---|---|---|
| `path` | string | 否 | `.` | 相对工作目录或白名单内绝对路径 |
| `max_entries` | integer | 否 | 200 | 1–1000 |

返回 `entries`（每项 `name`/`type`（dir/file/symlink）/`size`/`modified`）、`count`、`truncated`。截断时附 `hint` 提示缩小到子目录。失败：路径不存在、不是目录、路径越界、Docker 不可用。

### searchFiles

文件名 glob 与内容正则检索合一，两个模式都跳过 `.git`。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `pattern` | string | 是 | — | content 模式是正则（host 侧 Python `re`，与 grep -E 大体兼容）；filename 模式是 glob（不区分大小写） |
| `path` | string | 否 | `.` | 搜索起点目录 |
| `mode` | string | 否 | `content` | enum：`content` / `filename` |
| `max_results` | integer | 否 | 50 | 1–200 |

content 模式返回结构化匹配（`file`/`line`/`text`，`text` 每行截断到 200 字符）；filename 模式返回路径列表。返回 `count`/`truncated`，截断时附 `hint`。无匹配仍 `success=True`、`count=0`。失败：路径不存在、正则非法、路径越界、Docker 不可用。

## system

### getCurrentTime

无参数。返回宿主机本地时间（与用户同处一个时区）：`iso`（ISO 8601 含时区偏移）、`unix`、`timezone`、`weekday`。不依赖 Docker 沙箱，容器不可用时仍可用。

## shell

### executeBashCommand

在 agent 的 Docker 沙箱中执行 bash 命令，支持管道、重定向、分号串接。

执行模型（issue #25）：**每条命令都在全新的一次性容器中执行**（同镜像/挂载/限额/网络加固），命令结束后容器即被删除——环境变量、已安装的软件、`/tmp` 内容、`nohup` 启动的进程都不跨命令保留；跨命令需要保留的文件只能放 `/workspace`（持久 bind mount）。需要后台运行的命令用 `background=true`（host 侧 asyncio 任务包一次性容器），不要用 `nohup`。

| 参数 | 类型 | 必填 | 默认 | 约束 |
|---|---|---|---|---|
| `command` | string | 是 | — | bash 命令 |
| `background` | boolean | 否 | false | true 时立即返回 task_id |
| `timeout` | integer | 否 | 60 | 1–300 秒 |

前台：返回 `exit_code`/`stdout`/`stderr`；`success` 等于 `exit_code == 0`，非零时 `error` 为 stderr 内容。

后台（`background=true`）：立即返回 `success=True` 和 `data={task_id, command(截断200字符), status="running"}`；任务在该 agent 的后台任务表里执行，运行中任务上限 20 个（`_MAX_BACKGROUND_TASKS`，只统计未完成，已完成结果不占配额），超限直接拒绝。任务表是 per-agent 的，agent 之间互不可见。任务完成后结果（exit code/stdout/stderr）保留在任务表中供 `checkBashTask` 查询，每个 agent 最多保留最近 50 个已完成结果（`_MAX_FINISHED_TASKS`），超出后启动新后台任务时从最老的开始逐出。

注意事项：

- `timeout` 是前台命令的限时权威：schema 声明的 1–300 秒区间全部可兑现——超界值在参数校验时就被拒（错误信息含实际上限/下限），区间内的值引擎会等足（声明值 + 10 秒宽限，见总述），不会再被 `per_tool_timeout` 提前砍掉。容器内由 `timeout` 命令限时，到点返回 `Command timed out after {n} seconds`。需要超过 300 秒的长跑命令请用 `background=true`（后台任务不受此外层等待约束，因为 `execute` 立即返回）。
- agent 停止时 `ExecuteBashTool.shutdown` 取消该 agent 全部未完成后台任务。

### checkBashTask

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `task_id` | string | 是 | `executeBashCommand` 后台模式返回的 id |

返回：执行中 → `status="running"`；完成 → `status="completed"` + `exit_code`/`stdout`/`stderr`；任务被取消 → `success=False, status="cancelled"`；任务抛异常 → `success=False, status="failed"`。

注意事项：

- 已完成任务的结果保留在 per-agent 任务表中，可重复查询；保留窗口为每个 agent 最近 50 个已完成结果（`_MAX_FINISHED_TASKS`），更早的结果在启动新后台任务时被逐出。
- 查不到任务时返回 `success=False`，error 说明三种可能：任务从未存在、已完成并被逐出保留窗口、或进程已重启（任务表是内存态，不随重启存活）。无法进一步区分这三种情况——需要长期可靠产出时请让命令自己写文件，再用 `readFile` 读。

## memory

前三个工具操作当前 agent 自己的 `MemoryManager`（[memory/manager.py](../../src/tlon/memory/manager.py)），数据落在 `~/.tlon/{agent_id}/memory/claims.jsonl`（claim 模型，ADR 0017）；`readSession` 只查询本 agent 的 `sessions.sqlite3` 中指定 session 的事件，不回读旧 `sessions/*.jsonl`（ADR 0032）。检索为向量检索（ADR 0017 阶段 2，sqlite-vec），索引不可用时降级关键词子串匹配。

### rememberFact

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `fact` | string | 是 | — | 记忆的自然语言描述 |
| `importance` | number | 否 | 0.5 | 0.0–1.0，作为重要度存入 |

返回 `claim_id`，记忆立即追加进 `claims.jsonl`（一条 active claim，`source_session_ids` 为空）。

### searchMemories

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `query` | string | 是 | — | 搜索关键词 |
| `limit` | integer | 否 | 5 | 最多返回条数 |

返回拼接文本（`data.results` 与 `message` 同内容），向量检索 top-k active claims，每条带日期与 `source_session_ids` 来源引用（需要原文细节时用 `readSession` 回读该会话）；无命中时仍 `success=True`，文本为「未找到相关记忆。」。

### readSession

回读一个历史会话的 journal 原文摘要（ADR 0017 阶段 4）——claim → 来源的回读通道：`searchMemories` 结果带的 `source_session_ids` 在这里落地（ReadAgent 模式）。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `session_id` | string | 是 | — | 要回读的会话 id（searchMemories 结果里的 source_session_ids 之一） |

返回有界摘要：触发来信 + 各轮 LLM 回复 + 工具活动计数 + 结束原因。截断策略：触发截 300 字符、每轮回复截 600 字符、最多列 12 轮回复（超出注明轮数）、整个摘要硬上限 12000 字符（防 context 爆掉）。会话不存在（无日志）返回 `success=False` 与明确错误消息，不抛异常。

### forget

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `query` | string | 是 | — | 要删除内容的描述 |
| `confirm` | boolean | 否 | false | false=只预览，true=真删 |

两段式：`confirm=false` 按空格分词（长度 ≤1 的词丢弃，全丢则用整串）做关键词匹配，返回命中条数（附前几条预览）和确认提示；`confirm=true` 删除全部命中项，返回删除条数。无匹配时返回「未找到匹配 …」。删除不可撤销。

## tasks

每个 agent 一份任务清单，由 [tools/tasks/manager.py](../../src/tlon/tools/tasks/manager.py) 的 `TaskManager` 管理，每次变更整体落盘到 `~/.tlon/{agent_id}/tasks.jsonl`。任务 id 自增（`t-001`、`t-002`…），重启后从文件恢复计数。状态机：`todo` / `doing` / `paused` / `done`（[tools/tasks/models.py](../../src/tlon/tools/tasks/models.py) 的 `TaskStatus`）。

### createTask

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `title` | string | 是 | — | 任务标题 |
| `description` | string | 否 | `""` | 详细描述、验收标准 |

返回 `task_id`/`title`/`status`（初始 `todo`）。

### listTasks

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `filter` | string | 否 | `all` | enum：`all`/`todo`/`doing`/`paused`/`done` |

返回各状态统计（`total`/`todo`/`doing`/`paused`/`done`）+ 任务列表（每项仅 `id`/`title`/`status`），按创建时间排序。

### startTask

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `task_id` | string | 是 | 要开始/恢复的任务 id |

行为：开始目标任务；若另有 `doing` 任务，自动把它暂停（返回里的 `auto_paused` 字段）。从 `paused` 恢复不重置 `started_at`，只清 `paused_at`。任务不存在返回失败。

### pauseTask

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `task_id` | string | 是 | — | 要暂停的任务 id |
| `reason` | string | 否 | `""` | 暂停原因/进度记录 |

仅 `doing` 状态可暂停，其他状态返回失败。`reason` 以 `[Paused] ...` 追加进任务 description。返回 `pause_count`。

### completeTask

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `task_id` | string | 是 | — | 要完成的任务 id |
| `result` | string | 否 | `""` | 完成情况摘要 |

`todo` 状态拒绝（提示先 `startTask`）；`done` 拒绝（已完成）；`doing` 和 `paused` 都可直接完成。返回 `duration_minutes`（从 `started_at` 算，恢复执行不把暂停时间剔除）和 `pause_count`。

### removeTask

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `task_id` | string | 是 | 要移除的任务 id |

仅 `done` 状态可移除，其他状态返回失败（提示先 `completeTask`）。操作不可撤销。

### getCurrentTask

无参数。返回 `doing` 任务；无 `doing` 时返回最近暂停的任务；都没有时 `success=True, data={"has_task": false}`。有任务时返回 id/title/description/status/started_at/elapsed_minutes/pause_count。

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

创作新 behavior prototype，写入 `~/.tlon/prototypes-staging/`。新 prototype 一律 `trusted=false`（`status=pending_approval`），需人类在 ops 的 Agents 视图（`s` 审批）批准后才能被 `spawnAgent` 使用——LLM 写行为、人类把关的信任闸。原型不带人格（统一成员模型，ADR 0006）：人格在成员创建时播种、之后是成员自主数据（`updatePersona` 自改，ADR 0024），不属基因库。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `behavior` | string | 是 | — | 行为规约 EDN 字符串，v1 schema 的 rules-form |
| `display_name` | string | 是 | — | 非空行为显示名，描述步骤、预算、上下文或反思，不赋予职业 |
| `description` | string | 否 | `""` | 行为定位一句话描述 |
| `parent` | string | 否 | — | 父原型名称（谱系；须已存在，trusted 或 staging 皆可）；`generation` 服务端推导为父代 + 1 |
| `proposal_note` | string | 否 | — | 本代变更说明（相对父原型改了什么、为什么） |
| `lessons` | string | 否 | — | 随包教训文本（退休成员蒸馏的经验），落盘为原型的 `LESSONS.md`；实例化时拷贝给新成员，由其 spec 的 `(lessons)` section 注入对话 |

失败行为：EDN 校验失败抛 `PrototypeError`，返回 `success=False` 且 `data.hint` 携带按提交内容选择的 schema 提示（含 `:loop` 用 `_BEHAVIOR_HINT`，否则 `_SCRIPT_HINT`），供 LLM 修正后重试；未写入任何文件。behavior 是纯数据 spec，无法授予工具。EDN 字段细节见 docs/reference/dsl-reference.md。

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