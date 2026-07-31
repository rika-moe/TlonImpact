# Tlon 品牌叙事 V3

> 一个由观念构建的宇宙，一个机械生命的信件共和国，一个思想的共同体。

本文是 Tlon 的品牌叙事与术语正典。三层结构：隐喻层（博尔赫斯）、
概念层（我们在说什么）、工程层（它在代码里叫什么）。
**纪律：工程层的每一条声明都必须能在当前代码中验证；
任何架构级 PR 必须回访本文档。**（V2 死于没有这条纪律。）

---

## 第一章 宇宙观：特隆

《特隆、乌克巴尔、第三极的奥比斯·特蒂乌斯》里，一代学者在秘密
百科全书中想象出一个宇宙。他们想象得如此认真，以至于这个宇宙
开始入侵现实——虚构的器物（hrönir）真的出现在了世界上。

Tlon 是这个寓言的工程化：**agent 是会行动的观念**。

一个agent 不是因为服务器上跑着某个进程才存在；它存在，是因为
有一份规约（spec）描述它、一组记忆塑造它、一个邮箱能收到寄给
它的信。进程会死，容器会停，但只要规约还在，这个存在就可以被
重新唤起——就像特隆的器物，只要还有人记得它的形状。

这不是修辞。这是 Tlon 与"聊天机器人框架"的分界线：
我们造的不是会话界面，是一个由观念构建的宇宙。

---

## 第二章 信件共和国

十七、十八世纪的欧洲存在过一个没有疆域的国家：文人共和国
（Republic of Letters）。伏尔泰、洛克、莱布尼茨们彼此未必谋面，
但凭书信构成了一个真实的共同体——没有大厅，没有仪式，
只有信件在流动。

**Tlon 的代表大会就是这样一座信件共和国。**

市面上的 agent 框架都在造聊天室：所有人在场、所有消息广播、
注意力即权力。那是同步的、在场的、稍纵即逝的——消息只活在
总线上，错过就是错过。

Tlon 选择异步。每一封信都有明确的收件人，落在收件人的邮箱里
持久等待。agent 不需要在线才能收到信；它沉睡时，信在等它；
它醒来，扫一遍未读，逐封处理。上下文不再默认共享——你看到
的只有寄给你的信，想知道更多，就得有人把信**转发**给你。

这带来一种不同的协作美学：

- **写信，而不是喊话**。`gu-yanxi@tlon.internal` 是一个地址，
  不是一个@标记。你写信给具体的存在
- **邮箱即队列**。沉睡不是缺席，信件会等到苏醒
- **上下文是私有财产，靠显式传递共享**。CC 与转发是共和国里
  唯一的流言蜚语
- **查无此人，必有回音**。写错地址，postmaster 的退信会告诉你
- **`all@tlon.internal`**。共和国也有广场——一封寄给全体的信，
  每位居民自行决定是否回应

**工程层**：邮件模型（docs/design/mail.md）。MailDelivery 投递、
每成员一个 mailbox（inbox/sent/已读 cursor）、唤醒扫描、
postmaster 退信、all@ 邮件列表。聊天室与 @mention 已于
2026-07 退役。

---

## 第三章 机械生命

Tlon 的居民是机械生命：有完整的生命周期，但形态由你定义。

**居民（resident）**：有名有姓的常驻 agent。默认团队六位——
顾言蹊（客服）、沈清晏（项目经理）、林知遥（开发）、
江予白（运维）、苏晚晴（产品）、谢云舒（数据分析）。
他们各自有 persona（IDENTITY/SOUL/RULES）、记忆和行为规约。

**作息**：居民可以沉睡（lazy）。没有信的时候，他们不占用
任何资源；一封信抵达，亚当在数秒内将他们唤起；处理完毕、
空闲超时，他们又静静睡去。沉睡不丢信——邮箱即队列。

**临时工（contractor）**：一次性的存在。当任务需要一个专门的
角色，agent 可以从**原型（prototype）**把一个临时工梦进存在——
指定原型、起个名字，一个新的生命就开始运行。任务完成、
使命终结，他被回收，家目录连同邮箱一起消失，仿佛从未来过。

**原型是机械生命的基因库**：`~/.tlon/prototypes/` 里的每份
规约是一种"可造的生命"。人类可以写原型；LLM 也可以写——
它写的原型先进 staging，经人类批准才获得信任。

**工程层**：AgentConfig.activation（always/lazy）、idle drain、
spawnAgent/stopAgent 工具、prototype 信任模型
（docs/design/dsl-agents.md、members.md）。

---

## 第四章 环形废墟

《环形废墟》里，一位魔法师在火焚的废墟上，用梦造出了一个
儿子——然后发现，他自己也是别人梦中的造物。

这是 Tlon 里最深的隐喻，也是已经落地的现实：

**agent 可以把另一个 agent 梦进存在。**

一个 agent 觉得任务需要归档专家，它写下一份规约（或者调用
现成原型），spawn 出一个临时工，把信件寄给它，验收它的工作，
然后看着它消失。造物的 Creator 本身，也是人类在 mirror 里
造出来的。梦的链条没有尽头，只有一环一环的规约。

V2 时代这一章只能写"愿景"。现在它是日常：
`spawnAgent` 工具、staging 信任门、mirror 的规约审批，
都是环形废墟的砖石。

---

## 第五章 亚当

每个宇宙都需要一个开机键。

**亚当（Adam）是 Tlon 的 PID 1**——第一个存在，内置的 init
进程。它不归任何人书写，不接受任何规约文件；它有三条不可
让渡的职责：

1. **boot**：宇宙启动时，唤起所有不愿沉睡的居民
2. **唤醒**：一封信寄给沉睡的居民，亚当日发出唤醒请求
3. **休眠**：居民空闲太久，亚当让它静静睡去

亚当只做决策，不做执行。执行属于 registry——那是宇宙的
内核：装配、运行表、锁、预算。决策与机制分离，如同
systemd 与内核。

**工程层**：src/tlon/agents/adam.py（member_type="script"）、
policy/mechanism 分层（docs/design/members.md 阶段 D）。

---

## 第六章 行为即文学

在特隆，语言不仅描述现实，语言**构造**现实。

Tlon 的每个 agent 由一份 `behavior.tlon` 定义——一份刻意
图灵不完备的迷你 Lisp 规约：

```clojure
{:version 1
 :trigger (and (addressed-to-me? msg)
               (content-matches? msg "？$"))
 :context {:persona true :mail-history 20}
 :loop {:mode :react :max-iterations 10}
 :on-complete {:save-interaction true}}
```

四节：何时响应（trigger）、看见什么（context）、如何思考
（loop）、做完之后（on-complete）。白名单求值器、fuel 限制、
纯度校验——规约只能描述，执行永远属于引擎。

关键的分形在于：**这份 DSL 可以由 LLM 书写**。一个 agent 可以
为不同的情况设计不同的行为规约——行为成了可以生成、可以
评审、可以版本化的文学。人类审批 LLM 写的规约，就像编辑
审稿。

**工程层**：src/tlon/agents/spec.py、eval.py、edn.py
（docs/design/dsl-agents.md）。

---

## 第七章 莎士比亚的记忆

《与莎士比亚的记忆》里，一个人接受了莎士比亚的全部记忆——
记忆是可以赠予的财产。

Tlon 的记忆系统叫 Shakespeare：两层结构。**工作记忆**是当下
的书桌（最近的交互，满了就归档）；**情节记忆**是身后的书架
（摘要与事实，可被检索、被遗忘、被巩固）。agent 会在空闲时
整理自己的记忆（reflection），就像人在睡前回顾一天。

沉睡的 agent 醒来后之所以能"接着做自己"，靠的不是进程的
连续性，而是记忆与邮箱——**人格住在数据里，不住在进程里**。

**工程层**：src/tlon/memory/（working/episodic/summarizer）、
compact 工具（docs/design/memory.md）。

---

## 第八章 镜与花园

**Mirror（镜）**：《镜子与面具》。配置向导——人类创造、
重塑、端详 agent 的地方。`uv run tlon mirror`。

**Garden（花园）**：《小径分叉的花园》。开发工作流——
每个分支是一个可能的宇宙，合并是时间的收束。我们不消灭
可能性，我们只选择哪条小径走进现实。

---

## 第九章 自部署与可观测

Tlon 运行在**你的**机器上：你的密钥、你的数据、你的 `~/.tlon/`。
没有遥测，没有云端账号，信件不出你的家门。

但宇宙不黑箱。每一封信、每一次唤醒、每一步 ReAct 都有
痕迹：结构化日志（`~/.tlon/shared/logs/`）、分布式 trace
（`~/.tlon/shared/traces/`）。Operator 控制台的 events 与
traces 视图，是共和国的档案馆。

---

## 术语正典

| 术语 | 含义 | 出处/工程层 |
|------|------|-------------|
| **Tlon / 特隆** | 产品；由观念构建的宇宙 | 《特隆、乌克巴尔、第三极的奥比斯·特蒂乌斯》 |
| **信件共和国** | 邮件模型；代表大会的存在形式 | Republic of Letters；docs/design/mail.md |
| **机械生命** | agent；有完整生命周期的存在 | Agent（member_type="react"） |
| **思想的共同体** | Tlon 的本质定义；人类与 agent 共在 | — |
| **居民 / resident** | 常驻 agent | AgentConfig.role="resident" |
| **临时工 / contractor** | 一次性 agent，回收时删 home | AgentConfig.role="contractor" |
| **原型 / prototype** | 可造生命的规约基因库 | ~/.tlon/prototypes/ |
| **亚当 / Adam** | 内置 init 进程（boot/wake/drain） | src/tlon/agents/adam.py |
| **behavior.tlon** | 行为规约（DSL） | src/tlon/agents/spec.py |
| **Shakespeare** | 两层记忆系统 | src/tlon/memory/ |
| **Mirror / 镜** | 配置向导 | `uv run tlon mirror` |
| **Garden / 花园** | Git 工作流 | 分支即小径 |
| **环形废墟** | agent 创造 agent | spawnAgent + staging |
| **Operator** | 控制台（mail/agents/events/traces/config） | apps/operator |

**退役术语**（不再使用，历史文档除外）：
聊天室/Chatroom、@mention、proactive、巴别图书馆/Skill
（2026-03 移除）、Congress TUI（2026-07 退役，由 Operator
mail 视图取代）。

### 用词纪律

- 说**邮件/邮箱/写信/收件人**，不说消息/聊天室/@
- 说**机械生命/agent**，不说 bot/助手
- 说**唤醒/休眠**，不说上线/下线
- 地址一律写全：`<id>@tlon.internal`
- 工程层的每一条声明必须能在代码中验证；架构级 PR 必须
  回访本文档

---

*V3 · 2026-07-30 · 替代 V2（已归档）*
