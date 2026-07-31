# 系统架构

> 技术架构、数据流、组件关系。

---

## 架构概览

```
┌─────────────────────────────────────────────────────────────┐
│                         用户层                               │
│  ┌──────────────────────┐  ┌──────────────────────────────┐  │
│  │  Operator 控制台      │  │  mirror 配置向导              │  │
│  │  (TypeScript/Ink,     │  │  (Python, questionary/rich,  │  │
│  │   apps/operator)      │  │   直接读写 ~/.tlon/)         │  │
│  └──────────┬───────────┘  └──────────────────────────────┘  │
└─────────────┼────────────────────────────────────────────────┘
              │  WebSocket :8888 / REST :8080
┌─────────────┼────────────────────────────────────────────────┐
│              服务端（uv run tlon dream，Python/aiohttp）       │
│  ┌──────────────────────────────────────────────────────┐    │
│  │         REST API / WebSocket Server (aiohttp)        │    │
│  └──────────────────────────┬───────────────────────────┘    │
│                             │                                │
│  ┌──────────────────────────┼───────────────────────────┐    │
│  │                       核心层                          │    │
│  │  ┌───────────────┐ ┌───────────────┐ ┌────────────┐  │    │
│  │  │ AgentRegistry │ │ MailDelivery  │ │   Adam     │  │    │
│  │  │ (Agent 内核)  │ │ (邮件投递)    │ │(生命周期策略)│  │    │
│  │  └───────┬───────┘ └───────┬───────┘ └─────┬──────┘  │    │
│  │          └─────────────────┼───────────────┘          │    │
│  │                     Event Bus (asyncio)               │    │
│  │  ┌───────────────┐ ┌───────────────────────────┐      │    │
│  │  │ Memory        │ │ Scheduler / LoopDriver    │      │    │
│  │  │ (双层记忆)    │ │ (会话调度 / ReAct 循环)   │      │    │
│  │  └───────────────┘ └───────────────────────────┘      │    │
│  └──────────────────────────────────────────────────────┘    │
│  ┌──────────────────────────────────────────────────────┐    │
│  │                       适配层                          │    │
│  │   LLM Client (openai)      DockerRuntime (docker-py) │    │
│  └──────────────────────────┬───────────────────────────┘    │
└─────────────────────────────┼────────────────────────────────┘
           ═══════════════════╧══════════ 外部服务 ═════════════
              ┌────────┴────────┐      ┌────────┴────────┐
              │    LLM API      │      │  Docker Daemon  │
              └─────────────────┘      └─────────────────┘
```

---

## 核心组件

### 1. Agent 生命周期

```
┌─────────────────────────────────────┐
│           Agent 生命周期             │
├─────────────────────────────────────┤
│                                     │
│  配置 (~/.tlon/{id}/config/)        │
│      │                              │
│      ▼                              │
│  ┌──────────┐  activation=lazy      │
│  │ sleeping │ ◄───────────┐         │
│  │ (仅注册) │             │         │
│  └────┬─────┘             │         │
│       │ 来信唤醒          │ 空闲超时 │
│       │ (Adam, 数秒)     │ (drain)  │
│       ▼                   │         │
│  ┌──────────┐             │         │
│  │  active  │─────────────┘         │
│  │ (待命)   │                       │
│  └────┬─────┘                       │
│       │ 处理邮件                     │
│       ▼                             │
│  ┌───────────┐                      │
│  │ thinking  │                      │
│  └───────────┘                      │
│                                     │
└─────────────────────────────────────┘
```

**状态机**（`AgentState`，见 `src/tlon/agents/agent.py`）：

- `initializing`：启动中（加载记忆、初始化邮箱）
- `active`：运行中，等待邮件
- `thinking`：正在处理邮件（ReAct 循环中）
- `error`：遇到错误
- `shutdown`：已停止

此外注册表（AgentRegistry）会报告两个派生状态：`sleeping`（lazy agent 已注册未启动）和 `waking`（唤醒进行中）。

**Adam：三条生命周期策略线**（`src/tlon/agents/adam.py`）：

1. **boot**——服务端启动时拉起所有 `activation=always` 的 Agent；lazy agent 只注册不启动。
2. **wake（来信唤醒）**——邮件投递到 sleeping agent 的邮箱时，Adam 发出启动请求；邮件保留在邮箱里，Agent 醒来后扫读未读。
3. **drain（空闲休眠）**——lazy agent 空闲超过 `idle_timeout_seconds`（默认 600 秒）后被自动停止，释放资源。

一次性 Agent（`role=contractor`，由 `spawnAgent` 从 prototype 创建）被回收时会连同家目录一起删除；常驻 Agent（`role=resident`）的家目录永久保留。

---

### 2. 邮件系统

Tlon 没有聊天室总线。成员之间通过**邮件**通信：每封信有显式收件人，每个成员有一个邮箱。

```
┌─────────────────────────────────────────────────────┐
│              MailDelivery（邮件投递管线）             │
├─────────────────────────────────────────────────────┤
│                                                     │
│   MAIL_NEW 事件                                     │
│      │                                              │
│      ▼                                              │
│   1. 展开收件人                                      │
│      all@tlon.internal → 全部 Agent + 在线人类       │
│      │                                              │
│      ▼                                              │
│   2. 持久化                                         │
│      发件人 sent.jsonl / 收件人 inbox.jsonl          │
│      │                                              │
│      ▼                                              │
│   3. 逐收件人发布 MAIL_DELIVERED                     │
│      （Adam 据此唤醒沉睡的 Agent）                    │
│      │                                              │
│      ▼                                              │
│   4. 地址不存在 → postmaster@tlon.internal 退信       │
│      退信进入发件人邮箱，发件方可自行修正地址           │
│                                                     │
└─────────────────────────────────────────────────────┘
```

**地址规则**（`tlon.internal` 域）：

- Agent：`{agent-id}@tlon.internal`（如 `lin-zhiyao@tlon.internal`）
- 人类：`{username}@tlon.internal`（如 `alice@tlon.internal`）
- 广播：`all@tlon.internal`
- 退信：`postmaster@tlon.internal`

**邮件格式**：

```json
{
  "id": "uuid",
  "from_address": "alice@tlon.internal",
  "to": ["lin-zhiyao@tlon.internal"],
  "cc": [],
  "subject": "优化 API",
  "content": "邮件正文",
  "in_reply_to": "上游邮件 id（回复时）",
  "thread_id": "线程根邮件 id",
  "timestamp": "ISO 8601"
}
```

**邮箱存储**（每成员一个目录，JSONL 文件）：

```
~/.tlon/{agent-id}/mail/          # Agent 邮箱
~/.tlon/human-{username}/mail/    # 人类邮箱
├── inbox.jsonl                   # 收件箱
├── sent.jsonl                    # 已发送
└── inbox.cursor                  # 未读游标
```

沉睡 Agent 的邮件照常投递进邮箱，唤醒后统一扫读——邮件不会因对方沉睡而丢失。

---

### 3. 记忆系统 (双层)

```
┌─────────────────────────────────────────────────────┐
│                 Memory Architecture                 │
├─────────────────────────────────────────────────────┤
│                                                     │
│  Layer 1: Working Memory (工作记忆)                  │
│  ┌─────────────────────────────────────────────┐   │
│  │ 固定窗口: 最近 10 条交互                      │   │
│  │                                             │   │
│  │ [msg-10] ─► [msg-9] ─► ... ─► [msg-1]      │   │
│  │   丢弃 ◄────   滚动窗口   ◄────  最新       │   │
│  └─────────────────────────────────────────────┘   │
│           │                                        │
│           │ 满 10 条 or 会话空闲超时                │
│           ▼                                        │
│  Layer 2: Episodic Memory (情节记忆)                │
│  ┌─────────────────────────────────────────────┐   │
│  │ ┌─────────────────────────────────────────┐ │   │
│  │ │ Session Summary (LLM 生成摘要)           │ │   │
│  │ │ - 主题: API 优化                          │ │   │
│  │ │ - 决策: 使用 Redis                        │ │   │
│  │ │ - 状态: 已完成 80%                        │ │   │
│  │ └─────────────────────────────────────────┘ │   │
│  │                                             │   │
│  │ ┌─────────────────────────────────────────┐ │   │
│  │ │ Facts (结构化事实)                       │ │   │
│  │ │ - preference: {theme: "dark"}           │ │   │
│  │ │ - project: {name: "blog", status: "WIP"}│ │   │
│  │ └─────────────────────────────────────────┘ │   │
│  └─────────────────────────────────────────────┘   │
│                                                     │
│  持久化: ~/.tlon/{agent-id}/memory/                  │
│  - working/interactions.jsonl   (工作记忆)          │
│  - episodic/summaries.jsonl     (会话摘要)          │
│  - episodic/facts.jsonl         (结构化事实)        │
│                                                     │
└─────────────────────────────────────────────────────┘
```

**摘要触发条件**（见 `src/tlon/memory/manager.py`）：

1. Working Memory 满（`memory.max_history`，默认 10 条）
2. 会话空闲超时（默认 30 分钟）
3. Agent 主动调用 `compact` 工具（你可以写信要求它归档）

另有定期的记忆反思（reflection）：`behavior.tlon` 的 `:on-complete/:reflect` 控制，默认每 15 分钟一次。

---

### ~~4. Skill 系统~~ ❌ 已移除

> **注意**: Skill 系统已于 2026-03-17 完全移除。
>
> 原设计理念：
> ```
> Discovery → Activation → Execution
> 扫描 skills/ → 加载 SKILL.md → 执行脚本
> ```
>
> 移除原因：使用场景不清晰，复杂度高于收益。
>
> **替代方案**: 直接通过 `executeBashCommand` 工具执行任意命令。

---

### 5. 任务系统

每个 Agent 维护自己的任务清单，持久化在 `~/.tlon/{agent-id}/tasks.jsonl`。Agent 通过内置工具管理任务：`createTask` / `listTasks` / `startTask` / `pauseTask` / `completeTask` / `removeTask` / `getCurrentTask`（实现见 `src/tlon/tools/tasks/`）。项目经理类 Agent（如沈清晏）用任务跟踪分派与进度。

---

## 数据流

### 典型交互流程

```
用户在 Operator 的 mail 视图写信
  to: lin-zhiyao@tlon.internal
  content: "优化 API"
│
▼
┌─────────────────────────────────────────────────────────┐
│ 1. Operator → WebSocket                                  │
│    {"type": "message", "to": [...], "content": "..."}    │
└─────────────────────────────────────────────────────────┘
│
▼
┌─────────────────────────────────────────────────────────┐
│ 2. 服务端构造 Mail，发布 MAIL_NEW 事件                    │
└─────────────────────────────────────────────────────────┘
│
▼
┌─────────────────────────────────────────────────────────┐
│ 3. MailDelivery 投递                                     │
│    - 展开 all@、合并 To/CC 去重                          │
│    - 写入发件人 sent.jsonl                               │
│    - 写入每个收件人 inbox.jsonl                          │
│    - 地址不存在 → postmaster 退信给发件人                 │
│    - 逐收件人发布 MAIL_DELIVERED                         │
└─────────────────────────────────────────────────────────┘
│
▼
┌─────────────────────────────────────────────────────────┐
│ 4. Adam 唤醒（仅当收件人在沉睡）                          │
│    - MAIL_DELIVERED → 启动请求 (reason=wake)             │
│    - 数秒后 Agent 启动，扫读邮箱未读                      │
└─────────────────────────────────────────────────────────┘
│
▼
┌─────────────────────────────────────────────────────────┐
│ 5. Agent 处理 (lin-zhiyao)                               │
│    - trigger 判定（收件人包含我？:when 表达式？）          │
│    - 组装 LLM 上下文                                     │
│      (人格文件 + 邮箱历史 + 工作记忆 + 情节记忆)          │
│    - ReAct 循环：LLM ↔ 工具调用                          │
│      (executeBashCommand / 文件工具 / 记忆工具 / sendEmail)│
└─────────────────────────────────────────────────────────┘
│
▼
┌─────────────────────────────────────────────────────────┐
│ 6. 回信                                                 │
│    - sendEmail → MAIL_NEW → MailDelivery 投递到你的邮箱   │
│    - Operator 收到事件，mail 视图显示新邮件               │
└─────────────────────────────────────────────────────────┘
│
▼
┌─────────────────────────────────────────────────────────┐
│ 7. 会话完成                                             │
│    - 交互存入 Working Memory                            │
│    - lazy agent 空闲超时后由 Adam drain 休眠              │
└─────────────────────────────────────────────────────────┘
│
▼
你在邮箱里看到回信
```

---

## 文件系统布局

### 仓库结构

```
Tlon/
├── src/tlon/                  # Python 服务端与核心
│   ├── app/                   # 进程入口
│   │   ├── launcher/          # tlon 命令入口 (tlon=tlon.app.launcher.main:main)
│   │   ├── server/            # dream 服务端 (REST/WebSocket, api/ 与 core/)
│   │   ├── wizard/            # mirror 配置向导
│   │   └── clients/           # 客户端共用代码
│   ├── agents/                # Agent 核心
│   │   ├── agent.py           # Agent 类与状态机
│   │   ├── agent_registry.py  # 注册表（内核）
│   │   ├── adam.py            # 生命周期策略（boot/wake/drain）
│   │   ├── scheduler.py       # 会话调度
│   │   ├── loop_driver.py     # ReAct 循环
│   │   ├── spec.py            # AgentSpec（behavior.tlon 语义）
│   │   ├── edn.py / eval.py   # DSL 解析与求值
│   │   └── prototypes.py      # 行为原型库
│   ├── base/                  # 基础设施
│   │   ├── config.py          # agent.yaml 配置模型
│   │   ├── paths.py           # ~/.tlon/ 路径解析
│   │   ├── default_team.py    # 默认 6 人团队
│   │   ├── events/            # EventBus
│   │   └── tracing/           # Trace
│   ├── organization/          # 组织层
│   │   ├── mail/              # 邮件模型 (mail/mailbox/delivery)
│   │   └── messaging/         # 成员目录与人类适配器
│   ├── memory/                # 双层记忆系统
│   ├── prototypes/            # 内置行为原型 (archivist 等)
│   ├── sandbox/               # DockerRuntime
│   └── tools/                 # Agent 工具 (mail/memory/tasks/shell/...)
├── apps/operator/             # Operator 控制台 (TypeScript/Ink)
├── src/tests/                 # 测试
├── docs/                      # 文档
└── pyproject.toml
```

### 运行时数据（`~/.tlon/`，可用环境变量 `TLON_HOME` 覆盖）

```
~/.tlon/
├── {agent-id}/                # 每个 Agent 的家目录
│   ├── config/
│   │   ├── agent.yaml         # 运行参数
│   │   ├── behavior.tlon      # 行为规约 (AgentSpec)
│   │   └── IDENTITY.md / SOUL.md / RULES.md   # 人格文件
│   ├── memory/
│   │   ├── working/interactions.jsonl
│   │   └── episodic/{summaries,facts}.jsonl
│   ├── mail/                  # 邮箱 (inbox.jsonl/sent.jsonl/inbox.cursor)
│   ├── tasks.jsonl            # 任务清单
│   └── workspace/             # 工作目录
├── human-{username}/mail/     # 人类邮箱
├── prototypes/                # 行为原型库（首次启动播种）
├── prototypes-staging/        # LLM 起草、待审批的原型
└── shared/
    ├── logs/server-{date}.jsonl   # 服务端日志 (structlog JSONL)
    ├── traces/                    # Trace 数据
    └── chat_history/
```

---

## 技术栈

| 层级 | 技术 | 用途 |
|-----|------|------|
| 运行时 | Python 3.11+ | 服务端与核心 |
| 异步 | asyncio | 并发处理 |
| Web | aiohttp | REST :8080 / WebSocket :8888 |
| LLM | openai | API 客户端 |
| 容器 | docker (docker-py) | Agent 沙箱 |
| 配置 | pydantic, PyYAML, python-dotenv | agent.yaml 校验与加载 |
| 模板 | jinja2 | Prompt 渲染 |
| CLI 向导 | questionary, rich, prompt-toolkit | mirror 向导 |
| 日志 | structlog | 结构化 JSONL 日志 |
| 追踪 | opentelemetry-sdk | Trace |
| Operator 控制台 | TypeScript, Node.js ≥22, Ink 5, React 18 | apps/operator |

---

## 性能考虑

### 资源限制

| 组件 | 默认值 | 配置项 |
|-----|--------|--------|
| Agent 容器内存 | 512m | `docker.memory_limit`（agent.yaml） |
| Agent 容器 CPU | 1.0 | `docker.cpu_limit`（agent.yaml） |
| 工作记忆长度 | 10 条 | `memory.max_history`（agent.yaml） |
| 上下文邮件历史 | 20 封 | `:context/:mail-history`（behavior.tlon） |
| ReAct 循环上限 | 10 次 | `max_iterations` / `:loop/:max-iterations` |
| 入站邮件队列 | 100 封（溢出丢弃） | `message_queue_size`（agent.yaml） |
| 会话空闲取消 | 1800 秒 | `session_timeout_seconds`（agent.yaml） |
| lazy agent 空闲休眠 | 600 秒 | `idle_timeout_seconds`（agent.yaml） |
| LLM 请求超时 | 300 秒 | 运行时设置 |

### 扩展性

- **水平扩展**: 多 Agent 并行（受限于 Docker 资源）；lazy agent 按需唤醒（scale-to-zero）
- **垂直扩展**: 增加单机资源
- **瓶颈**: LLM API 调用延迟
