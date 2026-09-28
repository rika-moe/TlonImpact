# Tlon 行为 DSL 参考手册

每个成员的行为由一份 `behavior.tlon` 描述（Lisp 风格 EDN），由统一解释器 `MemberRuntime`（[src/tlon/agents/member_runtime.py](../../src/tlon/agents/member_runtime.py)）执行。本文是该 DSL 的完整语义参考，一切以代码为准；语义权威是 [src/tlon/agents/member_spec.py](../../src/tlon/agents/member_spec.py) 的 `MemberSpec` 数据模型，EDN 只是它的序列化前端。

**本文的定位**：tlon 宿主侧手册——MemberSpec schema 与领域词汇。语言核心（EDN 语法、求值器、燃料计量、特殊形式、通用词汇）已独立成文，见 [tlosure-reference.md](./tlosure-reference.md)；下文涉及语言机制处只给一句话与链接，不再展开。

## 1. MemberSpec 顶层结构

文件必须是一个顶层 map（`MemberSpec.from_edn`）；EDN 语法本身见 [tlosure-reference.md](./tlosure-reference.md) 第 1 节。

```clojure
{:version 1
 :rules [ {...规则一...} {...规则二...} ]}
```

| 键 | 类型 | 必填 | 语义 |
|---|---|---|---|
| `:version` | int | 是 | 只支持 `1`（`MEMBER_SPEC_VERSION`），其他值加载即拒绝 |
| `:rules` | 向量 | 是 | 规则列表，**不得为空**；每项是一个 map |
| `:parameters` | map | 否 | 模板参数声明（见第 7 节），运行时加载器忽略 |
| `:llm` | — | — | **已从语法移除**（ADR 0016：模型是部署策略不是基因型）——残留该键加载即报错；成员的模型在 agent.yaml 的 `llm.model_name` 配置（创建时经 WebUI/admin API 选择，spawn 继承父成员） |
| 其他键 | — | — | 未知键记 warning 后忽略（前向兼容） |

**total 语义**（`MemberSpec.from_dict` docstring）：缺必填字段是错误，不给默认值；类型错误同样拒绝。加载路径 `load_member_spec()`（[src/tlon/agents/loader.py](../../src/tlon/agents/loader.py)）：文件不存在抛 `FileNotFoundError`，EDN 非法抛 `EDNParseError`，校验失败抛 `MemberSpecValidationError`——任何失败都使该成员拒绝启动（注册表隔离失败，不影响其他成员）。

## 2. 规则四要素

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
| `:loop` | map | 否 | 认知会话（见第 5 节）；有它 = 认知规则，无它 = 确定规则 |
| `:actions` | 向量 | 否 | 动作 form 列表；每项必须是非空 form |
| `:fuel` | int | 否 | 本条规则的求值预算；须为正且 ≤ 10000（`MAX_FUEL`）；计量机制见 [tlosure-reference.md](./tlosure-reference.md) 第 2.2 节，多段表达式如何记账见第 3 节 |

### 2.1 `:on` 可选事件

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

### 2.2 `:when` 门控

- 纯表达式（纯度约束见第 3 节），环境为 `{msg, event, self}`：`msg` 是事件负载中的 `Mail`（非邮件事件为 `nil`），`event` 是事件负载 map，`self` 是 `{"id" ..., "name" ..., "address" ...}`（address 由宿主预派生注入，ADR 0018 §3——mail 谓词据此解析"我"）。
- 求值失败（含 fuel 耗尽）安全降级为"不命中"（`MemberRuntime._should_respond`）。
- 确定规则：`:when` 为假则跳过整条规则的 `:actions`。
- 认知规则：`:when` 为假则不入队会话，且该邮件立即标记已读（D6.1）。

### 2.3 命中后的执行顺序

- 认知规则：先跑 `:loop` 会话，会话完成后再跑 `:actions`（`OnCompleteRunner`，此时环境额外绑定 `result` 为最终回复文本）。
- 确定规则：直接逐条求值 `:actions` 并执行产出的 `ActionRequest`。

**同一成员至多一条认知规则**（D6.7，`validate_member_spec`）；超过一条加载即拒绝。

## 3. 纯度约束与求值托管（宿主视角）

求值器机制本身——特殊形式白名单、fuel 计量与终止保证、求值无副作用与 `ActionRequest` 数据机制——是语言规范，见 [tlosure-reference.md](./tlosure-reference.md) 第 2 节，不在这里重复。本节只记 tlon 宿主侧的闸与记账方式（"DSL 描述，引擎执行"）：

- 动作 builtin（`pure=False`，见第 4.5 节）只校验参数并产出 `ActionRequest` 数据，由引擎在求值结束后执行；求值过程本身永不产生副作用。
- 环境值对表达式不透明：没有属性/方法访问语法，`Mail` 只能经 builtin 检查。
- map 是数据：键保持字面量，值会被求值——动作选项 map 里可嵌表达式，如 `{:agent (get event :agent_id)}`。
- **动作位置闸 `validate_member_action`**（[src/tlon/agents/vocabulary.py](../../src/tlon/agents/vocabulary.py)）：`:when` 门控必须是纯表达式；动作位置允许的包装形式只有 `when` / `while` / `map`——`(when cond (spawn ...) (emit ...))`、`(while [i 0] (< i 3) (emit ...) (recur (inc i)))`、`(map x (forward {:to [x]}) ["a" "b"])`。`if` / `let` / `filter` / `reduce` 不能出现在动作位置；动作的全部参数必须是纯表达式。
- fuel 记账：默认值 `:when` 门控 `DEFAULT_WHEN_FUEL`、动作 `DEFAULT_FUEL`，规则/loop 自声明上限 `MAX_FUEL`（超过即加载拒绝；常量取值见下表）。声明了 `:fuel` 的确定规则：`:when` 与所有 `:actions` 共享一个计数器；认知规则的门控与完成动作分阶段，各按 `:fuel` 计。`:loop/:fuel` 单独约束 `:until` 与 stage `:if` 的求值。

<!-- BEGIN GENERATED: dsl-fuel-defaults -->
语言级求值预算常量（`tlosure/eval.py`，4 个；上文的默认值/上限数字以此表为准）：（本区块由 `scripts/gen_reference_docs.py` 生成，勿手改；改了会被 `--check` 打回）

| 常量 | 值 | 用途 |
|---|---|---|
| `DEFAULT_WHEN_FUEL` | 200 | `:when` 门控的默认预算 |
| `DEFAULT_FUEL` | 1000 | `:actions` / `:loop` 求值的默认预算 |
| `MAX_FUEL` | 10000 | `:fuel` 自声明的硬顶（超过即加载拒绝） |
| `DEFAULT_MAX_DEPTH` | 100 | form 嵌套深度上限 |
<!-- END GENERATED: dsl-fuel-defaults -->

### 3.1 环境契约（求值语境的可见数据）

<!-- BEGIN GENERATED: bridge-env-contracts -->
每个求值语境绑定一组固定符号、固定形状的数据（4 个顶层契约，`vocabulary.py` 的 `*Env` TypedDict 是形状的单一事实源，构建器函数是唯一构造点——引擎调用点全部委托到这里）：（本区块由 `scripts/gen_reference_docs.py` 生成，勿手改；改了会被 `--check` 打回）

| 环境契约 | 构建器 | 字段 | 类型 |
|---|---|---|---|
| `RuleEnv` | `rule_env()` | `msg` | `Mail \| None` |
| `RuleEnv` | `rule_env()` | `event` | `Any` |
| `RuleEnv` | `rule_env()` | `self` | `SelfEnv` |
| `OnCompleteEnv` | `on_complete_env()` | `msg` | `Mail \| None` |
| `OnCompleteEnv` | `on_complete_env()` | `result` | `str` |
| `OnCompleteEnv` | `on_complete_env()` | `self` | `SelfEnv` |
| `LoopEnv` | `loop_until_env()` | `self` | `SelfEnv` |
| `LoopEnv` | `loop_until_env()` | `state` | `LoopStateEnv` |
| `LoopEnv` | `loop_until_env()` | `limits` | `LoopLimitsEnv` |
| `StageCondEnv` | `stage_cond_env()` | `state` | `LoopStateEnv` |
| `StageCondEnv` | `stage_cond_env()` | `scratch` | `dict[str, Any]` |
| `StageCondEnv` | `stage_cond_env()` | `round` | `RoundEnv` |

嵌套类型的字段：

- `SelfEnv`：`id: str`、`name: str`、`address: str`
- `LoopStateEnv`：`iteration: int`、`error_count: int`
- `LoopLimitsEnv`：`max_iterations: int`、`max_consecutive_errors: int`
- `RoundEnv`：`completing: bool`
<!-- END GENERATED: bridge-env-contracts -->

## 4. Builtin 完整清单

语言级通用词汇（`CORE_BUILTINS` / `ARITHMETIC_BUILTINS`，[src/tlosure/builtins.py](../../src/tlosure/builtins.py)，ADR 0018 修订）的清单见 [tlosure-reference.md](./tlosure-reference.md) 第 3 节；本节只列 tlon 领域词汇。领域词汇的模块级注册表在 [src/tlon/agents/vocabulary.py](../../src/tlon/agents/vocabulary.py)；`make_member_builtins` 把 `TRIGGER_BUILTINS` + `SCRIPT_BUILTINS` + `ARITHMETIC_BUILTINS` 合并后再加上 4.5 的动作 builtin（`pure=False`），构成成员统一注册表——合并后的纯子集可用于 `:when`、动作参数等一切纯位置。另有四个读注册表状态的宿主查询由调用方闭包注入（`ScriptHostQueries`，实现为注册表模块里的 `RegistryQueries`，Adam 与注册表装配的全体成员共用同一份注入），清单见第 4.6 节。它们不在任何模块级注册表——无宿主注入的注册表里没有这些名字，引用即加载校验失败。

**版本纪律**（ADR 0020，[docs/design/bridge-api.md](../design/bridge-api.md)）：本节清单是 spec 作者面对的稳定界面。spec 是落盘数据，因此桥上词汇**只增不删、语义只紧不松**——既有 builtin 的名字、元数、返回形状不可变；修复式收紧（原来放行的坏输入改为加载拒绝）允许。新增词汇必须声明所属语境注册表，不存在"所有语境可见"的词；加载期校验（`validate_member_spec`）是引用错误的唯一守门人，运行期不存在"未定义词汇"这一类错误。

### 4.1 `TRIGGER_BUILTINS`（触发器谓词）

`:when` 门控可用，环境 `{msg, event, self}`。

<!-- BEGIN GENERATED: dsl-builtins-trigger -->
`TRIGGER_BUILTINS`（7 个，按源码定义顺序）：（本区块由 `scripts/gen_reference_docs.py` 生成，勿手改；改了会被 `--check` 打回）

| 名称 | pure | 说明 |
|---|---|---|
| `addressed-to-me?` | 是 | My address is in the mail's To list. |
| `cc-ed?` | 是 | My address is in the mail's CC list (None-safe, see addressed-to-me?). |
| `from?` | 是 | The mail's sender equals ``address`` (None-safe, see addressed-to-me?). |
| `content-matches?` | 是 | Regex-search a mail's content or a plain string (e.g. ``result``). |
| `contains?` | 是 |  |
| `=` | 是 |  |
| `not` | 是 |  |
<!-- END GENERATED: dsl-builtins-trigger -->

### 4.2 `LOOP_BUILTINS`（loop `:until` 谓词）

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

### 4.3 `STAGE_COND_BUILTINS`（stage `:if` 谓词）

环境 `{state, scratch, round}`；`scratch` 是会话循环暂存区（plan/critique stage 写入，见第 5 节）；`round` = `{:completing bool}`——响应链上的本轮响应若派发是否会让会话完成（单独的 `sendEmail is_complete=true` 或 `silence` 调用，判定与 act 的完成/冲突规则一致）。

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

### 4.4 `SCRIPT_BUILTINS`（确定规则纯访问器）

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

### 4.5 动作 builtin（`pure=False`，产出 `ActionRequest`，引擎执行）

动作 builtin 不在模块级注册表——它们由 `make_member_builtins` 以闭包定义、按调用方装配（`collector` / `host` 注入），因此本表手写维护。`:actions` 里的每个 form 头必须是这六个之一（或 `when`/`while`/`map` 包装）。

| 名称 | 签名 | 语义 |
|---|---|---|
| `spawn` | `(spawn prototype [options])` | 经注册表从原型派生一个新成员（统一成员模型，ADR 0006：与手建成员同构、永不删除）；`prototype` 必填字符串，`options` 只允许 `{:name "..."}`；无名时自动生成 `"{prototype}-{随机4位}"` |
| `emit` | `(emit content [to])` | 发邮件；`content` 必填字符串，`to` 为地址字符串向量；**省略 `to` 时回复触发邮件的发件人**（无触发邮件则收件人为空、发信失败仅记日志） |
| `start` | `(start {:agent id :reason s})` | 发布启动请求；`:agent` / `:reason` 均为必填非空字符串 |
| `stop` | `(stop {:agent id :reason s})` | 发布停止请求；参数同上 |
| `start-all` | `(start-all {:reason s})` | 对"声明了 `:on :boot` 的成员"集合逐个发启动请求；**不允许 `:agent`**；候选集展开依赖宿主查询注入——无宿主的注册表运行时会抛 `EvalError` |
| `forward` | `(forward {:to [...] :cc [...] :subject s})` | 转发触发邮件：`:to` 必填非空地址向量，`:cc` 可选向量，`:subject` 可选字符串；正文取自触发邮件，新邮件以 `forward_of` 链接原邮件；无触发邮件时跳过并记 warning |

<!-- BEGIN GENERATED: dsl-action-kinds -->
六个动作 builtin 实际产出的 `ActionRequest` 形状（5 种 kind，生成自 `vocabulary.py` 的调用点；`start-all` 不产出自己的 kind——它按宿主查询 `start_all_candidates` 的候选集展开为多个 `kind="start"` 请求）：（本区块由 `scripts/gen_reference_docs.py` 生成，勿手改；改了会被 `--check` 打回）

| kind | data 字段 |
|---|---|
| `spawn` | `prototype`、`name` |
| `emit` | `content`、`to` |
| `start` | `agent_id`、`reason` |
| `stop` | `agent_id`、`reason` |
| `forward` | `to`、`cc`、`subject` |
<!-- END GENERATED: dsl-action-kinds -->

### 4.6 宿主查询端口（闭包注入）

<!-- BEGIN GENERATED: bridge-query-ports -->
`make_member_builtins` 在调用方注入 `host` 时追加 4 个宿主查询（`pure=True`，全部读注册表状态、无宿主即不在注册表；说明取自 `ScriptHostQueries` 协议方法的 docstring）：（本区块由 `scripts/gen_reference_docs.py` 生成，勿手改；改了会被 `--check` 打回）

| DSL 词 | 宿主方法 | 说明 |
|---|---|---|
| `sleeping?` | `host.is_sleeping` | Registered (config known) but not running. |
| `running?` | `host.is_running` | Currently in the run table. |
| `lazy?` | `host.is_lazy` | A member that can sleep / be drained: no ``:on :boot`` rule. |
| `member-roster` | `host.member_roster` | The social roster: one pre-formatted line per registered member. |
<!-- END GENERATED: bridge-query-ports -->

## 5. `:loop` 详解（认知会话）

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

### 5.1 `:sections`（`SECTION_NAMES`，8 种）

`persona` 与 `protocol` 进 system prompt；`knowledge`、`lessons`、`skills`、`current-task`、`thread-recap` 与 `mail-history` 追加到对话上下文（`ContextBuilder`）。section 名不得重复。**位置语义例外（ADR 0017 阶段 4，2026-09-17）**：`knowledge` 不随向量顺序落位——它固定注入为整个 context 的最后一条（当前邮件之后、压缩锚点 anchor 之外），声明只控制「是否包含」；其余段按向量顺序追加。

**排序原则（缓存友好）**：LLM 供应商按请求前缀逐字节命中缓存定价，断裂点之后的内容全部按未命中计费。因此 section 向量应按变动频率排序——几乎不变的段（`lessons`、`skills`）靠前，尾部增长的段（`mail-history`）居中，每会话必变的段（`thread-recap`）与当前邮件靠后。内置原型统一为 `(persona) (protocol) (lessons) (skills) (knowledge) (current-task) (mail-history N) (thread-recap)`（`knowledge` 的声明位置是历史惯例，注入位置恒为最末）；当前邮件永远由引擎追加在最后（knowledge 之前）。

| section | 参数 | 语义 |
|---|---|---|
| `(persona)` | 无 | 成员人格文件（SOUL/IDENTITY/RULES 等）进 system prompt |
| `(protocol)` | 无 | 协作协议文本进 system prompt |
| `(knowledge)` | 无 | 记忆块拼成 `[已知信息]` 用户消息；**固定注入为 context 最后一条**（当前邮件之后、anchor 之外，ADR 0017 阶段 4）——声明位置不决定注入位置，只决定是否注入 |
| `(lessons)` | 无 | 成员继承的 `config/LESSONS.md`（退休成员蒸馏、随原型包传递，issue ）拼成 `[传承教训]` 用户消息；无该文件则跳过该 section（空态惯例同 knowledge/skills） |
| `(skills)` | 无 | 三层 skill 目录的索引（name + description + SKILL.md 路径）拼成 `[可用技能]` 用户消息；模型用 read_file 自读正文（ADR 0010，[design/skills.md](../design/skills.md)） |
| `(current-task)` | 无 | 当前 doing 任务（至多一条：id + 标题 + 状态 + 描述）拼成 `[当前任务]` 用户消息（issue ）；无 doing 任务则跳过该 section（空态惯例同 knowledge/skills），无 paused 回退。上下文在 session 首步构建一次（anchor），任务中途变化下个 session 才反映 |
| `(thread-recap)` | 无 | 本线程最近一个已完成 session 的简报（触发、工具调用摘要、结论）拼成 `[本线程上一轮处理摘要]` 用户消息（issue ）：确定性重建自 session journal（ADR 0007），不调 LLM、不等批触发摘要；与 `mail-history` 互补——原文看邮件，简报看「我上一轮做了什么」。只覆盖最近一轮，成本不随线程长度膨胀；线程首轮、上一轮未完成（无 `session/end`）或从未跑过 session 时跳过该 section |
| `(mail-history n)` | 一个非负 int | 取邮箱最近 n 封邮件进对话上下文 |

### 5.2 五个 stage（`STAGE_NAMES`）

stage form：`(name)` 或 `(name {opts})`。所有 stage 支持 `:if <表达式>`（用 4.3 的谓词；条件为假或求值失败都跳过该 stage）。仅 `plan` 额外支持 `:max-steps <正整数>`（默认 5）。其余 opts 键加载即拒绝。

| stage | 语义（实现见 [src/tlon/agents/stages/](../../src/tlon/agents/stages)） |
|---|---|
| `think` | 一次 LLM 调用，产出本轮响应（进响应链）。成功：清零连续错误计数。失败：记一次错误 + 推进迭代，不发邮件，由 `:until` 后续终止循环 |
| `act` | 执行本轮**全部**工具调用。无工具调用：追加提醒消息并推进迭代；`sendEmail is_complete=true` 与其他工具并发属冲突：追加冲突提示并推进迭代；否则每个工具一个后台任务并发执行。收集半程：`sendEmail` 成功即发出回复邮件（显式 `to` 优先，否则回复触发邮件发件人）；`is_complete=true` 触发会话完成——声明最终回复（on-complete 的 `result`）、跑 on-complete 的 `:actions`、标记完成 |
| `plan` | 额外一次 LLM 调用生成 ≤ `:max-steps` 步计划；写入 `scratch.plan` 并以 `[执行计划]` 用户消息入上下文。失败软降级（跳过，不记错误） |
| `critique` | 额外一次 LLM 评审：评审输入 = 会话 context + 本轮待审草稿（响应链上尚未入 context 的响应，渲染为 assistant 消息）+ 评审指令；首行 `PASS`/`FAIL`，结论写入 `scratch.critique = {:passed bool :feedback str}`；首行无法解析时跳过本次评审（记 warning、不写 verdict——`critique-passed?` 与 `critique-failed?` 均不成立，revise 不触发），不按 PASS 放行。失败软降级 |
| `revise` | 不调 LLM；把 `scratch.critique.feedback` 以 `[评审反馈]` 用户消息喂回上下文，供下一轮 `think` 修正；无反馈则空操作。消费过的 verdict 随之清除（`scratch.critique` 置 nil），避免陈旧 FAIL 在 critique 被条件跳过的后续轮次重复触发 |

stage 是"纯变换 + 声明意图"：返回值是 `StageOutcome` 数据（[src/tlon/agents/stages/outcome.py](../../src/tlon/agents/stages/outcome.py)），由 `LoopDriver._apply_outcome` 统一落地——应用顺序为：上下文追加 → scratch → 计数器/状态 → 工具结果 → 响应链 → 工具派发 → 邮件 → on-complete 动作 → 完成标记。

### 5.3 循环推进与终止（`LoopDriver.step_session`）

每一步的顺序：

1. **迭代硬顶**：`iteration ≥ :max-iterations` 时直接回复触发邮件"思考时间过长，请简化您的问题。"并结束会话——在任何 `:until` 求值之前。
2. **`:until` 求值**（4.2 谓词）：为真则终止——错误上限触发回复"抱歉，处理邮件时出现了错误。"，否则回复"思考时间过长，请简化您的问题。"。求值失败回退到引擎默认检查（迭代/错误上限），循环必然停止。
3. 按序解释 `:stages`；`think` 失败则本步提前返回。
4. 若 stage 向量不含 `act`（或本轮 act 未推进迭代），引擎自行推进迭代计数，保证硬顶始终生效。

正常的会话完成不经过 `:until`：由 `act` 收集到 `sendEmail is_complete=true` 事件驱动。`:until` 只是兜底。

## 6. 确定性成员的 `:actions`

无 `:loop` 的规则即确定规则：事件命中 → 求值 `:when` → 逐条求值 `:actions` → 执行产出的 `ActionRequest`。

- 环境为 `{msg, event, self}`；**`result` 只在认知规则的完成动作里绑定**——确定规则里引用 `result` 能通过加载校验（作用域含它）但运行时抛"undefined symbol"，整条规则跳过并记 warning。
- 每个动作 form 的求值结果是请求数据：`start-all` 返回请求列表，`while`/`map` 可收集多个请求；非 `ActionRequest` 的值被忽略。
- 求值失败（含 fuel 耗尽）跳过本条规则，不影响其他规则与总线分发。
- 邮件语义（`MemberRuntime`）：自己发出的邮件永不处理并立即标记已读；被 `:when` 拒绝的邮件也标记已读；确定规则命中即读；队列满丢弃的邮件保持未读，留待下次启动时的未读补救扫描（`_scan_unread_mail`）重放。

## 7. `:parameters` 模板（写侧专属）

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

## 8. 完整示例

### 8.1 带 `:loop`：`deep-thinker`（[src/tlon/prototypes/deep-thinker/behavior.tlon](../../src/tlon/prototypes/deep-thinker/behavior.tlon)）

```clojure
{:version 1
 :rules [{:name "respond"
          :on :mail-delivered                                   ; 邮件送达触发
          :when (or (addressed-to-me? msg) (cc-ed? msg))        ; 只理会在 To/CC 里点了我的邮件
          :loop {:context {:sections [(persona) (protocol)      ; system prompt：人格 + 协议
                                      (lessons)                 ; 对话上下文：传承教训（issue ）
                                      (skills)                  ; 对话上下文：技能索引（ADR 0010）
                                      (knowledge)               ; 对话上下文：记忆块（固定注入最末位，ADR 0017 阶段 4）
                                      (current-task)            ; 对话上下文：当前 doing 任务（issue ）
                                      (mail-history 20)         ; 对话上下文：最近 20 封邮件
                                      (thread-recap)]}          ; 对话上下文：本线程上一轮处理摘要（issue ）
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

### 8.2 无 `:loop`：`relay`（[src/tlon/prototypes/relay/behavior.tlon](../../src/tlon/prototypes/relay/behavior.tlon)）

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

更复杂的无 `:loop` 例子见 [src/tlon/agents/adam.py](../../src/tlon/agents/adam.py) 的 `ADAM_SPEC_EDN`，五条规则：boot 时 `(start-all {:reason "boot"})` 拉起全部 boot 成员；`:on :agent-idle` 时 `(stop {:agent (get event :agent_id) :reason "drain"})` 排干空闲成员；`:on :agent-noisy` 时 `(stop {:agent (get event :agent_id) :reason "noisy-circuit-breaker"})` 熔断刷屏成员（事件负载取自 `:agent_id`，与 drain 同一形态——健康监控器只发事实，后果由 spec 决定）；`:on :agent-spawned` 时给新成员发出生欢迎信（issue ）——正文用 `(str ...)` 拼接待成员名字、宿主查询 `(member-roster)` 展开的名册与静态通信惯例，收件人地址也由 `(str (get event :agent_id) "@tlon.internal")` 装配（`emit` 的 `to` 必须是完整邮件地址，`Mail.create` 拒绝裸 id）；`:on :mail-delivered` 时对寄给 Adam 的信回固定确认信（issue  守护者信箱）——`:when (and (addressed-to-me? msg) (not (from? msg "postmaster@tlon.internal")))` 展示门控组合：确定规则对总线上每封送达邮件都会执行，必须自带收件人门控；`from?` 排除 postmaster 发件人，退信永不回信。这个例子展示了选项 map 里嵌表达式、字符串拼接、宿主注入查询与纯谓词门控的用法。

## 9. 常见错误与加载失败行为

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
