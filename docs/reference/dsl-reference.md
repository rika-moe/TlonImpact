# Tlon 行为 DSL 参考手册

每个成员的行为由一份 `behavior.tlon` 描述（Lisp 风格 EDN），由统一解释器 `MemberRuntime`（`src/tlon/agents/member_runtime.py`）执行。本文是该 DSL 的完整语义参考，一切以代码为准；语义权威是 `src/tlon/agents/member_spec.py` 的 `MemberSpec` 数据模型，EDN 只是它的序列化前端。

## 1. EDN 语法速览

解析器：`src/tlon/agents/edn.py` 的 `loads()`。只解析数据，不求值。

| 字面量 | 例子 | 解析结果 |
|---|---|---|
| 空 / 布尔 | `nil` `true` `false` | `None` / `True` / `False` |
| 整数 | `42` `-7` `+3` | `int`（布尔值不被接受为 int） |
| 浮点 | `1.5` `1e3` `-2.5e-2` | `float` |
| 字符串 | `"你好\n"` | `str`，支持转义 `\"` `\\` `\n` `\t` `\r` `\b` `\f` `\uXXXX` |
| 关键字 | `:mail-delivered` | `Keyword`（str 子类，不含前导冒号） |
| 符号 | `react` `=` `content-matches?` | `Symbol`（str 子类） |
| 向量 | `[1 2 3]` | 普通 list —— **数据** |
| 列表 | `(think)` | `Form`（list 子类）—— **代码**，由求值器解释 |
| 映射 | `{:a 1 :b 2}` | dict；必须偶数个表单项，否则报解析错误 |
| 注释 | `; 到行尾` | 跳过；逗号视为空白 |

要点（`src/tlon/agents/edn.py` 模块 docstring）：

- `(...)` 与 `[...]` 语义不同：列表是表达式，向量是数据。`:when`、`:until`、stage、section、action 字段都必须是 `(...)` 形式。
- `Keyword`、`Symbol`、普通字符串是三种不同类型：`:mode :react` 与 `:mode "react"` 下游可区分；`=` 比较时 `:a` ≠ `"a"`（`src/tlon/agents/eval.py` 的 `_value_equal`）。
- 符号可用字符集：字母、数字及 `*+!-_?./<>=`，所以 `addressed-to-me?`、`>=`、`start-all` 都是合法符号。
- 解析失败抛 `EDNParseError`，带 1 起始的行列号。

## 2. MemberSpec 顶层结构

文件必须是一个顶层 map（`MemberSpec.from_edn`）：

```clojure
{:version 1
 :rules [ {...规则一...} {...规则二...} ]}
```

| 键 | 类型 | 必填 | 语义 |
|---|---|---|---|
| `:version` | int | 是 | 只支持 `1`（`MEMBER_SPEC_VERSION`），其他值加载即拒绝 |
| `:rules` | 向量 | 是 | 规则列表，**不得为空**；每项是一个 map |
| `:parameters` | map | 否 | 模板参数声明（见第 8 节），运行时加载器忽略 |
| 其他键 | — | — | 未知键记 warning 后忽略（前向兼容） |

**total 语义**（`MemberSpec.from_dict` docstring）：缺必填字段是错误，不给默认值；类型错误同样拒绝。加载路径 `load_member_spec()`（`src/tlon/agents/loader.py`）：文件不存在抛 `FileNotFoundError`，EDN 非法抛 `EDNParseError`，校验失败抛 `MemberSpecValidationError`——任何失败都使该成员拒绝启动（注册表隔离失败，不影响其他成员）。

## 3. 规则四要素

规则 = `:on` + `:when` + 可选 `:loop` + `:actions`（`MemberRule`）。

```clojure
{:name "respond"
 :on :mail-delivered
 :when (addressed-to-me? msg)
 :loop { ... }            ; 可选；有 loop 即认知规则
 :actions [ ... ]         ; 可选，默认 []
 :fuel 500}               ; 可选
```

| 键 | 类型 | 必填 | 语义 |
|---|---|---|---|
| `:name` | 字符串 | 是 | 规则名，用于日志与报错 |
| `:on` | 关键字 | 是 | 触发事件类型；`:boot` 或 `nil` 表示启动规则（见下） |
| `:when` | 表达式 form | 否 | 纯谓词门控；缺省 = 恒真 |
| `:loop` | map | 否 | 认知会话（见第 6 节）；有它 = 认知规则，无它 = 确定规则 |
| `:actions` | 向量 | 否 | 动作 form 列表；每项必须是非空 form |
| `:fuel` | int | 否 | 本条规则的求值预算；须为正且 ≤ 10000（`MAX_FUEL`），见第 4 节 |

### 3.1 `:on` 可选事件

`:on` 关键字经 kebab-case → `SCREAMING_SNAKE` 映射到 `EventType` 枚举（`src/tlon/base/events/event.py`，转换逻辑在 `member_spec._on_from`）。合法值：

| 类别 | 关键字 |
|---|---|
| 特殊 | `:boot` —— 订阅就绪后运行一次；此时 `msg` / `event` 均为 `nil` |
| 邮件 | `:mail-new`、`:mail-delivered` |
| 成员生命周期 | `:agent-join`、`:agent-leave`、`:agent-started`、`:agent-stopped`、`:agent-error`、`:agent-state-changed`、`:agent-waking`、`:agent-woken`、`:agent-wake-failed`、`:agent-sleeping`、`:agent-idle`、`:agent-start-request`、`:agent-stop-request`、`:agent-spawned` |
| 可观测性 | `:agent-thinking`、`:agent-response`、`:agent-thinking-start`、`:agent-thinking-end`、`:agent-llm-request`、`:agent-llm-response`、`:tool-execute`、`:tool-result`、`:tool-error`、`:tool-execute-start`、`:tool-execute-end` |
| 系统 / 配置 | `:system-startup`、`:system-shutdown`、`:system-error`、`:config-updated` |

约束（`MemberRuntime._on_event`）：**认知规则（带 `:loop`）只在 `:mail-delivered` 上生效**，订阅其他事件会在运行时记 warning 并跳过。

### 3.2 `:when` 门控

- 纯表达式，环境为 `{msg, event, self}`：`msg` 是事件负载中的 `Mail`（非邮件事件为 `nil`），`event` 是事件负载 map，`self` 是 `{"id" ..., "name" ...}`。
- 求值失败（含 fuel 耗尽）安全降级为"不命中"（`MemberRuntime._should_respond`）。
- 确定规则：`:when` 为假则跳过整条规则的 `:actions`。
- 认知规则：`:when` 为假则不入队会话，且该邮件立即标记已读（D6.1）。

### 3.3 命中后的执行顺序

- 认知规则：先跑 `:loop` 会话，会话完成后再跑 `:actions`（`OnCompleteRunner`，此时环境额外绑定 `result` 为最终回复文本）。
- 确定规则：直接逐条求值 `:actions` 并执行产出的 `ActionRequest`。

**同一成员至多一条认知规则**（D6.7，`validate_member_spec`）；超过一条加载即拒绝。

## 4. 求值器

受限求值器：`src/tlon/agents/eval.py` 的 `eval_form()`。

### 4.1 特殊形式（封闭白名单 `SPECIAL_FORMS`）

无 `def`、无 `lambda`、无宏、无可变全局状态；除下列形式外，form 头必须是已注册的 builtin 名。

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

### 4.2 计量与终止

- 每个求值步消耗 1 单位 **fuel**，耗尽抛 `FuelExhaustedError`；form 嵌套深度硬顶 `DEFAULT_MAX_DEPTH = 100`。递归不可能（无法命名函数），`while`/`recur` 表达能力等价图灵机，**终止性由 fuel 保证**。
- fuel 默认值（`src/tlon/agents/eval.py` 顶部常量）：`:when` 门控 200（`DEFAULT_WHEN_FUEL`），动作 1000（`DEFAULT_FUEL`），规则/loop 自声明上限 10000（`MAX_FUEL`，超过即加载拒绝）。
- 声明了 `:fuel` 的确定规则：`:when` 与所有 `:actions` 共享一个计数器；认知规则的门控与完成动作分阶段，各按 `:fuel` 计。`:loop/:fuel` 单独约束 `:until` 与 stage `:if` 的求值。

### 4.3 纯度约束（"DSL 描述，引擎执行"）

- 求值永不产生副作用。动作 builtin（`pure=False`）只校验参数并产出 `ActionRequest` 数据，由引擎在求值结束后执行。
- 环境值对表达式不透明：没有属性/方法访问语法，`Mail` 只能经 builtin 检查。
- map 是数据：键保持字面量，值会被求值——动作选项 map 里可嵌表达式，如 `{:agent (get event :agent_id)}`。
- **动作位置允许的包装形式只有 `when` / `while` / `map`**（`validate_member_action`）：`(when cond (spawn ...) (emit ...))`、`(while [i 0] (< i 3) (emit ...) (recur (inc i)))`、`(map x (forward {:to [x]}) ["a" "b"])`。`if` / `let` / `filter` / `reduce` 不能出现在动作位置；动作的全部参数必须是纯表达式。

## 5. Builtin 完整清单

### 5.1 trigger / 通用纯函数（成员统一注册表 `make_member_builtins` 的纯子集）

可用于 `:when`、动作参数、（经合并后）一切纯位置。环境 `{msg, event, self}`。

| 名称 | 签名 | 语义 |
|---|---|---|
| `addressed-to-me?` | `(addressed-to-me? msg)` | 我的地址在邮件 To 列表；`msg` 为 `nil`（非邮件事件）安全返回 `false` |
| `cc-ed?` | `(cc-ed? msg)` | 我的地址在 CC 列表；同上 nil 安全 |
| `content-matches?` | `(content-matches? target pattern)` | 对 `Mail` 正文或字符串做正则搜索（`re.search`）；`target` 为 `nil` 返回 `false`；正则非法抛错 |
| `contains?` | `(contains? coll item)` | 字符串包含子串，或向量包含元素 |
| `get` | `(get target key [default])` | 读 dict 键，缺键得 `default`（默认 `nil`）；非 dict 目标也得 default，**永不抛错** |
| `=` | `(= a b ...)` | 全等；`Keyword`/`Symbol`/字符串类型互不相等（`:a` ≠ `"a"`）；少于 2 参恒真 |
| `not` | `(not x)` | 逻辑非 |
| `+` `*` | `(+ a...)` `(* a...)` | 任意元算术；拒绝布尔与非数字 |
| `-` | `(- a [b])` | 一元取负，二元相减 |
| `inc` `dec` | `(inc a)` `(dec a)` | ±1 |
| `<` `>` `<=` `>=` | `(< a b)` 等 | 二元数值比较 |
| `sleeping?` `running?` `lazy?` | `(sleeping? agent-id)` 等 | **仅 Adam 可用**（宿主谓词，闭包注入；普通成员的注册表里没有，使用即加载校验失败） |

### 5.2 loop `:until` 谓词（`LOOP_BUILTINS`，环境 `{self, state, limits}`）

`state` = `{:iteration n :error_count n}`，`limits` = `{:max_iterations n :max_consecutive_errors n}`。键缺失安全返回 `false`，不抛错。

| 名称 | 语义 |
|---|---|
| `iteration-limit-reached?` | `state.iteration ≥ limits.max_iterations` |
| `error-limit-reached?` | `state.error_count ≥ limits.max_consecutive_errors` |
| `=` / `not` | 同 5.1 |

### 5.3 stage `:if` 谓词（`STAGE_COND_BUILTINS`，环境 `{state, scratch}`）

`scratch` 是会话循环暂存区（plan/critique stage 写入，见第 6 节）。

| 名称 | 语义 |
|---|---|
| `critique-failed?` | `scratch.critique` 存在且 `passed` 为 `false` |
| `critique-passed?` | `scratch.critique` 存在且 `passed` 为 `true` |
| `plan-exists?` | `scratch.plan` 是非空字符串 |
| `first-iteration?` | `state.iteration == 0`（循环第一轮） |
| `=` / `not` | 同 5.1 |

### 5.4 动作 builtin（`pure=False`，产出 `ActionRequest`，引擎执行）

`:actions` 里的每个 form 头必须是这六个之一（或 `when`/`while`/`map` 包装）。

| 名称 | 签名 | 语义 |
|---|---|---|
| `spawn` | `(spawn prototype [options])` | 经注册表生成一个 contractor；`prototype` 必填字符串，`options` 只允许 `{:name "..."}`；无名时自动生成 `"{prototype}-{随机4位}"` |
| `emit` | `(emit content [to])` | 发邮件；`content` 必填字符串，`to` 为地址字符串向量；**省略 `to` 时回复触发邮件的发件人**（无触发邮件则收件人为空、发信失败仅记日志） |
| `start` | `(start {:agent id :reason s})` | 发布启动请求；`:agent` / `:reason` 均为必填非空字符串 |
| `stop` | `(stop {:agent id :reason s})` | 发布停止请求；参数同上 |
| `start-all` | `(start-all {:reason s})` | 对"声明了 `:on :boot` 的成员"集合逐个发启动请求；**不允许 `:agent`**；需要宿主查询注入——普通成员运行时会抛 `EvalError`（Adam 专用） |
| `forward` | `(forward {:to [...] :cc [...] :subject s})` | 转发触发邮件：`:to` 必填非空地址向量，`:cc` 可选向量，`:subject` 可选字符串；正文取自触发邮件，新邮件以 `forward_of` 链接原邮件；无触发邮件时跳过并记 warning |

## 6. `:loop` 详解（认知会话）

`:loop` 是引擎执行的 ReAct 会话。所有字段均为 total 语义——**以下键全部必填，无默认值**（`_loop_from`）：

```clojure
:loop {:context {:sections [(persona) (protocol) (knowledge) (mail-history 20)]}
       :stages [(think) (act) (critique) (revise {:if (critique-failed?)})]
       :until (or (iteration-limit-reached?) (error-limit-reached?))
       :max-iterations 25
       :max-consecutive-errors 3
       :save-interaction true
       :reflect {:enabled true :interval-minutes 10}
       :fuel 1000}                  ; 可选
```

| 键 | 类型 | 语义 |
|---|---|---|
| `:context` | map | 只含 `:sections`——上下文 section 向量 |
| `:stages` | 向量 | stage form 列表，**不得为空**；每步按序解释执行 |
| `:until` | 表达式 form | 终止谓词，每步 pre-think 求值 |
| `:max-iterations` | int | 迭代硬顶（引擎无条件执行，`:until` 无法覆盖） |
| `:max-consecutive-errors` | int | 连续错误上限 |
| `:save-interaction` | bool | 会话完成时是否把最终回复存入记忆 |
| `:reflect` | map | `{:enabled bool :interval-minutes int}`——周期性后台记忆审计（`ReflectionEngine`：间隔 + 最少交互数双门控，对记忆做整理/增删） |
| `:fuel` | int | 可选；约束 `:until` 与 stage `:if` 的求值，默认 1000，上限 10000 |

### 6.1 `:sections`（`SECTION_NAMES`，4 种）

`persona` 与 `protocol` 进 system prompt；`knowledge` 与 `mail-history` 按向量顺序追加到对话上下文（`ContextBuilder`）。section 名不得重复。

| section | 参数 | 语义 |
|---|---|---|
| `(persona)` | 无 | 成员人格文件（SOUL/IDENTITY/RULES 等）进 system prompt |
| `(protocol)` | 无 | 协作协议文本进 system prompt |
| `(knowledge)` | 无 | 记忆块拼成 `[已知信息]` 用户消息 |
| `(mail-history n)` | 一个非负 int | 取邮箱最近 n 封邮件进对话上下文 |

### 6.2 五个 stage（`STAGE_NAMES`）

stage form：`(name)` 或 `(name {opts})`。所有 stage 支持 `:if <表达式>`（用 5.3 的谓词；条件为假或求值失败都跳过该 stage）。仅 `plan` 额外支持 `:max-steps <正整数>`（默认 5）。其余 opts 键加载即拒绝。

| stage | 语义（实现见 `src/tlon/agents/stages/`） |
|---|---|
| `think` | 一次 LLM 调用，产出本轮响应（进响应链）。失败：记一次错误 + 推进迭代，不发邮件，由 `:until` 后续终止循环 |
| `act` | 执行本轮**全部**工具调用。无工具调用：追加提醒消息并推进迭代；`sendEmail is_complete=true` 与其他工具并发属冲突：追加冲突提示并推进迭代；否则每个工具一个后台任务并发执行。收集半程：`sendEmail` 成功即发出回复邮件（显式 `to` 优先，否则回复触发邮件发件人）；`is_complete=true` 触发会话完成——保存交互、跑 on-complete 的 `:actions`、标记完成 |
| `plan` | 额外一次 LLM 调用生成 ≤ `:max-steps` 步计划；写入 `scratch.plan` 并以 `[执行计划]` 用户消息入上下文。失败软降级（跳过，不记错误） |
| `critique` | 额外一次 LLM 评审：首行 `PASS`/`FAIL`，无法解析按 PASS（宽容默认，永不阻塞主流程）；结论写入 `scratch.critique = {:passed bool :feedback str}`。失败软降级 |
| `revise` | 不调 LLM；把 `scratch.critique.feedback` 以 `[评审反馈]` 用户消息喂回上下文，供下一轮 `think` 修正；无反馈则空操作 |

stage 是"纯变换 + 声明意图"：返回值是 `StageOutcome` 数据（`src/tlon/agents/stages/outcome.py`），由 `LoopDriver._apply_outcome` 统一落地——应用顺序为：上下文追加 → scratch → 计数器/状态 → 工具结果 → 响应链 → 工具派发 → 邮件 → 保存交互 → on-complete 动作 → 完成标记。

### 6.3 循环推进与终止（`LoopDriver.step_session`）

每一步的顺序：

1. **迭代硬顶**：`iteration ≥ :max-iterations` 时直接回复触发邮件"思考时间过长，请简化您的问题。"并结束会话——在任何 `:until` 求值之前。
2. **`:until` 求值**（5.2 谓词）：为真则终止——错误上限触发回复"抱歉，处理邮件时出现了错误。"，否则回复"思考时间过长，请简化您的问题。"。求值失败回退到引擎默认检查（迭代/错误上限），循环必然停止。
3. 按序解释 `:stages`；`think` 失败则本步提前返回。
4. 若 stage 向量不含 `act`（或本轮 act 未推进迭代），引擎自行推进迭代计数，保证硬顶始终生效。

正常的会话完成不经过 `:until`：由 `act` 收集到 `sendEmail is_complete=true` 事件驱动。`:until` 只是兜底。

## 7. 确定性成员的 `:actions`

无 `:loop` 的规则即确定规则：事件命中 → 求值 `:when` → 逐条求值 `:actions` → 执行产出的 `ActionRequest`。

- 环境为 `{msg, event, self}`；**`result` 只在认知规则的完成动作里绑定**——确定规则里引用 `result` 能通过加载校验（作用域含它）但运行时抛"undefined symbol"，整条规则跳过并记 warning。
- 每个动作 form 的求值结果是请求数据：`start-all` 返回请求列表，`while`/`map` 可收集多个请求；非 `ActionRequest` 的值被忽略。
- 求值失败（含 fuel 耗尽）跳过本条规则，不影响其他规则与总线分发。
- 邮件语义（`MemberRuntime`）：自己发出的邮件永不处理并立即标记已读；被 `:when` 拒绝的邮件也标记已读；确定规则命中即读；队列满丢弃的邮件保持未读，留待下次启动时的未读补救扫描（`_scan_unread_mail`）重放。

## 8. `:parameters` 模板（写侧专属）

`behavior.tlon` 可声明顶层 `:parameters` map，并在规则文本里以 `${name}` 引用（`src/tlon/agents/behavior_template.py`）：

```clojure
:parameters {:to {:type :string-list :required true}}
```

| 要点 | 说明 |
|---|---|
| 类型 | `:string` 或 `:string-list`（缺省 `:string`） |
| `:required` | 缺省 `true`；渲染（`render_template`）时缺失必填参数、出现未声明参数、类型不符均抛 `BehaviorTemplateError` |
| 双向校验 | 正文出现的每个 `${var}` 必须在 `:parameters` 声明，反之亦然 |
| 生命周期 | **写侧专属**：实例化时 `${var}` 被渲染为 EDN 字面量后才落盘；运行时加载器看不到 `:parameters`（作为未知键记 warning），也永远见不到 `${...}` |

## 9. `member_type` 派生规则

`member_type()`（`src/tlon/agents/member_spec.py`）：**任一规则带 `:loop` 即 `"react"`（认知成员），否则 `"script"`（确定成员）**。类型是派生值，从不落盘。认知成员由注册表装配完整引擎（LLM、记忆、工具、调度器、反思）；确定成员只装配事件外壳，无 LLM。

## 10. 完整示例

### 10.1 认知型：`deep-thinker`（`src/tlon/prototypes/deep-thinker/behavior.tlon`）

```clojure
{:version 1
 :rules [{:name "respond"
          :on :mail-delivered                                   ; 邮件送达触发
          :when (or (addressed-to-me? msg) (cc-ed? msg))        ; 只理会在 To/CC 里点了我的邮件
          :loop {:context {:sections [(persona) (protocol)      ; system prompt：人格 + 协议
                                      (knowledge)               ; 对话上下文：记忆块
                                      (mail-history 20)]}       ; 对话上下文：最近 20 封邮件
                 :stages [(think) (act)                         ; 想 + 做（ReAct 主循环）
                          (critique)                            ; 评审本轮输出
                          (revise {:if (critique-failed?)})]    ; 仅当评审 FAIL 时把反馈喂回
                 :until (or (iteration-limit-reached?)          ; 兜底终止：迭代或错误到顶
                            (error-limit-reached?))
                 :max-iterations 25                             ; 引擎硬顶（先于 :until 生效）
                 :max-consecutive-errors 3
                 :save-interaction true                         ; 完成时把最终回复存记忆
                 :reflect {:enabled true :interval-minutes 10}}  ; 每 10 分钟级后台记忆审计
         }                                                      ; ← 注意：此处必须有 } 闭合规则 map
         {:name "boot"
          :on :boot                                             ; 启动即跑（声明 boot 集成员身份）
          :actions []}]}                                        ; 无动作也要写空向量或省略
```

> 注：仓库中 `src/tlon/prototypes/deep-thinker/behavior.tlon` 当前版本的规则一 map 少一个闭合 `}`，无法通过 `edn.loads` 解析；上例已补上（上文中单独标注的那行），抄写时以本文为准。

### 10.2 确定型：`relay`（`src/tlon/prototypes/relay/behavior.tlon`）

```clojure
{:version 1
 :parameters {:to {:type :string-list :required true}}          ; 模板参数：实例化时渲染掉
 :rules [{:name "relay"
          :on :mail-delivered                                   ; 邮件送达触发
          :actions [(forward {:to ${to}})]}                     ; 原文转发给参数指定地址（无 :loop → 确定规则）
         {:name "boot"
          :on :boot
          :actions []}]}
```

更复杂的确定型例子见 `src/tlon/agents/adam.py` 的 `ADAM_SPEC_EDN`：boot 时 `(start-all {:reason "boot"})` 拉起全部 boot 成员，`:on :agent-idle` 时 `(stop {:agent (get event :agent_id) :reason "drain"})` 排干空闲成员——展示了选项 map 里嵌表达式与 Adam 专属宿主谓词的用法。

## 11. 常见错误与加载失败行为

| 错误 | 后果（出处） |
|---|---|
| 文件不是合法 EDN / 有尾随内容 | `EDNParseError`（带行列），成员拒绝启动（`edn.loads`） |
| 顶层不是 map、`:rules` 缺失/为空/不是向量 | `MemberSpecValidationError`，拒绝启动（`MemberSpec.from_dict`） |
| `:version` 不是 1 | 同上（仅支持 `MEMBER_SPEC_VERSION = 1`） |
| 缺必填字段（`:name`、`:on`、`:loop` 的全部子键等） | 同上——total 语义，无默认值 |
| 类型错误（int 位置写 bool/string 等） | 同上（bool 永不被接受为 int） |
| 未知 section / stage 名，section 重复，`mail-history` 参数不是非负 int | 同上（`_validate_sections` / `_validate_stages`） |
| stage opts 键非 `:if`（`plan` 可加 `:max-steps`） | 同上（`_validate_stage_opts`） |
| `:when` / `:until` / `:if` 引用未注册 builtin 或作用域外符号 | 同上（`validate_member_spec` 统一静态校验） |
| 一个成员声明多条带 `:loop` 的规则 | 同上（D6.7：至多一条认知规则） |
| `:fuel` 非正数或超过 10000 | 同上（`MAX_FUEL` 硬顶） |
| 未知顶层/规则/loop 键 | **不报错**，记 warning 忽略（前向兼容） |
| `:when` 求值失败 / fuel 耗尽 | 运行时按"不命中"处理，邮件标记已读 |
| 动作求值失败（如确定规则引用 `result`、普通成员用 `start-all`） | 运行时跳过该规则/动作，记日志，不影响其他成员 |
| 认知规则订阅非 `:mail-delivered` 事件 | 运行时记 warning 并跳过（加载不拦） |

加载总原则（`load_member_spec` 与 `validate_member_spec`）：EDN 前端只做结构校验（类型、形状），builtin 作用域校验全在单一校验器 `validate_member_spec`；**任何失败都是整体拒绝启动**，没有"部分可用"的中间态。
