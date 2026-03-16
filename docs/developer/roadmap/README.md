# Tlon 开发路线图

> 一个由观念构建的、正在入侵现实的宇宙。

---

## 当前状态

| 里程碑 | 状态 | 说明 |
|--------|------|------|
| [里程碑 1: CLI + 单 Agent](milestone-1-cli.md) | ✅ 已完成 | 基础架构验证 |
| [里程碑 2: Skill 系统](milestone-2-skills.md) | ⚠️ 已废弃 | 当前实现待废弃，计划重做 |
| [里程碑 3: Memory 系统](milestone-3-memory.md) | ✅ 已完成 | Shakespeare 记忆 |
| [里程碑 4: 多 Agent 系统](milestone-4-multi-agent.md) | ✅ 已完成 | 代表大会协作 |
| [里程碑 5: Server + TUI + Wizard](milestone-5-server-tui.md) | ✅ 已完成 | Client-Server 架构 |
| [里程碑 6: Web + Telegram](milestone-6-web-telegram.md) | ⬜ 待实现 | 多界面支持 |

---

## 架构核心原则

Tlon 采用两种核心设计模式：

1. **显式依赖注入 (Explicit DI)**
   - 所有组件依赖通过构造函数显式注入
   - `ServerApp.initialize()` 负责 DI 组装

2. **基于 EventBus 的间接通信**
   - 组件间不直接调用
   - 通过 `EventBus.publish/subscribe` 解耦

---

## 快速导航

### 开发者
- [架构总览](../01-architecture/overview.md)
- [配置说明](../config/README.md)

### 用户
- [快速开始](../../user/01-getting-started.md)
- [命令参考](../../user/engineering/commands.md)

---

*路线图最后更新: 2026-03-16*
