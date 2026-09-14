# 成员运行时设计

> 统一成员模型（ADR 0006，2026-08 实施）：成员只有一种；排空一律是睡眠、档案归档。模型总览见 [docs/design/unified-member-model.md](./unified-member-model.md)。

本文解释 `tlon.agents` 里成员运行时为什么是现在这个样子：统一解释器、认知循环管线、effects-as-data、唤醒补偿、注册表的机制面。系统骨架与装配顺序见 [docs/architecture.md](../architecture.md)，DSL 本身的取舍见 [docs/design/dsl.md](./dsl.md)，邮件层见 [docs/design/mail.md](./mail.md)，方向锚点是 [docs/vision.md](../vision.md)。

两份相关 ADR：[docs/decisions/0003-mechanism-vs-policy.md](../decisions/0003-mechanism-vs-policy.md)（注册表/Adam 的机制策略分工）、[docs/decisions/0004-stage-purity.md](../decisions/0004-stage-purity.md)（stage 纯度）。本文侧重整体结构与动机，决策的备选方案与后果论证以 ADR 为准。

## 设计原点：行为即数据

愿景要求 agent 社会自进化，前提是行为可被系统自己读写——行为必须是**数据**而不是代码。这条原则落到运行时上，推出两个结构性决定：

- **spec 是唯一权威**。每个成员的全部行为由一份 `MemberSpec`（[agents/member_spec.py](../../src/tlon/agents/member_spec.py)）描述，落盘为每成员的 `config/behavior.tlon`：规则 = `:on` + `:when` + 可选 `:loop` + `:actions`。运行时不持有任何 spec 之外的行为知识；boot 集合、lazy 判定、唤醒资格全部从 spec 派生。
- **引擎与策略分离**。求值（DSL 表达式）与执行（状态变更、外部效果）是两层：DSL 只"描述"，引擎才"执行"。这条线贯穿整个运行时——规则动作求值为 `ActionRequest` 数据再由统一执行器落地；在 stage 层则表现为 effects-as-data（见下文）。

后文的每一个组件划分，本质上都是这两条决定的展开。

## 求值的停机与失败语义

运行时要执行的数据未来可能出自 LLM 之手，因此 DSL 求值层（[agents/eval.py](../../src/tlon/agents/eval.py)）的两条性质是运行时设计的前提，而不是附加功能：

- **可停机由 fuel 保证**。每个求值都有步数预算：规则的 `:fuel` 声明时是门控与动作共享的总预算，未声明时 `:when` 门控与各动作分别使用引擎默认值（`DEFAULT_WHEN_FUEL` / `DEFAULT_FUEL`）。跑飞的表达式耗尽预算即抛 `FuelExhaustedError`，与普通的 `EvalError` 区分记日志——前者是程序失控，后者是程序写错，运维含义不同。
- **求值永不产生副作用**。所有动作 builtin（`spawn` / `emit` / `start` / `stop` / `start-all` / `forward`）只做参数校验并产出 `ActionRequest` 数据；请求到达引擎的路径按规则形态区分——无 `:loop` 的规则收集动作表达式的返回值，on-complete 场景追加进收集器（`make_member_builtins` 的 `collector`）。「DSL 描述、引擎执行」在求值层就已经成立，不是执行层才开始的约定。
- **失败的安全默认一律是"不动"**。门控求值失败 = 不响应；规则求值失败 = 跳过该规则；stage 条件失败 = 跳过该 stage；until 失败 = 回落引擎默认终止检查。写错的程序最坏是沉默，而不是失控——这是把语言交给不可信作者的前提。

## 统一解释器

[agents/member_runtime.py](../../src/tlon/agents/member_runtime.py) 的 `MemberRuntime` 是 spec 驱动的统一解释器：带不带 `:loop` 是同一份 spec 的两种规则形态，不是两种实现——任一规则带 `:loop` 的成员挂认知引擎，否则只跑 `:actions`。共享逻辑（订阅、门控、已读语义、动作执行）只有一份。

### 派生而非存储

生命周期归属是从 spec **派生**的值，从不存储。存一份冗余的归属字段只会制造与 spec 不一致的可能；派生值则永远与权威一致。同一原则贯穿整个运行时：boot 集合看 `:on :boot` 规则是否存在，lazy 判定看 spec 是否声明了 boot 规则，唤醒资格看是否订阅 `:mail-delivered`。

### 统一的具体内容

- **订阅**：`MemberRuntime.initialize` 按各规则的 `:on` 去重订阅事件类型；boot 规则（`on` 为 `None`）在启动时执行。
- **命中即执行**：规则命中时，有 loop 先跑认知循环再跑 `:actions`；无 loop 只跑 `:actions`。
- **统一环境**：求值环境为 `{msg, event, self}`（`MemberRuntime._env`），会话完成时追加 `result`。环境取两个旧运行时词表的并集：非邮件事件让 `msg` 为 None，谓词安全地返回 False——同一套谓词词汇表对邮件与非邮件事件都成立，spec 作者不用区分自己在哪种运行时语境里写规则。
- **统一动作执行器**：`spawn` / `emit` / `start` / `stop` / `forward` 五种动作全部经 `MemberRuntime._execute_action` 一个出口。其中 `start` / `stop` 只是向总线发布生命周期**请求**——成员只表达决策，执行是注册表的事（见 ADR 0003）。
- **同构的生命周期**：所有成员在注册表眼里完全同构：同一张运行表、同一套启动/排空路径、同一套状态读法——`get_state` / `active_session_count` / `idle_seconds` 对带 `:loop` 的成员委派给调度器（并叠入 session 之外的真实后台活动：进行中的反思 LLM 调用算作活跃，常驻但大部分时间在 sleep 的重扫任务不算），对无 `:loop` 的成员退化为简单的活跃记账。装配同样统一：每个成员都拿到完整能力集（LLM、记忆、工具），无 `:loop` 的成员只是从不启动认知引擎——对象便宜，调用才贵。

### 无工作者模型

`MemberRuntime` 的规则派发全部发生在 EventBus 的派发上下文里（总线已经隔离了处理器异常），认知工作只是入队，由调度器的主循环 task 消费。这带来两个性质：一是成员不在派发上下文里跑长任务，派发慢的代价由有界队列的背压吸收而不是拖垮总线；二是关闭语义收敛——`shutdown` 依次停掉成员的后台 task 与认知循环（重扫、调度器主循环、进行中的反思，全部 cancel + await），再退订、关工具，不留游离协程。运行时里长住的 task 只有少数几类显式声明的后台工作：每成员的调度器主循环、邮件重扫（issue ）、反思。

### 运行时不做什么

统一解释器的边界同样重要——以下职责被刻意排除在外：

- **不决定自己或他人的生死**。成员的启动、停止、唤醒全部以请求事件表达，执行在注册表；运行时甚至不知道自己是不是 lazy。
- ~~**不持久化会话**。`AgentSession` 是易失的，进程重启即蒸发；持久性完全由邮箱（未读集合）与记忆（交互/摘要/事实）承担。会话只是"正在处理"的投影，不是需要抢救的状态。~~ 已被 [ADR 0007](../decisions/0007-session-event-sourcing.md) 取代：session 状态的唯一真相是 append-only 事件日志（见下文「Session 事件溯源」），内存中的 `AgentSession` 仍是易失投影，但可重放恢复。
- **不做投递与寻址**。发出的邮件只到 `MAIL_NEW` 事件为止；地址解析、落盘、退信是邮件层的职责（见 [docs/design/mail.md](./mail.md)）。
- **不保管第二份行为事实**。除 spec 的派生缓存外，运行时不维护任何需要与 spec 对账的状态。

## 认知循环管线

认知成员的处理链路是五个组件的接力，每个组件只做一件事、各有一个独立的变更理由：

```
MAIL_DELIVERED → MemberRuntime（:when 门控、入队）
  → SessionScheduler（每成员主循环、有界队列、会话回收）
    → AgentSession（一邮件一会话）
      → LoopDriver（逐步推进；_apply_outcome 是唯一状态变更点）
        → stages（think/plan/critique/revise/act，纯函数）
        → EffectExecutor（执行 StageOutcome 声明的外部效果）
```

### SessionScheduler：每成员一个主循环

为什么把循环拆成调度器、驱动器、stage、执行器四层，而不是一个大循环函数：每一层各有一个独立的变更理由。调度器的变更理由是"会话生命周期策略"（超时、回收、背压）；驱动器的是"循环语义"（stage 顺序、终止判定）；stage 的是"单步认知内容"；执行器的是"效果怎么落地"。旧 `Agent` god class 的教训是这四类变化搅在一起，任何一处改动都要通读全部。拆开之后，每层对上只暴露声明（Outcome）或委托（闭包），对下只认能力——这也使 ADR 0004 的纯度检查有一个清晰的适用边界（`stages/` 目录）。

[agents/scheduler.py](../../src/tlon/agents/scheduler.py) 的 `SessionScheduler` 拥有认知成员的连续主循环 task，做四件事：

1. **有界收件队列**。入队走 `enqueue` 的 `put_nowait`，队列满（`config.message_queue_size`）返回 False——宁可丢新邮件也不阻塞 EventBus 的派发上下文。丢的是"新事件的拷贝"：邮件在磁盘收件箱里仍保持未读，下次启动扫描会补回来（见「唤醒与补偿」）。
2. **一邮件一会话**。每封邮件排成一个 `AgentSession`（[agents/session.py](../../src/tlon/agents/session.py)）；一个成员可以同时有多个进行中的会话。会话有 ACTIVE / WAITING / COMPLETED 三态，超时（`session_timeout_seconds`）的会话被判卡死并强制完成。
3. **逐轮推进**。ACTIVE 会话调 `LoopDriver.step_session`，WAITING 会话调 `collect_session_results` 收割后台工具结果；完成的会话取消残留后台任务、推进收件箱 cursor、回收。
4. **状态派生而非回调**。成员的整体状态（THINKING/ACTIVE、空闲秒数）在读取时从会话集合派生，调度器从不回调宿主。这消灭了 MemberRuntime 与调度器之间的双向引用，也让"状态"永远与会话事实一致，不存在需要补偿的缓存。

调度器自身跑在注入的能力之上（config、memory、trace/mail/reflection 委托、LoopDriver），不持有对 MemberRuntime 的反向引用——这是从旧 `Agent` god class 拆解时定下的规矩：组件只认能力，不认宿主。

### AgentSession：会话即状态容器

`AgentSession` 是一封邮件触发的完整工作上下文：对话 context、迭代与错误计数、响应链、工具结果、后台任务句柄，以及 `loop_scratch`——stage 间共享的会话内草稿板（plan 文本、critique 结论等）。草稿板由引擎持有，DSL 只能经 stage 条件谓词读它：它是引擎内部状态，不是程序可写的内容。

会话的粒度是**一封邮件**而不是一个线程：会话 id 由邮件 id 派生（`{agent_id}-{mail.id}`）。邮件是系统里天然的工作单元——持久、有唯一 id、有明确的处理完成时点（标读）；线程归由由邮件头（`in_reply_to` / `thread_id`）在内容层表达，会话结构不必承担它。这让多封邮件天然并行成多个会话，而"同一话题的多轮往来"由 context builder 读邮箱历史还原，不需要长生命周期会话。

### Session 事件溯源

会话状态的**唯一真相**是 append-only 事件日志（ADR 0007，[agents/session_journal.py](../../src/tlon/agents/session_journal.py) 的 `SessionJournal`）：每个 session 一个 JSONL 文件（`~/.tlon/{agent_id}/sessions/{session_id}.jsonl`），每行一个 `{seq, ts, type, ...payload}` 事件，覆盖 session 从生到死的全部状态变更——初始 context、每次 context 追加、scratch 写入、迭代/错误计数、LLM 响应、工具 dispatch/result/collect、状态迁移、终止原因。

引擎遵守一条不变式：**模型可见 ⇒ 已入日志**。context 只有两个写入点（首次构建、`_apply_outcome`），都先写日志成功再改内存；写失败抛异常、不动内存。因此进程重启后调度器可以纯重放恢复会话：邮件再次进入调度时先查日志——含 `session/end` 的只推进收件箱 cursor 不重跑；有孤儿工具 dispatch 的（已落盘的结果保留，没结果的补一条合成 interrupted 结果并自愈进日志）经 `resume_collect` 续跑；无日志才新建。副作用（发信、记忆保存、on-complete 动作）绝不重演。journal 只在引擎层（LoopDriver / SessionScheduler / 装配点）出现，stage 纯度边界不变。

### 上下文压力管理

context 是模型可见的视图，可以在不动日志真相的前提下被压缩（ADR 0008，[agents/context_pressure.py](../../src/tlon/agents/context_pressure.py) 的 `ContextPressure`）。`LoopDriver.step_session` 在每个 step 的 stage 向量运行前做 token 估算（CJK 1 token/字、其余 4 字符/token 的字符启发式），超过阈值（`max(context_window - 40_000, 0.8w)`，`LLMConfig.context_window` 可配）时按梯度减压：先把锚点（初始构建块，`session.anchor_len`）之后、保留窗口（最近 20 条，不拆 tool_call/tool 配对）之外的 tool 结果原位换成占位符（prune，零 LLM 成本）；仍超才把中段交给 LLM 摘要成一条 `[前文摘要]` 消息（running summary 滚动折叠，摘要失败回退占位）。每次压缩先落 `context/compact` 事件（携带压缩后全量）再改内存，重放时整体替换、与运行态逐字节一致。紧急路径：`llm_client` 把 context-length 4xx 识别为 `ContextWindowExceededError` 不重试直接抛，driver 捕获后强制完整梯度压缩（窗口收紧到 10 条）并重试该 stage 一次。

### LoopDriver：循环的所有权在引擎

[agents/loop_driver.py](../../src/tlon/agents/loop_driver.py) 的 `LoopDriver` 把 loop 规则（`MemberLoop`）解释为逐步执行。关键边界：

- **`max_iterations` 是引擎的硬上限**，任何 `:until` 表达式都无法越过。终止表达式求值失败时回落到引擎默认检查（迭代数/错误数），保证循环必然停机。这是"DSL 必须可停机"这条愿景承诺在循环层的工程兜底：程序可以写错，引擎不能不停。
- **stage 向量是白名单组合**。跑哪些 stage、什么顺序，由 spec 的 `LoopSpec.stages` 决定；每个 stage 可选带 `:if` 条件（纯谓词，求值失败一律跳过——安全默认是"不跑"）。DSL 只能选择与排序既有积木，不能发明新 stage。
- **响应链由引擎串接**。think 产出本轮 LLM 响应，act 消费它，辅助 stage（plan / critique / revise）原样透传。
- **终止判定只在轮首发生**。`:until` 在每轮 think 之前求值一次，不在 stage 向量中途检查——循环要么完整地跑完一轮声明的 stage，要么整轮不开始，不存在"跑到一半的轮"这种需要定义语义的中间态。think 失败也只记错误并提前结束本轮，留给下一轮的 until 走统一终止路径收口。
- **`_apply_outcome` 是唯一的状态变更点**。stage 声明的一切变更在这里按固定顺序落地：context 追加 → scratch 写入 → 计数器/状态 → 工具结果 → 响应链 → 工具分发 → 邮件 → on-complete 动作 → 完成标记。顺序集中在一处，与历史实现的等价性可审计。
- **没有 act 的程序也必然停机**。不含 act 的 stage 向量自己不会推进迭代计数，引擎代为推进——硬上限对任何合法程序都成立，停机性不依赖程序写对了什么。
- **并发是引擎的**。act 只声明工具分发列表；每个工具调用一个后台 task、每个工具一个超时（`per_tool_timeout`），由引擎创建与收割。stage 不知道 asyncio 的存在。

### 五个 stage：各是一个语义单元

[agents/stages/](../../src/tlon/agents/stages) 下的五个 stage 覆盖一个 ReAct 轮的完整语义：

- `think`：一次 LLM 调用，产出本轮响应；
- `plan`：轮前规划，结论写草稿板；
- `critique`：评审本轮最新产出（含响应链上待派发、尚未入 context 的草稿）；
- `revise`：把评审反馈写回 context，并清除已消费的 verdict；
- `act`：工具执行。它按 asyncio 的"分发后收割"模型拆成两半——`act_stage_step` 声明分发列表，`act_stage_collect` 声明收割结果的含义；两半都是纯变换，并发与收割本身是引擎的。

默认循环是 think + act，外加一道**交卷前自查**（issue ）：`(critique {:if (completing?)})` 只在本轮响应会让会话完成（单独的 `sendEmail is_complete=true` 或 `silence`，判定与 act 的完成/冲突规则一致）时评审待交草稿；FAIL 则 `(act {:if (or (not (completing?)) (not (critique-failed?)))})` 拦截本轮派发、`(revise {:if (critique-failed?)})` 把反馈喂回上下文让下一轮 think 修正后重审。评审成本与轮数无关（每次交卷尝试一次全量评审），不随长 session 平方放大；plan 与更密的 critique 排布仍是 spec 可选插入的自省积木。把自省拆成独立 stage 而不是做进 think 的理由：自省是**程序级的选择**——一个成员要不要规划、要不要自我评审、评审几次，应该写在 spec 里可审计、可改写，而不是埋在 prompt 工程里。拆成积木后，"带自我评审的 ReAct"与"朴素 ReAct"是同一个引擎跑两份不同的 stage 向量，引擎零改动。

### 认知规则的唯一性

一个成员至多一条认知规则（D6.7）。这不是表达力的损失而是刻意的收窄：认知循环需要唯一一份 loop 配置（stage 向量、until、max_iterations、reflect），多条认知规则意味着多套循环配置竞争同一套会话与记忆基础设施，语义上说不清谁拥有循环。需要"对不同邮件做不同认知处理"时，答案是 spec 内的 `:when` 门控与无 `:loop` 规则的廉价前置路径（如自动婉拒），而不是第二条认知规则。带 `:loop` 与不带 `:loop` 的规则可以在同一成员内混编：邮件命中时无 `:loop` 的规则内联执行，带 `:loop` 的规则走会话——贵的能力只花在门控放行的邮件上。

### EffectExecutor：效果的唯一执行口

[agents/executor.py](../../src/tlon/agents/executor.py) 的 `EffectExecutor` 执行 stage 声明的外部效果：

- **发邮件**：`MailEffect` → 构造 `Mail`、注入当前 trace、发布 `MAIL_NEW`——与人类发信汇入同一条投递管道；
- **推进收件箱 cursor**：会话完成（成功、出错、超时不限）即标读——邮件已被处理，与处理结果无关。

所有效果执行**永不抛异常**：失败只记日志。一个失败的效果不能打断循环、不能破坏会话生命周期——循环的停机保证不被外部系统的故障绑架。

## effects-as-data

认知循环的每个 stage 是纯变换 `Session × StageServices → StageOutcome`。这条切分把 stage 对外的接触分成三类，各有唯一通道：

- **只读的外部查询**（LLM 调用）与**可观测性**（日志、事件、trace span）：走注入的 [agents/stages/services.py](../../src/tlon/agents/stages/services.py) 的 `StageServices`。它们不改变系统状态，因此不出现在 Outcome 里。
- **状态变更**：stage 永不执行，只声明。全部意图返回在 [agents/stages/outcome.py](../../src/tlon/agents/stages/outcome.py) 的 `StageOutcome` 里——context 追加、scratch 写入、迭代/错误计数、工具结果与分发、响应、邮件、最终回复（on-complete 的 `result` 输入）、on-complete、完成信号。默认全空的 Outcome 应用后是 no-op：stage 只声明它真正要改的部分。
- **应用**：`LoopDriver._apply_outcome` 落地会话本地字段，`EffectExecutor` 执行外部效果。stage 与副作用之间隔着一层可枚举、可断言的数据。

这条纯度不是约定而是强制：[scripts/check_stage_purity.py](../../scripts/check_stage_purity.py) 用 AST 静态禁止 stage 模块调用 session 的任何 mutator 方法、禁止对 session 属性赋值，纳入验证六件套，违规即构建失败。引擎与策略的边界因此是**机器可检查**的——这正是 DSL 白名单组合 stage 的安全地基，完整论证见 [docs/decisions/0004-stage-purity.md](../decisions/0004-stage-purity.md)。

为什么值得付出这层间接：组合安全的前提是积木不逾矩。stage 在类型层面拿不到可变的 session 操作面，在检查层面被禁掉直写，于是 spec 作者（包括未来的 LLM 作者）写出的任何 stage 向量，最坏结果是跑出一个 no-op 或被 `max_iterations` 截停，不可能破坏引擎不变量。测试也随之简化：验证一个 stage 只需构造 session 与 fake services、断言返回的 Outcome，不需要 mock 总线或邮箱。

## 无反向引用：能力装配原则

管线上的每个组件都由**能力**装配，而非由宿主对象装配：

- `SessionScheduler` 收到的是 config、memory、五个委托闭包与 LoopDriver；
- `LoopDriver` 收到的是 identity、`MemberRule`、`StageServices`、`EffectExecutor`、ToolExecutor 与 OnCompleteRunner；
- `EffectExecutor`、`OnCompleteRunner`、`ReflectionEngine` 收到的都是 identity / rule / memory / 闭包这类纯数据与函数；
- 事件发布走 `make_event_publisher`（[agents/stages/services.py](../../src/tlon/agents/stages/services.py)）闭包——闭包只封住 EventBus 与默认 source，不封住任何 Member。

这条规矩（拆解旧 `Agent` god class 时确立）有两个收益：一是组件间依赖图是树而非网，每个组件可独立实例化进测试；二是认知引擎不依赖 MemberRuntime 的任何实现细节，`MemberRuntime._assemble_cognitive` 是唯一知道两边如何拼合的地方——替换或演进认知引擎内部时，爆炸半径被限制在这一处装配代码里。

## 背压与可观测性

- **背压策略全链路一致**。EventBus 的 per-subscriber 队列、调度器的邮件队列都是有界的，溢出丢新保旧、绝不阻塞总线派发；邮件侧丢弃的代价由「邮箱即队列」兜底（未读事实在磁盘上）。
- **trace 贯穿唤醒与循环**。唤醒请求续接投递事件的 trace 树（`AgentRegistry._fulfill_wake`）；每个会话携带路由事件的 carrier，调度器每轮经 `StageServices.session_span` 重新挂上 OTel context 并开启会话 span，span 属性带 spec 的 `source_hash`——观测数据总能回答"跑的是哪一版行为"。同一 span 内还绑定 session_id 到 contextvars，后台工具任务（如 spawnAgent 的会话配额归属）据此归账。
- **决策即事件**。生命周期请求、邮件发送、工具分发起点（`TOOL_EXECUTE_START`）都是总线上的显式事件，运行时没有"隐式发生"的状态跃迁；这是机制可审计（ADR 0003）在运行时的投影。

## 唤醒与补偿

lazy 成员可以睡眠（scale-to-zero），但邮件是持久的，所以唤醒不需要搬运任何事件载荷：

- **唤醒资格从 spec 派生**。`AgentRegistry._on_mail_delivered` 看到 `MAIL_DELIVERED` 时，若收件人已注册、未运行、未在唤醒中，且 spec 声明了 `:on :mail-delivered`，就发布 `AGENT_START_REQUEST{reason="wake"}`，并续接投递事件的 trace 树。这是机制而非策略——唤醒资格本质是 spec 声明的订阅事实（见 [docs/decisions/0003-mechanism-vs-policy.md](../decisions/0003-mechanism-vs-policy.md)）。
- **唤醒不带载荷**。请求里只有 agent_id 与 reason；邮件早已持久化在收件箱，被唤醒的成员启动时由 `MemberRuntime._scan_unread_mail` 重放全部未读邮件。邮箱即队列，不存在"丢给睡眠者的消息"。
- **已读语义（D6.1）**：被 `:when` 门控拒绝的邮件立即标读（杜绝"永不回复 → 永远未读"的卡死）；排入认知会话的邮件延迟到会话完成才标读；被满队列丢弃的邮件保持未读，等下次启动扫描补处理。自己发给自己的邮件（`all@` 自拷贝）直接标读、永不处理。

整套设计把"恰好一次"降格为"至少一次 + 幂等处理"：未读集合是磁盘上的事实，运行时队列只是它的易失投影。唤醒、队列溢出、进程重启走的是同一条补偿路径——启动扫描。补偿路径唯一是有意的：每一种"邮件可能被遗漏"的场景如果各有专属的重放逻辑，重复处理的语义就要逐路径论证；收敛到一条扫描路径后，需要论证的只有"扫描是幂等的"这一件事。

## 反思与完成

会话的收尾有两台机器，都在循环之外、由引擎在固定时点触发：

- **反思引擎**（[agents/reflection.py](../../src/tlon/agents/reflection.py) 的 `ReflectionEngine`）：按**间隔 + 最少完成会话数双门控**触发（`reflect.interval_minutes` 与 `reflection_min_interactions`，两者都满足才跑），由调度器在主循环间隙经注入的 `check_reflection` 委托检查，实际反思跑在独立后台 task 里，不阻塞会话推进——task 由引擎持有：进行中计入成员活跃（`idle_seconds` 不判空闲，drain 不会打断审计），成员 shutdown 时被取消并 await，不留游离协程。双门控的理由：纯时间触发会在沉默期空转 LLM 调用，纯计数触发会在高频期反思过密；取两者的"与"，反思频率才与"确有新经验"挂钩。反思对记忆做体检：LLM 审视记忆摘要，产出 `record_fact` / `delete_fact` / `noop` 操作并应用。会话计数由调度器在 session 回收点经 `note_interaction` 喂入——只有真正完成的会话才算数（ADR 0009：摘要触发也挂在这个回收点上，pending 达 `summary_window` 即从 journal 批量压缩进 episodic，无需反思催办）。
- **完成动作**（[agents/on_complete.py](../../src/tlon/agents/on_complete.py) 的 `OnCompleteRunner`）：认知会话产出最终回复后，把认知规则的 `:actions` 求值一次——环境在统一环境上追加 `result`（最终回复文本，由 act stage 经 `StageOutcome.final_reply` 声明），收集到的动作请求走 `MemberRuntime._execute_action` 统一执行，五种动作全部可用。调用时点在会话标记完成之前，仍在会话 span 内，因此 spawn 配额的会话归属（contextvars 携带 session_id）不受影响。任何求值或执行失败只记日志，不打断剩余动作与会话生命周期。

两者的共同点是"挂在引擎的固定时点上，而非 stage 的程序内容"：反思与收尾是运行时服务质量的一部分，DSL 不能关掉它们，也不用知道它们存在。

## 注册表的机制面

[agents/agent_registry.py](../../src/tlon/agents/agent_registry.py) 的 `AgentRegistry` 是纯机制组件（策略在 Adam，论证见 ADR 0003）。它持有的机制：

- **运行表与装配**。`_start_agent` 是成员的唯一装配点：加载并校验 spec、建邮箱；统一装配（ADR 0006）——每个成员都经 `_assemble_cognitive` 得到全套能力对象（LLM 客户端、双层记忆、任务管理器、Docker 运行时、全套工具），是否装配认知引擎（调度器/循环）由 spec 是否有 `:loop` 规则派生，加载按需（懒装配：对象便宜，调用才贵）。装配期还派生一次信任等级（，信任阶梯，`agents/trust.py`，fail-open 按正式放行）：见习成员（spawn 出生且未获 endorse）不注册四个生殖/演化工具（`spawnAgent` / `stopAgent` / `writePrototype` / `proposeBehavior`），且其撰写发出的邮件抄送 mentor（= spawner；转发不抄送）——升级在下次唤醒重新装配时生效。Docker 容器不在装配时启动，首次使用时懒起：为纯对话唤醒一个 agent 不花容器成本。
- **幂等的生命周期执行**。注册表订阅 `AGENT_START_REQUEST` / `AGENT_STOP_REQUEST` 并幂等执行：`_waking` 集合在派发上下文里同步去重（背靠背请求折叠为一次装配）；每个 persona 一把 wake lock 串行化启动与排空；装配有 60 秒超时兜底——挂起的启动不能永远占住锁。
- **空闲事实检测，不决定后果**。空闲监视器每 30 秒扫描一次，对"lazy 且空闲超时且无存活会话"的成员发布 `AGENT_IDLE`——这是事实，不是决策。空闲计时含 session 之外的真实后台活动：进行中的反思 LLM 调用算作活跃，成员不会在审计中途被判空闲。排空执行（`_drain_agent`）在锁下**复查**条件：竞态窗口里新开的会话能保住成员。统一成员模型（ADR 0006）：排空一律是睡眠——home 目录永不删除，只发 `AGENT_SLEEPING`。
- **spawn 预算**。三道防失控闸门：全局同时运行上限 `max_activations = 8`、spawn 链深度 `max_spawn_depth = 3`、每 (spawner, session) 配额 `session_spawn_quota = 2`。配额账本落盘 `~/.tlon/shared/spawn-quota.json`（原子替换），跨重启存活——会话本身经 journal 回放存活，纯内存配额会让重启变成配额重置器。每会话配额按血脉适应度倾斜（，ADR 0014）：每次 spawn 现算目标血脉的效率（`agents/fitness.py`：血脉价值 = Σ_成员 (1 + endorsements_received)，效率 = 价值 / max(total_tokens, 1)），tilt = clamp(效率 / 全体均值, 0.5, 2.0)，有效配额 = `ceil(quota × tilt)`——高效血脉配额放宽、低效收紧；冷启动与一切计算故障回落中性 1.0（与现状无行为差异）。见习配额（，信任阶梯）：spawner 自身是见习成员（spawn 出生且未获 endorse，按 **spawner_id** 判定——注意与 tilt 按目标血脉判定的维度差异）时，有效配额再收紧为 `min(effective, 1)`，fail-open 同 tilt。全局激活上限不按血脉拆分，避免误伤暂时低效血脉的现役成员。spawn 前还有信任闸：未受信的（LLM 撰写、待审批）原型不能实例化。预算与信任闸是纯机制；"谁可以造新成员、一次造几个"是策略（可数据化，见 `design/unified-member-model.md`）。
- **spec 派生的生命周期档案**。boot 集合（`boot_candidates`）、lazy 判定（`is_lazy`）、唤醒资格全部从缓存的 spec profile 派生；加载失败缓存为 None，热重载/收养时才失效重试。spec 是生命周期归属的唯一权威，注册表不维护第二份事实。

### 热收养：运行中的系统接纳新成员

`AgentRegistry.adopt_agent` 让进程外创建的 agent（向导写的 `agent.yaml`）无需重启即被系统接纳：配置提供者重载、注册、非 lazy 立即走同一条 `_start_agent` 装配路径，lazy 则只注册、等第一封邮件唤醒。这里的设计要点是**收养与冷启动共用一条装配路径**——系统里没有"运行时才有的第二套启动逻辑"，新成员与开机成员经历完全相同的 spec 校验、能力装配与 wake lock 纪律。spec profile 缓存条目在收养时失效，保证磁盘上新写的行为文件会被重新读取而非沿用旧派生。

### spawn 出的成员：与手建成员同构

spawn 出的成员与手建成员**同构**：同一个 `MemberRuntime`、同一个注册表，身份由注册表生成（`c-{prototype}-{8hex}`，调用方不能指定），boot/lazy 判定由原型 spec 的 `:on :boot` 规则派生，`spawned_by` / `spawn_depth` / `prototype` 保留为血缘审计。孤儿策略是 reparent 而非 kill——成员独立注册，spawner 睡眠或停止不影响它，由自己的空闲策略回收（sleep，不删）。成员只会"睡"，不会"蒸发"（档案归档）。

## 方向

与愿景的差距主要在**个体学习未闭环**。`ReflectionEngine` 的结论只能写回记忆（事实与摘要），改善的是后续会话的上下文；它到不了 spec——成员的 `behavior.tlon` 在整个生命周期里不变，经验无法改写行为本身。

愿景路线里"闭环"一步要求"经验沉淀为知识，知识能改写行为"：记忆层已完成沉淀的一半，把反思结论安全地回写进 spec（或经原型库生成新行为）是尚未接上的另一半。运行时侧的地基已经就位：DSL 求值器可停机、stage 白名单可组合、effects-as-data 边界机器可检查——行为一旦可写，改写它的工具必须仍然落在今天这套机制之内，而不是绕开它。

其次是**策略仍是内置的**。Adam 的 `ADAM_SPEC_EDN` 证明了"策略可以是数据"，但这份数据目前随代码发布，与普通成员的 `behavior.tlon` 不同源。社会层面的自进化（策略由社会内部产出、评审、替换）与个体学习闭环共用同一个前提——行为写回通道——但它多一层问题：生命周期策略写错的代价是系统级的，信任闸该放在哪，是把策略也纳入原型库信任分级时要一并回答的。

这两处差距共享同一个判断：运行时今天要做的不是提前实现闭环，而是保证闭环到来时不需要拆开引擎。