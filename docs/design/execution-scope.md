# 执行作用域、共享额度与写入边界（P4b）

> P5：普通模块共享 output 根；旧 run-loop/recipe total 根的创建入口已删除，total 账本语义保留供宿主维护使用。本文涉及 stage/session 的说明属于旧阶段记录；当前路径见 [Runtime](runtime.md)。

状态：EPIC A、P5 #193 与 P6 #194 已合并，P7 暂停；本文描述当前共享执行契约。总体方案与验收见 [operations-plan](operations-plan.md)，叶子结果以 [ProgramStore invocation](invocation-recovery.md) 为权威。

## 宿主作用域

ExecutionScope 由宿主创建，包含额度根、确定路径、取消信号与可选 UTC 截止时间，通过显式绑定的 ContextVar 传到内部服务。Tool/DSL 输入不能指定身份、额度或取消对象。成员 LLMClient 的调用边界读取作用域，因而 stage、压缩和操作内部的模型请求不能绕过当前额度。

程序执行使用 output 根，额度来自成员 program_token_budget（默认 100000）；llm.max_tokens 仅为单次输出上限（默认 4096）。规划、行动、压缩、自评及提案内嵌模型请求共享所属根，保留获准输出预留成功不退款的规则。普通模块函数和 turn 不创建额外预算或历史权威；维护执行使用独立维护根。ProgramStore 保存根和调用回执，RunJournal 只读查询。旧 agent session 已退役，无历史读取兼容或迁移。

取消信号用于当前进程的任务树，shutdown 中断后仍允许按原记录恢复；它不是跨重启的永久撤销标记。程序和会话终态继续由各自生命周期记录决定。额度、计量口径与有效 UTC 截止时间则在根创建时持久冻结，恢复不能提高额度、重置消费或延长截止。

## 预留、响应与恢复

每个底层模型请求使用作用域路径和本位置序号标识。ProgramStore 在事务中检查剩余额度、创建 allocation 和模型 invocation intent。allocation 只表示额度占用，完整响应只在原 invocations 中保存，不建立第三份结果权威。并发请求不能共同预留超过剩余额度；请求输出上限按剩余额度裁剪。

output 根成功后仍占用原输出预留。total 根的预留为输入估算加输出上限，成功后按实际 usage.total_tokens 结算；完整的 prompt/completion 用量也可相加。缺少可靠 usage、取消或提交未知时不退预留。实际 total 超过估算时如实记录超额并拒绝后续新请求，不能宣称 token 估算保证 provider 实际用量永不越界。

成功模型响应与预算结算在同一 SQLite 事务提交。若响应已提交而 journal 尚未更新，重放返回原 LLMResponse，不再请求 provider，也不重复扣额度。只有 intent 的模型请求保留 unknown，禁止自动发第二次。明确的 provider 拒绝可以释放预留，并保存拒绝事实。当前作用域内关闭“超时/连接错误后自动再次请求”的路径；恢复不能把未知费用当成零。

run-loop 的程序调用位置对应稳定 session ID。同进程重入和进程恢复均重新使用该会话，不能通过创建新 ID 绕过未决模型调用。模型 unknown 保留未完成根并向父传播，不写成成功的 session/end 或程序效果结果；未决预留检查先于预算停机与用户 until，不能把未知消费伪装成正常结束。用户显式 until 仍可终止没有未决请求的会话，即使某个成功模型响应尚未投影进 journal；不承诺整条 stage 向量的任意位置都能精确续跑。

## 成员写入与取消

成员只装配一个写 gate，显式注入 OperationExecutor 与 MemoryManager；反思、蒸馏和直接记忆写入也使用同一把锁。读操作仍可并发；写操作在 gate 内检查当前取消、截止和实际领域授权后执行。同一 asyncio task 的嵌套 await 可以重入；同一根的不同 task 不能借相同 root_id 重入。父程序等待 run-loop 时不持有写锁。

一批 Tool 里的写操作显式按模型声明顺序排等待链，读操作不进入该链。单靠锁的等待顺序不足以兑现声明顺序，因为 SQLite 查询等 await 会改变到锁先后。任务创建与登记之间不插入 await，随后才发布分发事件；取消时能够找到并等待全部子任务，不留下游离写入。

历史回执在锁和当前取消检查之前返回。新效果在等锁后、写 intent 前以及进入 handler 前检查控制信号；超时由共享 executor 实施：成员 tool.per_timeout 是默认值，操作 default_timeout 覆盖，schema 声明的显式 timeout 再覆盖，另加操作 timeout_grace（Shell 为 10 秒），最后与根期限取小。该局部期限传给内部模型请求，但只收紧执行，不改写预算根冻结期限。LoopDriver 不另套计时或合成超时回执。取消不能回滚已经派发的外部效果，未知提交继续沿调用恢复契约处理。该写边界不是统一授权闸；当前没有统一操作 allowlist 或受控授权阶段，不能声称已经支持统一撤权。tools 尽量固定，未来执行授权与工具展示稳定性分开设计；工具是否展示不能代替现有领域授权检查。spawn 配额根取宿主 CallContext.run_id，不取日志上下文；恢复仍使用原会话配额，直接 DSL 使用程序根。

并发控制复用 [asyncio 任务取消](https://docs.python.org/3.13/library/asyncio-task.html#task-cancellation)与[锁](https://docs.python.org/3.13/library/asyncio-sync.html#lock)机制；保留项目现有跨轮 pending 任务所有权，不新增工作流框架。

## 查询与命名收敛

pure 仅表示确定性计算；外部查询直接声明非 pure 并正常挂起、采样与重放，不再由 ProgramDriver 按名字改写。P6 统一 when、until、stage-if 的纯函数准入，删除 Builtin.read_only 字段，外部查询不进入条件；先在 actions 取得已记录数据，再用于纯计算。

运行时装配容器与函数改名 MemberServices / assemble_member_services，准确表达服务集合；Capability artifact 与管理 API 已删除。旧容器名、局部预算计数和 _HOST_QUERIES 重写直接删除，无兼容别名或存储迁移。

## 模块程序模型边界（工作流程 P2）

普通程序与 v1 stage 通过同一 ModelService 调用成员模型。v2 入口受理时将宿主系统提示、工具定义和非秘密模型配置随源码包原子冻结；后续调用使用冻结值。新请求还校验当前工具定义，已提交的历史结果则按冻结契约重放。纯消息模块 `tlon.messages` 随运行源码冻结，不新增模型结果日志。

`model-call` 外层 Operation 与内部模型调用各有稳定路径。模型成功响应与额度结算已提交、外层回执缺失时，executor 在预算、取消和截止检查之前校验原请求并补回外层结果；明确拒绝已记录时补回 RejectedError，保留已退款事实。恢复只读取原模型记录，不派发 provider 请求。内部只有未知 intent 时仍拒绝自动重发，保留预留。output 成功保留输出上限费用，total 按可信实际用量结算，核验函数不会另建预算根。
