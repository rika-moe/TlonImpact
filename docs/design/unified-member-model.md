# 统一成员模型（Unified Member Model）

> **状态：决策已拍板（2026-08）并完成阶段 2 代码实施**——成员类别（react/script、resident/contractor）已删除、装配统一、排空一律睡眠、删除路径退场。本文是改造的方向锚点与迁移路径总览；阶段 3（档案归档落地，随阶段 2 完成）与阶段 4（世代通道 `proposeBehavior`）待实施。决策记录见 [docs/decisions/0006-unified-member-model.md](../decisions/0006-unified-member-model.md)。

## 背景：实然的四概念与两个类别轴

当前系统里有四个概念，彼此关系如下（现状，非目标）：

- **DSL** —— 受限 EDN 行为语言，语义权威是 [agents/member_spec.py](../../src/tlon/agents/member_spec.py) 的 `MemberSpec`，落盘为每成员 `config/behavior.tlon`。spec 是成员行为的唯一权威：boot/lazy/wake/成员类型全部由它派生。
- **Agent** —— 术语分裂，横跨 `AgentConfig`（agent.yaml 身份卡）、`MemberRuntime`（统一解释器实例）、`~/.tlon/{id}/` home 目录。
- **Prototype** —— 纯数据目录：`behavior.tlon`（行为基因型）+ `prototype.edn` 元数据；**不带人格**（ADR 0006 实施后：人格是成员创建时数据）。trusted 区可直接 spawn，staging 区（LLM 撰写）须人类批准。当时（决策前）还带可选人格 .md。
- **Member** —— 社会成员：`MemberRuntime` 实例 + 持久身份。被两条类别轴切成三份：**react/script**（认知/确定）与 **resident/contractor**（常住/临时工）。

两个类别轴是本文要消除的核心缺陷。逐条看它们在功能上到底在做什么：

| 类别轴 | 机制 | 实际在做什么 |
|---|---|---|
| react vs script（`member_type`） | 装配分叉：spec 有 `:loop` → `_assemble_cognitive` 全套；无 → `cognitive=None` 零基础设施 | 一条**资源分配策略**伪装成了类型；全有或全无的布尔分叉，纯转发 daemon 永远升不了级 |
| resident vs contractor（`AgentConfig.role`） | contractor：恒 lazy、短空闲引线、home 删除（idle-reap/stopAgent）；resident：只能睡、永不删 | 一条**生命周期治理策略**伪装成了出生标记；可丢弃性写死在身份上，出生即定型 |
| 两者共用的外壳 | 同一个 `MemberRuntime` + 同一个 `AgentRegistry` | 运行时早就统一了，分类是**历史残留**（两套旧运行时合并 + spawn 体系引入） |

另有三个文档已承认的缺陷作为背景：个体学习环断裂（反思写回记忆、到不了行为）、变异算子缺失 + 审批人工瓶颈、策略（Adam）不可进化。以及两个文档未承认但更根本的矛盾：标准 resident 团队的行为来自 Python 模板（[agents/behavior_templates.py](../../src/tlon/agents/behavior_templates.py) 的 `_RESIDENT_IDS` 狗粮），"行为即数据"在主干成员上打折；Prototype 不在书信共和国里（spec 不能作为信的载荷在社会中流传）。

**核心判断**：两个类别轴没有一条在做"行为"的事，全在做"资源分配"和"生命周期治理"的事——而这两件事按愿景都不该是类别（类 = 出生即定、代码写死），而应该是**策略**（可进化的数据）与**状态**（可变化的当前值）。

## 决策集（9 条）

### 类别全消

**决策 1：成员只有一种。** `react/script`、`resident/contractor` 作为**类别**删除；规则形态（一条规则有没有 `:loop`）保留为 DSL 语法，不再是成员类型。成员 = 身份（agent_id/name/地址）+ 行为（behavior.tlon，数据；不可自改，更新走世代更替，见决策 4）+ 状态（running/sleeping/retired，当前值不是类）+ 档案（home 目录）。

### 能力与成本

**决策 2：零 token 承诺废弃。** "确定型成员保证不调 LLM"不再作为成员类别的承诺与宣传。便宜路径（无 `:loop` 规则只跑 `:actions`）继续存在，但它是**规则级策略**——由 spec 数据推导的观察值（无 loop → 不启动会话引擎 → 不触发 LLM/记忆），不是类别特权。

**决策 3：懒装配。** 能力对象（LLM/记忆/工具/Docker）对所有成员**统一构造**（对象便宜），**加载按需**（`memory.load()`、会话引擎启动按 spec 派生决定，调用才贵）。`_assemble_cognitive` 的 if/else 分叉合并为一条装配路径。

### 行为演化 = 世代更替

**决策 4：spec 不可直接修改，自己的也不行。** 行为更新的唯一通道：**创建新 prototype → 派生新 member**。成员是基因型的纯表达（phenotype），不反馈回基因型——进化完全发生在原型（物种）层面。这绕开运行中热更新行为的所有一致性问题（并发会话读新旧混合、回滚、审计），也把进化的单位分清：原型（可变的基因型）与成员（不可变的表达）在两个时间尺度上各自演化。反拉马克、达尔文式：学习的成果归种群，不归个体。

**决策 5：允许 member 创建新 prototype。** `writePrototype`（写 staging，`:trusted false`）通道即行为演化的主通道，现状已具备。

**决策 6：不能修改别人的 spec。** 改他人行为只走提案通道（新 prototype → 评审），默认禁止直写。

### 生命周期

**决策 7：退休——停止是请求，接受与否是策略。** "谁能停谁、什么条件下停"从注册表硬编码（`resident_not_stoppable`）搬进策略数据；机制（执行停止、回收）留在注册表。**短期**：人类守护默认规则（策略的默认实现），规则本身是数据。

**决策 8：退休成员档案归档——不删。** `~/.tlon/{agent_id}/` 的 home 目录（config/memory/mailbox/workspace）**永不删除**，现存的所有删除路径退场：contractor 空闲回收、`stopAgent`、容器 force-remove。成员只会"睡"，不会"蒸发"。

### 暂不引入

**决策 9：原型种子知识不引入。** 原型不带初始 knowledge（也无人格——人格是成员创建时数据，不是基因型的一部分）。

## 应然模型：一类成员 + 世代更替

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
- **Agent 术语收敛**：agent ≡ 运行中的成员；prototype = 出生定义；DSL = 行为语言。

## 对现存代码/文档的影响

| 现存机制 | 结局 |
|---|---|
| [agents/member_spec.py](../../src/tlon/agents/member_spec.py) 的 `member_type`（react/script 类别） | 删除类别语义；`:loop` 回到"这条规则有没有认知循环"的语法事实 |
| [agents/prototypes.py](../../src/tlon/agents/prototypes.py) 的 `_derive_runtime` | 删除 |
| [base/config.py](../../src/tlon/base/config.py) 的 `AgentConfig.role`（resident/contractor） | 删除；`spawned_by` / `spawn_depth` / `prototype` 血缘字段**保留为审计**（世代更替的可追溯性） |
| [agents/agent_registry.py](../../src/tlon/agents/agent_registry.py) 的 `is_lazy` 的 `config.role == "contractor"` 分支 | 删除；lazy 判定只由 spec 派生（现有 `_spec_profile` 逻辑收敛） |
| [agents/agent_registry.py](../../src/tlon/agents/agent_registry.py) 的 `spawn_contractor` 里 `config.role = "contractor"` | 删除 |
| [agents/agent_registry.py](../../src/tlon/agents/agent_registry.py) 的 `stop_contractor` 的 `resident_not_stoppable` 与 role 检查 | 删除；停止走 `AGENT_STOP_REQUEST` → 策略规则裁决（决策 7） |
| [agents/agent_registry.py](../../src/tlon/agents/agent_registry.py) 的 `_discard_contractor`（home 删除 + 容器 force-remove） | 整体退场；空闲回收变为"睡"（scale-to-zero），永不删档案（决策 8） |
| [agents/agent_registry.py](../../src/tlon/agents/agent_registry.py) 的 `_assemble_cognitive` if/else 分叉 | 统一装配 + 懒加载（决策 3） |
| [agents/behavior_templates.py](../../src/tlon/agents/behavior_templates.py) 的 `_RESIDENT_IDS` 狗粮 | 退场（resident 特殊类别的残留）；标准团队从原型/数据出生 ✅（六个职位原型 + `default_team.py` 角色卡，2026-08 实施） |
| spawn 预算（`max_activations` / `max_spawn_depth` / `session_spawn_quota`） | **保留为纯资源硬顶**（机制）；"谁可以造新成员、一次造几个"是策略（可数据化，后续） |
| docs：`design/dsl.md`（cost routing 叙事）、`design/runtime.md`（合并叙事/方向节）、`design/prototypes.md`（contractor/信任分级叙事）、`architecture.md`（装配/存储布局）、`reference/config-reference.md`（role 字段表）、`reference/tools-reference.md`（spawnAgent/stopAgent 语义） | 实施时同步；类别对照表退场 |

## 迁移路径（分阶段，每阶段独立可提交）

1. **阶段 1：术语与文档先行**（成本最低）。确立"一类成员 + 状态"的表述，删除 docs 里的类别对照表；拍板决策已在本文件与 ADR 0006 固化。✅ 已完成
2. **阶段 2：代码消类**。删 `role`、`member_type` 类别语义、`_derive_runtime`、`_discard_contractor`、`stop_contractor` 的 role 检查（停止裁决策略化，短期人类守护默认规则）；统一装配 + 懒加载。✅ 已完成（2026-08，随本文件实施）
3. **阶段 3：档案归档落地**。确认退休流程"归档而非删除"（决策 8 的代码面，随阶段 2 的 `_discard_contractor` 退场完成）。✅ 已随阶段 2 落地
4. **阶段 4：世代通道**。`proposeBehavior` 类工具（经验/总结 → 原型提案），把个体学习环接到原型通道上——这是"经验改写行为"的真正落点，待 2–3 稳定后实施。⏳ 待实施

## 后续未决项（不阻塞当前改造）

- **知识继承通道**：归档 ≠ 知识继承——当前没有跨成员读取通道（mailbox 是私有的）。新成员读旧世代的知识是独立未来项（种子知识已暂缓，决策 9）。
- **磁盘增长治理**：不删 = 磁盘单调增长。短期人类手动清理可接受；长期需要治理规则（大概率是未来的社会规则，与决策 7 同源）。
- **审批社会化**：世代更替的自动化程度被人工审批瓶颈锁死（与愿景"人类退为规则守护者"的张力），属路线"开放"一步，短期接受。
- **退休策略数据化形态**：决策 7 的"策略"具体落在哪（Adam 的 spec 扩展？独立策略 spec？）待阶段 4 设计时定。