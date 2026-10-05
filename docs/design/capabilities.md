# 能力数据化：复合能力成为可审批的数据 artifact

> 本文是能力数据化的设计拍板（issue #45，愿景路线第三步「开放」）。方向共识 2026-09-28，机制五点 2026-10-05 拍板。现状：三个施工切片全部落地（见文末「施工清单」；第 4 项 dsl.md 边界修订随本文档同步）。能力数据化把「行为即数据」推广到能力层：工具/能力描述成为数据 artifact，复用 spec 的 staging + 人工批准通道，从社会内部生长。

## 问题：能力层不可进化

[dsl.md](./dsl.md) 的信任边界长期划在**行为重组**：LLM 可以组合已有机制写出新行为，但工具清单、动作词汇表都是 Python 侧的白名单常量，新增能力只能人类改代码发版。愿景第三步「开放」要求能力也能从社会内部生长——而「能自己定义工具的 DSL」曾经是信任模型的禁区。

开闸的钥匙在**形态选择**：能力不是宿主代码，而是 tlosure 表达式。tlosure 求值器是被故意阉割到极点的执行环境（可停机、无副作用、fuel 计量），安全到可以交给 agent 自己书写——把能力限制在这个形态里，开闸就不等于抽掉地基。

## 已拍板总表

| 决策点 | 结论 | 拍板 |
|---|---|---|
| 数据形态 | **tlosure 函数 artifact**（`{:name :args :impl (fn ...)}`），非宿主代码、非 WASM | 2026-09-28 方向共识，2026-10-05 机制拍板 |
| 信任通道 | **staging + 人工批准**原样套用（与原型同一模式） | 同上 |
| 权限 | **不放大**：能力产出 ActionRequest 数据，引擎按**调用者**的配额与信任级执行 | 2026-09-28 |
| 能力天花板 | **复合可进化、原语不可进化**：组合既有词汇的能力走本通道；新原语（LLM 调用、新 IO、Docker、异步）永远 Python + 人类写 + 发版 | 2026-09-28 |
| env 契约 | **最小化：只见 args**，零注入 | 2026-10-05 |
| artifact 粒度 | **一个 artifact 一个词** | 2026-10-05 |
| 语境可见性 | 准入声明纯度；纯词可进门控语境，非纯词仅 `:actions` | 2026-10-05 |
| 命名空间 | **遮蔽即拒**（与既有 builtin/特殊形式同名拒载），不加前缀机制 | 2026-10-05 |
| 退役 | vocabulary only grows；deprecated → 全仓零引用才物理删 | 2026-10-05 |

## artifact 形态

```clojure
{:version 1
 :name "thread-digest"
 :description "把邮件线程蒸馏成三段式摘要文本"
 :args [{:name "mails" :type :list :doc "按时间升序的邮件列表"}]
 :impl (fn [mails] ...)
 :pure true
 :examples [{:args [[mail-a mail-b]] :expect "digest:2"}]}
```

- **`:impl` 是能力行为的全部**——tlosure 表达式，全文可读、加载期静态校验、fuel 有界。能力文本即行为全部，与 spec 同一套审计
- **`:args` 是显式声明的位置参数 schema**。类型注解（`:string` / `:number` / `:boolean` / `:list` / `:map`）是**评审材料不是静态保证**——tlosure 无类型系统，元数由 fn 应用在运行时兜住（与 builtin 同）。写清楚是为了评审时能判断「这个词的契约是什么」
- **`:pure` 是准入声明**，不是作者自觉——加载期校验强制（见「语境可见性」）
- **`:examples` 是可选的输入/输出断言**（仅 `:pure true` 可携带——非纯 impl 产出 ActionRequest 宿主对象，无法与 EDN 值比对）：`{:args [...] :expect <值>}` 列表，`:args` 是**字面数据不求值**，数量须等于 `:args` 声明数。准入校验时在沙盒求值器跑断言（纯注册表 + 零注入 env + DEFAULT_FUEL 计量），任一失败即拒载；detail 端点同步呈现逐条 PASS/FAIL 与实际值/错误（见「信任通道」）
- v1 边界：**`:impl` 只能组合内置词汇**（builtin 注册表 + 动作词），不能引用其他能力词——能力引能力的依赖图与退役级联留待真实需求出现再设计。落地口径（切片 1）：纯词注册表 = `CORE_BUILTINS` + `ARITHMETIC_BUILTINS`（19 词，逐词核实不读 env，与 `POLICY_BUILTINS` 同组成同契约）；非纯词注册表 = 纯词 + 六个动作词。**不含** trigger/loop/stage-cond 语境谓词（`addressed-to-me?`、`iteration-limit-reached?` 等直接读 env 的 `self`/`state`/`scratch` 段）与 host 查询词（`sleeping?` 等读注册表状态）——它们依赖隐式上下文，在零注入 env 下会静默退化；需要上下文一律调用点显式传参

## env 契约：只见 args

能力求值的 env = 调用方传入的位置参数，**零注入**：没有 `msg`、没有记忆、没有 `self`、没有任何隐式上下文。

- 需要上下文？**调用点显式传参**：`(thread-digest (mail-history 20))`——能看见什么全写在调用点上，评审不用猜隐式依赖
- 推论一：能力词在所有成员、所有 spec 里语义一致——可静态推理、可缓存、可 Diff 评审
- 推论二：这是数据能力与 Python builtin 的本质差异。builtin 有各自的 env 契约（TypedDict，如 `RuleEnv`/`OnCompleteEnv`）；数据能力没有家——这正是它比 builtin 更安全的原因
- **fuel**：`:impl` 体求值**计入调用方的 fuel 预算**（Closure 应用照计，与 Python builtin 的「宿主资源域」豁免不同）。晋升的词不会成为预算黑洞——这是特性：能力留在 fuel 纪律内，和「tlosure 是 L0」的宪法一致

## 信任通道：staging 复用

- **存储**：受信库 `~/.tlon/capabilities/` + staging 区 `~/.tlon/capabilities-staging/`，目录布局、元数据（`capability.edn`）、REVIEW.md、批准移动（staging → 受信库）全部对齐原型通道（[prototypes.md](./prototypes.md)、`agents/prototypes/staging.py` 模式）
- **信任模型同构**：受信是默认（人类手写进受信库即可信），staging 写入显式 `:trusted false`；approve/reject 是纯人类决定
- **动态验证 = `:examples` 断言**（2026-10-05 随 #30 拍板）：artifact 携带 `{:args [...] :expect ...}` 输入/输出对，准入校验时在沙盒求值器跑断言（纯词 + fuel 计量，天然安全）——评审看到的不只是「它声称做什么」，还有「它做到了什么」。更重的行为验证（携带该能力的 staging 原型进演武场，轨迹即证据，见 [arena.md](./arena.md)）留作后续独立项
- **异质评审**可复用 reviewPrototype 的模式（独立模型血线的评审意见，advisory）

## 语境可见性与纯度

准入时声明 `:pure`，加载期校验强制：

- **纯词**（`:impl` 只对纯注册表通过静态校验）：所有求值语境可用——`:when` 门控、`:until`、stage `:if`、`:actions`
- **非纯词**（组合动作词、产出 ActionRequest）：仅 `:actions` 与 on-complete 语境

分区机制与既有 builtin 的 `pure` 标记完全同构（`vocabulary.py` 的语境注册表切分）——校验器按词声明的纯度把它放进对应语境的可见集。

**命名空间**：词名与既有 builtin、特殊形式、其他受信能力同名即**拒载**（遮蔽即拒）。不加前缀机制——约定优于机制：词汇表总共几十个词，碰撞人眼可查；词的出处（哪个提案引入）记录在 artifact 元数据里。

## 词汇桥（机制缺口）

现状缺口：tlosure 的 `defn` 产物活在单次求值的 env dict 里，对一个 spec 之外的世界不可见；builtin 注册表全是 Python 模块级 dict，数据定义的 Closure 不会被 `validate_form` 认作已注册词汇。

机制答案：**受信能力库 → 成员注册表**。加载期（loader 唯一读盘路径）把受信库里每个 artifact 的 `:impl` 校验（结构 + 纯度 + 命名空间）后注册进成员 spec 的校验/求值注册表——从此全社会每个成员都能在自己的 spec 里组合它。这座桥同时是 #30（突变晋升通道）的受信词汇库机制：一次建设，两个 EPIC 共用。

落地形状（切片 2/3）：`Builtin` 注册表条目从「只有宿主 fn」扩成两形——宿主 fn（内部不计费）或 **closure-backed**（携带 `Closure`，求值器命中时走 closure 应用，body 计入调用方 fuel/depth）；captured env 为空，body 只见自己的参数绑定，body 内的词经调用方注册表解析。桥本身（`agents/capabilities/bridge.py` 的 `load_capability_builtins`）在**每次 `load_member_spec` 时重算**（受信库是小数据，无缓存失效问题）——新批准的词在成员下次 wake 自然生效。单个坏 artifact 只排除该词（fail-soft），引用者在 own 加载校验处报 unknown word。

## 退役纪律

对齐既有「vocabulary only grows, semantics only tighten」：

- 词**只增不删**；要退役先 `:deprecated true`（元数据标记 + detail 呈现 + 评审警告）
- **物理删除的唯一前提：全仓成员 spec 与受信能力 `:impl` 零引用**（grep 可证）。删除是显式的人类动作——加载期校验是守门人：删掉仍被引用的词 = 引用者全部拒载，所以这条纪律不是约定而是结构

## 信任边界（不做什么）

- **权限不放大**：能力产出的是 ActionRequest **数据**，引擎按调用者身份执行——一个能力词做不到调用者本人做不到的事
- **能力碰不到原语**：LLM 调用、新 IO、Docker、异步永不在 `:impl` 的表达力内（fuel 限得住步数限不住钱——LLM 类能力留在 Python 层的根本原因）
- **v1 能力词只在 DSL 语境**（spec 的 rules/actions），不进 LLM 工具面——ToolExecutor 注册（LLM 可动态调用的工具）需要 参数 schema → tool schema 的映射设计，是独立后续决策。这收窄了 issue #45 原清单的「ToolExecutor 从 artifact 注册」一项：DSL 词汇先行，对齐宪法五「全社会每个成员都能在自己的 **spec** 里组合它」的原始表述
- 能力引能力（`:impl` 引用其他能力词）v1 不做

## #30：突变晋升通道（全面 tlosure，2026-10-05 拍板）

#30（突变晋升通道，宪法五）与本通道的关系重新定位：~~workspace 程序 → 重表达为 tlosure → 演武场 → staging~~ 的 workspace 起源**正式废止**。新口径：**突变出生即 tlosure artifact**——agent 从意图与自身经验直接起草 `:impl`（`proposeBehavior` 的能力版），经同一条 staging 闸投递。device 不再是突变发生器；tlosure 的受限表达力从「晋升前必须跨越的围墙」变成「突变的天生形态」——静态准入校验器（19 词注册表 + `:pure` 强制 + 遮蔽即拒）就是唯一判据，写不出来的能力不配晋升。

随动的两个机制拍板：

- **动态验证走 `:examples` 断言最小版**（见「信任通道」节）；演武场行为验证留作后续独立项
- **版本演进：同名即拒、改进走新名**——「vocabulary only grows」延伸到词义不可变；淘汰仍走 deprecated → 零引用物理删（见「退役纪律」）

#30 施工清单（详见 issue #30）：设计文档回填（本节）✅ → `:examples` 动态验证 ✅（2026-10-05：`ExampleSpec` schema（pure-only、args 字面数据、数量对齐声明）+ `evaluate_capability_examples`（纯注册表 + 零注入 env + DEFAULT_FUEL 沙盒求值，EvalError 记录不抛出）挂进 `validate_capability` 加载闸末段（staging 写路径自动强制），facade `run_examples` fail-soft + admin detail payload `examples` 键（EDN 值渲染为 EDN 文本）+ WebUI detail 面板 PASS/FAIL 呈现）→ `proposeCapability` 工具 ✅（2026-10-05）：`tools/agents/propose_capability.py` 的 `ProposeCapabilityTool`，严格对齐 `ProposeBehaviorTool` 模式——血脉偿付闸（fail-open，排在起草前）→ 读记忆 digest + lessons（**不读 behavior.tlon 底稿**）→ 一次起草 LLM 调用（`purpose="propose"`、60s）→ `_extract_edn` 提取 + 词名一致性核对（起草产物 `:name` 必须恰好是指定词名，不一致回喂重起草）→ `write_staging_capability` 闸（校验错误原文回喂 + `data.hint` artifact schema 提示，外层 ReAct 重试）；起草 hint 的 19 纯词从 tlosure 注册表代码枚举、六动作词名单有测试锚定 `CAPABILITY_PURE_BUILTINS`/`MEMBER_ACTION_NAMES` 防漂移；既有受信词清单由 assembly 侧渲染成 prompt 文本以零参 callable 注入（防重复造词 + 风格示范）；`CapabilityError` 改继承 tools 层端口 `CapabilityProposalError`（`tools/agents/ports.py`，`PrototypeError` 先例——`tlon.tools` 不反向依赖 `tlon.agents`）；**不做 near-kin**（几十词小词汇表 + 同名硬拒 + 评审见全文与断言，相似度子系统是预防性抽象）；注册进 `REPRODUCTION_TOOLS`（见习成员不注册），单工具 v1 无 writeCapability 裸写通道（人类裸写已有 WebUI 表单）。

## 施工清单（对应 issue #45）

1. **artifact 格式 + 加载/校验链**：`capability.edn` schema、staging 区读写、纯度强制、命名空间拒载（对齐 `agents/prototypes/staging.py` 模式） ✅（2026-10-05，issue #45 切片 1/3）：新包 `tlon.agents.capabilities`——`spec.py`（`CapabilitySpec` from_edn/to_edn roundtrip + 结构校验：version==1、name 合法词标识符（字母起头）、args 形状与五类 `:type`、impl 必须 `(fn [...] ...)` 字面且定长（拒 `& rest`）、args 数 == fn 参数数、pure 是 bool；`validate_capability` 准入闸 = 命名空间拒载（特殊形式/builtin/受信能力遮蔽即拒，`taken_names` 注入）+ 纯度强制（`:pure true` 的 impl 只对 `CAPABILITY_PURE_BUILTINS` 过静态校验，动作词按未注册词汇拒；非纯对 `CAPABILITY_FULL_BUILTINS`）+ 空 scope 校验（`msg`/`self` 等隐式符号一律 undefined））；校验注册表逐词核实不读 env——纯注册表 = CORE+ARITHMETIC 19 词（同 `POLICY_BUILTINS` 组成），动作词六枚经 `make_member_builtins()` 提取，env-free 用 AST 静态锚定测试固话；`info.py`/`catalog.py`/`staging.py` 全量对齐原型通道（先校验后落盘、`:trusted false`、approve 整目录 move 翻 trusted 携带 REVIEW.md、reject 路径注入防护）；`TlonPaths` 加 `capabilities_dir()`/`capabilities_staging_dir()`（纯解析器，目录创建在写入点）
2. **词汇桥**：受信能力库 → 成员注册表（loader 注入，纯度分区进语境可见集） ✅（2026-10-05，issue #45 切片 2/3）：`Builtin` 扩为两形——宿主 fn（内部不计费）或 closure-backed（`Builtin.from_closure`，携带 `Closure`，evaluator 命中走 `_apply`，body 计入调用方 fuel/depth，fuel 计量差有回归锚定测试）；captured env 为空 = 零注入的运行时面，body 内词经调用方注册表解析；`capabilities/bridge.py` 的 `load_capability_builtins(tlon_paths, reserved=...)` 扫受信库 → 逐 artifact `from_edn` + `validate_capability`（taken = 宿主词 + 已接受受信词）→ 构造 closure-backed 条目（pure 按声明）；坏 artifact fail-soft（structlog error + 排除该词，引用者在 own 加载处报 unknown word）；`AgentRegistry._member_builtins()` 每次 `load_member_spec` 重算能力部分并与宿主词合并（profile loader 与 `_start_agent` 同一注册表，MemberRuntime 求值侧同一份），新批准的词成员下次 wake 生效；顺带补 `count` 进 `SCRIPT_BUILTINS`（成员注册表原是准入注册表 19 词缺 `count` 的子集——不修则含 `count` 的 artifact 批准后在每个成员运行时炸，子集关系有回归测试固话）
3. **staging 通道接线**：admin 端点 + detail payload + WebUI 呈现（对齐原型通道） ✅（2026-10-05，issue #45 切片 3/3）：`tlon.api.capabilities.CapabilitiesFacade`（薄透传 catalog/staging 函数族，`CapabilityError`/`CapabilityInfo` 经 `__all__` 对 server 可见）+ `Runtime` 第八个 facade；REST 新 mixin `admin_capabilities.py`——`GET /admin/capabilities[/staging]`（列表）、`GET /admin/capabilities/{name}` 与 `GET /admin/capabilities/staging/{name}`（详情：元数据 + artifact 全文 + REVIEW.md）、`POST /admin/capabilities/staging`（创建提案，加载闸不过 400 `invalid_capability` 不落盘）、`POST .../staging/{name}/approve|reject`（语义对齐原型通道，approve 后成员下次 wake 经词汇桥可用）；WebUI 新 `CAPABILITIES` 视图（受信库/staging 双区列表、纯度标记、deprecated 划线 + 徽章、详情面板 artifact/REVIEW.md 终端全文、APPROVE 直按 / REJECT 确认框、新建提案表单——v1 人类写能力的主入口）
4. **dsl.md 边界修订**：「能力不可进化」→「复合可进化、原语不可进化」（本设计文档落地即同步）

后续（非本文范围）：ToolExecutor 注册（LLM 工具面）、能力引能力、#30 的施工项（见上节）。
