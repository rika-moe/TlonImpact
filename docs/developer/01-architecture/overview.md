# 架构设计文档

## 1. 总体架构

### 1.1 架构概览

Tlon 采用分层架构，分为多层：
- **入口层**: 程序入口点
- **交互层 (Adapters)**: 处理用户输入输出
- **服务端层 (Server)**: REST/WebSocket API、TlonServer 核心
- **核心层 (Core)**: 消息路由和状态管理（注：后续将改名为「组织层 Organization」）
- **Agent 层**: 智能体实现
- **子系统层**: Memory、Skills、Tasks
- **工具层**: 各类工具实现
- **🔒 Sandbox 层**: Docker 容器隔离执行环境

```
┌─────────────────────────────────────────────────────────────────────────┐
│                              入口层 (Entry Points)                       │
│  ┌───────────────┐  ┌───────────────┐  ┌───────────────────────────────┐│
│  │  __main__.py  │  │   cli/main.py │  │    cli/wizard.py              ││
│  │  模块入口     │  │   CLI主入口   │  │    配置向导(Mirror)           ││
│  └───────────────┘  └───────────────┘  └───────────────────────────────┘│
└───────────────────────────────┬─────────────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────────────┐
│                          交互层 (CLI/Adapters)                          │
│  ┌─────────────┐  ┌───────────────┐  ┌─────────────┐  ┌───────────────┐ │
│  │ cli/tui_    │  │ cli/tui.py    │  │ adapters/   │  │ clients/sdk.py│ │
│  │ simple.py   │  │ (富文本TUI)   │  │ base.py     │  │ (SDK客户端)   │ │
│  │ (简化TUI)   │  │               │  │ (预留接口)  │  │               │ │
│  └─────────────┘  └───────────────┘  └─────────────┘  └───────────────┘ │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │ WebSocket / REST API
┌───────────────────────────────▼─────────────────────────────────────────┐
│                          服务端层 (Server)                               │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │                      server/app.py (ServerApp)                    │  │
│  │  主应用：整合 REST + WebSocket + Core Server                     │  │
│  └──────────────────────────────────────────────────────────────────┘  │
│                                    │                                    │
│  ┌─────────────────────────────────┼─────────────────────────────────┐ │
│  │                                 ▼                                 │ │
│  │  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐   │ │
│  │  │ api/rest.py     │  │ api/websocket.py│  │ core/server.py  │   │ │
│  │  │ RESTServer      │  │ WebSocketServer │  │ TlonServer      │   │ │
│  │  │ (REST API)      │  │ (WS API)        │  │ (核心业务逻辑)  │   │ │
│  │  └─────────────────┘  └─────────────────┘  └─────────────────┘   │ │
│  └────────────────────────────────────────────────────────────────────┘ │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────────────┐
│                          核心层 (Core)                                   │
│                    注：后续将改名为「组织层 (Organization)」               │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────────┐ │
│  │ messaging/      │  │  protocol/      │  │  positions/             │ │
│  │ ├─ congress.py  │  │  ├─ event.py    │  │  ├─ models.py           │ │
│  │ ├─ event_bus.py │  │  ├─ message.py  │  │  ├─ registry.py         │ │
│  │                 │  │                 │  │  └─ prompt_renderer.py  │ │
│  │ Congress        │  │ Event, Message  │  │                         │ │
│  │ EventBus        │  │                 │  │ PositionRegistry        │ │
│  └─────────────────┘  └─────────────────┘  └─────────────────────────┘ │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────────────┐
│                         Agent 层 (agents/)                               │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌─────────────────┐ │
│  │ agent.py     │ │ llm_client.py│ │docker_runtime│ │ tool_executor.py│ │
│  │ Agent类      │ │ LLMClient    │ │DockerRuntime │ │ ToolExecutor    │ │
│  │ (核心智能体) │ │ (LLM调用)    │ │ (容器管理)   │ │ (工具执行器)    │ │
│  └──────┬───────┘ └──────────────┘ └──────┬───────┘ └─────────────────┘ │
│         │                                 │                             │
│  ┌──────┴───────┐ ┌─────────────────┐    │  ┌────────────────────────┐ │
│  │ agent_config │ │ agent_registry  │    │  │ prompt_builder.py      │ │
│  │ .py          │ │ AgentRegistry   │    │  │ SystemPromptBuilder    │ │
│  │ AgentConfig  │ │ (生命周期管理)  │    │  │ (系统提示构建)         │ │
│  └──────────────┘ └─────────────────┘    │  └────────────────────────┘ │
└──────────────────────────────────────────┼─────────────────────────────┘
                                           │ Docker API / Docker SDK
┌──────────────────────────────────────────▼─────────────────────────────┐
│                         子系统层 (Subsystems)                            │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────────┐ │
│  │   memory/       │  │    skills/      │  │       tasks/            │ │
│  │  ├─ manager.py  │  │  ├─ manager.py  │  │  ├─ manager.py          │ │
│  │  ├─ working_    │  │  ├─ models.py   │  │  └─ models.py           │ │
│  │  │  store.py    │  │  └─ validator.  │  │                         │ │
│  │  ├─ episodic_   │  │     py          │  │ TaskManager             │ │
│  │  │  store.py    │  │                 │  │ Task                    │ │
│  │  ├─ models.py   │  │ SkillManager    │  │ TaskStatus              │ │
│  │  └─ summarizer  │  │ Skill           │  │                         │ │
│  │     .py         │  │                 │  │                         │ │
│  │                 │  │                 │  │                         │ │
│  │ MemoryManager   │  │                 │  │                         │ │
│  │ (双层记忆系统)  │  │                 │  │                         │ │
│  └─────────────────┘  └─────────────────┘  └─────────────────────────┘ │
└────────────────────────────────────┬────────────────────────────────────┘
                                     │
┌────────────────────────────────────▼────────────────────────────────────┐
│                         工具层 (tools/)                                  │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌─────────────────────┐│
│  │ base.py     │ │ execute_    │ │ send_       │ │ remember_fact.py    ││
│  │ Tool        │ │ bash.py     │ │ message.py  │ │ search_memories.py  ││
│  │ ToolResult  │ │ ExecuteBash │ │ SendMessage │ │ forget_memory.py    ││
│  └─────────────┘ │ Tool        │ │ Tool        │ └─────────────────────┘│
│                  └──────┬──────┘ └─────────────┘ ┌─────────────────────┐│
│  ┌──────────────────────┘                        │ activate_skill.py   ││
│  │ complete_dialog.py  │ create_task.py          │ deactivate_skill.py ││
│  │ complete_task.py    │ list_tasks.py           │ load_skill_resource ││
│  │ start_task.py       │ pause_task.py           │ .py                 ││
│  │ get_current_task.py │ remove_task.py          │ generate_summary.py ││
│  └───────────────────────────────────────────────┴─────────────────────┘│
└──────────────────────────────────────┬──────────────────────────────────┘
                                       │ docker exec / container API
┌──────────────────────────────────────▼──────────────────────────────────┐
│                    🔒 Sandbox 层 (Docker 容器)                           │
│                                                                          │
│   ┌─────────────────────────────────────────────────────────────────┐   │
│   │                     Docker Container (per Agent)                 │   │
│   │  ┌───────────────────────────────────────────────────────────┐  │   │
│   │  │  Ubuntu/Debian Base Image                                 │  │   │
│   │  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────────┐   │  │   │
│   │  │  │ Bash Shell  │  │ File System │  │ Skill Scripts   │   │  │   │
│   │  │  │ (隔离执行)  │  │ (工作目录)  │  │ (技能脚本执行)  │   │  │   │
│   │  │  └─────────────┘  └─────────────┘  └─────────────────┘   │  │   │
│   │  │                                                           │  │   │
│   │  │  资源限制：                                                 │  │   │
│   │  │  - Memory: 512m (可配置)                                   │  │   │
│   │  │  - CPU: 1.0 (可配置)                                       │  │   │
│   │  │  - Network: host/bridge                                    │  │   │
│   │  │  - Working Dir: /workspace                                 │  │   │
│   │  └───────────────────────────────────────────────────────────┘  │   │
│   └─────────────────────────────────────────────────────────────────┘   │
│                                                                          │
│   Agent 1: tlon-agent-lin-zhiyao    Agent 2: tlon-agent-su-wanqing      │
│   ┌────────────────────────────┐    ┌────────────────────────────┐      │
│   │  Container: ubuntu:22.04   │    │  Container: ubuntu:22.04   │      │
│   │  - /workspace (skills)     │    │  - /workspace (skills)     │      │
│   │  - Isolated processes      │    │  - Isolated processes      │      │
│   └────────────────────────────┘    └────────────────────────────┘      │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 1.2 数据流向

```
用户输入 → CLI/TUI → WebSocket → ServerApp → TlonServer → Event Bus → ChatRoom → Agent
                                                                               │
                                                                               ▼
                                                                        ┌──────────────┐
                                                                        │   Memory     │
                                                                        │  (Context)   │
                                                                        └──────────────┘
                                                                               │
                                                                               ▼
                                                                          LLM Think
                                                                               │
                              ┌──────────────────────────┬────────────────────┼─────────────────────────┐
                              │                          │                    │                         │
                              ▼                          ▼                    ▼                         ▼
                       ┌──────────────┐          ┌──────────────┐     ┌──────────────┐          ┌──────────────┐
                       │  sendMessage │          │executeBash   │     │createTask    │          │ activateSkill│
                       │    Tool      │          │   Tool       │     │   Tool       │          │    Tool      │
                       └──────┬───────┘          └──────┬───────┘     └──────┬───────┘          └──────┬───────┘
                              │                         │                    │                         │
                              ▼                         ▼                    ▼                         ▼
                       ChatRoom广播               Docker API            Task Manager            加载SKILL.md
                              │                         │                    │                         │
                              │                         ▼                    │                         │
                              │              ┌─────────────────────┐         │                         │
                              │              │   🔒 Sandbox 层     │         │                         │
                              │              │  Docker Container   │         │                         │
                              │              │  - Bash 执行        │         │                         │
                              │              │  - Skill 脚本       │         │                         │
                              │              └─────────────────────┘         │                         │
                              │                         │                    │                         │
                              └─────────────────────────┼────────────────────┴─────────────────────────┘
                                                        │
                                                        ▼
                                                   返回结果
                                                        │
                                                        ▼
用户输出 ← CLI/TUI ← WebSocket ← ServerApp ← ChatRoom ← Agent
```

### 1.3 模块职责

| 模块 | 层级 | 职责 | 详细设计 |
|------|------|------|---------|
| **CLI/TUI** | 交互层 | 命令行交互：接收输入、格式化输出 | - |
| **Server** | 服务端层 | REST/WebSocket API、TlonServer、Session管理 | - |
| **Event Bus** | 组织层(原核心层) | 异步消息总线：订阅发布模式、事件分发 | - |
| **ChatRoom** | 组织层(原核心层) | 聊天室状态：参与者管理、消息历史持久化 | - |
| **Message** | 组织层(原核心层) | 消息协议：统一格式、OpenAI格式转换 | - |
| **Position** | 组织层(原核心层) | 岗位管理：职责定义、Prompt 模板 | - |
| **Agent** | Agent层 | 智能体核心：消息处理、Context构建、LLM调用 | - |
| **LLM Client** | Agent层 | LLM通信：API调用、重试机制、流式响应 | - |
| **Tool Executor** | Agent层 | 工具执行：注册表、参数校验、执行分发 | - |
| **Docker Runtime** | Agent层 | 容器管理：生命周期、命令执行、文件隔离 | - |
| **Skill Manager** | 子系统层 | 技能系统：发现/激活/执行 (Agent Skills标准) | - |
| **Memory Manager** | 子系统层 | 记忆系统：双层记忆、Context注入、摘要生成 | [memory.md](./memory.md) |
| **Task Manager** | 子系统层 | 任务管理：任务创建、状态流转、暂停恢复 | [../05-tasks/README.md](../05-tasks/README.md) |
| **Tools** | 工具层 | 各类工具实现：Bash执行、消息发送、技能加载等 | - |
| **🔒 Sandbox** | Sandbox层 | Docker容器隔离：命令执行环境、资源限制、安全隔离 | - |

---

## 2. 模块详细设计

### 2.1 交互层 (Adapters)

**职责**: 适配不同输入输出渠道，统一转换为内部消息格式

**设计原则**:
- 适配器只负责输入接收、格式转换、输出发送
- 核心逻辑与交互渠道解耦
- 通过 `AdapterInterface` 抽象，支持多种适配器并存

**当前实现**:
- `CLIAdapter`: 命令行交互

**未来扩展**:
- `TelegramAdapter`: Telegram Bot
- `WebAdapter`: WebSocket/REST API

---

### 2.2 组织层 (Organization) - 原核心层 (Core)

> **注**：该层原名「核心层 (Core)」，现计划改名为「组织层 (Organization)」，以更好地反映其职责：管理多 Agent 协作的组织基础设施。

#### 2.2.1 Event Bus

**职责**: 中央异步事件总线，负责模块间消息路由

**关键特性**:
- 发布/订阅模式
- 异步事件处理
- 支持多订阅者

**事件类型**:
- `MESSAGE_NEW`: 新消息
- `MESSAGE_MENTION`: 提及消息
- `AGENT_THINKING`: Agent 思考中（调试用）
- `TOOL_EXECUTE`: 工具执行
- `TOOL_RESULT`: 工具结果

#### 2.2.2 ChatRoom

**职责**: 维护聊天室状态和消息历史

**核心功能**:
- 参与者管理 (join/leave)
- 消息历史 (send_message/get_history)
- 持久化存储 (JSONL 格式)

**数据持久化**:
- 路径: `data/chat_history/{chatroom_id}.jsonl`
- 格式: 每行一条 JSON 消息
- 启动时加载，实时追加

#### 2.2.3 Message Protocol

**职责**: 定义统一消息格式，提供格式转换能力

**Message 核心字段**:
- `id`: 消息唯一标识
- `sender_id/sender_name/sender_type`: 发送者信息
- `content`: 消息内容
- `mentions`: @提及列表
- `timestamp`: 时间戳

**转换能力**:
- `to_openai_message()`: 转换为 OpenAI API 格式
- `format_for_display()`: 格式化为显示格式

---

### 2.3 Agent 层

#### 2.3.1 Agent

**职责**: 智能体核心，处理消息、决策、执行工具

**核心流程**:
```
1. 接收消息 (on_message)
2. 判断是否响应 (_should_respond)
   - 检查 @mention
   - 检查 respond_to_all 配置
3. 构建 Context (_build_context)
   - System Prompt (角色、技能、工具)
   - Memory Context (摘要、事实)
   - ChatRoom History (最近消息)
   - Current Message
4. LLM 思考 (think)
   - 调用 LLM，传入 Context 和 Tools
   - 支持多轮工具调用
5. 执行工具 (execute_tool)
   - 根据 LLM 决定调用相应工具
6. 发送响应 (send_message)
7. 保存记忆 (save_to_memory)
```

**响应规则**:
- 只有被 @ 的 Agent 才会响应
- 支持 `respond_to_all` 模式（单 Agent 场景）

#### 2.3.2 LLM Client

**职责**: LLM API 客户端，封装调用逻辑

**功能**:
- OpenAI 兼容 API 调用
- 指数退避重试机制
- 工具调用 (Tool Use) 支持
- 流式响应 (未来)

#### 2.3.3 Tool Executor

**职责**: 工具注册和执行

**内置工具**:
- `sendMessage`: 发送消息到聊天室
- `executeBashCommand`: 在 Docker 容器中执行命令

**Skill 工具**:
- `activateSkill`: 激活技能
- `deactivateSkill`: 关闭技能
- `loadSkillResource`: 加载技能资源

**Memory 工具**:
- `rememberFact`: 记录重要事实
- `searchMemories`: 搜索历史记忆
- `generateSummary`: 生成会话摘要
- `forget`: 遗忘记忆

#### 2.3.4 Docker Runtime

**职责**: 管理 Agent 的隔离执行环境（🔒 Sandbox 层）

**特性**:
- 每个 Agent 独立 Docker 容器（Sandbox 隔离）
- 容器长期运行（应用重启后保留）
- 支持任意 bash 命令执行（通过 `executeBashCommand` 工具）
- Skills 目录挂载到容器内的 `/workspace`
- 资源限制：内存、CPU、网络隔离

**调用链**:
```
Agent → ToolExecutor → ExecuteBashTool → DockerRuntime → Docker SDK → docker exec → Container
```

#### 2.3.5 Skill Manager

**职责**: 实现 [Agent Skills](https://github.com/agentskills/agentskills) 标准

**渐进式披露机制**:
1. **Discovery**: 启动时扫描 `skills/` 目录，加载 `name` 和 `description`
2. **Activation**: LLM 调用 `activateSkill` 时加载完整 `SKILL.md`
3. **Execution**: 执行 `scripts/` 或读取 `references/`

**目录结构**:
```
skills/{skill-name}/
├── SKILL.md          # 技能定义（必需）
├── scripts/          # 可执行脚本（可选）
└── references/       # 参考文档（可选）
```

#### 2.3.6 Memory Manager

**职责**: 长时记忆管理，支持跨会话记忆

**双层架构**:
- **Working Memory**: 当前会话原始交互（含 tool_results），固定 10 条
- **Episodic Memory**: 跨会话摘要和关键事实

**Context 注入**:
- 按优先级构建记忆块
- 控制注入长度，避免超出 Token 限制

**详细设计**: 参见 [memory.md](./memory.md)

---

## 3. 关键流程

### 3.1 消息处理流程

```
┌─────────┐   ┌─────────┐   ┌─────────┐   ┌─────────┐   ┌─────────┐
│  User   │──▶│ Adapter │──▶│EventBus │──▶│ChatRoom │──▶│  Agent  │
└─────────┘   └─────────┘   └─────────┘   └─────────┘   └────┬────┘
                                                              │
                    ┌─────────────────────────────────────────┤
                    │                                         │
                    ▼                                         ▼
             ┌────────────┐                           ┌────────────┐
             │  Memory    │                           │    LLM     │
             │  (Context) │◀─────────────────────────▶│   Think    │
             └────────────┘                           └─────┬──────┘
                                                            │
                              ┌─────────────────────────────┼─────────┐
                              │                             │         │
                              ▼                             ▼         ▼
                       ┌────────────┐                ┌──────────┐ ┌──────────┐
                       │ChatRoom    │◀───────────────│   Tool   │ │  Tool    │
                       │Broadcast   │                │  Result  │ │ Execution│
                       └────────────┘                └──────────┘ └──────────┘
```

### 3.2 Skill 生命周期

```
App 启动
    │
    ▼
┌────────────────┐
│   Discovery    │ 扫描 skills/ 目录，解析 SKILL.md frontmatter
│   (metadata)   │ 只加载 name + description 到 Agent
└────────────────┘
    │
    │ 用户消息提到 Skill 相关任务
    ▼
┌────────────────┐
│  Activation    │ LLM 调用 activateSkill 工具
│  (SKILL.md)    │ 加载完整 SKILL.md 内容到 Context
└────────────────┘
    │
    │ 根据 SKILL.md 指令
    ▼
┌────────────────┐
│   Execution    │ 执行 scripts/ 或读取 references/
│   (scripts)    │ 使用 executeBashCommand 工具
└────────────────┘
    │
    ▼
Skill 使用完成，可选调用 deactivateSkill 释放资源
```

### 3.3 Memory 生命周期

```
Agent 启动
    │
    ▼
┌────────────────┐
│     Load       │ 从文件加载 Episodic Memory (摘要+事实)
│   Episodic     │ Working Memory 为空
└────────────────┘
    │
    │ 开始消息处理循环
    ▼
┌────────────────┐
│   Interaction  │ 每次交互完成后存入 Working Memory
│   (Working)    │
└────────────────┘
    │
    │ Working Memory 满 10 条 / 空闲 30 分钟 / 主动触发
    ▼
┌────────────────┐
│  Summarization │ LLM 生成会话摘要 + 提取关键事实
│   (LLM Call)   │
└────────────────┘
    │
    ▼
┌────────────────┐
│    Store       │ 摘要和事实存入 Episodic Memory
│   Episodic     │ Working Memory 清空
└────────────────┘
    │
    │ Agent Shutdown
    ▼
┌────────────────┐
│  Force Summary │ 强制生成最终摘要
│   & Persist    │ 保存所有记忆到文件
└────────────────┘
```

详细设计参见 [memory.md](./memory.md)

---

## 4. 数据存储

### 4.1 存储结构

```
data/
├── chat_history/              # 聊天室消息历史
│   └── {chatroom_id}.jsonl
├── agents/                    # Agent 数据
│   └── {agent_id}/
│       └── memory/            # Agent 记忆
│           ├── config.json
│           ├── working/
│           │   └── interactions.jsonl
│           └── episodic/
│               ├── summaries.jsonl
│               └── facts.jsonl
└── skills/                    # Skill 定义
    └── {skill-name}/
        ├── SKILL.md
        ├── scripts/
        └── references/
```

### 4.2 持久化策略

| 数据 | 存储格式 | 触发时机 |
|------|---------|---------|
| ChatRoom 历史 | JSONL (追加) | 每条消息实时写入 |
| Working Memory | JSONL | 每次交互后追加 |
| Episodic Memory | JSONL | 生成摘要时写入 |
| Agent 配置 | YAML | 手动编辑 |

---

## 5. 配置设计

### 5.1 Agent 配置 (config/agents.yaml)

```yaml
agents:
  - id: "dev-001"
    name: "开发-A"
    role:
      name: "developer"
      description: "负责代码编写和 Bug 修复"
      personality: "技术能力强、注重细节"
    llm:
      model_name: "gpt-4"
      temperature: 0.7
    docker:
      image: "ubuntu:22.04"
      memory_limit: "512m"
    skills: ["file-operations", "git-operations"]
    memory:
      max_history: 10
      summary_window: 10
```

### 5.2 环境变量 (.env)

```bash
# LLM API
OPENAI_API_KEY=sk-xxx
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL_NAME=gpt-4

# 应用设置
LOG_LEVEL=INFO
DATA_DIR=./data
```

---

## 6. 扩展性设计

### 6.1 添加新 Adapter

1. 实现 `AdapterInterface`
2. 处理输入转换为 `UserInput`
3. 实现 `send_message()` 格式化输出

### 6.2 添加新 Tool

1. 继承 `Tool` 基类
2. 实现 `execute()` 方法
3. 在 Agent 初始化时注册

### 6.3 添加新 Skill

1. 创建 `skills/{skill-name}/` 目录
2. 编写 `SKILL.md` (YAML frontmatter + Markdown)
3. 添加 `scripts/` 脚本（可选）
4. 在 Agent 配置中启用

---

## 7. 相关文档

| 文档 | 内容 |
|------|------|
| [spec.md](./spec.md) | 产品规格和需求 |
| [roadmap.md](./roadmap.md) | 开发计划和里程碑 |
| [memory.md](./memory.md) | Memory 系统详细设计 |
| [multi-agent.md](./multi-agent.md) | 多 Agent 协作系统设计 |
| [AGENTS.md](./AGENTS.md) | 开发指南和代码规范 |
| [ui-architecture.md](./ui-architecture.md) | 多界面架构设计 |
| [tui-design.md](./tui-design.md) | TUI 客户端设计 |
| [web-design.md](./web-design.md) | Web 客户端设计 |
| [telegram-design.md](./telegram-design.md) | Telegram Bot 设计 |
| [wizard-design.md](./wizard-design.md) | 配置向导设计 |

---

## 8. 架构演进

### 当前状态
- **入口层**: ✅ 模块入口、CLI 命令分发
- **交互层**: ✅ TUI (极简流式控制台)
- **服务端层**: ✅ REST API、WebSocket、TlonServer
- **组织层(原核心层)**: ✅ EventBus、Congress、PositionRegistry
- **Agent 层**: ✅ Agent、LLMClient、DockerRuntime、ToolExecutor
- **子系统层**: ✅ Skills、Memory、Tasks
- **工具层**: ✅ 完整工具集
- **🔒 Sandbox 层**: ✅ Docker 容器隔离执行

### 新架构规划（多界面支持）
- **Server-Client 架构**: 核心服务与界面分离 ✅
- **TUI 客户端**: 富文本终端界面（三种模式）✅
- **Web 界面**: React + WebSocket 实时通信 🔄 预留接口
- **Telegram Bot**: 远程交互支持 🔄 预留接口
- **Wizard**: 交互式配置工具 ✅

### 架构层次（已确定）

```
┌─────────────────────────────────────────────────────────────┐
│ 1. 入口层 (Entry)        │ __main__.py, cli/main.py         │ ✅
├─────────────────────────────────────────────────────────────┤
│ 2. 交互层 (Adapters)     │ CLI, TUI, SDK                    │ ✅
├─────────────────────────────────────────────────────────────┤
│ 3. 服务端层 (Server)     │ REST API, WebSocket, TlonServer  │ ✅
├─────────────────────────────────────────────────────────────┤
│ 4. 组织层 (Organization) │ EventBus, Congress, Positions    │ ✅
│    (原核心层 Core)       │ 注：即将改名                      │ 
├─────────────────────────────────────────────────────────────┤
│ 5. Agent 层              │ Agent, LLMClient, DockerRuntime  │ ✅
├─────────────────────────────────────────────────────────────┤
│ 6. 子系统层 (Subsystems) │ Skills, Memory, Tasks            │ ✅
├─────────────────────────────────────────────────────────────┤
│ 7. 工具层 (Tools)        │ Tools 集合                        │ ✅
├─────────────────────────────────────────────────────────────┤
│ 8. 🔒 Sandbox 层         │ Docker Container (per Agent)     │ ✅
└─────────────────────────────────────────────────────────────┘
```

### 未来规划
- 向量检索 (Semantic Search)
- 移动端适配优化
- 插件系统

### 多 Agent 架构要点

**响应规则:**
- 客服：主动响应用户输入 + 被@时响应
- 其他角色（项目经理/开发/运维/产品经理）：仅被@时响应
- 大幅减少无效 Token 消耗

**启动策略:**
- 客服常驻启动（应用启动时立即初始化）
- 其他角色延迟启动（首次被@时创建实例）
- 空闲回收机制（可选）：长时间无活动停止 Docker

**并发模型:**
- Agent 间：完全并行，各自独立 Docker 容器
- Agent 内：消息队列串行处理，保证顺序
- fire-and-forget 风格，不阻塞

**循环@防护:**
- 不设计复杂状态跟踪机制
- 依赖 System Prompt 中的自我节制规则
- 结合 max_iterations 限制作为后备

**团队协作:**
- 所有 Agent 的 Prompt 注入团队信息表
- 包含：同事名字、职责、何时该@谁
- 让 Agent 能够准确地进行任务分派
