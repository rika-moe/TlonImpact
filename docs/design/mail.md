# 邮件模型设计（Republic of Letters）

> 状态: ✅ R1a（Mail 模型 + Mailbox 存储）/ R1b（管线大切换）/ R2 PR-A（服务端协议）/ PR-B（operator mail 视图）/ PR-C（拆桥 + Congress 退役）已实施（2026-07-29）
> 日期: 2026-07-28

---

## 实施状态（R2 PR-A 落地记录）

R2 PR-A（服务端协议，2026-07-29）已完成：

- **WS 发信协议**：`message` 消息支持显式 `to` / `cc` / `subject`。
  `HumanAdapter.speak` / `AdapterManager.publish` 签名扩展；收件人由
  三路合并去重——`to`（每项过地址解析：名字 / id / 全地址）+
  `mentions`（deprecated 别名）+ 正文 `@名字` 解析（R1b 桥）。
  `cc` 同样解析。零可解析收件人仍 `ValueError`，文案引导用 `to`
  指定收件人。`to` / `cc` 非 list → 服务端报错给客户端。
- **REST `/inbox`**：`GET /inbox?username=x&limit=N`（username 缺省
  `guest`，limit 缺省 50 上限 200）返回 `human:<username>` 邮箱的
  `recent()`（inbox + sent 合并）——`{"mails": [...], "count": N}`；
  username 为空或含路径分隔符 → 400。
- **人类投递单轨化**：chat 模式 session 的 `mail.delivered` 由
  HumanAdapter 投递（订阅 MAIL_DELIVERED，把 `recipient ==
  human:<username>` 的副本扇出到自己所有 attached session），
  NotificationService 不再向 chat session 投递邮件——"每个 Member 一个
  邮箱、投递经 Member"对人类同样成立。event / agent 模式不受影响——
  operator 是运维控制台，必须看到全部邮件流量。其余事件类型不受影响。

**过渡期桥（PR-C 已全部拆除 ✅）**：

- ~~**`mentions` 保留为 `to` 的 deprecated 别名**~~——已随 PR-C 移除；
  旧客户端仍发送 `mentions` 时服务端静默忽略。
- ~~**正文 `@` 解析保留**（R1b 桥）~~——已随 PR-C 移除；显式 `to`/`cc`
  是唯一收件人入口。
- ~~**REST `/history` 仍返回空列表**~~——已随 PR-C 移除（连同 WS 初始
  空 `history` 推送）；拉邮件用 `/inbox`。
- **人类无已读 cursor**（v1 决策）：`/inbox` 不做已读标记。

**R2 分期**：

- **PR-B** ✅：operator 增加 mail 视图，作为人类主界面。
- **PR-C** ✅：拆桥——`mentions` 别名、`@` 解析、`/history` 已移除，
  Python Congress TUI 已退役（`tlon chat` 命令删除，textual 依赖移除）。

---

## R1b 落地记录

R1a/R1b 已完成：`Mail`/`Mailbox`（R1a）、投递器 `MailDelivery`
（`organization/mail/delivery.py`）、agent 收信（MAIL_DELIVERED 入队 +
boot 未读扫描 + 每封一 session + 完成 mark_read）、`_send_mail` 回复头
继承、`sendEmail` 工具、trigger/proactive 退役、context_builder 邮箱
历史、wake 重投递管道整套删除、receptionist 移除。

R1b 桥接偏差中已被 R2 PR-A 收敛的项：**推送按收件人过滤**（原全量
推送，PR-A 起 chat 模式按收件人过滤；后于单轨化重构移交
HumanAdapter——NotificationService 不再向 chat session 投递
`mail.delivered`）。其余桥已全部随 PR-C 拆除（见上方清单）。

R1b 其它遗留：`AgentConfig.proactive` 字段保留但 deprecated ignored
（旧 agent.yaml 可继续加载）；spec 旧 key（`:proactive` /
`:room-history`）静默忽略；引用 `mentioned?` 的旧 trigger 表达式加载
失败 → 回退 DefaultSpec 并记 `agent_spec_load_failed` 日志。

---

## 背景与动机

Tlon 的消息系统建立在"聊天室"假设上：一个共享大厅、消息广播、mention 定向、
proactive 维持讨论感。这是**同步在线语义**——消息的生命在总线上，转瞬即逝。
沉睡 agent 会错过消息，于是系统被迫长出"重投递"机制（`StartRequest.trigger_event`
携带原事件、启动后重发、靠运行表做循环防护）来补救。

邮件模型把这一切换成**异步协作语义**：每封信有明确收件人，落在收件人邮箱里
持久等待；收件人何时读，与发送者无关。邮箱即队列——agent 不在场，邮件照样
安全；agent 醒来，扫一遍未读即可。重投递机制存在的理由整个消失。

### 核心隐喻：Republic of Letters（书信共和国）

代表大会的隐喻没有死，而是升级了：Congress 不需要实体大厅——每个代表凭书信
出席。17–18 世纪启蒙时代的"文人共和国"正是如此：一个由多人邮件沟通构成的、
无实体的信件国度。我们每个人都身处大会中，每个人都是代表。

上下文不再默认共享，而是**显式传递**（forward / CC）——这更符合
"每个 agent 是独立心智"的本体论。

---

## 设计决策（已与作者确认）

| # | 决策 | 结论 |
|---|------|------|
| 1 | 语义 | 聊天室（同步在线）→ 邮件（异步协作） |
| 2 | 地址 | `<agent-id>@tlon.internal`（如 `gu-yanxi@tlon.internal`，id 即 local part）；人类 `username@tlon.internal` |
| 3 | 收件人 | 发送时显式指定；前端展示 agents 并提供填写功能 |
| 4 | 共享上下文 | 由邮件 **forward**（及 CC）显式传递 |
| 5 | 广播 | 提供 `all@tlon.internal` 邮件列表（展开为全体成员） |
| 6 | 邮箱 | 每成员一个 mailbox |
| 7 | 已读 | cursor 记录已读位置 |
| 8 | proactive | **退役**（spec 字段、trigger 分支、`triggers_proactive` 保险丝整套删除） |
| 9 | 唤醒公告 | 不要（receptionist 系统消息随之移除） |
| 10 | 未读批处理 | **每封邮件一个 session**（复用现有多 session 调度） |
| 11 | 查无此人 | **退信**（bounce 系统邮件回给发件人） |
| 12 | TUI | 最小改动适配；以后用 React+Ink 重写 |

---

## 邮件数据模型

```
Mail: id, from, to[], cc[], subject?, content,
      in_reply_to?, thread_id, forward_of?, timestamp
```

- **线程**：`in_reply_to` 指向上游邮件；`thread_id` = 线程根邮件的 id
  （回复时继承）。不用 subject 前缀拼线程——机器用 id 链。
- **subject** 可选（LLM 通信经常不需要）。
- **forward**：`forward_of: mail_id` 字段保留结构信息（谁转发了哪封），
  投递时原信内容被引用进新信。

## 邮箱布局

- **agent**：复用现有 home——`~/.tlon/<agent-id>/mail/inbox.jsonl` +
  `sent.jsonl`。contractor 被回收时邮箱随 home 删除，语义自洽。
- **人类**：新建 home——`~/.tlon/human-<username>/mail/`
  （member id 中的冒号换连字符作目录名）。
- **已读**：`inbox.cursor` 记录已读位置（jsonl append-only，逐封改标志
  需重写文件；cursor 只记"读到哪"最省）。agent 处理完一封即推进。

## 投递器（router 的继任者）

sendEmail → 校验收件人 → 展开 `all@tlon.internal` → 写入每个收件人 inbox →
对每个收件人发 `MAIL_DELIVERED` 定向事件。

- **all@ 展开范围**：全部注册 agent（含沉睡）+ 有邮箱的人类——all 的字面义
  即全体代表。
- **退信**：收件人查无此人时，系统以 `postmaster@tlon.internal` 给发件人
  回一封 bounce 邮件（错误反馈进收件箱，LLM 能自行纠正地址）。
- 发送者的邮件同时写入自己的 `sent.jsonl`。

## 收信处理

- **沉睡 agent**：邮件到达 → registry 发 wake 请求（按收件人 spec 的
  `:on :mail-delivered` 派生；**不再携带 trigger_event**，重投递管道整套
  删除）→ registry 启动 agent → agent 扫 inbox cursor 之后的未读邮件。
- **运行中 agent**：`MAIL_DELIVERED`（按收件人定向）→ 入队 → 正常 session。
- **每封邮件一个 session**：通知事件带 mail id；session 处理该邮件，完成即
  推进 cursor。每封信独立成败、独立 trace；积压 N 封则 N 个 session
  （复用现有多 session 调度器）。

## trigger / DSL 新语义

- 默认触发规则：**未读邮件 To 或 CC 含我 → 处理**。
- DSL builtin 重定义：`addressed-to-me?` = To 含我；新增 `cc-ed?` = CC 含我
  （`mentioned?` 随之退役——邮件语义里"提及"不再是路由依据）。
- 退役清单：spec 的 `proactive` 字段、trigger 的 proactive 分支、
  Member 接口的 `triggers_proactive`、router 的保险丝。
- `all@` 邮件到达时各 agent 的 spec 自行决定理不理（默认理）。

## context_builder

房间历史 → **邮箱历史**：inbox + sent 最近 N 封（按 thread 组织呈现）。
LLM 上下文里不再有"别人的对话"——只有自己的通信。

## 人类侧

- 人类邮箱持久等待：离线时邮件留在邮箱，连接后走 REST `/inbox` 拉取。
- 人类投递走 Member 抽象（单轨）：HumanAdapter 订阅 `MAIL_DELIVERED`，
  按 recipient 过滤后向 attached sessions 扇出；NotificationService 不向
  chat 模式 session 投递 `mail.delivered`（event 模式的 operator 运维
  控制台不受影响，仍可见全部邮件流量）。
- WS 协议：发消息带 `to`/`cc`/`subject` 字段（`mentions` 别名与正文
  `@` 解析桥已于 PR-C 移除）；拉历史改为 REST `/inbox`。
- 人类**无已读 cursor**（v1 决策）：人类邮件不做已读追踪。
- operator（React+Ink）的 mail 视图是人类主界面（PR-B）；Python
  Congress TUI 已于 PR-C 退役。

## 系统邮件

- 退信：`postmaster@tlon.internal`。
- 亚当的生命周期通知如需可见，以 `adam@tlon.internal` 发系统邮件
  （v1 不需要——唤醒公告已决策删除）。

---

## 删除清单

- `StartRequest.trigger_event` 携带/重投递/循环防护整套管道
- 消息正文 @ 解析（human_adapter）、mention 解析（MemberDirectory）
- `messages.jsonl` 共享房间历史（断代，不迁移——开发期项目）
- context_builder 的房间历史
- proactive 整套（见上）
- receptionist 唤醒公告（，随本重构回滚）

Member / 亚当的 policy-mechanism 分层**不受影响**。

---

## 分期实施

- **R1（服务端核心）**：Mail 模型 + mailbox 存储（含 cursor）+ 投递器 +
  agent 收信处理（唤醒扫描 + 每封一 session）+ sendEmail 工具 +
  trigger/proactive 退役 + context_builder 邮箱历史 + 删 trigger_event 管道
- **R2（人类侧）**：
  - **PR-A（服务端协议）** ✅：WS `to`/`cc`/`subject`、REST `/inbox`、
    NotificationService 按收件人过滤（chat 模式；后于单轨化重构移交
    HumanAdapter）
  - **PR-B** ✅：operator mail 视图（人类主界面）
  - **PR-C** ✅：拆桥——`mentions` 别名、`@` 解析、`/history` 已移除，
    Python Congress TUI 已退役
- **R3（打磨）**：forward、退信、all@ 细节、线程呈现

R1 内部可再拆 PR。

## 验收标准

- agent A sendEmail 给沉睡的 agent B：邮件落在 B 的 inbox，B 被唤醒，
  扫描未读并逐封处理（每封一个 session），处理后 cursor 推进。
- agent 转发收到的邮件给 C（forward_of 保留引用），C 的上下文里出现
  原信内容——共享上下文靠显式传递。
- `all@tlon.internal` 展开为全体成员；每个收件人的 spec 各自决定响应。
- 发往不存在地址的邮件收到 postmaster 退信。
- 人类离线期间邮件留在邮箱；TUI 连接后可见，可显式填写收件人回信。
- 全程无 proactive、无重投递、无房间历史。

---

*代码为准，文档为映射。实施中如与设计冲突，回写本文档。*
