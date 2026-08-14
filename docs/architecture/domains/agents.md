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
├── member_spec.py         # MemberSpec：统一行为规约（语义唯一权威）+ 单校验闸 + 派生 member_type
├── member_runtime.py      # MemberRuntime：统一解释器（规则分发/认知装配/邮箱/唤醒扫读）
├── loader.py              # behavior.tlon 加载（单一读取路径，total 校验）
├── edn.py                 # EDN 解析器（L0 数据字面量 + Form list 类型，只解析不求值）
├── eval.py                # L1 受限求值器：白名单特殊形式 + 纯 builtin 注册表 + fuel/深度限制
├── prototypes.py          # Behavior prototype 库：种子 / 列举 / 实例化（纯拷贝）
├── behavior_templates.py  # rules 形态行为模板（wizard 默认 / 居民个性化狗粮）
├── behavior_template.py   # 模板层：:parameters 声明 + ${var} 渲染
├── scheduler.py           # SessionScheduler：主循环 + session 生命周期 + 超时清理
├── loop_driver.py         # LoopDriver：ReAct 步进 / 工具收割 / StageOutcome 应用（能力注入，无宿主回指）
├── executor.py            # EffectExecutor：引擎侧执行 stage 声明的外部效果（发信/写记忆/已读游标，C2）
├── on_complete.py         # OnCompleteRunner：session 完成动作表达式求值 + 执行
├── trigger.py             # AgentIdentity：成员身份 / 邮件地址解析
├── context_builder.py     # ContextBuilder：对话上下文 + 系统提示缓存
├── reflection.py          # ReflectionEngine：记忆反思子系统
├── session.py             # AgentSession：单个会话/任务状态
├── prompt_builder.py      # SystemPromptBuilder：纯函数式系统提示组合
├── prompt_context.py      # AgentPromptContext 数据对象
├── agent_registry.py      # AgentRegistry：纯机制（装配/运行表/锁/预算/空闲事实检测/执行 START/STOP 请求 + spec 派生生命周期）
├── lifecycle.py           # StartRequest / StopRequest：亚当与 registry 之间的请求契约
├── adam.py                # Adam：内置策略 Member（PID 1）——boot / drain（wake 已移入 registry）
└── tool_executor.py       # 工具执行器
```

> 注：`LLMClient` 位于 `base/llm_client.py`（基础设施层，供 agents 与 memory 共用）。

---

## 关键类

| 类 | 职责 | 路径 |
|----|------|------|
| `MemberSpec` | 统一行为规约（语义唯一权威）+ 单校验闸 | `agents/member_spec.py` |
| `MemberRuntime` | 统一解释器（规则分发 / 认知装配 / 唤醒扫读） | `agents/member_runtime.py` |
| `SessionScheduler` | 主循环、多 session 调度 | `agents/scheduler.py` |
| `LoopDriver` | ReAct step/collect + StageOutcome 应用（能力注入，无宿主回指） | `agents/loop_driver.py` |
| `OnCompleteRunner` | session 完成动作求值 + 执行（纯求值收集，引擎执行） | `agents/on_complete.py` |
| `eval_form` / `validate_form` | L1 受限求值 / 静态校验（fuel 计量） | `agents/eval.py` |
| `ContextBuilder` | 上下文组装、系统提示缓存 | `agents/context_builder.py` |
| `ReflectionEngine` | 记忆反思 | `agents/reflection.py` |
| `AgentRegistry` | 生命周期机制（装配/运行表/请求执行/空闲事实） | `agents/agent_registry.py` |
| `Adam` | 生命周期策略（boot/drain 决策，内置 Member；wake 为 registry 的 spec 派生机制） | `agents/adam.py` |
| `LLMClient` | LLM API 调用 | `base/llm_client.py` |
| `ToolExecutor` | 工具注册执行 | `agents/tool_executor.py` |

---

## 子系统解耦（P4 + 方案 C，2026-08）

**所有子系统均为能力注入，没有任何子系统持有 Agent 引用**——host
Protocol 文件（agents/host.py）已随最后一个消费者（SessionScheduler）
改为能力注入而删除：

- **服务/数据直注**：不可变 `AgentIdentity`（id/name/address，`agents/trigger.py`）
  注入各引擎（认知规则分发走 `_on_event` 的 `:on`/`:when`，`TriggerEvaluator` 已随
  阶段 C 吸收）；`ReflectionEngine` 收 memory + llm_client + spec；
  `ContextBuilder` 收 config/team_members/mailbox/memory/spec/address，并自带
  系统提示缓存（`_system_prompt_cache`/`_mtime` 从 Agent 迁入）；
  `OnCompleteRunner` 收 identity/spec/logger/mail_sender（EffectExecutor）/
  registry；
- **能力注入（LoopDriver，方案 C 的 C2）**：stage 全部纯化为
  `Session × Services → StageOutcome`（见
  [../../design/dsl-agents.md](../../design/dsl-agents.md) 方案 C），LoopDriver
  签名为 `(identity, spec, services: StageServices, executor: EffectExecutor,
  tool_executor, per_tool_timeout, on_complete)`——查询/可观测性走
  `StageServices`（含 LLM 调用、context 首建、session span），stage 声明的外部
  效果（发信/写记忆/已读游标）由 `EffectExecutor` 执行。**session 字段写同样
  只声明不直写**（last LLM response / tool 结果日志 / 迭代与 ACTIVE 转换），
  收割（`collect_completed`）归引擎；纯度由
  `scripts/check_stage_purity.py` 机制强制（）；
- **SessionScheduler**：收 `(config, memory, session_span, mark_mail_read,
  check_reflection, loop_driver)`——六个委托分别来自 AgentConfig、
  MemoryManager、StageServices、EffectExecutor、ReflectionEngine 与直注的
  LoopDriver；邮件队列与 worker 生命周期由 scheduler 自持（R2）；
  **agent 状态读时派生**（`Agent.state` property 由 scheduler 存活会话集合
  算出 ACTIVE/THINKING），引擎不再回调 Agent 写状态。

Agent 的对象图由此彻底无环：无任何子系统/服务持有 Agent 引用。

---

## 动态 Registry（休眠/唤醒，阶段 6a + 阶段 D 机制化）

`AgentRegistry` 管理 activation（瞬态运行时），persona（`~/.tlon/{id}/`）由
AgentInfoProvider 提供全集，两者分离（见 [../../design/dsl-agents.md](../../design/dsl-agents.md)）。
阶段 D 起 registry 收缩为**纯机制**；阶段 E 起**生命周期从 spec 派生**——
启动集合、lazy 集合、wake 资格都来自成员 behavior.tlon 的 `:on` 规则
（spec 是唯一权威）。内置 Member **亚当**（`agents/adam.py`，`member_id="adam"`，
不来自用户 spec；设计定稿见 [../../design/members.md](../../design/members.md) 阶段 D）
只余 boot + drain 两条决策线：

- **启动集合（spec 派生）**：注册成员中 spec 含 `:on :boot` 规则且未运行者
  （`AgentRegistry.boot_candidates()`）。`AgentConfig.activation` 已废弃
  （读点全部移除，字段保留只为旧 agent.yaml 能加载）；
- **boot（决策在亚当）**：registry 的 `initialize()` 不再启动任何 agent（只订阅
  `AGENT_START_REQUEST` / `AGENT_STOP_REQUEST` / `MAIL_DELIVERED` + 启动 idle
  monitor）。亚当 initialize 后对 `boot_candidates()` 全体发
  `AGENT_START_REQUEST{reason="boot"}`，registry 幂等执行——boot 因此变异步，
  server 不再等 agent 全部起来；
- **lazy（spec 派生）**：`AgentRegistry.is_lazy()` = `role=="contractor"`（恒 lazy，
  可回收）或 spec 无 `:on :boot` 规则、或 spec 不可装载；
- **状态**：`get_agent_status` 对 lazy 未运行返回 `"sleeping"`，唤醒中 `"waking"`，
  非 lazy 未运行保持 `"unknown"`；
- **Wake-on-mail（registry 派生，阶段 E）**：注册表订阅 `MAIL_DELIVERED`，仅对
  "已注册 + 未运行/未唤醒 + spec 声明 `:on :mail-delivered`" 的收件成员发
  `AGENT_START_REQUEST{reason="wake"}`（mechanized policy，取代亚当 wake 规则）。
  去重：running/waking 跳过。邮件已在收件箱持久等待，
  **不再携带 trigger_event、不做重投递**——registry 执行
  （`_fulfill_start_request`）：per-agent `asyncio.Lock` 串行化（并发请求只装配一次）
  → 复用 `_start_agent` 装配，新 agent boot 时扫 `mailbox.unread()` 逐封入队
  （邮箱即队列，邮件模型见 [../../design/mail.md](../../design/mail.md)）。
  观测：`AGENT_WAKING` / `AGENT_WOKEN`（含 wake_duration）/
  `AGENT_WAKE_FAILED` 事件 + 同名 snake_case 日志，唤醒耗时是投递事件 trace 树下的
  `agent.wake` span（boot 路径不发这三个事件，与机制化前一致）；
- **空闲回收（事实/决策/执行三段）**：registry 的 30s monitor 检测"lazy member
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
  contractor 恒 lazy（`is_lazy()` 由 role 派生，阶段 E）+ `idle_timeout_seconds=300`
  （registry 构造参数 `contractor_idle_timeout_seconds`），完工后靠 6a 的空闲 drain
  自然回收；
- **spawn_contractor(prototype, name, spawner_id, session_id, params=None)**：
  预算检查 → 生成 id（`c-{prototype}-{8位uuid}`，prototype 片段过字符集清洗）→
  `instantiate()` 写盘 → 补 contractor 字段重存 agent.yaml →
  `AgentInfoProvider.reload_agent` 使配置对 router/status 可见 → `register` +
  `_start_agent` **立即启动** → 发 `AGENT_SPAWNED`（含 spawner/prototype/depth）；
- **stop_contractor(agent_id, stopped_by)**：仅 contractor 可停（resident 拒绝）；
  drain 语义与 6a 一致（有 ACTIVE/WAITING session 拒绝，per-agent 锁下 shutdown 前
  重查一次）→ shutdown → **回收容器**（`_docker_runtimes` 句柄表
  `remove(force=True)`，；runtime 缺失/失败只记日志）→ 注销 → **删除 home 目录**
  → 发 `AGENT_STOPPED`。
  空闲回收路径对 contractor 同样收容器删目录（`_drain_agent` 分支，发 `AGENT_STOPPED`
  而非 `AGENT_SLEEPING`）；resident 回收只停进程，**容器与目录都保留**（唤醒零成本、
  下次启动按名懒复用）。删除严格限定
  role==contractor，且 id 过字符集校验 + 解析路径必须在 tlon_home 直下（防路径注入）；
- **孤儿策略（reparent，不连坐）**：spawner 休眠/停止后其 contractor 继续独立运行，
  由各自 idle timeout 回收；`spawned_by` 是审计记录不是生命周期锁；
- **spawn 预算**（registry 构造参数，账本在内存、重启清零）：`max_activations=8`
  （运行中总数含 resident）、`max_spawn_depth=3`、`session_spawn_quota=2`（同一
  (spawner, session) 上限；session_id 由 `StageServices.session_span` 经 structlog
  contextvars 绑定，后台工具 task 继承 context 可读，读不到时同一 spawner 共用一桶）。
  拒绝抛 `SpawnRejectedError`（`.reason` = max_activations/max_spawn_depth/session_quota），
  记 `spawn_rejected` 日志；权限不可升格——只能从 prototype 库 spawn，spec 是 total 的
  无 overrides，无法授予新工具；
- **工具**（`tools/agents/`，注入 registry + spawner 自身 id）：
  `spawnAgent(prototype, name, params?)`（参数化原型模板值，如 relay 的 `:to`）、
  `stopAgent(agent_id)`、`listAgents()`（`get_agent_status` 扩展
  role/prototype/spawned_by/spawn_depth）。

---

## 行为规约（MemberSpec + behavior.tlon）

Agent 的行为是数据不是代码（见 [../../design/dsl-agents.md](../../design/dsl-agents.md)）。
成员构造时加载统一 `MemberSpec`（`agents/member_spec.py`，语义唯一权威），全成员
（居民 / 临时工 / 亚当）走同一份解释执行：

- 文件路径：`~/.tlon/{agent_id}/config/behavior.tlon`（EDN 字面量，解析器在
  `agents/edn.py`）；统一形态 `{:version 1 :rules [...]}`（阶段 C 移除旧双 spec：
  AgentSpec / ScriptSpec 及其 `script.tlon` 全部删除）；
- **规约是全部（total）**：文件即行为的全部，缺失 / 解析失败 / 校验不过 → 拒绝启动
  （registry 记 `agent_start_failed`），**没有 DefaultSpec 回落**——旧 `AgentSpec.load` /
  `AgentSpec.default` 的"缺省回退"语义已删除；
- 加载机制：`load_member_spec`（`agents/loader.py`，单一读取路径）——EDN 解析 +
  `MemberSpec.from_edn` + `validate_member_spec` 单校验闸（`:when` 纯谓词子集、动作
  白名单、`:loop` 认知 section/stage/until、fuel 硬顶、D6.7 至多一条认知规则）；
- 规则语义：每条规则 = `:on`（订阅事件）+ `:when`（命中后过滤）+ 可选 `:loop`
  （认知会话）+ `:actions`（动作）。无 `:loop` = 确定性规则，命中直接执行动作；
  同事件多条规则按声明序全部执行，deflection 用 `:when` 排除（v1 无短路/优先级）；
- 消费方：MemberRuntime 逐规则分发（`_on_event`，`agents/member_runtime.py`）；认知
  规则经 `_assemble_cognitive` 装配（LoopDriver / ContextBuilder / ReflectionEngine /
  SessionScheduler 注入）；确定性规则的动作（emit/forward/start/stop/spawn）由运行时
  直接执行；唤醒粒度 = 规则的 `:on` 声明（沉睡期间只能按事件类型唤醒）。

边界（R1b / 阶段 E）：`AgentConfig.proactive` 已删除（2026-08， 清理，
旧 agent.yaml 残留该键被静默忽略）；`activation` deprecated ignored
（字段保留仅为旧 agent.yaml 继续可加载）；启动集合与 lazy 集合由 spec 的 `:on :boot` 规则派生；
`session_timeout_seconds` / `message_queue_size` 是构造期 infra，留在 `AgentConfig`
不进 spec。

### 规则表达式（L1 受限求值器，`agents/eval.py`）

`:when` / 动作表达式是 s-expr（edn.py 解析为 `Form`，与 vector 的 list 类型区分）。
特殊形式 `if` / `when` / `let` / `and` / `or` + 阶段 D 的 `while` / `recur` /
`map` / `filter` / `reduce`（图灵完备，fuel 有界）；其余形式头必须命中 builtin
注册表——邮件谓词（`addressed-to-me?` / `cc-ed?` / `content-matches?` / `contains?`）、
宿主谓词（闭包注入，查 registry 只读接口）、stage 条件。求值每步耗 fuel（耗尽
`FuelExhaustedError`）、深度受限。load 时 `validate_member_spec` 静态校验（形式头
白名单 + Symbol 作用域 + 纯度不变式），`writePrototype` 的 schema 校验因此自动覆盖
表达式。运行时 MemberRuntime 以 env=`{msg, event, self}` 求值。

### 动作表达式（统一动作词汇，阶段 C/D）

`:actions`（vector of Form）每个 Form 是一个动作：`spawn` / `emit` / `start` / `stop` /
`start-all` / `forward`，可被 `when` / `while` / `map` 包裹（条件/测试/绑定初始化纯、
动作承载位为动作）。延续"DSL 只描述，引擎执行"：

- **求值保持纯**：动作注册为 `Builtin(pure=False)`，但求值时不做任何副作用——校验
  参数后把 `ActionRequest` 数据收集进 collector，求值完成后由引擎异步逐条执行；
- **两位置纯度校验**：load 时校验 `when` 条件位只许纯谓词，动作位必须是动作 Form；
- **执行点**：确定性规则命中即执行；认知规则在 session 完成时由 OnCompleteRunner
  执行（`agents/on_complete.py`）；
- **动作语义**：`spawn` 调 `registry.spawn_contractor`（spawner_id=当前 agent）——
  **信任闸与 spawn 预算全部生效**（untrusted / 超预算 → 拒绝记日志，不影响 session）；
  链式 spawn 由 `max_spawn_depth=3` 硬顶；`emit` 空收件人回复触发邮件发件人（确定性
  路径与认知完成路径一致，阶段 F）；`forward` 原样转发触发邮件；
- **异常隔离**：单条规则失败（求值异常 / 动作失败）只记日志，不影响其余规则与成员。

---

## Behavior Prototype 库

预制行为原型 = 完整 MemberSpec + 人格文件，实例化是**纯拷贝**（total：prototype 不再
充当 merge base），让人类和 agent 以"选型 + 实例化"方式创建新 agent（设计见
[../../design/dsl-agents.md](../../design/dsl-agents.md)）。

- **目录形式**：`tlon/prototypes/{name}/`（包内数据，随 wheel 分发），每个含
  `behavior.tlon`（统一 rules 形态，`{:version 1 :rules [...]}`，即完整的 total spec）、
  `IDENTITY.md` / `SOUL.md` / `RULES.md` 人格模板（可选）、`prototype.edn` 元数据
  （`{:name :display-name :description :trusted :author}`，edn.py 解析；信任语义见下文
  "信任闸"）。runtime 从 spec **派生**（有 `:loop` 规则 → "react"，否则 "script"），
  不是元数据关键字。
  内置 4 个：`deep-thinker`（多轮评审）/ `fast-responder`（低迭代）/ `archivist`
  （高频反思）——认知 react；`relay`（纯转发，确定性 script，可参数化）；
- **种子**：`seed_prototypes()` 在 ServerApp 初始化早段把包内 prototypes 经
  `importlib.resources` 拷贝到 `~/.tlon/prototypes/`（幂等，同名文件跳过——用户可改可加，
  不覆盖）；wizard 创建入口也会先种子（mirror 是独立进程）；
- **发现**：`list_prototypes()` 读 `~/.tlon/prototypes/`（非包内——用户修改可见），
  暴露给 agent 的是 `listPrototypes` 工具（`tools/prototypes/list_prototypes.py`，
  随每个 agent 注册）；暴露给人类的是 wizard 人员管理菜单"从 Prototype 创建"；
- **实例化**：`instantiate(prototype_name, agent_id, agent_name, params)` ——校验
  prototype 存在与 agent_id 不冲突；behavior.tlon 原样**拷贝**（不合并）到
  `~/.tlon/{agent_id}/config/`；若原型声明 `:parameters`（模板变量 `${var}`），
  `params` 必须先提供，渲染后写盘（wizard 从 mirror 菜单按参数类型逐项提示，阶段 F）；
  人格文件拷贝；agent.yaml 走与 wizard 相同的 `yaml_save` 路径，`AgentConfig.prototype`
  记录来源 prototype；
- **生效时机**：热生效——wizard/mirror 创建后 best-effort 通知运行中的服务端
  （`POST /admin/config/agents/{id}/reload` → `AgentRegistry.adopt_agent`），
  新 agent 无需重启即可用；服务端未运行时静默跳过，重启后生效作为兜底。

---

## 信任闸（staging + 审批，阶段 4a）

信任分级落地"代码签名"模型（设计见 [../../design/dsl-agents.md](../../design/dsl-agents.md)
阶段 4a 与操作系统类比附录）：包内/用户 prototype = 签名二进制（可信，可直接 spawn）；
LLM 现写规约 = 未签名二进制（staging + 人类显式批准的 Gatekeeper 模型）。

- **信任标记**：`prototype.edn` 增加 `:trusted true|false`（包内 4 个 builtin 均为 true）。
  `PrototypeInfo` 增加 `trusted` / `author` / `parameters` 字段；缺 `:trusted` 的用户手写
  prototype 默认可信（手写即信任），只有 staging 区写出 `false`；
- **staging 区**：`~/.tlon/prototypes-staging/`（`TlonPaths.prototypes_staging_dir()`，
  无副作用解析）。`write_staging_prototype(name, behavior_edn, persona, display_name,
  description, author_id, spec_type)` **先校验后落盘**——behavior 须过 EDN 解析 +
  `MemberSpec.from_edn` + `validate_member_spec` schema 校验（`:version` 必填、字段类型、
  `:loop` 认知 section/stage/until、fuel 硬顶），失败抛 `PrototypeError` 且一个字节都
  不写盘；name 字符集校验（alnum + `-_`，防路径注入）、与可信库和 staging 双向查重；
  prototype.edn 写入 `:trusted false` + `:author agent_id`；
- **审批**：`approve_prototype(name)` 把目录移到 `~/.tlon/prototypes/` 并把 metadata
  改写为 `:trusted true`（`:author` 保留）；`reject_prototype(name)` 删除 staging 目录
  （防护同 contractor home 删除：字符集 + resolve 必须在 staging 根直下）。
  人类入口是 wizard 人员管理菜单的"规约审批"（列表含作者与描述 → 预览 behavior.tlon
  与人格文件 → 批准/拒绝）；
- **spawn 闸**：`spawn_contractor` 在任何预算检查之前先查信任——prototype 不可信即抛
  `SpawnRejectedError(reason="untrusted_prototype")`；`spawnAgent` 工具把该拒绝翻译为
  可读错误（提示需人类在 mirror 批准）。`listPrototypes` 输出带 `trusted` 字段；
- **`writePrototype` 工具**（`tools/agents/write_prototype.py`）：参数 `name` /
  `behavior`（EDN 字符串，必填）+ `spec_type`（"react" 默认 / "script"，只选模板——
  react 连带写人格文件，script 无 persona；输出均为统一 rules 形态）+ `display_name` /
  `description` / `identity` / `soul` / `rules`；成功返回 `status: pending_approval`，
  校验失败返回具体错误 + schema 提示（LLM 可修正重试）；`author_id` 绑定装配它的 agent；
- **规约版本哈希**：`MemberSpec.source_hash`——`load_member_spec` 对所读 behavior.tlon
  内容计算 sha256 短哈希（前 8 位）。该哈希进入 `agent.session` span attributes
  （`spec_hash`）、`agent_started` 日志与 `AGENT_STARTED` 事件 payload——任何一条
  trace 都能查到当时生效的规约版本。

---

## Agent 核心流程

```
MAIL_DELIVERED（收件人=我）→ MemberRuntime._on_event 规则分发（同 :on 规则按声明序：
确定性规则 :when 命中直接执行动作；认知规则 :when 命中 → 入队）
→ SessionScheduler 主循环：每封邮件一个 session → LoopDriver 步进（Think → 并发派发工具 → 收割）
→ sendEmail 回复（in_reply_to/thread_id 继承触发邮件）→ session 完成 → OnCompleteRunner 执行 :actions
→ mailbox.mark_read（认知排队则推迟到 session 完成，D6.1）→ 保存记忆 → ReflectionEngine 定期反思
```

沉睡期间到的邮件留在收件箱；唤醒时 `_scan_unread_mail` 逐封重放（确定性规则与认知
入队都执行——阶段 F 修复混排成员确定性规则被吞的缺口）。

**Tool 执行**: Agent 直接调用 `tool_executor.execute()`（模块内聚合），非 EventBus RPC。

**韧性配置**（`AgentConfig`）：`max_iterations`（ReAct 轮数上限，默认 10）、
`max_consecutive_errors`（连续 LLM 错误上限，默认 3）、`session_timeout_seconds`
（卡死会话清理，默认 1800）、`message_queue_size`（入队背压，默认 100）。

---

## 响应判断逻辑

1. 自己的邮件永不响应（防自循环）
2. 每条规则独立的 `:when` 过滤（纯谓词；命中则执行，未命中跳过）
3. 同事件多条规则按声明序全部执行；deflection 用 `:when` 排除便宜路径已处理的
   （如退订 deflection：确定性 auto-optout 先回，认知 respond 的 `:when` 排除退订）

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
