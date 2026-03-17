# Tlon 完整架构图（含 Sandbox 层）

> **版本**: 2.0  
> **更新日期**: 2026-03-13  
> **状态**: 已同步代码结构

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
│  │   messaging/    │  │    positions/   │  │   (EventBus from base)  │  │
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

---

## 🔒 Sandbox 层详解

### 核心组件

| 组件 | 文件 | 职责 |
|------|------|------|
| **DockerRuntime** | `sandbox/docker_runtime.py` | 管理容器生命周期、执行命令 |
| **Docker Container** | 运行时创建 | 隔离执行环境（每个 Agent 独立） |
| **ExecuteBashTool** | `subsystem/tools/shell/execute_bash.py` | 调用 DockerRuntime 执行 bash 命令 |

### 调用链

```
Agent._process_message()
    ↓
ToolExecutor.execute("executeBash", {...})
    ↓
ExecuteBashTool.execute(command="...")
    ↓
DockerRuntime.execute(command, timeout)
    ↓
Docker SDK (docker-py)
    ↓
Docker Daemon
    ↓
docker exec tlon-agent-{agent_id} bash -c "command"
    ↓
┌─────────────────────────────────┐
│  Container: tlon-agent-{id}     │
│  - Isolated filesystem          │
│  - Resource limits enforced     │
│  - Skill scripts available      │
└─────────────────────────────────┘
```

### 安全隔离

```
┌────────────────────────────────────────────────────────────┐
│  Host OS                                                   │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  Tlon Server Process                                 │  │
│  │  ┌────────────────────────────────────────────────┐  │  │
│  │  │  Agent (Python object)                         │  │  │
│  │  │  ┌──────────────────────────────────────────┐  │  │  │
│  │  │  │  DockerRuntime (管理接口)                │  │  │  │
│  │  │  └──────────────────┬───────────────────────┘  │  │  │
│  │  └─────────────────────┼──────────────────────────┘  │  │
│  └────────────────────────┼─────────────────────────────┘  │
│                           │ Docker API                      │
│  ┌────────────────────────▼─────────────────────────────┐  │
│  │  Docker Daemon                                       │  │
│  └────────────────────────┬─────────────────────────────┘  │
│                           │ container create/run/exec      │
│  ╔════════════════════════▼═════════════════════════════╗  │
│  ║  🐳 Docker Container (per Agent)                     ║  │
│  ║  ┌────────────────────────────────────────────────┐  ║  │
│  ║  │  Ubuntu 22.04                                  │  ║  │
│  ║  │  ┌──────────────┐  ┌────────────────────────┐  │  ║  │
│  ║  │  │  Bash Shell  │  │  /workspace (skills)   │  │  ║  │
│  ║  │  │  - 隔离进程  │  │  - Skill scripts       │  │  ║  │
│  ║  │  │  - 资源限制  │  │  - 工作目录            │  │  ║  │
│  ║  │  └──────────────┘  └────────────────────────┘  │  ║  │
│  ║  └────────────────────────────────────────────────┘  ║  │
│  ╚══════════════════════════════════════════════════════╝  │
└────────────────────────────────────────────────────────────┘
```

### 每个 Agent 的隔离环境

```
┌────────────────────────────────────────────────────────────────┐
│                        Host System                             │
│                                                                │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │              Docker Daemon (共享)                         │  │
│  └───────────────────────┬──────────────────────────────────┘  │
│                          │                                      │
│  ┌───────────────────────┼──────────────────────────────────┐  │
│  │                       │                                  │  │
│  ▼                       ▼                                  ▼  │
│ ╔═══════════════╗  ╔═══════════════╗  ╔═══════════════╗         │
│ ║ Container:    ║  ║ Container:    ║  ║ Container:    ║         │
│ ║ tlon-agent-   ║  ║ tlon-agent-   ║  ║ tlon-agent-   ║         │
│ ║ lin-zhiyao    ║  ║ su-wanqing    ║  │ pm-001        ║         │
│ ╠═══════════════╣  ╠═══════════════╣  ╠═══════════════╣         │
│ ║ 📁 /workspace ║  ║ 📁 /workspace ║  ║ 📁 /workspace ║         │
│ ║ 📄 skills/    ║  ║ 📄 skills/    ║  ║ 📄 skills/    ║         │
│ ║ 🔧 bash       ║  ║ 🔧 bash       ║  ║ 🔧 bash       ║         │
│ ║ ⚡ python3    ║  ║ ⚡ python3    ║  ║ ⚡ python3    ║         │
│ ║ 💾 512MB RAM  ║  ║ 💾 512MB RAM  ║  ║ 💾 512MB RAM  ║         │
│ ║ 🔄 1 CPU      ║  ║ 🔄 1 CPU      ║  ║ 🔄 1 CPU      ║         │
│ ╚═══════════════╝  ╚═══════════════╝  ╚═══════════════╝         │
│                                                                │
│  Agent 1 (Developer)  Agent 2 (Developer)  Agent 3 (PM)        │
└────────────────────────────────────────────────────────────────┘
```

---

## 完整层次结构（7层）

| 层级 | 目录/模块 | 主要职责 |
|------|----------|----------|
| 1. **入口层** | `__main__.py`, `app/cli/main.py` | 程序入口、命令解析 |
| 2. **应用层** | `app/cli/`, `app/server/`, `app/clients/`, `app/wizard/` | CLI、Server、Clients、Wizard |
| 3. **基础层** | `base/config/`, `base/events/`, `base/logger.py` | 配置、事件、日志 |
| 4. **组织层** (原核心层) | `organization/messaging/`, `organization/positions/` | EventBus、Congress、消息协议、岗位系统 |
| 5. **Agent 层** | `agents/` | Agent 实例、LLM 调用、Tool 执行 |
| 6. **Sandbox 层** | `sandbox/` | Docker 容器管理 |
| 7. **子系统层** | `subsystem/memory/`, `subsystem/skills/`, `subsystem/tools/` | 记忆、技能、工具 |

---

## 关键重构变更

| 变更项 | 原位置 | 新位置 | 说明 |
|--------|--------|--------|------|
| **核心层→组织层** | `core/` 或 `messaging/` | `organization/` | 已改名 |
| **protocol/ 拆分** | `protocol/event.py` | `base/events/event.py` | Event 移至基础层 |
| | `protocol/message.py` | `organization/messaging/message.py` | Message 移至组织层 |
| **EventBus 移动** | `messaging/event_bus.py` | `base/events/event_bus.py` | 移至基础层 |
| **DockerRuntime 提取** | `agents/docker_runtime.py` | `sandbox/docker_runtime.py` | 独立为 Sandbox 层 |
| **应用层整合** | `cli/`, `server/`, `clients/`, `wizard/` | `app/` 下的子目录 | 统一整合 |
| **子系统层重组** | `memory/`, `skills/`, `tools/` | `subsystem/` 下的子目录 | 统一归入 |
| **Tools 分组** | 扁平 `tools/` | `subsystem/tools/{category}/` | 按功能分组 |

---

## 导入路径示例

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
from tlon.subsystem.tools.base import Tool, ToolResult
from tlon.subsystem.tools.tasks import TaskManager
```

---

*更新日期: 2026-03-13*
