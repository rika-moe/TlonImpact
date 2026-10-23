# tlosure 语言参考

tlosure（[src/tlosure/](../../src/tlosure/)）是 Tlon 行为 DSL 的**语言核心**：一个 EDN 子集（Clojure 兼容子集，[ADR 0001](../decisions/0001-edn-as-dsl.md)、[ADR 0011](../decisions/0011-sci-compatible-subset.md)）加一个燃料计费的受限求值器——每个求值步消耗燃料，任何表达式必然终止（ADR 0011）。它是一门**纯语言**：不依赖任何宿主代码，自身不含任何业务概念（核心命名检验：一个构造能不提及宿主业务概念就解释清楚才配进 tlosure，[ADR 0018 修订](../decisions/0018-dsl-extraction-tlosure.md)）；tlon 是它的第一宿主。

宿主接入模型：宿主调用 `eval_form(form, env, builtins, fuel=..., max_depth=...)`（[src/tlosure/eval.py](../../src/tlosure/eval.py)）时**注入两样东西**——`builtins` 注册表（词汇表，可并入本文第 3 节的通用词汇）与 `env` 环境绑定（符号 → 值的 map）。env 里放什么、求值结果与暂停的 `BuiltinCall` 如何执行和恢复，都是宿主契约，不属于本语言规范。tlon 宿主的领域词汇与 MemberSpec schema 见 [dsl-reference.md](./dsl-reference.md)。

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
- 符号可用字符集：字母、数字及 `*+!-_?./<>=&`（`&` 为变长参数标记，[ADR 0021](../decisions/0021-tlosure-full-functions.md)），所以 `contains?`、`>=`、`start-all` 都是合法符号。
- 解析失败抛 `EDNParseError`，带 1 起始的行列号。

## 2. 求值器

受限求值器：[src/tlosure/eval.py](../../src/tlosure/eval.py) 的 `eval_form()`。

### 2.1 特殊形式（封闭白名单 `SPECIAL_FORMS`）

无 `def`、无宏、无可变全局状态；除下列形式外，form 头必须解析为环境中的闭包（用户函数）或已注册的 builtin 名（分辨顺序：特殊形式 → env → builtin）。`defn` / `fn` 是 [ADR 0021](../decisions/0021-tlosure-full-functions.md) 引入的完整函数能力，语义见 §2.1.1。

<!-- BEGIN GENERATED: dsl-special-forms -->
封闭白名单（13 个）：`quote`、`if`、`when`、`let`、`and`、`or`、`while`、`recur`、`map`、`filter`、`reduce`、`defn`、`fn`。（本区块由 `scripts/gen_reference_docs.py` 生成，勿手改；改了会被 `--check` 打回）
<!-- END GENERATED: dsl-special-forms -->

| 形式 | 形状 | 语义 |
|---|---|---|
| `quote` | `(quote data)` | 恰有一个参数，原样返回语法数据；不校验或求值其内部调用，适合向宿主传递会话配方 |
| `if` | `(if test then [else])` | 2 或 3 参；无 else 且 test 为假时得 `nil` |
| `when` | `(when test body...)` | test 为假得 `nil`；否则依次求值 body，返回最后一个 |
| `let` | `(let [name init ...] body...)` | 绑定向量须偶数项；init 按序求值、后者可见前者 |
| `and` / `or` | `(and a...)` / `(or a...)` | 短路；`and` 返回第一个假值或末值，`or` 返回第一个真值或 `nil` |
| `while` | `(while [name init ...] test body...)` | Clojure 风格有界循环：test 为假结束（返回 `nil`），body 末值即循环值；迭代靠 `recur` |
| `recur` | `(recur v...)` | 只能出现在**最近一层 `while` 的尾位置**（`if` 分支、`when`/`let` 末尾 body、`and`/`or` 末参数继承尾位置）；参数个数须等于 `while` 绑定数；参数先在旧环境求值再同时重绑 |
| `map` | `(map x body coll)` | 对向量逐元素绑定 `x` 求值 body，收集结果向量。**注意参数顺序是绑定、body、集合** |
| `filter` | `(filter x body coll)` | 同上，收集 body 为真的元素本身 |
| `reduce` | `(reduce acc x body init coll)` | 五个参数：累加器名、元素名、body、初值、集合 |
| `defn` | `(defn name [params] body...)` | 在当前环境定义命名函数（任意 body 位置，不限顶层），返回闭包本身；详见 §2.1.1 |
| `fn` | `(fn [params] body...)` | 匿名闭包值，不绑定名字；详见 §2.1.1 |

求真值按 Python 语义：`nil`/`false`/`0`/`""`/`[]` 均为假。

### 2.1.1 函数：`defn` / `fn`（ADR 0021）

语言有完整函数能力：函数是一等值（可作参数传递、作返回值返回），闭包按**引用**捕获定义时环境，支持变长参数与递归。约束不新增任何专门限制——统一由 fuel 与深度顶承担（§2.2）。

- **定义**：`(defn name [params] body...)` 把闭包绑定进当前环境 dict——名字在定义体内立即可见（递归由此成立），同一 body 的后续兄弟 form 也可见（`let`/`when`/`while` body 同理）。`defn` 表达式的值是闭包本身。
- **闭包捕获**：`(fn [params] body...)` 求值为 `Closure` 值，按引用捕获定义处环境。`(let [base 10] ((fn [x] (+ base x)) 5))` → `15`。函数体引用宿主 env 的 `msg`/`event`/`self` 等绑定与在顶层无异。
- **应用**：Symbol 头按「特殊形式 → env → builtin」顺序分辨，env 里的闭包优先于同名 builtin；头也可以是 Form（先求值，必须是闭包）：`((fn [x] (inc x)) 5)` → `6`。env 里解析到非闭包值报 `... is not callable`。
- **参数向量**：`[a b]` 定参，应用时实参个数必须恰等；`[a b & rest]` 变长，`&` 恰在倒数第二位、后随一个符号，多余实参收进 `rest` 向量（无多余时是 `[]`），实参数不得少于定参数。元数不符报 `EvalError`（带函数名）。
- **递归**：直接自引用（`(defn fact [n] (if (< n 2) 1 (* n (fact (dec n)))))`）。深层递归撞 `max_depth`（`EvalDepthError`），指数扇出撞 fuel（`FuelExhaustedError`）——终止性仍由两道闸保证。
- **`recur` 不跨函数边界**（Clojure 语义）：进入函数应用时保存并清空 enclosing loop 上下文，退出时恢复；函数体内的 `recur` 报「recur outside while」，外层 `while` 在函数返回后继续正常工作。静态校验镜像同一规则（fn/defn body 以无 enclosing loop 校验）。
- **静态校验的作用域规则是「先定义后使用」**：body 按序穿线，`defn` 的名字只对其后的兄弟 form 可见；`defn` 体内引用后定义的兄弟函数在加载期被拒（尽管按引用捕获在运行期能解析）。
- **函数体可组合效果调用**：异步驱动在非纯宿主 builtin 的调用处暂停并交出 `BuiltinCall`，宿主返回实际结果后继续求值。闭包和普通表达式使用相同的调用边界。

### 2.1.2 注册表里的闭包词（closure-backed builtin）

`Builtin` 注册表条目有两种实现形状（[src/tlosure/builtins.py](../../src/tlosure/builtins.py)，`fn` 与 `closure` 恰居其一）：

- **宿主形**（`fn`）：Python 函数，收 `(env, *求值后参数)`；其内部计算**不计费**——宿主词汇自己的开销属于宿主资源域。
- **闭包形**（`closure`，经 `Builtin.from_closure` 构造）：条目携带一个 `Closure` 而非宿主代码——**数据定义的词**。求值器命中该条目时走与 `defn`/`fn` 闭包完全相同的应用路径（`_apply`）：元数检查、参数绑定、body 逐 form 经求值器递归——**body 的每一步计入调用方的 fuel 与深度预算**。这是与宿主形的实质语义差：数据定义的词留在 fuel 纪律内，不会成为预算黑洞。

闭包形条目的捕获 env 就是注册时给定的那个：宿主注册**空 env** 意味着 body 只见自己的参数绑定（零注入），调用方 env 里的符号对 body 不可见；body 内的词（form 头）照常经**调用方的注册表**解析。`.pure` 标记两种形状通用，宿主的纯度分区（如社会政策使用纯注册表）不经区分地工作。静态校验无需特殊处理：词名在注册表里即通过，元数由运行时的闭包应用兜住（与宿主 builtin 同）。这属于通用语言能力；Tlon 的 Capability 目录机制已删除。

### 2.2 计量与终止

递归与有界循环表达能力等价图灵机（ADR 0021 起函数可命名、可递归），**终止性由 fuel + 深度顶保证**——任何表达式必然在有限步内停机。

**计费模型**（一步 = 1 单位 fuel，精确定义）：

- 一步 = 一次**节点求值**：字面量自求值、符号解析、向量/map 的每个元素、一次 builtin 调用、特殊形式中**实际被求值**的每个子表达式，各计 1 步；
- 未走的分支不计费：`if`/`when` 未命中的分支、`and`/`or` 短路掉的后续参数消耗为 0；
- builtin **内部**的计算不计费——宿主词汇自己的开销属于宿主的资源域，不属于语言计量；**闭包形条目（§2.1.2）相反**：body 是普通求值，计入调用方预算；
- 循环（`while`/`recur`、`map`/`filter`/`reduce`）的每次迭代、函数应用的每个 body form 与嵌套调用，都经过节点求值，因此必然被预算包住。

**预算边界**：

- `fuel` 必须落在 `[0, MAX_FUEL]`，越界在入口处抛 `ValueError`——`MAX_FUEL` 是语言级硬顶（由 `Evaluator` 构造器强制），任何入口都无法发起一次实质无界的求值；
- `max_depth` 至少为 1，否则同样 `ValueError`；
- 耗尽抛 `FuelExhaustedError`，异常携带 `budget` 属性（本次求值的启动预算）供宿主结构化记日志/打点；嵌套超顶抛 `EvalDepthError`。

**预算分摊**：一个 `Evaluator` 实例 = 一个预算计数器。单表达式走 `eval_form`；宿主若要让多个表达式共用一个总预算（如一个模块入口及其调用的函数共享 `:fuel`），直接构造 `Evaluator` 连续 `eval`——每次调用从同一个计数器扣。共享还是分段由宿主决定。

计量常量（[src/tlosure/eval.py](../../src/tlosure/eval.py) 顶部）：默认预算 `DEFAULT_FUEL = 1000`；硬顶 `MAX_FUEL = 10000`；嵌套深度硬顶 `DEFAULT_MAX_DEPTH = 100`。

### 2.3 效果调用与 `BuiltinCall`

语言内核不直接执行 I/O。宿主注册的效果 builtin 标记 `pure=False`；同一个 generator 内核供 `Evaluator.eval` 同步驱动和 `Evaluator.eval_async` 异步驱动使用，两者共享燃料与深度计量。

同步驱动直接调用 builtin，返回其结果。异步驱动在每个效果调用处产出 `BuiltinCall`，宿主执行并将实际结果送回表达式，因此 `let`、`if`、函数和循环都能组合 I/O 返回值。未选分支不触发调用。纯 registry 不含效果词，仍按构造保证不发生外部效果。

环境对象没有属性/方法访问语法，只能经宿主注册的 builtin 读取；map 的键保持字面量，值会被求值。

效果执行、持久化与恢复由宿主负责，语言核心不依赖 Tlon。宿主已经提交的效果不会因后续求值失败而自动回滚；通用词汇、fuel 和深度限制也不能替代宿主的 I/O 超时与权限检查。Tlon 的具体契约见 [runtime](../design/runtime.md) 和 [ADR 0043](../decisions/0043-newspaper-member-workflow.md)。

## 3. 通用词汇

纯（pure）、total 的函数注册表，宿主可原样并入自己的词汇表（[src/tlosure/builtins.py](../../src/tlosure/builtins.py)）。两个注册表全部 `pure=True`——对着它们求值按构造无副作用。

### 3.1 `CORE_BUILTINS`（通用谓词与访问器）

<!-- BEGIN GENERATED: dsl-builtins-core -->
`CORE_BUILTINS`（17 个，按源码定义顺序）：（本区块由 `scripts/gen_reference_docs.py` 生成，勿手改；改了会被 `--check` 打回）

| 名称 | pure | 说明 |
|---|---|---|
| `assert` | 是 | Reject invalid data without introducing a host effect. |
| `integer?` | 是 | Test for an integer value, excluding booleans. |
| `json-encode` | 是 | Encode strict JSON data, accepting EDN keywords only as object keys. |
| `assoc` | 是 | Return a map/vector copy with key-value pairs applied in order. |
| `dissoc` | 是 | Return a map copy without the supplied keys; missing keys are harmless. |
| `conj` | 是 | Append to a vector, or add two-element entry vectors to a map copy. |
| `keys` | 是 | Return map keys in insertion order. |
| `vals` | 是 | Return map values in insertion order, matching ``keys``. |
| `=` | 是 |  |
| `not` | 是 |  |
| `contains?` | 是 |  |
| `get` | 是 | Read ``key`` from a dict payload; missing keys yield ``default``. |
| `str` | 是 | Concatenate arguments into one string. |
| `split` | 是 | Split a string on a separator into a vector of strings. |
| `join` | 是 | Join a vector of strings with a separator into one string. |
| `substring` | 是 | ``s[start:end]`` — Python slice semantics, total by construction. |
| `count` | 是 | Item count of a vector / map / string; ``nil`` counts as 0. |
<!-- END GENERATED: dsl-builtins-core -->

`=` 是任意元比较，按 `_value_equal` 判定：`Keyword` / `Symbol` / `str` 三种字符串类型互不相等。`get` 是 total 的——目标不是 dict 也返回 `default`，永不对畸形负载抛错；`contains?` 对非向量/字符串目标抛 `EvalError`。

### 3.2 `ARITHMETIC_BUILTINS`（算术）

任意元算术与二元数值比较；拒绝布尔与非数字。

<!-- BEGIN GENERATED: dsl-builtins-arithmetic -->
`ARITHMETIC_BUILTINS`（10 个，按源码定义顺序）：（本区块由 `scripts/gen_reference_docs.py` 生成，勿手改；改了会被 `--check` 打回）

| 名称 | pure | 说明 |
|---|---|---|
| `+` | 是 |  |
| `-` | 是 |  |
| `*` | 是 |  |
| `/` | 是 | Left-fold float division: ``(/ a b c)`` = ``a / b / c``. |
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
| `FuelExhaustedError` | 同上 | fuel 预算耗尽（携带 `budget` 属性 = 本次启动预算） |
| `EvalDepthError` | 同上 | form 嵌套深度超顶 |

静态校验器 `validate_form(form, builtins, scope)`（[src/tlosure/validate.py](../../src/tlosure/validate.py)）不执行地走遍 form：每个 form 头必须是白名单特殊形式、已注册 builtin 或 `scope` 内的用户函数名，每个符号须在 `scope`（含 `let` / 循环绑定、`defn`/`fn` 参数与按序穿线的 `defn` 名字扩展）内可解析，特殊形式的形状必须合法，`recur` 的尾位置规则与「不跨函数边界」一并检查；首个违例抛 `EvalError`。宿主负责提供本次可用的 builtin 注册表；Tlon 的模块加载器对完整函数体进行静态校验，没有额外的动作位置语法。

## 5. 宿主接入

- 求值入口：`eval_form(form, env, builtins, fuel=DEFAULT_FUEL, max_depth=DEFAULT_MAX_DEPTH)`；加载期先跑 `validate_form` 静态拒绝非法表达式。多表达式共享一个总预算时用公开的 `Evaluator`（见 §2.2 预算分摊）。
- 宿主职责：组装 `builtins`（并入第 3 节通用词汇 + 自有领域词汇）、构造 `env`、按场景给 `fuel`（不得越过 `MAX_FUEL` 硬顶）、执行暂停的 `BuiltinCall` 并返回调用结果。
- tlon 宿主的领域词汇（普通 Operation、物化事件输入）与 MemberSpec schema 见 [dsl-reference.md](./dsl-reference.md)。

## 源码模块（P1）

`edn.loads_all` 解析多个顶层 form；`ModulePackage.from_sources(sources, builtins)` 只接受宿主提供的 `.tlon` 源码映射，不自行读盘。`ns` 声明与文件路径一一对应，`:require [namespace :as alias]` 导入导出函数，`:export` 显式导出；`defn` 为普通函数，顶层 `def` 只接受字面常量。模块加载不运行函数体或宿主调用。`exports` 为限定名到闭包的映射，`entry` 要求固定单参数；闭包在当前求值器共享 fuel/depth。完整示例见[指南](../guides/writing-a-module-program.md)。

## 纯数据校验与 JSON 编码

`(integer? value)` 仅对整数返回 true，布尔和浮点数返回 false。`(assert condition message)` 在条件为假时抛 EvalError，否则返回条件值。`(json-encode value)` 用标准 JSON 编码严格数据，接受字符串/有限数字/布尔/nil、列表和 map，Keyword 仅可用作 map key；拒绝符号、代码 Form、NaN、归一化冲突键、循环和超过 100 层的嵌套。这些词没有宿主效果，不能用来执行字符串代码。
