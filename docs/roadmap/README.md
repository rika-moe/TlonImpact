# Tlon 开发路线图

> 状态: 🔄 里程碑 6 进行中
> 最后更新: 2026-03-17

---

## 当前状态

| 里程碑 | 状态 | 说明 |
|--------|------|------|
| [M1: CLI + 单 Agent](milestone-1-cli.md) | ✅ 已完成 | 基础架构验证 |
| [M2: Skill 系统](milestone-2-skills.md) | ⚠️ 已废弃 | 待重新设计 |
| [M3: Memory 系统](milestone-3-memory.md) | ✅ 已完成 | Shakespeare 记忆 |
| [M4: 多 Agent 系统](milestone-4-multi-agent.md) | ✅ 已完成 | 代表大会协作 |
| [M5: Server + TUI](milestone-5-server-tui.md) | ✅ 已完成 | Client-Server 架构 |
| [M6: Web + Telegram](milestone-6-web.md) | 🔄 进行中 | 多界面支持 |

---

## 架构核心原则

1. **显式依赖注入**: 所有组件依赖通过构造函数显式注入
2. **基于 EventBus 的间接通信**: 组件间通过 `EventBus.publish/subscribe` 解耦

---

## 快速导航

- [架构文档](../architecture/README.md)
- [设计文档](../design/README.md)
- [用户指南](../user/README.md)

---

## 文档索引

| 文档 | 目的 | 状态 |
|------|------|------|
| [milestone-1-cli.md](./milestone-1-cli.md) | CLI 基础 | 🗂️ 已归档 |
| [milestone-2-skills.md](./milestone-2-skills.md) | Skill 系统 | 🗂️ 已归档 |
| [milestone-3-memory.md](./milestone-3-memory.md) | Memory 系统 | 🗂️ 已归档 |
| [milestone-4-multi-agent.md](./milestone-4-multi-agent.md) | 多 Agent | 🗂️ 已归档 |
| [milestone-5-server-tui.md](./milestone-5-server-tui.md) | Server+TUI | 🗂️ 已归档 |
| [milestone-6-web.md](./milestone-6-web.md) | Web 界面 | 🔄 进行中 |

---

## 验证清单

- [x] 里程碑状态与代码一致
- [x] 所有链接有效
- [x] 当前里程碑明确
