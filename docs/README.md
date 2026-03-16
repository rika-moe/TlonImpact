# Tlon 文档中心

> **代码为准，文档为映射**

本文档中心采用分层架构：
- **Layer 0**: [AGENTS.md](../AGENTS.md) - 地图层 (~100行)
- **Layer 1**: [design/](./design/) - 编目层
- **Layer 2**: [architecture/](./architecture/) - 分层地图
- **Layer 3**: [roadmap/](./roadmap/) - 规划层

---

## 📖 用户文档 (user/)

面向最终用户，帮助了解产品功能和使用方法。

```
user/
├── 01-getting-started.md    # 快速开始
├── 02-first-conversation.md # 第一次对话指南
├── 03-the-congress.md       # 代表大会（聊天室）
├── 04-memory.md             # 莎士比亚的记忆系统
├── 05-library.md            # 巴别图书馆（Skill 系统）⚠️
├── 06-mirror.md             # 镜子（Agent 管理）
├── 07-garden.md             # 花园（Git 工作流）
├── 08-ruins.md              # 环形废墟（高级功能）
└── engineering/             # 工程型用户指南
    ├── quickstart.md
    ├── commands.md
    └── configuration.md
```

---

## 🎨 设计文档 (design/)

**Layer 1 - 编目层**: 各领域的编目和索引，记录核心理念和验证状态。

| 文档 | 内容 | 状态 |
|------|------|------|
| [README.md](./design/README.md) | 设计文档总览 | ✅ |
| [technical.md](./design/technical.md) | 技术栈与依赖 | ✅ |
| [config.md](./design/config.md) | 配置系统设计 | ✅ |
| [agents.md](./design/agents.md) | Agent 系统设计 | ✅ |
| [memory.md](./design/memory.md) | 记忆系统设计 | ✅ |
| [messaging.md](./design/messaging.md) | 消息系统设计 | ✅ |
| [development-process.md](./design/development-process.md) | 开发流程规范 | ✅ |
| [code-style.md](./design/code-style.md) | 代码风格规范 | ✅ |
| [debugging.md](./design/debugging.md) | 调试与故障排除 | ✅ |

---

## 🏗️ 架构文档 (architecture/)

**Layer 2 - 分层地图**: 提供域和包分层的顶层地图。

| 文档 | 内容 | 状态 |
|------|------|------|
| [README.md](./architecture/README.md) | 架构总览 | ✅ |
| [layers.md](./architecture/layers.md) | 分层架构详解 | ✅ |
| [data-flow.md](./architecture/data-flow.md) | 数据流向 | ✅ |
| [domains/base.md](./architecture/domains/base.md) | 基础层 | ✅ |
| [domains/organization.md](./architecture/domains/organization.md) | 组织层 | ✅ |
| [domains/agents.md](./architecture/domains/agents.md) | Agent层 | ✅ |
| [domains/sandbox.md](./architecture/domains/sandbox.md) | Sandbox层 | ✅ |
| [domains/subsystem.md](./architecture/domains/subsystem.md) | 子系统层 | ⚠️ |

---

## 🗺️ 路线图 (roadmap/)

**Layer 3 - 规划层**: 记录里程碑、版本规划和演进路线。

| 里程碑 | 状态 | 说明 |
|--------|------|------|
| [milestone-1-cli.md](./roadmap/milestone-1-cli.md) | 🗂️ 已归档 | CLI 基础 |
| [milestone-2-skills.md](./roadmap/milestone-2-skills.md) | 🗂️ 已归档 ⚠️ | Skill 系统（废弃） |
| [milestone-3-memory.md](./roadmap/milestone-3-memory.md) | 🗂️ 已归档 | Memory 系统 |
| [milestone-4-multi-agent.md](./roadmap/milestone-4-multi-agent.md) | 🗂️ 已归档 | 多 Agent 协作 |
| [milestone-5-server-tui.md](./roadmap/milestone-5-server-tui.md) | 🗂️ 已归档 | Server + TUI |
| [milestone-6-web.md](./roadmap/milestone-6-web.md) | 🔄 进行中 | Web 界面 |

---

## 📚 参考文档 (reference/)

速查参考：
- [imports.md](./reference/imports.md) - 导入路径速查

---

## 🗄️ 归档文档 (archive/)

历史文档归档，保留以供参考。

---

## 🗂️ 项目入口文件

位于项目根目录：
- [`../README.md`](../README.md) - 项目主页
- [`../AGENTS.md`](../AGENTS.md) - 开发指南（地图层）

---

*文档最后更新: 2026-03-17*
