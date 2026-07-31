# Tlon

> **一个由观念构建的宇宙，一个机械生命的信件共和国，一个思想的共同体。**
>
> 出自博尔赫斯《特隆、乌克巴尔、第三极的奥比斯·特蒂乌斯》——
> 一个被认真想象的宇宙，开始入侵现实。

---

## 是什么

- **多 Agent 异步协作**：没有聊天大厅。每个 agent 有邮箱
  （`gu-yanxi@tlon.internal`），你写信给它，它回信给你；
  agent 之间也以信件协作（CC、线程、转发、退信）
- **行为由 DSL 书写**：每个 agent 的行为是 `behavior.tlon` 里的一份
  规约（trigger / context / loop / on-complete），可被人类书写，
  也可被 LLM 书写
- **按需存在的居民**：agent 可以沉睡——被一封信唤醒，处理完再睡去；
  也可以由别的 agent 从原型（prototype）梦进存在，用完回收
- **自部署**：你的机器、你的密钥、你的数据。trace 与结构化日志开箱即用

---

## 你需要什么

- Python 3.11+ 与 [uv](https://docs.astral.sh/uv/)
- Node.js 22+（Operator 控制台）
- Docker（沙箱执行环境）
- OpenAI API Key（或兼容的 LLM 服务）

---

## 快速开始

```bash
uv sync
cp .env.example .env   # 填入 OPENAI_API_KEY

uv run tlon dream                    # 终端 1：启动服务端
uv run tlon ops --username 你的名字   # 终端 2：打开 Operator（默认 mail 视图）
```

在 mail 视图按 `c` 写信：收件人填 `gu-yanxi`，写下你的需求，发送。

```
你 → gu-yanxi@tlon.internal    我想做一个个人博客，帮我规划一下
顾言蹊 → 你                     收到，规划如下……（并 CC 林知遥评估技术栈）
林知遥 → 你                     技术选型已确认，初始化完毕 [执行：npm init]
```

命令是文学命名的：`dream` = `server`，`ops` = `intrude` = `tui`，
`mirror` = `wizard`（配置向导）。

---

## 概念地图

| 概念 | 是什么 | 文档 |
|------|--------|------|
| **信件共和国** | 邮件模型：显式收件人、邮箱、线程、`all@` 列表、postmaster 退信 | [mail.md](docs/design/mail.md) |
| **亚当** | 内置 init 进程（PID 1）：boot / 唤醒 / 休眠三条策略线 | [members.md](docs/design/members.md) |
| **behavior.tlon** | 用 DSL 书写 agent 行为的规约 | [dsl-agents.md](docs/design/dsl-agents.md) |
| **Shakespeare** | 两层记忆系统（工作记忆 + 情节记忆） | [memory.md](docs/user/memory.md) |
| **Mirror** | 配置向导（创造 / 重塑 agent） | `uv run tlon mirror` |
| **花园** | Git 工作流：分支即小径分叉 | [garden.md](docs/user/garden.md) |

---

## 文档入口

| 你想... | 去这里 |
|---|---|
| **理解概念** | [用户指南](docs/user/README.md) |
| **跑起来** | [快速开始](docs/user/engineering/quickstart.md) |
| **配置 Agent** | [配置说明](docs/user/engineering/configuration.md) |
| **读懂架构** | [架构文档](docs/architecture/README.md) |
| **路线图** | [里程碑](docs/roadmap/README.md) |

---

## 许可证

Python 服务端 + TypeScript/Ink Operator，uv 管理，Docker 沙箱。
