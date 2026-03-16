# 文档清理实施计划

> **基于审查报告的最终决策版本**
> **更新日期**: 2026-03-17
> **状态**: ✅ 已完成

---

## 决策摘要

| 项目 | 决策 |
|------|------|
| **产品名称** | 统一为 **Tlon**（Python 包名保持 `tlon` 小写） |
| **配置字段** | 使用 `position_id`（而非 `position`） |
| **配置格式** | 以实际 YAML 文件和代码为准 |
| **命令体系** | 文学化名称和工程化名称是**别名**，指向同一函数 |
| **Skill 相关** | 全部废弃，等待重做 |
| **架构核心** | 显式依赖注入 + 基于 EventBus 的间接通信 |
| **Roadmap** | 合并为层次结构 |
| **架构文档** | 分层整理到 docs/architecture/ |
| **AGENTS.md** | 精简为 ~100 行的地图层 |

---

## 执行结果

### ✅ 已完成的工作

1. **AGENTS.md 重构**: 从 633 行精简至 75 行
2. **建立 docs/design/**: 创建 8 个设计文档
3. **建立 docs/architecture/**: 创建 8 个架构文档
4. **建立 docs/roadmap/**: 创建 7 个路线图文档
5. **建立 docs/reference/**: 创建导入路径速查

### 📁 新文档结构

```
docs/
├── design/              # Layer 1: 编目层
│   ├── README.md
│   ├── technical.md
│   ├── config.md
│   ├── agents.md
│   ├── memory.md
│   ├── messaging.md
│   ├── development-process.md
│   ├── code-style.md
│   └── debugging.md
├── architecture/        # Layer 2: 分层地图
│   ├── README.md
│   ├── layers.md
│   ├── data-flow.md
│   └── domains/
│       ├── base.md
│       ├── organization.md
│       ├── agents.md
│       ├── sandbox.md
│       └── subsystem.md
├── roadmap/             # Layer 3: 规划层
│   ├── README.md
│   ├── milestone-1-cli.md
│   ├── milestone-2-skills.md
│   ├── milestone-3-memory.md
│   ├── milestone-4-multi-agent.md
│   ├── milestone-5-server-tui.md
│   └── milestone-6-web.md
├── reference/           # 参考文档
│   └── imports.md
├── user/                # 用户文档（保持不变）
├── marketing/           # 营销文档（保持不变）
└── archive/             # 归档文档
```

---

## 分层文档原则

- **Layer 0**: AGENTS.md (~100行) - 地图层
- **Layer 1**: docs/design/ - 编目层
- **Layer 2**: docs/architecture/ - 分层地图
- **Layer 3**: docs/roadmap/ - 规划层

**核心原则**: 代码为准，文档为映射；AGENTS.md 是入口，详细信息在下级文档。

---

*计划完成时间: 2026-03-17*
*执行者: Claude Code (Doc Gardening)*
