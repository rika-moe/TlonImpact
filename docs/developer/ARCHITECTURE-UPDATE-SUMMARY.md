# 架构文档更新总结

> 更新日期: 2026-03-10  
> 更新内容: 统一使用最新架构图（含 🔒 Sandbox 层和「核心层→组织层」改名注释）

---

## 架构变更概述

### 新的 8 层架构

```
┌─────────────────────────────────────────────────────────────┐
│ 1. 入口层 (Entry Points)                                     │
│    __main__.py, cli/main.py, cli/wizard.py                  │
├─────────────────────────────────────────────────────────────┤
│ 2. 交互层 (Adapters/CLI)                                     │
│    cli/tui_simple.py, cli/tui.py, adapters/, clients/       │
├─────────────────────────────────────────────────────────────┤
│ 3. 服务端层 (Server)                                         │
│    server/app.py, api/rest.py, api/websocket.py,            │
│    core/server.py (TlonServer)                              │
├─────────────────────────────────────────────────────────────┤
│ 4. 核心层 (Core) → 将改名为「组织层 (Organization)」         │
│    messaging/, protocol/, positions/                        │
│    注：后续将改名为 Organization，反映其管理协作基础设施的职责 │
├─────────────────────────────────────────────────────────────┤
│ 5. Agent 层                                                  │
│    agents/agent.py, llm_client.py, docker_runtime.py,       │
│    tool_executor.py, agent_registry.py, prompt_builder.py   │
├─────────────────────────────────────────────────────────────┤
│ 6. 子系统层 (Subsystems)                                     │
│    memory/, skills/, tasks/                                 │
├─────────────────────────────────────────────────────────────┤
│ 7. 工具层 (Tools)                                            │
│    tools/execute_bash.py, tools/send_message.py,            │
│    tools/create_task.py, tools/activate_skill.py 等         │
├─────────────────────────────────────────────────────────────┤
│ 8. 🔒 Sandbox 层 (Docker 容器)                               │
│    Docker Container (per Agent)                             │
│    - executeBashCommand 在此层执行                          │
│    - 每个 Agent 独立的隔离环境                               │
└─────────────────────────────────────────────────────────────┘
```

### 主要变更点

1. **新增 🔒 Sandbox 层**：明确标识 Docker 容器作为隔离执行环境
2. **核心层添加改名注释**：在「核心层 (Core)」处添加注释，说明后续将改名为「组织层 (Organization)」
3. **明确服务端层**：Server-Client 架构中的服务端组件
4. **细化层次结构**：从原来的 3 层细化为 8 层

---

## 已更新的文档

| 文档路径 | 更新内容 |
|----------|----------|
| `architecture-with-sandbox.md` | 新增完整架构图（含 Sandbox 层和改名注释） |
| `01-architecture/overview.md` | 更新架构图、数据流向图、模块职责表、架构演进章节 |
| `01-architecture/ui-architecture.md` | 更新分层架构图，添加组织层和 Sandbox 层 |
| `01-architecture/multi-agent.md` | 更新并发模型图，添加 Sandbox 层 |
| `04-skills/ARCHITECTURE.md` | 添加技能系统在整体架构中的位置说明 |
| `05-tasks/README.md` | 添加任务系统在整体架构中的位置说明 |
| `config/config_architecture.md` | 更新整体架构图，添加组织层和 Sandbox 层 |
| `roadmap.md` | 添加架构层次参考图 |

---

## 关键术语对照

| 旧术语 | 新术语 | 说明 |
|--------|--------|------|
| 核心层 (Core) | 组织层 (Organization) | 即将改名，反映其管理协作基础设施的职责 |
| - | 🔒 Sandbox 层 | 新增，Docker 容器隔离执行环境 |
| ChatRoom | Congress (代表大会) | 博尔赫斯文学命名 |
| - | TlonServer | 服务端核心类 |
| - | AgentRegistry | Agent 生命周期管理 |

---

## 数据流向（更新后）

```
用户输入 → CLI/TUI → WebSocket → ServerApp → TlonServer → Event Bus → ChatRoom → Agent
                                                                               │
                                                                               ▼
                              ┌──────────────────────────────────────────────┤
                              │                                              │
                              ▼                                              ▼
                        ┌────────────┐                              ┌────────────┐
                        │   Memory   │                              │    LLM     │
                        │  (Context) │                              │   Think    │
                        └────────────┘                              └─────┬──────┘
                                                                          │
                              ┌─────────────────────────┬─────────────────┼─────────┐
                              │                         │                 │         │
                              ▼                         ▼                 ▼         ▼
                       ┌────────────┐          ┌────────────┐     ┌────────────┐ ┌────────────┐
                       │sendMessage │          │executeBash │     │createTask  │ │activateSkill│
                       │   Tool     │          │   Tool     │     │   Tool     │ │   Tool     │
                       └─────┬──────┘          └─────┬──────┘     └─────┬──────┘ └─────┬──────┘
                             │                       │                  │            │
                             ▼                       ▼                  │            │
                      ChatRoom广播             Docker API              │            │
                             │                       │                  │            │
                             │              ┌────────▼─────────┐        │            │
                             │              │  🔒 Sandbox 层   │        │            │
                             │              │ Docker Container │        │            │
                             │              └────────┬─────────┘        │            │
                             │                       │                  │            │
                             └───────────────────────┼──────────────────┴────────────┘
                                                     │
                                                     ▼
                                                返回结果
                                                     │
                                                     ▼
用户输出 ← CLI/TUI ← WebSocket ← ServerApp ← ChatRoom ← Agent
```

---

## 后续工作

1. **代码层面**：考虑将 `messaging/`、`protocol/`、`positions/` 目录重新组织为 `organization/` 目录
2. **文档层面**：在组织层改名完成后，更新所有文档中的术语
3. **架构层面**：继续完善 Sandbox 层的文档，包括安全策略和资源管理

---

*更新日期: 2026-03-10*
