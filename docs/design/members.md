# Member 接口与人类 Adapter 设计

> 状态: ✅ 阶段 A/B/C 已实施
> 日期: 2026-07-24（阶段 C 完成于 2026-07-27）

---

## 背景与动机

Tlon 目前的运行时里，"谁能说话"存在两套不对等的机制：

- **Agent** 是总线上的正式公民：继承 `Component`（生命周期）+ `Device`（EventBus 收发），
  订阅 `MESSAGE_ROUTED` 按 `target_ids` 过滤，通过 `MESSAGE_NEW` 发声。
- **人类** 不是公民，只是 `ClientGateway.send_message()` 的一组硬编码参数
  （`sender_id="human", sender_name="人类"`）。人类不能被 @、不出现在参与者列表、
  多个客户端共享同一个 `"human"` 身份无法区分。

由此产生的不对称散落在代码里：

- `router.py` 中 `_route_human_message` / `_route_agent_message` 两条分岔；
- agent 消息固定附加 `@clients` 伪目标，人类不在路由解析表中；
- `sender_type == "human"` 特判散布于 router、trigger、context_builder 三处；
- 服务端不解析人类输入中的 `@名字`（客户端必须显式传 mentions，挂账问题）。

本设计将"总线公民"的隐式契约显式化为 **Member 接口**，让人类以 **AdapterAgent**
的身份获得与 agent 完全对等的地位——如同操作系统中 shell 与后台服务都是进程。

类比（灵感，非规约）：Member 之于 Congress，如进程之于操作系统。
ReactAgent 是思考着的进程，AdapterAgent 是用户的 shell 进程。

---

## 设计决策（已与作者确认）

| # | 决策 | 结论 |
|---|------|------|
| 1 | 接口命名 | **Member**（Congress 的成员；`Participant` 弃用） |
| 2 | 人类身份粒度 | **每个用户一个 AdapterAgent**，session 是其 attachment |
| 3 | 多用户 | 支持；客户端可自定义用户名；同名用户 attach 同一 adapter |
| 4 | 类型标识 | **不标识人类**——LLM 上下文中人类与 agent 无差别 |
| 5 | 房间模型 | 单房间、全公开；不做私聊/分房间（随未来房间重构再议） |
| 6 | 默认用户名 | `guest`（`--username` 与配置均未提供时） |
| 7 | 显示名 | 用户名即所见，第一版不支持单独显示名 |
| 8 | 实施划分 | 阶段 A / B / C 各自独立 PR |

---

## 概念模型

```
Member（ABC，继承 Component + Device）
├── member_id: str            # "xie-yunshu" / "human:alice"
├── member_type: str          # "react" | "adapter" | "script"
├── triggers_proactive: bool  # 见下文「无标识与防循环」
├── initialize() / shutdown() # 继承自 Component
└── get_state() -> str

ReactAgent   —— 现有 Agent，声明符合 Member（纯重构）
AdapterAgent —— 人类用户身份的在场化身，session 是其 attachment
ScriptAgent  —— 确定性 daemon（唤醒者、relay）；P5a 落地为通用 ScriptMember
               运行时（亚当为内置实例），P5b 起可经 prototype/spawn 创建
               （relay 为第一个 script prototype）
```

统一发生在 **Member 层而非 Agent 层**：AdapterAgent 不经过 LLM / 记忆 / Docker
那套机器，它只有身份、生命周期和总线收发。

### 既有类型的改名

| 现状 | 改为 | 说明 |
|------|------|------|
| `Participant`（dataclass，`organization/messaging/participant.py`） | `MemberInfo` | 读模型 DTO，职责不变 |
| `ParticipantDirectory` | `MemberDirectory` | 聚合 AgentRegistry + 在线 human adapters |

### 命名空间

人类 member id 使用 `human:<username>` 前缀。这**不是身份标识**，只是防止与
agent id（如 `xie-yunshu`）撞名的内部命名空间——未来 spawn 一个名叫 `alice`
的 agent 也不会冲突。LLM 上下文中只出现用户名，前缀不可见。

---

## 无标识与防循环

设计要求 LLM 无法区分人类与 agent，但系统仍须防止 agent 间的 proactive
无限循环。今天防循环依赖 `sender_type == "human"` 标识（router 不对 agent 消息做
proactive 广播）。

解法：将"身份标签"替换为 Member 的**行为属性**——

```python
class Member:
    triggers_proactive: bool  # AdapterAgent = True，ReactAgent = False
```

语义：该成员的**无定向消息**是否邀请 proactive 响应。这是系统内部的循环保险丝，
**从不进入 LLM 上下文**。context_builder 只渲染发送者名字，不渲染任何类型标签。
agent 眼中的世界：Congress 里一群有名字的 Member，仅此而已。

`Message.sender_type` 字段保留于数据模型与 `messages.jsonl`（历史兼容 +
trace/debug 价值），但**不再驱动路由**。DSL 的 `sender-type` builtin 先
deprecate、后于 2026-07 移除——router 的 `triggers_proactive` 闸门接管其
防循环职责；引用它的 spec 在 load 时校验失败。

---

## HumanAdapter 生命周期

```
auth(username="alice") 成功
  → AdapterManager.get_or_create("alice")
  → 创建 HumanAdapter(member_id="human:alice")，initialize()，注册进 MemberDirectory
  → session attach（refcount +1）

最后一个 session detach
  → adapter.shutdown()，从 MemberDirectory 移除
  → 该用户离线；@alice 解析失败（router 记 warning，
    与 @ 不存在的 agent 行为一致）
```

- 同名用户的多个客户端 = 同一 adapter 的多个 attachment（允许多人自称同名，
  自部署可信环境）。
- adapter 随 attach 而生、随最后 detach 而眠——与 lazy agent 的唤醒/沉睡镜像：
  Congress 的在场者全部按需存在。
- 离线收件箱（store-and-forward）第一版不做。

---

## 消息流（多用户）

1. **接入**：WS connect → session 创建 → `auth {token, username}` →
   AdapterManager 挂接。
2. **发言**：WS handler 不再调 `gateway.send_message(sender_id="human", ...)`，
   改为将原始 content 交给对应 adapter → **adapter 解析 content 中的 `@名字`**
   （关闭"服务端不解析 @"的挂账）→ 合并显式 mentions → 发布
   `MESSAGE_NEW(sender_id="human:alice", sender_name=username)`。
3. **路由（统一后）**：
   - 有 mentions → 经 MemberDirectory 解析（agent 与人类**同表**）→ 定向；
   - 无 mentions 且 `sender.triggers_proactive` → proactive agents 广播；
   - `_route_human_message` / `_route_agent_message` 分岔删除。
4. **发信给人类**：投递器向 `human:alice` 邮箱投递 → `MAIL_DELIVERED`
   （recipient=`human:alice`）→ adapter 订阅 `MAIL_DELIVERED` 按 member_id
   过滤（与 Agent 同一模式）→ 向所有 attached session 的 sink 扇出。
   ~~**v1 决定**：HumanAdapter 暂不订阅路由事件——NotificationService 广播
   已够，adapter 转发会重复投递。~~（已废弃：邮件模型 R2 单轨化后，
   NotificationService 不再向 chat session 投递 `mail.delivered`，
   adapter 扇出是人类收信的唯一通道。）
5. **LLM 上下文**：context_builder 只写 `alice: 内容`，无类型标签。

---

## 客户端用户名功能

- **SDK**：`TlonClient.authenticate(username: str | None = None)`。
- **CLI/TUI**：原 `uv run tlon chat --username alice`（Python Congress
  TUI）已于 PR-C 退役（2026-07）；现由 Operator（apps/operator）在
  auth 时携带 username。
- **REST**：暂无发言端点；需要时经 AdapterManager 以人类身份发布。
- TUI 界面显示自己的用户名；消息列表渲染不变（名字来源改变而已）。

---

## 实施计划

### 阶段 A — Member 接口化（纯重构，零行为变化）

- 新增 `organization/messaging/member.py`（Member ABC）。
- `participant.py`：`Participant` → `MemberInfo`，更新引用方
  （`participant_directory.py`、`client_gateway.py`、REST/WS 用到处）。
- `Agent` 实现 Member 接口（`member_id` 代理到 `self.id`，
  `member_type="react"`，`triggers_proactive=False`）。
- `ParticipantDirectory` → `MemberDirectory`。
- 测试同步改名；验证四件套（pytest / ruff / check_architecture / check_no_reexport）。

### 阶段 B — HumanAdapter + 多用户（核心）

- 新增 `organization/messaging/human_adapter.py`：`HumanAdapter(Member)` +
  `AdapterManager`（username→adapter、refcount、attach/detach）。
- `websocket.py`：auth 握手接受 `username`；`_handle_send_message` 改为委托
  AdapterManager。
- @ 解析能力下沉至 MemberDirectory，adapter 复用。
- `router.py`：mention 解析改走 MemberDirectory（agents + humans 同表）；
  proactive 闸门改查 `triggers_proactive`；删除 human/agent 分岔。
- `context_builder.py`：去掉发送者类型标签。
- SDK `authenticate(username)` + TUI `--username` / config / 提示。
- MemberDirectory 列举在线人类（删除 `session_count > 0` 塞伪条目的逻辑）。
- 测试：AdapterManager 生命周期、多用户路由、@ 解析、agent@人类 推送；
  冒烟用两个 SDK client 扮演两个用户。

**单轨投递（2026-08，邮件模型 R2 收口）**：

- `HumanAdapter` 订阅 `MAIL_DELIVERED`，过滤 `recipient == self.member_id`，
  向所有 attached session 的 sink 扇出（sink 即 auth 时注册的 WS 事件回调，
  `AdapterManager.attach(username, session_id, sink)` 透传）。
- NotificationService 的影子通道关闭：chat 模式 session 永不经该服务收到
  `mail.delivered`（event / agent 模式的 operator 控制台不受影响）。
- 至此"每个 Member 一个邮箱、投递经 Member"对人类同样成立。

### 阶段 C — 清理 ✅（2026-07-27 完成）

- 移除 `@clients` 伪目标：router 不再为 agent/system 消息附加 `@clients`，
  无 mentions 且不触发 proactive 的消息无 targets（记 `message_unrouted`）。
- 删除 `ClientGateway.send_message`（无生产调用方；发言入口已移交
  AdapterManager）。
- DSL `sender-type` builtin deprecate：保留一个版本，spec 加载校验时发现
  引用记 `spec_uses_deprecated_builtin` warning（**已于 2026-07-28 移除**，
  引用它的 spec 现在 load 时校验失败）。
- 文档同步（dsl-agents.md、client.md、protocol.md 更新握手协议）。

### 阶段 D — registry 机制化 + 亚当（Adam，PID 1）

设计定稿于 2026-07-27（四点决策：亚当不来自用户 spec、查接口而非事件重建
run-state、drain 的事实/决策切分、命名"亚当"）。

**动机**：policy/mechanism 分离。registry 此前既是内核（PID 表 + fork/exec）
又是 init（boot 启动集、wake-on-mention、idle drain 三条策略硬编码其中）。
Linux 把 socket activation 放在 systemd（一个进程）里，Tlon 却焊死在内核里。

**分层**：

- **registry（内核，纯机制）**：`_start_agent` / `_stop_agent` 装配、运行表、
  wake lock、budget 执行、空闲事实检测。订阅 `AGENT_START_REQUEST` /
  `AGENT_STOP_REQUEST` 并执行。删除：MESSAGE_ROUTED 订阅（wake 决策）、
  boot 启动循环、drain 决策。
- **亚当（`member_id="adam"`，`member_type="script"`，内置 Member）**：
  不来自任何用户 spec 文件；由 ServerApp 在 registry 之后初始化
  （内核先起、PID 1 其次——保持 DAG 构造无环，registry 不持有亚当引用）。
  查 registry 的只读接口（`is_running` / `get_config` / `get_all`）获知
  run-state，不自行跟踪事件重建。
- **agent spawn contractor（fork）**：agent 生 contractor 走 registry 直连
  （= 进程调 fork 系统调用，不经过 init）；budget 作为 rlimit 式机制留在
  registry。**只有 boot / wake / drain 三条策略线归亚当**。

**三条策略线**：

1. **boot**：亚当 initialize 后对 `activation=="always"` 的注册 agent 逐一发
   `AGENT_START_REQUEST{reason="boot"}`。boot 变为异步（经总线），server
   不再等 agent 全部起来——REST /agents 在 boot 早期可能显示较少 agent。
2. **wake（socket activation）**：亚当订阅 MESSAGE_ROUTED →
   `mention_target_ids` 中有沉睡 agent（查接口确认未在运行）→ 发携带原事件
   引用的 `AGENT_START_REQUEST{reason="wake", trigger_event}` → registry
   启动入表后**重投递原事件**（循环防护时序不变：agent 已在运行表，
   重投递由它自己的 handler 接收，不会二次唤醒）。registry 侧幂等
   （running/waking 跳过），亚当无需去重状态。
3. **drain**：registry 的 30s monitor 检测到"lazy agent 空闲超时且无 live
   session" → 发事实事件 `AGENT_IDLE{agent_id, idle_seconds}`（机制不含
   判断）→ 亚当决策 → `AGENT_STOP_REQUEST{reason="drain"}` → registry
   在 wake lock 下复查（session/idle 可能已变）后执行 drain
   （contractor 丢弃 persona，resident 发 AGENT_SLEEPING）。

**事件**：新增 `AGENT_START_REQUEST` / `AGENT_STOP_REQUEST` / `AGENT_IDLE`；
既有 `AGENT_WAKING` / `AGENT_WOKEN` / `AGENT_WAKE_FAILED` / `AGENT_SLEEPING`
保留（registry 执行期发布）。亚当的每个决策都是总线上可见的事件，
trace 里可直接看到"谁决定唤醒谁"。

**监管（v1）**：亚当无 worker task，全部工作发生在 EventBus dispatch 上下文
（bus 已隔离 handler 异常）；亚当 initialize 失败 = 内核 panic 等价物，
server 启动失败。运行时重启监管留待需要时再加。

> **P5a 更新（2026-08，策略即数据）**：亚当已重写为通用 `ScriptMember`
> （`agents/script_member.py`）的第一个配置实例——三条策略线从 Python
> 代码变成内置 `ADAM_SPEC_EDN`（`agents/adam.py`）的三条数据规则，
> 经 `ScriptSpec`（`agents/script_spec.py`）解析、通用运行时解释执行：
> `:boot` 规则 `(start-all ...)`、`:mail-delivered` 规则
> `(when (sleeping? (get event :recipient)) (start ...))`、`:agent-idle`
> 规则 `(stop ...)`。查 registry 只读接口改为宿主谓词闭包注入
> （`sleeping?` 等），动作延续"求值纯、动作变数据、引擎执行"（与
> on-complete 4b 同构），trace carrier 透传不变。上文第 237-242 行的
> wake 描述（MESSAGE_ROUTED + trigger_event + 重投递）更早已被邮件模型
> 取代（wake 订阅 MAIL_DELIVERED，收件箱即持久队列），以代码为准。
>
> **P5b 更新（2026-08，script 成员可 spawn）**：relay 是第二个 script
> 成员形态（亚当之后）——`AgentConfig.runtime="script"` 的 agent 经
> prototype 实例化（relay prototype 只含 `script.tlon`，无人格三件套）、
> registry 的 `_start_agent` 按 runtime 分流装配 ScriptMember（注入宿主
> 谓词闭包与 mailbox），运行表/预算/唤醒/idle drain/完工即弃全部复用。
> 邮件语义与 Agent 侧对齐：自发邮件忽略、initialize 补扫未读、处理后
> `mark_read`；`active_session_count` 恒 0，`idle_seconds` 从最近一次
> 规则动作起算。详见 dsl-agents.md 阶段 P5b。

**不在本次范围**：离线收件箱、私聊/分房间（随房间功能重构再议）；
receptionist（"正在唤醒 X…"系统消息，届时只是再订阅一个事件）；
waker 的用户可写规约化（证据表明不值得做，从路线图删除）。

---

## 验收标准

- 两个 `uv run tlon chat --username A/B` 客户端可同时在线，身份可区分。
- 人类可在消息正文中写 `@谢云舒`，服务端解析并定向（无需显式 mentions）。
- agent 可 `@alice`，消息推送到该用户所有在线客户端。
- LLM 上下文中不出现任何人类/agent 类型标签。
- 无 mention 的 agent 消息不触发 proactive 广播（无无限循环）。
- 既有 505 测试全绿 + 四件套通过。

---

*代码为准，文档为映射。实施中如与设计冲突，回写本文档。*
