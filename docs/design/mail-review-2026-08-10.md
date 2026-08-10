# 邮件子系统评审记录（2026-08-10）

> 评审范围：`organization/mail/{mail,mailbox,delivery}.py`、`organization/messaging/human_adapter.py`、
> `tools/mail/send_email.py` 及使用方（`app/server/api/rest.py`、`agents/member_runtime.py`、
> `agents/agent_registry.py`）。对照权威设计 [mail.md](./mail.md)（R2）。
>
> 结论：实现方式总体正确，与 R2 设计高度一致；发现 1 个设计违背（P1）与 7 个小问题（P2）。
> 修复落在分支 `fix/mail-review-findings`。

---

## 实现正确的部分（评审确认，无需改动）

- **错误即数据**：查无此人时 postmaster 退信进发件人 inbox，LLM 可自行纠错
  （`delivery.py:243-265`）；退信直接写 inbox、不发 `MAIL_NEW`，无 bounce 循环风险
- **cursor 读模型**：已读 id 集合 + append-only jsonl（`mailbox.py:26-29`）；`mark_read` 幂等
- **own-mail guard**：自发自收的信直接忽略（`member_runtime.py:336-342`），boot scan 同样
  清理（`:816`）——`all@` 自拷贝不触发自循环
- **单轨投递**：人类邮件只走 HumanAdapter 扇出，NotificationService 不重复推
- **入口校验**：`Mail.create` 强制正文非空、至少一个收件人、地址形态合法；to/cc 跨字段
  去重在投递侧兜底（`delivery.py:189-190`）
- **D6.1 语义自洽**：gate 拒绝的邮件标记已读；队列满丢弃的留未读，等下次 boot 救援扫描

---

## P1：离线人类收不到信 —— 与设计矛盾 ✅ 本分支修复

**设计**（mail.md）：

- 「人类邮箱持久等待：**离线时邮件留在邮箱**，连接后走 REST `/inbox` 拉取」（:167）
- 「all@ 展开范围：全部注册 agent（含沉睡）+ **有邮箱的人类**」（:135）

**实现**（修复前）：

- `delivery.py::_resolve_address` 只认**在线** adapter → 发给离线人类的信被**退信**，
  发件人收到"地址不存在"——LLM 会据此得出"此人不存在"的错误结论
- `delivery.py::_expand_all` 只展开 `list_online()`，离线人类被广播静默跳过

**根因**：系统没有持久的人类名录，`AdapterManager` 只管在线会话，delivery 无法区分
「人类存在但离线」与「查无此人」。

**修复**：

- `TlonPaths` 新增 `member_mail_dir_existing()`（不建目录的判定）与 `known_human_ids()`
  （扫描 `human-*/mail` 目录）；目录结构知识不外泄到 delivery
- `_resolve_address`：在线 adapter **或邮箱目录已存在**即可解析为已知人类
- `_expand_all`：在线人类 ∪ 有邮箱的人类（下游去重）
- `AdapterManager.attach` 首次创建 adapter 时建立邮箱目录——"接入过一次"即成为已知人类
- 测试：离线已知人类收信、未知人类仍退信、`all@` 含离线已知人类

---

## P2：小问题

| # | 问题 | 位置 | 状态 |
|---|------|------|------|
| 1 | `sendEmail` 虚拟工具立即回 success + "✉️ 已发送"，真实投递异步；地址错误只能靠退信发现，LLM 可能向用户谎报已送达 | `send_email.py:123` | ✅ 本分支修复（message 改为"已受理，投递结果以退信为准"） |
| 2 | Mailbox 无单一属主：delivery 缓存一份、`rest.py:327` 每请求新建、`agent_registry.py:484` 又一份；`asyncio.Lock` 是实例级，跨实例写无互斥 | 多处 | ✅ 本分支修复（`mailbox.py` 按目录共享锁：同目录所有实例共用一个 asyncio.Lock，进程内写串行化） |
| 3 | 无幂等投递：`MAIL_NEW` 重发会在 inbox 写重复行、重复发 `MAIL_DELIVERED`、重复退信 | `delivery.py` | ✅ 本分支修复（进程内有界去重集 `(mail_id, member)`；重启或淘汰后重投由 append-only 邮箱容忍） |
| 4 | `AdapterManager.attach` TOCTOU：`await initialize()` 在 get 与赋值之间，并发首连同名用户会建两个 adapter，先建的订阅泄漏 | `human_adapter.py:344-348` | ✅ 本分支修复（asyncio.Lock 保护 attach/detach） |
| 5 | 名称碰撞 agent 优先：与 agent 同名的人类永远无法被点名。docstring 已承认，设计文档未记 | `delivery.py:208`、`human_adapter.py:469-485` | ✅ 本分支补记 mail.md |
| 6 | 存储无界增长：`inbox.cursor` 与 `inbox.jsonl` 同步增长（cursor 大小 ∝ inbox 大小，单独压缩 cursor 无意义）——轮转/归档需要留存策略设计决策（保留多久？是否归档？），非代码层面能独自拍板 | `mailbox.py` | ⏸ 记录暂缓（待留存策略设计决策） |
| 7 | `all@` 自拷贝运行期挂为未读，直到下次 boot scan 才清 | `member_runtime.py:336-342` | ✅ 本分支修复（own-mail guard 处立即标记已读） |

---

*评审人：Kimi（结对）· 2026-08-10*
