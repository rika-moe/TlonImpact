# tlosure 语言参考

tlosure（[src/tlosure/](../../src/tlosure/)）是 Tlon 行为 DSL 的**语言核心**：一个 EDN 子集（Clojure 兼容子集，[ADR 0001](../decisions/0001-edn-as-dsl.md)、[ADR 0011](../decisions/0011-sci-compatible-subset.md)）加一个燃料计费的受限求值器——每个求值步消耗燃料，任何表达式必然终止（ADR 0011）。它是一门**纯语言**：不依赖任何宿主代码，自身不含任何业务概念（核心命名检验：一个构造能不提及宿主业务概念就解释清楚才配进 tlosure，[ADR 0018 修订](../decisions/0018-dsl-extraction-tlosure.md)）；tlon 是它的第一宿主。

宿主接入模型：宿主调用 `eval_form(form, env, builtins, fuel=..., max_depth=...)`（[src/tlosure/eval.py](../../src/tlosure/eval.py)）时**注入两样东西**——`builtins` 注册表（词汇表，可并入本文第 3 节的通用词汇）与 `env` 环境绑定（符号 → 值的 map）。env 里放什么、求值结果与产出的 `ActionRequest` 怎么落地，都是宿主契约，不属于本语言规范。tlon 宿主的领域词汇与 MemberSpec schema 见 [dsl-reference.md](./dsl-reference.md)。

## 1. EDN 语法速览

解析器：[src/tlosure/edn.py](../../src/tlosure/edn.py) 的 `loads()`。只解析数据，不求值。

| 字面量 | 例子 | 解析结果 |
|---|---|---|
| 空 / 布尔 | `nil` `true` `false` | `None` / `True` / `False` |
| 整数 | `42` `-7` `+3` | `int`（布尔值不被接受为 int） |
| 浮点 | `1.5` `1e3` `-2.5e-2` | `float` |
| 字符串 | `"你好\n"` | `str`，支持转义 `\"` `\\` `\n` `\t` `\r` `\b` `\f` `\uXXXX` |
| 关键字 | `:foo-bar` | `Keyword`（str 子类，不含前导冒号） |
| 符号 | `react` `=` `contains?` | `Symbol`（str 子类） |
| 向量 | `[1 2 3]` | 普通 list —— **数据** |
| 列表 | `(think)` | `Form`（list 子类）—— **代码**，由求值器解释 |
| 映射 | `{:a 1 :b 2}` | dict；必须偶数个表单项，否则报解析错误 |
| 注释 | `; 到行尾` | 跳过；逗号视为空白 |

要点（[src/tlosure/edn.py](../../src/tlosure/edn.py) 模块 docstring）：

- `(...)` 与 `[...]` 语义不同：列表是表达式，向量是数据。宿主 schema 中所有表达式位置都必须写成 `(...)` 形式。
- `Keyword`、`Symbol`、普通字符串是三种不同类型：`:mode :react` 与 `:mode "react"` 下游可区分；`=` 比较时 `:a` ≠ `"a"`（[src/tlosure/builtins.py](../../src/tlosure/builtins.py) 的 `_value_equal`）。
- 符号可用字符集：字母、数字及 `*+!-_?./<>=`，所以 `contains?`、`>=`、`start-all` 都是合法符号。
- 解析失败抛 `EDNParseError`，带 1 起始的行列号。

## 2. 求值器

受限求值器：[src/tlosure/eval.py](../../src/tlosure/eval.py) 的 `eval_form()`。

### 2.1 特殊形式（封闭白名单 `SPECIAL_FORMS`）

无 `def`、无 `lambda`、无宏、无可变全局状态；除下列形式外，form 头必须是已注册的 builtin 名。

<!-- BEGIN GENERATED: dsl-special-forms -->
封闭白名单（10 个）：`if`、`when`、`let`、`and`、`or`、`while`、`recur`、`map`、`filter`、`reduce`。（本区块由 `scripts/gen_reference_docs.py` 生成，勿手改；改了会被 `--check` 打回）
<!-- END GENERATED: dsl-special-forms -->

| 形式 | 形状 | 语义 |
|---|---|---|
| `if` | `(if test then [else])` | 2 或 3 参；无 else 且 test 为假时得 `nil` |
| `when` | `(when test body...)` | test 为假得 `nil`；否则依次求值 body，返回最后一个 |
| `let` | `(let [name init ...] body...)` | 绑定向量须偶数项；init 按序求值、后者可见前者 |
| `and` / `or` | `(and a...)` / `(or a...)` | 短路；`and` 返回第一个假值或末值，`or` 返回第一个真值或 `nil` |
| `while` | `(while [name init ...] test body...)` | Clojure 风格有界循环：test 为假结束（返回 `nil`），body 末值即循环值；迭代靠 `recur` |
| `recur` | `(recur v...)` | 只能出现在**最近一层 `while` 的尾位置**（`if` 分支、`when`/`let` 末尾 body、`and`/`or` 末参数继承尾位置）；参数个数须等于 `while` 绑定数；参数先在旧环境求值再同时重绑 |
| `map` | `(map x body coll)` | 对向量逐元素绑定 `x` 求值 body，收集结果向量。**注意参数顺序是绑定、body、集合** |
| `filter` | `(filter x body coll)` | 同上，收集 body 为真的元素本身 |
| `reduce` | `(reduce acc x body init coll)` | 五个参数：累加器名、元素名、body、初值、集合 |

求真值按 Python 语义：`nil`/`false`/`0`/`""`/`[]` 均为假。

### 2.2 计量与终止

- 每个求值步消耗 1 单位 **fuel**，耗尽抛 `FuelExhaustedError`；form 嵌套深度硬顶 `DEFAULT_MAX_DEPTH = 100`（超出抛 `EvalDepthError`）。递归不可能（无法命名函数），`while`/`recur` 表达能力等价图灵机，**终止性由 fuel 保证**。
- 计量常量（[src/tlosure/eval.py](../../src/tlosure/eval.py) 顶部）：默认预算 `DEFAULT_FUEL = 1000`；`DEFAULT_WHEN_FUEL = 200`（供宿主给门控类求值用的更紧默认值）；宿主可声明预算的硬顶 `MAX_FUEL = 10000`；嵌套深度硬顶 `DEFAULT_MAX_DEPTH = 100`。宿主在多段表达式间怎么分摊预算（共享计数器还是分段计量）由宿主决定。

### 2.3 无副作用与 `ActionRequest`（"DSL 描述，宿主引擎执行"）

- 求值永不产生副作用。宿主注册的动作 builtin 标记 `pure=False`，只校验参数并产出 `ActionRequest` 数据（`kind` + `data`，[src/tlosure/builtins.py](../../src/tlosure/builtins.py)），由宿主引擎在求值结束后执行。
- 环境值对表达式不透明：没有属性/方法访问语法，`env` 里的宿主对象只能经宿主注册的 builtin 检查。
- map 是数据：键保持字面量，值会被求值——选项 map 里可嵌表达式，如 `{:to [(get env-value :recipient)]}`。

## 3. 通用词汇

纯（pure）、total 的函数注册表，宿主可原样并入自己的词汇表（[src/tlosure/builtins.py](../../src/tlosure/builtins.py)）。两个注册表全部 `pure=True`——对着它们求值按构造无副作用。

### 3.1 `CORE_BUILTINS`（通用谓词与访问器）

<!-- BEGIN GENERATED: dsl-builtins-core -->
`CORE_BUILTINS`（5 个，按源码定义顺序）：（本区块由 `scripts/gen_reference_docs.py` 生成，勿手改；改了会被 `--check` 打回）

| 名称 | pure | 说明 |
|---|---|---|
| `=` | 是 |  |
| `not` | 是 |  |
| `contains?` | 是 |  |
| `get` | 是 | Read ``key`` from a dict payload; missing keys yield ``default``. |
| `str` | 是 | Concatenate arguments into one string (issue ). |
<!-- END GENERATED: dsl-builtins-core -->

`=` 是任意元比较，按 `_value_equal` 判定：`Keyword` / `Symbol` / `str` 三种字符串类型互不相等。`get` 是 total 的——目标不是 dict 也返回 `default`，永不对畸形负载抛错；`contains?` 对非向量/字符串目标抛 `EvalError`。

### 3.2 `ARITHMETIC_BUILTINS`（算术）

任意元算术与二元数值比较；拒绝布尔与非数字。

<!-- BEGIN GENERATED: dsl-builtins-arithmetic -->
`ARITHMETIC_BUILTINS`（9 个，按源码定义顺序）：（本区块由 `scripts/gen_reference_docs.py` 生成，勿手改；改了会被 `--check` 打回）

| 名称 | pure | 说明 |
|---|---|---|
| `+` | 是 |  |
| `-` | 是 |  |
| `*` | 是 |  |
| `inc` | 是 |  |
| `dec` | 是 |  |
| `<` | 是 |  |
| `>` | 是 |  |
| `<=` | 是 |  |
| `>=` | 是 |  |
<!-- END GENERATED: dsl-builtins-arithmetic -->

## 4. 错误模型

| 异常 | 定义处 | 触发 |
|---|---|---|
| `EDNParseError` | [src/tlosure/edn.py](../../src/tlosure/edn.py) | 解析失败（`ValueError` 子类，带 1 起始行列号） |
| `EvalError` | [src/tlosure/builtins.py](../../src/tlosure/builtins.py) | 一切求值或静态校验失败（未知符号/form、builtin 报错等）的基类 |
| `FuelExhaustedError` | 同上 | fuel 预算耗尽 |
| `EvalDepthError` | 同上 | form 嵌套深度超顶 |

静态校验器 `validate_form(form, builtins, scope)`（[src/tlosure/validate.py](../../src/tlosure/validate.py)）不执行地走遍 form：每个 form 头必须是白名单特殊形式或已注册 builtin，每个符号须在 `scope`（含 `let` / 循环绑定扩展）内可解析，特殊形式的形状必须合法，`recur` 的尾位置规则一并检查；首个违例抛 `EvalError`。**动作位置的校验是宿主业务**（它要点名宿主的动作词汇），不在语言核心里。

## 5. 宿主接入

- 求值入口：`eval_form(form, env, builtins, fuel=DEFAULT_FUEL, max_depth=DEFAULT_MAX_DEPTH)`；加载期先跑 `validate_form` 静态拒绝非法表达式。
- 宿主职责：组装 `builtins`（并入第 3 节通用词汇 + 自有领域词汇）、构造 `env`、按场景给 `fuel`、执行求值产出的 `ActionRequest`。
- tlon 宿主的领域词汇（谓词、动作族、宿主查询注入）与 MemberSpec schema 见 [dsl-reference.md](./dsl-reference.md)。
