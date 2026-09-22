# ADR 0018：DSL 抽取为独立包 tlosure

状态：已接受（2026-09-20，方向讨论拍板；同日修订——身份定稿与领域词汇迁出，见文末「修订」）

## 修订（2026-09-20）：身份定稿——tlosure 是纯语言，tlon 是第一宿主

方向讨论（编程语言的 core / 类库 / 业务分层对 tlosure/tlon 的启发）拍板：

1. **三层映射**：tlosure 核心 = 带 fuel 的 Lisp 风格通用语言；tlon runtime（`tlon/{base, memory, organization, sandbox, tools, agents, prototypes}`）= tlosure 的第一宿主，即 tlon agents 与社会的运行时；`tlon_server` / `tlon_client` / `tlon_launcher` + `apps/webui` + `apps/operator` = 基于 runtime 包装的应用软件（ADR 0019 第二步已把原应用层物理拆为这三包）。后两层的边界已由 import-linter DAG 固话（server/client/launcher 居 DAG 顶端）。**未来方向**：tlon runtime 与 tlon web server 可分离为独立分发，目前不拆。
2. **领域词汇迁出 tlosure**（修订 §1 的边界）：mail 谓词、loop 谓词、stage 谓词、动作族（`spawn`/`emit`/`start`/`stop`/`start-all`/`forward`）、`*_ACTION_NAMES`、`ScriptHostQueries`、`make_member_builtins`、`validate_member_action` 全部是 tlon 领域词汇，迁往 `tlon/agents/vocabulary.py`。tlosure 只留：edn reader/printer、eval 内核（special forms、fuel）、`Builtin`/`EvalError`/`ActionRequest` 契约类型、`validate_form`、通用词汇（`=`/`not`/`contains?`/`get`/`str`/算术族）。`validate_member_action` 引 `MEMBER_ACTION_NAMES` 是核心命名业务概念的漏缝，随迁移修复。
3. **`MailView` 退役**（修订 §3）：协议倒置只在跨包边界需要；词汇迁入 tlon 后 mail 谓词直接用 `Mail` 类型。`self.address` env 数据注入保留——env-as-data 使谓词保持纯函数，与分层无关。
4. **三条原则固化**：① 核心命名检验——一个构造能不提及 member/mail/stage 就解释清楚才配进 tlosure；② 词汇走注册表，`SPECIAL_FORMS` 冻结，宿主新能力一律以 builtin 注册；③ env 段（`self`/`state`/`limits`/`scratch`/`round`/`event`/`msg`/`result`）是 tlon 的宿主契约，不属于语言规范。

## 背景

DSL（EDN 子集，ADR 0001）及其求值器内核自始内嵌在 `tlon.agents` 下，与成员引擎（`member_runtime` / `on_complete` / `loop_driver`）混居一包。实际上 DSL 核心早已是一个干净的内部 DAG——`edn.py`（L0 解析）→ `builtins.py`（内建词汇表 + 契约类型）→ `validate.py`（静态校验）→ `eval.py`（L1 燃料计费求值内核）——纯标准库实现，对外只有一处跨界 import（`builtins.py` 引 `tlon.organization.mail.mail` 的 `Mail` / `address_for_member`）。把它抽成独立包，让"语言"与"宿主"边界显式化：tlosure 是可独立理解、独立测试的语言核心，tlon 是它的第一个宿主。

## 决策

### 1. tlosure 边界：只抽语言核心

移入 `src/tlosure/`（模块平铺，不建子包）：

- `edn.py` — EDN 解析/序列化，`loads` / `dumps` / `Keyword` / `Symbol` / `Form` / `EDNParseError`
- `builtins.py` — 内建词汇表（trigger / loop / stage-cond / script / arithmetic 注册表）、契约类型（`EvalError` 家族 / `Builtin` / `ActionRequest`）、`ScriptHostQueries` Protocol、`make_member_builtins`
- `validate.py` — 静态校验 `validate_form` / `validate_member_action`
- `eval.py` — 求值内核 `eval_form`、特殊形式白名单、燃料常量

留在 `tlon.agents`（tlon 领域语义，不属于语言）：

- `member_spec.py`（成员语义模型，`STAGE_NAMES` / `EventType` 是 tlon 流水线概念）
- `loader.py`（磁盘加载，依赖 `tlon.base.paths`）
- `behavior_template.py`（写侧模板层，服务于原型系统）
- 引擎三件套 `member_runtime.py` / `on_complete.py` / `loop_driver.py` 原地不动——"DSL 描述，引擎执行"的分工不变

### 2. 依赖方向：tlon → tlosure，tlosure 零 tlon 依赖

tlosure 只依赖标准库。import-linter 增加契约固话此方向（`root_packages = ["tlon", "tlosure"]`，tlon 各层可依赖 tlosure，tlosure 禁止 import tlon）。

### 3. Mail 耦合：依赖倒置（数据注入，不留函数调用）

`builtins.py` 对 `tlon.organization.mail` 的唯一依赖拆为两处注入：

- **`Mail` 类型**：tlosure 定义 `MailView`（`runtime_checkable` Protocol，结构成员 `to` / `cc` / `from_address` / `content`），mail 谓词（`addressed-to-me?` / `cc-ed?` / `from?` / `content-matches?`）以 `isinstance(msg, MailView)` 判定。`Mail` 本体留在 `tlon.organization.mail`，天然满足协议。
- **`address_for_member` 派生**：改为 **env 数据注入**——宿主构造 env 时在 `self` map 里带 `"address"` 键（由 tlon 侧调用 `address_for_member` 预先派生），`_my_address` 直接读 `self.address`，不再 import 任何 tlon 函数。env 构造点共 3 处（`member_runtime.py` / `on_complete.py` / `loop_driver.py`）同步改。这与既有 `ScriptHostQueries` 闭包注入模式一脉相承：tlosure 只声明契约，宿主提供事实。

### 4. 无兼容层

`tlon.agents` 下四个原模块直接删除，不留 re-export shim；tlon 侧全部 import 点（`agent_registry` / `member_runtime` / `loop_driver` / `on_complete` / `adam` / `prototypes` / `lessons` / `retirement` / `executor` / `reflection` / `context_builder` / `member_spec` / `loader` / `behavior_template`）改为 `tlosure.*`。

### 5. 打包与工具链

同仓库、同分发、单 `pyproject.toml`：`packages = ["src/tlon", "src/tlosure"]`。同步更新：

- `scripts/check_no_reexport.py`：根包自动探测改为支持 src 下多包
- `scripts/check_all.sh`：mypy 路径覆盖 `src/tlosure`
- `scripts/gen_reference_docs.py`：`EVAL_FILE` / `BUILTINS_FILE` 硬编码路径指向 tlosure
- `pyproject.toml`：coverage source 增加 `tlosure`；import-linter 契约
- 测试：DSL 核心测试迁入 `src/tests/tlosure/`，引擎侧测试留在 `src/tests/agents/` 并更新 import

## 后果

- 语言核心零宿主依赖，可独立阅读、独立测试、独立演进；ADR 0011（SCI 兼容子集）的语义约束随代码一并迁入 tlosure。
- `MailView` 结构协议意味着任何满足 `to` / `cc` / `from_address` / `content` 形状的对象都可进入 DSL env——宿主替换 mail 实现时 tlosure 无感。
- env 契约变化：`self` map 新增 `address` 键（DSL 表达式可读 `(get self :address)`，算顺带能力）。
- 文档同步：`docs/design/dsl.md` 分层表、`docs/architecture.md` 层表、`docs/reference/dsl-reference.md`（重新生成）。
