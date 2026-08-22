# Tlon 工具手册

agent 通过 OpenAI function calling 调用的全部工具。工具实现位于 [src/tlon/tools/](../../src/tlon/tools)，按域分包；参数表以各工具构造器里的 JSON schema 为准。本文只描述当前实现。

## 总述

### Tool 基类契约

[tools/base.py](../../src/tlon/tools/base.py) 的 `Tool`：每个工具有 `name`（LLM 可见的名字）、`description`、`parameters`（JSON schema）三要素，`to_openai_definition()` 输出 OpenAI function calling 格式（`{"type": "function", "function": {...}}`）。`execute(**kwargs)` 是唯一执行入口，返回 `ToolResult(success, data, error, message)`；`message` 是给人看的摘要，[data](../../data) 是给 LLM 的结构化结果。可选生命周期钩子 `initialize()` / `shutdown()`，由执行器启动/停止时调用（目前只有 `ExecuteBashTool` 用 `shutdown` 取消未完成的后台任务）。

`Tool.validate_arguments` 在执行前按 schema 校验：缺必填、未知参数、类型不符都会拒绝执行，返回 `success=False, error="Argument validation failed: ..."`。schema 只做浅校验——`enum`、`minimum`/`maximum` 等约束不进 `validate_arguments`，靠各工具自己 clamp 或报错。

### ToolExecutor 执行模型

[agents/tool_executor.py](../../src/tlon/agents/tool_executor.py) 的 `ToolExecutor` 是每 agent 一个的工具注册表：直接 async 调用，不经过 EventBus。`execute(tool_name, arguments)` 包住一层 tracing span（`tool.{name}`，参数截断到 200 字符），三类失败都转成 `ToolResult(success=False)` 而不是抛异常：工具未注册、参数校验失败、`execute` 内部抛异常（记日志后返回 `error="Execution error: ..."`）。

认知循环里的并发分发不在 ToolExecutor，而在引擎侧：

- act stage（[agents/stages/act.py](../../src/tlon/agents/stages/act.py) 的 `act_stage_step`）是纯变换，只把本轮 LLM 的 tool calls 声明进 `StageOutcome.tool_dispatches`，自己不执行。
- [agents/loop_driver.py](../../src/tlon/agents/loop_driver.py) 的 `LoopDriver.execute_one_tool` 为每个 tool call 起一个后台 asyncio task 并发执行，外层套 `asyncio.wait_for(per_tool_timeout)`；超时返回 `success=False, error="Timeout after {n}s"`。`per_tool_timeout` 来自 agent 配置的 `tool.per_timeout`（[base/config.py](../../src/tlon/base/config.py) 的 `ToolConfig`，默认 30 秒）。
- 超大输出 spill：结果在收割前先过 [tools/spill.py](../../src/tlon/tools/spill.py) 的 `ToolSpill`——`data` 序列化超过 16k 字符即整个落盘到 `workspace/spills/{session_id}/{tool_call_id}.json`（缩进 JSON，可分页），模型与 session 日志只见到 locator（路径/大小/preview/检索提示），可用 `readFile` 的 offset/limit 读回（workspace 即容器工作目录的 bind mount）。写盘失败回退原始结果。
- 收割：`collect_session_results` 把已完成 task 的结果交给 `act_stage_collect`，结果以 `{"role": "tool", ...}` 消息追加回 LLM 上下文（`ToolResult.to_dict()` 的 JSON 序列化）。
- 特殊规则：同一轮里 `sendEmail` 带 `is_complete=true` 且伴随其他工具调用时，act stage 拒绝整轮（一个工具都不执行），向 LLM 返回冲突提示要求重新规划；`silence` 与 `sendEmail` 互斥（见 [mail/silence](#silence)），同时调用同样整轮拒绝。

### 工具与成员类型

工具只属于认知型成员。[agents/agent_registry.py](../../src/tlon/agents/agent_registry.py) 的 `_start_agent` 从成员 spec 派生类型：spec 含带 `:loop` 的认知规则才调用 `_assemble_cognitive` 装配 LLM 客户端、记忆、TaskManager、Docker 运行时和全套工具；确定型成员（无 `:loop` 的规则）`cognitive=None`，不建任何工具、不消耗 token。

### 注册清单

<!-- BEGIN GENERATED: tool-registry -->
`_assemble_cognitive` 为每个认知型成员注册 23 个工具（注册顺序即下表顺序；同名注册会覆盖并记 warning）：（本区块由 `scripts/gen_reference_docs.py` 生成，勿手改；改了会被 `--check` 打回）

| # | 工具名 | 实现 | 域 |
|---|---|---|---|
| 1 | `executeBashCommand` | [tools/shell/execute_bash.py](../../src/tlon/tools/shell/execute_bash.py) 的 `ExecuteBashTool` | shell |
| 2 | `checkBashTask` | [tools/shell/execute_bash.py](../../src/tlon/tools/shell/execute_bash.py) 的 `CheckBashTaskTool` | shell |
| 3 | `readFile` | [tools/filesystem/read_file.py](../../src/tlon/tools/filesystem/read_file.py) 的 `ReadFileTool` | filesystem |
| 4 | `writeFile` | [tools/filesystem/write_file.py](../../src/tlon/tools/filesystem/write_file.py) 的 `WriteFileTool` | filesystem |
| 5 | `editFile` | [tools/filesystem/edit_file.py](../../src/tlon/tools/filesystem/edit_file.py) 的 `EditFileTool` | filesystem |
| 6 | `sendEmail` | [tools/mail/send_email.py](../../src/tlon/tools/mail/send_email.py) 的 `SendEmailTool` | mail |
| 7 | `silence` | [tools/mail/silence.py](../../src/tlon/tools/mail/silence.py) 的 `SilenceTool` | mail |
| 8 | `listPrototypes` | [tools/prototypes/list_prototypes.py](../../src/tlon/tools/prototypes/list_prototypes.py) 的 `ListPrototypesTool` | prototypes |
| 9 | `spawnAgent` | [tools/agents/spawn_agent.py](../../src/tlon/tools/agents/spawn_agent.py) 的 `SpawnAgentTool` | agents |
| 10 | `stopAgent` | [tools/agents/stop_agent.py](../../src/tlon/tools/agents/stop_agent.py) 的 `StopAgentTool` | agents |
| 11 | `listAgents` | [tools/agents/list_agents.py](../../src/tlon/tools/agents/list_agents.py) 的 `ListAgentsTool` | agents |
| 12 | `writePrototype` | [tools/agents/write_prototype.py](../../src/tlon/tools/agents/write_prototype.py) 的 `WritePrototypeTool` | agents |
| 13 | `rememberFact` | [tools/memory/remember_fact.py](../../src/tlon/tools/memory/remember_fact.py) 的 `RememberFactTool` | memory |
| 14 | `searchMemories` | [tools/memory/search_memories.py](../../src/tlon/tools/memory/search_memories.py) 的 `SearchMemoriesTool` | memory |
| 15 | `compact` | [tools/memory/compact.py](../../src/tlon/tools/memory/compact.py) 的 `CompactTool` | memory |
| 16 | `forget` | [tools/memory/forget_memory.py](../../src/tlon/tools/memory/forget_memory.py) 的 `ForgetMemoryTool` | memory |
| 17 | `createTask` | [tools/tasks/create_task.py](../../src/tlon/tools/tasks/create_task.py) 的 `CreateTaskTool` | tasks |
| 18 | `listTasks` | [tools/tasks/list_tasks.py](../../src/tlon/tools/tasks/list_tasks.py) 的 `ListTasksTool` | tasks |
| 19 | `startTask` | [tools/tasks/start_task.py](../../src/tlon/tools/tasks/start_task.py) 的 `StartTaskTool` | tasks |
| 20 | `pauseTask` | [tools/tasks/pause_task.py](../../src/tlon/tools/tasks/pause_task.py) 的 `PauseTaskTool` | tasks |
| 21 | `completeTask` | [tools/tasks/complete_task.py](../../src/tlon/tools/tasks/complete_task.py) 的 `CompleteTaskTool` | tasks |
| 22 | `removeTask` | [tools/tasks/remove_task.py](../../src/tlon/tools/tasks/remove_task.py) 的 `RemoveTaskTool` | tasks |
| 23 | `getCurrentTask` | [tools/tasks/get_current_task.py](../../src/tlon/tools/tasks/get_current_task.py) 的 `GetCurrentTaskTool` | tasks |
<!-- END GENERATED: tool-registry -->

依赖倒置：`tlon.tools` 不许反向依赖 `tlon.agents`/`tlon.prototypes`，agents 域与 prototypes 域工具的端口（`AgentLifecycle` Protocol、`SpawnRejectedError`、`StopRejectedError`）声明在 [tools/agents/ports.py](../../src/tlon/tools/agents/ports.py)，原型端口（`PrototypeError`、`PrototypeSummary`）声明在 [tools/prototypes/ports.py](../../src/tlon/tools/prototypes/ports.py)；装配时由 `AgentRegistry` 把自身和原型库函数注入工具实例。

shell 与 filesystem 工具共享同一个 `DockerRuntime` 实例（[sandbox/docker_runtime.py](../../src/tlon/sandbox/docker_runtime.py)）。容器在装配时不启动，首次 execute/read/write 时惰性拉起；Docker daemon 不可用时这些工具返回 `error="Docker runtime not initialized"`，agent 本体照常启动。

## mail

### sendEmail

发送邮件，agent 唯一的邮件出口。工具本身不投递：它把参数打包进 `ToolResult.data`（`action="send_email"`），由 act stage 收割后声明 `MailEffect`，`EffectExecutor` 构造 `Mail` 并发布 `MAIL_NEW` 进入投递管道（见 docs/design/mail.md）。所有地址都在 `tlon.internal` 域：`<agent-id>@tlon.internal`、`<username>@tlon.internal`，广播用 `all@tlon.internal`。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `to` | array[string] | 是 | — | 收件人地址列表 |
| `cc` | array[string] | 否 | `[]` | 抄送地址列表 |
| `subject` | string | 否 | — | 邮件主题 |
| `content` | string | 是 | — | 邮件正文 |
| `is_complete` | boolean | 是 | — | `false`=中间进度，发完继续思考；`true`=最终回复，结束本轮循环 |
| `in_reply_to` | string | 否 | — | 被回复邮件的 id |

返回行为：恒 `success=True`，[data](../../data) 回显全部参数。投递异步——地址不存在时稍后会收到 `postmaster@tlon.internal` 的退信，退信是唯一失败信号。

注意事项：

- `is_complete=true` 时本轮不得同时调用其他工具，否则整轮被 act stage 拒绝（见总述）。
- 线程继承由引擎完成而非工具：`act_stage_collect` 构造 `MailEffect` 时，`in_reply_to`/`thread_id` 总是取 session 触发邮件的值——工具参数 `in_reply_to` 当前不被读取，另起线程靠新 session（新触发邮件）实现。
- `to` 传空数组时回退为回复触发邮件的发件人（`act_stage_collect` 的 reply 语义）。

### silence

不回复即结束本轮。当 LLM 决定不回信（循环寒暄、重复内容、无事可回）时调用，替代 `sendEmail` 的"发一封『我们该停了』的信"：工具本身无副作用，不投递任何邮件、不写记忆、不持久化任何状态；act stage 收割到成功的 `silence` 调用后把本轮标记完成（`is_complete=true` 语义），触发邮件沿既有 completion 路径被标记已读，循环就此收口。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `reason` | string | 否 | — | 停止原因（只进本轮上下文，不落任何存储） |

返回行为：恒 `success=True`，[data](../../data) 为 `{"silenced": true, "reason": ...}`。收割时不计入 `final_content`、不产生 interaction 记录——silence 是一轮"什么都没说"的结束。

注意事项：

- 与 `sendEmail` 互斥：同一轮同时调用 `silence` 和 `sendEmail`（无论 `is_complete` 取值）时，act stage 拒绝整轮并要求重新规划（见总述）。
- 无状态、无跨轮记忆：silence 不阻止后续新邮件触发新 session，也不对其他线程生效——它只结束当前这一轮。

## filesystem

三个工具都在 Docker 沙箱容器内操作，agent 不接触宿主机文件系统。路径统一经 [tools/filesystem/path_guard.py](../../src/tlon/tools/filesystem/path_guard.py) 的 `resolve_container_path` 校验：

- 相对路径先拼到容器工作目录（`DockerRuntimeConfig.working_dir`，默认 `/workspace`），再按 POSIX 语义规范化（折叠 [.](./.)/[..](./..)/重复分隔符，[..](./..) 越出根时在 `/` 处截断）。
- 解析结果必须等于或位于 `allowed_paths`（agent 配置 `tool.allowed_paths`，默认 `["/workspace"]`）之下，否则抛 `ValueError`，工具返回 `success=False` 和越界说明。
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

行为：文件不存在则创建，存在则整体覆盖；自动 `mkdir -p` 父目录。返回 `bytes_written`（UTF-8 字节数）、`existed_before`。失败：路径越界、容器写入错误。

### editFile

old_string/new_string 精确替换模型。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `file_path` | string | 是 | 目标路径 |
| `old_string` | string | 是 | 被替换的精确文本，必须在文件中唯一出现 |
| `new_string` | string | 是 | 替换后文本 |

行为：`old_string` 出现 0 次 → 失败（提示文本不在文件中）；出现 2 次及以上 → 失败（提示加上下文消歧）；唯一匹配则替换一次写回。返回 `replaced=true`、`old_length`、`new_length`。失败：文件不存在、非 UTF-8、路径越界。

## shell

### executeBashCommand

在 agent 的 Docker 容器中执行 bash 命令，支持管道、重定向、分号串接。

| 参数 | 类型 | 必填 | 默认 | 约束 |
|---|---|---|---|---|
| `command` | string | 是 | — | bash 命令 |
| `background` | boolean | 否 | false | true 时立即返回 task_id |
| `timeout` | integer | 否 | 60 | 1–300 秒 |

前台：返回 `exit_code`/`stdout`/`stderr`；`success` 等于 `exit_code == 0`，非零时 `error` 为 stderr 内容。

后台（`background=true`）：立即返回 `success=True` 和 `data={task_id, command(截断200字符), status="running"}`；任务在该 agent 的后台任务表里执行，上限 20 个（`_MAX_BACKGROUND_TASKS`），超限直接拒绝。任务表是 per-agent 的，agent 之间互不可见。

注意事项：

- 所有工具调用外层还有 `per_tool_timeout`（默认 30 秒，见总述）：前台命令即使 `timeout` 设到 300，超过 `per_tool_timeout` 也会被引擎先砍成 `Timeout after 30s`。长跑命令请用 `background=true`（后台任务不受 `per_tool_timeout` 约束，因为 `execute` 立即返回）。
- agent 停止时 `ExecuteBashTool.shutdown` 取消该 agent 全部未完成后台任务。

### checkBashTask

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `task_id` | string | 是 | `executeBashCommand` 后台模式返回的 id |

返回：执行中 → `status="running"`；完成 → `status="completed"` + `exit_code`/`stdout`/`stderr`；任务抛异常 → `success=False, status="failed"`。

注意事项：任务完成时 done callback 会把它从任务表中移除，因此查一个已完成任务通常得到 `success=False, error="Task '...' not found. It may have already completed."`——想拿到输出要在任务刚完成、尚未被移除的窗口内查询，实践中基本拿不到。后台任务的可靠产出方式是命令自己写文件，再用 `readFile` 读。

## memory

四个工具都操作当前 agent 自己的 `MemoryManager`（[memory/manager.py](../../src/tlon/memory/manager.py)），数据落在 `~/.tlon/{agent_id}/memory/`。搜索均为简单关键词匹配，不是向量检索。

### rememberFact

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `fact` | string | 是 | — | 事实的自然语言描述 |
| `fact_type` | string | 否 | `GENERAL` | enum：`USER_PREFERENCE` / `PROJECT_STATE` / `TECH_STACK` / `DECISION` / `GENERAL`（schema 声明 enum 但不做强校验） |
| `subject` | string | 否 | `用户` | 事实主体 |
| `predicate` | string | 否 | `提到` | 谓词/关系 |
| `object` | string | 否 | `fact` 前 50 字符 | 对象 |
| `importance` | number | 否 | 0.5 | 0.0–1.0，作为置信度存入 |

返回 `fact_id`，事实追加进 `episodic/facts.jsonl`。

### searchMemories

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `query` | string | 是 | — | 搜索关键词 |
| `limit` | integer | 否 | 5 | 最多返回条数 |

返回拼接文本（`data.results` 与 `message` 同内容），覆盖摘要与事实两类；无命中时仍 `success=True`，文本为「未找到相关记忆。」。

### compact

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `instructions` | string | 否 | 摘要重点，如要保留的话题或决策 |

把当前工作记忆强制压缩为长期摘要（`MemoryManager.generate_summary(force=True)`）。成功返回 `summary_id`；工作记忆内容不足时仍 `success=True`、`data={}`、`message="没有足够的内容生成摘要"`。采用快照+差量清除：摘要生成期间新写入的交互不会被清掉。

### forget

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `query` | string | 是 | — | 要删除内容的描述 |
| `confirm` | boolean | 否 | false | false=只预览，true=真删 |

两段式：`confirm=false` 按空格分词（长度 ≤1 的词丢弃，全丢则用整串）做关键词匹配，返回命中条数（摘要/事实分开计数）和确认提示；`confirm=true` 删除全部命中项，返回删除条数。无匹配时返回「未找到匹配 …」。删除不可撤销。

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

从 behavior prototype 派生一个新 agent 并立即启动（统一成员模型，ADR 0006：新 agent 是普通社会成员，永不删除——空闲睡眠、档案归档；是否开机启动由原型 spec 的 `:on :boot` 规则派生）。behavior 文件原样拷贝（spec 是 total 的，无 overrides），spawn 不能授予新工具。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `prototype` | string | 是 | prototype 名称（先 `listPrototypes` 查可用项） |
| `name` | string | 是 | 显示名，他人 @ 此名即可对话 |
| `params` | object | 否 | 模板参数，仅当 prototype 声明 `:parameters`；`:string` 传字符串，`:string-list` 传字符串数组 |

成功返回 `agent_id`（注册表生成，形如 `c-{prototype}-{8位hex}`）、`name`、`prototype`、`spawn_depth`。

拒绝行为（`SpawnRejectedError`，`success=False`，`data.reason` 为机器可读 token）：

| reason | 含义 |
|---|---|
| `untrusted_prototype` | 信任闸：staging 区未批准的 prototype 不能 spawn，需人类在 ops 中批准；先于一切预算检查 |
| `max_activations` | 全局同时运行 agent 数到顶（默认 8） |
| `max_spawn_depth` | spawn 链深度超限（默认 3，非 spawn 成员深度 0） |
| `session_quota` | 同一 (spawner, session) 配额耗尽（默认 2；账本只在内存，重启清零） |
| `config_reload_failed` | 新配置未能被 config provider 加载 |

prototype 不存在或参数校验失败时抛 `PrototypeError`，同样以 `success=False` 返回。session 归属经 structlog contextvars 里的 `session_id` 绑定，用于配额记账。

### stopAgent

停止一个 agent（统一成员模型，ADR 0006）：立即关闭并使其**睡眠**——home 目录（人格、记忆、邮箱）永久保留（归档），之后可被唤醒。停止永不删除数据。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `agent_id` | string | 是 | 要停止的 agent_id |

拒绝行为（`StopRejectedError`，`data.reason`）：`unknown_agent`（不存在）、`active_sessions`（drain 语义：有进行中的 session 就拒绝，停止临界区内在锁内复检一次）。

### listAgents

无参数。返回全部已注册 agent 的状态列表：运行状态（running/sleeping/waking）、来源 prototype、spawner 血缘。spawn/stop 前用来确认目标。

### writePrototype

创作新 behavior prototype，写入 `~/.tlon/prototypes-staging/`。新 prototype 一律 `trusted=false`（`status=pending_approval`），需人类在 ops 的 Agents 视图（`s` 审批）批准后才能被 `spawnAgent` 使用——LLM 写行为、人类把关的信任闸。原型不带人格（统一成员模型，ADR 0006）：人格是成员创建时数据，不属基因库。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `name` | string | 是 | — | 名称（字母/数字/`-`/`_`，全局唯一） |
| `behavior` | string | 是 | — | 行为规约 EDN 字符串，v1 schema 的 rules-form |
| `spec_type` | string | 否 | `react` | 写侧模板：`react`（带 `:loop` 的认知规则）/ `script`（无 `:loop` 的确定性规则） |
| `display_name` | string | 否 | `""` | 人类审批时看到的显示名 |
| `description` | string | 否 | `""` | 行为定位一句话描述 |

失败行为：EDN 校验失败抛 `PrototypeError`，返回 `success=False` 且 `data.hint` 携带对应 spec_type 的 schema 提示（`_BEHAVIOR_HINT`/`_SCRIPT_HINT`），供 LLM 修正后重试；未写入任何文件。behavior 是纯数据 spec，无法授予工具。EDN 字段细节见 docs/reference/dsl-reference.md。

## prototypes

### listPrototypes

无参数。列出 `~/.tlon/prototypes/`（启动时播种、用户可编辑的原型库）里的全部 behavior prototype，返回每项的 `name`/`display_name`/`description`/`trusted`。空库时 `success=True, data=[]`。