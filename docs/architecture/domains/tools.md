# Tools 层

> 状态: ✅ 已验证
> 最后验证: 2026-08-13

---

## 职责

Agent 可调用的工具集：沙箱命令与文件操作、邮件出口、agent 生命周期
（spawn/stop）、prototype 创作、记忆操作、个人任务管理。工具是数据化能力的
唯一入口，Agent 经 `ToolExecutor` 直接调用（模块内聚合，非 EventBus RPC）。

---

## 模块结构

```
tools/
├── base.py            # Tool ABC（name/description/parameters JSON Schema + execute + initialize/shutdown 钩子
│                      #   + to_openai_definition + validate_arguments）+ ToolResult dataclass
├── shell/             # execute_bash.py：ExecuteBashTool + CheckBashTaskTool（同文件）
├── filesystem/        # read_file.py / write_file.py / edit_file.py + path_guard.py（容器路径白名单解析，纯 posixpath 字符串处理）
├── mail/              # send_email.py：SendEmailTool
├── agents/            # ports.py（AgentLifecycle Protocol + Spawn/StopRejectedError）+ spawn_agent.py / stop_agent.py / list_agents.py / write_prototype.py
├── prototypes/        # ports.py（PrototypeError + PrototypeSummary Protocol）+ list_prototypes.py
├── memory/            # remember_fact.py / search_memories.py / compact.py / forget_memory.py（MemoryManager 仅 TYPE_CHECKING 引用）
└── tasks/             # manager.py（TaskManager：单 agent 任务 CRUD，JSONL 持久化到 agent_tasks_file）+ models.py + 7 个工具文件
```

---

## 关键类

| 类 | 职责 | 路径 |
|----|------|------|
| `Tool` (ABC) | 工具契约：name/description/parameters + execute + 生命周期钩子 | `tools/base.py:44` |
| `ToolResult` | 工具返回（success/output/error） | `tools/base.py:26` |
| `ToolExecutor` | 工具注册与执行（**在 agents 域**） | `agents/tool_executor.py:37` |
| `TaskManager` | 单 agent 任务 CRUD，JSONL 持久化 | `tools/tasks/manager.py:33` |

---

## 注册机制

- `ToolExecutor` 在 **agents 域**（`agents/tool_executor.py:37`）：
  `_tools: dict[str, Tool]`，`register_tool`（:54，重名覆盖+告警）、
  `execute`（:142，包 `tool.{name}` OTel span + 结构化日志）、
  `_execute_inner`（:173，先 JSON Schema `validate_arguments` 再执行，异常一律
  转 `ToolResult(success=False)` 不抛出）、`initialize/shutdown` 逐个调工具钩子。
- **每 agent 一个 ToolExecutor 实例**，装配点是
  `AgentRegistry._build_cognitive`（`agents/agent_registry.py:577-618`）——
  22 个工具按固定顺序注册，随后 `tool_executor.initialize()`。
- 注入方式（agent_registry.py:581-610）：shell/filesystem 共享同一个
  `DockerRuntime` 实例；`allowed_paths = config.tool.allowed_paths`（默认
  `["/workspace"]`，base/config.py:127-144）；Spawn/Stop/List 注入
  `registry=self`（结构满足 `AgentLifecycle` Protocol）；ListPrototypes/
  WritePrototype 注入裸 callable（`lister=list_prototypes` /
  `writer=write_staging_prototype`）；memory 工具注入 `MemoryManager`；
  tasks 工具注入 `TaskManager`。

---

## 工具清单（22 个，name 以代码为准）

| 工具 name | 类 / 位置 | 一句话 |
|---|---|---|
| `executeBashCommand` | ExecuteBashTool, shell/execute_bash.py:34 | 在 agent 的 Docker 容器执行 bash；`background=true` 立即返回 task_id |
| `checkBashTask` | CheckBashTaskTool, shell/execute_bash.py:164 | 查询本 agent 后台 bash 任务状态/结果 |
| `readFile` | ReadFileTool, filesystem/read_file.py:33 | 读沙箱文件（支持起始行/行数），allowed_paths 白名单 |
| `writeFile` | WriteFileTool, filesystem/write_file.py:30 | 写/重写沙箱文件，自动建父目录 |
| `editFile` | EditFileTool, filesystem/edit_file.py:30 | old_string→new_string 精准替换（须唯一匹配） |
| `sendEmail` | SendEmailTool, mail/send_email.py:25 | agent 唯一邮件出口；`is_complete=true` 结束思考轮；execute 只返回数据（:123-133），实际投递由 ReAct 循环/EffectExecutor 检测该调用后完成，失败以 postmaster 退信为信号 |
| `listPrototypes` | ListPrototypesTool, prototypes/list_prototypes.py:26 | 列出 behavior prototype（名称/显示名/描述/trusted） |
| `spawnAgent` | SpawnAgentTool, agents/spawn_agent.py:35 | 从 prototype 派生 contractor 并立即启动；注入 `registry + spawner_id` |
| `stopAgent` | StopAgentTool, agents/stop_agent.py:29 | 停止 contractor（shutdown+删 home，仅 contractor） |
| `listAgents` | ListAgentsTool, agents/list_agents.py:28 | 列出全部 agent（running/sleeping/waking、resident/contractor、prototype、spawn 元数据） |
| `writePrototype` | WritePrototypeTool, agents/write_prototype.py:59 | 创作 prototype 写 staging 区（`:trusted false`，`author_id=装配它的 agent`），待人类审批 |
| `rememberFact` | RememberFactTool, memory/remember_fact.py:31 | 记重要事实到长期记忆 |
| `searchMemories` | SearchMemoriesTool, memory/search_memories.py:31 | 搜索长期记忆摘要与事实 |
| `compact` | CompactTool, memory/compact.py:31 | 压缩工作记忆为长期摘要 |
| `forget` | ForgetMemoryTool, memory/forget_memory.py:31 | 删除长期记忆条目 |
| `createTask`/`listTasks`/`startTask`/`pauseTask`/`completeTask`/`removeTask`/`getCurrentTask` | tasks/*.py（7 个工具文件） | 单 agent 个人任务管理，TaskManager（tasks/manager.py:33）JSONL 持久化到 `~/.tlon/{id}/tasks.jsonl` |

---

## 后台任务表（ 改动后现状）

- `ExecuteBashTool` 持有**实例级**
  `_background_tasks: dict[str, asyncio.Task[ExecutionResult]]`
  （execute_bash.py:74），经 `background_tasks` property（:77）暴露；上限
  `_MAX_BACKGROUND_TASKS = 20`（:31）；task 完成经 `add_done_callback` 自动从表
  移除（:150）；`shutdown()` 取消未完成任务（:81-89）。
- `CheckBashTaskTool` 构造时收**同一个 dict 引用**（execute_bash.py:167-189；
  装配在 agent_registry.py:584：
  `CheckBashTaskTool(execute_bash_tool.background_tasks)`）——表的所有权在
  ExecuteBashTool 实例，check 工具只读视图，跨 agent 零可见性。

---

## tools 与 agents 的依赖方向（ 已收敛）

- **agents→tools（合法）**：`agents/agent_registry.py` 顶层 import
  全部 22 个工具类；`tool_executor.py:32`、`loop_driver.py:77`、
  `stages/act.py:37` import `tools.base`；`member_runtime.py:93` import
  `tools.tasks.manager`。
- **tools→agents：零依赖**。端口与异常下沉 tools（依赖倒置）：
  `tools/agents/ports.py` 提供 `AgentLifecycle` Protocol
  （`spawn_contractor`/`stop_contractor`/`get_all_agent_status`）与
  `SpawnRejectedError`/`StopRejectedError`（自 agents 平移，
  `agents/spawn_errors.py` 已删除）；`tools/prototypes/ports.py` 提供
  `PrototypeError` 与 `PrototypeSummary` Protocol。Spawn/Stop/List 工具的
  构造注解是 `AgentLifecycle`（registry 实例结构满足）；list/write
  prototypes 工具收裸 callable，由装配点注入 `agents.prototypes` 的函数。
- import-linter 契约为干净的 "tools 不依赖 agents/app/prototypes"
  （pyproject.toml），**无任何 ignore_imports 豁免**（原 8 条已于  删除）。

---

## 文档索引

| 文档 | 目的 | 深度 |
|------|------|------|
| [agents.md](./agents.md) | 工具在 ReAct 循环中的调用侧（LoopDriver 步进/收割） | L2 |
| [sandbox.md](./sandbox.md) | shell/filesystem 工具背后的 DockerRuntime | L2 |
| [../../design/mail.md](../../design/mail.md) | sendEmail 的投递与退信语义 | L1 |

---

## 验证清单

- [x] 22 个工具 name/类/位置与代码逐一核对一致
- [x] 注册机制（每 agent 一个 ToolExecutor，装配点 `_build_cognitive`）与代码一致
- [x] 后台任务表实例级共享语义（ExecuteBashTool 持有、CheckBashTaskTool 只读）与代码一致
- [x] import-linter 契约无豁免，tools→agents 零依赖（ 收敛后）
