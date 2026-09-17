# ADR 0004：stage 纯度——状态变更一律声明为 StageOutcome 数据

状态：已接受（回溯记录，2026-08-15）

## 背景

认知循环的 stage（think / plan / critique / revise / act）需要满足三个要求：

1. **可组合**：spec 的 `LoopSpec.stages` 是白名单向量，DSL 可以选哪些 stage、以什么顺序、带什么 `:if` 条件组合它们。
2. **可测试**：每个 stage 是一个语义单元，要能脱离 asyncio 调度与外部系统独立验证。
3. **可信任**：愿景要求 DSL 最终交给 agent 自己书写。组合出来的循环不能有任何一条路径偷偷改状态——组合的每个积木必须可被静态检查。

如果 stage 是命令式代码、直接读写 session、直接发邮件，前两条会退化为"靠 reviewer 盯住每个 stage"，第三条直接不成立。

## 决策

**stage 是纯函数 `Session × StageServices → StageOutcome`；一切状态变更与副作用声明为数据，由引擎统一应用。**

具体分工：

- **stage 只计算**。想做的一切变更——context 追加、scratch 写入、迭代/错误计数、工具结果与分发、响应链、邮件（`MailEffect`）、交互保存、on-complete、完成信号——都返回在 [agents/stages/outcome.py](../../src/tlon/agents/stages/outcome.py) 的 `StageOutcome` 里。stage 不触碰 session 的任何 mutator。
- **只读查询与可观测性走服务**。LLM 调用（只读外部查询）与日志/事件/trace（可观测性）经注入的 [agents/stages/services.py](../../src/tlon/agents/stages/services.py) 的 `StageServices` 进行——它们不是状态变更，不进 Outcome。
- **应用顺序集中在引擎**。`LoopDriver._apply_outcome`（[agents/loop_driver.py](../../src/tlon/agents/loop_driver.py)）是会话状态的唯一变更点，按固定顺序应用 Outcome；外部效果委托给 `EffectExecutor`（[agents/executor.py](../../src/tlon/agents/executor.py)）：发信、记忆保存、收件箱 cursor 推进。并发（每工具一个后台 task）也是引擎的，act 只声明分发列表。
- **纯度由静态检查强制**。[scripts/check_stage_purity.py](../../scripts/check_stage_purity.py) 用 AST 禁止 stage 模块调用 session mutator 方法、禁止对 session 属性赋值，纳入验证质量门，违规即构建失败。

## 理由

- **语义单元可枚举、可测试**。stage 的全部输出就是 Outcome 的字段集合，测试只需构造 session + fake services、断言返回的 Outcome，不需要 mock 事件总线或邮箱。
- **应用顺序集中可审计**。状态变更只发生在 `_apply_outcome` 一处，顺序固定且与历史实现等价可比对；新 reviewer 读一个函数就知道循环会做什么。
- **DSL 组合 stage 才安全**。白名单组合的前提是积木不会逾矩：stage 在类型层面拿不到可变的 session 操作面，在检查层面被禁掉直写——agent 写的 loop 程序最坏结果是跑出一个 no-op 或被 `max_iterations` 截停，不可能破坏引擎不变量。
- **引擎/策略边界机器可检查**。"stage 纯、引擎执行"不靠 code review 记忆，而靠 `check_stage_purity.py` 每次构建强制——与其他架构检查（`check_architecture.py`、import-linter）同一套治理方式。

## 备选方案

- **stage 命令式直改 session**：stage 拿到可变 session 与执行器句柄，想改就改。实现更简单、少一层 Outcome 数据类。被否：纯度不可静态约束，DSL 白名单组合失去安全地基，stage 的测试必须重建半个引擎。

## 后果

- **状态变更须绕行声明式，略繁琐**。stage 里多写一份 Outcome 构造而不是一行赋值；这是为可检查性付的固定税。
- **新增状态种类要改两处**。任何新的可声明变更需要同时扩展 `StageOutcome` 的字段与 `_apply_outcome` 的应用顺序（含与既有步骤的先后论证）；漏掉后者时变更会静默丢失，因此新增字段必须配套引擎侧的应用与测试。
- **act 被 step/collect 拆成两半**。asyncio 的"分发后收割"模型让一个语义单元（`act_stage_step` / `act_stage_collect`）拆在两个文件函数里，阅读时要拼合；这是纯度与并发模型妥协的可见接缝。