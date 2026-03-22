# Tlon 开发路线图

> 状态: 🔄 里程碑 7 进行中
> 最后更新: 2026-03-18

---

## 当前状态

| 里程碑 | 状态 | 说明 |
|--------|------|------|
| [M1: CLI + 单 Agent](completed/milestone-1-cli.md) | ✅ 已完成 | 基础架构验证 |
| [M2: Skill 系统](completed/milestone-2-skills.md) | ❌ 已移除 | 2026-03-17 移除 |
| [M3: Memory 系统](completed/milestone-3-memory.md) | ✅ 已完成 | Shakespeare 记忆 |
| [M4: 多 Agent 系统](completed/milestone-4-multi-agent.md) | ✅ 已完成 | 代表大会协作 |
| [M5: Server + TUI](completed/milestone-5-server-tui.md) | ✅ 已完成 | Client-Server 架构 |
| [M6: Web + Telegram](completed/milestone-6-web.md) | ✅ 已归档 | 多界面支持规划 |
| [M7: Client 扩展与可观测性](milestone-7-client-expansion.md) | 🔄 进行中 | 协议版本化、Tracing、多 Client |

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
| [completed/milestone-1-cli.md](./completed/milestone-1-cli.md) | CLI 基础 | 🗂️ 已归档 |
| [completed/milestone-2-skills.md](./completed/milestone-2-skills.md) | Skill 系统 | 🗂️ 已归档 |
| [completed/milestone-3-memory.md](./completed/milestone-3-memory.md) | Memory 系统 | 🗂️ 已归档 |
| [completed/milestone-4-multi-agent.md](./completed/milestone-4-multi-agent.md) | 多 Agent | 🗂️ 已归档 |
| [completed/milestone-5-server-tui.md](./completed/milestone-5-server-tui.md) | Server+TUI | 🗂️ 已归档 |
| [completed/milestone-6-web.md](./completed/milestone-6-web.md) | Web 界面 | 🗂️ 已归档 |
| [milestone-7-client-expansion.md](./milestone-7-client-expansion.md) | Client 扩展 | 🔄 进行中 |

---

## 验证清单

- [x] 里程碑状态与代码一致
- [x] 所有链接有效
- [x] 当前里程碑明确
