# DSL 设计：行为即数据

> 后续方案（邮件 P2 与文件/Shell/时间 P3a 已分别通过 #185/#186 合并；P3b 已接入，后续仍分阶段实施）：[统一操作与可组合行为](operations.md) 已认可能力定义与调用入口分离的方向，施工见 [分阶段计划](operations-plan.md)。本页仍描述当前机制，不能将目标当作已实现。

> 统一成员模型（ADR 0006，2026-08 实施）：成员只有一种。模型总览见 [docs/design/unified-member-model.md](./unified-member-model.md)。

语言规范（EDN 语法、求值器、燃料计量、特殊形式、通用词汇）见 [../reference/tlosure-reference.md](../reference/tlosure-reference.md)；MemberSpec 全字段与领域词汇清单见 [../reference/dsl-reference.md](../reference/dsl-reference.md)；本文定义当前行为模型、信任边界和加载机制，并解释必要的取舍。选择 EDN 作为语法的决策记录见 [../decisions/0001-edn-as-dsl.md](../decisions/0001-edn-as-dsl.md)；语义演进锚定 SCI 兼容子集（为 JVM 迁移铺路）的决策见 [../decisions/0011-sci-compatible-subset.md](../decisions/0011-sci-compatible-subset.md)。

## DSL 在愿景中的位置

Tlon 的愿景是 agent 社会自进化（见 [../vision.md](../vision.md)）：不依赖人类修改代码，新的行为从社会内部生长出来。这条愿景对行为描述有一个硬约束——**进化要求基因型是可写的**。如果 agent 的行为是 Python 代码，它就永远只能由人类修改；只有当行为变成数据，agent 才能阅读、书写、传递和修改自己的行为。

成员的事件规则、循环编排和完成动作由一份 spec 描述：[agents/member_spec.py](../../src/tlon/agents/member_spec.py) 的 `MemberSpec`，落盘为每成员唯一的 `config/behavior.tlon` 文件。DSL 就是这份数据的语法。它是自进化的前提，而不是一个配置格式的偏好问题。

由此推出本文其余部分的全部设计取向：这门语言的预期作者不只是人类，还包括 LLM（[tools/agents/write_prototype.py](../../src/tlon/tools/agents/write_prototype.py) 的 `WritePrototypeTool` 让 agent 自己撰写新成员的 spec）。所以 DSL 被**故意**设计得受限——表达能力让渡给安全性，一门语言只有足够安全，才配交给 agent 自己书写。

成员的规则统一为 `:on + :when + :actions`，可选 `:fuel`。`run-loop` 是普通效果词，配方独立于规则；同一成员可以按条件调用不同配方，也可以用 `while` 完成不涉及模型的计算。`quote` 将配方表达式保留为数据。成员不再按有无会话分类，具体决定见 [ADR 0042](../decisions/0042-unified-member-rule-execution.md)。

愿景的判断标准是"人类退得越远，社会是否依然进步"。DSL 的受限程度直接决定人类能退多远：语言每安全一分，审批者就可以少看一行；反过来，语言里任何一处不受约束的能力，最终都会变成一个人类必须亲自盯住的岗位。本文后面描述的每一道限制，都可以换算成"人类往后退了一步"。

DSL 与愿景的另一支柱——书信共和国——是互补的两半。邮件让**知识**成为数据（信件持久、可检索、可在成员间流传），DSL 让**行为**成为数据（spec 可读、可写、可由 agent 撰写并传递）。前者是进化的传播通道，后者是进化的遗传物质；只有行为是数据而知识不是，改进无法积累；只有知识是数据而行为不是，积累永远停在外部，进不了基因型。spec 里 `(mail-history 20)` 这样的 section 正是两者的接缝：行为数据里声明了对知识数据的消费方式。

## 为什么故意受限

spec 的作者是 LLM，这意味着 DSL 面对的是不可信输入。把"不可信"拆开看，威胁形态是可枚举的：作者可能写出语法错误（要能在加载时报清楚、拒绝掉）；可能写出死循环或失控计算（要能保证停机且代价有界）；可能尝试触碰不该看的状态、做不该做的事（效果由宿主执行、能力要白名单化）；可能写出一意孤行的成员定义（生效前要有人类批准）。下面几条约束与这几类威胁一一对应，每条单独看都是表达能力的损失，合起来是信任模型的地基。

### fuel：停机性靠预算，不靠砍掉循环

求值是计量的：每个求值步消耗 1 单位 fuel，耗尽抛 `FuelExhaustedError`；form 嵌套深度另有硬顶（`EvalDepthError`）。常量都在 [tlosure/eval.py](../../src/tlosure/eval.py) 顶部：`:when` 门控默认 200（`DEFAULT_WHEN_FUEL`），动作默认 1000（`DEFAULT_FUEL`），spec 自声明上限 10000（`MAX_FUEL`，超过即加载拒绝；同为求值器入口的语言级硬顶——`Evaluator` 构造器对越界预算抛 `ValueError`）。两道闸各管一种失控：fuel 管时间（步数），深度顶管空间（递归嵌套）——没有深度顶，一个深度自嵌套的构造即便步数有限也可能先把宿主 Python 的调用栈打爆。

值得说明的是，这门语言**并非**靠表达能力残缺来保证停机：`while`/`recur`（Clojure 风格有界循环对）加进来之后，语言在图灵等价意义下是完备的，终止性完全由 fuel 预算保证。这是刻意的取舍——"足够弱以至于显然停机"的语言写不出真实策略（比如对一组收件人逐个 `mail-forward`）；正确的做法是让语言够用，同时让任何求值都有一个不可逾越的步数上界。无论 LLM 写出什么样的表达式，求值都必然终止，且代价可预测。

规则显式声明 `:fuel` 时，门控与全部动作共享预算；未声明时门控与各动作使用默认预算。会话配方的 `:fuel` 单独约束每次 `:until` 与 stage `:if` 求值；会话总成本另由轮次和累计 token 硬上限约束。

### 词汇表按语境切分

受限不是只有"少"，还有"各归各位"。builtin 注册表按使用语境切成互不混淆的几份：`:when` 门控用成员统一注册表的纯子集（`make_member_builtins` 里 `pure=True` 的部分）；`:until` 只能见 `LOOP_BUILTINS`（迭代/错误上限谓词）；stage `:if` 只能见 `STAGE_COND_BUILTINS`（对会话 scratchpad 的只读谓词）。这些纯语境的注册表只含纯词，所以三个语境的求值都是无副作用的**构造性**结论——不是靠约定"作者别乱写"，而是乱写的词汇根本不在注册表里。`:when` 里引用 `iteration-limit-reached?`、`:until` 里引用 `addressed-to-me?`，都在加载校验时因"未注册"被拒。

规则的门控与动作都使用 `{msg, event, self}`。非邮件事件的 `msg` 为 `nil`，邮件谓词对此安全返回；动作不再有另一套完成环境或隐式 `result`。`llm-call`、状态和邮件效果的实际返回值可以作为普通表达式值继续求值（[ADR 0043](../decisions/0043-newspaper-member-workflow.md)）。

### 函数能力与信任边界

语言支持 `defn`、`fn`、闭包、变长参数、递归和一等函数。用户函数可在任意合法位置定义，也可在动作语境组合效果词；`map`/`filter`/`reduce` 保留显式绑定形式。语言不靠禁止函数来限制消耗，而由 fuel/depth 对求值定界。

当前信任边界由四部分组成：fuel/depth 限制求值；effects-as-data 将意图与执行分开；语境注册表限制可调用词汇；staging + 人工批准控制机器产物的生效。加载校验保证结构、作用域和词汇合法，不能静态枚举所有动作，也不能证明合法程序无运行时错误。

不提供 `eval`、宏、可变全局状态或 DSL 并发。函数能力的引入及放弃语法动作分区的取舍见 [ADR 0021](../decisions/0021-tlosure-full-functions.md)。

### 效果边界：DSL 描述，宿主执行并返回结果

已切换的 Operation builtin（read/write）在异步调用边界执行共享契约并返回真实结果。尚未切换的生命周期和 llm-call builtin 仍产生 `ActionRequest` 数据。tlosure 使用同一个可暂停 generator 内核计量同步与异步求值：同步入口返回请求数据，供分析和测试；生产程序使用异步入口，在调用处等待宿主执行，再把实际结果送回表达式。权限、配额、收件人和四态接纳仍由宿主检查，DSL 无法绕过。

这是 ADR 0043 对 ADR 0042 阶段“先完整求值、后执行所有效果”的替代。函数、条件和传统循环共享同一调用机制；未选分支没有效果。后面的表达式失败不会撤回已经提交的效果，不能将整条规则理解为外部事务。

```clojure
(let [draft (llm-call {:prompt "根据给定来源编辑简报" :max-tokens 256})]
  (when (not (= (get draft :content) ""))
    (state-put {:key "edition" :value (get draft :content)})))
```

`ProgramStore` 保存成员私有状态、每次规则的原始程序与触发输入、每步效果参数和结果。恢复从原程序开始，已完成调用返回原结果，未完成调用继续执行；参数漂移报错。`state-put` 和效果结果同事务提交。模型结果成功记录后不会重复生成；上游已生成而本地还没记录时仍可能重试，不能承诺模型恰好一次。

`llm-call` 不附工具，保留成员人格、RULES 与协议；输出上限传给 provider。规则内按请求上限预留共享输出额度，重放沿用预留，不因 usage 缺失获得无限额度。`mail-send` 返回真实邮局回执，稳定请求 ID 复用邮局幂等提交；明确拒收与未知提交均不能当成已发送。报社示例在发送前冻结期刊内容与订阅者快照，重试不会重新编辑或重发已提交的同一期邮件。

环境值对表达式不透明：没有属性/方法访问语法，`Mail` 只能经白名单 builtin 检查或读取。`mail-field` 提供真实 ID、发件人、主题、正文等字段，模型不能用杜撰的编号替代原始来源。宿主动态查询在程序执行中记录结果；能力 artifact 仍只接收显式参数，不获得隐式宿主上下文。

`validate_member_actions` 使用全量成员注册表校验动作表达式，并让 `defn` 在动作 form 之间按定义顺序穿线。参数校验在构造请求时完成；运行时合法性和当前权限由效果执行侧判断，两者不能互相替代。

map 字面量的键保持字面，值可以内嵌表达式，例如 `(stop {:agent (get event :agent_id) :reason "drain"})`。语言没有 `eval` 或宏，不能把运行中生成的表达式再当程序执行。失败保留日志和未完成记录；已读状态不决定程序是否完成，未知外部提交不制造成功回执。

### 信任闸：staging + 人工批准

受限语言把风险降到了"写错行为"的级别，但写错的行为仍然是行为。所以 LLM 撰写的 spec 不直接生效：`WritePrototypeTool` 的产物经 [agents/prototypes/staging.py](../../src/tlon/agents/prototypes/staging.py) 的 `write_staging_prototype` 落入 `~/.tlon/prototypes-staging/`，元数据标记 `:trusted false`；`AgentRegistry` 在 spawn 前检查信任位，未受信原型直接拒绝。人类通过管理审批入口批准后，`approve_prototype` 把目录移入受信库并改写 `:trusted true`。

信任的默认值是不对称的：随包播种的与人类手写的原型默认受信（`:trusted true`，可直接 spawn——手写即意味着人类已经看过），只有 staging 区写出 `:trusted false`。也就是说信任位标记的不是"这份 spec 的质量"，而是"它的作者是不是机器"；审批动作把一份机器产物正式记为人类认可。`:author` 字段记录撰写者 agent 的 id，审计链完整：谁写的、谁批准的、何时进的可信库，都可追溯。

注意写入磁盘之前还有一道机器闸：`write_staging_prototype` 先跑与运行时加载完全相同的校验链（EDN 解析 → schema → 单一校验器），坏了的 spec 根本落不了盘，校验错误原文返回给 LLM 让它自行修正重试。机器管"合法"，人管"该不该存在"——两层信任闸各管一段。

## 机制/策略分离

DSL 的设计前提是看清哪一半在语言内、哪一半在语言外：

- **机制是 Python 固定实现**：工具（发邮件、读写文件、Shell、记忆、spawn）、stage（think / act / plan / critique / revise）、section（persona / protocol / knowledge / skills / current-task / thread-recap / mail-history）、求值器本身、事件总线。这些由引擎提供，有单元测试，不随 spec 变化。
- **策略是 spec 数据**：`:on` 和 `:when` 决定触发，`:actions` 组合普通计算与效果；`run-loop` 配方声明上下文、阶段和终止条件。Adam 同样用内置规则表达启动、空闲卸载和噪声治理，权限来自注入的宿主端口。

由此得到 DSL 表达能力的天花板：**组合已有机制**。spec 可以决定 act 之前先 plan、critique 失败才 revise、只对 To 里点了自己的邮件启动会话；但 spec 不能创造新的 stage、section 或宿主效果原语。受信复合能力可以新增 DSL 词，但只能组合已有原语，见 [capabilities](capabilities.md)。这个天花板是特性而非缺陷：机制是可信的（人类写的、有测试的），策略是可进化的（LLM 写的、受限的），边界正好落在信任边界上。

机制/策略的界线在会话循环内部同样清晰。`:stages` 向量决定跑哪些 stage、以什么顺序、各自带什么条件（`:if`），但每个 stage 的语义——`think` 是一次 LLM 调用、`act` 是执行本轮全部工具调用、`critique` 失败如何软降级——是 [agents/stages/](../../src/tlon/agents/stages) 下的 Python 实现。spec 里的 `act` 只声明"执行这轮的工具分发列表"，每个工具一个后台 task 的并发执行是引擎的事。更关键的是引擎保留了 spec 无法覆盖的否决权：`:max-iterations` 与 `:max-tokens` 是双停机线（ADR 0023），任一到顶、在任何 `:until` 求值之前无条件生效——spec 可以声明终止条件，但声明不出"永不终止"。策略可以自由，自由有边。

所有已订阅事件都使用同一种执行机制，包括非邮件事件。是否请求模型由普通程序决定，不以事件名称或成员类别作特殊分派；外部通信仍由邮件层负责。

生命周期管理也是同一个划法的示范：wake-on-mail 不是策略而是机制——注册表从 spec 派生唤醒资格（声明了 `:on :mail-delivered` 的睡眠收件人会被唤醒），唤醒动作本身是幂等的机制执行，任何 spec 都不需要写"收到邮件时叫醒我"这样的规则；反过来，空闲检测也是机制（注册表只发布 `AGENT_IDLE` 这个事实），而"空闲了要不要停"是策略——Adam 的 spec 里那条 `:on :agent-idle` 规则。事实由机制生产，后果由 spec 决定，这条线让整个社会的启停行为也可进化：换一种排空策略只是改 Adam 的 spec 文本，不动一行引擎代码。

## total 语义：加载失败即整体拒绝

spec 的加载是全有或全无的：缺必填字段是错误而不是默认值，类型错误同样拒绝（`bool` 永不被接受为 `int`），任何失败——文件缺失、EDN 非法、校验不过——都使该成员拒绝启动。入口是 [agents/loader.py](../../src/tlon/agents/loader.py) 的 `load_member_spec`，结构校验在 `MemberSpec.from_dict`（`from_edn` 只做 kebab-case 关键字到 snake_case 字段名的归一化后委托给它），builtin 作用域校验在单一校验器 `validate_member_spec`。三类失败各有专属异常——`FileNotFoundError`（文件缺失；其他 I/O 错误按原异常上报）、`EDNParseError`（文本非法，带行列号）、`MemberSpecValidationError`（结构或作用域校验不过）——异常类型本身就是失败发生在哪一层的信号，调用方（注册表）不需要解析错误消息就能分类记日志。

为什么不做"尽力加载、能跑多少跑多少"：spec 是行为的**唯一权威**（见下节），一份半残的 spec 意味着一个行为不可预测的成员——它可能收不到邮件、可能永不停机、可能发出不该发的信。对一个人类会长期缺席的系统，"拒绝启动、原地报错"比"带病运行"安全得多。注册表隔离失败：一个成员的 spec 坏了只影响它自己，server 与其他成员不受影响。

报错设计也承认读者可能是机器：校验失败的错误消息原文返回给撰写者（`WritePrototypeTool` 把 `PrototypeError` 连同格式提示一起塞回工具结果），LLM 据此自行修正重试。所以错误消息要求自解释——带字段路径、带期望值、带实际值——这不是日志美学，而是"agent 能自己修好自己写的 spec"这个闭环的必要条件。错误信息是 API 的一部分。

当前解析器对未知键会记 warning 后忽略（`_warn_unknown_keys`）；这描述现有校验行为，不构成维护旧格式、兼容层或迁移程序的要求。缺失必填项和错误值仍拒绝加载。

total 还体现在配方与预算的校验上，都在加载时拒绝而非运行时兜底：

- **会话配方独立校验**：同一成员可以在不同规则和事件里请求不同配方。并发约束是每个成员串行执行程序，不是限制成员只能拥有一个配方。
- **fuel 声明在加载时定界**：规则与会话配方的 fuel 必须为正且不超过 `MAX_FUEL`；语法数据中的阶段、section 与终止条件仍按各自词汇表校验。

加载时还有一份不参与语义只参与审计的产物：`load_member_spec` 对原始文件字节算 sha256 短哈希挂到 `MemberSpec.source_hash`，随可观测性事件上报——这份 spec 是从哪个文件来的有据可查，但哈希永远不写回 behavior 文件（provenance 不是 semantics）。

## spec 的权威范围与配置边界

spec 是成员规则的权威来源；boot、lazy、wake 资格从规则派生，不另存一份独立的配置。模型、人格、社会政策和运行时执行授权分别有自己的来源，不能由“spec 是唯一权威”推导为所有成员状态都由 spec 决定：

- **boot 集合**：`AgentRegistry.start_all_candidates`——spec 声明了 `:on :boot` 的成员。
- **lazy 资格**：`AgentRegistry.is_lazy`——spec 没有 `:on :boot` 规则（或 spec 不可读）的成员可以被排空。
- **wake 资格**：`AgentRegistry._on_mail_delivered`——睡眠收件人的 spec 声明了 `:on :mail-delivered` 才发唤醒请求。

不存储的原因是消除双源真相：如果 boot 集合存了一份缓存，spec 改动后就要考虑失效与迁移，而 spec 文件本身就是那盘——改文件即改行为，热重载（`POST /admin/config/agents/{agent_id}/reload`）只是重新走一遍加载链。派生也让原型库自然继承同一语义：`prototype.tlon` 元数据里永远没有 `:runtime` 字段。

唯一权威还有两层推论：

- **实例化生成独立配置，不是引用**。[agents/prototypes/instantiate.py](../../src/tlon/agents/prototypes/instantiate.py) 的 `instantiate` 把原型 behavior（带参数时先渲染）写成新成员自己的配置，行为冻结在创建时刻；之后原型的修改不影响已出生的成员，成员的 spec 被改也不回流原型。原型不带人格（ADR 0006）——成员的人格文件在创建时播种，之后 IDENTITY/SOUL 是成员自主数据（`persona-update` 自改，ADR 0024）。spec 是 total 的，所以不存在"继承 + 覆盖"的合并语义——没有可合并的东西，拷贝就是完整答案。模型不是基因型（ADR 0016——机制/策略分离，模型选择是部署/资源策略）：成员的 `llm.model_name` 来自 `instantiate` 的必填 `model` 参数（spawn 时继承 spawner 的模型）；spec 语法已删除顶层 `:llm` 键，残留即加载报错；未声明模型的成员装配期硬报错，没有进程级兜底。执行能力声明则相反——它是基因型：spec 的可选顶层段 `:execution {:level :L0|:L1|:L2}` 随原型提案一起评审，`instantiate` 把它一次性投影进 agent.yaml 的 `docker.level`，此后运行时权威是 agent.yaml——成员自改自己 spec 的 `:execution` 不生效，防自提权。
- **模板只存在于写侧**。behavior 文件可以声明顶层 `:parameters` 并用 `${name}` 占位（[agents/behavior_template.py](../../src/tlon/agents/behavior_template.py)），但渲染发生在实例化落盘之前；运行时加载器永远见不到占位符，`:parameters` 本身也只作为未知键记 warning。运行时面对的是具体规则，不在读取时重新套模板；其词汇仍依赖显式注入的受信注册表。

## 分层

DSL 的实现分五层，各层职责单一、失败模式明确。语言核心（解析、求值/校验、通用 builtin 词汇）住在独立包 [tlosure](../../src/tlosure)（ADR 0018），不依赖 tlon 的任何层；tlon 的领域词汇（mail/loop/stage 谓词、动作族、宿主端口）住在 [agents/vocabulary.py](../../src/tlon/agents/vocabulary.py)（ADR 0018 修订），语义模型与加载校验留在 `tlon.agents`，依赖方向 tlon → tlosure：

| 层 | 位置 | 职责 | 失败即 |
|---|---|---|---|
| EDN 解析 | [tlosure/edn.py](../../src/tlosure/edn.py) 的 `loads` / `dumps` | 文本 → 纯 Python 数据，不求值 | `EDNParseError`（带行列号） |
| 语义模型 | [agents/member_spec.py](../../src/tlon/agents/member_spec.py) 的 `MemberSpec` / `MemberRule` / `SessionRecipe` | 语义权威：typed frozen dataclass 树 + 结构校验 | `MemberSpecValidationError` |
| 受限求值器 | [tlosure/eval.py](../../src/tlosure/eval.py) 的 `eval_form` 与 [tlosure/validate.py](../../src/tlosure/validate.py) 的 `validate_form` | fuel 计量求值与静态校验；已切换操作返回宿主结果；剩余动作产出 `ActionRequest` | `EvalError` 家族 |
| 领域词汇 | [agents/vocabulary.py](../../src/tlon/agents/vocabulary.py) 的四个纯注册表 + `make_member_builtins` + `validate_member_actions` | tlon 的 DSL 词汇表：mail/loop/stage 谓词、动作族、`ScriptHostQueries` 宿主端口；通用词汇与契约类型来自 tlosure | 未注册词汇在加载校验被拒 |
| 加载校验 | [agents/loader.py](../../src/tlon/agents/loader.py) 的 `load_member_spec` | 唯一读盘路径：解析 → 模型 → 校验 → 挂 provenance | 成员拒绝启动（注册表隔离） |

解析不求值这一点值得单独说：解析的产物是惰性、安全、可流转的数据。正因为如此，模板分析（[agents/behavior_template.py](../../src/tlon/agents/behavior_template.py) 的 `analyze` 在文本/数据层提取 `:parameters`）、`source_hash` 对原文字节做哈希、原型元数据读取失败时安全回退（`_read_metadata` 对一个坏掉的 `prototype.tlon` 只记 warning 返回名字兜底）都可以在完全不触碰求值器的前提下工作。求值只在宿主显式发起时发生——数据在系统里流动的每一站都不承担"执行"的风险。

三条边界值得强调：

- **解析层把语义区分固化进类型**。[tlosure/edn.py](../../src/tlosure/edn.py) 产出的不只是"数据"，而是带类型的数据：`Keyword`、`Symbol`、`Form` 分别是 str / str / list 的子类。`(...)` 是代码（Form，交给求值器）、`[...]` 是数据（普通 list）在解析完成时就是两个 Python 类型，`:mode :react` 与 `:mode "react"` 下游始终可区分。下游所有"这是表达式还是字面量"的判断都是 `isinstance`，没有任何约定俗成的二次解析。这正是 ADR 0001 选 EDN 的理由在代码里的落点。
- **EDN 只是语法，dataclass 才是语义**。`MemberSpec` 的模块 docstring 写明："EDN is only syntax, the dataclass is the semantics authority"。序列化前端可以换（`to_dict` 已经提供 snake_case dict 形态），语义模型不动。归一化（`_normalize_data`）只把 map 的 kebab-case 关键字键翻成 snake_case 字段名，对 Form 内部原样放行——表达式里的关键字是语言词汇（如 `:recipient`），不是配置键，不能被改名。
- **结构校验与作用域校验分离**。EDN 前端只查类型与形状（这个字段是不是 form、是不是非负 int）；builtin 白名单与符号作用域的校验全部集中在 `validate_member_spec` 一处，由 loader 调用而 `from_dict` 不调用——这样宿主相关的序列化 roundtrip（比如 Adam 的 spec 导出再导入）不会被无宿主的校验器误杀。校验规则只有一个家，散落即腐烂。

`dumps` 的存在也不只是为了测试 roundtrip：agent 自己书写 spec（writePrototype 修正重试、`to_edn` 序列化）依赖同一套读写对称性。

L1 内部还有一个刻意的紧邻：运行时求值（`eval_form`）与加载时静态校验（`validate_form`）住在 tlosure 包内相邻的两个模块（`eval.py` 求值、`validate.py` 校验）、共享对表达式树的同一套结构理解（特殊形式的形状、尾位置规则、绑定向量格式在两处是 mirrored 实现）。这保证了"校验器承认的语言"与"求值器执行的语言"不会随版本漂移而裂开——如果校验在别处另起炉灶，两边对 `(recur ...)` 合法位置的理解迟早出现缝隙，而静态校验的全部价值就在于它证明的东西在运行时成立。

loader 是运行时加载成员 behavior 的统一读盘入口，这点也有设计意图：热重载、首次启动、测试中的临时 spec，走的都是同一条"解析 → 模型 → 校验 → 挂 provenance"的链，不存在第二条绕过校验的加载方式。链条唯一意味着校验策略的改动（比如新增一条结构约束）自动对所有加载场景生效，不需要逐个调用点追缴。

## 复合能力与历史来源

当前已有两条机器提案通道：新成员行为经原型 staging 与批准后实例化；复合能力经能力 staging 与批准后成为受信词。两者都只能组合已有宿主机制。新原语（LLM 调用、新 IO、Docker、异步）仍由人类修改 Python 并发版，因为 fuel 限制的是语言求值步数，不能替代外部调用的成本和权限控制。

复合能力的 artifact、零注入环境、纯度准入与调用方预算见 [capabilities](capabilities.md)。桥的查询、效果和环境契约见 [bridge-api](bridge-api.md)。

历史取舍见 [EDN 语法](../decisions/0001-edn-as-dsl.md)、[SCI 兼容子集方向](../decisions/0011-sci-compatible-subset.md)、[tlosure 独立](../decisions/0018-dsl-extraction-tlosure.md) 与 [完整函数能力](../decisions/0021-tlosure-full-functions.md)。SCI 是语言演进方向，不代表系统已经迁移到 JVM。
