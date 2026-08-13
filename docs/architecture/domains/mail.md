# Mail 层

> 状态: ✅ 已验证
> 最后验证: 2026-08-13

---

## 职责

消息系统：R2 邮件模型（显式 to/cc 唯一入口）、收件人解析与展开、文件邮箱
（append-only + 已读游标）、投递与 bounce、人类在 bus 上的存在（HumanAdapter）。
位于 organization 域，不 import agents / memory / app。

---

## 模块结构

```
organization/
├── mail/
│   ├── mail.py       # Mail dataclass + 地址规则（tlon.internal 域、all@、postmaster@）
│   ├── mailbox.py    # Mailbox：单成员文件邮箱（inbox/sent/cursor 三文件）
│   ├── delivery.py   # MailDelivery：MAIL_NEW → 展开/解析/落盘/MAIL_DELIVERED/bounce
│   └── __init__.py   # 空（仅 license 头）
└── messaging/
    ├── member.py         # Member ABC：bus 公民契约（identity + lifecycle + bus I/O）
    ├── member_info.py    # MemberInfo：UI/wire 只读 DTO
    ├── human_adapter.py  # HumanAdapter（人类在 bus 上的存在）+ AdapterManager（生命周期）
    └── __init__.py       # 仅 docstring
```

---

## 关键类

| 类 | 职责 | 路径 |
|----|------|------|
| `Mail` | 邮件数据模型；`create` 校验（非空 content、至少一个收件人、地址合法）；thread 由 id 链接（`in_reply_to`/`thread_id`） | `mail/mail.py:101`（create 在 `:129-188`） |
| `address_for_member` / `member_id_for_address` | member id ↔ 地址映射（后者有损，只回 local part） | `mail/mail.py:57`, `:75` |
| `Mailbox` | 文件邮箱：append-only jsonl + 已读游标；同目录多实例共享锁 | `mail/mailbox.py:58` |
| `MailDelivery` | 投递组件（router 继任者）：订阅 MAIL_NEW，落盘 + 每收件人发 MAIL_DELIVERED | `mail/delivery.py:72` |
| `Member` | bus 公民抽象（`member_id`/`member_type`/`get_state`，`address` 派生）；统一点在 Member 层而非 Agent 层 | `messaging/member.py:30` |
| `HumanAdapter` | 人类的 bus 存在；session 是其 attachment（refcount 语义） | `messaging/human_adapter.py:73` |
| `AdapterManager` | adapter 生命周期（username→adapter）+ 第一层收件人解析 | `messaging/human_adapter.py:296` |

---

## 核心机制

- **R2 邮件模型**：显式 `to`/`cc` 是唯一入口，R1b `mentions` 别名与 `@名字` 内容
  解析桥已在 PR-C 删除（`human_adapter.py:25-29` docstring）；零可解析收件人的
  邮件直接拒绝（`human_adapter.py:233-236`）。
- **收件人解析两层**（ 对齐"离线人类"规则）：
  - adapter 层（名称→地址）：`AdapterManager.resolve_address`
    （`human_adapter.py:546-589`）——完整地址直通 → agent id → agent display name →
    在线 human → **有持久邮箱的离线 human**；
  - delivery 层（地址→member id）：`MailDelivery._resolve_address`
    （`delivery.py:228-247`）——local part 冲突时 agent 优先；human 在线或有持久
    邮箱即收。
  - 两侧用同一条离线规则，保证"此处可解析 = 彼处可送达"（注释明确写于
    `human_adapter.py:553`、`:584`）。
- **all@ 展开**：注册 agents（含沉睡，`AgentInfoProvider.get_all_ids`）+ 在线
  humans + 已知离线 humans（`known_human_ids`）；to+cc 展开后保序去重，一人一份
  （`delivery.py:197-226`）。
- **邮箱即队列 + wake-on-mail**：邮件先持久化进收件箱再发事件；沉睡 agent 由
  `AgentRegistry._on_mail_delivered` 唤醒（仅"已注册 + 未运行/未唤醒 + spec 声明
  `:on :mail-delivered`"，发 `AGENT_START_REQUEST{reason="wake"}` 并续投递 trace，
  `agents/agent_registry.py:645-679`）；唤醒后 `MemberRuntime._scan_unread_mail`
  逐封重放未读（`agents/member_runtime.py:800-846`）。不携带 trigger_event、不做
  重投递。registry 侧机制详见 [agents.md](./agents.md)。
- **已读游标**：`inbox.cursor` 一行一个已读 mail id（集合而非 per-mail flag，
  append-only 友好，乱序处理仍正确；缺文件 = 全未读，`mailbox.py:18-29`）。认知
  入队的邮件延迟到 session 完成才 mark_read（D6.1，`agents/executor.py:144-156`）；
  自己发的邮件立即标记已读防自循环残留（`agents/member_runtime.py:339-347`）。
- **进程内幂等**：`_seen` OrderedDict 记 `(mail_id, member)` 键，cap 4096 最旧
  逐出；重启或逐出回落重投递，append-only 邮箱容忍（`delivery.py:69`、`:257-271`）。
- **bounce**：未知收件人汇总后由 `postmaster@tlon.internal` 退回信进发件人收件箱，
  LLM 可自行改址（`delivery.py:288-310`）；发件人无邮箱则 bounce 丢弃记 warning
  （`delivery.py:182-188`）。
- **`_LOCKS` 锁**（ 现状）：`mailbox.py:52-55`——模块级 `dict[Path, asyncio.Lock]`，
  同目录所有 Mailbox 实例（delivery 缓存、agent runtime、REST handler）共享一把锁；
  **条目永不移除**，注释称"按 member 数有界"，但 member 目录生命周期内持续增长，
  无清理机制。
- **人类侧投递单轨**：HumanAdapter 订阅 MAIL_DELIVERED，只 fanout 收件人是自己
  member_id 的副本给 attached sessions（`human_adapter.py:151-176`）；
  NotificationService 明确不向 chat session 推 mail
  （`app/server/core/notification_service.py:246-248`）。
- **持久邮箱的建立**：首次 session attach 或首次 `publish_as` 即创建
  `human-<username>/mail/` 目录，此后该 human 即"已知"，离线邮件留在收件箱
  （`human_adapter.py:371-390`；`publish_as` 瞬态 adapter 模式在 `:463-525`）。
- **trace 树**：`mail.received`（human_adapter / executor 发件）→ `mail.delivery`
  （`delivery.py:139-148`，continue_trace）→ `agent.wake`。

---

## 存储布局

- agent：`~/.tlon/{agent_id}/mail/{inbox.jsonl, sent.jsonl, inbox.cursor}`
  （`base/paths.py:127-130`）。
- human：`human:<username>` → `~/.tlon/human-{username}/mail/`——冒号→连字符只发生
  在 `member_mail_dir`（`base/paths.py:132-144`）。
- `member_mail_dir_existing`（`paths.py:146-159`）：不创建目录的存在性检查，专供
  "是否已知 human"判断；`known_human_ids`（`paths.py:161-173`）扫 `human-*/mail/` 目录。
- **Mailbox 的构造方（4 处）**：MailDelivery 的 on-demand 缓存
  （`delivery.py:249-255`）、AgentRegistry 每 agent 一个（`agents/agent_registry.py:484`）、
  REST `/inbox` handler（`app/server/api/rest.py:405`）、MemberRuntime 注入自用。
  Mailbox 本身不知道布局策略，目录由调用方解析（`mailbox.py:31-33`）。

---

## 装配与边界

- ServerApp 装配顺序：`AdapterManager`（`app/server/serverapp.py:180-187`）→
  `MemberDirectory`（`:189-195`）→ `MailDelivery`（`:197-207`）；REST 注入
  `adapter_manager` 供 `POST /messages`（`rest.py:1131` 起，`handle_send_message`
  在 `:415`）。
- import-linter 契约：**organization 只依赖 base**（`pyproject.toml:178-190`）——
  mail 域不 import agents / memory / app。
- 测试：`src/tests/unit/test_mail.py`、`test_mail_delivery.py`、`test_human_adapter.py`、
  `test_rest_inbox.py`、`test_rest_send_message.py`、`src/tests/tools/test_mail_tools.py`。

---

## 文档索引

| 文档 | 目的 | 深度 |
|------|------|------|
| [../../design/mail.md](../../design/mail.md) | 消息系统设计（R2 邮件模型） | L1 |
| [../../design/members.md](../../design/members.md) | Member 与亚当：bus 公民契约 | L1 |
| [agents.md](./agents.md) | wake-on-mail 的 registry 侧机制 | L2 |

---

## 验证清单

- [x] R2 模型：显式 to/cc 唯一入口，mentions/@名字 桥已删
- [x] 收件人解析两层同规则（"此处可解析 = 彼处可送达"）
- [x] 邮箱即队列 + wake-on-mail + 已读游标集合语义
- [x] 进程内幂等 _seen 与 bounce/postmaster 行为
- [x] import-linter 契约：organization 只依赖 base
