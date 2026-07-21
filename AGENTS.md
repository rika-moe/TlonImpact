# Tlon 开发指南

> 一个由观念构建的、正在入侵现实的宇宙

---

## 核心术语（博尔赫斯宇宙）

| 概念 | 命名 | 出处 |
|-----|------|------|
| 产品 | Tlon / 特隆 | 《特隆、乌克巴尔、第三极的奥比斯·特蒂乌斯》 |
| Chatroom | 代表大会 / Congress | 《代表大会》 |
| 记忆系统 | Shakespeare | 《与莎士比亚的记忆》 |
| ~~Skill 系统~~ | ~~巴别图书馆~~ | ~~《巴别图书馆》~~ |
| Git 工作流 | 花园 / Garden | 《小径分叉的花园》 |
| 自举计划 | 环形废墟 | 《环形废墟》 |
| Agent 管理 | Mirror / 镜 | 《镜子与面具》 |

---

## 快速开始

```bash
# 安装依赖
uv sync

# 配置环境
cp .env.example .env
# 编辑 .env，填入 OPENAI_API_KEY

# 运行（服务端 + TUI）
uv run tlon server    # 终端 1
uv run tlon tui       # 终端 2

# 单元测试
uv run pytest
```

**文学化命令别名**：`server` = `dream`, `tui` = `intrude`, `wizard` = `mirror`

---

## 文档索引

| 领域 | 设计文档 | 架构文档 |
|------|----------|----------|
| 概述 | [design/README.md](./docs/design/README.md) | [architecture/README.md](./docs/architecture/README.md) |
| **dream 进程（Server）** | - | **[dream.md](./docs/architecture/dream.md)** ⭐ |
| **chat + ops 进程（TUI）** | - | **[client.md](./docs/architecture/client.md)** |
| **mirror 进程（Wizard）** | - | **[mirror.md](./docs/architecture/mirror.md)** |
| **通信协议** | [design/client-server-protocol.md](./docs/design/client-server-protocol.md) | **[protocol.md](./docs/architecture/protocol.md)** |
| 用户指南 | [user/README.md](./docs/user/README.md) | - |
| 架构边界 | - | [explicit-boundaries.md](./docs/architecture/explicit-boundaries.md) |
| 配置系统 | [design/config.md](./docs/design/config.md) | [architecture/domains/base.md](./docs/architecture/domains/base.md) |
| **文件系统布局** | **[design/config.md](./docs/design/config.md)** (含 ~/.tlon/ 目录结构) | - |
| Agent 系统 | [design/agents.md](./docs/design/agents.md), [narrative-prompt.md](./docs/design/narrative-prompt.md), [async-agent-loop.md](./docs/design/async-agent-loop.md) | [architecture/domains/agents.md](./docs/architecture/domains/agents.md) |
| 记忆系统 | [design/memory.md](./docs/design/memory.md) | [architecture/dream.md](./docs/architecture/dream.md) |
| 消息系统 | [design/messaging.md](./docs/design/messaging.md) | [architecture/domains/organization.md](./docs/architecture/domains/organization.md) |
| **TUI 设计** | **[Congress](./docs/design/tui-congres.md)** · **[Operator](./docs/design/tui-operator.md)** 🆕 | - |
| 路线图 | - | [roadmap/README.md](./docs/roadmap/README.md) |

---

## 状态总览

| 领域 | 状态 | 评分 | 备注 |
|------|------|------|------|
| 配置系统 | ✅ 已验证 | 100/100 | 稳定 |
| Agent 系统 | ✅ 已验证 | 95/100 | 异步多 Session、步进调度 |
| 记忆系统 | ✅ 已验证 | 100/100 | 稳定 |
| 消息系统 | ✅ 已验证 | 100/100 | 稳定 |
| Client-Server | ✅ 已验证 | 95/100 | Trace 系统、Event 扩展已完成 |
| ~~Skill 系统~~ | ❌ 已移除 | - | 2026-03-17 移除 |
| TUI (Congress) | ⚠️ 待验证 | 65/100 | 基础功能可用 |
| TUI (Operator) | ⚠️ 待验证 | 80/100 | TypeScript/Ink 实现（apps/operator），真实连接服务端 |
| Sandbox | ✅ 已验证 | 95/100 | 共享 DockerRuntime, macOS 支持 |

-- 
## 开发规范
- **禁止直接推送 main 分支** — 所有修改必须通过分支 + PR
- 合理划分模块，高内聚，低耦合
- 排查bug要深挖根因，纵览全局，防止后续出现相同bug
- **必须使用 type hint 类型注解**
- 必须使用lint工具: `uv run ruff`

---

## 参考链接

| 类型 | 文档 |
|------|------|
| 开发规范 | [开发流程](./docs/design/development-process.md), [代码风格](./docs/design/code-style.md), [故障排除](./docs/design/debugging.md) |
| 架构 | [dream 进程](./docs/architecture/dream.md), [客户端架构](./docs/architecture/client.md), [通信协议](./docs/architecture/protocol.md), [显式边界](./docs/architecture/explicit-boundaries.md), [mirror 进程](./docs/architecture/mirror.md) |
| 路线图 | [Roadmap](./docs/roadmap/README.md), [已归档](./docs/roadmap/completed/) |
| 速查 | [导入路径速查](./docs/reference/imports.md) |

---

*最后更新：2026-06-01*
