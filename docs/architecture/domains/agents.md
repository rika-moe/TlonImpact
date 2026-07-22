# Agent 层

> 状态: ✅ 已验证
> 最后验证: 2026-07-22

---

## 职责

智能体实现：消息处理、Context 构建、LLM 调用、工具执行

---

## 模块结构

```
agents/
├── agent.py               # Agent 薄壳：生命周期、消息队列、think、子系统编排
├── spec.py                # AgentSpec：行为规约（语义唯一权威）+ behavior.tlon 加载
├── edn.py                 # EDN 数据字面量解析器（L0，只解析不求值）
├── scheduler.py           # SessionScheduler：主循环 + session 生命周期 + 超时清理
├── loop_driver.py         # LoopDriver：ReAct 步进 / 工具收割
├── trigger.py             # TriggerEvaluator：响应触发判断
├── context_builder.py     # ContextBuilder：对话上下文 + 系统提示缓存
├── reflection.py          # ReflectionEngine：记忆反思子系统
├── session.py             # AgentSession：单个会话/任务状态
├── prompt_builder.py      # SystemPromptBuilder：纯函数式系统提示组合
├── prompt_context.py      # AgentPromptContext 数据对象
├── agent_registry.py      # Agent 生命周期管理
└── tool_executor.py       # 工具执行器
```

> 注：`LLMClient` 位于 `base/llm_client.py`（基础设施层，供 agents 与 memory 共用）。

---

## 关键类

| 类 | 职责 | 路径 |
|----|------|------|
| `Agent` | 智能体薄壳（编排子系统） | `agents/agent.py` |
| `AgentSpec` | 行为规约（语义唯一权威） | `agents/spec.py` |
| `SessionScheduler` | 主循环、多 session 调度 | `agents/scheduler.py` |
| `LoopDriver` | ReAct step/collect | `agents/loop_driver.py` |
| `TriggerEvaluator` | 触发判断（mention/proactive） | `agents/trigger.py` |
| `ContextBuilder` | 上下文组装、系统提示缓存 | `agents/context_builder.py` |
| `ReflectionEngine` | 记忆反思 | `agents/reflection.py` |
| `AgentRegistry` | 生命周期管理 | `agents/agent_registry.py` |
| `LLMClient` | LLM API 调用 | `base/llm_client.py` |
| `ToolExecutor` | 工具注册执行 | `agents/tool_executor.py` |

---

## 行为规约（AgentSpec + behavior.tlon）

Agent 的行为是数据不是代码（见 [../../design/dsl-agents.md](../../design/dsl-agents.md)）。
`Agent` 构造时加载 `AgentSpec`（`agent.spec`），子系统全部从 spec 读行为参数：

- 文件路径：`~/.tlon/{agent_id}/config/behavior.tlon`（纯 EDN 数据字面量，L0 只解析不求值，
  解析器在 `agents/edn.py`；`:version` 必须为 1，未知 key 忽略并告警，缺省字段继承 DefaultSpec）；
- 加载机制：`AgentSpec.load(agent)` —— 文件存在则解析（解析/校验失败 → warning 日志 +
  回落 DefaultSpec），否则直接 DefaultSpec；
- `AgentSpec.default(agent)`（DefaultSpec）从 `AgentConfig` + memory config 精确复刻历史行为，
  因此无 spec 文件时行为不变；
- 消费方：TriggerEvaluator（`spec.trigger`）、LoopDriver（`spec.loop`）、
  ContextBuilder / SystemPromptBuilder（`spec.context`）、ReflectionEngine
  （`spec.on_complete.reflect`，`enabled` 仍是运行时开关）、
  `Agent._save_interaction_to_memory`（`spec.on_complete.save_interaction`）。

边界：`AgentConfig.proactive` 同时被消息路由器用作路由预筛（第一道闸，在 spec 之前），
`spec.trigger.proactive` 是 agent 内第二道闸，DefaultSpec 保证两者一致；
`session_timeout_seconds` / `message_queue_size` 是构造期 infra，留在 `AgentConfig` 不进 spec。

---

## Agent 核心流程

```
接收消息 → TriggerEvaluator.should_respond? → 入队（有界，满则丢弃）
→ SessionScheduler 主循环：建 session → LoopDriver 步进（Think → 并发派发工具 → 收割）
→ 发送响应 → 保存记忆 → ReflectionEngine 定期反思
```

**Tool 执行**: Agent 直接调用 `tool_executor.execute()`（模块内聚合），非 EventBus RPC。

**韧性配置**（`AgentConfig`）：`max_iterations`（ReAct 轮数上限，默认 10）、
`max_consecutive_errors`（连续 LLM 错误上限，默认 3）、`session_timeout_seconds`
（卡死会话清理，默认 1800）、`message_queue_size`（入队背压，默认 100）。

---

## 响应判断逻辑

1. 检查 @mention
2. 检查 Agent 的 proactive 配置

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
