# DSL 设计：行为即数据

> 统一成员模型（ADR 0006，2026-08 实施）：成员只有一种，react/script 是 spec 派生观测、resident/contractor 类别已删除；DSL 语言本身的受限设计（本文主体）不受影响。模型总览见 `docs/design/unified-member-model.md`。

语法、全字段与 builtin 清单见 [../reference/dsl-reference.md](../reference/dsl-reference.md)；本文只回答"为什么这样设计"。选择 EDN 作为语法的决策记录见 [../decisions/0001-edn-as-dsl.md](../decisions/0001-edn-as-dsl.md)。一切以代码为准。

## DSL 在愿景中的位置

Tlon 的愿景是 agent 社会自进化（见 [../vision.md](../vision.md)）：不依赖人类修改代码，新的行为从社会内部生长出来。这条愿景对行为描述有一个硬约束——**进化要求基因型是可写的**。如果 agent 的行为是 Python 代码，它就永远只能由人类修改；只有当行为变成数据，agent 才能阅读、书写、传递和修改自己的行为。

因此每个成员的行为完全由一份 spec 描述：`agents/member_spec.py` 的 `MemberSpec`，落盘为每成员唯一的 `config/behavior.tlon` 文件。DSL 就是这份数据的语法。它是自进化的前提，而不是一个配置格式的偏好问题。

由此推出本文其余部分的全部设计取向：这门语言的预期作者不只是人类，还包括 LLM（`tools/agents/write_prototype.py` 的 `WritePrototypeTool` 让 agent 自己撰写新成员的 spec）。所以 DSL 被**故意**设计得受限——表达能力让渡给安全性，一门语言只有足够安全，才配交给 agent 自己书写。

一个补充事实：spec 只有一种。历史上认知型 agent（`AgentSpec`）与确定型脚本成员（`ScriptSpec`）各有自己的 spec 格式与运行时，合并后的 `MemberSpec` 用统一规则形态 `规则 = :on + :when + 可选 :loop + :actions` 同时覆盖两者——有 `:loop` 即认知规则，无即确定规则——由统一解释器 `agents/member_runtime.py` 的 `MemberRuntime` 执行。统一的理由同样是面向作者的：LLM 只需学会一种格式，`writePrototype` 的 `spec_type` 参数只选择模板（认知型附带人格文件，确定型是无人格的 daemon），输出永远是同一个 rules-form 的 `behavior.tlon`。格式每多一种，作者要学的、校验器要管的、文档要讲的都多一份。

愿景的判断标准是"人类退得越远，社会是否依然进步"。DSL 的受限程度直接决定人类能退多远：语言每安全一分，审批者就可以少看一行；反过来，语言里任何一处不受约束的能力，最终都会变成一个人类必须亲自盯住的岗位。本文后面描述的每一道限制，都可以换算成"人类往后退了一步"。

DSL 与愿景的另一支柱——书信共和国——是互补的两半。邮件让**知识**成为数据（信件持久、可检索、可在成员间流传），DSL 让**行为**成为数据（spec 可读、可写、可由 agent 撰写并传递）。前者是进化的传播通道，后者是进化的遗传物质；只有行为是数据而知识不是，改进无法积累；只有知识是数据而行为不是，积累永远停在外部，进不了基因型。spec 里 `(mail-history 20)` 这样的 section 正是两者的接缝：行为数据里声明了对知识数据的消费方式。

## 为什么故意受限

spec 的作者是 LLM，这意味着 DSL 面对的是不可信输入。把"不可信"拆开看，威胁形态是可枚举的：作者可能写出语法错误（要能在加载时报清楚、拒绝掉）；可能写出死循环或失控计算（要能保证停机且代价有界）；可能尝试触碰不该看的状态、做不该做的事（求值要无副作用、能力要白名单化）；可能写出一意孤行的成员定义（生效前要有人类批准）。下面几条约束与这几类威胁一一对应，每条单独看都是表达能力的损失，合起来是信任模型的地基。

### fuel：停机性靠预算，不靠砍掉循环

求值是计量的：每个求值步消耗 1 单位 fuel，耗尽抛 `FuelExhaustedError`；form 嵌套深度另有硬顶（`EvalDepthError`）。常量都在 `agents/eval.py` 顶部：`:when` 门控默认 200（`DEFAULT_WHEN_FUEL`），动作默认 1000（`DEFAULT_FUEL`），spec 自声明上限 10000（`MAX_FUEL`，超过即加载拒绝）。两道闸各管一种失控：fuel 管时间（步数），深度顶管空间（递归嵌套）——没有深度顶，一个深度自嵌套的构造即便步数有限也可能先把宿主 Python 的调用栈打爆。

值得说明的是，这门语言**并非**靠表达能力残缺来保证停机：`while`/`recur`（Clojure 风格有界循环对）加进来之后，语言在图灵等价意义下是完备的，终止性完全由 fuel 预算保证。这是刻意的取舍——"足够弱以至于显然停机"的语言写不出真实策略（比如对一组收件人逐个 `forward`）；正确的做法是让语言够用，同时让任何求值都有一个不可逾越的步数上界。无论 LLM 写出什么样的表达式，求值都必然终止，且代价可预测。

fuel 的分摊方式也跟规则形态对齐：确定规则的 `:when` 门控与全部 `:actions` 共享一个计数器（它们是同一次事件处理里的连续计算）；认知规则的门控与会话完成后的 `:actions` 在时间上分离，各自独立计一份预算；`:loop/:fuel` 单独约束 `:until` 与 stage `:if` 的求值。预算按"一次不可中断的求值片段"划分，而不是按字段死板分配——spec 作者声明的 `:fuel` 语义因此是直观的：这条规则每次被触发最多花这么多步。

### 词汇表按语境切分

受限不是只有"少"，还有"各归各位"。builtin 注册表按使用语境切成互不混淆的几份：`:when` 门控与动作参数用成员统一注册表的纯子集（`make_member_builtins` 里 `pure=True` 的部分）；`:until` 只能见 `LOOP_BUILTINS`（迭代/错误上限谓词）；stage `:if` 只能见 `STAGE_COND_BUILTINS`（对会话 scratchpad 的只读谓词）。每份注册表只含纯函数，所以三个语境的求值都是无副作用的**构造性**结论——不是靠约定"作者别乱写"，而是乱写的词汇根本不在注册表里。`:when` 里引用 `iteration-limit-reached?`、`:until` 里引用 `addressed-to-me?`，都在加载校验时因"未注册"被拒。

环境同理：表达式能看到的只有显式绑定的几个符号（门控是 `{msg, event, self}`，完成动作多一个 `result`），非邮件事件的 `msg` 绑定为 `nil` 且所有邮件相关 builtin 对此 nil-safe。事件负载来自总线、形状不完全可控，所以 builtin 对畸形输入的默认行为是安全返回（缺键得 `false`/`nil`），而不是抛错——抛错留给真正的 spec bug（如类型用错），运行时噪音因此有区分度。

### 无定义能力：没有 fn / def / 宏

特殊形式是封闭白名单（`agents/eval.py` 的 `SPECIAL_FORMS`：`if` / `when` / `let` / `and` / `or` / `while` / `recur` / `map` / `filter` / `reduce`），没有 `def`、没有 `lambda`/`fn`、没有宏、没有可变全局状态。无法命名函数意味着递归在语法上不可能，`recur` 只能跳回最近一层 `while` 的循环头——控制流的所有形态在加载时就是可枚举的，这正是静态校验（下文的 total 语义）可行的原因。代价是 spec 作者不能抽象自己的可复用片段，只能重复书写；对 LLM 作者而言这是可接受的代价，重复恰恰是 LLM 擅长的。

没有 lambda 不等于没有高阶遍历：`map` / `filter` / `reduce` 用显式绑定元素变量（`(map x body coll)`）代替了函数参数——逐元素处理的表达力保住了，但"函数值"这个概念不存在于语言里。这堵死了一整类复杂性：没有闭包就没有捕获环境，没有一等函数就没有把行为当参数传来传去的间接层，表达式树里有什么就是什么。对一门要求"加载时看清全部行为"的语言，函数值的缺席是静态可分析性的重要来源。

### 无副作用：DSL 描述，引擎执行

求值永不产生副作用。动作 builtin（`spawn` / `emit` / `start` / `stop` / `start-all` / `forward`，`pure=False`）只校验参数并产出 `ActionRequest` 数据，由引擎在求值结束后执行——DSL 说"想做什么"，做不做、怎么做是引擎的事。这个边界带来两个直接收益：

- spec 作者无法绕过引擎的配额与权限检查。`spawn` 请求仍然要过注册表的 spawn 预算与信任闸（见下），spec 文本本身够不到任何系统资源。
- 环境值对表达式不透明：没有属性/方法访问语法，`Mail` 对象只能经 builtin 检查（`addressed-to-me?`、`content-matches?` 等）。能触碰什么数据，由 builtin 注册表决定，而不是由对象自身的能力决定。

宿主谓词（`sleeping?` / `running?` / `lazy?`）读取注册表状态，因此永不注册在模块级，只由 `make_member_builtins` 的调用方闭包注入——它们是 Adam 的词汇表，普通成员的 spec 引用即在加载校验时被拒。

请求从求值器到引擎有两条路径，按规则形态分开：确定规则取每个动作 form 的求值**返回值**（`(spawn ...)` 求值即其请求，`start-all` 求值为请求列表）；认知规则的完成动作（on-complete）允许 `(when cond (spawn ...) (emit ...))` 这样的多动作体，而 `when` 只求值出最后一个 body 值，所以这条路径给注册表附一个 collector，动作 builtin 在纯求值过程中把每个请求追加进去。两条路径共用同一组 builtin 实现（`_record` 同时追加并返回）。时序上的关键点不变：无论哪条路径，请求的执行都发生在整个求值结束之后——表达式永远观察不到自己意图的执行结果，求值因此是一个可静态校验、可重放的纯函数。

参数校验同样前置在纯求值期完成：动作 builtin 在产出请求前就把参数查完（`spawn` 的 prototype 必须是字符串、`start`/`stop` 的 `:reason` 必须非空、`forward` 的 `:to` 必须是非空地址向量），引擎执行侧拿到的 `ActionRequest` 是已验证的数据，执行路径因此不必重复防御。校验集中在描述侧、执行保持简单——这是"DSL 描述，引擎执行"的另一半：描述侧把错都挑完，执行侧只管照做。

纯度约束还细化到了语法位置（`agents/eval.py` 的 `validate_member_action`）：动作位置允许的包装形式只有 `when` / `while` / `map`，`if` / `let` / `filter` / `reduce` 不能出现在动作位置；动作的全部参数必须是纯表达式。也就是说"计算"与"产生意图"在语法上是分区的——一个 spec 里哪里可能有动作意图，校验器一眼就能数清，不存在藏在深层计算里的副作用声明。

数据与计算之间留了一座受控的桥：map 字面量的键保持字面（实际中都是关键字），值会被求值。于是动作的选项 map 可以内嵌表达式——`(stop {:agent (get event :agent_id) :reason "drain"})` 里目标成员 id 取自触发事件。桥是单向的：数据位置可以引入计算，计算的结果再回流成请求数据；反向（表达式产出新的表达式再执行）不存在，语言没有 `eval`。这条"无自反求值"的禁令配合无宏，保证了 spec 文本就是行为的全部——不存在运行时生成的、加载时看不见的行为。

与加载期拒绝互补的是运行期的 fail-safe 降级：加载把非法 spec 挡在门外，而对合法 spec 的运行时意外，系统的默认动作一律偏向"不做"。`:when` 求值失败（含 fuel 耗尽）按不命中处理、邮件标记已读；动作求值失败跳过该条规则，不影响其他规则与总线分发。原则是一致的：不可信作者制造的任何意外，结果都是沉默而不是误动。

### 信任闸：staging + 人工批准

受限语言把风险降到了"写错行为"的级别，但写错的行为仍然是行为。所以 LLM 撰写的 spec 不直接生效：`WritePrototypeTool` 的产物经 `agents/prototypes.py` 的 `write_staging_prototype` 落入 `~/.tlon/prototypes-staging/`，元数据标记 `:trusted false`；`AgentRegistry` 在 spawn 前检查信任位，未受信原型直接拒绝。人类在 ops 的 Agents 视图（`s` 审批）中批准后，`approve_prototype` 把目录移入受信库并改写 `:trusted true`。

信任的默认值是不对称的：随包播种的与人类手写的原型默认受信（`:trusted true`，可直接 spawn——手写即意味着人类已经看过），只有 staging 区写出 `:trusted false`。也就是说信任位标记的不是"这份 spec 的质量"，而是"它的作者是不是机器"；审批动作把一份机器产物正式记为人类认可。`:author` 字段记录撰写者 agent 的 id，审计链完整：谁写的、谁批准的、何时进的可信库，都可追溯。

注意写入磁盘之前还有一道机器闸：`write_staging_prototype` 先跑与运行时加载完全相同的校验链（EDN 解析 → schema → 单一校验器），坏了的 spec 根本落不了盘，校验错误原文返回给 LLM 让它自行修正重试。机器管"合法"，人管"该不该存在"——两层信任闸各管一段。

## 机制/策略分离

DSL 的设计前提是看清哪一半在语言内、哪一半在语言外：

- **机制是 Python 固定实现**：工具（发邮件、读写文件、Shell、记忆、spawn）、stage（think / act / plan / critique / revise）、section（persona / protocol / knowledge / mail-history）、求值器本身、事件总线。这些由引擎提供，有单元测试，不随 spec 变化。
- **策略是 spec 数据**：何时醒（`:on` + `:when`）、如何思考（`:loop` 的 stage 编排、上下文组装、`:until` 终止条件）、何时完成、完成后做什么（`:actions`）。Adam 是最纯粹的例子：作为 agent 世界的 init 进程，它的全部策略就是 `agents/adam.py` 里一段内置 spec 文本（`ADAM_SPEC_EDN`），boot 时 `start-all`、空闲时 `stop`，机制上一个普通的 `MemberRuntime` 实例。这个例子的分量在于：系统里权限最高的成员（能启停其他所有成员）恰恰是用最受限的方式写成的——它的权力不来自语言能力的豁免，只来自注入给它的宿主谓词与动作。权限是注入的，不是语法授予的。

由此得到 DSL 表达能力的天花板：**组合已有机制**。spec 可以决定 act 之前先 plan、critique 失败才 revise、只对 To 里点了自己的邮件启动会话；但 spec 发明不出第六个 stage、第五种 section、第七个动作。这个天花板是特性而非缺陷：机制是可信的（人类写的、有测试的），策略是可进化的（LLM 写的、受限的），边界正好落在信任边界上。

机制/策略的界线在认知循环内部同样清晰。`:stages` 向量决定跑哪些 stage、以什么顺序、各自带什么条件（`:if`），但每个 stage 的语义——`think` 是一次 LLM 调用、`act` 是执行本轮全部工具调用、`critique` 失败如何软降级——是 `agents/stages/` 下的 Python 实现。spec 里的 `act` 只声明"执行这轮的工具分发列表"，每个工具一个后台 task 的并发执行是引擎的事。更关键的是引擎保留了 spec 无法覆盖的否决权：`:max-iterations` 是迭代硬顶，在任何 `:until` 求值之前无条件生效——spec 可以声明终止条件，但声明不出"永不终止"。策略可以自由，自由有边。

触发面也在机制侧收敛：认知规则（带 `:loop`）只在 `:mail-delivered` 上生效，订阅其他事件的认知规则在运行时记 warning 并跳过（`MemberRuntime`）。spec 的 `:on` 词汇表虽然覆盖全部 `EventType`（确定规则如 Adam 确实需要感知生命周期事件），但"启动一次昂贵的认知会话"的入口只有邮件一个。这与愿景的书信共和国一致：邮件是社会唯一的公共通道，也是唯一值得动用 LLM 思考的刺激源；可观测性事件可以有确定规则盯着，但不该触发认知。

生命周期管理也是同一个划法的示范：wake-on-mail 不是策略而是机制——注册表从 spec 派生唤醒资格（声明了 `:on :mail-delivered` 的睡眠收件人会被唤醒），唤醒动作本身是幂等的机制执行，任何 spec 都不需要写"收到邮件时叫醒我"这样的规则；反过来，空闲检测也是机制（注册表只发布 `AGENT_IDLE` 这个事实），而"空闲了要不要停"是策略——Adam 的 spec 里那条 `:on :agent-idle` 规则。事实由机制生产，后果由 spec 决定，这条线让整个社会的启停行为也可进化：换一种排空策略只是改 Adam 的 spec 文本，不动一行引擎代码。

## total 语义：加载失败即整体拒绝

spec 的加载是全有或全无的：缺必填字段是错误而不是默认值，类型错误同样拒绝（`bool` 永不被接受为 `int`），任何失败——文件缺失、EDN 非法、校验不过——都使该成员拒绝启动。入口是 `agents/loader.py` 的 `load_member_spec`，结构校验在 `MemberSpec.from_dict`（`from_edn` 只做 kebab-case 关键字到 snake_case 字段名的归一化后委托给它），builtin 作用域校验在单一校验器 `validate_member_spec`。三类失败各有专属异常——`FileNotFoundError`（文件缺失或不可读）、`EDNParseError`（文本非法，带行列号）、`MemberSpecValidationError`（结构或作用域校验不过）——异常类型本身就是失败发生在哪一层的信号，调用方（注册表）不需要解析错误消息就能分类记日志。

为什么不做"尽力加载、能跑多少跑多少"：spec 是行为的**唯一权威**（见下节），一份半残的 spec 意味着一个行为不可预测的成员——它可能收不到邮件、可能永不停机、可能发出不该发的信。对一个人类会长期缺席的系统，"拒绝启动、原地报错"比"带病运行"安全得多。注册表隔离失败：一个成员的 spec 坏了只影响它自己，server 与其他成员不受影响。

报错设计也承认读者可能是机器：校验失败的错误消息原文返回给撰写者（`WritePrototypeTool` 把 `PrototypeError` 连同格式提示一起塞回工具结果），LLM 据此自行修正重试。所以错误消息要求自解释——带字段路径、带期望值、带实际值——这不是日志美学，而是"agent 能自己修好自己写的 spec"这个闭环的必要条件。错误信息是 API 的一部分。

唯一的宽容是前向兼容：未知键记 warning 后忽略（`_warn_unknown_keys`），让旧版本引擎能读新版本写出的文件。宽容只给"多出来的"，不给"缺的"和"错的"。

total 还体现在两条结构性约束上，都在加载时拒绝而非运行时兜底：

- **一个成员至多一条认知规则**（`validate_member_spec`）：认知引擎绑定单条规则的 loop——stage 编排、`:until`、反思设置都是成员级的单例配置，两条认知规则意味着两套循环语义无法调和，与其定义优先级仲裁不如直接禁止。认知规则之外可以混排若干确定规则（cost routing：便宜路径先用确定规则挡掉，认知规则的 `:when` 用 `(not ...)` 排除已处理的），这是 spec 表达能力内的组合，不是第二条认知规则。
- **fuel 声明在加载时定界**：规则级 `:fuel` 与 loop 级 `:loop/:fuel` 必须为正且不超过引擎硬顶 `MAX_FUEL`——spec 可以在预算内申请更多燃料，但申请不了无限燃料。自声明预算的意义是让"这条规则值得更多计算"成为显式的、可审查的 spec 内容，而不是隐藏在表达式复杂度里。

加载时还有一份不参与语义只参与审计的产物：`load_member_spec` 对原始文件字节算 sha256 短哈希挂到 `MemberSpec.source_hash`，随可观测性事件上报——这份 spec 是从哪个文件来的有据可查，但哈希永远不写回 behavior 文件（provenance 不是 semantics）。

## spec 是唯一权威：一切派生，无处存储

spec 不只是"行为的描述"，它是行为事实的唯一来源。系统里凡是可以从 spec 算出来的东西，一律现算、从不落盘：

- **成员类型**：`member_spec.py` 的 `member_type`——任一规则带 `:loop` 即认知成员（react），否则确定成员（script）。认知/确定的分叉（装配完整引擎还是只装事件外壳）由这个派生值驱动。
- **boot 集合**：`AgentRegistry.start_all_candidates`——spec 声明了 `:on :boot` 的成员。
- **lazy 资格**：`AgentRegistry.is_lazy`——spec 没有 `:on :boot` 规则（或 spec 不可读）的成员可以被排空。
- **wake 资格**：`AgentRegistry._on_mail_delivered`——睡眠收件人的 spec 声明了 `:on :mail-delivered` 才发唤醒请求。

不存储的原因是消除双源真相：如果 boot 集合存了一份缓存，spec 改动后就要考虑失效与迁移，而 spec 文件本身就是那盘——改文件即改行为，热重载（`POST /admin/config/agents/{agent_id}/reload`）只是重新走一遍加载链。派生也让原型库自然继承同一语义：`agents/prototypes.py` 的 `_derive_runtime` 用同一个 `member_type` 从原型的 behavior 文件派生类型，`prototype.edn` 元数据里永远没有 `:runtime` 字段。

唯一权威还有两层推论：

- **实例化是复制，不是引用**。`agents/prototypes.py` 的 `instantiate` 把原型的 behavior 文件原样拷贝成新成员自己的配置，行为冻结在创建时刻；之后原型的修改不影响已出生的成员，成员的 spec 被改也不回流原型。原型不带人格（ADR 0006）——成员的人格文件是创建时数据。spec 是 total 的，所以不存在"继承 + 覆盖"的合并语义——没有可合并的东西，拷贝就是完整答案。
- **模板只存在于写侧**。behavior 文件可以声明顶层 `:parameters` 并用 `${name}` 占位（`agents/behavior_template.py`），但渲染发生在实例化落盘之前；运行时加载器永远见不到占位符，`:parameters` 本身也只作为未知键记 warning。运行时面对的永远是一份完全的、具体的 spec——"这份文件就是这个成员的全部行为"这个命题，不需要任何上下文就成立。

## 分层

DSL 的实现分四层，各层职责单一、失败模式明确：

| 层 | 位置 | 职责 | 失败即 |
|---|---|---|---|
| EDN 解析（L0） | `agents/edn.py` 的 `loads` / `dumps` | 文本 → 纯 Python 数据，不求值 | `EDNParseError`（带行列号） |
| 语义模型 | `agents/member_spec.py` 的 `MemberSpec` / `MemberRule` / `MemberLoop` | 语义权威：typed frozen dataclass 树 + 结构校验 | `MemberSpecValidationError` |
| 受限求值器（L1） | `agents/eval.py` 的 `eval_form` / `validate_form` | fuel 计量求值与静态校验；动作产出 `ActionRequest` | `EvalError` 家族 |
| 加载校验 | `agents/loader.py` 的 `load_member_spec` | 唯一读盘路径：解析 → 模型 → 校验 → 挂 provenance | 成员拒绝启动（注册表隔离） |

L0 不求值这一点值得单独说：解析的产物是惰性、安全、可流转的数据。正因为如此，模板分析（`agents/behavior_template.py` 的 `analyze` 在文本/数据层提取 `:parameters`）、`source_hash` 对原文字节做哈希、原型元数据读取失败时安全回退（`_read_metadata` 对一个坏掉的 `prototype.edn` 只记 warning 返回名字兜底）都可以在完全不触碰求值器的前提下工作。求值被推迟到 L1 且永远由引擎发起——数据在系统里流动的每一站都不承担"执行"的风险。

三条边界值得强调：

- **解析层把语义区分固化进类型**。`agents/edn.py` 产出的不只是"数据"，而是带类型的数据：`Keyword`、`Symbol`、`Form` 分别是 str / str / list 的子类。`(...)` 是代码（Form，交给求值器）、`[...]` 是数据（普通 list）在解析完成时就是两个 Python 类型，`:mode :react` 与 `:mode "react"` 下游始终可区分。下游所有"这是表达式还是字面量"的判断都是 `isinstance`，没有任何约定俗成的二次解析。这正是 ADR 0001 选 EDN 的理由在代码里的落点。
- **EDN 只是语法，dataclass 才是语义**。`MemberSpec` 的模块 docstring 写明："EDN is only syntax, the dataclass is the semantics authority"。序列化前端可以换（`to_dict` 已经提供 snake_case dict 形态），语义模型不动。归一化（`_normalize_data`）只把 map 的 kebab-case 关键字键翻成 snake_case 字段名，对 Form 内部原样放行——表达式里的关键字是语言词汇（如 `:recipient`），不是配置键，不能被改名。
- **结构校验与作用域校验分离**。EDN 前端只查类型与形状（这个字段是不是 form、是不是非负 int）；builtin 白名单与符号作用域的校验全部集中在 `validate_member_spec` 一处，由 loader 调用而 `from_dict` 不调用——这样宿主相关的序列化 roundtrip（比如 Adam 的 spec 导出再导入）不会被无宿主的校验器误杀。校验规则只有一个家，散落即腐烂。

`dumps` 的存在也不只是为了测试 roundtrip：agent 自己书写 spec（writePrototype 修正重试、`to_edn` 序列化）依赖同一套读写对称性。

L1 内部还有一个刻意的同居：运行时求值（`eval_form`）与加载时静态校验（`validate_form`）住在同一个模块、共享对表达式树的同一套结构理解（特殊形式的形状、尾位置规则、绑定向量格式在两处是 mirrored 实现）。这保证了"校验器承认的语言"与"求值器执行的语言"不会随版本漂移而裂开——如果校验在别处另起炉灶，两边对 `(recur ...)` 合法位置的理解迟早出现缝隙，而静态校验的全部价值就在于它证明的东西在运行时成立。

loader 是整个系统**唯一**的读盘路径，这点也有设计意图：热重载、首次启动、测试中的临时 spec，走的都是同一条"解析 → 模型 → 校验 → 挂 provenance"的链，不存在第二条绕过校验的加载方式。链条唯一意味着校验策略的改动（比如新增一条结构约束）自动对所有加载场景生效，不需要逐个调用点追缴。

## 方向：与愿景的差距

当前实现的信任边界划在**行为重组**：LLM 可以组合已有机制写出新成员的行为（writePrototype → staging → 人工批准 → spawn），这已经是"新成员从社会内部产生"的最小闭环。

但**能力层尚不可进化**：工具清单、stage 集合、section 白名单、动作词汇表都是 Python 侧的白名单常量（`STAGE_NAMES` / `SECTION_NAMES` / `MEMBER_ACTION_NAMES` / 各 builtin 注册表），新增能力仍然只能由人类改代码。愿景路线的第三步（开放）要求能力也能从社会内部生长，而这道闸门目前刻意不开——一个能自己定义工具的 DSL 等于把信任模型的地基抽掉，本文所述的全部受限设计都会失效。

要开这道闸，至少有三个问题需要先拍板，而它们都不是 DSL 语法问题：能力的数据化形态是什么（工具描述进 spec 还是独立的 artifact）；能力的信任分级怎么做（行为的 staging + 人工批准流程能否原样套用到能力上，执行沙箱的边界画在哪）；审计责任归谁（新能力上线后出了事，是看 spec 还是看能力实现）。在这些答案出现之前，保持能力层为代码是让现有信任模型继续成立的必要条件。

所以现阶段的定位是：行为即数据已经成立，能力仍是代码。这不是待补的缺陷，而是当前信任边界的有意选择；何时、以什么形式把能力层也数据化（沙箱化工具描述？能力也需要 staging 与批准？），属于尚未拍板的方向性讨论，先见 [../vision.md](../vision.md) 的路线校准再动。
