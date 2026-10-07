# 统一成员模型（Unified Member Model）

> **状态：模型已落地（2026-08）**——成员只有一种，装配统一 + 懒加载，排空一律睡眠，档案归档永不删除。世代通道 `proposeBehavior`（个体经验 → 原型提案）已实施（2026-09）。决策记录与理由见 [ADR 0006](../decisions/0006-unified-member-model.md)；世代更替的完整闭环包括出生社会化、教训传承、退休交接、谱系可见性、基因多样性。

## 概念

系统里四个概念，各就各位：

- **DSL** —— 受限 EDN 行为语言，语义权威是 [agents/member_spec.py](../../src/tlon/agents/member_spec.py) 的 `MemberSpec`，落盘为每成员 `config/behavior.tlon`。spec 是成员行为的唯一权威：boot 集合、lazy 判定、wake 资格全部由它现算，从不落盘。
- **Prototype** —— 纯数据目录：`behavior.tlon`（行为基因型）+ `prototype.tlon` 元数据（`:name :display-name :description :trusted :author` + 谱系字段 `:parent :generation :proposal-note`）+ 可选 `LESSONS.md`（随包教训：退休成员蒸馏的经验，实例化时拷贝给新成员、由 `(lessons)` section 注入）；**不带人格**——~~人格是成员创建时数据~~人格是**成员自主数据**（ADR 0024：成员可自由自改自己的 `IDENTITY.md` / `SOUL.md`，见 [identity.md](identity.md)），不是基因型的一部分。trusted 库（`~/.tlon/prototypes/`）里的原型可直接 spawn；staging 区（`~/.tlon/prototypes-staging/`，成员经 `writePrototype` 撰写，`:trusted false`）须人类批准（`approve_prototype` 晋升 / `reject_prototype` 否决）才能进入 trusted 库。
- **Member** —— 社会成员，只有一种：`MemberRuntime` 实例 + 持久身份 = 身份（agent_id/name/地址）+ 行为（behavior.tlon，数据；不可自改，更新走世代更替，见决策 4）+ 状态（running/sleeping/retired，当前值不是类）+ 档案（home 目录，永不删除）。
- **Agent** —— 术语收敛：agent ≡ 运行中的成员；prototype = 出生定义；DSL = 行为语言。

## 决策集（9 条）

### 成员

**决策 1：成员只有一种。** 一条规则有没有 `:loop` 是 DSL 语法事实，不构成成员类型；成员的生命周期待遇由 spec 与策略决定（决策 7），不由出生标记决定。

### 能力与成本

**决策 2：便宜路径是规则级策略。** 无 `:loop` 的规则只跑 `:actions`——由 spec 数据推导的事实（不启动认知引擎 → 不触发 LLM/记忆），不是任何成员级承诺。

**决策 3：懒装配。** 能力对象（LLM/记忆/工具/Docker）对所有成员**统一构造**（对象便宜），**加载按需**（`memory.load()`、认知引擎启动按 spec 派生决定，调用才贵）。实现见 [agents/assembly.py](../../src/tlon/agents/assembly.py) 的 `assemble_cognitive`：只有一条装配路径。

### 行为演化 = 世代更替

**决策 4：spec 不可直接修改，自己的也不行。** 行为更新的唯一通道：**创建新 prototype → 派生新 member**。成员是基因型的纯表达（phenotype），不反馈回基因型——进化完全发生在原型（物种）层面。这绕开运行中热更新行为的所有一致性问题（并发会话读新旧混合、回滚、审计），也把进化的单位分清：原型（可变的基因型）与成员（不可变的表达）在两个时间尺度上各自演化。反拉马克、达尔文式：学习的成果归种群，不归个体。

> **边界澄清（ADR 0024，2026-10）**：「spec」仅指 `behavior.tlon`。人格文件 `IDENTITY.md` / `SOUL.md` 是叙事不是行为规则——成员可自主修改，不走世代更替；`RULES.md` 是行为约束的自然语言版，与 spec 同侧，仍不可自改。

**决策 5：member 可以创建新 prototype。** `writePrototype` 写 staging 区（`:trusted false`，记 `:author` 供审计）——这是行为演化的主通道；人类批准后由 `spawnAgent` 派生新成员。

**决策 6：不能修改别人的 spec。** 改他人行为只走提案通道（新 prototype → 评审），默认禁止直写。

### 生命周期

**决策 7：退休——停止是请求，接受与否是策略。** 机制（执行停止、排空、回收）在注册表：`AGENT_STOP_REQUEST` 的幂等执行，有活跃会话的成员排空后才停止。"谁能停谁、什么条件下停"是策略数据，**短期**由人类守护默认规则实现，规则本身是数据。停止 = 排空后睡眠（scale-to-zero），不是删除。

**决策 8：退休成员档案归档——不删。** `~/.tlon/{agent_id}/` 的 home 目录（config/memory/mailbox/workspace）**永不删除**；系统内（agent 侧与自动路径）不存在成员数据的删除路径，成员只会"睡"，不会"蒸发"。唯一的删除通道是人类治理的显式动作（`DELETE /admin/config/agents/{agent_id}` 管理端点）——不删是"保留策略"，治理是显式动作，二者不矛盾。（注意区分：原型库不是档案——trusted 原型可由人类增删改，staging 提案可被否决；永不删除的是成员的 home。）

### 暂不引入

**决策 9：原型种子知识不引入。** 原型不带初始 knowledge（也无人格——~~人格是成员创建时数据~~人格是成员自主数据（ADR 0024），不是基因型的一部分）。知识继承通道（跨成员读取）尚不存在，种子知识没有落地形态；待个体学习环接上后再议。

> **补注（2026-09 落地）**：知识继承通道已有第一形态——不是原案设想的「出生种子知识」，而是**退休后蒸馏的教训附件**（生物层机制，ADR 0014 环境-成员共演化）：成员退休（蓄意停止）时，`LessonsDistiller` 离线重建其归档记忆、一次 LLM 蒸馏产出 `memory/lessons.md`；该文本经 `writePrototype` / admin API 的 `lessons` 参数包装进原型的 `LESSONS.md` 随包传递；`instantiate` 拷贝到新成员的 `config/LESSONS.md`，由 spec 的 `(lessons)` context section 显式声明注入。种子知识（原型自带初始 knowledge）原案形态仍不引入。

## 模型：一类成员 + 世代更替

```
        ┌────────────────────────────────────────────┐
        │           书信共和国（传播/选择介质）             │
        │   知识（信）  +  行为（spec 作为信的载荷）          │
        └────────────────────────────────────────────┘
                 ▲                            │
     行为投稿/同行评议                   实例化（遗传）
                 │                            ▼
    ┌──── Prototype（物种/基因库）◄──────── Member（个体）
    │    · 可传播、可评审、有血缘/版本        · 行为是"当前版本"，不可自改
    │    · 变异算子：拷贝/重组/调参/经验回流   · 经验→记忆→原型提案（归种群）
    │    · 世代更替的唯一出生通道            · 出生→运行→退休（归档），血缘可追溯
    └──────────────────┬────────────────────┘
                       ▼
             DSL（基因型语法，保持受限）
```

- **DSL 保持受限设计**（fuel 停机、无副作用、组合已有机制、total 语义）——信任模型的地基，不动。变异在原型层做，不引入语言内 `def`/继承。
- **Prototype 是社会的基因库**：唯一出生通道；行为演化的发生地。审批社会化（同行评议自动晋级，人类退为规则守护者）是远期方向，短期保持人工审批。
- **Member 是基因型的个体表达**：不可自改（决策 4）、经验不回流到自身、退休即归档（决策 8）。"个体学习"的正确形态是"经验 → 原型提案 → 新一代成员"，不是"反思 → 改写我的 spec"。
- **信任闸按影响半径分级**：改自己是危险的 → 干脆禁止（决策 4）；提案是安全的 → 走评审（决策 5/6）。

### 信任阶梯：新成员的渐进式自主权（2026-09 落地）

上述信任闸按**影响半径**分级；信任阶梯是它的补轴——按**成员**分级。两轴正交：原型轴的 staging 闸（[prototypes.md](prototypes.md)：LLM 写的原型待人类批准）管「什么行为可以出生」，成员轴的信任阶梯管「这个成员此刻能做多大动作」。阶梯**派生不落盘**（与声誉/谱系/适应度同一纪律）：

- **见习（probation）**：`spawned_by` 非 None（spawn 出生）且 `endorsements_received == 0`；
- **正式（full）**：人类放置（`spawned_by=None`）或已获至少一次 endorse（`ENDORSEMENT_THRESHOLD = 1`，首个认可即转正；更细的阶梯等数据积累）。

升级生效时机 = 下次唤醒重新装配（scale-to-zero 世界自然发生）。见习收紧三档闸门，全部 fail-open（派生异常一律按正式放行，可用性优先）：

1. **spawn 配额收紧**：`SpawnManager` 第三个 provider `probation` 按 **spawner_id** 判见习（注意维度差异：tilt/solvency 按目标 prototype，probation 按 spawner）；见习 spawner 的有效每会话配额 `min(effective, 1)`。
2. **工具子集**：装配期一次性派生信任等级，见习成员不注册四个生殖/演化工具（`spawnAgent` / `stopAgent` / `writePrototype` / `proposeBehavior`，即 `trust.REPRODUCTION_TOOLS`）；`reviewPrototype` 咨询性保留。DSL 的 spawn 动作由 SpawnManager 配额闸兜底（同一漏斗）；DSL stop 不闸——已知缺口：停止请求的策略闸是 Adam 侧/未来项。
3. **发信抄送 mentor**：见习成员的 mentor = `spawned_by`。装配期算出 `mentor_cc`（`address_for_member(spawned_by)`），注入 MemberRuntime 与 EffectExecutor 两条发信链路；mentor 已在 to/cc（含收件人即 mentor 本人）时不重复加。**forward 不抄送**——转发是既有邮件的传递，抄送是噪音。

可见性：`listAgents` 不加 trust_level——status 构造是同步的，声誉派生是 async 全社会扫描，成本不合；人类可见性靠 REST 详情已有的 `spawned_by` + `getReputation` 工具。

## 代码锚点

| 机制 | 位置 |
|---|---|
| spec 语义权威：`MemberSpec` 加载/校验、`source_hash` 审计 | [agents/member_spec.py](../../src/tlon/agents/member_spec.py) |
| 统一解释器：规则执行、认知引擎按 `:loop` 挂载、排空语义 | [agents/member_runtime.py](../../src/tlon/agents/member_runtime.py) |
| 统一装配 + 懒加载：`assemble_cognitive` | [agents/assembly.py](../../src/tlon/agents/assembly.py) |
| 原型库：trusted/staging 两区、`instantiate` 拷贝语义、审批晋升/否决、谱系（`:parent`/`:generation`/`:proposal-note` 落盘，`get_prototype_lineage` 派生查询） | [agents/prototypes/](../../src/tlon/agents/prototypes) |
| 生命周期机制：`AGENT_START_REQUEST` / `AGENT_STOP_REQUEST` 幂等执行、排空=睡眠 | [agents/agent_registry.py](../../src/tlon/agents/agent_registry.py) |
| 退休蒸馏：`AGENT_STOPPED` 触发离线记忆蒸馏 → `memory/lessons.md`（归属记账在退休成员头上） | [agents/lessons.py](../../src/tlon/agents/lessons.py) 的 `LessonsDistiller` |
| 退休交接：`AGENT_STOPPED` 触发离线移交盘点 → 告别信（退休成员地址发出）+ `AGENT_RETIRED` 事实 | [agents/retirement.py](../../src/tlon/agents/retirement.py) 的 `RetirementCeremony` |
| 生命周期策略：boot/stop 裁决 + 出生欢迎信 + 守护者信箱（Adam 的策略规则，数据——装配层 `GuardianAgentInfoProvider` 合成解析，不进广播/名册） | [agents/adam.py](../../src/tlon/agents/adam.py)、[organization/mail/guardian_provider.py](../../src/tlon/organization/mail/guardian_provider.py) |
| spawn 预算（`max_activations` / `max_spawn_depth` / `session_spawn_quota`）：纯资源硬顶，机制；每会话配额按血脉适应度倾斜（`ceil(quota × tilt)`，tilt ∈ [0.5, 2.0]，冷启动恒 1.0）；偿付门槛：失去偿付能力的血脉被拒绝 spawn（`insolvent_lineage`，信任门之后、预算之前）——倾斜调多调少（连续），门槛定生死（二值），两者共用同一效率派生、各自 fail-open；tilt/偿付的公式是社会政策数据（ADR 0022，`policy.tlon` 的 `tilt` / `solvent?` hook） | [agents/spawn.py](../../src/tlon/agents/spawn.py)、[agents/policy.py](../../src/tlon/agents/policy.py) |
| 血缘审计字段：`spawned_by` / `spawn_depth` / `prototype` | [base/config.py](../../src/tlon/base/config.py) 的 `AgentConfig` |
| 信任阶梯：见习/正式两级是社会政策（`full-member?` hook，ADR 0022，可在 `policy.tlon` 调整）；`policy_trust_provider` / `policy_probation_provider`（fail-open）、`REPRODUCTION_TOOLS` 工具子集常量（机制，assembly 侧） | [agents/policy.py](../../src/tlon/agents/policy.py)、[agents/assembly.py](../../src/tlon/agents/assembly.py) |
| 世代通道工具：`writePrototype`（写 staging）/ `proposeBehavior`（经验入口：个体经验 → 起草 → staging 提案）/ `reviewPrototype`（异质评审：成员评审 staging 提案，记录落 `REVIEW.md`）/ `spawnAgent` / `stopAgent`（排空后睡眠）；谱系查询 `getPrototypeLineage` | [tools/agents/](../../src/tlon/tools/agents/)、[tools/prototypes/](../../src/tlon/tools/prototypes/) |

## 后续未决项（不阻塞当前模型）

世代更替的完整闭环：出生社会化、~~教训蒸馏~~、~~退休交接~~、谱系可见性、基因多样性。

- ~~**世代通道 `proposeBehavior`**：把个体经验/总结接到原型提案上——"经验改写行为"的真正落点。当前个体学习环在此断裂：反思写回记忆，到不了行为。~~ **已落地（2026-09）**：成员只需给变更意图与名称，工具内部读自己的 `behavior.tlon` + 记忆 digest + `lessons.md` 起草完整新 spec，以自己的 prototype 为父写入 staging（`proposal_note` 记意图、lessons 随包），人类批准后经 `spawnAgent` 派生新世代。
- **知识继承通道**：~~归档 ≠ 知识继承——当前没有跨成员读取通道（mailbox 是私有的）。新成员读旧世代的知识是独立未来项（种子知识已暂缓，决策 9）。~~ **已部分落地（2026-09）**：继承通道 = `LESSONS.md` 随原型包传递 + spec 的 `(lessons)` section 注入——但传的是退休蒸馏的教训（digest 提炼），不是任意跨成员记忆读取；通用知识共享仍未建设（决策 9 补注）。
- **磁盘增长治理**：不删 = 磁盘单调增长。短期人类手动清理可接受；长期需要治理规则（大概率是未来的社会规则，与决策 7 同源）。
- **审批社会化**：世代更替的自动化程度被人工审批瓶颈锁死（与愿景"人类退为规则守护者"的张力），属路线"开放"一步，短期接受。
- **退休策略数据化形态**：决策 7 的"策略"具体落在哪（Adam 的 spec 扩展？独立策略 spec？）待世代通道设计时定。
