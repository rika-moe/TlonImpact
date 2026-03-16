# Tlon 开发指南

> 一个由观念构建的、正在入侵现实的宇宙

---

## 核心术语（博尔赫斯宇宙）

| 概念 | 命名 | 出处 |
|-----|------|------|
| 产品 | Tlon / 特隆 | 《特隆、乌克巴尔、第三极的奥比斯·特蒂乌斯》 |
| Chatroom | 代表大会 / Congress | 《代表大会》 |
| 记忆系统 | Shakespeare | 《与莎士比亚的记忆》 |
| Skill 系统 | 巴别图书馆 | 《巴别图书馆》 |
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
```

**文学化命令别名**：`server` = `dream`, `tui` = `intrude`, `wizard` = `mirror`

---

## 文档索引

| 领域 | 设计文档 | 架构文档 |
|------|----------|----------|
| 概述 | [design/README.md](docs/design/README.md) | [architecture/README.md](docs/architecture/README.md) |
| 配置系统 | [design/config.md](docs/design/config.md) | [architecture/domains/base.md](docs/architecture/domains/base.md) |
| Agent 系统 | [design/agents.md](docs/design/agents.md) | [architecture/domains/agents.md](docs/architecture/domains/agents.md) |
| 记忆系统 | [design/memory.md](docs/design/memory.md) | [architecture/domains/subsystem.md](docs/architecture/domains/subsystem.md) |
| 消息系统 | [design/messaging.md](docs/design/messaging.md) | [architecture/domains/organization.md](docs/architecture/domains/organization.md) |
| 路线图 | - | [roadmap/README.md](docs/roadmap/README.md) |

---

## 状态总览

| 领域 | 状态 | 评分 | 备注 |
|------|------|------|------|
| 配置系统 | ✅ 已验证 | 85/100 | 稳定 |
| Agent 系统 | ✅ 已验证 | 90/100 | 稳定 |
| 记忆系统 | ✅ 已验证 | 85/100 | 稳定 |
| 消息系统 | ✅ 已验证 | 90/100 | 稳定 |
| Skill 系统 | ⚠️ 待重做 | 40/100 | 计划重新设计 |
| Sandbox | ✅ 已验证 | 85/100 | 稳定 |

---

## 参考链接

- [开发流程](docs/design/development-process.md)
- [代码风格](docs/design/code-style.md)
- [导入路径速查](docs/reference/imports.md)
- [故障排除](docs/design/debugging.md)

---

*最后更新：2026-03-17*
