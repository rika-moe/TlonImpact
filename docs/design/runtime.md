# 成员运行时设计

> P4a 已通过 #189 合并。P4b 将共享额度、取消/截止时间与成员写顺序接入同一宿主执行作用域，当前契约见 [执行作用域](execution-scope.md) 与 [调用恢复](invocation-recovery.md)。后续组合能力与动态工具视图见 [分阶段计划](operations-plan.md)。

成员共用四态调度：活跃、等待、定时睡眠、死亡。活跃由实际执行、模型或工具调用及后台维护派生；等待实例可以卸载，卸载不等于睡眠；死亡拒收新邮件并保留历史。管理员可请求排空后等待、定时睡眠、提前唤醒或永久退出，完整契约见 [agent-scheduling-states](agent-scheduling-states.md)。

[ADR 0042](../decisions/0042-unified-member-rule-execution.md) 删除了按是否包含会话循环区分成员或规则的模型。每个成员执行同一种事件程序：`规则 = :on + :when + :actions`，可选 `:fuel`。传统循环、条件分支和模型会话可以组合；是否调用模型不是成员类型。成员与原型关系见 [unified-member-model](unified-member-model.md)，语言设计见 [dsl](dsl.md)，字段见 [DSL reference](../reference/dsl-reference.md)。

## 统一解释器

[MemberRuntime](../../src/tlon/agents/member_runtime.py) 负责事件接纳、规则求值和串行执行效果。

```text
事件 / 持久邮箱补偿
  → 成员事件队列（邮件去重）
  → 冻结本次规则源码与触发输入，按 spec 顺序求值
  → 遇到效果调用：读取已记录结果，或执行并记录实际结果
      → state-get / state-put：成员私有结构化状态
      → model-call / mail-send：返回模型内容或邮局回执
      → run-loop：按本次配方执行会话
  → 将结果送回表达式，继续 let / if / 函数 / 循环
```

事件回调只接纳工作，成员工作者串行处理；同一成员不会因为命中多条规则而并发执行多个程序。所有事件都可以产生 `run-loop` 请求，同一成员也可以调用不同配方。规则的求值环境始终是 `{msg, event, self}`，非邮件事件的 `msg` 为 `nil`。不再有单独的完成动作环境或隐式 `result` 绑定。

邮件、文件、Shell、时间及 P3b 成员服务通过共享 Operation 执行并返回真实结果。生命周期、演化和 model-call 也使用共享 Operation，只有 run-loop 保留 ActionRequest。生产运行通过 [ProgramDriver](../../src/tlon/agents/program_driver.py) 驱动 tlosure 的异步求值入口：在效果调用处交给宿主，实际结果落盘后送回表达式。同步求值入口与 collector 仍可用于分析请求数据；它们不是生产程序的 I/O 执行路径。[ADR 0043](../decisions/0043-newspaper-member-workflow.md) 替代了 ADR 0042 阶段“整个规则求值成功后再执行全部效果”的实现。

已提交的效果不会因后续表达式失败而回滚。可重试 I/O 或未知提交保留未完成记录，重新执行时返回已有结果，再推进未完成部分。确定性求值错误、燃料耗尽等记录失败终态，保留错误和已提交事实，从待恢复队列移除，让后续工作继续；失败不标记为成功，也不因重启反复执行坏程序。参数、定义或构建身份不一致时报错，不能猜测恢复。当前 ProgramStore 使用 user_version=2；旧数据库格式与缺少构建身份的会话记录明确拒绝恢复，不迁移、不自动清库。

### 成员程序存储与恢复

[ProgramStore](../../src/tlon/agents/program_store.py) 在成员私有 `program.sqlite3` 保存本次规则原始源码、触发输入快照、完成状态与失败原因。`roots` 冻结构建身份，`invocations` 是 DSL 与模型 Tool 的操作 intent、成功或错误回执的唯一权威；旧 `intents` 表及操作专用 effects 路径已删除。`effects` 仅保留 `run-loop` 与尚未迁移的宿主查询。`state-put` 与同一 invocation 的成功回执在一个 SQLite 事务提交；回放已完成的写操作只返回原结果，不覆盖后来写入的状态。同一事件匹配的全部规则在一个事务中预登记，防止前一条规则标读后崩溃丢失后续规则；未完成规则恢复之前不执行后续规则或新事件。磁盘连接按操作打开并关闭。

装配就绪后由工作者先恢复未完成程序，再执行 boot 和新工作；启动不等待模型 I/O。恢复期间新事件可入队，但不能越过未完成程序。恢复使用记录中的源码和输入，读取、模型调用以及动态宿主查询复用已记录结果，不重新观察变化后的状态。来源邮件即使已读也不丢弃未完成程序。四态接纳门与管理员排空继续生效；I/O 等待属于 active，睡眠等唤醒后恢复，死亡不执行。记录开始前未入队的非邮件事件不享有通用持久队列保证。

`model-call` 是一次无工具调用的模型查询，保留成员人格、RULES 和协议前缀。程序直接调用使用持久 output 额度根，按每次获准的输出上限预留，成功后不退款；回放还原原结果与既有占用，不重复扣额度，缺少 usage 也不能让后续调用免费。实际 provider 输出上限取请求额度、规则剩余额度与成员 `llm.max_tokens` 的最小值。已记录的内容直接复用；模型调用使用非幂等 intent，已分发但未记录结果时保留 unknown，不自动重发。

## 求值与资源边界

`tlosure` 不依赖 tlon，只提供解析、受预算约束的求值、静态校验和通用词汇。每步消耗 fuel，深度另有限制。规则显式声明 `:fuel` 时门控与全部动作共享一个计数器；未声明时使用门控与动作默认预算。会话配方的 `:fuel` 单独约束 `:until` 和 stage 条件；会话累计 token 和轮次还有独立硬上限。

门控失败不匹配规则，动作求值失败跳过该规则；stage 条件失败跳过该 stage，终止表达式失败回到引擎终止检查。尚未提交的新效果执行仍需通过实时状态、信任、权限和配额检查；已经提交的历史只还原回执，不重新进入当前 handler 的授权检查。合法 DSL 不能借此产生新的越权效果。

## 会话执行管线

`(run-loop (quote {...}))` 是普通效果词。`quote` 保留配方中的 section、stage 和终止表达式，不在外围规则求值时执行它们。配方由独立的 `SessionRecipe` 表达，加载时检查字面配方，执行时再次校验动态配方。

```text
run-loop 请求 + SessionRecipe + 原始事件 / 邮件
  → SessionScheduler.run_invocation
  → AgentSession
  → LoopDriver
  → think / plan / critique / revise / act
  → 真实工具执行与 EffectExecutor 邮局提交
  → 会话完成与维护
  → 原规则剩余效果
```

### SessionScheduler

[SessionScheduler](../../src/tlon/agents/scheduler.py) 推进一次具体会话，管理超时、恢复、清理、反思计数与记忆蒸馏。每次调用携带自己的配方、触发内容、来源邮件、责任集和所在程序效果的位置。调度器不再从成员的第一条规则挑选固定循环配置，也不要求必须存在邮件才能创建会话。

会话暂停等待模型或工具时仍属于成员的公开活跃状态。`AgentSession` 内部 ACTIVE/WAITING 是推进协议，不等于成员公开的四态。死亡、睡眠及排空意图的处理由注册表门控所有入口。

### AgentSession 与 journal

[AgentSession](../../src/tlon/agents/session.py) 持有上下文、迭代与错误计数、累计 token、工具结果、后台任务和 stage 草稿板。草稿板由引擎持有，DSL 可经受限谓词读取，不能直接改写。

[SessionJournal](../../src/tlon/agents/session_journal.py) 将事件写入每个成员的 `sessions.sqlite3`，是模型可见历史和恢复状态的事实来源。初始 `session/start` 记录配方、来源邮件和程序效果位置；恢复必须使用记录中的配方，不能取当前 spec 任意一条规则替代。嵌套效果数据以 EDN 保存，保留表达式与向量的区别。

journal 先落盘再更新内存投影，记录上下文、工具派发与供模型消费的回执、计数及结束事件。操作是否提交由 ProgramStore 的 invocation 回执判定，journal 不是第二份操作提交权威。工具结果恢复须保留模型协议要求的配对，不将未执行草稿伪装成工具结果。派发完成记录与未读事实分别管理，已处理的邮件规则不会因重复通知或扫描再次入队。即使工具结果已经写入 journal，恢复前仍验证 root 构建身份。孤立 dispatch 尚无 invocation intent 时可以开始执行；已有成功或失败回执直接还原；非幂等写仅有 intent 时保留 unknown；安全写使用相同调用键对账，未提交的读取可重新采样。这不构成任意外部操作“恰好一次”的保证。

### LoopDriver 与上下文

[LoopDriver](../../src/tlon/agents/loop_driver.py) 按本次配方解释 stage 向量及条件，执行统一终止检查，持久记录并应用 stage outcome。[ContextBuilder](../../src/tlon/agents/context_builder.py) 按同一配方的 section 向量构建上下文；人格、协议、技能、邮件历史和近期工作摘要各有明确输入。

上下文压力管理复用 journal，先裁剪或摘要可压缩部分，保留系统前缀与锚点。stage、上下文压缩及工具内部的模型调用共用本会话的 total 额度根，不能另行绕过预算。`thread-recap` 是最近已完成会话的有界事实摘要，不调用模型，也不代表邮件线程归属。

### 五个 stage

| Stage | 职责 |
|---|---|
| `think` | 调用模型，产生本轮内容及工具调用 |
| `plan` | 生成有限计划，按条件插入会话草稿板 |
| `critique` | 评审本轮输出，使用显式指令与执行事实 |
| `revise` | 将失败反馈和被拦截草稿交给下一轮 |
| `act` | 派发本轮工具调用，收集真实结果，处理结束请求 |

stage 通过 `StageServices` 调用获准的读取/模型能力，返回 `StageOutcome` 数据；驱动器应用状态和外部效果。`act` 的派发与结果收集跨越异步边界，同轮独立读取可以并发；写操作按模型声明顺序进入同一成员写 gate，同一 task 的嵌套 await 可重入，不同 task 不能借相同 root ID 绕过锁。

被评审拦截的邮件草稿明确标记尚未派发、尚未发送。只有真实邮局回执能够证明提交；评审意见、自然语言和草稿正文均不能代替回执（ADR 0040）。

## 效果执行与邮件

邮件操作 `mail-send`、`mail-forward`、`mail-check-inbox`、`mail-search`、`mail-mark-read` 定义在 [operations/mail.py](../../src/tlon/operations/mail.py)，由成员作用域的 [MemberMailService](../../src/tlon/organization/mail/member_mail.py) 执行。DSLAdapter 与 OperationTool 共用 schema、服务及结果；回复线程、转发正文、endorsement、mentor cc 只在邮件领域实现一次。LoopDriver 不再根据工具名二次提交。

发送操作返回真实 `mail_id` / `request_id` / `local_recipients` / `gateways` 回执。明确拒收抛 `RejectedError`，未确定是否提交抛 `UnknownOutcomeError`，两者都继承 `OperationError` 并带结构化 `data`；Tool 入口将其投影为失败结果。DSL 不把失败当成成功值记录。接受只表示本地入箱，不保证外部网关最终送达。

两入口的操作调用都由 ProgramStore 保存。程序稳定路径来自冻结 run 与调用位置；会话路径包含 session、轮次与模型 tool call ID，避免不同轮次复用模型 ID 时错误命中旧回执。session journal 继续保存 dispatch/result/collect 协议状态。邮局按发件身份和 request_id 幂等提交，覆盖已入箱但程序或会话结果未记录的崩溃窗口。超时与中断结果保留 request_id 和 unknown；重试必须复用该 ID，相同 ID 改内容会冲突。已有结果只重放结果，不重发。DSL 的 UnknownOutcomeError 不将运行标为失败或记录成功，保留 pending，恢复时用相同调用 ID 重试；明确拒收仍作为程序失败处理。

DSL 和模型都显式指定邮件引用。例如 `(mail-forward {:mail_id (mail-field msg :id) :to ["bob@tlon.internal"]})`；不存在自动从当前触发邮件补收件人或转发来源的隐式入口。回复用显式 `in_reply_to`，线程由本成员邮箱中的被引用邮件推导。

读状态与运行状态独立。命中规则、完成会话、进入等待均不隐式标读；程序可用 `(mail-mark-read {:mail_ids [(mail-field msg :id)]})` 标记当前来源邮件，模型可用 `mail-mark-read` 选择邮箱中的邮件。自寄保护和拒绝消费的处理不用于区分规则类型，具体邮局契约见 [mail](mail.md)。

## 唤醒与补偿

boot/lazy 与邮件订阅资格来自 spec，不是当前调度状态。注册表发现已卸载的等待成员有来信时，按订阅与四态门控装配成员；睡眠成员只入箱，除管理员提前唤醒外等到期限。死亡成员由邮局直接拒信。

邮件在通知前已经持久入箱。启动扫描、周期扫描与邮局恢复通知都走同一事件接纳路径；内存中的待处理集合防止已排队或执行中的邮件重复加入，journal 中的分派完成记录防止完成规则重复执行。保留未读是成员的选择，不意味着自动反复执行同一条已完成规则。

事件队列是运行期调度投影，持久邮箱、程序存储与会话 journal 保存各自负责的可恢复事实。程序重放不构成任意外部效果的跨系统事务；邮件幂等性来自邮局已有提交记录。

## 预算与终止回复

`max_iterations` 与会话 `max_tokens` 共同限制执行，`:until` 不能覆盖。run-loop 显式建立 recipe.max_tokens 的独立 total 根，不把整个会话扣进程序默认输出额度。stage、压缩、model-call 和提案/评审内部调用均在成员 LLMClient 边界原子预留；成功按可靠的 total usage（或完整 prompt/completion 之和）结算，缺少 usage、取消或提交未知时保留预留。session.token_usage 与 journal tokens 是该持久账本的投影，不再独立决定另一套扣减规则。

输入 token 估算只用于准入；实际 total 高于预留时记录真实耗用并阻止后续请求，不承诺 provider 实际消费永不越界。output 与 total 两种根共用 ProgramStore 的预算表，模型成功响应与结算同事务提交；恢复不重置额度、重复扣费或重新请求已记录响应。模型请求只有 intent 时保留 unknown，不自动重发或写成会话成功。

工具有单调用超时，实际等待取工具上限与根剩余时间的较小值。额度、口径和有效 UTC deadline 在根初始化时冻结，重启不能延长。取消是当前进程任务树的信号，子作用域取消不影响父作用域；shutdown 中断不是跨重启的永久撤销，原运行仍可按持久事实恢复。即使 stage 向量没有 act，引擎也推进迭代计数。详细边界见 [执行作用域](execution-scope.md)。

预算耗尽通知由运行时确定性生成：明确任务未完成，仅依据本次真实工具回执统计确认提交、拒绝和未知，按 `request_id` / `mail_id` 去重。草稿不计入，通知本身不计入，不额外调用模型；邮局提交也不代表收件人已读或外部送达。

终止通知只发给责任集内已知人类发件人，每人一封，以其责任集中最后一封邮件为回复锚点；非邮件事件或责任集中没有人类发件人时不发通知。正常 `mail-send` 仍可自由选择合法收件人。

## 反思与完成

[ReflectionEngine](../../src/tlon/agents/reflection.py) 的参数来自当前会话配方，按间隔与完成会话数检查记忆维护条件。后台反思、蒸馏均计入实际活动，并使用独立维护额度根，不能继续消费已结束会话的额度。排空与关闭要等待或取消并收割后台任务，不能遗留游离协程。

会话结束后由统一程序继续求值，不再有第二个完成动作求值器。需要一次明确模型结果时用 `model-call` 并绑定其返回值；`run-loop` 返回 `nil`，不隐式暴露会话草稿板。

## 注册表与能力装配

[AgentRegistry](../../src/tlon/agents/agent_registry.py) 负责启动、锁与去重、四态持久事实、排空、唤醒、配额和热收养；Adam 的 DSL 规则选择空闲卸载、启动和噪声治理政策。成员只管理自身调度，人类管理面可管理其他成员。

`assemble_member_services` 根据成员配置、装置等级与信任政策构造服务，实际模型调用和容器启动按需发生。L0 无执行装置，L1 只注册文件工具，L2 可用 shell；见习成员缺少受信政策禁止的演化工具。能写 `run-loop` 不会授予额外权限。

新成员热收养和冷启动走同一加载装配路径。spawn 来源及世代字段用于审计；父成员的睡眠或死亡不自动终止子成员。自动卸载保留历史，永久死亡与人类显式删除的区别见 [成员模型](unified-member-model.md)。

## 当前演化接口与未实施方向

行为原型、复合能力和受信工具的边界分别见 [prototypes](prototypes.md)、[capabilities](capabilities.md)、[Bridge API](bridge-api.md)。成员自主调整 context 仍是远期方向。

随包 [报社示例](../../src/tlon/resources/examples/newspaper/README.md) 用普通 DSL 实现订阅、退订、按真实邮件 ID 收稿、明确期号出版、冻结内容及订阅者快照、逐人幂等投递。冻结版本在投递前持久化，同期号重试不重新编辑；新稿与新订阅进入后续期刊。来源编号来自真实 `Mail`，模型不负责生成来源附录。示例不会在首装或升级时新增默认成员，也不改现有 `~/.tlon`。


EPIC A 审计收尾将两入口超时统一到 OperationExecutor，并由同一成员 write gate 保护 Operation 与 MemoryManager 的写入（含反思/蒸馏）。spawn 配额由冻结调用根决定，恢复不依赖日志会话绑定。预算和期限的确定拒绝跨领域包装保持原语义；超时后以真实调用回执为准，只有可能已执行且无回执的调用才为 unknown。
