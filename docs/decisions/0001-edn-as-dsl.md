# ADR 0001：用 Lisp 风格 EDN 作为行为 DSL 的语法

> 阅读定位：本文保留当时的选择与理由；当前设计见 [dsl](../design/dsl.md)。当前适用范围：语法选择保留；「无 fn/def」由 ADR 0021 修订，语言核心路径由 ADR 0018 调整。完整关系见 [决策索引](README.md)。

- 状态：已接受（回溯记录，2026-08-15）
- 相关：[../design/dsl.md](../design/dsl.md)（DSL 整体设计）、[../reference/tlosure-reference.md](../reference/tlosure-reference.md)（语法手册）、[../reference/dsl-reference.md](../reference/dsl-reference.md)（宿主 schema 手册）

## 背景

Tlon 要求 agent 的行为是数据而不是代码（见 [../vision.md](../vision.md) 的「行为即数据」）：只有行为可被读写，agent 才能自己书写行为，自进化才有可能。同时这份数据的预期作者包括 LLM（[tools/agents/write_prototype.py](../../src/tlon/tools/agents/write_prototype.py)），所以承载它的语言必须满足一组不常见的同时成立的约束：

1. 是数据——可解析、可序列化、可整体校验；
2. 能表达策略——条件门控、循环、对事件负载的计算，不只是静态配置；
3. 对不可信作者安全——可停机、无副作用、词汇表可白名单化；
4. 解析与校验的实现必须足够小，小到"完全正确"是可达成的目标。

需要选的是一种语法形态，它决定了后面三条约束的实现成本。

## 决策

用 Lisp 风格的 EDN（Extensible Data Notation）子集作为 DSL 语法。解析器是自研的单文件实现 [tlosure/edn.py](../../src/tlosure/edn.py)（`loads` / `dumps`），语义模型是 [agents/member_spec.py](../../src/tlon/agents/member_spec.py) 的 `MemberSpec` dataclass 树——EDN 只是序列化前端，dataclass 才是语义权威。

## 理由

**`(...)` 代码 / `[...]` 数据在语法层面二分。** 这是选 EDN 而非通用数据格式的核心理由。解析器把列表 `(think)` 解析为 `Form`（代码，交给求值器），把向量 `[...]` 解析为普通 list（数据）——表达式与字面量在解析完成的那一刻就是两个不同的 Python 类型，无需任何二次解析、无需约定俗成的转义协议即可区分。`:when` 字段里写一个谓词表达式和写一个字符串，下游靠类型就能分辨。

**解析器极小，total 校验可行。** EDN 子集只有九种字面量（nil/布尔/数/字符串/关键字/符号/向量/列表/映射），[tlosure/edn.py](../../src/tlosure/edn.py) 是一个约三百行的递归下降解析器，无依赖、错误带行列号。安全语言的前提是"没有未定义行为"：语法足够小，加载时的全量静态校验（`validate_form` 遍历表达式，确认每个 form 头是白名单特殊形式或已注册 builtin、每个符号在作用域内）才是彻底而非抽样的。

**同像性让特殊形式可扩展而不改语法。** 代码即数据意味着新增一个特殊形式（如 phase D 加入的 `while`/`recur`）只需要扩展求值器的白名单 `SPECIAL_FORMS` 和校验器，解析器与所有既有 spec 文本一行不动。语法演化成本被压到接近零，这对一个会长期演进、且作者会逐渐变成 agent 的语言至关重要。

**受限求值器在 EDN 上实现成本最低。** DSL 的核心安全性质——fuel 计量保证停机、无 fn/def/宏、求值纯无副作用、动作只产出 `ActionRequest` 数据（见 design 文档）——落在 s-expression 上是教科书形态：一个 `eval_form` 函数（[tlosure/eval.py](../../src/tlosure/eval.py)）递归解释 `Form`，每个求值步扣一格 fuel，form 头不是特殊形式就是 builtin 白名单成员。没有语句、没有运算符优先级、没有作用域解析的语法歧义需要处理，求值器与静态校验器共享同一套遍历逻辑。

**读写对称。** `dumps` 把数据序列化回 EDN 文本，roundtrip 成立。agent 书写 spec（writePrototype 的校验-修正-重试循环）、`MemberSpec.to_edn` 导出，都依赖同一套对称性——这在"配置文件"类格式里通常要么缺失要么需要引第三方库。

## 备选方案

**YAML/JSON 配置。** 纯数据格式天然满足约束 1，但不满足约束 2：表达式只能以字符串内嵌（`when: "msg.to.includes(me)"`），于是需要第二个表达式语言、第二次解析、第二套错误报告，且嵌在字符串里的表达式无法参与加载时的静态校验——坏表达式要到运行时才炸。数据与代码的区分退化为约定，而不是类型。

**Python 代码（直接执行或用 ast 沙箱）。** 表达力最强，但三条安全约束全部失守：Python 的停机性与副作用无法静态约束，ast 级沙箱是出了名堵不完的攻防面；且代码无法作为数据热载、diff、序列化传递。把它交给 LLM 自由书写等于放弃信任模型。

**JSON Schema + 自定义 DSL。** 在 JSON 之上自定义表达式编码（如 `{"op": "and", "args": [...]}`）：表达力与安全性都要自己从头造，而造出来的东西是同像性更差、语法噪音更大的 Lisp——花更大的成本得到一个更差的 EDN。

## 后果

**接受的代价：**

- 自研解析器与求值器的长期维护成本——没有上游可以升级，bug 都是自己的。用实现极小（单文件、无依赖）对冲。
- 生态工具缺失：编辑器高亮、格式化、LSP 都没有现成的，`behavior.tlon` 在通用 IDE 里只能当普通文本。用 `dumps` 的规范输出充当事实上的格式化器。
- EDN 对团队多数成员是陌生语法，LLM 也可能产出括号失衡的文本——由解析器的行列号报错与 writePrototype 的"错误原文返回、让 LLM 自己修"流程消化。

**换得的收益：**

- 行为即数据成立：spec 可解析、可校验、可序列化、可由 agent 书写，自进化的基因型前提就位。
- 安全性质（停机、纯求值、白名单词汇）的实现与验证成本都在可控量级。
- 语法零演化成本：语言随需求长大的同时，既有 spec 与解析器不动。

## 演进补注

- **ADR 0018（2026-09）路径迁移**：解析器与求值器随 DSL 抽包迁至 [tlosure](../../src/tlosure) 包（`tlosure/edn.py`、`tlosure/eval.py`），不再位于 `agents/`；语义模型 `MemberSpec` 留在 tlon 宿主侧。
- **ADR 0021（2026-09-28）函数能力**：正文「无 fn/def/宏」的表述已被推翻——tlosure 获得完整函数能力（`defn`、闭包、变长参数、递归、一等函数、函数体可含动作词汇），安全约束统一由 fuel/depth 承担；宏仍然不做。