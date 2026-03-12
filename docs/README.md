# Minions 文档中心

本文档中心按受众分为两大类：

## 📖 用户文档 (user/)

面向最终用户，帮助了解产品功能和使用方法。

```
user/
├── 01-product/          # 产品规格
│   └── spec.md          # 产品规格文档 - 了解 Minions 是什么
├── 02-interface/        # 界面使用指南
│   ├── tui-guide.md     # TUI 终端界面使用指南
│   ├── web-guide.md     # Web 界面使用指南 (未来)
│   └── telegram-guide.md # Telegram Bot 使用指南 (未来)
└── 03-configuration/    # 配置指南
    └── wizard-guide.md  # 配置向导使用指南
```

**快速开始**: 从 [`01-product/spec.md`](user/01-product/spec.md) 开始了解产品。

---

## 🔧 开发文档 (developer/)

面向开发者，包含架构设计、开发计划和实现细节。

```
developer/
├── 01-architecture/     # 架构设计
│   ├── overview.md      # 架构总览
│   ├── ui-architecture.md # UI 架构设计
│   ├── multi-agent.md   # 多 Agent 协作系统设计
│   └── memory.md        # Memory 内存系统设计
├── 02-roadmap/          # 开发计划与路线图
│   ├── roadmap.md       # 主开发路线图
│   ├── agent-position-roadmap.md # Position-Agent 架构重构路线
│   └── refactor-event-plan.md    # Event 系统重构计划
├── 03-wizard/           # 配置向导设计
│   ├── wizard-design.md         # Wizard 1.0 设计
│   ├── wizard-v2-design.md      # Wizard 2.0 设计
│   └── wizard-v2-implementation.md # Wizard 2.0 实施计划
└── 04-skills/           # Skills 系统
    ├── README.md        # Skills 开发指南
    └── ARCHITECTURE.md  # Skills 架构设计
```

**快速开始**: 从 [`01-architecture/overview.md`](developer/01-architecture/overview.md) 开始了解架构。

---

## 🗂️ 项目入口文件

位于项目根目录：

- [`../README.md`](../README.md) - 项目主页，快速开始指南
- [`../AGENTS.md`](../AGENTS.md) - 开发指南，新人上手必读

---

## 📚 其他资源

- `../skills/` - Skills 具体定义目录
  - `file-operations/SKILL.md`
  - `git-operations/SKILL.md`
  - `csv-pipeline/SKILL.md`
  - `plan-i/SKILL.md`
  - `frontend-design/SKILL.md`
