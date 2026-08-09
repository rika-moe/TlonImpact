# MemberSpec —— 统一成员行为规约

> 状态：设计定稿（2026-08-08 讨论收敛）· 阶段 A/B/C 已实施（C 完成于 2026-08-08），D–F 待实施
>
> 关联：本设计取代旧的双 spec 架构——AgentSpec（`behavior.tlon`，ReactAgent）与 ScriptSpec（`script.tlon`，ScriptMember）统一为一份 **MemberSpec**；HumanAdapter（第三类成员）暂不并入，位置已留。旧版设计全文（含历史工作记录）见 git 历史，本文件为现行权威。

## 核心理念

**成员的行为是数据，不是代码。**

ReactAgent（认知）与 ScriptMember（确定性）在代码里早已共享：Member 抽象、邮箱、生命周期（唤醒/休眠/drain）、spawn 预算，以及"求值纯、动作变数据、引擎执行"的动作机制——on-complete 的 `(spawn)`/`(emit)` 与 script 的 `(start)`/`(stop)`/`(forward)` 是同一套 ActionRequest。二者唯一的结构差异是**认知循环（loop）**。MemberSpec 把两者收进一份规约：

- 规约存储在文件中，一份规约的解释执行即一个成员的运行；
- 一份规约可被 LLM 生成、被 agent 改写——**环形废墟：agent 创造 agent**；
- 一份规约可触发另一份规约的解释执行（`spawn`）；
- 一个成员可**混排**确定性规则与认知规则：低成本路径（脚本）挡在前面，只有难的交给 LLM（灵活）——**cost routing** 是统合的核心收益。

## 铁律的修订（2026-08-08）

原设计以"图灵不完备（刻意）"换取三样保证：保证停机、可静态分析、自我改写安全（先例：Bazel Starlark）。本次修订**允许循环与图灵完备**，以新机制换回旧保证：

| 旧保证 | 新机制 |
|--------|--------|
| 保证停机（语言无循环） | **fuel 即停机保证**：每求值步耗 1 fuel，耗尽抛 `FuelExhaustedError`；规约自报 `:fuel` 预算，引擎全局硬顶（LLM 写的 spec 不能超） |
| 可静态分析（证明停机） | **语法校验 + 资源有界**：`validate_form` 仍查形式白名单/符号作用域/动作纯度，但不承诺终止；LLM 写 spec 走 staging + 人工批准闸 |
| 自我改写安全（激活前完整验证） | **声明式资源预算 + 信任分级**：spec 自报燃料，引擎封顶；trusted（包内/手写）/ staging（LLM 写，未批准）分级保留 |

**继续成立的不变式**：

- **求值纯、动作变数据、引擎执行**：循环是求值期内的纯计算，不产生副作用；`(while ... (emit ...))` 只是往 collector 里多收集 ActionRequest，动作仍在求值完成后由引擎逐条执行。
- **认知循环由引擎执行**：session 的 stage 迭代（think/act/plan/critique/revise + until）仍属引擎——DSL 循环是同步、fuel 有界、**永不 await** 的，不威胁 asyncio 调度器；需要调度器交织的认知会话仍由引擎掌管。
- **语言核心仍小**：语法给控制流（`while`/`recur`），数据操作走 builtin（`map`/`filter`/`reduce`）；不引入 `fn`/`def`/宏/可变全局状态。

## 统一形态

**规则 = `:on` + `:when` + 可选 `:loop` + `:actions`**——命中则先跑 loop（若有），完成后跑 actions（若有）；无 loop 就只跑 actions。`:trigger`、`:on-complete`、`:rules` 三概念全部收敛进规则记录。

```clojure
;; MemberSpec —— 一份行为规约，覆盖认知与确定性
{:version 1
 :rules [{:name "auto-optout"                       ; 确定性规则：低成本路径，零 LLM
          :on :mail-delivered
          :when (content-matches? msg "退订")
          :actions [(emit "已为您处理退订" [])]}
         {:name "respond"                           ; 认知规则：其余全部走 LLM 会话
          :on :mail-delivered
          :when (and (addressed-to-me? msg)
                     (not (content-matches? msg "退订")))   ; deflection：用 :when 排除便宜路径已处理的
          :loop {:context {:sections [(persona) (protocol) (knowledge) (mail-history 20)]}
                 :stages [(think) (act)]
                 :until (or (iteration-limit-reached?) (error-limit-reached?))
                 :max-iterations 10
                 :fuel 5000}
          :actions [(emit "本轮处理完毕" [])]}]}
```

（v1 认知规则只挂邮件事件——见决策 8。）

## 设计决策（已收敛）

| # | 决策 | 说明 |
|---|------|------|
| 1 | 先语义，后语法 | MemberSpec 是类型化 dataclass，语义唯一权威；EDN 只是序列化前端 |
| 2 | 认知循环由引擎执行 | stage 是白名单枚举、Python 实现、可测试的语义单元；DSL 组合 stage，不发明迭代原语 |
| 3 | 允许循环与图灵完备（本次修订） | `while`/`recur` 进语言，fuel 有界；见"铁律的修订" |
| 4 | 单一 Lisp 语法 | 不做 YAML 前端；人类与 LLM 写同一种 s-expr（EDN） |
| 5 | spawn 互触发 | on-complete 时触发另一份规约的解释执行；全局深度/速率限制 |
| 6 | spec 是 total 的 | 文件即行为的全部；缺失/解析失败/不完整→拒绝启动（registry 记 `agent_start_failed`），其余成员不受影响 |
| 7 | 成员可声明 `:on :boot` / `:on :mail-delivered` | 唤醒粒度与启动集合从 spec 派生；亚当退化为 boot+drain（见"唤醒与生命周期"） |
| 8 | `:context` 只处理邮件事件（v1） | 认知规则只挂 `:on :mail-delivered`；context 依赖 msg（邮件形状） |

## 语言设计

**L0 — EDN 数据字面量**（`agents/edn.py`）：map / vector / keyword / string / number / bool；`(...)` 解析为 Form（代码），`[...]` 为 list（数据），两者类型可区分。

**L1 — 求值器**（`agents/eval.py`，本次修订）：

- 特殊形式白名单：`if` / `when` / `let`（vector 顺序绑定）/ `and` / `or` + **`while` / `recur`**（新）+ **`map` / `filter` / `reduce`**（新，显式绑定元素变量，无需 `fn`）；无 `fn`/`def`/宏；
- **`while` = Clojure loop/recur 风格**：状态经绑定向量 + `recur` 显式流转；无 recur 的 body 只跑一次；`recur` 只许在 while body 尾位置（`if` 分支、`when`/`let` 末尾 body、`and`/`or` 最后参数继承尾位置），静态校验拒绝其余位置；
- 内置谓词分三类，全部 pure：
  - 邮件谓词：`addressed-to-me?` / `cc-ed?` / `content-matches?` / `contains?` / `=` / `not`；
  - 宿主谓词（闭包注入，查 registry 只读接口）：`sleeping?` 等；
  - stage 条件：`iteration-limit-reached?` / `error-limit-reached?` / `critique-failed?` / `critique-passed?` / `plan-exists?` / `first-iteration?`；
- 算术 builtin（fuel 计量，循环与数据变换需要）：`+` / `-` / `*` / `inc` / `dec` / `<` / `>` / `<=` / `>=`；
- 动作（`Builtin(pure=False)`，求值只收集 ActionRequest，引擎执行）：`spawn` / `emit` / `start` / `stop` / `start-all` / `forward`（`start-all` 需注册表宿主）；动作位可为 `when` / `while` / `map` 包裹（`(while [i 0] (< i 3) (emit ...) (recur (inc i)))`——循环内动作只是多收集请求）；`save-interaction` 是 loop 配置位而非动作；
- **fuel**：每求值步耗 1，耗尽抛 `FuelExhaustedError`；规则自报 `:fuel` 预算——确定性规则 when+actions **共享一个合并预算**（:when 跑飞挤占动作额度），未设时 :when 独立 200 步、每条动作 1000 步，互不干扰；认知规则 when（gate）与完成动作（会话完成）时间分离，各以 `:fuel` 为 cap；引擎全局硬顶 `MAX_FUEL=10000`，超限拒绝启动。

## 规则语义

- **同事件多条规则全部执行**（声明顺序），单条失败隔离（求值异常与动作失败只记日志，不影响其余规则与成员）；
- **deflection 用 `:when` 排除**（v1 默认，无短路/优先级机制；未来需要再加）；
- **会话身份**：一条认知规则命中一个事件 = 一个 session（引擎职责，DSL 不描述）；
- 纯度不变式：`:when` 条件位只许纯谓词；动作位只许动作 Form——含 `when` / `while` / `map` 包裹，其中条件/测试/绑定初始化纯、动作承载位为动作；循环内的动作只是多收集 ActionRequest。

## 唤醒与生命周期（决策 7 的推论）

- 成员的 `:on` 声明 = **唤醒粒度**（沉睡期间无法求值 `:when`，所以唤醒只能精确到事件类型）；
- `:when` = **唤醒后的过滤**；
- 有 `:on :boot` 规则的成员 = 启动时拉起（取代 `activation="always"` 配置）；
- **亚当**（PID 1）退化为 boot + drain 两条策略线；wake 规则已退休（阶段 E）：wake-on-mail 移入注册表，按成员 spec 的 `:on :mail-delivered` 派生（mechanized policy），`activation` 字段已废弃。

## 安全闸

- 规约经数据化 schema 校验才能激活；缺失/不完整拒绝启动（total，决策 6）；
- LLM 生成的规约进 staging，默认不授予 shell 等高危工具，需用户显式批准；
- spawn 链有全局深度/速率限制；工具权限不可升格（只从 prototype 库 spawn）；
- **fuel 预算**：规约自报 + 引擎硬顶（见"铁律的修订"）。

## 可观测性要求

- 每次解释执行在 trace 中记录生效规约的版本哈希；
- 日志记录 DSL 决策点（规则命中/未命中及原因）；
- 每个 DSL 解释步骤对应一个 span；fuel 耗尽与 `:when` 失败有独立日志。

## 原型库（统一为 MemberSpec 形态）

Prototype = MemberSpec + 人格模板 + 工具白名单，目录形式；实例化是纯拷贝（total：prototype 不再充当 merge base）。

```
prototypes/
├── deep-thinker/     # 显式 stage 程序：think/act/critique/revise（认知规则）
├── fast-responder/   # 迭代少、context 精简（认知规则）
├── archivist/        # reflect 频繁、记忆工具全开（认知规则）
└── relay/            # 纯转发者（确定性规则，无 loop → 无 LLM）
```

迁移期 `behavior.tlon`（AgentSpec）与 `script.tlon`（ScriptSpec）两种形态并存，阶段 B/C 统一后都进 `rules` 形态——**已落地（2026-08-08）**：双格式已删除，全成员单一 `behavior.tlon` rules 形态。

## 动态 Agent Registry（保留，身份与运行时分离）

- **Persona（持久）**：`~/.tlon/{id}/` 下的人格、记忆、配置；
- **Activation（瞬态）**：主循环、容器、session，按需激活、闲置回收；
- 居民（resident）与临时工（contractor）身份不变；孤儿 reparent、完工即弃、drain 语义不变；
- spawn 预算（全局 `max_activations` / 深度 `max_spawn_depth` / 会话配额 `session_spawn_quota`）不变。

## 操作系统类比（灵感，非规约）

| Tlon | 操作系统 |
|------|---------|
| MemberSpec 文件（rules / loop / actions） | 可执行文件 + systemd unit（声明触发事件） |
| Activation（解释执行） | 进程 |
| AgentRegistry | 内核 + 进程调度器 |
| 规则的 `:on` 声明 → 唤醒粒度 | systemd socket activation |
| 燃料预算 | RLIMIT_CPU / cgroup |
| 信任分级（trusted / staging） | 代码签名 |
| 混排认知+确定性规则 | 同一份配置里既有前台服务又有钩子脚本 |

## 路线图（阶段 A–F）

| 阶段 | 内容 | 检验 | 状态 |
|------|------|------|------|
| A | **语义等价**：AgentSpec→单条认知规则、ScriptSpec→规则组；写等价性测试 | 断言过时形态的红测试删除；新等价性测试覆盖映射 | ✅ |
| B | **spec 层合并**：MemberSpec schema（规则带 `:on/:when/:loop/:actions`）、loader/validator/EDN 前端合一；`runtime` 暂留 | 行为不变，四件套通过 | ✅ |
| C | **运行时合一**：LoopDriver 与 ScriptMember 收敛为单一解释器；`runtime`/`member_type` 变派生值 | 删除重复编码，行为不变 | ✅ 2026-08-08 |
| D | **破铁律**：求值器加 `while`/`recur` + `map`/`filter`/`reduce` + fuel 停机保证 + 规约自报 `:fuel`；认知循环不动 | 最险，放统一之后做，单点变更 | ✅ 2026-08-09 |
| E | **唤醒/启动派生**：从 `:on` 声明推 wake 与 boot 集，亚当 wake 规则退休 | 与 D 无耦合，可并行 | ✅ 2026-08-09 |
| F | **能力扩张 + 狗粮**：混排成员实测（退订 deflection）、统一 prototype/writePrototype、文档与测试朝新架构对齐 | 真实冒烟 | ✅ 2026-08-09 |

> 阶段 7d 的 6 份居民 behavior.tlon 在阶段 A 逐份翻译为"一条认知规则"，作为等价性测试的输入。

## 明确不做（v1）

- `fn`/`def`/宏、可变全局状态——语言核心仍小，函数式组合走 builtin（`map`/`filter`/`reduce`）；
- 认知循环的自由拓扑（完整 stage 机）——仍以白名单 stage 枚举回应（7c 的 plan/critique/revise 是这条路径的第一次兑现）；
- `:context` 泛化到非邮件事件；
- 规则短路/优先级机制（v1 默认全部执行 + `:when` 排除）；
- HumanAdapter 规约化（第三类成员，位置已留，后话）。
