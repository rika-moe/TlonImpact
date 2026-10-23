# 可编程工作流程：目标设计

状态：已接受；P1–P4a 已合并（#208/#209/#210/#211），P4b 本阶段实现，P5 尚未实施，2026-10-23。决策见 [ADR 0046](../decisions/0046-programmable-workflows.md)，施工与证据见 [实施计划](programmable-workflows-plan.md)。当前机制仍见 [DSL](dsl.md)、[runtime](runtime.md) 和 [调用恢复](invocation-recovery.md)。目标与实现边界以分阶段计划为准；当前语法/API 见[模块程序指南](../guides/writing-a-module-program.md)。

## 1. 执行与分层

事件 → Behavior 导出入口 → 模块函数 → Operation → 宿主领域服务。所有工作方法使用同一个 tlosure 求值器和 ProgramDriver；标准对话循环也是函数。模型协议、工具回执、预算、调度与资源权限不能由作者绕过。

| 概念 | 所有权与职责 |
|---|---|
| tlosure module | 命名、导入导出、词法作用域及普通函数调用；不访问文件系统和审批服务 |
| Behavior | 显式事件到导出入口的映射；筛选、分支和组合在入口函数中 |
| Operation | Python 宿主契约与效果执行；共享 schema、身份、权限、预算和回执 |
| Agent | 身份、模型、人格、记忆、资源授权与程序运行实例 |
| Prototype | 程序包的审查、参数化与创建来源；不承担函数继承合并 |
| Tool | 宿主操作面向模型的投影；不自动暴露所有函数 |

operations 不依赖 agents，tlosure 不依赖 tlon；模型和工具服务以端口装配，不反向调用成员运行时。ProgramDriver 是求值与效果调度边界，不包含计划、核验或压缩政策。

## 2. 模块与发布

示意：

```clojure
(ns work.reply
  (:require [work.review :as review])
  (:export handle-mail))

(defn handle-mail [event]
  (let [draft (make-draft event)
        result (review/check draft)]
    (when (get result :passed)
      (mail-send {:to [(get event :sender)]
                  :content (get result :content)}))))
```

模块只定义函数和不可变常量；导入时禁止外部操作，不注册事件，不运行用户工作。命名空间与文件映射唯一；重复模块、重复绑定、缺少导入、访问私有函数及模块依赖环在加载时拒绝。函数递归和一等函数保留；闭包捕获局部值而非可变全局环境。首版无需宏、运行中 require、依赖版本求解或可变导出。

语言模块加载器接收宿主提供的源码映射。Tlon 程序包包含 Behavior、模块源文件和静态依赖；运行包的相对路径必须规范化并拒绝路径穿越、符号链接越界和同名覆盖。运行时只从已解析程序包与固定标准库解析，不搜索当前目录或任意宿主路径。

沿用原型提案/人类审批渠道，扩展其完整包读写、预览、校验与实例化；不另建 Capability 目录和逐函数审批系统。成员可以提交模块源文件及入口变更供审查，审批涵盖完整依赖内容。跨程序复用的源码在发布时显式纳入包并固定，导入不实时追随另一个成员文件。v2 源码包发布后不可原位 PATCH，改源码需新建关联 parent 的原型；元数据编辑不重新批准源码。实例化复制完整程序包，不仅 behavior 文件；本轮不开放成员自行切换程序版本。新运行使用新批准程序，已有运行固定旧包。标准库由宿主发行并纳入构建身份。

Behavior 目标示意为 `{:handlers {:mail-delivered work.reply/handle-mail}}`。仅导出函数可作为入口；一个事件映射一个入口，多步动作与业务条件都由函数明确表达。部署额度与执行装置不由模块导入授予。不得在一份最终格式中同时维护旧规则和新 handlers 双源。

## 3. 模型请求与上下文

统一 model-call 接受消息、工具名集合、输出上限等受控选项，返回 content、tool_calls、finish_reason、usage。文本请求使用同一契约的空工具集合，不保留第二条 think 专用请求路径。程序不能传入任意工具 schema 冒充授权接口，工具名由宿主从成员可用 Operation 投影。

成员模型、身份、Persona/RULES/协议与系统约束由宿主绑定。运行开始时物化固定的系统输入、模型配置和工具契约，实际模型请求冻结有效输入；新请求重新检查当前权限，不能因历史快照获得已撤销权限。工具展示默认在对话内稳定，核验可选择空工具集合；不建设逐阶段动态 ToolView。

邮件、知识、skills、教训、时钟等外部信息经受控读取取得并记录；选择和组织上下文由库函数表达。上下文窗口校验、provider 协议归模型适配层；压缩、重试与错误反馈策略归工作程序，模型压缩也经同一操作计费和恢复。缓存前缀处理复用现有 provider 能力，不能为了工具选择频繁改写 schema。

模型结果、预算结算与调用结果必须协调原子提交，保留现有 ProgramStore model_allocations/invocations 权威。不得复制一份账本或由包装父请求重复扣费。现有 output/total 的预留和结算语义保持；不能将 output 的预留额度记账写成已按实际 completion usage 计费。已派发但结果不确定的请求保持 unknown，不伪装普通失败并重新请求。

### P2 当前实现

`model-call` 必填 `messages`、`max_tokens`，可选 `tools`（工具名列表，默认空）与 `cache_prefix_len`。返回 content（可为 null）、规范化的 `{id,name,arguments}` tool_calls、finish_reason、usage 和 reasoning_content。输入拒绝旧 prompt 字段、system 消息及任意工具 schema。普通程序与旧 StageServices 共同使用 ModelService；旧 stage 引擎保留至 P5。

宿主提供纯模块 `tlon.messages`（user/assistant/append），保留 tlon 命名空间；链接时合并标准源码，运行快照包含完整标准源码，作者模块与审批 API 保持原作者内容。v2 受理事务同时冻结 host（系统词、工具定义）与 config（有效非秘密模型配置，包括运行时重试/超时参数）；同一根运行复用它们。模型的工具选择只影响本次声明，不执行工具。

Operation 的恢复 hook 只读取同根、同稳定调用路径的已提交模型结果，核对有效消息/系统词/工具/max_tokens/缓存前缀，再补外层回执。未知模型请求保持阻塞。新请求还校验当前工具契约；历史已提交结果不要求工具仍可执行。output 按获准输出额度计量，total 沿用现有结算，不另设账本。

## 4. 工具分派和错误

工作程序可检查模型响应，在效果发生前核验或拒绝调用，再请求受控 tool-execute。分派入口只处理数据，不 eval 代码；验证请求工具范围、成员目录、参数、调用 ID 与控制冲突，并检查实际执行时权限。

每个叶子 Operation 由 OperationExecutor 执行并保存独立身份和回执。批量分派不创建遮蔽部分成功的非幂等父 intent；若保存派生结果，只能在叶子结果充分确定后生成。子身份由父操作确定序号和输入位置派生，provider tool-call ID 只用于协议关联；完成顺序不改变身份或返回顺序。同成员写 gate、读写调度和配额保持，不引入 DSL 并发语法。

错误分类：输入/领域拒绝可以作为结构化工具失败供模型修正；取消、截止、预算、恢复失配交由运行器处理；未知外部结果阻塞相应运行并保留证据，不由 ToolExecutor 的兜底异常转换伪装可重试失败。重试只适用于明确拒绝或宿主声明可安全重试的操作。程序错误不回滚已提交效果。

模型消息中一次完整 assistant 响应关联全部 tool_calls，随后附对应 tool 结果；普通会话库负责协议排列与校验。不要按完成一个工具就重复追加一份 assistant 响应，也不能向下一次模型请求提供缺失必要结果的非法消息。模型局部结束信号与宿主工具效果分开处理，不直接执行成员睡眠/退出。

### P3 当前实现

`tool-execute` 是 DSLAdapter 的编排端口，接受 tools 名称列表和规范化 tool_calls，返回按输入顺序排列的 `{id,name,success,data,error}`。批次不写父 intent；每个叶子都经 OperationExecutor 独立持久化，身份绑定完整批次与位置。先还原历史，再检查新效果的当前目录；撤权/定义变化阻塞，未知结果不能作为模型重试建议。

`tlon.conversation/run` 是普通模块函数，参数含 messages/tools/max_tokens/max_rounds。max_rounds 为正整数，局部返回 reason 为 complete、finish-turn 或 round-limit。`tlon.messages/response` 与 exchange 转换和校验完整消息协议。仅显式依赖时装配 conversation；无需模型的模块继续独立运行。模型工具执行只接受宿主叶子，模块函数不注册为 Tool。

### P4a 当前实现

机器起草由普通 tlon.authoring/draft 组合文件查询、模型调用、静态检查和修订，返回完整 v2 作者包；调用方最后用 prototype-write 发布。同伴审阅和 WebUI 展示全部源码与逐文件谱系 diff，近亲比较覆盖作者包，批准继续绑定原 digest。没有自动批准或热更新成员。文档和实际标准库源码只读映射到 workspace 的 tlon/docs、tlon/stdlib，使用现有文件工具查询；不新增文档服务或查询 API，也不预注入手册。

普通 tlon.workflows 实现短回合、计划执行、独立检索核验、评审修订、路由和原信转发。tlon.context 实现消息组装、窗口适配和逐完整交换的模型摘要；model-context 是宿主只读估算口，包含冻结系统和工具定义。完整组过大时拒绝；缓存前缀、草稿/已执行事实与模型账本保持。

v2 终止保存原始输入/错误/模型回执，并通过普通邮件叶子向核实的人类责任发件人投递幂等通知。通知零模型额度维护根与原根隔离；不重发原未知效果。已失败运行扫描只对账通知，不重新执行程序。交互投影由 P4b 下述只读视图接续，内置原型全面切换仍待 P5。

## 5. 状态、返回与调度

| 状态 | 权威 | 恢复方式 |
|---|---|---|
| 草稿、评审、消息列表、迭代/错误计数 | 函数局部不可变值 | 从冻结程序与效果结果重放 |
| 输入、程序包、调用、额度、终态、返回值与调度意图 | ProgramStore / 运行器 | 持久记录与身份校验 |
| 记忆、任务、联系人、邮箱与长期私有状态 | 现有领域服务 | Operation 访问；保持领域事务与调用幂等 |
| 交互展示与蒸馏阅读视图 | 权威运行记录的投影 | 可重建，不驱动新程序恢复 |

不提供任意模块访问 AgentSession 对象的桥。会话数据先使用普通值，不预建庞大的会话对象框架、blob 平台或新配置层。必要的大数据存储优化须由实际数据量证明。

普通函数返回只结束自身；对话循环返回局部结果；Behavior 入口返回结束事件处理。只有入口结果可被宿主解释为 `{:result ... :next {:kind :sleep-until :at ...}}` 等调度意图。内部函数返回相同 map 不自动触发调度。模型的 finish-turn 意图先结束所属对话函数，由外层决定是否继续、发信或调度。

运行器校验入口结果，并在同一事务提交返回值、完成状态和待应用调度意图。调度器按稳定运行 ID 应用并确认；提交完成后、应用前崩溃必须可继续，重复应用不可导致重复唤醒/退出或遗失意图。沿用成员串行、boot/邮件/idle 资格、四态与死亡约束。强制资源终止、责任邮件失败通知仍由宿主兜底，发信也使用可恢复的稳定调用身份。程序可选择礼貌回复，但不能绕过责任集合和既有通知对象约束。

## 6. 持久执行与交互历史

ProgramDriver 统一执行冻结入口，ProgramStore 是新运行恢复权威。首次受理在同一事务保存事件输入、入口、完整依赖源码/身份及运行约束。模块调用不创建新预算根；模型、工具、核验、修订与压缩共享根预算和截止。语言 fuel/depth 不因嵌套调用重置，恢复从同一预算重放计算，不额外赠送计算额度；外部消费从账本还原，不二次收费。

恢复按以下顺序进行：校验宿主构建与契约身份 → 读取原程序包/输入 → 从入口重放纯计算 → 以确定序号核对并还原既有操作结果 → 处理未提交调用或阻塞 unknown → 提交入口结果。时间、随机、外部读取不能藏进纯计算；不序列化 Python generator、函数对象或进程任务。

SessionJournal/SessionStore 为旧历史保留读取，新运行的消息和完成索引从运行权威记录构建。投影采用按需只读重建，读取失败不能触发重新生成或重发邮件；用稳定来源 ID 消费完成事实，无独立投影数据库。反思、蒸馏及其水位、thread recap、memory-read、退休归档、实际 API 和可观测性消费者都必须完成核对。客户端连接 REST `/sessions` 与 WebUI SessionsView 不属于 AgentSession，不迁移；本轮不新增运行轨迹 UI。现有历史数据不删除、不迁移；旧历史和新记录按明确来源查询，读取历史不保留旧执行引擎。

入口完成与投影发布之间故障后，消费者不得重复计完成次数或重复产生维护任务。维护任务以来源运行/批次稳定身份创建，反思和蒸馏继续使用自己的维护预算根。

## 7. 退役与非目标

最终删除 run-loop ActionRequest、SessionRecipe、stage/section 专属解释器、LoopDriver、StageOutcome、AgentSession 的执行状态及 SessionScheduler 的旧恢复循环。通用 PromptBuilder、provider 适配、预算、OperationExecutor、领域服务与交互历史读取保留或按职责拆出。

建设期只按明确入口选择新旧执行路径，无“新失败尝试旧”。最终切换先排空旧运行；无法排空的 unfinished session 明确阻塞并列出处置，不清库、不静默当作成功、不让新执行器解释旧日志。

不包含 Capability、模块函数工具化、任意事件持久等待、语言级并发、自主热切换行为或多版本宿主恢复。这些另行决策，不能借本 EPIC 扩张。

### P4b 当前实现

SessionJournal 联合旧历史与 ProgramStore 的只读派生事件，来源为 program:<run_id>。完成索引只读元数据，单个来源回读模型与工具回执、未决证据、入口结果和错误；不创建第二份恢复日志。蒸馏与反思按来源 ID 集合、固定批次和独立额度根消费，较早开始但较晚完成的运行不会被时间水位遗漏。session-read、thread recap、教训、退休归档及演武场证据导出使用同一视图；客户端连接会话不变，旧引擎仍待 P5 退役。
