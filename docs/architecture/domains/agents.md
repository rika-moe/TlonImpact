# Agent 层

> 状态: ✅ 已验证
> 最后验证: 2026-07-22

---

## 职责

智能体实现：消息处理、Context 构建、LLM 调用、工具执行

---

## 模块结构

```
agents/
├── agent.py               # Agent 薄壳：生命周期、消息队列、think、子系统编排
├── spec.py                # AgentSpec：行为规约（语义唯一权威）+ behavior.tlon 加载
├── edn.py                 # EDN 解析器（L0 数据字面量 + Form list 类型，只解析不求值）
├── eval.py                # L1 受限求值器：白名单特殊形式 + 纯 builtin 注册表 + fuel/深度限制
├── prototypes.py          # Behavior prototype 库：种子 / 列举 / 实例化
├── spawn_errors.py        # SpawnRejectedError / StopRejectedError（spawn/stop 拒绝原因）
├── scheduler.py           # SessionScheduler：主循环 + session 生命周期 + 超时清理
├── loop_driver.py         # LoopDriver：ReAct 步进 / 工具收割
├── trigger.py             # TriggerEvaluator：响应触发判断
├── on_complete.py         # OnCompleteRunner：on-complete 动作表达式求值 + 执行（4b）
├── context_builder.py     # ContextBuilder：对话上下文 + 系统提示缓存
├── reflection.py          # ReflectionEngine：记忆反思子系统
├── session.py             # AgentSession：单个会话/任务状态
├── prompt_builder.py      # SystemPromptBuilder：纯函数式系统提示组合
├── prompt_context.py      # AgentPromptContext 数据对象
├── agent_registry.py      # AgentRegistry：纯机制（装配/运行表/锁/预算/空闲事实检测/执行 START/STOP 请求）
├── lifecycle.py           # StartRequest / StopRequest：亚当与 registry 之间的请求契约
├── adam.py                # Adam：内置策略 Member（PID 1）——boot / wake-on-mention / idle drain
└── tool_executor.py       # 工具执行器
```

> 注：`LLMClient` 位于 `base/llm_client.py`（基础设施层，供 agents 与 memory 共用）。

---

## 关键类

| 类 | 职责 | 路径 |
|----|------|------|
| `Agent` | 智能体薄壳（编排子系统） | `agents/agent.py` |
| `AgentSpec` | 行为规约（语义唯一权威） | `agents/spec.py` |
| `SessionScheduler` | 主循环、多 session 调度 | `agents/scheduler.py` |
| `LoopDriver` | ReAct step/collect | `agents/loop_driver.py` |
| `TriggerEvaluator` | 触发判断（表达式 / To·CC 地址数据模式） | `agents/trigger.py` |
| `OnCompleteRunner` | on-complete 动作求值 + 执行（纯求值收集，引擎执行） | `agents/on_complete.py` |
| `eval_form` / `validate_form` | L1 受限求值 / 静态校验（图灵不完备，fuel 计量） | `agents/eval.py` |
| `ContextBuilder` | 上下文组装、系统提示缓存 | `agents/context_builder.py` |
| `ReflectionEngine` | 记忆反思 | `agents/reflection.py` |
| `AgentRegistry` | 生命周期机制（装配/运行表/请求执行/空闲事实） | `agents/agent_registry.py` |
| `Adam` | 生命周期策略（boot/唤醒/drain 决策，内置 Member） | `agents/adam.py` |
| `LLMClient` | LLM API 调用 | `base/llm_client.py` |
| `ToolExecutor` | 工具注册执行 | `agents/tool_executor.py` |

---

## 动态 Registry（休眠/唤醒，阶段 6a + 阶段 D 机制化）

`AgentRegistry` 管理 activation（瞬态运行时），persona（`~/.tlon/{id}/`）由
AgentInfoProvider 提供全集，两者分离（见 [../../design/dsl-agents.md](../../design/dsl-agents.md)）。
阶段 D 起 registry 收缩为**纯机制**，三条策略线移入内置 Member **亚当**
（`agents/adam.py`，`member_id="adam"`，不来自用户 spec；设计定稿见
[../../design/members.md](../../design/members.md) 阶段 D）：

- **激活模式**（`AgentConfig.activation`）：`"always"`（默认）boot 即启动、永不休眠，
  行为与旧版一致；`"lazy"` 只注册不启动，被显式 @mention 时唤醒，空闲后回收。
  全 lazy 配置下 boot 后 0 个运行中 agent；
- **boot（策略在亚当）**：registry 的 `initialize()` 不再启动任何 agent（只订阅
  `AGENT_START_REQUEST` / `AGENT_STOP_REQUEST` + 启动 idle monitor）。亚当
  initialize 后对所有非 lazy 且未运行的注册 agent 发
  `AGENT_START_REQUEST{reason="boot"}`，registry 幂等执行——boot 因此变异步，
  server 不再等 agent 全部起来；
- **状态**：`get_agent_status` 对 lazy 未运行返回 `"sleeping"`，唤醒中 `"waking"`，
  always 未运行保持 `"unknown"`；
- **Wake-on-mail（亚当决策 + registry 执行，R1b）**：亚当订阅 `MAIL_DELIVERED`，
  仅对"已知配置 + 未运行"的收件 agent 发 `AGENT_START_REQUEST{reason="wake"}`；
  亚当无去重状态，registry 侧幂等（running/waking 跳过）。邮件已在收件箱持久等待，
  **不再携带 trigger_event、不做重投递**——registry 执行
  （`_fulfill_start_request`）：per-agent `asyncio.Lock` 串行化（并发请求只装配一次）
  → 复用 `_start_agent` 装配，新 agent boot 时扫 `mailbox.unread()` 逐封入队
  （邮箱即队列，邮件模型见 [../../design/mail.md](../../design/mail.md)）。
  观测：`AGENT_WAKING` / `AGENT_WOKEN`（含 wake_duration）/
  `AGENT_WAKE_FAILED` 事件 + 同名 snake_case 日志，唤醒耗时是投递事件 trace 树下的
  `agent.wake` span（boot 路径不发这三个事件，与机制化前一致）；
- **空闲回收（事实/决策/执行三段）**：registry 的 30s monitor 检测"lazy agent
  空闲超 `idle_timeout_seconds`（默认 600）且无存活 session" → 发**事实事件**
  `AGENT_IDLE{agent_id, idle_seconds}`（不停机）→ 亚当决策发
  `AGENT_STOP_REQUEST{reason="drain"}` → registry 执行（`_drain_agent`）：
  `agent.shutdown()` + 移出运行表（保留注册），发 `AGENT_SLEEPING`。drain 语义：
  有 ACTIVE/WAITING session 绝不停，且在 per-agent 锁下 shutdown 前重查一次
  （事实可能已过期）。Agent 只读暴露 `active_session_count` / `idle_seconds`
  （SessionScheduler 提供："活动"= session 创建 / 存活 session 的 `last_active_at` /
  session 完结；无 session 时从主循环启动算起）。休眠即主循环停止，reflection 自然停烧；
- **容器懒启动**：`_start_agent` 只构造 `DockerRuntime` 不启动容器；
  `DockerRuntime.execute` / `read_file` / `write_file` / `file_exists` 在 container
  为空时先 `ensure_running()`（幂等），daemon 不可用表现为工具报错而非启动失败。
  纯对话唤醒零容器成本。

---

## 动态 Registry（spawn/stop，阶段 6b）

agent 可从 prototype 现造临时工（contractor）并停止之，全程受 spawn 预算约束
（设计见 [../../design/dsl-agents.md](../../design/dsl-agents.md)）：

- **身份模型**：`AgentConfig.role`（`"resident"` 默认 / `"contractor"`）+
  `spawned_by` + `spawn_depth`（resident 为 0，contractor = spawner.depth + 1）。
  contractor 固定 `activation="lazy"` + `idle_timeout_seconds=300`（registry 构造参数
  `contractor_idle_timeout_seconds`），完工后靠 6a 的空闲 drain 自然回收；
- **spawn_contractor(prototype, name, spawner_id, session_id, overrides)**：
  预算检查 → 生成 id（`c-{prototype}-{8位uuid}`，prototype 片段过字符集清洗）→
  `instantiate()` 写盘 → 补 contractor 字段重存 agent.yaml →
  `AgentInfoProvider.reload_agent` 使配置对 router/status 可见 → `register` +
  `_start_agent` **立即启动** → 发 `AGENT_SPAWNED`（含 spawner/prototype/depth）；
- **stop_contractor(agent_id, stopped_by)**：仅 contractor 可停（resident 拒绝）；
  drain 语义与 6a 一致（有 ACTIVE/WAITING session 拒绝，per-agent 锁下 shutdown 前
  重查一次）→ shutdown → 注销 → **删除 home 目录** → 发 `AGENT_STOPPED`。
  空闲回收路径对 contractor 同样删目录（`_drain_agent` 分支，发 `AGENT_STOPPED`
  而非 `AGENT_SLEEPING`）；resident 回收只停进程不删目录。删除严格限定
  role==contractor，且 id 过字符集校验 + 解析路径必须在 tlon_home 直下（防路径注入）；
- **孤儿策略（reparent，不连坐）**：spawner 休眠/停止后其 contractor 继续独立运行，
  由各自 idle timeout 回收；`spawned_by` 是审计记录不是生命周期锁；
- **spawn 预算**（registry 构造参数，账本在内存、重启清零）：`max_activations=8`
  （运行中总数含 resident）、`max_spawn_depth=3`、`session_spawn_quota=2`（同一
  (spawner, session) 上限；session_id 由 `Agent._session_span` 经 structlog
  contextvars 绑定，后台工具 task 继承 context 可读，读不到时同一 spawner 共用一桶）。
  拒绝抛 `SpawnRejectedError`（`.reason` = max_activations/max_spawn_depth/session_quota），
  记 `spawn_rejected` 日志；权限不可升格——只能从 prototype 库 spawn，overrides 仅
  behavior 字段（tools 不在 spec schema 内）；
- **工具**（`tools/agents/`，注入 registry + spawner 自身 id，工具总数 18 → 21）：
  `spawnAgent(prototype, name, overrides?)`（overrides 的 JSON 字符串键递归转 EDN
  keyword 合并）、`stopAgent(agent_id)`、`listAgents()`（`get_agent_status` 扩展
  role/prototype/spawned_by/spawn_depth）。

---

## 行为规约（AgentSpec + behavior.tlon）

Agent 的行为是数据不是代码（见 [../../design/dsl-agents.md](../../design/dsl-agents.md)）。
`Agent` 构造时加载 `AgentSpec`（`agent.spec`），子系统全部从 spec 读行为参数：

- 文件路径：`~/.tlon/{agent_id}/config/behavior.tlon`（EDN 字面量，解析器在
  `agents/edn.py`；`:trigger` 节可为 map 或 L1 表达式 Form，表达式 load 时静态校验、
  运行时由 `agents/eval.py` 求值；`:version` 必须为 1，未知 key 忽略并告警，
  缺省字段继承 DefaultSpec）；
- 加载机制：`AgentSpec.load(agent)` —— 文件存在则解析（解析/校验失败 → warning 日志 +
  回落 DefaultSpec），否则直接 DefaultSpec；
- `AgentSpec.default(agent)`（DefaultSpec）从 `AgentConfig` + memory config 精确复刻历史行为，
  因此无 spec 文件时行为不变；
- 消费方：TriggerEvaluator（`spec.trigger`）、LoopDriver（`spec.loop`）、
  ContextBuilder / SystemPromptBuilder（`spec.context`）、ReflectionEngine
  （`spec.on_complete.reflect`，`enabled` 仍是运行时开关）、
  `Agent._save_interaction_to_memory`（`spec.on_complete.save_interaction`）、
  OnCompleteRunner（`spec.on_complete.actions`，见下文"on-complete 动作表达式"）。

边界（R1b）：proactive 已随邮件模型整套退役——`AgentConfig.proactive` 字段保留但
deprecated ignored（旧 agent.yaml 继续可加载），spec 侧无对应字段，旧 key
（`:proactive` / `:room-history`）加载时静默忽略；
`session_timeout_seconds` / `message_queue_size` 是构造期 infra，留在 `AgentConfig` 不进 spec。

### trigger 表达式（L1 受限求值器，`agents/eval.py`）

`:trigger` 节除数据 map 外可写 s-expr 表达式（edn.py 解析为 `Form`，与 vector 的 list
类型区分）。语义刻意图灵不完备：特殊形式仅 `if` / `when` / `let` / `and` / `or`，
无 `def` / `lambda` / `loop` / `recur`（递归无从表达）；其余形式头必须命中
`TRIGGER_BUILTINS` 注册表——6 个纯谓词/函数（`addressed-to-me?`（To 含我）/
`cc-ed?`（CC 含我）/`content-matches?` / `contains?` / `=` / `not`，`Builtin(fn, pure=True)`
带纯度标记，副作用动作不注册，trigger 求值无副作用 by construction）。求值每步耗
fuel（默认 1000，耗尽 `FuelExhaustedError`）、深度限 100（`EvalDepthError`）。
load 时 `validate_form` 静态校验（形式头白名单 + Symbol 作用域 + `let` 形状，不执行），
`writePrototype` 的 schema 校验因此自动覆盖表达式。运行时 TriggerEvaluator 以
env=`{msg, self}` 求值，真值即响应；`EvalError` 安全默认不响应并记
`trigger_eval_failed` warning，命中/未命中记 `trigger_expr_result` debug。

### on-complete 动作表达式（阶段 4b，`agents/on_complete.py`）

`:on-complete` 节新增 `:actions`（vector of Form），每个 Form 是一个动作
（`(spawn "prototype" {:name "显示名"})` / `(emit "内容" ["地址" ...])`，
两动作的第二参数均可选，emit 缺省收件人时回复触发邮件的发件人）或
`(when 条件 动作...)`。延续"DSL 只描述，引擎执行"：

- **求值保持纯**：`spawn` / `emit` 注册为 `Builtin(pure=False)`，但求值时不做任何
  副作用——校验参数后把 `ActionRequest` 数据收集进 collector
  （`make_actions_builtins(collector)`；`ACTIONS_BUILTINS` 是无 collector 的校验用
  注册表）。求值完成后由引擎异步逐条执行；
- **两位置纯度校验**：load 时 `validate_action_form` 静态校验——`when` 条件位只许
  纯谓词（`TRIGGER_BUILTINS`，动作在条件位直接拒绝），动作位必须是动作 Form 且参数为
  纯表达式；顶层只许动作 Form 或单层 `when`（不支持嵌套 `when`）；
- **env**：`msg`（触发邮件 Mail，经 `session.trigger_message` 传递）、`result`
  （本轮最终回复字符串）、`self`；`content-matches?` 同时接受 Mail 与字符串；
- **执行点**：session 以 `is_complete` 收尾时，在 `_save_interaction_to_memory` 之后、
  `session.mark_completed()` 之前（LoopDriver 收尾处，仍在 `_session_span` 内——
  spawn 的 session 配额因此自然归因）由 `OnCompleteRunner` 执行一次；
- **动作语义**：`spawn` 调 `registry.spawn_contractor`（spawner_id=当前 agent，
  session_id 读 structlog contextvars）——**信任闸与 spawn 预算全部生效**
  （untrusted / 超预算 → 拒绝记日志，不影响 session）；链式 spawn（contractor 自己的
  on-complete 再 spawn）由 `max_spawn_depth=3` 硬顶。Agent 新增可选构造参数
  `registry`（由 `_start_agent` 注入自身；测试直建为 None，spawn 动作记日志跳过）。
  `emit` 调 `agent._send_mail`（回复头继承触发邮件）；
- **异常隔离**：单个动作失败记 `on_complete_action_failed` error（含动作与错误）并继续
  后续动作；求值失败记 `on_complete_eval_failed`，绝不影响 session 生命周期。观测：
  `on_complete_actions` info 日志（动作数/类型），spawn 走 registry 现有
  `agent_spawned` 事件链。

---

## Behavior Prototype 库

预制行为原型 = 部分 AgentSpec 覆盖 + 人格文件模板，让人类和 agent 以"模板 + 覆盖"
方式创建新 agent（设计见 [../../design/dsl-agents.md](../../design/dsl-agents.md) 阶段 5）。

- **目录形式**：`tlon/prototypes/{name}/`（包内数据，随 wheel 分发），每个含
  `behavior.tlon`（只写与 DefaultSpec 的差异字段）、`IDENTITY.md` / `SOUL.md` / `RULES.md`
  人格模板、`prototype.edn` 元数据（`{:name :display-name :description :trusted :author}`，
  edn.py 解析；信任语义见下文"信任闸"）。
  首批 3 个：`deep-thinker`（:max-iterations 25、reflect 10 分钟）、
  `fast-responder`（:max-iterations 3、:knowledge false、:room-history 5）、
  `archivist`（reflect 5 分钟、:save-interaction true）；relay 因需要 `:llm false`
  逃生门而推迟；
- **种子**：`seed_prototypes()` 在 ServerApp 初始化早段把包内 prototypes 经
  `importlib.resources` 拷贝到 `~/.tlon/prototypes/`（幂等，同名文件跳过——用户可改可加，
  不覆盖）；wizard 创建入口也会先种子（mirror 是独立进程）；
- **发现**：`list_prototypes()` 读 `~/.tlon/prototypes/`（非包内——用户修改可见），
  暴露给 agent 的是 `listPrototypes` 工具（`tools/prototypes/list_prototypes.py`，
  随每个 agent 注册）；暴露给人类的是 wizard 人员管理菜单"从 Prototype 创建"；
- **实例化**：`instantiate(prototype_name, agent_id, agent_name, overrides, proactive)`
  ——校验 prototype 存在与 agent_id 不冲突；人格三文件**拷贝**（非引用，行为冻结于创建时刻）
  到 `~/.tlon/{agent_id}/config/`；prototype 的 behavior.tlon 解析后在 **EDN 数据层**
  与 overrides 深合并（map 按键递归合并，其余替换，keyword 类型保留）再写为实例自有
  behavior.tlon；agent.yaml 走与 wizard 相同的 `_yaml_save` 路径，
  `AgentConfig.prototype` 记录来源 prototype；
- **生效时机**：热生效（P3）——wizard/mirror 创建后 best-effort 通知运行中的服务端
  （`POST /admin/config/agents/{id}/reload` → `AgentRegistry.adopt_agent`），
  新 agent 无需重启即可用；服务端未运行时静默跳过，重启后生效作为兜底。

---

## 信任闸（staging + 审批，阶段 4a）

信任分级落地"代码签名"模型（设计见 [../../design/dsl-agents.md](../../design/dsl-agents.md)
阶段 4a 与操作系统类比附录）：包内/用户 prototype = 签名二进制（可信，可直接 spawn）；
LLM 现写规约 = 未签名二进制（staging + 人类显式批准的 Gatekeeper 模型）。

- **信任标记**：`prototype.edn` 增加 `:trusted true|false`（包内 3 个 builtin 均为 true）。
  `PrototypeInfo` 增加 `trusted` / `author` 字段；缺 `:trusted` 的用户手写 prototype
  默认可信（手写即信任），只有 staging 区写出 `false`；
- **staging 区**：`~/.tlon/prototypes-staging/`（`TlonPaths.prototypes_staging_dir()`，
  无副作用解析）。`write_staging_prototype(name, behavior_edn, persona, display_name,
  description, author_id)` **先校验后落盘**——behavior 须过 EDN 解析 + `AgentSpec.from_edn`
  schema 校验（`:version` 必填、字段类型、`:loop {:mode}` 限已实现值），失败抛
  `PrototypeError` 且一个字节都不写盘；name 字符集校验（alnum + `-_`，防路径注入）、
  与可信库和 staging 双向查重；prototype.edn 写入 `:trusted false` + `:author agent_id`；
- **审批**：`approve_prototype(name)` 把目录移到 `~/.tlon/prototypes/` 并把 metadata
  改写为 `:trusted true`（`:author` 保留）；`reject_prototype(name)` 删除 staging 目录
  （防护同 contractor home 删除：字符集 + resolve 必须在 staging 根直下）。
  人类入口是 wizard 人员管理菜单的"规约审批"（列表含作者与描述 → 预览 behavior.tlon
  与人格文件 → 批准/拒绝）；
- **spawn 闸**：`spawn_contractor` 在任何预算检查之前先查信任——prototype 不可信即抛
  `SpawnRejectedError(reason="untrusted_prototype")`；`spawnAgent` 工具把该拒绝翻译为
  可读错误（提示需人类在 mirror 批准）。`listPrototypes` 输出带 `trusted` 字段；
- **`writePrototype` 工具**（`tools/agents/write_prototype.py`，工具总数 21 → 22）：
  参数 `name` / `behavior`（EDN 字符串，必填）+ `display_name` / `description` /
  `identity` / `soul` / `rules`；成功返回 `status: pending_approval`，校验失败返回
  具体错误 + schema 提示（LLM 可修正重试）；`author_id` 绑定装配它的 agent；
- **规约版本哈希**：`AgentSpec.source_hash`——`AgentSpec.load` 对所读 behavior.tlon
  内容计算 sha256 短哈希（前 12 位）；无文件或文件损坏走 DefaultSpec 时为 `"default"`。
  该哈希进入 `agent.session` span attributes（`spec_hash`）、`agent_started` 日志与
  `AGENT_STARTED` 事件 payload——任何一条 trace 都能查到当时生效的规约版本。

---

## Agent 核心流程

```
MAIL_DELIVERED（收件人=我）→ TriggerEvaluator.should_respond? → 入队（有界，满则丢弃；
邮箱仍是持久队列，boot 时扫 mailbox.unread() 补投）
→ SessionScheduler 主循环：每封邮件一个 session → LoopDriver 步进（Think → 并发派发工具 → 收割）
→ sendEmail 回复（in_reply_to/thread_id 继承触发邮件）→ session 完成 → mailbox.mark_read
→ 保存记忆 → ReflectionEngine 定期反思
```

**Tool 执行**: Agent 直接调用 `tool_executor.execute()`（模块内聚合），非 EventBus RPC。

**韧性配置**（`AgentConfig`）：`max_iterations`（ReAct 轮数上限，默认 10）、
`max_consecutive_errors`（连续 LLM 错误上限，默认 3）、`session_timeout_seconds`
（卡死会话清理，默认 1800）、`message_queue_size`（入队背压，默认 100）。

---

## 响应判断逻辑

1. 自己的邮件永不响应（防自循环）
2. `trigger.when` 表达式命中（优先级最高）
3. 数据模式：To 或 CC 含我的地址（`trigger.on_mention` 闸门）

---

## 文档索引

| 文档 | 目的 | 深度 |
|------|------|------|
| [../../design/agents.md](../../design/agents.md) | Agent 系统设计 | L1 |

---

## 验证清单

- [x] Agent 配置字段与代码一致
- [x] `RoleConfig` 已完全移除，角色信息合并入 narrative 文本
- [x] Tool 注册机制正确
