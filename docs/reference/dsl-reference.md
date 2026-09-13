# Tlon 行为 DSL 参考手册

每个成员的行为由一份 `behavior.tlon` 描述（Lisp 风格 EDN），由统一解释器 `MemberRuntime`（[src/tlon/agents/member_runtime.py](../../src/tlon/agents/member_runtime.py)）执行。本文是该 DSL 的完整语义参考，一切以代码为准；语义权威是 [src/tlon/agents/member_spec.py](../../src/tlon/agents/member_spec.py) 的 `MemberSpec` 数据模型，EDN 只是它的序列化前端。

## 1. EDN 语法速览

解析器：[src/tlon/agents/edn.py](../../src/tlon/agents/edn.py) 的 `loads()`。只解析数据，不求值。

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

要点（[src/tlon/agents/edn.py](../../src/tlon/agents/edn.py) 模块 docstring）：

- `(...)` 与 `[...]` 语义不同：列表是表达式，向量是数据。`:when`、`:until`、stage、section、action 字段都必须是 `(...)` 形式。
- `Keyword`、`Symbol`、普通字符串是三种不同类型：`:mode :react` 与 `:mode "react"` 下游可区分；`=` 比较时 `:a` ≠ `"a"`（[src/tlon/agents/builtins.py](../../src/tlon/agents/builtins.py) 的 `_value_equal`）。
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

**total 语义**（`MemberSpec.from_dict` docstring）：缺必填字段是错误，不给默认值；类型错误同样拒绝。加载路径 `load_member_spec()`（[src/tlon/agents/loader.py](../../src/tlon/agents/loader.py)）：文件不存在抛 `FileNotFoundError`，EDN 非法抛 `EDNParseError`，校验失败抛 `MemberSpecValidationError`——任何失败都使该成员拒绝启动（注册表隔离失败，不影响其他成员）。

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

`:on` 关键字经 kebab-case → `SCREAMING_SNAKE` 映射到 `EventType` 枚举（[src/tlon/base/events/event.py](../../src/tlon/base/events/event.py)，转换逻辑在 `member_spec._on_from`）。合法值：

<!-- BEGIN GENERATED: dsl-on-events -->
合法值共 36 个（`:boot` + 全部 `EventType` 成员，转换逻辑 `member_spec._on_from` / `_on_to_str`）：（本区块由 `scripts/gen_reference_docs.py` 生成，勿手改；改了会被 `--check` 打回）

| `:on` 关键字 | 事件值 | 说明 |
|---|---|---|
| `:boot` | — | 启动规则：映射 `None`，订阅就绪后运行一次，`msg` / `event` 均为 `nil` |
| `:mail-new` | `mail.new` | A mail was sent (pre-delivery) |
| `:mail-delivered` | `mail.delivered` | A mail landed in one recipient's inbox |
| `:mail-endorsed` | `mail.endorsed` | A delivered mail endorses another mail (reputation signal, ) |
| `:agent-join` | `agent.join` |  |
| `:agent-leave` | `agent.leave` |  |
| `:agent-thinking` | `agent.thinking` |  |
| `:agent-response` | `agent.response` |  |
| `:agent-started` | `agent.started` |  |
| `:agent-stopped` | `agent.stopped` |  |
| `:agent-retired` | `agent.retired` | Retirement ceremony fact: handover inventory + farewell outcome () |
| `:agent-error` | `agent.error` |  |
| `:agent-state-changed` | `agent.state_changed` |  |
| `:agent-waking` | `agent.waking` |  |
| `:agent-woken` | `agent.woken` |  |
| `:agent-wake-failed` | `agent.wake_failed` |  |
| `:agent-sleeping` | `agent.sleeping` |  |
| `:agent-start-request` | `agent.start_request` |  |
| `:agent-stop-request` | `agent.stop_request` |  |
| `:agent-idle` | `agent.idle` |  |
| `:agent-spawned` | `agent.spawned` |  |
| `:agent-noisy` | `agent.noisy` | A member is generating social noise (mail burst / broadcast storm) |
| `:mail-orphan-scan` | `mail.orphan_scan` | Periodic orphan-mail (unanswered thread root) scan statistics |
| `:agent-thinking-start` | `agent.thinking_start` |  |
| `:agent-thinking-end` | `agent.thinking_end` |  |
| `:agent-llm-request` | `agent.llm_request` |  |
| `:agent-llm-response` | `agent.llm_response` |  |
| `:tool-execute` | `tool.execute` |  |
| `:tool-result` | `tool.result` |  |
| `:tool-error` | `tool.error` |  |
| `:tool-execute-start` | `tool.execute_start` |  |
| `:tool-execute-end` | `tool.execute_end` |  |
| `:system-startup` | `system.startup` |  |
| `:system-shutdown` | `system.shutdown` |  |
| `:system-error` | `system.error` |  |
| `:config-updated` | `config.updated` |  |
<!-- END GENERATED: dsl-on-events -->

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

受限求值器：[src/tlon/agents/eval.py](../../src/tlon/agents/eval.py) 的 `eval_form()`。

### 4.1 特殊形式（封闭白名单 `SPECIAL_FORMS`）

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

### 4.2 计量与终止

- 每个求值步消耗 1 单位 **fuel**，耗尽抛 `FuelExhaustedError`；form 嵌套深度硬顶 `DEFAULT_MAX_DEPTH = 100`。递归不可能（无法命名函数），`while`/`recur` 表达能力等价图灵机，**终止性由 fuel 保证**。
- fuel 默认值（[src/tlon/agents/eval.py](../../src/tlon/agents/eval.py) 顶部常量）：`:when` 门控 200（`DEFAULT_WHEN_FUEL`），动作 1000（`DEFAULT_FUEL`），规则/loop 自声明上限 10000（`MAX_FUEL`，超过即加载拒绝）。
- 声明了 `:fuel` 的确定规则：`:when` 与所有 `:actions` 共享一个计数器；认知规则的门控与完成动作分阶段，各按 `:fuel` 计。`:loop/:fuel` 单独约束 `:until` 与 stage `:if` 的求值。

### 4.3 纯度约束（"DSL 描述，引擎执行"）

- 求值永不产生副作用。动作 builtin（`pure=False`）只校验参数并产出 `ActionRequest` 数据，由引擎在求值结束后执行。
- 环境值对表达式不透明：没有属性/方法访问语法，`Mail` 只能经 builtin 检查。
- map 是数据：键保持字面量，值会被求值——动作选项 map 里可嵌表达式，如 `{:agent (get event :agent_id)}`。
- **动作位置允许的包装形式只有 `when` / `while` / `map`**（`validate_member_action`）：`(when cond (spawn ...) (emit ...))`、`(while [i 0] (< i 3) (emit ...) (recur (inc i)))`、`(map x (forward {:to [x]}) ["a" "b"])`。`if` / `let` / `filter` / `reduce` 不能出现在动作位置；动作的全部参数必须是纯表达式。

## 5. Builtin 完整清单

模块级注册表在 [src/tlon/agents/builtins.py](../../src/tlon/agents/builtins.py)；`make_member_builtins` 把 `TRIGGER_BUILTINS` + `SCRIPT_BUILTINS` + `ARITHMETIC_BUILTINS` 合并后再加上 5.6 的动作 builtin（`pure=False`），构成成员统一注册表——合并后的纯子集可用于 `:when`、动作参数等一切纯位置。另有四个读注册表状态的宿主查询由调用方闭包注入（`ScriptHostQueries`，实现为注册表模块里的 `RegistryQueries`，Adam 与注册表装配的全体成员共用同一份注入）：谓词 `sleeping?` / `running?` / `lazy?` 与社会名册 `member-roster`（issue ，零参数，返回预格式化的名册字符串，每行一个成员 `名字（id） — prototype：描述`，prototype 段与描述按需省略；睡眠成员仍在册，Adam 自身不进注册表故不在册）。它们不在任何模块级注册表——无宿主注入的注册表里没有这些名字，引用即加载校验失败。

### 5.1 `TRIGGER_BUILTINS`（触发器谓词）

`:when` 门控可用，环境 `{msg, event, self}`。

<!-- BEGIN GENERATED: dsl-builtins-trigger -->
`TRIGGER_BUILTINS`（6 个，按源码定义顺序）：（本区块由 `scripts/gen_reference_docs.py` 生成，勿手改；改了会被 `--check` 打回）

| 名称 | pure | 说明 |
|---|---|---|
| `addressed-to-me?` | 是 | My address is in the mail's To list. |
| `cc-ed?` | 是 | My address is in the mail's CC list (None-safe, see addressed-to-me?). |
| `content-matches?` | 是 | Regex-search a Mail's content or a plain string (e.g. ``result``). |
| `contains?` | 是 |  |
| `=` | 是 |  |
| `not` | 是 |  |
<!-- END GENERATED: dsl-builtins-trigger -->

### 5.2 `LOOP_BUILTINS`（loop `:until` 谓词）

环境 `{self, state, limits}`：`state` = `{:iteration n :error_count n}`（`error_count` 是**连续** think 失败数——think 成功一次即清零，不是会话累计值），`limits` = `{:max_iterations n :max_consecutive_errors n}`。键缺失安全返回 `false`，不抛错。

<!-- BEGIN GENERATED: dsl-builtins-loop -->
`LOOP_BUILTINS`（4 个，按源码定义顺序）：（本区块由 `scripts/gen_reference_docs.py` 生成，勿手改；改了会被 `--check` 打回）

| 名称 | pure | 说明 |
|---|---|---|
| `iteration-limit-reached?` | 是 | ``state.iteration`` has reached ``limits.max_iterations``. |
| `error-limit-reached?` | 是 | ``state.error_count`` (consecutive think failures; a successful think resets it to 0) has reached ``limits.max_consecutive_errors``. |
| `=` | 是 |  |
| `not` | 是 |  |
<!-- END GENERATED: dsl-builtins-loop -->

### 5.3 `STAGE_COND_BUILTINS`（stage `:if` 谓词）

环境 `{state, scratch, round}`；`scratch` 是会话循环暂存区（plan/critique stage 写入，见第 6 节）；`round` = `{:completing bool}`——响应链上的本轮响应若派发是否会让会话完成（单独的 `sendEmail is_complete=true` 或 `silence` 调用，判定与 act 的完成/冲突规则一致）。

<!-- BEGIN GENERATED: dsl-builtins-stage-cond -->
`STAGE_COND_BUILTINS`（7 个，按源码定义顺序）：（本区块由 `scripts/gen_reference_docs.py` 生成，勿手改；改了会被 `--check` 打回）

| 名称 | pure | 说明 |
|---|---|---|
| `critique-failed?` | 是 | A critique verdict exists in scratch and did not pass. |
| `critique-passed?` | 是 | A critique verdict exists in scratch and passed. |
| `plan-exists?` | 是 | Scratch holds a non-empty plan string. |
| `first-iteration?` | 是 | ``state.iteration`` is 0 (the loop's first round). |
| `completing?` | 是 | The round's pending response would end the session if dispatched. |
| `=` | 是 |  |
| `not` | 是 |  |
<!-- END GENERATED: dsl-builtins-stage-cond -->

### 5.4 `SCRIPT_BUILTINS`（确定规则纯访问器）

环境 `{event, self}`：`event` 是触发事件的负载 map。

<!-- BEGIN GENERATED: dsl-builtins-script -->
`SCRIPT_BUILTINS`（5 个，按源码定义顺序）：（本区块由 `scripts/gen_reference_docs.py` 生成，勿手改；改了会被 `--check` 打回）

| 名称 | pure | 说明 |
|---|---|---|
| `get` | 是 | Read ``key`` from a dict payload; missing keys yield ``default``. |
| `str` | 是 | Concatenate arguments into one string (issue ). |
| `contains?` | 是 |  |
| `=` | 是 |  |
| `not` | 是 |  |
<!-- END GENERATED: dsl-builtins-script -->

### 5.5 `ARITHMETIC_BUILTINS`（算术）

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

### 5.6 动作 builtin（`pure=False`，产出 `ActionRequest`，引擎执行）

动作 builtin 不在模块级注册表——它们由 `make_member_builtins` 以闭包定义、按调用方装配（`collector` / `host` 注入），因此本表手写维护。`:actions` 里的每个 form 头必须是这六个之一（或 `when`/`while`/`map` 包装）。

| 名称 | 签名 | 语义 |
|---|---|---|
| `spawn` | `(spawn prototype [options])` | 经注册表从原型派生一个新成员（统一成员模型，ADR 0006：与手建成员同构、永不删除）；`prototype` 必填字符串，`options` 只允许 `{:name "..."}`；无名时自动生成 `"{prototype}-{随机4位}"` |
| `emit` | `(emit content [to])` | 发邮件；`content` 必填字符串，`to` 为地址字符串向量；**省略 `to` 时回复触发邮件的发件人**（无触发邮件则收件人为空、发信失败仅记日志） |
| `start` | `(start {:agent id :reason s})` | 发布启动请求；`:agent` / `:reason` 均为必填非空字符串 |
| `stop` | `(stop {:agent id :reason s})` | 发布停止请求；参数同上 |
| `start-all` | `(start-all {:reason s})` | 对"声明了 `:on :boot` 的成员"集合逐个发启动请求；**不允许 `:agent`**；候选集展开依赖宿主查询注入——无宿主的注册表运行时会抛 `EvalError` |
| `forward` | `(forward {:to [...] :cc [...] :subject s})` | 转发触发邮件：`:to` 必填非空地址向量，`:cc` 可选向量，`:subject` 可选字符串；正文取自触发邮件，新邮件以 `forward_of` 链接原邮件；无触发邮件时跳过并记 warning |

## 6. `:loop` 详解（认知会话）

<!-- BEGIN GENERATED: dsl-loop-names -->
合法名称全集（生成自 `member_spec.py`）：（本区块由 `scripts/gen_reference_docs.py` 生成，勿手改；改了会被 `--check` 打回）

- stage（`STAGE_NAMES`，5 个）：`act`、`critique`、`plan`、`revise`、`think`
- section（`SECTION_NAMES`，8 个）：`current-task`、`knowledge`、`lessons`、`mail-history`、`persona`、`protocol`、`skills`、`thread-recap`
<!-- END GENERATED: dsl-loop-names -->

`:loop` 是引擎执行的 ReAct 会话。所有字段均为 total 语义——**以下键全部必填，无默认值**（`_loop_from`）：

```clojure
:loop {:context {:sections [(persona) (protocol) (knowledge) (mail-history 20)]}
       :stages [(think)
                (critique {:if (completing?)})
                (act {:if (or (not (completing?)) (not (critique-failed?)))})
                (revise {:if (critique-failed?)})]
       :until (or (iteration-limit-reached?) (error-limit-reached?))
       :max-iterations 25
       :max-consecutive-errors 3
       :reflect {:enabled true :interval-minutes 10}
       :fuel 1000}                  ; 可选
```

| 键 | 类型 | 语义 |
|---|---|---|
| `:context` | map | 只含 `:sections`——上下文 section 向量 |
| `:stages` | 向量 | stage form 列表，**不得为空**；每步按序解释执行 |
| `:until` | 表达式 form | 终止谓词，每步 pre-think 求值 |
| `:max-iterations` | int | 迭代硬顶（引擎无条件执行，`:until` 无法覆盖） |
| `:max-consecutive-errors` | int | 连续错误上限（think 成功一次即清零重计；散布的错误不累计） |
| `:reflect` | map | `{:enabled bool :interval-minutes int}`——周期性后台记忆审计（`ReflectionEngine`：间隔 + 最少完成会话数双门控，对记忆做整理/增删） |
| `:fuel` | int | 可选；约束 `:until` 与 stage `:if` 的求值，默认 1000，上限 10000 |

### 6.1 `:sections`（`SECTION_NAMES`，8 种）

`persona` 与 `protocol` 进 system prompt；`knowledge`、`lessons`、`skills`、`current-task`、`thread-recap` 与 `mail-history` 按向量顺序追加到对话上下文（`ContextBuilder`）。section 名不得重复。

| section | 参数 | 语义 |
|---|---|---|
| `(persona)` | 无 | 成员人格文件（SOUL/IDENTITY/RULES 等）进 system prompt |
| `(protocol)` | 无 | 协作协议文本进 system prompt |
| `(knowledge)` | 无 | 记忆块拼成 `[已知信息]` 用户消息 |
| `(lessons)` | 无 | 成员继承的 `config/LESSONS.md`（退休成员蒸馏、随原型包传递，issue ）拼成 `[传承教训]` 用户消息；无该文件则跳过该 section（空态惯例同 knowledge/skills） |
| `(skills)` | 无 | 三层 skill 目录的索引（name + description + SKILL.md 路径）拼成 `[可用技能]` 用户消息；模型用 read_file 自读正文（ADR 0010，[design/skills.md](../design/skills.md)） |
| `(current-task)` | 无 | 当前 doing 任务（至多一条：id + 标题 + 状态 + 描述）拼成 `[当前任务]` 用户消息（issue ）；无 doing 任务则跳过该 section（空态惯例同 knowledge/skills），无 paused 回退。上下文在 session 首步构建一次（anchor），任务中途变化下个 session 才反映 |
| `(thread-recap)` | 无 | 本线程最近一个已完成 session 的简报（触发、工具调用摘要、结论）拼成 `[本线程上一轮处理摘要]` 用户消息（issue ）：确定性重建自 session journal（ADR 0007），不调 LLM、不等批触发摘要；与 `mail-history` 互补——原文看邮件，简报看「我上一轮做了什么」。只覆盖最近一轮，成本不随线程长度膨胀；线程首轮、上一轮未完成（无 `session/end`）或从未跑过 session 时跳过该 section |
| `(mail-history n)` | 一个非负 int | 取邮箱最近 n 封邮件进对话上下文 |

### 6.2 五个 stage（`STAGE_NAMES`）

stage form：`(name)` 或 `(name {opts})`。所有 stage 支持 `:if <表达式>`（用 5.3 的谓词；条件为假或求值失败都跳过该 stage）。仅 `plan` 额外支持 `:max-steps <正整数>`（默认 5）。其余 opts 键加载即拒绝。

| stage | 语义（实现见 [src/tlon/agents/stages/](../../src/tlon/agents/stages)） |
|---|---|
| `think` | 一次 LLM 调用，产出本轮响应（进响应链）。成功：清零连续错误计数。失败：记一次错误 + 推进迭代，不发邮件，由 `:until` 后续终止循环 |
| `act` | 执行本轮**全部**工具调用。无工具调用：追加提醒消息并推进迭代；`sendEmail is_complete=true` 与其他工具并发属冲突：追加冲突提示并推进迭代；否则每个工具一个后台任务并发执行。收集半程：`sendEmail` 成功即发出回复邮件（显式 `to` 优先，否则回复触发邮件发件人）；`is_complete=true` 触发会话完成——声明最终回复（on-complete 的 `result`）、跑 on-complete 的 `:actions`、标记完成 |
| `plan` | 额外一次 LLM 调用生成 ≤ `:max-steps` 步计划；写入 `scratch.plan` 并以 `[执行计划]` 用户消息入上下文。失败软降级（跳过，不记错误） |
| `critique` | 额外一次 LLM 评审：评审输入 = 会话 context + 本轮待审草稿（响应链上尚未入 context 的响应，渲染为 assistant 消息）+ 评审指令；首行 `PASS`/`FAIL`，结论写入 `scratch.critique = {:passed bool :feedback str}`；首行无法解析时跳过本次评审（记 warning、不写 verdict——`critique-passed?` 与 `critique-failed?` 均不成立，revise 不触发），不按 PASS 放行。失败软降级 |
| `revise` | 不调 LLM；把 `scratch.critique.feedback` 以 `[评审反馈]` 用户消息喂回上下文，供下一轮 `think` 修正；无反馈则空操作。消费过的 verdict 随之清除（`scratch.critique` 置 nil），避免陈旧 FAIL 在 critique 被条件跳过的后续轮次重复触发 |

stage 是"纯变换 + 声明意图"：返回值是 `StageOutcome` 数据（[src/tlon/agents/stages/outcome.py](../../src/tlon/agents/stages/outcome.py)），由 `LoopDriver._apply_outcome` 统一落地——应用顺序为：上下文追加 → scratch → 计数器/状态 → 工具结果 → 响应链 → 工具派发 → 邮件 → on-complete 动作 → 完成标记。

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

`behavior.tlon` 可声明顶层 `:parameters` map，并在规则文本里以 `${name}` 引用（[src/tlon/agents/behavior_template.py](../../src/tlon/agents/behavior_template.py)）：

```clojure
:parameters {:to {:type :string-list :required true}}
```

| 要点 | 说明 |
|---|---|
| 类型 | `:string` 或 `:string-list`（缺省 `:string`） |
| `:required` | 缺省 `true`；渲染（`render_template`）时缺失必填参数、出现未声明参数、类型不符均抛 `BehaviorTemplateError` |
| 双向校验 | 正文出现的每个 `${var}` 必须在 `:parameters` 声明，反之亦然 |
| 生命周期 | **写侧专属**：实例化时 `${var}` 被渲染为 EDN 字面量后才落盘；运行时加载器看不到 `:parameters`（作为未知键记 warning），也永远见不到 `${...}` |

## 9. 完整示例

### 9.1 带 `:loop`：`deep-thinker`（[src/tlon/prototypes/deep-thinker/behavior.tlon](../../src/tlon/prototypes/deep-thinker/behavior.tlon)）

```clojure
{:version 1
 :rules [{:name "respond"
          :on :mail-delivered                                   ; 邮件送达触发
          :when (or (addressed-to-me? msg) (cc-ed? msg))        ; 只理会在 To/CC 里点了我的邮件
          :loop {:context {:sections [(persona) (protocol)      ; system prompt：人格 + 协议
                                      (knowledge)               ; 对话上下文：记忆块
                                      (skills)                  ; 对话上下文：技能索引（ADR 0010）
                                      (current-task)            ; 对话上下文：当前 doing 任务（issue ）
                                      (thread-recap)            ; 对话上下文：本线程上一轮处理摘要（issue ）
                                      (mail-history 20)]}       ; 对话上下文：最近 20 封邮件
                 :stages [(think)                                  ; 想：一次 LLM 调用
                          (critique {:if (completing?)})             ; 只在即将交卷时评审待交回复
                          (act {:if (or (not (completing?))          ; 做：评审 FAIL 时拦截本轮完成
                                        (not (critique-failed?)))})
                          (revise {:if (critique-failed?)})]         ; FAIL 时把反馈喂回，下轮修正后重审
                 :until (or (iteration-limit-reached?)          ; 兜底终止：迭代或错误到顶
                            (error-limit-reached?))
                 :max-iterations 25                             ; 引擎硬顶（先于 :until 生效）
                 :max-consecutive-errors 3
                 :reflect {:enabled true :interval-minutes 10}}  ; 每 10 分钟级后台记忆审计
         }                                                      ; ← 注意：此处必须有 } 闭合规则 map
         {:name "boot"
          :on :boot                                             ; 启动即跑（声明 boot 集成员身份）
          :actions []}]}                                        ; 无动作也要写空向量或省略
```

> 注：仓库中 [src/tlon/prototypes/deep-thinker/behavior.tlon](../../src/tlon/prototypes/deep-thinker/behavior.tlon) 的规则一 map 曾少一个闭合 `}`（历史问题，已修复）；抄写时以上例的闭合层次为准。

### 9.2 无 `:loop`：`relay`（[src/tlon/prototypes/relay/behavior.tlon](../../src/tlon/prototypes/relay/behavior.tlon)）

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

更复杂的无 `:loop` 例子见 [src/tlon/agents/adam.py](../../src/tlon/agents/adam.py) 的 `ADAM_SPEC_EDN`，三条规则：boot 时 `(start-all {:reason "boot"})` 拉起全部 boot 成员；`:on :agent-idle` 时 `(stop {:agent (get event :agent_id) :reason "drain"})` 排干空闲成员；`:on :agent-spawned` 时给新成员发出生欢迎信（issue ）——正文用 `(str ...)` 拼接待成员名字、宿主查询 `(member-roster)` 展开的名册与静态通信惯例，收件人地址也由 `(str (get event :agent_id) "@tlon.internal")` 装配（`emit` 的 `to` 必须是完整邮件地址，`Mail.create` 拒绝裸 id）。这个例子展示了选项 map 里嵌表达式、字符串拼接与宿主注入查询的用法。

## 10. 常见错误与加载失败行为

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
