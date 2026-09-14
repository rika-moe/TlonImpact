# ADR 0003：机制与策略分离——注册表纯机制，策略集中于 Adam

状态：已接受（回溯记录，2026-08-15）

## 背景

系统里有三类生命周期动作：agent 何时启动（boot）、何时停止（idle 排空）、何时被唤醒（wake-on-mail）。早期这些决策散落在各处：注册表内置了启动逻辑，唤醒曾是 Adam 的一条策略规则。随着 lazy 成员（scale-to-zero）与 spawn 出的成员出现，"谁决定哪个 agent 应该活着"这个问题需要一个明确的家。

同时愿景要求策略可进化：未来策略的作者不应是人类改代码，而应是系统内部产生的数据。如果策略硬化在注册表的 Python 里，它就和机制绑定死，永远无法被 agent 自己改写。

## 决策

**`AgentRegistry`（[agents/agent_registry.py](../../src/tlon/agents/agent_registry.py)）只做纯机制；全部生命周期策略集中到 Adam（[agents/adam.py](../../src/tlon/agents/adam.py)）。**

具体边界：

- **注册表 = 机制**。装配与运行表、`AGENT_START_REQUEST` / `AGENT_STOP_REQUEST` 的幂等执行（wake lock、`_waking` 去重、锁下条件复查）、空闲*事实*检测（发布 `AGENT_IDLE`，不决定后果）、spawn 预算。注册表从不回答"该不该"，只回答"怎么做到"。
- **Adam = 策略，且策略是数据**。Adam 是一个普通的 `MemberRuntime` 实例（无 `:loop` 规则，不进注册表、不出现在 MemberDirectory），其全部策略是内置的 `ADAM_SPEC_EDN` 文本，只有两条规则：`:on :boot` 时对候选集合发 `AGENT_START_REQUEST{reason="boot"}`（`start-all`）；`:on :agent-idle` 时把空闲事实转成 `AGENT_STOP_REQUEST{reason="drain"}`。
- **依赖单向**。Adam import 注册表（复用 `RegistryQueries` 读取运行状态），注册表永不 import Adam——kernel 先行、PID 1 随后，组件 DAG 保持无环。
- **wake-on-mail 是机制，不是策略**。唤醒资格由注册表从各成员 spec 派生（声明了 `:on :mail-delivered` 的睡眠收件人被 `AgentRegistry._on_mail_delivered` 唤醒），不需要 Adam 参与——唤醒资格本质是 spec 声明的订阅事实，不是一条可自由裁量的策略。

## 理由

- **机制稳定，策略可进化**。机制（锁、幂等、预算）变化慢、正确性敏感，沉淀在注册表里可获得充分的测试与审计；策略变化快，以数据（EDN spec）形式存在，可热重载、可改写——这正是愿景"行为即数据"在系统自身的第一次兑现。
- **DAG 无环**。单向依赖让装配顺序自然成立（注册表先于 Adam 初始化），也让两者的失败域隔离：Adam 挂了，注册表机制仍在；策略写错了，后果仅限于多启/少启，不破坏机制不变量。
- **策略作者将来可以是 LLM**。策略是一段经统一加载器校验的 MemberSpec 文本，与普通成员的 `behavior.tlon` 同构。等个体学习闭环后，改写 Adam 策略与改写任何成员行为走的是同一条已验证的通道。

## 备选方案

- **注册表内置策略**：把 boot 集合与 idle 排空直接写进注册表。被否：策略硬化进机制，注册表既执行又裁决，机制的可测性被策略的易变性污染，且策略从此只能由人类改代码。
- **独立调度服务**：策略独立成第三个组件。被否：多一个组件、多一份状态同步问题，而 MemberRuntime 已经能表达策略——复用统一解释器零新机制，Adam 与普通成员同构也降低了认知成本。

## 后果

- **边界靠纪律与检查维持**。"注册表不含策略"没有机器强制，依赖代码评审与 import-linter 的依赖方向契约维持；wake-on-mail 归入机制的论证（spec 派生而非自由裁量）需要随实现演进持续回看。
- **宿主谓词是注入特权，而非 Adam 独占**。`sleeping?` / `running?` / `lazy?` 谓词与 `start-all` 动作从不注册在模块级 builtin 表，必须由调用方闭包注入宿主查询（[agents/builtins.py](../../src/tlon/agents/builtins.py) 的 `make_member_builtins` + `ScriptHostQueries`）。当前注入面有两处：Adam（`make_adam`）与注册表全体成员的 spec（`AgentRegistry._host_builtins`，D6.6 向成员开放了宿主谓词）。即特权边界画在"宿主注入 vs 默认求值环境"，而非"Adam vs 其他成员"——未来若要收紧成员侧的宿主可见性，只需收窄 `_host_builtins` 的注入。
- **排空等决策的最终落点仍在机制侧复查**。Adam 的策略只是请求；`_drain_agent` 在 wake lock 下重新校验空闲条件，竞态窗口里新开的会话能保住成员。策略可以是陈旧的，机制必须自己兜底。

## 补注

- **2026-09（issue ）**：`ADAM_SPEC_EDN` 增至三条规则——新增 `:on :agent-spawned` 的出生欢迎信（出生社会化：名册经宿主查询 `member-roster` 现算，通信惯例为 spec 内静态文本；收件人地址由 `(str (get event :agent_id) "@tlon.internal")` 装配）。mentor 行为按本 ADR 的划法落在策略侧（spec 数据），未新增专门机制；宿主注入面同决策时（`make_adam` 与 `AgentRegistry._host_builtins` 两处，`member-roster` 随 `RegistryQueries` 对两方同份开放）。
- **2026-09（issue ）**：`ADAM_SPEC_EDN` 增至四条规则——新增 `:on :mail-delivered` 的守护者信箱（寄给 `adam@tlon.internal` 的信回一封固定确认信；`:when` 用 `addressed-to-me?` 收窄到本人、用新增的纯谓词 `from?` 排除 postmaster 发件人防退信循环）。可投递性不动 base/agents 数据：装配层加 `GuardianAgentInfoProvider`（app/server/core/guardian_provider.py）包装 ConfigService，仅 `get_config("adam")` 合成应答，`get_all_ids()` 原样委托——Adam 不进广播、名册与枚举扫描。Adam 自此携带真实邮箱（`~/.tlon/adam/mail/`，已读标记与 boot 未读重放复用统一运行时机制）。顺带按邮差惯例修复退信套娃：寄给 postmaster 的信静默丢弃、来自 postmaster 的信不再退信。