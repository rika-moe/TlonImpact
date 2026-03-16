# 架构总览

> 状态: ✅ 已验证
> 最后验证: 2026-03-17

---

## 核心理念

1. **显式依赖注入**: 所有依赖通过构造函数注入，无全局状态
2. **事件驱动解耦**: 组件间通过 EventBus 通信，不直接调用
3. **分层边界清晰**: 每层有明确职责，单向依赖

---

## 分层架构

```
┌─────────────────────────────────────────┐
│ 1. 入口层 (Entry)                        │
│    __main__.py, cli/main.py             │
├─────────────────────────────────────────┤
│ 2. 应用层 (App)                          │
│    cli/, server/, clients/, wizard/     │
├─────────────────────────────────────────┤
│ 3. 基础层 (Base)                         │
│    config/, events/, logger, settings   │
├─────────────────────────────────────────┤
│ 4. 组织层 (Organization)                 │
│    messaging/, positions/               │
├─────────────────────────────────────────┤
│ 5. Agent 层                              │
│    agents/                              │
├─────────────────────────────────────────┤
│ 6. Sandbox 层                            │
│    sandbox/                             │
├─────────────────────────────────────────┤
│ 7. 子系统层 (Subsystem)                  │
│    memory/, skills/, tools/             │
└─────────────────────────────────────────┘
```

---

## 文档索引

| 文档 | 目的 | 深度 |
|------|------|------|
| [layers.md](./layers.md) | 分层详解 | L2 |
| [data-flow.md](./data-flow.md) | 数据流向 | L2 |
| [domains/base.md](./domains/base.md) | 基础层 | L2 |
| [domains/organization.md](./domains/organization.md) | 组织层 | L2 |
| [domains/agents.md](./domains/agents.md) | Agent层 | L2 |
| [domains/sandbox.md](./domains/sandbox.md) | Sandbox层 | L2 |
| [domains/subsystem.md](./domains/subsystem.md) | 子系统层 | L2 |

---

## 验证清单

- [x] 分层与代码目录一致
- [x] 依赖方向正确（上层依赖下层）
- [x] 无循环依赖
