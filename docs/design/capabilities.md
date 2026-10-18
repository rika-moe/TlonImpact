# 能力数据化：复合能力成为可审批的数据 artifact

复合能力是可审批的 tlosure 函数 artifact，批准后成为成员 spec 可调用的词。本文定义当前准入、注册、执行和提案机制；行为语言见 [dsl](dsl.md)，宿主接口见 [bridge-api](bridge-api.md)。

## 复合能力与宿主原语

[dsl.md](./dsl.md) 的信任边界长期划在**行为重组**：LLM 可以组合已有机制写出新行为，但工具清单、动作词汇表都是 Python 侧的白名单常量，新增能力只能人类改代码发版。愿景第三步「开放」要求能力也能从社会内部生长——而「能自己定义工具的 DSL」曾经是信任模型的禁区。

开闸的钥匙在**形态选择**：能力不是宿主代码，而是 tlosure 表达式。tlosure 求值器以 fuel/depth 定界，不直接执行外部效果——把能力限制在这个形态里，开闸就不等于抽掉地基。

## 领域约束

| 约束 | 当前规则 |
|---|---|
| 数据形态 | **tlosure 函数 artifact**（`{:name :args :impl (fn ...)}`），非宿主代码、非 WASM |
| 信任通道 | **staging + 人工批准**原样套用（与原型同一模式） |
| 权限 | **不放大**：能力产出 ActionRequest 数据，引擎按**调用者**的配额与信任级执行 |
| 能力天花板 | **复合可进化、原语不可进化**：组合既有词汇的能力走本通道；新原语（LLM 调用、新 IO、Docker、异步）永远 Python + 人类写 + 发版 |
| env 契约 | **最小化：只见 args**，零注入 |
| artifact 粒度 | **一个 artifact 一个词** |
| 语境可见性 | 准入声明纯度；纯词可进门控语境，非纯词仅 `:actions` |
| 命名空间 | **遮蔽即拒**（与既有 builtin/特殊形式同名拒载），不加前缀机制 |
| 退役 | vocabulary only grows；deprecated → 全仓零引用才物理删 |

## artifact 形态

```clojure
{:version 1
 :name "double-value"
 :description "把输入数字乘以二"
 :args [{:name "n" :type :number :doc "待计算的数字"}]
 :impl (fn [n] (* n 2))
 :pure true
 :examples [{:args [3] :expect 6}]}
```

- **`:impl` 是能力行为的全部**——tlosure 表达式，全文可读、加载期静态校验、fuel 有界。能力文本即行为全部，与 spec 同一套审计
- **`:args` 是显式声明的位置参数 schema**。类型注解（`:string` / `:number` / `:boolean` / `:list` / `:map`）是**评审材料不是静态保证**——tlosure 无类型系统，元数由 fn 应用在运行时兜住（与 builtin 同）。写清楚是为了评审时能判断「这个词的契约是什么」
- **`:pure` 是准入声明**，不是作者自觉——加载期校验强制（见「语境可见性」）
- **`:examples` 是可选的输入/输出断言**（仅 `:pure true` 可携带——非纯 impl 产出 ActionRequest 宿主对象，无法与 EDN 值比对）：`{:args [...] :expect <值>}` 列表，`:args` 是**字面数据不求值**，数量须等于 `:args` 声明数。准入校验时在沙盒求值器跑断言（纯注册表 + 零注入 env + DEFAULT_FUEL 计量），任一失败即拒载；detail 端点同步呈现逐条 PASS/FAIL 与实际值/错误（见「信任通道」）
- v1 边界：**`:impl` 只能组合内置词汇**（builtin 注册表 + 动作词），不能引用其他能力词——能力引能力的依赖图与退役级联留待真实需求出现再设计。当前词汇：纯词注册表 = `CORE_BUILTINS` + `ARITHMETIC_BUILTINS`（19 词，逐词核实不读 env，与 `POLICY_BUILTINS` 同组成同契约）；非纯词注册表 = 纯词 + 六个动作词。**不含** trigger/loop/stage-cond 语境谓词（`addressed-to-me?`、`iteration-limit-reached?` 等直接读 env 的 `self`/`state`/`scratch` 段）与 host 查询词（`sleeping?` 等读注册表状态）——它们依赖隐式上下文，在零注入 env 下会静默退化；需要上下文一律调用点显式传参

## env 契约：只见 args

能力求值的 env = 调用方传入的位置参数，**零注入**：没有 `msg`、没有记忆、没有 `self`、没有任何隐式上下文。

- 需要上下文？**调用点显式传参**：`(double-value (get event :count))`（调用语境须提供 `event`，且 `:count` 为数字）——能看见什么全写在调用点上，评审不用猜隐式依赖
- 推论一：能力词在所有成员、所有 spec 里语义一致——可静态推理、可缓存、可 Diff 评审
- 推论二：这是数据能力与 Python builtin 的本质差异。builtin 有各自的 env 契约（TypedDict，如 `RuleEnv`/`LoopEnv`）；数据能力没有家——这正是它比 builtin 更安全的原因
- **fuel**：`:impl` 体求值**计入调用方的 fuel 预算**（Closure 应用照计，与 Python builtin 的「宿主资源域」豁免不同）。晋升的词不会成为预算黑洞——这是特性：能力留在 fuel 纪律内，与 tlosure 的计量求值边界一致

## 信任通道：staging 复用

- **存储**：受信库 `~/.tlon/capabilities/` + staging 区 `~/.tlon/capabilities-staging/`，目录布局、元数据（`meta.tlon`）、REVIEW.md、批准移动（staging → 受信库）全部对齐原型通道（[prototypes.md](./prototypes.md)、`agents/prototypes/staging.py` 模式）
- **信任模型同构**：受信是默认（人类手写进受信库即可信），staging 写入显式 `:trusted false`；approve/reject 是纯人类决定
- **动态验证 = `:examples` 断言**：artifact 携带 `{:args [...] :expect ...}` 输入/输出对，准入校验时在沙盒求值器跑断言（纯词 + fuel 计量，天然安全）——评审看到的不只是「它声称做什么」，还有「它做到了什么」。更重的行为验证（携带该能力的 staging 原型进演武场，轨迹即证据，见 [arena.md](./arena.md)）留作后续独立项
- **异质评审**可复用 reviewPrototype 的模式（独立模型血线的评审意见，advisory）

## 语境可见性与纯度

准入强制 `:pure` 声明：纯能力只能引用纯注册表；非纯能力可组合动作词、产出 `ActionRequest`。接受的设计边界是纯词可用于纯语境，非纯词只用于动作语境，不能通过函数封装逃逸纯度限制。

**当前接线范围**：`AgentRegistry._member_builtins` 将受信词加入成员注册表，规则 `:when` 使用其纯子集，规则 `:actions` 使用全表。这两类执行点已经接通能力库。

**语境边界**：会话配方的 `:until` 和 stage `:if` 使用固定注册表，能力词在这些位置加载即被拒绝。普通规则动作统一使用含受信能力的成员注册表，不再有另一套完成动作求值器。能力可组合不等于可以绕过宿主词汇、效果权限或预算限制。

**命名空间**：词名与既有 builtin、特殊形式、其他受信能力同名即**拒载**（遮蔽即拒）。不加前缀机制——约定优于机制：词汇表总共几十个词，碰撞人眼可查；词的出处（哪个提案引入）记录在 artifact 元数据里。

## 词汇桥：受信能力库进入成员注册表

单次求值中的 `defn` 不能自行成为其他成员可用的词。能力库通过独立准入与注册把经过批准的函数暴露给其他 spec。

机制答案：**受信能力库 → 成员注册表**。成员加载链上把受信库里每个 artifact 的 `:impl` 校验（结构 + 纯度 + 命名空间）后注册进成员 spec 的校验/求值注册表——从此全社会每个成员都能在自己的 spec 里组合它。直接起草的能力与人类提交的能力共用这一条受信词汇桥。

当前实现：`Builtin` 注册表条目从「只有宿主 fn」扩成两形——宿主 fn（内部不计费）或 **closure-backed**（携带 `Closure`，求值器命中时走 closure 应用，body 计入调用方 fuel/depth）；captured env 为空，body 只见自己的参数绑定，body 内的词经调用方注册表解析。桥本身（`agents/capabilities/bridge.py` 的 `load_capability_builtins`）在**注册表为成员加载 spec 时重算**（受信库是小数据，无缓存失效问题）——新批准的词在成员下次 wake 自然生效。单个坏 artifact 只排除该词（fail-soft），引用它的成员在自身加载校验处报未注册词汇。

## 退役纪律

对齐既有「vocabulary only grows, semantics only tighten」：

- 词**只增不删**；要退役先 `:deprecated true`（元数据标记 + detail 呈现 + 评审警告）
- **物理删除的唯一前提：受信能力与所有受管理的原型、staging/成员 spec 零引用**（删除前显式核查）。删除是显式的人类动作——加载期校验是守门人：删掉仍被引用的词 = 引用者全部拒载，但当前没有自动检查全库引用并阻止删除的退役闸，删除前的引用核查仍是治理责任

## 信任边界（不做什么）

- **权限不放大**：能力产出的是 ActionRequest **数据**，引擎按调用者身份执行——一个能力词做不到调用者本人做不到的事
- **能力碰不到原语**：LLM 调用、新 IO、Docker、异步永不在 `:impl` 的表达力内（fuel 限得住步数限不住钱——LLM 类能力留在 Python 层的根本原因）
- **v1 能力词只在 DSL 语境**（spec 的 rules/actions），不进 LLM 工具面——ToolExecutor 注册（LLM 可动态调用的工具）需要 参数 schema → tool schema 的映射设计，是独立后续决策。这收窄了 issue #45 原清单的「ToolExecutor 从 artifact 注册」一项：DSL 词汇先行，当前使用入口是成员 **spec** 中的词汇组合
- 能力引能力（`:impl` 引用其他能力词）v1 不做

## 当前提案机制

突变直接起草为 tlosure artifact：成员从意图、记忆 digest 与 lessons 生成 `:impl`，不以 workspace 程序为起点。`proposeCapability` 由 [tools/agents/propose_capability.py](../../src/tlon/tools/agents/propose_capability.py) 的 `ProposeCapabilityTool` 实现：

1. 在起草前执行血脉偿付政策检查；政策求值异常采用既定 fail-open 处理。
2. 一次 LLM 起草调用提取 EDN，并核对词名必须等于申请名称。
3. 经 `write_staging_capability` 运行结构、纯度、命名空间及 examples 准入；校验错误和格式提示返回给调用者，由外层会话循环决定重试。
4. 产物进入 staging，由人类评审并批准。已有受信词清单提供给起草 prompt，避免重复造词；同名仍由准入硬拒绝。

工具属于 `REPRODUCTION_TOOLS`，见习成员不注册。不存在给成员绕开起草与 staging 校验的裸写工具；人类可以通过管理界面提交 artifact。

类型与校验在 [agents/capabilities/spec.py](../../src/tlon/agents/capabilities/spec.py) 的 `CapabilitySpec` / `validate_capability`；example 求值由 `evaluate_capability_examples` 完成；[agents/capabilities/bridge.py](../../src/tlon/agents/capabilities/bridge.py) 的 `load_capability_builtins` 负责词汇桥。工具层依赖 proposal 端口，不能反向 import agents 实现。

## 演进与未实施方向

同名即拒，改进使用新名；词义不原地替换。deprecated 元数据用于目录和评审呈现，词仍注册；物理删除需先确认引用已经清除。该规则记录当前接受的能力治理约束，不增加旧格式解析或运行时版本矩阵。

尚未实施：能力进入 LLM ToolExecutor、能力引用其他能力、自动相似能力检测、携带候选能力的原型演武。后者与当前纯 examples 动态断言不同，不能把 examples 的通过视为认知行为评估通过。

历史来源为能力数据化 Issue #45 与直接起草/动态断言 Issue #30；原型通道的完整设计见 [prototypes](prototypes.md)。
