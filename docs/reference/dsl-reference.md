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
 :when (addressed-to-me? msg)
 :actions [ ... ]         ; 可选，默认 []
 :fuel 500}               ; 可选
```

| 键 | 类型 | 必填 | 语义 |
|---|---|---|---|
| `:name` | 字符串 | 是 | 规则名，用于日志与报错 |
| `:on` | 关键字 | 是 | 触发事件类型；`:boot` 或 `nil` 表示启动规则（见下） |
| `:when` | 表达式 form | 否 | 纯谓词门控；缺省 = 恒真 |
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

纯表达式，环境为 `{msg, event, self}`。`msg` 为来源邮件，非邮件事件为 `nil`；`event` 为原始事件负载；`self` 含 id、name 和 address。门控为假或求值失败时跳过规则，不隐式把邮件标读。

### 2.3 命中后的执行顺序

规则按 spec 顺序匹配。统一求值 `:actions`，共享 collector 保留函数、条件和传统循环内产生的全部 `ActionRequest`；求值成功后按产生顺序执行。`run-loop` 按本次配方执行会话，完成后继续剩余效果，同一成员串行推进程序。

后续效果已在原始规则求值时构造，没有另一个完成动作求值环境，不额外绑定 `result`。当前阶段不能将实际模型返回值作为普通表达式结果继续分支。

## 3. 纯度约束与求值托管（宿主视角）

求值器机制本身——特殊形式白名单、fuel 计量与终止保证、求值无副作用与 `ActionRequest` 数据机制——是语言规范，见 [tlosure-reference.md](./tlosure-reference.md) 第 2 节，不在这里重复。本节只记 tlon 宿主侧的闸与记账方式（"DSL 描述，引擎执行"）：

- 动作 builtin（`pure=False`，见第 4.5 节）只校验参数并产出 `ActionRequest` 数据，由引擎在求值结束后执行；求值过程本身永不产生副作用。
- 环境值对表达式不透明：没有属性/方法访问语法，`Mail` 只能经 builtin 检查。
- map 是数据：键保持字面量，值会被求值——动作选项 map 里可嵌表达式，如 `{:agent (get event :agent_id)}`。
- **动作位置校验**：`validate_member_actions` 使用全量成员词汇，支持条件、函数和传统循环；`defn` 按顺序向后续表达式引入名字。统一 collector 收集全部嵌套效果，外层返回 `nil` 不会丢失请求。纯门控只能使用纯子集词汇。
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
| `RuleEnv` | `rule_env()` | `msg` | `Mail \| None` |
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

语言级通用词汇（`CORE_BUILTINS` / `ARITHMETIC_BUILTINS`，[src/tlosure/builtins.py](../../src/tlosure/builtins.py)，ADR 0018 修订）的清单见 [tlosure-reference.md](./tlosure-reference.md) 第 3 节；本节只列 tlon 领域词汇。领域词汇的模块级注册表在 [src/tlon/agents/vocabulary.py](../../src/tlon/agents/vocabulary.py)；`make_member_builtins` 把 `TRIGGER_BUILTINS` + `SCRIPT_BUILTINS` + `ARITHMETIC_BUILTINS` 合并后再加上 4.5 的动作 builtin（`pure=False`），构成成员统一注册表——合并后的纯子集可用于 `:when`、动作参数等一切纯位置。另有四个读注册表状态的宿主查询由调用方闭包注入（`ScriptHostQueries`，实现为注册表模块里的 `RegistryQueries`，Adam 与注册表装配的全体成员共用同一份注入），清单见第 4.6 节。它们不在任何模块级注册表——无宿主注入的注册表里没有这些名字，引用即加载校验失败。

**版本纪律**（ADR 0020，[docs/design/bridge-api.md](../design/bridge-api.md)；2026-09-28 修订）：本节清单是 spec 作者面对的稳定界面。演进默认 additive；删除走 deprecation 生命周期——发现坏设计先标记废弃（加载期 warning + 文档标注 + 迁移窗口），窗口后删除并机械迁移磁盘 spec（语料小且可触达，`loads`/`dumps` 对称使迁移可测试）。语义只紧不松：修复式收紧（原来放行的坏输入改为加载拒绝）允许。新增词汇必须声明所属语境注册表，不存在"所有语境可见"的词；加载期校验（`validate_member_spec`）是引用错误的唯一守门人，运行期不存在"未定义词汇"这一类错误。

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

环境 `{self, state, limits}`：`state` = `{:iteration n :error_count n :token_usage n}`（`error_count` 是**连续** think 失败数——think 成功一次即清零，不是会话累计值；`token_usage` 是 session 级累计 stage LLM token 消耗，ADR 0023），`limits` = `{:max_iterations n :max_consecutive_errors n :max_tokens n}`。键缺失安全返回 `false`，不抛错。

<!-- BEGIN GENERATED: dsl-builtins-loop -->
`LOOP_BUILTINS`（5 个，按源码定义顺序）：（本区块由 `scripts/gen_reference_docs.py` 生成，勿手改；改了会被 `--check` 打回）

| 名称 | pure | 说明 |
|---|---|---|
| `iteration-limit-reached?` | 是 | ``state.iteration`` has reached ``limits.max_iterations``. |
| `error-limit-reached?` | 是 | ``state.error_count`` (consecutive think failures; a successful think resets it to 0) has reached ``limits.max_consecutive_errors``. |
| `token-limit-reached?` | 是 | ``state.token_usage`` (session-cumulative stage LLM tokens, ADR 0023) has reached ``limits.max_tokens``. |
| `=` | 是 |  |
| `not` | 是 |  |
<!-- END GENERATED: dsl-builtins-loop -->

### 4.3 `STAGE_COND_BUILTINS`（stage `:if` 谓词）

环境 `{state, scratch, round}`；scratch 是会话暂存区，round 同时提供 completing 与 sending_mail：前者表示单独的 finishTurn/sleepUntil/exitAgent，后者表示将派发邮件工具。`completing?` 与 `sending-mail?` 是独立谓词；交付评审应使用后者。

<!-- BEGIN GENERATED: dsl-builtins-stage-cond -->
`STAGE_COND_BUILTINS`（8 个，按源码定义顺序）：（本区块由 `scripts/gen_reference_docs.py` 生成，勿手改；改了会被 `--check` 打回）

| 名称 | pure | 说明 |
|---|---|---|
| `critique-failed?` | 是 | A critique verdict exists in scratch and did not pass. |
| `critique-passed?` | 是 | A critique verdict exists in scratch and passed. |
| `plan-exists?` | 是 | Scratch holds a non-empty plan string. |
| `first-iteration?` | 是 | ``state.iteration`` is 0 (the loop's first round). |
| `completing?` | 是 | The round's pending response would end the session if dispatched. |
| `sending-mail?` | 是 | The round would dispatch an outgoing mail tool, independently of completion. |
| `=` | 是 |  |
| `not` | 是 |  |
<!-- END GENERATED: dsl-builtins-stage-cond -->

### 4.4 `SCRIPT_BUILTINS`（规则纯访问器）

环境 `{event, self}`：`event` 是触发事件的负载 map。

<!-- BEGIN GENERATED: dsl-builtins-script -->
`SCRIPT_BUILTINS`（9 个，按源码定义顺序）：（本区块由 `scripts/gen_reference_docs.py` 生成，勿手改；改了会被 `--check` 打回）

| 名称 | pure | 说明 |
|---|---|---|
| `get` | 是 | Read ``key`` from a dict payload; missing keys yield ``default``. |
| `str` | 是 | Concatenate arguments into one string. |
| `split` | 是 | Split a string on a separator into a vector of strings. |
| `join` | 是 | Join a vector of strings with a separator into one string. |
| `substring` | 是 | ``s[start:end]`` — Python slice semantics, total by construction. |
| `contains?` | 是 |  |
| `count` | 是 | Item count of a vector / map / string; ``nil`` counts as 0. |
| `=` | 是 |  |
| `not` | 是 |  |
<!-- END GENERATED: dsl-builtins-script -->

### 4.5 动作 builtin（`pure=False`，产出 `ActionRequest`，引擎执行）

动作由 `make_member_builtins` 注入，能力白名单为 `MEMBER_ACTION_NAMES`。所有规则共用这些词；它们可以出现在函数、条件和传统循环中。效果执行受实时权限和四态门控约束。

| 名称 | 签名 | 语义 |
|---|---|---|
| `spawn` | `(spawn prototype [options])` | 经注册表从原型派生一个新成员（统一成员模型，ADR 0006：与手建成员同构、永不删除）；`prototype` 必填字符串，`options` 只允许 `{:name "..."}`；无名时自动生成 `"{prototype}-{随机4位}"` |
| `emit` | `(emit content [to])` | 发邮件；`content` 必填字符串，`to` 为地址字符串向量；**省略 `to` 时回复触发邮件的发件人**（无触发邮件则收件人为空、发信失败仅记日志） |
| `start` | `(start {:agent id :reason s})` | 发布启动请求；`:agent` / `:reason` 均为必填非空字符串 |
| `stop` | `(stop {:agent id :reason s})` | 发布停止请求；参数同上 |
| `start-all` | `(start-all {:reason s})` | 对"声明了 `:on :boot` 的成员"集合逐个发启动请求；**不允许 `:agent`**；候选集展开依赖宿主查询注入——无宿主的注册表运行时会抛 `EvalError` |
| `forward` | `(forward {:to [...] :cc [...] :subject s})` | 转发当前规则的原始邮件；会话后仍保留来源。`:to` 必填非空地址向量，`:cc`、`:subject` 可选；新信以 `forward_of` 记录来源，无来源时跳过并记录 warning |
| `run-loop` | `(run-loop (quote {...}))` | 请求按独立配方运行一次会话，结束后继续剩余效果；可用于任意事件、条件及传统循环 |
| `mark-read` | `(mark-read)` | 显式标读当前触发邮件；无来源时不操作 |

<!-- BEGIN GENERATED: dsl-action-kinds -->
动作 builtin 实际产出的 `ActionRequest` 形状（7 种 kind，生成自 `vocabulary.py` 的调用点；`start-all` 不产出自己的 kind——它按宿主查询 `start_all_candidates` 的候选集展开为多个 `kind="start"` 请求）：（本区块由 `scripts/gen_reference_docs.py` 生成，勿手改；改了会被 `--check` 打回）

| kind | data 字段 |
|---|---|
| `run_loop` | `recipe` |
| `mark_read` |  |
| `spawn` | `prototype`、`name` |
| `emit` | `content`、`to` |
| `start` | `agent_id`、`reason` |
| `stop` | `agent_id`、`reason` |
| `forward` | `to`、`cc`、`subject` |
<!-- END GENERATED: dsl-action-kinds -->

### 4.6 宿主查询端口（闭包注入）

<!-- BEGIN GENERATED: bridge-query-ports -->
`make_member_builtins` 在调用方注入 `host` 时追加 4 个宿主查询（`pure=True`，只读注册表或投递目录、无宿主即不注册这些词；说明取自 `ScriptHostQueries` 协议方法的 docstring）：（本区块由 `scripts/gen_reference_docs.py` 生成，勿手改；改了会被 `--check` 打回）

| DSL 词 | 宿主方法 | 说明 |
|---|---|---|
| `sleeping?` | `host.is_sleeping` | Registered (config known) but not running. |
| `running?` | `host.is_running` | Currently in the run table. |
| `lazy?` | `host.is_lazy` | A member that can sleep / be drained: no ``:on :boot`` rule. |
| `member-roster` | `host.member_roster` | The addressing yellow page: one pre-formatted line per member. |
<!-- END GENERATED: bridge-query-ports -->

### 4.7 数据定义的能力词（受信能力库）

第 4.1–4.6 节全是 Python 侧注册的宿主词汇；成员注册表还并入**数据定义的词**——受信能力库 `~/.tlon/capabilities/` 里每个受信 artifact（`capability.tlon`，一个 artifact 一个词，设计与评审通道见 [../design/capabilities.md](../design/capabilities.md)）经词汇桥（`agents/capabilities/bridge.py`）校验后注册为一个 closure-backed builtin（机制见 [tlosure-reference.md](./tlosure-reference.md) §2.1.2）：

- **纯度分区与宿主词同构**：artifact 声明 `:pure true` 且 impl 只对纯注册表过静态校验的词进一切求值语境（`:when` 门控、`:actions` 等）；非纯词（组合动作词、产出 `ActionRequest`）仅 `:actions`。校验器按词的 `.pure` 标记切语境可见集，不区分词是代码还是数据。
- **生效时机**：能力词在**每次成员 spec 加载时重算**（loader 唯一读盘路径，受信库是小数据）——新批准（staging → 受信库）的词在成员**下次 wake** 自然生效，不需要重启 server；反之一个被删除/损坏的 artifact 只会让引用它的 spec 在自己的加载校验处报 unknown word。
- **求值语义**：调用能力词 = 闭包应用——body 只见声明的位置参数（零注入），body 的每一步**计入调用规则的 fuel 预算**（与宿主 builtin 的免计量内部不同）。
- **命名空间**：词名与既有 builtin、特殊形式、宿主查询词、其他受信词同名即拒载（遮蔽即拒）。
- 能力词**不出现在本节 GENERATED 的 builtin 清单里**——它们是数据不是代码常量，清单生成器扫的是 Python 注册表；在册能力词以 `list_trusted_capabilities`（`agents/capabilities/catalog.py`）/ 受信库目录为准。

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
            (critique {:if (sending-mail?)})
            (act {:if (or (not (sending-mail?)) (not (critique-failed?)))})
            (revise {:if (critique-failed?)})]
   :until (or (iteration-limit-reached?) (error-limit-reached?) (token-limit-reached?))
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
| `(skills)` | 无 | 三层 skill 目录的索引（name + description + SKILL.md 路径）拼成 `[可用技能]` 用户消息；模型用 read_file 自读正文（ADR 0010，[design/skills.md](../design/skills.md)） |
| `(current-task)` | 无 | 当前 doing 任务（至多一条：id + 标题 + 状态 + 描述）拼成 `[当前任务]` 用户消息；无 doing 任务则跳过该 section（空态惯例同 knowledge/skills），无 paused 回退。上下文在 session 首步构建一次（anchor），任务中途变化下个 session 才反映 |
| `(thread-recap)` | 无 | 最近一个已完成 session 的简报（触发语、工具调用摘要、结论）拼成 `[最近会话摘要]` 用户消息：确定性重建自 session journal（ADR 0007），不调 LLM、不等批触发摘要；与 `mail-history` 互补——原文看邮件，简报看「我上一轮做了什么」。只覆盖最近一轮，成本不随历史膨胀；上一轮未完成（无 `session/end`）或从未跑过 session 时跳过该 section。ADR 0028 起与线程无关——session 身份不再由邮件派生，简报对象是本成员最近完成的那个 session |
| `(mail-history n)` | 一个非负 int | 滑动窗口：取邮箱（inbox+sent 按时间正序合并）最近 n 封邮件进对话上下文 |
| `(mail-history f r)` | 两个非负 int | 两段式（ADR 0025）：冻结段 = 合并时间序列表的最早 f 封（写满后逐字节稳定、永驻 prompt 前缀）+ 滑动段 = 最近 r 封；总量不足 f+r 时重叠部分按 mail id 去重只渲染一次；渲染保持时间正序（冻结段在前）。空正文邮件不渲染等规则与一段式相同 |

### 5.2 五个 stage（`STAGE_NAMES`）

stage form：`(name)` 或 `(name {opts})`。所有 stage 支持 `:if <表达式>`（用 4.3 的谓词；条件为假或求值失败都跳过该 stage）。`plan` 额外支持 `:max-steps <正整数>`（默认 5）；`critique` 额外支持 `:instructions <非空字符串>`，追加专项评审标准，不替代 PASS/FAIL 输出协议。其余 opts 键加载即拒绝。

| stage | 语义（实现见 [src/tlon/agents/stages/](../../src/tlon/agents/stages)） |
|---|---|
| `think` | 一次 LLM 调用，产出本轮响应（进响应链）。成功：清零连续错误计数。失败：记一次错误 + 推进迭代，不发邮件，由 `:until` 后续终止循环 |
| `act` | 声明并发工具执行并收集结果；邮件工具只投递，不完成会话。finishTurn/sleepUntil/exitAgent 必须单独一轮，成功后记录调度意图并完成当前会话；混用其他工具整轮拒绝。 |
| `plan` | 额外一次 LLM 调用生成 ≤ `:max-steps` 步计划；写入 `scratch.plan` 并以 `[执行计划]` 用户消息入上下文。失败软降级（跳过，不记错误） |
| `critique` | 额外一次 LLM 评审：评审输入 = 会话 context + 本轮待审输出（完整调用参数、调用编号及运行时执行状态，以普通文本渲染为 assistant 消息；草稿不是执行回执）+ 评审指令；首行去空白后必须恰好是 `PASS`/`FAIL`，结论写入 `scratch.critique = {:passed bool :feedback str}`；首行无法解析时跳过本次评审（记 warning、清除旧 verdict——`critique-passed?` 与 `critique-failed?` 均不成立，revise 不触发），不记录为 PASS。异常同样清除旧 verdict；完成动作是否要求明确 PASS 由后续 stage 的 `:if` 决定。失败软降级 |
| `revise` | 不调 LLM；仅在 FAIL 时把评审意见、完整待审输出及真实执行状态以普通用户消息喂回上下文，供下一轮 `think` 修正；空意见也保留草稿和未执行说明，不伪造工具调用/回执。消费过的 verdict 随之清除（`scratch.critique` 置 nil），避免陈旧 FAIL 在 critique 被条件跳过的后续轮次重复触发 |

stage 是"纯变换 + 声明意图"：返回值是 `StageOutcome` 数据（[src/tlon/agents/stages/outcome.py](../../src/tlon/agents/stages/outcome.py)），由 `LoopDriver._apply_outcome` 统一落地——应用顺序为：上下文追加 → scratch → 计数器/状态 → 工具结果 → 响应链 → 工具派发 → 邮件 → on-complete 动作 → 完成标记。

### 5.3 循环推进与终止（`LoopDriver.step_session`）

每一步的顺序：

1. **引擎硬顶（双停机线，ADR 0023）**：`iteration ≥ :max-iterations` 或 `token_usage ≥ :max-tokens` 任一到顶即结束会话（先到先停，`session/end` reason 分别为 `max_iterations` / `max_tokens`）——在任何 `:until` 求值之前。责任集内有**人类**发件人时回复：回复正文由运行时确定性生成，明确任务未完成，仅依据已收集的邮件工具回执报告确认提交、拒绝与结果未知并去重，不把草稿或评审意见当作发信事实（ADR 0040）；`:replies/:iteration-limit` 只能附加为预设提示，不能替换事实报告；责任集内没有人类发件人时不发信（ADR 0027）。
2. **`:until` 求值**（4.2 谓词）：为真则终止——错误上限触发回复"抱歉，处理邮件时出现了错误。"（`:replies/:error` 可覆盖），否则回复"思考时间过长，请简化您的问题。"（`:replies/:iteration-limit` 可覆盖）。求值失败回退到引擎默认检查（迭代/错误/token 上限），循环必然停止。调度器侧的异常终止（会话超时 / stage 未捕获错误）回复"抱歉，处理这封邮件超时了，请稍后重试。"（`:replies/:timeout`）或错误措辞（`:replies/:error`）。**以上所有终止回复都只发给责任集内的人类发件人（ADR 0027，锚点经 ADR 0028 决策 7 修订）**：按发件人地址去重一人一封，`in_reply_to` 取该发件人在责任集内最新一封；责任集内的非人类发件人（agent、postmaster、外部地址）不发信，只照常记 `session/end` 并加一条 `termination_reply_suppressed` 日志；`:replies` 覆盖语义不受影响（有人类发件人时仍生效）。判定依据是 `TlonPaths.known_human_ids()`——有持久邮箱的人类 id 集合，每次发送判定实时重查。
3. 按序解释 `:stages`；`think` 失败则本步提前返回。
4. 若 stage 向量不含 `act`（或本轮 act 未推进迭代），引擎自行推进迭代计数，保证硬顶始终生效。

**token 计量口径（ADR 0023）**：session 内每次经 `StageServices.think` 的 stage LLM 调用（think / critique / plan）计入 `token_usage`——取 `response.usage.total_tokens`，缺失回落 `prompt_tokens + completion_tokens`，无 usage 计 0。stage 在 `StageOutcome.token_usage` 声明用量，引擎先 journal（`tokens` 事件，累加后绝对值）再改内存。compaction 摘要调用不计入；预算通知不调用模型。

正常会话完成由 act 收集到独立结束工具的成功结果驱动；邮件工具没有结束语义。`:until` 只是限额与故障的兜底。

## 6. 普通程序与邮件读状态

所有规则使用 `{msg, event, self}`，动作共享求值环境，所以前一个表达式定义的函数可在后续使用。共享 collector 保留循环或函数中产生的多个请求；每个成员按同一条效果执行路径处理它们。

邮件的读状态不表示规则类型或调度状态。处理结束、进入等待均不隐式标读。程序用 `(mark-read)` 标记当前来源邮件，模型工具 `markRead` 可选择具体邮件。重复通知、启动和周期扫描按邮件及规则身份去重，已排队、执行中及已完成分派不会重复启动。

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
   :when (or (addressed-to-me? msg) (cc-ed? msg))
   :actions
   [(run-loop
     (quote
      {:context {:sections [(persona) (protocol) (lessons) (skills)
                            (knowledge) (current-task) (mail-history 20 10)
                            (thread-recap)]}
       :stages [(think)
                (critique {:if (sending-mail?)})
                (act {:if (or (not (sending-mail?)) (not (critique-failed?)))})
                (revise {:if (critique-failed?)})]
       :until (or (iteration-limit-reached?) (error-limit-reached?)
                  (token-limit-reached?))
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
   :when (or (addressed-to-me? msg) (cc-ed? msg))
   :actions [(forward {:to ${to}}) (mark-read)]}
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
`POLICY_BUILTINS`（19 个，按源码定义顺序）：（本区块由 `scripts/gen_reference_docs.py` 生成，勿手改；改了会被 `--check` 打回）

| 名称 | pure | 说明 |
|---|---|---|
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
