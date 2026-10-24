# Tlon 模块程序参考

行为是事件入口声明；工作流程是普通模块函数。语言语法、模块可见性和求值规则见 [tlosure-reference](tlosure-reference.md)。宿主模型、工具、记忆及领域操作见 [tools-reference](tools-reference.md)。

## 1. 行为入口

```clojure
{:version 2
 :handlers {:mail-delivered my.worker/respond :boot my.worker/boot}
 :fuel 1000
 :execution {:level :L1}}
```

`:version` 必须为 `2`；`:handlers` 是非空事件→限定函数名映射。`:fuel` 为 1..10000 的共享求值预算，默认 1000。`:execution` 可省略，声明时只接受 `:level :L0/:L1/:L2`；实例化投影到部署配置，运行中改行为不能提权。模型与根 token 预算来自成员部署配置。未知字段、旧版本和旧规则格式一律拒绝。

模块源文件位于 `modules/`，完整源码闭包在受理时冻结。导入、导出、初始化纯度、路径边界和处理函数均经静态校验；源码更新只影响新受理的程序。

## 2. 事件与返回值

入口接收物化的事件 map，含 `:mail`、`:self`、`:sender` 等相应数据。普通函数自行判断条件、读取上下文、调用模型、执行工具或投递邮件。子函数返回只结束自身；只有根入口返回的 `:next` 影响成员调度，具体形状见 [工作流程设计](../design/programmable-workflows.md)。

<!-- BEGIN GENERATED: dsl-on-events -->
合法值共 32 个（`:boot` + 全部 `EventType` 成员，转换逻辑 `member_spec._handler_event` / `MemberHandler.event_name`）：（本区块由 `scripts/gen_reference_docs.py` 生成，勿手改；改了会被 `--check` 打回）

| `:on` 关键字 | 事件值 | 说明 |
|---|---|---|
| `:boot` | — | 启动入口：映射 `None`，订阅就绪后运行一次，`msg` / `event` 均为 `nil` |
| `:mail-new` | `mail.new` | A mail was committed by the post office (notification only) |
| `:mail-delivered` | `mail.delivered` | A mail landed in one recipient's inbox |
| `:mail-endorsed` | `mail.endorsed` | A delivered mail endorses another mail (reputation signal, see docs/design/mail.md) |
| `:agent-join` | `agent.join` |  |
| `:agent-leave` | `agent.leave` |  |
| `:agent-response` | `agent.response` |  |
| `:agent-started` | `agent.started` |  |
| `:agent-stopped` | `agent.stopped` |  |
| `:agent-retired` | `agent.retired` | Offline handover inventory after permanent exit |
| `:agent-error` | `agent.error` |  |
| `:agent-state-changed` | `agent.state_changed` |  |
| `:agent-waiting` | `agent.waiting` | Waiting remains waiting when its runtime is unloaded |
| `:agent-waking` | `agent.waking` |  |
| `:agent-woken` | `agent.woken` |  |
| `:agent-wake-failed` | `agent.wake_failed` |  |
| `:agent-sleeping` | `agent.sleeping` |  |
| `:agent-start-request` | `agent.start_request` |  |
| `:agent-stop-request` | `agent.stop_request` |  |
| `:agent-idle` | `agent.idle` |  |
| `:agent-spawned` | `agent.spawned` |  |
| `:agent-noisy` | `agent.noisy` |  |
| `:mail-orphan-scan` | `mail.orphan_scan` |  |
| `:agent-llm-request` | `agent.llm_request` |  |
| `:agent-llm-response` | `agent.llm_response` |  |
| `:tool-error` | `tool.error` |  |
| `:tool-execute-start` | `tool.execute_start` |  |
| `:tool-execute-end` | `tool.execute_end` |  |
| `:system-startup` | `system.startup` |  |
| `:system-shutdown` | `system.shutdown` |  |
| `:system-error` | `system.error` |  |
| `:config-updated` | `config.updated` |  |
<!-- END GENERATED: dsl-on-events -->

## 3. 宿主词汇

模型调用 `model-call` 只返回数据，`tool-execute` 执行明确工具请求。工具目录和写入权限由宿主绑定。`finish-turn` 是纯操作，返回局部完成信号；它不会停止成员。`sleep-until`、`exit-agent` 和 `run-loop` 不再存在。睡眠或退出由入口结果表达。

`member-context` 是固定结构的只读操作，返回知识、教训、技能索引、当前任务、最近交互摘要、邮件历史与可用工具名。程序决定如何组织这些值；宿主不解释 section 配置。文档和标准库源码可通过 workspace 的只读映射及现有读取工具查询。

<!-- BEGIN GENERATED: dsl-builtins-pure -->
`SCRIPT_BUILTINS`（30 个，按源码定义顺序）：（本区块由 `scripts/gen_reference_docs.py` 生成，勿手改；改了会被 `--check` 打回）

| 名称 | pure | 说明 |
|---|---|---|
| `assert` | 是 | Reject invalid data without introducing a host effect. |
| `integer?` | 是 | Test for an integer value, excluding booleans. |
| `json-encode` | 是 | Encode strict JSON data, accepting EDN keywords only as object keys. |
| `assoc` | 是 | Return a map/vector copy with key-value pairs applied in order. |
| `dissoc` | 是 | Return a map copy without the supplied keys; missing keys are harmless. |
| `conj` | 是 | Append to a vector, or add two-element entry vectors to a map copy. |
| `keys` | 是 | Return map keys in insertion order. |
| `vals` | 是 | Return map values in insertion order, matching ``keys``. |
| `=` | 是 |  |
| `not` | 是 |  |
| `contains?` | 是 |  |
| `get` | 是 | Read ``key`` from a dict payload; missing keys yield ``default``. |
| `str` | 是 | Concatenate arguments into one string. |
| `split` | 是 | Split a string on a separator into a vector of strings. |
| `join` | 是 | Join a vector of strings with a separator into one string. |
| `substring` | 是 | ``s[start:end]`` — Python slice semantics, total by construction. |
| `count` | 是 | Item count of a vector / map / string; ``nil`` counts as 0. |
| `+` | 是 |  |
| `-` | 是 |  |
| `*` | 是 |  |
| `/` | 是 | Left-fold float division: ``(/ a b c)`` = ``a / b / c``. |
| `inc` | 是 |  |
| `dec` | 是 |  |
| `<` | 是 |  |
| `>` | 是 |  |
| `<=` | 是 |  |
| `>=` | 是 |  |
| `mail-field` | 是 |  |
| `from?` | 是 | Compare an explicit mail record's sender with an explicit address. |
| `content-matches?` | 是 | Regex-search a mail's content or a plain string (e.g. ``result``). |
<!-- END GENERATED: dsl-builtins-pure -->

### 社会策略的只读词汇

策略仍是独立纯函数式计算，不驱动 Agent 工作流程。

<!-- BEGIN GENERATED: dsl-builtins-policy -->
`POLICY_BUILTINS`（27 个，按源码定义顺序）：（本区块由 `scripts/gen_reference_docs.py` 生成，勿手改；改了会被 `--check` 打回）

| 名称 | pure | 说明 |
|---|---|---|
| `assert` | 是 | Reject invalid data without introducing a host effect. |
| `integer?` | 是 | Test for an integer value, excluding booleans. |
| `json-encode` | 是 | Encode strict JSON data, accepting EDN keywords only as object keys. |
| `assoc` | 是 | Return a map/vector copy with key-value pairs applied in order. |
| `dissoc` | 是 | Return a map copy without the supplied keys; missing keys are harmless. |
| `conj` | 是 | Append to a vector, or add two-element entry vectors to a map copy. |
| `keys` | 是 | Return map keys in insertion order. |
| `vals` | 是 | Return map values in insertion order, matching ``keys``. |
| `=` | 是 |  |
| `not` | 是 |  |
| `contains?` | 是 |  |
| `get` | 是 | Read ``key`` from a dict payload; missing keys yield ``default``. |
| `str` | 是 | Concatenate arguments into one string. |
| `split` | 是 | Split a string on a separator into a vector of strings. |
| `join` | 是 | Join a vector of strings with a separator into one string. |
| `substring` | 是 | ``s[start:end]`` — Python slice semantics, total by construction. |
| `count` | 是 | Item count of a vector / map / string; ``nil`` counts as 0. |
| `+` | 是 |  |
| `-` | 是 |  |
| `*` | 是 |  |
| `/` | 是 | Left-fold float division: ``(/ a b c)`` = ``a / b / c``. |
| `inc` | 是 |  |
| `dec` | 是 |  |
| `<` | 是 |  |
| `>` | 是 |  |
| `<=` | 是 |  |
| `>=` | 是 |  |
<!-- END GENERATED: dsl-builtins-policy -->

<!-- BEGIN GENERATED: bridge-query-ports -->
注册表读取使用普通只读 Operation：`member-sleeping`、`member-running`、`member-lazy` 接受 `{:agent_id "…"}`，`member-roster` 接受 `{}`。调用结果经统一 invocation 回执采样与重放；不再注入独立宿主谓词或写入 effects 表。（本区块由 `scripts/gen_reference_docs.py` 生成，勿手改；改了会被 `--check` 打回）
<!-- END GENERATED: bridge-query-ports -->

## 4. 资源与恢复

模型、工具、压缩及嵌套函数共享根预算和截止时间；持久执行身份与回执来自 ProgramStore。未知外部结果阻塞，不能改根身份绕过。RunJournal 只读 ProgramStore 运行投影，不参与恢复或追加新执行记录。

<!-- BEGIN GENERATED: dsl-fuel-defaults -->
语言级求值预算常量（`tlosure/eval.py`，3 个；上文的默认值/上限数字以此表为准）：（本区块由 `scripts/gen_reference_docs.py` 生成，勿手改；改了会被 `--check` 打回）

| 常量 | 值 | 用途 |
|---|---|---|
| `DEFAULT_FUEL` | 1000 | 普通程序求值的默认预算 |
| `MAX_FUEL` | 10000 | 程序 `:fuel` 自声明的硬顶（超过即加载拒绝） |
| `DEFAULT_MAX_DEPTH` | 100 | form 嵌套深度上限 |
<!-- END GENERATED: dsl-fuel-defaults -->

## 5. 原型模板

`:parameters` 属于写侧模板元数据；实例化会渲染行为和全部模块源码并移除声明，再校验完整 v2 程序包。运行时不解释模板参数。内置原型使用普通模块组合，纯路由和转发不调用模型。
