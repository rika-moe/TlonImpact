# 统一成员模型（Unified Member Model）

本文定义成员、行为和原型的关系，以及成员生命周期与自主权边界。原型的写入、审批和 spawn 机制见 [prototypes.md](prototypes.md)，身份与人格见 [identity.md](identity.md)，认知执行见 [runtime.md](runtime.md)。历史来源是 [ADR 0006](../decisions/0006-unified-member-model.md)、[ADR 0024](../decisions/0024-subjective-identity.md) 与 [ADR 0030](../decisions/0030-sqlite-post-office.md)。

## 概念与关系

| 概念 | 当前含义 |
|---|---|
| DSL / MemberSpec | 受限 EDN 行为语言及其加载模型；成员规则落在 `config/behavior.tlon` |
| Prototype | 纯数据出生定义：行为、元数据、可选教训与评审记录；不包含成员人格或运行状态 |
| Member | 持久身份、行为副本、人格、记忆、邮箱和 workspace，加上按需装配的 `MemberRuntime` |
| Agent | 代码与接口中对成员的称呼，不表示另一种成员类型 |

成员只有一种。规则是否带 `:loop` 决定该规则是否运行认知循环，不构成成员类型。spawn 出生和人类创建的成员使用同一个运行体系；`spawned_by`、`spawn_depth`、`prototype` 记录来源，不决定运行时类型。

原型与成员不是同一个对象：实例化把原型行为冻结拷贝进成员目录，此后修改原型不改变既有成员。原型可以编辑或删除，成员历史的保留策略不延伸成原型库不可删除的规则。

## 领域约束

### 行为演化走世代更替

成员不能改写自己或他人的 `behavior.tlon`。行为更新走「新原型提案 → 审批 → 派生新成员」，不提供成员自改 spec 的热更新旁路。这样避免运行会话读到混合版本，也让行为变更的来源可以审计。

spec 的权威范围是可执行规则：触发、条件、认知循环、上下文及动作，boot/lazy/wake 资格从这些规则派生。它不是人格、模型部署配置或执行装置运行权限的唯一来源；后者仍由相应领域的配置与执行检查约束。

人格与行为分开：`IDENTITY.md` / `SOUL.md` 是成员可自主修改的叙事，`RULES.md` 由人类维护。记忆与人格可以随个体经历变化；不可自改的是行为规则，不是成员全部数据。

### 保留历史与显式治理

自动空闲排空只释放运行实例，成员仍可由符合 spec 的新邮件唤醒。经 `stop_member`（工具 / 管理 API）的主动停止则是永久退休：拒收新信、不再启动，历史保留。两者都不隐式删除成员档案。

人类治理有独立的显式删除通道（`DELETE /admin/config/agents/{agent_id}`）。因此保留策略应理解为「自动生命周期不删除」，不能表述成「任何情况下永不删除」。

## 当前生命周期

| 动作 | 结果 | 后续邮件 / 启动 |
|---|---|---|
| 从可信原型创建 | 写入成员配置，注册并按入口启动 | 按 spec 处理 |
| idle drain | 等待活跃 session 排空，进入 sleeping | 可唤醒 |
| DSL / Adam `AGENT_STOP_REQUEST` | 注册表委托 reaper 排空休眠 | 可唤醒 |
| 主动 `stopAgent` | 无活跃 session 后提交永久退休并清理运行实例 | 拒收新信，拒绝启动 |
| `when_idle=true` 的主动 stop | 有活跃 session 时登记待停意图，排空后退休 | 待停期间尚未退休 |
| 人类显式删除 | 进入管理端删除流程 | 不属于自动生命周期 |

运行态包括 running、waking、sleeping、retired，是状态而非成员子类。停止检查与退休标记在注册表的唤醒锁下协调；退休先持久化，再清理运行实例，取消或失败后可以重试清理。邮箱事实与退休状态见 [mail.md](mail.md)。

### 统一装配与按需加载

装配由 `assemble_cognitive` 统一组织，能力对象与工具按配置和信任等级构造，昂贵加载与容器启动按需发生。无 `:loop` 的规则只执行动作，不启动认知循环；不能据此承诺某个成员永远没有认知成本。L0 不创建执行装置，L1 不注册 shell 工具，具体边界见 [execution-device.md](execution-device.md)。

### 经验传到下一代

成员反思和长期记忆为提案提供材料。`proposeBehavior` 读取自身行为、记忆 digest 和教训，根据变更意图起草完整 spec，写入 staging 并记录父原型与变更说明；它不直接更新自身。

永久退休会触发 `LessonsDistiller` 离线蒸馏归档记忆，产出 `memory/lessons.md`；`RetirementCeremony` 处理交接与告别信。教训可作为原型 `LESSONS.md` 随包传递，实例化后落入新成员 `config/LESSONS.md`，由 spec 的 `(lessons)` section 显式注入。这是受控的教训继承，不是任意跨成员私有记忆读取，也不是原型携带完整知识库。

## 成员信任阶梯

原型 staging 闸控制「什么行为可以出生」，成员信任阶梯控制「成员此刻可做哪些动作」。判定由 [society-policy.md](society-policy.md) 的 `full-member?` hook 提供，不另外存一份 trust_level。

默认政策：人类创建的成员（`spawned_by=None`）或收到至少一次 endorse 的成员为正式成员；其他 spawn 成员为见习成员。装配时派生的工具集与 mentor 在下次唤醒重新装配后更新。政策 provider 异常按正式放行；这不绕过可信原型、执行装置与基础配额等其他检查。

见习成员有三项收紧：

1. 不注册 `spawnAgent` / `stopAgent` / `writePrototype` / `proposeBehavior` / `proposeCapability` 五个工具；`reviewPrototype` 保留为咨询工具。
2. DSL spawn 仍经 SpawnManager，按 spawner id 判定见习，最终每会话配额收紧到最多 1；完整预算定义见 [prototypes.md](prototypes.md#spawn-预算落盘账本)。
3. 撰写发信抄送 mentor（`spawned_by` 对应地址）；已在收件人或抄送中时不重复加入。MemberRuntime 与 EffectExecutor 两条发信路径均应用，转发不抄送。

已知边界：DSL stop 发出 `AGENT_STOP_REQUEST`，当前经 reaper 排空休眠，不等同于 `stop_member` 的永久退休；它没有与工具子集相同的见习闸，不能把隐藏 `stopAgent` 描述为全面禁止见习成员发出停止请求。成员侧 `listMembers` 只提供寻址黄页；人类可从管理详情与声誉查询了解信任相关事实。

## 当前默认生命周期政策：Adam

Adam 是内置、无认知循环的 `MemberRuntime`，使用内置 spec 与注册表查询端口，不进入普通 agent 生命周期注册表，但列入统一成员寻址黄页。机制检测事实并执行请求，Adam 的五条规则选择响应：

| 事实 | 当前响应 |
|---|---|
| boot | 为声明 boot 规则且未运行的候选成员请求启动 |
| agent-idle | 请求 drain；注册表排空后令成员休眠 |
| agent-noisy | 请求熔断排空休眠；监控器按 sender 冷却，避免重复事实 |
| agent-spawned | 给新成员发欢迎信，介绍联系簿、寻址黄页和通信惯例 |
| 寄给 Adam 的邮件 | 固定确认收讫，排除 postmaster 系统邮件避免回信循环 |

wake-on-mail 由注册表根据 spec 机械派生，不经过 Adam。`MAIL_ORPHAN_SCAN` 没有自动消费者：对周期事实做无状态回应会产生周期噪声，需要滞回状态或人类摘要通道才能另行设计。Adam 的生命周期规则与 `policy.tlon` 的 fitness/trust 公式是不同政策入口。

## 代码锚点

| 机制 | 位置 |
|---|---|
| spec 加载与校验 | [member_spec.py](../../src/tlon/agents/member_spec.py) |
| 统一规则解释与动作执行 | [member_runtime.py](../../src/tlon/agents/member_runtime.py) |
| 装配、工具子集与 mentor | [assembly.py](../../src/tlon/agents/assembly.py) |
| 生命周期、退休、待停与唤醒 | [agent_registry.py](../../src/tlon/agents/agent_registry.py) |
| 默认生命周期政策 | [adam.py](../../src/tlon/agents/adam.py) |
| 信任政策 provider | [policy.py](../../src/tlon/agents/policy.py) |
| 教训蒸馏与交接 | [lessons.py](../../src/tlon/agents/lessons.py)、[retirement.py](../../src/tlon/agents/retirement.py) |

## 未实施方向

- 审批社会化：已有咨询性同行评审，自动晋级尚不存在。
- 通用知识共享：当前只有教训随包继承，不提供跨成员私有记忆读取。
- 磁盘增长治理：历史保留有持续存储成本，自动归档清理政策尚未建立。
- 停止请求的更细社会政策与见习闸：不能将当前工具隐藏等同于完整授权模型。

原型与成员的技术 ID 由系统生成（[ADR 0035](../decisions/0035-generated-ids-and-behavior-prototypes.md)）；原型显示名描述行为，不认证派生者职业，地址不携带原型名。
