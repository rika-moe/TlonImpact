# Agent 系统设计

> 状态: ✅ 已验证
> 最后验证: 2026-03-22

---

## 核心理念

1. **Narrative 驱动**: Agent 行为由 `identity`/`profile`/`behavior` 三段自由文本定义，非结构化字段
2. **工具扩展**: 通过 Tool 系统扩展能力，非硬编码
3. **记忆感知**: 自动注入相关记忆到 User Prompt（非 System Prompt）
4. **沙箱执行**: 所有外部操作在 Docker 容器中执行

---

## Agent 生命周期

```
注册 → 加载配置 → 激活 → 消息循环 → 停用
```

| 阶段 | 说明 |
|------|------|
| 注册 | AgentRegistry 管理生命周期 |
| 加载 | 从 YAML 加载配置和记忆 |
| 激活 | 启动 Docker 容器，加载 Tools |
| 消息循环 | 接收消息 → 构建 Context → LLM 决策 → 执行 Tool |
| 停用 | 保存记忆，停止容器 |

---

## 消息处理流程

```
接收消息 → _should_respond? → 构建 Context → LLM Think → 执行 Tool → 发送响应 → 保存记忆
```

---

## 文档索引

| 文档 | 目的 | 深度 |
|------|------|------|
| [architecture/domains/agents.md](../architecture/domains/agents.md) | Agent 层架构 | L2 |
| [messaging.md](./messaging.md) | 消息系统 | L1 |
| [memory.md](./memory.md) | 记忆系统 | L1 |

---

## 验证清单

- [x] Agent 配置字段与代码一致（`identity`/`profile`/`behavior`）
- [x] `RoleConfig` 已移除，角色信息合并入 narrative 文本
- [x] 记忆注入到 User Prompt，System Prompt 保持静态
- [x] Tool 注册机制与代码一致
