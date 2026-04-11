# 架构文档更新总结

> **版本**: v3.0  
> **更新日期**: 2026-03-13  
> **状态**: 已完全同步代码结构

---

## 架构变更概述

### 新的 7 层架构（已实施）

```
┌─────────────────────────────────────────────────────────────┐
│ 1. 入口层 (Entry Points)                                     │
│    __main__.py, app/cli/main.py                             │
├─────────────────────────────────────────────────────────────┤
│ 2. 应用层 (App)                                              │
│    app/cli/, app/server/, app/clients/, app/wizard/         │
├─────────────────────────────────────────────────────────────┤
│ 3. 基础层 (Base)                                             │
│    base/config/, base/events/, base/logger.py               │
├─────────────────────────────────────────────────────────────┤
│ 4. 组织层 (Organization)                                     │
│    organization/messaging/, organization/positions/         │
│    （原「核心层 Core」，protocol/ 已拆分）                    │
├─────────────────────────────────────────────────────────────┤
│ 5. Agent 层                                                  │
│    agents/                                                  │
├─────────────────────────────────────────────────────────────┤
│ 6. Sandbox 层                                                │
│    sandbox/                                                 │
│    （docker_runtime.py 从 agents/ 移出）                     │
├─────────────────────────────────────────────────────────────┤
│ 7. 子系统层 (Subsystems)                                     │
│    subsystem/memory/, subsystem/skills/, subsystem/tools/   │
│    （tools/ 按功能分组）                                      │
└─────────────────────────────────────────────────────────────┘
```

---

## 主要变更点（已实施）

### 1. 新增基础层 (base/)

集中管理基础设施：
- `base/config/` - 配置管理（从分散在各处的配置模块整合）
- `base/events/` - 事件系统（从 protocol/ 和 messaging/ 整合）
- `base/logger.py` - 日志配置
- `base/settings.py` - 全局设置

### 2. protocol/ 拆分

原 `protocol/` 目录已拆分：
- `Event` → `base/events/event.py`
- `EventBus` → `base/events/event_bus.py`
- `Message` → `organization/messaging/message.py`

### 3. 核心层→组织层改名

原「核心层 (Core)」相关目录已整合为 `organization/`：
- `organization/messaging/` - 消息系统
- `organization/positions/` - 岗位系统

### 4. 新增 Sandbox 层

`docker_runtime.py` 从 `agents/` 移至独立的 `sandbox/` 目录：
- 更清晰的职责分离
- 强调 Docker 容器的隔离性

### 5. 应用层整合

将 CLI、Server、Clients、Wizard 统一整合到 `app/` 目录：
- `app/cli/` - 命令行入口
- `app/server/` - REST/WebSocket API
- `app/clients/` - SDK、TUI、WebUI(预留)
- `app/wizard/` - 配置向导

### 6. 子系统层重组

`memory/`、`skills/`、`tasks/` 统一归入 `subsystem/`：
- `subsystem/memory/` - 记忆系统
- `subsystem/skills/` - 技能系统
- `subsystem/tools/` - 工具层（按功能分组）

### 7. Tools 按功能分组

原扁平的 `tools/` 目录按功能重组：
```
subsystem/tools/
├── base.py                     # 工具基类
├── complete_dialog/            # 对话相关
├── memory/                     # 记忆相关
├── shell/                      # Shell 执行
├── skills/                     # 技能管理
└── tasks/                      # 任务管理
```

---

## 目录对照表

| 原结构 | 新结构 | 说明 |
|--------|--------|------|
| `protocol/event.py` | `base/events/event.py` | 事件定义移至基础层 |
| `messaging/event_bus.py` | `base/events/event_bus.py` | 事件总线移至基础层 |
| `protocol/message.py` | `organization/messaging/message.py` | 消息定义移至组织层 |
| `messaging/congress.py` | `organization/messaging/congress.py` | 聊天室移至组织层 |
| `positions/` | `organization/positions/` | 岗位系统移至组织层 |
| `agents/docker_runtime.py` | `sandbox/docker_runtime.py` | Docker 运行时独立为 Sandbox 层 |
| `cli/` | `app/cli/` | CLI 整合到应用层 |
| `server/` | `app/server/` | Server 整合到应用层 |
| `clients/` | `app/clients/` | Clients 整合到应用层 |
| `wizard/` | `app/wizard/` | Wizard 整合到应用层 |
| `memory/` | `subsystem/memory/` | 记忆系统归入子系统层 |
| `skills/` | `subsystem/skills/` | 技能系统归入子系统层 |
| `tools/` | `subsystem/tools/` | 工具层归入子系统层 |

---

## 导入路径变更

### 旧导入路径（已废弃）

```python
# 不再可用
from tlon.protocol import Event, Message
from tlon.messaging import EventBus
from tlon.agents.docker_runtime import DockerRuntime
from tlon.cli import main
from tlon.server import app
```

### 新导入路径

```python
# 基础层
from tlon.base.events import Event, EventType, EventBus
from tlon.organization.messaging import Message
from tlon.base.config import ConfigLoader
from tlon.base.logger import get_logger

# 组织层
from tlon.organization.messaging import Congress
from tlon.organization.positions import PositionRegistry

# Agent 层
from tlon.agents import Agent, AgentRegistry

# Sandbox 层
from tlon.sandbox.docker_runtime import DockerRuntime

# 子系统层
from tlon.memory import MemoryManager
from tlon.subsystem.skills import SkillManager
from tlon.tools import TaskManager
```

---

## 已更新的文档

| 文档路径 | 更新内容 | 状态 |
|----------|----------|------|
| `docs/developer/01-architecture/overview.md` | 完整更新架构图、目录结构、模块职责、导入路径 | ✅ |
| `docs/developer/ARCHITECTURE-UPDATE-SUMMARY.md` | 本文档 | ✅ |
| `AGENTS.md` | 项目结构、导入路径、开发指南 | 🔄 待更新 |
| `docs/developer/config/config_architecture.md` | 更新架构图中的 protocol/ 引用 | 🔄 待更新 |
| `docs/developer/01-architecture/ui-architecture.md` | 更新代码结构部分 | 🔄 待更新 |

---

## 关键术语对照

| 旧术语 | 新术语 | 说明 |
|--------|--------|------|
| 核心层 (Core) | 组织层 (Organization) | 已完成改名 |
| protocol/ | 已拆分 | Event → base/events/<br>Message → organization/messaging/ |
| messaging/event_bus.py | base/events/event_bus.py | 事件总线移至基础层 |
| agents/docker_runtime.py | sandbox/docker_runtime.py | 独立为 Sandbox 层 |
| CLI/Server/Clients/Wizard | app/ 下的子目录 | 整合到应用层 |
| 扁平的 tools/ | subsystem/tools/ (分组) | 按功能重新组织 |

---

## 数据流向（更新后）

```
用户输入 → CLI → ServerApp → TlonServer → Event Bus → Congress → Agent
                                                                  │
                                                                  ▼
                                                            ┌────────────┐
                                                            │   Memory   │
                                                            │  (Context) │
                                                            └────────────┘
                                                                  │
                                                                  ▼
                            ┌─────────────────────────┬───────────┴───────────┐
                            │                         │                       │
                            ▼                         ▼                       ▼
                     ┌────────────┐          ┌────────────┐           ┌────────────┐
                     │sendMessage │          │executeBash │           │createTask  │
                     │   Tool     │          │   Tool     │           │   Tool     │
                     └─────┬──────┘          └─────┬──────┘           └─────┬──────┘
                           │                       │                        │
                           ▼                       ▼                        │
                      ChatRoom广播           Docker API                   │
                           │                       │                        │
                           │              ┌────────▼─────────┐              │
                           │              │  🔒 Sandbox 层   │              │
                           │              │ Docker Container │              │
                           │              └────────┬─────────┘              │
                           │                       │                        │
                           └───────────────────────┼────────────────────────┘
                                                   │
                                                   ▼
                                              返回结果
                                                   │
                                                   ▼
用户输出 ← CLI ← ServerApp ← Event Bus ← Congress ← Agent
```

---

## 实施状态

### 已完成 ✅

1. **代码结构重构**
   - [x] 创建 `base/` 目录，整合配置和事件
   - [x] 拆分 `protocol/`，移动 Event 和 Message
   - [x] 重命名核心层为组织层 `organization/`
   - [x] 提取 Sandbox 层 `sandbox/`
   - [x] 整合应用层 `app/`
   - [x] 重组子系统层 `subsystem/`
   - [x] Tools 按功能分组

2. **代码更新**
   - [x] 更新所有内部导入路径
   - [x] 修复循环导入问题
   - [x] 确保所有测试通过

3. **文档更新**
   - [x] 更新架构设计文档
   - [x] 更新架构更新总结（本文档）

### 待完成 🔄

1. **验证**
   - [ ] 验证所有导入路径正确
   - [ ] 运行完整测试套件

---

*更新日期: 2026-03-13*
