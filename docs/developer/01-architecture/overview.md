# 架构设计文档

> **版本**: v2.0  
> **更新日期**: 2026-03-13  
> **状态**: 已同步代码结构

---

## 1. 总体架构

### 1.1 架构概览

Tlon 采用分层架构，分为多层：
- **入口层**: 程序入口点
- **应用层 (App)**: 整合交互层和服务端层
- **基础层 (Base)**: 配置、事件、日志等基础设施
- **组织层 (Organization)**: 消息路由和状态管理
- **Agent 层**: 智能体实现
- **Sandbox 层**: Docker 容器隔离执行环境
- **子系统层**: Memory、Skills、Tasks、Tools

```
┌─────────────────────────────────────────────────────────────────────────┐
│                              入口层 (Entry Points)                       │
│  ┌───────────────┐  ┌───────────────┐  ┌───────────────────────────────┐│
│  │  __main__.py  │  │ cli/main.py   │  │    wizard.py                  ││
│  │  模块入口     │  │   CLI主入口   │  │    配置向导(Mirror)           ││
│  └───────────────┘  └───────────────┘  └───────────────────────────────┘│
└───────────────────────────────┬─────────────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────────────┐
│                          应用层 (App)                                    │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌───────────┐ │  │
│  │  │   cli/      │  │   server/   │  │  clients/   │  │  wizard/  │ │  │
│  │  │  命令行入口  │  │ REST/WebSocket│  │ SDK/TUI    │  │  配置向导  │ │  │
│  │  └─────────────┘  └─────────────┘  └─────────────┘  └───────────┘ │  │
│  └───────────────────────────────────────────────────────────────────┘  │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │ WebSocket / REST API
┌───────────────────────────────▼─────────────────────────────────────────┐
│                          基础层 (Base)                                   │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────────┐  │
│  │   config/       │  │    events/      │  │   logger.py/settings    │  │
│  │  ├─ models.py   │  │  ├─ event.py    │  │                         │  │
│  │  ├─ loader.py   │  │  ├─ event_bus   │  │   日志配置/全局设置       │  │
│  │  ├─ saver.py    │  │     .py         │  │                         │  │
│  │  └─ ...         │  │                 │  │                         │  │
│  │                 │  │                 │  │                         │  │
│  │ ConfigLoader    │  │ Event/EventBus  │  │  get_logger/settings    │  │
│  └─────────────────┘  └─────────────────┘  └─────────────────────────┘  │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────────────┐
│                          组织层 (Organization)                           │
│                    （管理多 Agent 协作的基础设施）                         │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────────┐  │
│  │   messaging/    │  │    positions/   │  │   (EventBus 来自 base)  │  │
│  │  ├─ congress.py │  │  ├─ models.py   │  │                         │  │
│  │  ├─ message.py  │  │  ├─ registry.py │  │                         │  │
│  │  └─ __init__.py │  │  └─ ...         │  │                         │  │
│  │                 │  │                 │  │                         │  │
│  │ Congress        │  │ PositionRegistry│  │                         │  │
│  │ Message         │  │                 │  │                         │  │
│  └─────────────────┘  └─────────────────┘  └─────────────────────────┘  │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────────────┐
│                         Agent 层 (agents/)                               │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌─────────────────┐  │
│  │ agent.py     │ │ llm_client.py│ │tool_executor │ │prompt_builder   │  │
│  │ Agent类      │ │ LLMClient    │ │   .py        │ │   .py           │  │
│  │ (核心智能体) │ │ (LLM调用)    │ │ToolExecutor  │ │SystemPrompt     │  │
│  └──────┬───────┘ └──────────────┘ └──────┬───────┘ └─────────────────┘  │
│         │                                 │                                │
│  ┌──────┴───────┐ ┌─────────────────┐     │  ┌────────────────────────┐  │
│  │ agent_       │ │ agent_registry  │     │  │                        │  │
│  │ registry.py  │ │ (生命周期管理)   │     │  │                        │  │
│  └──────────────┘ └─────────────────┘     │  │                        │  │
└───────────────────────────────────────────┼──────────────────────────────┘
                                            │ Docker API
┌───────────────────────────────────────────▼──────────────────────────────┐
│                    🔒 Sandbox 层 (sandbox/)                               │
│  ┌─────────────────────────────────────────────────────────────────────┐  │
│  │                    docker_runtime.py                                │  │
│  │                    DockerRuntime                                    │  │
│  │  - 每个 Agent 独立的隔离执行环境                                     │  │
│  │  - executeBashCommand 在此层执行                                     │  │
│  └─────────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────┬─────────────────────────────────────┘
                                       │
┌──────────────────────────────────────▼─────────────────────────────────────┐
│                         子系统层 (subsystem/)                               │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────────────┐ │
│  │   memory/       │  │    skills/      │  │          tools/             │ │
│  │  ├─ manager.py  │  │  ├─ manager.py  │  │  ┌───────────────────────┐  │ │
│  │  ├─ working_    │  │  ├─ models.py   │  │  │ base.py (工具基类)     │  │ │
│  │  │  store.py    │  │  └─ validator.  │  │  ├─ complete_dialog/    │  │ │
│  │  ├─ episodic_   │  │     py          │  │  ├─ memory/             │  │ │
│  │  │  store.py    │  │                 │  │  ├─ shell/              │  │ │
│  │  ├─ models.py   │  │ SkillManager    │  │  ├─ skills/             │  │ │
│  │  └─ summarizer  │  │ Skill           │  │  └─ tasks/              │  │ │
│  │     .py         │  │                 │  │                         │  │ │
│  │                 │  │                 │  │  按功能分组的工具集        │  │ │
│  │ MemoryManager   │  │                 │  │                         │  │ │
│  │ (双层记忆系统)   │  │                 │  │                         │  │ │
│  └─────────────────┘  └─────────────────┘  └─────────────────────────────┘ │
└────────────────────────────────────────────────────────────────────────────┘
```

### 1.2 目录结构（实际代码）

```
src/tlon/
│
├── __init__.py                     # 包入口
│
├── agents/                         # 【Agent 层】
│   ├── agent.py                    #   Agent 核心类
│   ├── agent_registry.py           #   Agent 生命周期管理
│   ├── llm_client.py               #   LLM 客户端
│   ├── prompt_builder.py           #   系统提示构建
│   └── tool_executor.py            #   工具执行器
│
├── app/                            # 【应用层】
│   ├── cli/                        #   CLI 入口
│   │   ├── main.py
│   │   └── __init__.py
│   ├── clients/                    #   客户端实现
│   │   ├── tui/                    #     TUI 客户端 (tui_simple.py)
│   │   ├── sdk.py                  #     SDK 客户端
│   │   ├── webui/                  #     WebUI (预留)
│   │   └── imui/                   #     IMUI (预留)
│   ├── server/                     #   服务端
│   │   ├── app.py                  #     ServerApp 主应用
│   │   ├── api/                    #     REST + WebSocket API
│   │   │   ├── rest.py
│   │   │   └── websocket.py
│   │   └── core/                   #     TlonServer 核心
│   │       └── server.py
│   └── wizard/                     #   配置向导 (Mirror)
│       ├── core.py
│       ├── menus/
│       └── ...
│
├── base/                           # 【基础层】
│   ├── config/                     #   配置管理
│   │   ├── config.py
│   │   ├── loader.py
│   │   ├── models.py
│   │   ├── prompts.py
│   │   ├── saver.py
│   │   └── validator.py
│   ├── events/                     #   事件系统
│   │   ├── event.py                #     Event 类 (原 protocol/event.py)
│   │   └── event_bus.py            #     EventBus (原 messaging/event_bus.py)
│   ├── logger.py                   #   日志配置
│   └── settings.py                 #   全局设置
│
├── organization/                   # 【组织层】
│   ├── messaging/                  #   消息系统
│   │   ├── congress.py             #     Congress (代表大会)
│   │   ├── message.py              #     Message 类 (原 protocol/message.py)
│   │   └── __init__.py
│   └── positions/                  #   岗位系统
│       ├── models.py
│       ├── registry.py
│       └── prompt_renderer.py
│
├── sandbox/                        # 【Sandbox 层】
│   └── docker_runtime.py           #   Docker 容器管理
│                                   #   (从 agents/ 移出)
│
└── subsystem/                      # 【子系统层】
    ├── memory/                     #   记忆系统 (Shakespeare)
    │   ├── manager.py
    │   ├── working_store.py
    │   ├── episodic_store.py
    │   ├── summarizer.py
    │   └── models.py
    ├── skills/                     #   技能系统 (Library of Babel)
    │   ├── manager.py
    │   ├── models.py
    │   └── validator.py
    └── tools/                      #   工具层
        ├── base.py                 #     工具基类
        ├── complete_dialog/        #     对话完成工具
        │   └── complete_dialog.py
        ├── memory/                 #     记忆工具
        │   ├── remember_fact.py
        │   ├── search_memories.py
        │   ├── forget_memory.py
        │   └── generate_summary.py
        ├── shell/                  #     Shell 工具
        │   └── execute_bash.py
        ├── skills/                 #     技能工具
        │   ├── activate_skill.py
        │   ├── deactivate_skill.py
        │   └── load_skill_resource.py
        └── tasks/                  #     任务工具 + 任务管理
            ├── create_task.py
            ├── complete_task.py
            ├── start_task.py
            ├── pause_task.py
            ├── remove_task.py
            ├── list_tasks.py
            ├── get_current_task.py
            ├── manager.py
            └── models.py
```

### 1.3 数据流向

```
用户输入 → CLI → ServerApp → TlonServer → Event Bus → Congress → Agent
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
                              ┌──────────────────────────┬───────┼─────────────────────────┐
                              │                          │       │                         │
                              ▼                          ▼       ▼                         ▼
                       ┌──────────────┐          ┌──────────────┐     ┌──────────────┐ ┌──────────────┐
                       │ completeDialog│          │executeBash   │     │createTask    │ │activateSkill │
                       │    Tool      │          │   Tool       │     │   Tool       │ │   Tool       │
                       └──────┬───────┘          └──────┬───────┘     └──────┬───────┘ └──────┬───────┘
                              │                         │                    │                │
                              ▼                         ▼                    │                │
                       ChatRoom广播               Docker API               │                │
                              │                         │                    │                │
                              │              ┌──────────▼──────────┐         │                │
                              │              │   🔒 Sandbox 层     │         │                │
                              │              │  Docker Container   │         │                │
                              │              └──────────┬──────────┘         │                │
                              │                         │                    │                │
                              └─────────────────────────┼────────────────────┴────────────────┘
                                                        │
                                                        ▼
                                                   返回结果
                                                        │
                                                        ▼
用户输出 ← CLI ← ServerApp ← Event Bus ← Congress ← Agent
```

### 1.4 模块职责

| 模块 | 层级 | 职责 | 路径 |
|------|------|------|------|
| **CLI** | 应用层 | 命令行交互：接收输入、格式化输出 | `app/cli/` |
| **Server** | 应用层 | REST/WebSocket API、TlonServer、Session管理 | `app/server/` |
| **Event Bus** | 基础层 | 异步消息总线：订阅发布模式、事件分发 | `base/events/` |
| **Config** | 基础层 | 配置管理：加载、保存、验证配置 | `base/config/` |
| **Congress** | 组织层 | 聊天室状态：参与者管理、消息历史持久化 | `organization/messaging/` |
| **Message** | 组织层 | 消息协议：统一格式、OpenAI格式转换 | `organization/messaging/` |
| **Position** | 组织层 | 岗位管理：职责定义、Prompt 模板 | `organization/positions/` |
| **Agent** | Agent层 | 智能体核心：消息处理、Context构建、LLM调用 | `agents/` |
| **LLM Client** | Agent层 | LLM通信：API调用、重试机制、流式响应 | `agents/llm_client.py` |
| **Tool Executor** | Agent层 | 工具执行：注册表、参数校验、执行分发 | `agents/tool_executor.py` |
| **Docker Runtime** | Sandbox层 | 容器管理：生命周期、命令执行、文件隔离 | `sandbox/docker_runtime.py` |
| **Skill Manager** | 子系统层 | 技能系统：发现/激活/执行 (Agent Skills标准) | `subsystem/skills/` |
| **Memory Manager** | 子系统层 | 记忆系统：双层记忆、Context注入、摘要生成 | `subsystem/memory/` |
| **Task Manager** | 子系统层 | 任务管理：任务创建、状态流转、暂停恢复 | `subsystem/tools/tasks/` |
| **Tools** | 子系统层 | 各类工具实现：按功能分组 | `subsystem/tools/` |

---

## 2. 模块详细设计

### 2.1 应用层 (App)

**职责**: 整合交互层和服务端层，提供统一的应用入口

**设计原则**:
- 将 CLI、Server、Clients、Wizard 统一组织在 `app/` 下
- 清晰分离底层逻辑与应用接口

**当前实现**:
- `app/cli/`: 命令行入口
- `app/server/`: REST/WebSocket API、TlonServer
- `app/clients/`: SDK、TUI、WebUI(预留)、IMUI(预留)
- `app/wizard/`: 配置向导

### 2.2 基础层 (Base)

**职责**: 提供基础设施服务（配置、事件、日志）

#### 2.2.1 Config 模块

**职责**: 统一的配置管理

**文件**:
- `config/models.py`: 数据模型 (AgentConfig, PositionConfig 等)
- `config/loader.py`: 配置加载
- `config/saver.py`: 配置保存
- `config/validator.py`: 配置验证
- `config/prompts.py`: Prompt 模板管理

#### 2.2.2 Events 模块

**职责**: 事件系统（原 `protocol/event.py` + `messaging/event_bus.py`）

**文件**:
- `events/event.py`: Event、EventType 定义
- `events/event_bus.py`: EventBus 实现

**迁移说明**:
- `Event` 类从 `protocol/event.py` 移至 `base/events/event.py`
- `EventBus` 从 `messaging/event_bus.py` 移至 `base/events/event_bus.py`

### 2.3 组织层 (Organization)

**职责**: 管理多 Agent 协作的组织基础设施

> **注**：原「核心层 (Core)」已改名为「组织层 (Organization)」

#### 2.3.1 Messaging 模块

**职责**: 消息系统和聊天室

**文件**:
- `messaging/congress.py`: Congress (代表大会) 聊天室
- `messaging/message.py`: Message 类 (原 `protocol/message.py`)

**迁移说明**:
- `Message` 类从 `protocol/message.py` 移至 `organization/messaging/message.py`

#### 2.3.2 Positions 模块

**职责**: 岗位管理

**文件**:
- `positions/models.py`: Position 模型
- `positions/registry.py`: PositionRegistry
- `positions/prompt_renderer.py`: Prompt 渲染

### 2.4 Agent 层

#### 2.4.1 Agent

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

#### 2.4.2 LLM Client

**职责**: LLM API 客户端，封装调用逻辑

**功能**:
- OpenAI 兼容 API 调用
- 指数退避重试机制
- 工具调用 (Tool Use) 支持

#### 2.4.3 Tool Executor

**职责**: 工具注册和执行

**内置工具**:
- `completeDialog`: 完成对话并发送最终回复
- `executeBashCommand`: 在 Docker 容器中执行命令

#### 2.4.4 Agent Registry

**职责**: 管理多个 Agent 的生命周期

**功能**:
- 注册 Agent 配置
- 常驻 vs 延迟启动
- 激活/停用 Agent

### 2.5 Sandbox 层

**职责**: Docker 容器隔离执行环境

**文件**:
- `sandbox/docker_runtime.py`: DockerRuntime 类

**迁移说明**:
- `docker_runtime.py` 从 `agents/docker_runtime.py` 移至 `sandbox/docker_runtime.py`

**特性**:
- 每个 Agent 独立 Docker 容器
- 容器长期运行（应用重启后保留）
- 支持任意 bash 命令执行
- Skills 目录挂载到容器内的 `/workspace`
- 资源限制：内存、CPU、网络隔离

**调用链**:
```
Agent → ToolExecutor → ExecuteBashTool → DockerRuntime → Docker SDK → docker exec → Container
```

### 2.6 子系统层 (Subsystem)

#### 2.6.1 Skill Manager

**职责**: 实现 [Agent Skills](https://github.com/agentskills/agentskills) 标准

**文件**:
- `subsystem/skills/manager.py`: SkillManager
- `subsystem/skills/models.py`: Skill 模型
- `subsystem/skills/validator.py`: Skill 验证

**渐进式披露机制**:
1. **Discovery**: 启动时扫描 `skills/` 目录，加载 `name` 和 `description`
2. **Activation**: LLM 调用 `activateSkill` 时加载完整 `SKILL.md`
3. **Execution**: 执行 `scripts/` 或读取 `references/`

#### 2.6.2 Memory Manager

**职责**: 长时记忆管理，支持跨会话记忆

**文件**:
- `subsystem/memory/manager.py`: MemoryManager
- `subsystem/memory/working_store.py`: 工作记忆
- `subsystem/memory/episodic_store.py`: 情景记忆
- `subsystem/memory/summarizer.py`: 摘要生成

**双层架构**:
- **Working Memory**: 当前会话原始交互（含 tool_results），固定 10 条
- **Episodic Memory**: 跨会话摘要和关键事实

**详细设计**: 参见 [memory.md](./memory.md)

#### 2.6.3 Tools

**职责**: 各类工具实现

**文件组织**（按功能分组）:
```
subsystem/tools/
├── base.py                     # 工具基类
├── complete_dialog/            # 对话完成工具
│   └── complete_dialog.py
├── memory/                     # 记忆工具
│   ├── remember_fact.py
│   ├── search_memories.py
│   ├── forget_memory.py
│   └── generate_summary.py
├── shell/                      # Shell 工具
│   └── execute_bash.py
├── skills/                     # 技能工具
│   ├── activate_skill.py
│   ├── deactivate_skill.py
│   └── load_skill_resource.py
└── tasks/                      # 任务工具 + 任务管理
    ├── create_task.py
    ├── complete_task.py
    ├── start_task.py
    ├── pause_task.py
    ├── remove_task.py
    ├── list_tasks.py
    ├── get_current_task.py
    ├── manager.py
    └── models.py
```

---

## 3. 关键流程

### 3.1 消息处理流程

```
┌─────────┐   ┌─────────┐   ┌─────────┐   ┌─────────┐   ┌─────────┐
│  User   │──▶│   CLI   │──▶│  Server │──▶│EventBus │──▶│ Congress│
└─────────┘   └─────────┘   └─────────┘   └─────────┘   └────┬────┘
                                                             │
                                                             ▼
                                                        ┌─────────┐
                                                        │  Agent  │
                                                        └────┬────┘
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

### 5.1 Agent 配置 (config/agents/{agent_id}.yaml)

```yaml
agent:
  id: "lin-zhiyao"
  name: "林知遥"
  personality: "技术能力强、注重细节"
  position_id: "developer"
  skills:
    - "git-operations"
    - "file-operations"
  llm:
    model_name: "kimi-k2.5"
    temperature: 0.7
  docker:
    image: "ubuntu:22.04"
    memory_limit: "512m"
```

### 5.2 Position 配置 (config/positions/{position_id}.yaml)

```yaml
position:
  id: "developer"
  type: "developer"
  title: "开发工程师"
  department: "技术部"
  jd: |
    负责代码编写、Bug 修复和技术方案实现。
  proactive: false
  contact_when: "有技术问题时"
  rules:
    - "只在你被@时响应"
  default_skills:
    - "git-operations"
```

### 5.3 环境变量 (.env)

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

### 6.1 添加新 Tool

1. 在 `subsystem/tools/{category}/` 创建新文件，继承 `Tool` 基类
2. 实现 `name`, `description`, `parameters`, `execute()` 方法
3. 在 `Agent._register_tools()` 中注册

```python
# src/tlon/subsystem/tools/my_category/my_tool.py
from ..base import Tool, ToolResult

class MyTool(Tool):
    name = "myTool"
    description = "工具描述"
    parameters = {...}
    
    async def execute(self, **kwargs) -> ToolResult:
        # 实现逻辑
        return ToolResult.success(data=...)
```

### 6.2 添加新 Skill

1. 创建 `skills/{skill-name}/` 目录
2. 编写 `SKILL.md`（YAML frontmatter + Markdown）
3. 添加 `scripts/` 脚本（可选）
4. 在 Agent 配置中启用

### 6.3 添加新 Agent

1. 创建 `config/agents/{agent-id}.yaml`
2. 指定 `position_id` 关联岗位
3. 配置技能、LLM、Docker 参数
4. 运行 `tlon wizard` 可交互式创建

---

## 7. 相关文档

| 文档 | 内容 |
|------|------|
| [品牌叙事](../../marketing/brand-narrative-v2.md) | 产品规格和需求 |
| [memory.md](./memory.md) | Memory 系统详细设计 |
| [multi-agent.md](./multi-agent.md) | 多 Agent 协作系统设计 |
| [AGENTS.md](../../../AGENTS.md) | 开发指南和代码规范 |
| [ui-architecture.md](./ui-architecture.md) | 多界面架构设计 |

---

## 8. 架构演进

### 当前状态
- **入口层**: ✅ 模块入口、CLI 命令分发
- **应用层**: ✅ CLI、Server、Clients、Wizard 整合在 `app/`
- **基础层**: ✅ Config、Events、Logger 在 `base/`
- **组织层**: ✅ `organization/` 目录，已改名（原核心层）
- **Agent 层**: ✅ Agent、LLMClient、ToolExecutor
- **Sandbox 层**: ✅ `sandbox/` 独立目录
- **子系统层**: ✅ `subsystem/` 包含 memory/、skills/、tools/

### 架构层次

```
┌─────────────────────────────────────────────────────────────┐
│ 1. 入口层 (Entry)        │ __main__.py, app/cli/main.py      │ ✅
├─────────────────────────────────────────────────────────────┤
│ 2. 应用层 (App)          │ cli/, server/, clients/, wizard/  │ ✅
├─────────────────────────────────────────────────────────────┤
│ 3. 基础层 (Base)         │ config/, events/, logger, settings│ ✅ 🆕
├─────────────────────────────────────────────────────────────┤
│ 4. 组织层 (Organization) │ messaging/, positions/            │ ✅
│    (原核心层 Core)       │ 已从 protocol/ 拆分                │
├─────────────────────────────────────────────────────────────┤
│ 5. Agent 层              │ agents/                           │ ✅
├─────────────────────────────────────────────────────────────┤
│ 6. Sandbox 层            │ sandbox/                          │ ✅ 🆕
├─────────────────────────────────────────────────────────────┤
│ 7. 子系统层 (Subsystems) │ memory/, skills/, tools/          │ ✅
│                          │ tools/ 按功能分组                   │
└─────────────────────────────────────────────────────────────┘
```

### 关键重构变更

| 变更项 | 原位置 | 新位置 | 状态 |
|--------|--------|--------|------|
| Event 类 | `protocol/event.py` | `base/events/event.py` | ✅ 已完成 |
| EventBus | `messaging/event_bus.py` | `base/events/event_bus.py` | ✅ 已完成 |
| Message 类 | `protocol/message.py` | `organization/messaging/message.py` | ✅ 已完成 |
| DockerRuntime | `agents/docker_runtime.py` | `sandbox/docker_runtime.py` | ✅ 已完成 |
| 核心层 | `core/` 或 `messaging/` | `organization/` | ✅ 已改名 |
| CLI | `cli/` | `app/cli/` | ✅ 已整合 |
| Server | `server/` | `app/server/` | ✅ 已整合 |
| Clients | `clients/` | `app/clients/` | ✅ 已整合 |
| Wizard | `wizard/` | `app/wizard/` | ✅ 已整合 |
| Tools | `tools/` (扁平) | `subsystem/tools/` (分组) | ✅ 已重组 |

---

## 9. 导入路径示例

```python
# 基础层
from tlon.base.config import ConfigLoader, ConfigSaver
from tlon.base.config.models import AgentConfig, PositionConfig
from tlon.base.events import Event, EventType, EventBus
from tlon.base.logger import get_logger
from tlon.base.settings import settings

# 组织层
from tlon.organization.messaging import Congress, Message
from tlon.organization.positions import PositionRegistry

# Agent 层
from tlon.agents import Agent, AgentRegistry
from tlon.agents.llm_client import LLMClient
from tlon.agents.tool_executor import ToolExecutor

# Sandbox 层
from tlon.sandbox.docker_runtime import DockerRuntime

# 子系统层
from tlon.subsystem.memory import MemoryManager
from tlon.subsystem.skills import SkillManager
from tlon.subsystem.tools.tasks import TaskManager
```

---

## 10. 相关链接

- **Agent Skills 标准**: https://github.com/agentskills/agentskills
- **OpenAI API**: https://platform.openai.com/docs
- **Docker SDK**: https://docker-py.readthedocs.io/

---

*最后更新：2026-03-13*
