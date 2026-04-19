# Agent 层

> 状态: ✅ 已验证
> 最后验证: 2026-04-19

---

## 职责

智能体实现：消息处理、Context 构建、LLM 调用、工具执行

---

## 模块结构

```
agents/
├── agent.py               # Agent 核心类
├── agent_registry.py      # Agent 生命周期管理
├── llm_client.py          # LLM 客户端
├── prompt_builder.py      # 系统提示构建
└── tool_executor.py       # 工具执行器
```

---

## 关键类

| 类 | 职责 | 路径 |
|----|------|------|
| `Agent` | 智能体核心 | `agents/agent.py` |
| `AgentRegistry` | 生命周期管理 | `agents/agent_registry.py` |
| `LLMClient` | LLM API 调用 | `agents/llm_client.py` |
| `ToolExecutor` | 工具注册执行 | `agents/tool_executor.py` |

---

## Agent 核心流程

```
接收消息 → _should_respond? → 构建 Context → LLM Think → 直接调用 Tool → 发送响应 → 保存记忆
```

**Tool 执行**: Agent 直接调用 `tool_executor.execute()`（模块内聚合），非 EventBus RPC。

---

## 响应判断逻辑

1. 检查 @mention
2. 检查 `respond_to_all` 配置
3. 检查 Agent 的 proactive 配置

---

## 文档索引

| 文档 | 目的 | 深度 |
|------|------|------|
| [../../design/agents.md](../../design/agents.md) | Agent 系统设计 | L1 |

---

## 验证清单

- [x] Agent 配置字段与代码一致
- [x] `RoleConfig` 已完全移除，角色信息合并入 narrative 文本
- [x] Tool 注册机制正确
