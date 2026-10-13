# 社会政策语境：fitness / trust 政策数据化（policy.tlon）

> 状态：已落地（2026-09-28，EPIC #62，PR #63–#67）。
> 上游：ADR 0020（Bridge API）、ADR 0021（tlosure 完整函数能力）、docs/design/bridge-api.md。

## 定位

Bridge API 分层落地后，runtime 里仍有一批**政策公式**硬编码在 Python：

- fitness（`src/tlon/agents/fitness.py`）：血缘效率公式（value = Σ(1 + endorsements)、efficiency = value / tokens）、tilt 钳制（[0.5, 2.0]）、资不抵债阈值（样本下限 10 万 token、效率低于社会均值 0.25 倍）；
- trust（`src/tlon/agents/trust.py`）：见习判定（`spawned_by` 非空且 `endorsements_received < 1`）。

这些是纯函数式政策：输入是社会事实（血缘账本、声誉），输出是数值或布尔判定。改政策今天等于改代码、发版。ADR 0021 之后 tlosure 具备完整函数能力，这些公式可以直接用 tlosure 表达——本设计把它们迁到一个人类专写的 society 级政策文件，宿主在固定求值点调用。

机制/策略边界不变：数据收集（`base/ledger.py` 的血缘聚合、`agents/reputation.py` 的背书计数）、provider 接口、门执行（SpawnManager 配额、assembly 工具门）永远留在 Python；公式与阈值落在 tlosure。

## 第五个求值语境：pull 型，数据物化注入

现有 4 个语境（规则 `:when`、on-complete、`:until`、stage `:if`）全是**事件触发型**：事件到达，宿主求值 spec。社会政策语境是第一种 **pull 型**语境：宿主在固定求值点主动调用政策 hook——

| 求值点 | hook | 输入 | 输出 | 消费方 |
| --- | --- | --- | --- | --- |
| spawn 前 | `tilt` | 目标血缘 + 全社会血缘统计 | 配额乘数（float） | SpawnManager 预算 3 |
| spawn / staging 提案前 | `solvent?` | 同上 | 是否有偿付能力（bool） | SpawnManager 偿付门、staging 提案门 |
| assembly 前 | `full-member?` | 成员社会事实 | 正式/见习（bool） | 工具门（REPRODUCTION_TOOLS）、mentor cc、SpawnManager 见习配额 |

与事件型语境的第二个差异：**环境数据由宿主预先物化注入，不是回调式查询端口**。社会数据的收集是异步 IO（扫账本文件、扫全社会信箱），不可能发生在纯 builtin 里；宿主在求值前把原始事实聚合成 EDN 数据，政策函数只读数据。这比成员语境更彻底地贯彻 ADR 0018 的「builtin 读数据、不回调宿主」。

机制与政策的切分点：**物化原始事实是机制**（total_tokens 求和、endorsements 计数、spawned_by 读取——没有判断，只有收集）；**从事实到判定是政策**（value 公式、效率公式、钳制区间、阈值比较——全在 DSL）。

## 政策文件：policy.tlon

位置 `~/.tlon/policy.tlon`（与 `cron.yaml` 同级的 society 根目录文件）。形如：

```clojure
{:version 1
 :policy [(defn lineage-value [l]
            (reduce (fn [acc m] (+ acc 1 (get m :endorsements-received)))
                    0 (get l :members)))
          (defn efficiency [l]
            (/ (lineage-value l) (if (> (get l :total-tokens) 1) (get l :total-tokens) 1)))
          (defn tilt [subject lineages] ...)
          (defn solvent? [subject lineages] ...)
          (defn full-member? [member] ...)]}
```

- `:policy` 是一个 **form 序列，在共享 env 中顺序求值**，`defn` 名字向后穿线——完整复用 ADR 0021 落地的 defn 穿线机制（`validate_member_actions` 同款模式），不新增任何求值机制。
- **hook 契约 = 保留函数名**：宿主求值完序列后在 env 里按名查找 `tilt` / `solvent?` / `full-member?`。缺失的 hook 回落到内嵌默认实现（见下）——政策文件可以只覆盖一个 hook。
- 文件整体缺失、解析失败、校验失败 → 全部 hook 用内嵌默认，记 warning。空政策文件（`:policy []`）等价于全默认。

### 环境契约

| hook | 参数 | 形状 |
| --- | --- | --- |
| `tilt` / `solvent?` | `subject lineages` | `subject`：目标血缘统计 map `{:name str :total-tokens int :members [{:id str :endorsements-received int}]}`，血缘无数据时为 nil；`lineages`：全社会血缘统计 vector（同形状） |
| `full-member?` | `member` | `{:id str :spawned-by str|nil :endorsements-received int}` |

`lineages`/`members` 注入为 vector 而非 map：政策侧不需要 `keys` 等新词汇，`map`/`filter`/`reduce` 即可遍历。求均值需要 `count`——已作为通用词汇补进 tlosure（对齐 SCI core）。

### 内嵌默认政策（DEFAULT_POLICY_EDN）

新模块 `src/tlon/agents/policy.py` 内嵌一份默认政策文本，**精确复刻** fitness.py / trust.py 的现行公式——与 `ADAM_SPEC_EDN`（`src/tlon/agents/adam.py`）同一模式：政策是数据，内嵌在代码里，经统一加载语义解析并做加载期校验。policy.tlon 缺失时行为与现状逐字节一致，这是「行为不变先行」的保证。

### 词汇与静态校验

政策语境注册表是成员注册表的**纯子集**：通用词汇（`get`/`str`/`contains?`/`=` 等）+ 算术（`+ - * / < > <= >=`）+ 特殊形式（`if`/`let`/`and`/`or`/`defn`/`fn`/`map`/`filter`/`reduce`）。**无动作词、无 host 查询回调**——数据已物化，政策不需要也拿不到任何效果端口。加载期校验 = 全量注册表校验 + defn 穿线，镜像运行时行为。

### fuel 与 fail-open

政策求值有独立 fuel cap（语境级统一一个值，不做 per-hook 自声明——政策文件是人类写的治理文件，不是 agent 自调适的对象）。**任何求值异常（含 fuel 耗尽、类型错误）→ 该 hook 的安全默认**：`tilt` → 1.0，`solvent?` → true，`full-member?` → true，记 warning。沿用 fitness/trust 现行的 fail-open 纪律：政策簿记永不阻塞 spawn 或 assembly。

### provider 接口不动

SpawnManager 的三个注入点（`tilt_provider` / `solvency` / `probation`，`src/tlon/agents/spawn.py`）与 assembly 的 trust 门（`src/tlon/agents/assembly.py`）的 **provider 签名全部不变**。`agents/policy.py` 提供同签名的 provider 实现（内部：物化数据 → 求值 hook → 异常回落默认）。调用方零改动——provider 接口是机制，公式是政策，这次迁移只换公式的载体。

fitness.py / trust.py 的公式函数（`tilt_for` / `insolvent_lineages` / `is_full_member` 等）在迁移后删除；`REPRODUCTION_TOOLS` 工具集常量与 mentor cc 是执行侧机制，保留在 assembly 侧。

## 安全边界：政策文件人类专写

改政策 = 改社会宪法，只能人类动手：

- `policy.tlon` 位于 `~/.tlon` 根目录，不进任何成员的 `allowed_paths`——成员文件工具 confine 在各自 workspace，够不到；
- 原型/提案通道写的是 `prototypes/staging/`，与政策文件无交集；
- 政策文件不经 staging 审批流——它不是 LLM 产物，是人类直接编辑的文件（与 `cron.yaml` 同级别）。

## 与既有决策的关系

- **ADR 0020 / bridge-api.md**：本设计是桥的第 5 个语境、第一个 pull 型语境，证明「语境注册表 + 环境契约 + 端口编目」的桥结构能容纳非事件型求值。桥的版本纪律不变：hook 名只增，词汇演进走 deprecation 生命周期。
- **ADR 0021**：政策文件是 defn/闭包/高阶函数的第一个 society 级用例；公式共享辅助函数（lineage-value / efficiency）正是 defn 穿线的动机场景。
- **ADR 0014（环境-agent 协同演化）**：fitness/trust 公式从代码变成可编辑数据后，社会演化政策的调整不再需要发版——这是 ADR 0014 的运维面补完。
- **Adam 模式**：内嵌默认政策 + 统一加载语义直接复用 `ADAM_SPEC_EDN` 开创的模式。

## 非目标

- cron 条目条件化：需要独立的 tick 语境（`human-roster` 等数据注入），后续候选，不在本 EPIC。
- act pre-act 语境 / `reject-with` 效果端口：动作拦截语义需要单独设计。
- retirement / reaper 政策化：本次不动。
- `MAIL_ORPHAN_SCAN` 消费：adam.py 已记录不消费的理由（周期性事实的无状态反应本身会变成周期噪声），不变。
