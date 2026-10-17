# 社会政策语境：fitness / trust 政策数据化（policy.tlon）

本文定义 society 级 `policy.tlon` 的当前加载语义、hook 契约与默认政策。它把社会事实转换为 fitness/trust 判定，由宿主在固定求值点调用。历史背景与取舍见 [ADR 0022](../decisions/0022-society-policy-context.md)，求值边界见 [Bridge API](bridge-api.md)。

## 领域约束：事实、公式与执行分离

宿主收集账本、背书和出生信息等事实；政策计算价值、效率、配额倾斜、偿付能力与信任等级；SpawnManager 和 assembly 执行门控。政策不获得外部效果端口，也不在纯求值期间做异步 IO。

公式与阈值是人类可编辑的治理数据，修改不需要发版。数据收集、provider 接口与门控执行仍是宿主机制，不交给政策改写。

## 当前机制：pull 型求值与数据物化

社会政策与成员规则、循环条件和 stage 条件的求值入口不同：宿主在固定点主动调用 hook，以预先物化的数据作为参数。

| 求值点 | hook | 输入 | 输出 | 消费方 |
| --- | --- | --- | --- | --- |
| spawn 前 | `tilt` | 目标血缘 + 全社会血缘统计 | 配额乘数（float） | SpawnManager 预算 3 |
| spawn / staging 提案前 | `solvent?` | 同上 | 是否有偿付能力（bool） | SpawnManager 偿付门、staging 提案门 |
| assembly / spawn 前 | `full-member?` | 成员社会事实 | 正式/见习（bool） | 工具门（REPRODUCTION_TOOLS）、mentor cc、SpawnManager 见习配额 |

与事件型语境的第二个差异：**环境数据由宿主预先物化注入，不是回调式查询端口**。社会数据的收集是异步 IO（扫账本文件、扫全社会信箱），不可能发生在纯 builtin 里；宿主在求值前把原始事实聚合成 EDN 数据，政策函数只读数据。这比成员语境更彻底地贯彻 ADR 0018 的「builtin 读数据、不回调宿主」。

机制与政策的切分点：**物化原始事实是机制**（total_tokens 求和、endorsements 计数、spawned_by 读取——没有判断，只有收集）；**从事实到判定是政策**（value 公式、效率公式、钳制区间、阈值比较——全在 DSL）。

## 政策文件：policy.tlon

位置 `~/.tlon/policy.tlon`（与 `cron.yaml` 同级的 society 根目录文件）。形如：

```clojure
{:version 1
 :policy [(defn lineage-value [l]
            (reduce acc m (+ acc 1 (get m :endorsements_received))
                    0 (get l :members)))
          (defn efficiency [l]
            (/ (lineage-value l)
               (if (> (get l :total_tokens) 1) (get l :total_tokens) 1)))
          (defn full-member? [member]
            (or (= nil (get member :spawned_by))
                (>= (get member :endorsements_received) 1)))]}
```

这个完整示例只覆盖 `full-member?` 及其辅助定义；未覆盖的 `tilt` / `solvent?` 保留默认实现。

- `:policy` 是一个 **form 序列，在共享 env 中顺序求值**，`defn` 名字向后穿线——完整复用 ADR 0021 落地的 defn 穿线机制（`validate_member_actions` 同款模式），不新增任何求值机制。
- **hook 契约 = 保留函数名**：宿主求值完序列后在 env 里按名查找 `tilt` / `solvent?` / `full-member?`。缺失的 hook 回落到内嵌默认实现（见下）——政策文件可以只覆盖一个 hook。
- 文件缺失时使用内嵌默认；文件解析、校验或加载求值失败时记 warning 并整体使用默认，不泄漏半求值环境。空政策（`:policy []`）等价于全默认。

### 环境契约

| hook | 参数 | 形状 |
| --- | --- | --- |
| `tilt` / `solvent?` | `subject lineages` | `subject`：目标血缘统计 map `{:name str :total_tokens int :members [{:id str :endorsements_received int}]}`，血缘无数据时为 nil；`lineages`：全社会血缘统计 vector（同形状） |
| `full-member?` | `member` | `{:id str :spawned_by str|nil :endorsements_received int}` |

`lineages`/`members` 注入为 vector 而非 map：政策侧不需要 `keys` 等新词汇，`map`/`filter`/`reduce` 即可遍历。求均值需要 `count`——已作为通用词汇补进 tlosure（对齐 SCI core）。

### 内嵌默认政策（DEFAULT_POLICY_EDN）

[agents/policy.py](../../src/tlon/agents/policy.py) 的默认政策先建立共享环境，人类文件再覆盖其中的定义。未重定义的 hook 保留默认，用户定义也可复用默认辅助函数。与 Adam 的内置生命周期 spec 相同，默认政策仍以 DSL 数据表示并经过加载校验。

当前默认公式是：

| 判断 | 默认规则 |
|---|---|
| 血脉价值 | 本血脉各成员的 `1 + endorsements_received` 之和 |
| 血脉效率 | `value / max(total_tokens, 1)` |
| 社会均值 | 所有已收集血脉效率的算术均值 |
| `tilt` | 目标血脉效率除以社会均值，钳制在 `[0.5, 2.0]`；目标无数据或社会均值非正则为 `1.0` |
| `solvent?` | 目标无数据、消耗不足 100000 token 或均值非正时通过；否则效率至少为社会均值的 `0.25` 倍才通过 |
| `full-member?` | 人类创建（`spawned_by=nil`）或收到至少一次 endorse 为正式成员，其余为见习成员 |

这些是当前默认政策，不是机制不可更改的常量。tilt 与偿付按目标 prototype 判断，见习配额按发起者 member id 判断，两个维度不能混用。配额怎样应用这些结果见 [prototypes.md](prototypes.md#spawn-预算落盘账本)，工具与 mentor 闸见 [统一成员模型](unified-member-model.md#成员信任阶梯)。

### 词汇与静态校验

政策语境注册表是成员注册表的**纯子集**：通用词汇（`get`/`str`/`contains?`/`=` 等）+ 算术（`+ - * / < > <= >=`）+ 特殊形式（`if`/`let`/`and`/`or`/`defn`/`fn`/`map`/`filter`/`reduce`）。**无动作词、无 host 查询回调**——数据已物化，政策不需要也拿不到任何效果端口。加载期校验 = 全量注册表校验 + defn 穿线，镜像运行时行为。

### fuel 与 fail-open

政策求值有独立 fuel cap（语境级统一一个值，不做 per-hook 自声明——政策文件是人类写的治理文件，不是 agent 自调适的对象）。**任何求值异常（含 fuel 耗尽、类型错误）→ 该 hook 的安全默认**：`tilt` → 1.0，`solvent?` → true，`full-member?` → true，记 warning。沿用 fitness/trust 现行的 fail-open 纪律：政策簿记故障不额外阻塞 spawn 或 assembly；正常返回的政策拒绝仍执行，可信原型检查、基础预算、装置权限等其他闸也不因此放行。

### provider 与执行边界

SpawnManager 的三个注入点（`tilt_provider` / `solvency` / `probation`，`src/tlon/agents/spawn.py`）与 assembly 的 trust 门（`src/tlon/agents/assembly.py`）通过固定 provider 接口调用政策。`agents/policy.py` 提供同签名的 provider 实现（内部：物化数据 → 求值 hook → 异常回落默认）。provider 接口是机制，公式是政策；调用方不解析政策文件。

`REPRODUCTION_TOOLS` 工具集常量与 mentor cc 是执行侧机制，由 assembly 实施。

## 安全边界：政策文件人类专写

改政策 = 改社会宪法，只能人类动手：

- `policy.tlon` 位于 `~/.tlon` 根目录，不进任何成员的 `allowed_paths`——成员文件工具 confine 在各自 workspace，够不到；
- 原型/提案通道写的是 `prototypes-staging/`，与政策文件无交集；
- 政策文件不经 staging 审批流——它不是 LLM 产物，是人类直接编辑的文件（与 `cron.yaml` 同级别）。

## 未实施方向与当前边界

- cron 条件化需要独立 tick 语境与环境数据，尚未实施。
- 动作执行前的 pre-act 语境和 `reject-with` 效果需要独立拦截设计，尚未实施。
- retirement / reaper 政策尚未迁入 `policy.tlon`；当前 Adam 生命周期政策见 [统一成员模型](unified-member-model.md#当前默认生命周期政策adam)。
- `MAIL_ORPHAN_SCAN` 当前无自动消费者；周期事实不能直接触发无状态回信，否则会制造周期噪声。

## 代码锚点

- [agents/policy.py](../../src/tlon/agents/policy.py)：默认政策、加载、物化与 provider。
- [agents/vocabulary.py](../../src/tlon/agents/vocabulary.py)：政策词汇与环境类型。
- [agents/spawn.py](../../src/tlon/agents/spawn.py)：配额、偿付和见习结果执行。
- [agents/assembly.py](../../src/tlon/agents/assembly.py)：信任工具子集与 mentor。
