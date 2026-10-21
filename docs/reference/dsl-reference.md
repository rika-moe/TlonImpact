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
| `:execution` | map | 否 | 执行装置能力声明（见 1.1 节）；缺失时实例化沿用 agent.yaml 默认 |
| `:parameters` | map | 否 | 模板参数声明（见第 7 节），运行时加载器忽略 |
| `:llm` | — | — | **已从语法移除**（ADR 0016：模型是部署策略不是基因型）——残留该键加载即报错；成员的模型在 agent.yaml 的 `llm.model_name` 配置（创建时经 WebUI/admin API 选择，spawn 继承父成员） |
| 其他键 | — | — | 未知键记 warning 后忽略（前向兼容） |

**total 语义**（`MemberSpec.from_dict` docstring）：缺必填字段是错误，不给默认值；类型错误同样拒绝。加载路径 `load_member_spec()`（[src/tlon/agents/loader.py](../../src/tlon/agents/loader.py)）：文件不存在抛 `FileNotFoundError`，EDN 非法抛 `EDNParseError`，校验失败抛 `MemberSpecValidationError`——任何失败都使该成员拒绝启动（注册表隔离失败，不影响其他成员）。

### 1.1 `:execution` 段

```clojure
{:version 1
 :execution {:level :L1}
 :rules [...]}
```

执行装置能力声明（宪法一，[design/execution-device.md](../design/execution-device.md)）：声明进基因组、随原型提案一起被评审。`:level` 取关键字 `:L0`（无执行装置）/`:L1`（文件层，结构性零代码执行）/`:L2`（Docker 完整 userland），语义同 agent.yaml 的 `docker.level`（见 [config-reference.md](./config-reference.md) 的 `docker` 节）。

**投影语义**：`instantiate`（[src/tlon/agents/prototypes/instantiate.py](../../src/tlon/agents/prototypes/instantiate.py)）在实例化时把声明一次性投影进 agent.yaml 的 `docker.level`；此后运行时权威是 agent.yaml——成员事后自改 behavior.tlon 的 `:execution` 不会重新投影（这正是防自提权的语义）。spec 不带 `:execution` 段时不投影，agent.yaml 保持默认（当前 `"L1"`，issue #31 于 2026-10-05 拍板翻转——未声明的新成员拿最低够用的文件层，需要代码执行的原型显式声明 `:L2`）。

段本身是 total 的：必须是 map、`:level` 必填且取值合法，违反即加载拒绝；段内未知键记 warning 后忽略（前向兼容，与其他 map 同款容错）。staging 原型声明 `:L2` 是评审红旗——详情 payload 派生 `red_flags`，评审 prompt 含对应角度（见 [design/prototypes.md](../design/prototypes.md)）。

## 2. 统一规则

规则 = `:on` + `:when` + `:actions`，可选 `:fuel`（`MemberRule`）。会话是 actions 中的普通效果，不产生成员或规则分类（ADR 0042）。

```clojure
{:name "respond"
 :on :mail-delivered
 :when (contains? (or (mail-field msg :to) []) (get self :address))
 :actions [ ... ]         ; 可选，默认 []
 :fuel 500}               ; 可选
```

| 键 | 类型 | 必填 | 语义 |
|---|---|---|---|
| `:name` | 字符串 | 是 | 规则名，用于日志与报错 |
| `:on` | 关键字 | 是 | 触发事件类型；`:boot` 或 `nil` 表示启动规则（见下） |
| `:when` | 表达式 form | 否 | 确定性或只读谓词门控；缺省 = 恒真 |
| `:actions` | 向量 | 否 | 动作 form 列表；每项必须是非空 form |
| `:fuel` | int | 否 | 本条规则的求值预算；须为正且 ≤ 10000（`MAX_FUEL`）；计量机制见 [tlosure-reference.md](./tlosure-reference.md) 第 2.2 节，多段表达式如何记账见第 3 节 |

### 2.1 `:on` 可选事件

`:on` 关键字经 kebab-case → `SCREAMING_SNAKE` 映射到 `EventType` 枚举（[src/tlon/base/events/event.py](../../src/tlon/base/events/event.py)，转换逻辑在 `member_spec._on_from`）。合法值：

<!-- BEGIN GENERATED: dsl-on-events -->
合法值共 37 个（`:boot` + 全部 `EventType` 成员，转换逻辑 `member_spec._on_from` / `_on_to_str`）：（本区块由 `scripts/gen_reference_docs.py` 生成，勿手改；改了会被 `--check` 打回）

| `:on` 关键字 | 事件值 | 说明 |
|---|---|---|
| `:boot` | — | 启动规则：映射 `None`，订阅就绪后运行一次，`msg` / `event` 均为 `nil` |
| `:mail-new` | `mail.new` | A mail was committed by the post office (notification only) |
| `:mail-delivered` | `mail.delivered` | A mail landed in one recipient's inbox |
| `:mail-endorsed` | `mail.endorsed` | A delivered mail endorses another mail (reputation signal, see docs/design/mail.md) |
| `:agent-join` | `agent.join` |  |
| `:agent-leave` | `agent.leave` |  |
| `:agent-thinking` | `agent.thinking` |  |
| `:agent-response` | `agent.response` |  |
| `:agent-started` | `agent.started` |  |
| `:agent-stopped` | `agent.stopped` |  |
| `:agent-retired` | `agent.retired` | Offline handover inventory after permanent exit |
| `:agent-error` | `agent.error` |  |
| `:agent-state-changed` | `agent.state_changed` |  |
| `:agent-waiting` | `agent.waiting` | Waiting remains waiting when its runtime is unloaded |
| `:agent-waking` | `agent.waking` |  |
| `:agent-woken` | `agent.woken` |  |
| `:agent-wake-failed` | `agent.wake_failed` |  |
| `:agent-sleeping` | `agent.sleeping` |  |
| `:agent-start-request` | `agent.start_request` |  |
| `:agent-stop-request` | `agent.stop_request` |  |
| `:agent-idle` | `agent.idle` |  |
| `:agent-spawned` | `agent.spawned` |  |
| `:agent-noisy` | `agent.noisy` |  |
| `:mail-orphan-scan` | `mail.orphan_scan` |  |
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

所有已订阅事件都可以触发普通动作或会话请求；同一成员允许多条规则和不同会话配方。不存在只有邮件事件才能运行会话的限制。

### 2.2 `:when` 门控

确定性或明确准入的只读表达式，环境为 `{msg, event, self}`。`msg` 为来源邮件，非邮件事件为 `nil`；`event` 为原始事件负载；`self` 含 id、name 和 address。门控为假或求值失败时跳过规则，不隐式把邮件标读。

### 2.3 命中后的执行顺序

规则按 spec 顺序匹配。生产程序通过可暂停求值内核在效果调用处等待宿主执行，记录并返回实际结果，再继续函数、条件或传统循环。`run-loop` 按本次配方执行会话并返回 `nil`，同一成员串行推进程序。

效果结果可显式绑定至 `let` 并用于后续分支，没有另一个完成动作求值环境，不隐式绑定 `result`。原始程序、触发输入和已完成效果保存在成员私有 `program.sqlite3`，恢复复用原结果（ADR 0043）。P4a 中共享 Operation 的 intent/回执统一记录在 roots/invocations，effects 仅保留 run-loop 与旧宿主查询；路径、参数、定义及构建身份漂移明确拒绝。会话工具也使用同一调用权威，细节见 [调用恢复](../design/invocation-recovery.md)。

## 3. 纯度约束与求值托管（宿主视角）

求值器机制本身——特殊形式白名单、fuel 计量与终止保证、效果交接与 `ActionRequest` 数据机制——是语言规范，见 [tlosure-reference.md](./tlosure-reference.md) 第 2 节，不在这里重复。本节只记 tlon 宿主侧的闸与记账方式（"DSL 描述，引擎执行"）：

- 宿主操作由 DSLAdapter 投影为 builtin，在调用边界交给 OperationExecutor 执行并返回实际结果；仅 `run-loop` 保留 ActionRequest，tlosure 不直接执行系统 I/O。
- 环境是明确的数据记录：邮件由宿主物化为 record；没有任意属性/方法访问语法，mail-field 提供字段适配。
- map 是数据：键保持字面量，值会被求值——动作选项 map 里可嵌表达式，如 `{:agent (get event :agent_id)}`。
- **动作位置校验**：`validate_member_actions` 使用全量成员词汇，支持条件、函数和传统循环；`defn` 按顺序向后续表达式引入名字。生产程序对每次实际经过的调用进行效果交接；同步分析仍可使用 collector。when、until 和 stage-if 统一使用纯函数准入；外部查询只在 actions 挂起、采样及重放。
- fuel 记账：规则声明 `:fuel` 时，门控与所有动作共享一个计数器；未声明时分别用默认门控与动作预算。配方的 `:fuel` 单独约束会话终止条件和 stage 条件；硬上限见下表。

<!-- BEGIN GENERATED: dsl-fuel-defaults -->
语言级求值预算常量（`tlosure/eval.py`，4 个；上文的默认值/上限数字以此表为准）：（本区块由 `scripts/gen_reference_docs.py` 生成，勿手改；改了会被 `--check` 打回）

| 常量 | 值 | 用途 |
|---|---|---|
| `DEFAULT_WHEN_FUEL` | 200 | `:when` 门控的默认预算 |
| `DEFAULT_FUEL` | 1000 | 动作与会话条件求值的默认预算 |
| `MAX_FUEL` | 10000 | `:fuel` 自声明的硬顶（超过即加载拒绝） |
| `DEFAULT_MAX_DEPTH` | 100 | form 嵌套深度上限 |
<!-- END GENERATED: dsl-fuel-defaults -->

### 3.1 环境契约（求值语境的可见数据）

<!-- BEGIN GENERATED: bridge-env-contracts -->
每个求值语境绑定一组固定符号、固定形状的数据（3 个顶层契约，`vocabulary.py` 的 `*Env` TypedDict 是形状的单一事实源，构建器函数是唯一构造点——引擎调用点全部委托到这里）：（本区块由 `scripts/gen_reference_docs.py` 生成，勿手改；改了会被 `--check` 打回）

| 环境契约 | 构建器 | 字段 | 类型 |
|---|---|---|---|
| `RuleEnv` | `rule_env()` | `msg` | `dict[str, Any] \| None` |
| `RuleEnv` | `rule_env()` | `event` | `Any` |
| `RuleEnv` | `rule_env()` | `self` | `SelfEnv` |
| `LoopEnv` | `loop_until_env()` | `self` | `SelfEnv` |
| `LoopEnv` | `loop_until_env()` | `state` | `LoopStateEnv` |
| `LoopEnv` | `loop_until_env()` | `limits` | `LoopLimitsEnv` |
| `StageCondEnv` | `stage_cond_env()` | `state` | `LoopStateEnv` |
| `StageCondEnv` | `stage_cond_env()` | `scratch` | `dict[str, Any]` |
| `StageCondEnv` | `stage_cond_env()` | `round` | `RoundEnv` |

嵌套类型的字段：

- `SelfEnv`：`id: str`、`name: str`、`address: str`
- `LoopStateEnv`：`iteration: int`、`error_count: int`、`token_usage: int`
- `LoopLimitsEnv`：`max_iterations: int`、`max_consecutive_errors: int`、`max_tokens: int`
- `RoundEnv`：`completing: bool`、`sending_mail: bool`
<!-- END GENERATED: bridge-env-contracts -->

## 4. Builtin 完整清单

语言级通用词汇见 [tlosure-reference.md](./tlosure-reference.md) 第 3 节。领域函数在 [vocabulary.py](../../src/tlon/agents/vocabulary.py) 的 `SCRIPT_BUILTINS` 定义；成员 actions 另接入 Operation 目录与宿主查询。when、until、stage-if 均使用同一个纯函数投影：通用函数、显式参数领域函数和 effects 为空且 control=false 的宿主 Operation。只读外部查询仍是非纯操作，不能进入这些条件，也不能由局部 fn 绕过限制。

旧隐式邮件身份、上限和 stage 谓词已删除，无兼容别名或用户文件迁移。以下写法是当前契约；加载时拒绝未知词和不在该位置作用域内的符号，执行缺失必需上下文时报错。

### 4.1 统一纯函数

<!-- BEGIN GENERATED: dsl-builtins-pure -->
`SCRIPT_BUILTINS`（27 个，按源码定义顺序）：（本区块由 `scripts/gen_reference_docs.py` 生成，勿手改；改了会被 `--check` 打回）

| 名称 | pure | 说明 |
|---|---|---|
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
| `mail-field` | 是 |  |
| `from?` | 是 | Compare an explicit mail record's sender with an explicit address. |
| `content-matches?` | 是 | Regex-search a mail's content or a plain string (e.g. ``result``). |
<!-- END GENERATED: dsl-builtins-pure -->


### 4.2 when 数据

作用域 `{msg, event, self}`。非邮件事件的 msg 为 nil；mail-field 读取显式邮件参数，from? 与 content-matches? 也不读取隐式身份。收件人条件：

```clojure
(or (contains? (or (mail-field msg :to) []) (get self :address))
    (contains? (or (mail-field msg :cc) []) (get self :address)))
```

外部查询如 member-roster、sleeping?、running?、lazy? 只能在 actions 中调用，结果按程序调用记录后由 let/if 等普通表达式处理。

### 4.3 until 数据

作用域 `{self, state, limits}`。state 包含 iteration、error_count、token_usage；limits 包含 max_iterations、max_consecutive_errors、max_tokens。error_count 是连续 think 失败数，成功后归零；token_usage 是 session 累计 stage LLM 消耗。

```clojure
(or (>= (get state :iteration) (get limits :max_iterations))
    (>= (get state :error_count) (get limits :max_consecutive_errors))
    (>= (get state :token_usage) (get limits :max_tokens)))
```

### 4.4 stage-if 数据

作用域 `{state, scratch, round}`。round 的 completing 表示单独的 finish-turn/sleep-until/exit-agent；sending_mail 表示待派发邮件工具，两者独立。交付评审使用 sending_mail。scratch 中评审结果可以缺省：没有 verdict 时通过与失败比较均为 false。

```clojure
(= (get state :iteration) 0)
(get round :sending_mail)
(= (get (get scratch :critique) :passed) true)
(= (get (get scratch :critique) :passed) false)
```

核心 get 对业务 map 的缺键仍按语言规则返回 nil 或显式默认值；这与运行时遗漏整个必需作用域绑定是不同错误。三处均通过显式数据调用纯函数。

### 4.5 程序效果与统一邮件操作

通用工作流效果：

| 调用 | 返回值 |
| --- | --- |
| `(state-get {:key "key"})` | 成员私有状态；缺失返回 `nil`，读取结果被记录供重放 |
| `(state-put {:key "key" :value value})` | 写入值；状态更新与统一 invocation 回执同事务提交 |
| `(model-call {:prompt "任务数据" :max_tokens 256})` | `{:content "..." :usage {...}}`，usage 保留 provider 的 snake_case 键，如 `:completion_tokens` |

邮件是统一 Operation，不再构造旧邮件 `ActionRequest`。DSL 使用一个命名参数 map，键与 JSON Tool 参数均为 snake_case：

| 调用 | 语义 |
| --- | --- |
| `(mail-send {:to ["成员地址"] :subject "主题" :content "正文" :request_id "稳定键"})` | 返回真实 `{:mail_id "..." :request_id "..." :local_recipients [...] :gateways [...] :message "..."}` |
| `(mail-forward {:mail_id (mail-field msg :id) :to ["成员地址"]})` | 从自己的邮箱显式取来源；自动生成 Fwd 主题及归因正文，另起线程 |
| `(mail-check-inbox {})` | 查询自己的未读邮件 |
| `(mail-search {:mail_id "邮件ID"})` | 查询自己邮箱的邮件全文；也支持 query 关键词检索 |
| `(mail-mark-read {:mail_ids [(mail-field msg :id)]})` | 显式标读自己的邮件 |

`:subject`、`:request_id` 可省略；宿主默认生成稳定调用 ID。回复显式传 `:in_reply_to`，收件人显式传 `:to`，不会隐式从触发邮件推断。完整参数以 [邮件操作 schema](../../src/tlon/operations/mail.py) 为权威。拒收和未知结果分别抛带结构化 data 的 `RejectedError` / `UnknownOutcomeError`，不返回伪成功；Tool 入口将同一错误投影为失败结果。请求 ID 相同但内容不同会冲突。邮局已有回执覆盖已入箱而程序尚未记结果的崩溃窗口；unknown 必须复用原 request_id 重试。接受只证明本地信箱提交，网关字段不证明外部送达。

`model-call` 不附工具，保留人格、RULES 和协议。输出上限不超过请求值、规则剩余额度和成员 `llm.max_tokens`；程序直接调用按获准输出上限持久预留且不退款，重放还原既有占用、不再次扣额。已记录结果直接复用；模型调用属于非幂等写入，已分发但未记录结果时保留 unknown，不自动重发。

`mail-field` 读取宿主物化邮件 record 的 `:id`、`:from`、`:to`、`:cc`、`:subject`、`:content`、`:timestamp`、`:thread-id`、`:in-reply-to`、`:forward-of`、`:endorsing`；`nil` 输入返回 `nil`，非 record 输入及未知字段报错。随包 [报社示例](../../src/tlon/resources/examples/newspaper/README.md) 演示先冻结期刊再逐人幂等投递，不自动创建默认成员。

宿主操作由成员 OperationRegistry 统一追加，均接收一个命名参数 map；完整清单与 schema 见[工具手册](tools-reference.md)。`member-spawn`、`member-start`、`member-stop`、`member-start-all` 替代旧位置参数动作入口。普通操作可在函数、条件和循环中组合；控制操作 `finish-turn`、`sleep-until`、`exit-agent` 必须独立位于规则根动作，不能嵌入组合表达式。

`run-loop` 保持 `(run-loop (quote {...}))` 形式，声明一次独立配方会话；这是当前唯一 ActionRequest 动作，参数仍由会话配方校验。

<!-- BEGIN GENERATED: dsl-action-kinds -->
效果 builtin 实际产出的 `ActionRequest` 形状（1 种 kind，生成自 `vocabulary.py` 的调用点；仅用于会话配方声明，普通宿主操作通过 Operation 执行）：（本区块由 `scripts/gen_reference_docs.py` 生成，勿手改；改了会被 `--check` 打回）

| kind | data 字段 |
|---|---|
| `run_loop` | `recipe` |
<!-- END GENERATED: dsl-action-kinds -->

### 4.6 宿主查询端口（闭包注入）

<!-- BEGIN GENERATED: bridge-query-ports -->
`make_member_builtins` 在调用方注入 `host` 时追加 4 个宿主查询（`pure=False`，只可在 actions 调用，读取注册表或投递目录；无宿主即不注册这些词；说明取自 `ScriptHostQueries` 协议方法的 docstring）：（本区块由 `scripts/gen_reference_docs.py` 生成，勿手改；改了会被 `--check` 打回）

| DSL 词 | 宿主方法 | 说明 |
|---|---|---|
| `sleeping?` | `host.is_sleeping` | Registered (config known) but not running. |
| `running?` | `host.is_running` | Currently in the run table. |
| `lazy?` | `host.is_lazy` | A member that can sleep / be drained: no ``:on :boot`` rule. |
| `member-roster` | `host.member_roster` | The addressing yellow page: one pre-formatted line per member. |
<!-- END GENERATED: bridge-query-ports -->


## 5. `run-loop` 与会话配方

<!-- BEGIN GENERATED: dsl-loop-names -->
合法名称全集（生成自 `member_spec.py`）：（本区块由 `scripts/gen_reference_docs.py` 生成，勿手改；改了会被 `--check` 打回）

- stage（`STAGE_NAMES`，5 个）：`act`、`critique`、`plan`、`revise`、`think`
- section（`SECTION_NAMES`，8 个）：`current-task`、`knowledge`、`lessons`、`mail-history`、`persona`、`protocol`、`skills`、`thread-recap`
<!-- END GENERATED: dsl-loop-names -->

`run-loop` 接收 `SessionRecipe` 数据。`quote` 保留阶段与终止条件的表达式，不提前求值。字面配方在加载时校验，动态配方在请求时校验；配方独立于成员和规则。

```clojure
(run-loop
 (quote
  {:context {:sections [(persona) (protocol) (knowledge) (mail-history 20)]}
   :stages [(think)
            (critique {:if (get round :sending_mail)})
            (act {:if (or (not (get round :sending_mail)) (not (= (get (get scratch :critique) :passed) false)))})
            (revise {:if (= (get (get scratch :critique) :passed) false)})]
   :until (or (>= (get state :iteration) (get limits :max_iterations)) (>= (get state :error_count) (get limits :max_consecutive_errors)) (>= (get state :token_usage) (get limits :max_tokens)))
   :max-iterations 10
   :max-consecutive-errors 3
   :max-tokens 1000000
   :reflect {:enabled true :interval-minutes 15}
   :fuel 1000
   :replies {:error "出错了。" :timeout "超时了。"}}))
```

`:context`、`:stages`、`:until`、三项资源上限和 `:reflect` 必填；`:fuel` 与 `:replies` 可选。

| 键 | 类型 | 语义 |
|---|---|---|
| `:context` | map | 只含 `:sections`——上下文 section 向量 |
| `:stages` | 向量 | stage form 列表，**不得为空**；每步按序解释执行 |
| `:until` | 表达式 form | 终止谓词，每步 pre-think 求值 |
| `:max-iterations` | int | 迭代硬顶（引擎无条件执行，`:until` 无法覆盖） |
| `:max-consecutive-errors` | int | 连续错误上限（think 成功一次即清零重计；散布的错误不累计） |
| `:max-tokens` | int | session 级累计 token 预算硬顶（ADR 0023；引擎无条件执行，与 `:max-iterations` 取或、先到先停；计量口径见 5.3 节） |
| `:reflect` | map | `{:enabled bool :interval-minutes int}`——周期性后台记忆审计（`ReflectionEngine`：间隔 + 最少完成会话数双门控，对记忆做整理/增删） |
| `:fuel` | int | 可选；约束 `:until` 与 stage `:if` 的求值，默认 1000，上限 10000 |
| `:replies` | map | 可选；三条面向用户的终止回信措辞覆盖，键只允许 `:iteration-limit` / `:error` / `:timeout`，值必须是非空字符串（未知键、非字符串、空串均加载拒载）。纯数据原文使用——不求值、不渲染模板；未声明的键回落引擎内置文案（`loop_driver.py` 的三条常量）。终止回信只发给责任集内的**人类**发件人（ADR 0027，锚点经 ADR 0028 决策 7 修订）：责任集内的非人类发件人（agent、postmaster、外部地址）只记 journal + 日志，不发信 |

### 5.1 `:sections`（`SECTION_NAMES`，8 种）

`persona` 与 `protocol` 进 system prompt；`knowledge`、`lessons`、`skills`、`current-task`、`thread-recap` 与 `mail-history` 追加到对话上下文（`ContextBuilder`）。section 名不得重复。**位置语义例外（ADR 0017 阶段 4，2026-09-17）**：`knowledge` 不随向量顺序落位——它固定注入为整个 context 的最后一条（触发语之后、压缩锚点 anchor 之外），声明只控制「是否包含」；其余段按向量顺序追加。

**排序原则（缓存友好）**：LLM 供应商按请求前缀逐字节命中缓存定价，断裂点之后的内容全部按未命中计费。因此 section 向量应按变动频率排序——几乎不变的段（`lessons`、`skills`）靠前，尾部增长的段（`mail-history`）居中，每会话必变的段（`thread-recap`）与触发语靠后。内置原型统一为 `(persona) (protocol) (lessons) (skills) (knowledge) (current-task) (mail-history F R) (thread-recap)`（`knowledge` 的声明位置是历史惯例，注入位置恒为最末）；触发语永远由引擎追加在最后（knowledge 之前）——ADR 0028 起它是固定唤醒模板「你有 N 封未读邮件」，不含邮件本体。两段式 `(mail-history F R)`（ADR 0025）让 mail-history 内部的前段（冻结段 = 最早 F 封）写满后逐字节稳定——新邮件到来时前缀断裂点收缩到滑动段的 R 封内。

| section | 参数 | 语义 |
|---|---|---|
| `(persona)` | 无 | 成员人格文件（SOUL/IDENTITY/RULES 等）进 system prompt |
| `(protocol)` | 无 | 协作协议文本进 system prompt |
| `(knowledge)` | 无 | 记忆块拼成 `[已知信息]` 用户消息；**固定注入为 context 最后一条**（触发语之后、anchor 之外，ADR 0017 阶段 4）——声明位置不决定注入位置，只决定是否注入；JIT 检索的查询是责任集各邮件的 subject+正文拼接（ADR 0028） |
| `(lessons)` | 无 | 成员继承的 `config/LESSONS.md`（退休成员蒸馏、随原型包传递，见 [design/memory.md](../design/memory.md)「退休蒸馏」）拼成 `[传承教训]` 用户消息；无该文件则跳过该 section（空态惯例同 knowledge/skills） |
| `(skills)` | 无 | 三层 skill 目录的索引（name + description + SKILL.md 路径）拼成 `[可用技能]` 用户消息；模型用 file-read 自读正文（ADR 0010，[design/skills.md](../design/skills.md)） |
| `(current-task)` | 无 | 当前 doing 任务（至多一条：id + 标题 + 状态 + 描述）拼成 `[当前任务]` 用户消息；无 doing 任务则跳过该 section（空态惯例同 knowledge/skills），无 paused 回退。上下文在 session 首步构建一次（anchor），任务中途变化下个 session 才反映 |
| `(thread-recap)` | 无 | 最近一个已完成 session 的简报（触发语、工具调用摘要、结论）拼成 `[最近会话摘要]` 用户消息：确定性重建自 session journal（ADR 0007），不调 LLM、不等批触发摘要；与 `mail-history` 互补——原文看邮件，简报看「我上一轮做了什么」。只覆盖最近一轮，成本不随历史膨胀；上一轮未完成（无 `session/end`）或从未跑过 session 时跳过该 section。ADR 0028 起与线程无关——session 身份不再由邮件派生，简报对象是本成员最近完成的那个 session |
| `(mail-history n)` | 一个非负 int | 滑动窗口：取邮箱（inbox+sent 按时间正序合并）最近 n 封邮件进对话上下文 |
| `(mail-history f r)` | 两个非负 int | 两段式（ADR 0025）：冻结段 = 合并时间序列表的最早 f 封（写满后逐字节稳定、永驻 prompt 前缀）+ 滑动段 = 最近 r 封；总量不足 f+r 时重叠部分按 mail id 去重只渲染一次；渲染保持时间正序（冻结段在前）。空正文邮件不渲染等规则与一段式相同 |

### 5.2 五个 stage（`STAGE_NAMES`）

stage form：`(name)` 或 `(name {opts})`。所有 stage 支持 `:if <表达式>`（用 4.3 的谓词；条件为假或求值失败都跳过该 stage）。`plan` 额外支持 `:max-steps <正整数>`（默认 5）；`critique` 额外支持 `:instructions <非空字符串>`，追加专项评审标准，不替代 PASS/FAIL 输出协议。其余 opts 键加载即拒绝。

| stage | 语义（实现见 [src/tlon/agents/stages/](../../src/tlon/agents/stages)） |
|---|---|
| `think` | 一次 LLM 调用，产出本轮响应（进响应链）。成功：清零连续错误计数。失败：记一次错误 + 推进迭代，不发邮件，由 `:until` 后续终止循环 |
| `act` | 声明并发工具执行并收集结果；邮件工具只投递，不完成会话。finish-turn/sleep-until/exit-agent 必须单独一轮，成功后记录调度意图并完成当前会话；混用其他工具整轮拒绝。 |
| `plan` | 额外一次 LLM 调用生成 ≤ `:max-steps` 步计划；写入 `scratch.plan` 并以 `[执行计划]` 用户消息入上下文。失败软降级（跳过，不记错误） |
| `critique` | 额外一次 LLM 评审：评审输入 = 会话 context + 本轮待审输出（完整调用参数、调用编号及运行时执行状态，以普通文本渲染为 assistant 消息；草稿不是执行回执）+ 评审指令；首行去空白后必须恰好是 `PASS`/`FAIL`，结论写入 `scratch.critique = {:passed bool :feedback str}`；首行无法解析时跳过本次评审（记 warning、清除旧 verdict——显式 passed=true 与 passed=false 比较均不成立，revise 不触发），不记录为 PASS。异常同样清除旧 verdict；完成动作是否要求明确 PASS 由后续 stage 的 `:if` 决定。失败软降级 |
| `revise` | 不调 LLM；仅在 FAIL 时把评审意见、完整待审输出及真实执行状态以普通用户消息喂回上下文，供下一轮 `think` 修正；空意见也保留草稿和未执行说明，不伪造工具调用/回执。消费过的 verdict 随之清除（`scratch.critique` 置 nil），避免陈旧 FAIL 在 critique 被条件跳过的后续轮次重复触发 |

stage 是"纯变换 + 声明意图"：返回值是 `StageOutcome` 数据（[src/tlon/agents/stages/outcome.py](../../src/tlon/agents/stages/outcome.py)），由 `LoopDriver._apply_outcome` 统一落地——应用顺序为：上下文追加 → scratch → 计数器/状态 → 工具结果 → 响应链 → 工具派发 → 完成标记。

### 5.3 循环推进与终止（`LoopDriver.step_session`）

每一步的顺序：

1. **引擎硬顶（双停机线，ADR 0023）**：`iteration ≥ :max-iterations` 或 `token_usage ≥ :max-tokens` 任一到顶即结束会话（先到先停，`session/end` reason 分别为 `max_iterations` / `max_tokens`）——在任何 `:until` 求值之前。责任集内有**人类**发件人时回复：回复正文由运行时确定性生成，明确任务未完成，仅依据已收集的邮件工具回执报告确认提交、拒绝与结果未知并去重，不把草稿或评审意见当作发信事实（ADR 0040）；`:replies/:iteration-limit` 只能附加为预设提示，不能替换事实报告；责任集内没有人类发件人时不发信（ADR 0027）。
2. **`:until` 求值**（4.2 谓词）：为真则终止——错误上限触发回复"抱歉，处理邮件时出现了错误。"（`:replies/:error` 可覆盖），否则回复"思考时间过长，请简化您的问题。"（`:replies/:iteration-limit` 可覆盖）。求值失败回退到引擎默认检查（迭代/错误/token 上限），循环必然停止。调度器侧的异常终止（会话超时 / stage 未捕获错误）回复"抱歉，处理这封邮件超时了，请稍后重试。"（`:replies/:timeout`）或错误措辞（`:replies/:error`）。**以上所有终止回复都只发给责任集内的人类发件人（ADR 0027，锚点经 ADR 0028 决策 7 修订）**：按发件人地址去重一人一封，`in_reply_to` 取该发件人在责任集内最新一封；责任集内的非人类发件人（agent、postmaster、外部地址）不发信，只照常记 `session/end` 并加一条 `termination_reply_suppressed` 日志；`:replies` 覆盖语义不受影响（有人类发件人时仍生效）。判定依据是 `TlonPaths.known_human_ids()`——有持久邮箱的人类 id 集合，每次发送判定实时重查。
3. 按序解释 `:stages`；`think` 失败则本步提前返回。
4. 若 stage 向量不含 `act`（或本轮 act 未推进迭代），引擎自行推进迭代计数，保证硬顶始终生效。

**token 计量口径（P4b 修订 ADR 0023）**：run-loop 创建 recipe.max_tokens 的独立 total 根，stage（think/critique/plan）、compaction 摘要及工具内部模型调用均计入。成员 LLMClient 统一在 ProgramStore 原子预留，成功取可靠的 usage.total_tokens，或完整 prompt_tokens + completion_tokens 结算；无可靠 usage、取消或提交未知时保留预留。实际 total 超过估算时记录真实耗用并阻止后续新调用，估算不是 provider 消费的硬上界。session.token_usage 与 journal tokens 只是账本投影，不能再次独立累加收费；预算通知不调用模型。

程序直接调用使用成员 llm.max_tokens 的 output 根，按获准输出额度预留且成功不退款；run-loop 不消费该程序输出池。底层模型响应与预算结算同事务保存，恢复不重置、重复扣额或自动重发未知请求。额度与有效 UTC deadline 冻结；取消只影响当前进程任务树，不是跨重启的永久撤销。ProgramStore user_version=2 拒绝旧格式，无迁移或自动清库。完整契约见 [执行作用域](../design/execution-scope.md)。

正常会话完成由 act 收集到独立结束工具的成功结果驱动；邮件工具没有结束语义。`:until` 只是限额与故障的兜底。

## 6. 普通程序与邮件读状态

所有规则使用 `{msg, event, self}`，动作共享求值环境，所以前一个表达式定义的函数可在后续使用。循环或函数中的效果使用同一调用边界，结果记录后继续表达式；同步 collector 只用于分析请求数据。

邮件的读状态不表示规则类型或调度状态。处理结束、进入等待均不隐式标读。程序用 `(mail-mark-read {:mail_ids [(mail-field msg :id)]})` 标记当前来源邮件，模型工具 `mail-mark-read` 可选择具体邮件。重复通知、启动和周期扫描按邮件及规则身份去重，已排队、执行中及已完成分派不会重复启动。

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

### 8.1 短回合回复

```clojure
{:version 1
 :execution {:level :L0}
 :rules
 [{:name "respond"
   :on :mail-delivered
   :when (or (contains? (or (mail-field msg :to) []) (get self :address)) (contains? (or (mail-field msg :cc) []) (get self :address)))
   :actions
   [(run-loop
     (quote
      {:context {:sections [(persona) (protocol) (lessons) (skills)
                            (knowledge) (current-task) (mail-history 20 10)
                            (thread-recap)]}
       :stages [(think)
                (critique {:if (get round :sending_mail)})
                (act {:if (or (not (get round :sending_mail)) (not (= (get (get scratch :critique) :passed) false)))})
                (revise {:if (= (get (get scratch :critique) :passed) false)})]
       :until (or (>= (get state :iteration) (get limits :max_iterations)) (>= (get state :error_count) (get limits :max_consecutive_errors))
                  (>= (get state :token_usage) (get limits :max_tokens)))
       :max-iterations 3
       :max-consecutive-errors 3
       :max-tokens 32000
       :reflect {:enabled false :interval-minutes 15}}))]}
  {:name "boot" :on :boot :actions []}]}
```

### 8.2 直接转发

```clojure
{:version 1
 :execution {:level :L0}
 :parameters {:to {:type :string-list :required true}}
 :rules
 [{:name "relay"
   :on :mail-delivered
   :when (or (contains? (or (mail-field msg :to) []) (get self :address)) (contains? (or (mail-field msg :cc) []) (get self :address)))
   :actions [(mail-forward {:mail_id (mail-field msg :id) :to ${to}}) (mail-mark-read {:mail_ids [(mail-field msg :id)]})]}
  {:name "boot" :on :boot :actions []}]}
```

这两个例子使用同一种规则格式。前者请求会话，后者直接转发；可以在同一个成员的同一程序中组合两者。更多事件与宿主查询的例子见 [Adam](../../src/tlon/agents/adam.py)。

## 9. 常见错误与加载失败行为

| 错误 | 后果（出处） |
|---|---|
| 文件不是合法 EDN / 有尾随内容 | `EDNParseError`（带行列），成员拒绝启动（`edn.loads`） |
| 顶层不是 map、`:rules` 缺失/为空/不是向量 | `MemberSpecValidationError`，拒绝启动（`MemberSpec.from_dict`） |
| `:version` 不是 1 | 同上（仅支持 `MEMBER_SPEC_VERSION = 1`） |
| 缺必填字段（`:name`、`:on`、会话配方的必填项等） | 同上——total 语义，无默认值 |
| `:execution` 不是 map、缺 `:level`、`:level` 非 `:L0`/`:L1`/`:L2` | 同上（`_execution_from`） |
| 类型错误（int 位置写 bool/string 等） | 同上（bool 永不被接受为 int） |
| 未知 section / stage 名，section 重复，`mail-history` 参数个数不是 1~2 个或参数不是非负 int | 同上（`_validate_sections` / `_validate_stages`） |
| stage opts 键非 `:if`（`plan` 可加 `:max-steps`，`critique` 可加 `:instructions`） | 同上（`_validate_stage_opts`） |
| 配方 `:replies` 键超出 `:iteration-limit` / `:error` / `:timeout`，或值不是非空字符串 | 同上（`_replies_from`） |
| `:when` / `:until` / `:if` 引用未注册 builtin 或作用域外符号 | 同上（`validate_member_spec` 统一静态校验） |
| 旧规则顶层 `:loop` | 直接拒绝加载，改为 actions 中的 `run-loop` 请求，无兼容路径 |
| `:fuel` 非正数或超过 10000 | 同上（`MAX_FUEL` 硬顶） |
| 未知顶层/规则/配方/`:execution` 段内键 | **不报错**，记 warning 忽略（前向兼容） |
| `:when` 求值失败 / fuel 耗尽 | 按不命中处理，不隐式改变邮件读状态 |
| 动作求值失败或引用未定义符号 | 跳过本条规则并记录日志，已收集但未执行的请求不生效 |

加载总原则（`load_member_spec` 与 `validate_member_spec`）：EDN 前端只做结构校验（类型、形状），builtin 作用域校验全在单一校验器 `validate_member_spec`；**任何失败都是整体拒绝启动**，没有"部分可用"的中间态。

## 10. 社会政策文件（`~/.tlon/policy.tlon`，ADR 0022）

MemberSpec 之外还有一种 DSL 文件：society 级政策文件 `policy.tlon`（`paths.policy_file()`），由**人类直接编辑**（不进任何成员的 `allowed_paths`，不走原型/staging 通道）。它属于第五个求值语境——社会政策语境，也是唯一的 pull 型语境：宿主在固定求值点（spawn 前、staging 提案前、装配前）主动调用政策 hook，而非事件触发。设计与机制/策略切分见 [design/society-policy.md](../design/society-policy.md)。

```clojure
{:version 1
 :policy [(defn helper [x] ...)            ; 共享定义向后穿线（ADR 0021）
          (defn tilt [subject lineages] ...)       ; spawn 配额乘数（float）
          (defn solvent? [subject lineages] ...)   ; 血缘偿付门（bool）
          (defn full-member? [member] ...)]}       ; 信任阶梯（bool）
```

- **hook 契约是保留函数名**：宿主求值完 `:policy` 序列后按名调用 `tilt` / `solvent?` / `full-member?`（`POLICY_HOOK_NAMES`）。政策文件的 form 序列穿线进**内嵌默认政策**（`agents/policy.py` 的 `DEFAULT_POLICY_EDN`）的 env——未覆盖的 hook 保持默认实现，覆盖方可以复用默认 helper（`lineage-value` / `efficiency` / `society-mean-efficiency`）。文件缺失、解析/校验/求值失败 → 整体回退默认政策。
- **数据物化注入**：hook 收到的是宿主预先收集的原始事实（无回调端口）——`subject` 是目标血缘 `{:name str :total_tokens int :members [{:id str :endorsements_received int}]}`（无数据为 nil），`lineages` 是全社会血缘 vector（同形状），`member` 是 `{:id str :spawned_by str|nil :endorsements_received int}`。
- **fail-open**：任何 hook 求值异常（含 fuel 耗尽）回落安全默认——`tilt` 1.0、`solvent?` true、`full-member?` true；政策簿记永不阻塞 spawn 或装配。
- 加载与求值共用语境级 fuel 顶（`POLICY_FUEL` = `MAX_FUEL`），无 per-hook 自声明。

### 10.1 `POLICY_BUILTINS`（政策语境词汇）

政策语境注册表是成员注册表的**纯子集**（语言核心通用词汇 + 算术）：无动作词、无宿主查询回调——求值副作用自由由构造保证。特殊形式（`if` / `let` / `and` / `or` / `defn` / `fn` / `map` / `filter` / `reduce` 等）由求值器提供，不在注册表内。

<!-- BEGIN GENERATED: dsl-builtins-policy -->
`POLICY_BUILTINS`（24 个，按源码定义顺序）：（本区块由 `scripts/gen_reference_docs.py` 生成，勿手改；改了会被 `--check` 打回）

| 名称 | pure | 说明 |
|---|---|---|
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
<!-- END GENERATED: dsl-builtins-policy -->
