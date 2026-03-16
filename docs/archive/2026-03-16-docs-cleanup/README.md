# 文档归档说明

**归档日期**: 2026-03-16  
**归档原因**: 文档清理与重构

---

## 归档内容

### 旧架构文档 (old-architecture/)

以下文档内容已合并到 `docs/developer/01-architecture/overview.md` 或已被取代：

- `architecture-with-sandbox.md` - 早期架构设计，已整合到 overview.md
- `agent-loading-flow.md` - Agent 加载流程，已整合到 overview.md
- `ARCHITECTURE-UPDATE-SUMMARY.md` - 架构更新历史，信息已过时

### 旧路线图 (old-roadmaps/)

以下 roadmap 文档已合并到新的 `docs/developer/roadmap/` 目录：

- `roadmap.md` - 旧的主路线图
- `02-roadmap/roadmap.md` - 重复的主路线图
- `config-refactor-roadmap.md` - Config 重构路线图（已完成）
- `config-refactor-summary.md` - Config 重构总结（已完成）
- `roadmap-config-refactor.md` - 重复的 Config 路线图

### 旧配置文档 (old-config-docs/)

以下文档已合并到 `docs/developer/config/README.md`：

- `config_architecture.md` - 详细的配置架构设计
- `config_design.md` - 配置系统设计
- `config_roadmap.md` - Config 模块路线图

---

## 当前文档入口

清理后的文档结构：

```
docs/
├── README.md                          # 文档中心首页
├── developer/
│   ├── 01-architecture/
│   │   └── overview.md                # 唯一的主架构文档
│   ├── roadmap/                       # 层次化路线图
│   │   ├── README.md
│   │   ├── milestone-1-cli.md
│   │   ├── milestone-2-skills.md
│   │   ├── milestone-3-memory.md
│   │   ├── milestone-4-multi-agent.md
│   │   ├── milestone-5-server-tui.md
│   │   └── milestone-6-web-telegram.md
│   └── config/
│       └── README.md                  # 唯一的配置文档
└── user/                              # 用户文档
```

---

## 清理决策摘要

1. **产品名称**: 统一为 **Tlon**（Minions 为早期代号）
2. **配置字段**: `position_id`（而非 `position`）
3. **命令体系**: 文学名和工程名是**别名**，指向同一函数
4. **架构核心**: 显式依赖注入 + 基于 EventBus 的间接通信
5. **Skill 系统**: 标记为废弃，等待重做

---

*归档操作者: 架构师模式*  
*相关文档: `docs/developer/documentation-cleanup-plan.md`*
