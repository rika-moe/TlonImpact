# ADR 0011：DSL 语义演进锚定 SCI 兼容子集（为 JVM 迁移铺路）

> 阅读定位：本文保留当时的选择与理由；当前设计见 [dsl](../design/dsl.md)。当前适用范围：SCI 语义锚定方向；函数能力按 ADR 0021 扩展，不表示已迁移 JVM。完整关系见 [决策索引](README.md)。

- 状态：已接受（2026-08-28，方向讨论拍板）
- 相关：[0001-edn-as-dsl.md](0001-edn-as-dsl.md)（DSL 语法选型）、[../design/dsl.md](../design/dsl.md)（DSL 整体设计）

## 背景

Tlon 将来可能迁移到 Java 技术栈。DSL 是 EDN 子集（ADR 0001），表层语法与 Clojure 高度同源——`.tlon` 文件今天就能被 Clojure reader 原样解析——但语义层存在已知的异形点：`while`/`recur`（Clojure 是 `loop`/`recur`）、显式绑定变量的 `map`/`filter`/`reduce`（Clojure 用 lambda）、fuel 计量（Clojure 无此概念）。

JVM 生态里存在为"在宿主程序里安全求值不可信 Clojure 代码"而生的标准答案 [SCI（Small Clojure Interpreter）](https://github.com/babashka/sci)：`:namespaces` 白名单 + `:deny` 黑名单机制，与我们按语境切分的四份 builtin 注册表（`builtins.py`）在设计上同构，已被 babashka、Clerk、Joyride 等项目验证。经逐条核对，DSL 现有的全部语义设计在 Clojure/SCI 里都有对应实现，且多数以更自然的形态成立：

| 现有 DSL 特性 | SCI/Clojure 对应 |
|---|---|
| `if`/`when`/`let`/`and`/`or` 白名单 | Clojure 原生形式，零成本 |
| `while`/`recur` | 宏展开到 `loop`/`recur`；尾位置合法性由宏展开期静态检查（强于现在的运行时检查） |
| 显式绑定 `map`/`filter`（`(map x body coll)`） | 宏展开到 `(map (fn [x] body) coll)` |
| 四份语境注册表 | SCI `:namespaces` 按语境配置；Adam 专属谓词 = 只注入其 context 的私有命名空间 |
| 动作即数据（builtin 产出 `ActionRequest`） | builtin 函数返回普通 map；同像性使"求值结果可落盘/diff/审计"成为语言默认行为 |
| 加载期 total 校验 | spec/malli 校验 spec 骨架 + SCI 白名单约束表达式层 |
| fuel 计量停机 | **唯一直接映射不掉的一项**。迁移时用"循环宏强制有界（上限为宏参数）+ wall-clock 超时兜底"替代；确定性步数计量语义变弱，届时需重新设计取舍 |

## 决策

1. **DSL 语义演进锚定"SCI 可执行的 Clojure 子集"。** 新增语法（特殊形式、求值规则、字面量种类）之前必须先回答：在 SCI 里它是宏还是函数？展开/求值后是什么形态？宏抹不平、必须改解释器内核才能表达的异形语义，不引入。
2. **上表三个异形点登记为已知迁移映射项**，保留现状不追溯修改——它们在 Python 实现里工作正常，且有宏级映射方案兜底。
3. **现在不换解释器。** SCI 无 Python 运行时；现在就换的三条路均不成立（见备选方案）。迁移 Java 时换的是求值器，不是语言——`.tlon` 文件本身就是迁移资产。
4. **迁移启动前做一个 spike 验证**：用 bb/SCI 读入现有 `.tlon` 样例（archivist、relay 等），配 3 个兼容宏（`while`、`map*`、`filter*`）+ mock builtin 命名空间，验证原样可执行。spike 不进主代码库，发现宏抹不平的特性时回到本条决策修订。

### 明确不做

- 不内嵌 JVM（JPype/Py4J/GraalVM）
- 不引入 babashka 子进程依赖
- 不做 Python/Clojure 双实现
- 不现在取消 fuel——它是当前 Python 实现里图灵完备 `while`/`recur` 的停机保障，取消它必须先有替代设计

## 备选方案

**现在就换成 SCI（babashka 子进程）。** DSL 求值不是纯计算：builtin 谓词经 `ScriptHostQueries` Protocol 闭包同步读取 Python 宿主状态（`builtins.py`），env 里绑定的是活的 `Mail` 对象。子进程化意味着每次规则触发都是一次 RPC（宿主状态序列化往返或双向桥），外加 bb 二进制部署依赖和 fuel 语义丢失——换来的是零行为变化。复杂度全部前置，收益全部后置，否决。

**现在就换（JVM 内嵌 / Basilisp）。** 内嵌 JVM 是在迁移之前先背上 Java 的全部运维成本；Basilisp 没有 SCI 的沙箱模型（无 `:namespaces` 白名单），用它就得自造沙箱，等于回到自研求值器的原点还多一层依赖。均否决。

**DSL 自由演进、不锚定。** 迁移时才发现语义鸿沟，`.tlon` 资产需要逐文件改写，风险集中在迁移当天爆发。锚定约束的成本是每次加语法时多回答一个问题，收益是迁移面收敛为"换求值器"，否决自由演进。

**直接以全量 Clojure 语义为目标（现在取消 fuel、引入 fn/def）。** 放弃 ADR 0001 的核心论证（作者含 LLM、不可信输入、total 校验），否决。

## 后果

**换得的收益：**

- `.tlon` 文件成为迁移资产而非迁移负担：届时换求值器，不换语言。
- DSL 的表层就是 Clojure，LLM 书写时吃的是训练数据里最大的 Lisp 语料，无需为私有方言额外纠错。
- 迁移风险前置消化：语义鸿沟在每次演进时就被约束拦住，而不是积累到迁移当天。

**接受的代价：**

- DSL 表达力上限被锁定在"宏可模拟"的范围内；未来某个强需求若需要解释器内核级语义（如 delimited continuation），要么放弃需求，要么修订本 ADR。
- fuel 的确定性停机保证在迁移后会弱化为"有界循环 + 超时"，这是已登记的技术债，迁移设计阶段必须正面处理（倾向方案：循环宏强制显式上限，图灵不完备换终止性）。
- 锚定是约定而非机制：靠 code review 和本条决策执行，没有自动化检查（DSL 变更频率低，人工把关足够；若未来 DSL 改动变多，可考虑加一条 CI 检查项）。
