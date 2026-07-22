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
├── prototypes.py          # Behavior prototype 库：种子 / 列举 / 实例化
├── scheduler.py           # SessionScheduler：主循环 + session 生命周期 + 超时清理
├── loop_driver.py         # LoopDriver：ReAct 步进 / 工具收割
├── trigger.py             # TriggerEvaluator：响应触发判断
├── context_builder.py     # ContextBuilder：对话上下文 + 系统提示缓存
├── reflection.py          # ReflectionEngine：记忆反思子系统
├── session.py             # AgentSession：单个会话/任务状态
├── prompt_builder.py      # SystemPromptBuilder：纯函数式系统提示组合
├── prompt_context.py      # AgentPromptContext 数据对象
├── agent_registry.py      # AgentRegistry：激活感知生命周期（启动分流/唤醒/空闲回收）
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
| `AgentRegistry` | 生命周期管理（休眠/唤醒/回收） | `agents/agent_registry.py` |
| `LLMClient` | LLM API 调用 | `base/llm_client.py` |
| `ToolExecutor` | 工具注册执行 | `agents/tool_executor.py` |

---

## 动态 Registry（休眠/唤醒，阶段 6a）

`AgentRegistry` 管理 activation（瞬态运行时），persona（`~/.tlon/{id}/`）由
AgentInfoProvider 提供全集，两者分离（见 [../../design/dsl-agents.md](../../design/dsl-agents.md)）：

- **激活模式**（`AgentConfig.activation`）：`"always"`（默认）boot 即启动、永不休眠，
  行为与旧版一致；`"lazy"` 只注册不启动，被显式 @mention 时唤醒，空闲后回收。
  全 lazy 配置下 boot 后 0 个运行中 agent；
- **状态**：`get_agent_status` 对 lazy 未运行返回 `"sleeping"`，唤醒中 `"waking"`，
  always 未运行保持 `"unknown"`；
- **Wake-on-mention**：registry 订阅 `MESSAGE_ROUTED`，仅对
  `RoutedMessage.mention_target_ids`（mention 解析出的 target 子集，proactive 广播
  不在其中）中"已知配置 + 未运行 + 非 waking"的 agent 触发 `_wake_and_redeliver`：
  per-agent `asyncio.Lock` 串行化（并发 @ 只装配一次，但每个在沉睡期到达的事件都会
  重投递）→ 复用 `_start_agent` 装配 → **重发原 MESSAGE_ROUTED 事件对象**（同 payload、
  同 trace carrier），新 agent 的 handler 自然拾取；唤醒后 agent 已在运行表，重发不会
  再触发唤醒（防循环）。观测：`AGENT_WAKING` / `AGENT_WOKEN`（含 wake_duration）/
  `AGENT_WAKE_FAILED` 事件 + 同名 snake_case 日志，唤醒耗时是原消息 trace 树下的
  `agent.wake` span；
- **空闲回收（drain）**：30s 周期 monitor；lazy agent 空闲超 `idle_timeout_seconds`
  （默认 600）且无存活 session → `agent.shutdown()` + 移出运行表（保留注册），
  发 `AGENT_SLEEPING`。drain 语义：有 ACTIVE/WAITING session 绝不停，且在 per-agent
  锁下 shutdown 前重查一次。Agent 只读暴露 `active_session_count` / `idle_seconds`
  （SessionScheduler 提供："活动"= session 创建 / 存活 session 的 `last_active_at` /
  session 完结；无 session 时从主循环启动算起）。休眠即主循环停止，reflection 自然停烧；
- **容器懒启动**：`_start_agent` 只构造 `DockerRuntime` 不启动容器；
  `DockerRuntime.execute` / `read_file` / `write_file` / `file_exists` 在 container
  为空时先 `ensure_running()`（幂等），daemon 不可用表现为工具报错而非启动失败。
  纯对话唤醒零容器成本。

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

## Behavior Prototype 库

预制行为原型 = 部分 AgentSpec 覆盖 + 人格文件模板，让人类和 agent 以"模板 + 覆盖"
方式创建新 agent（设计见 [../../design/dsl-agents.md](../../design/dsl-agents.md) 阶段 5）。

- **目录形式**：`tlon/prototypes/{name}/`（包内数据，随 wheel 分发），每个含
  `behavior.tlon`（只写与 DefaultSpec 的差异字段）、`IDENTITY.md` / `SOUL.md` / `RULES.md`
  人格模板、`prototype.edn` 元数据（`{:name :display-name :description}`，edn.py 解析）。
  首批 3 个：`deep-thinker`（:max-iterations 25、reflect 10 分钟）、
  `fast-responder`（:max-iterations 3、:knowledge false、:room-history 5）、
  `archivist`（reflect 5 分钟、:save-interaction true）；relay 因需要 `:llm false`
  逃生门而推迟；
- **种子**：`seed_prototypes()` 在 ServerApp 初始化早段把包内 prototypes 经
  `importlib.resources` 拷贝到 `~/.tlon/prototypes/`（幂等，同名文件跳过——用户可改可加，
  不覆盖）；wizard 创建入口也会先种子（mirror 是独立进程）；
- **发现**：`list_prototypes()` 读 `~/.tlon/prototypes/`（非包内——用户修改可见），
  暴露给 agent 的是 `listPrototypes` 工具（`tools/prototypes/list_prototypes.py`，
  随每个 agent 注册）；暴露给人类的是 wizard 人员管理菜单"从 Prototype 创建"；
- **实例化**：`instantiate(prototype_name, agent_id, agent_name, overrides, proactive)`
  ——校验 prototype 存在与 agent_id 不冲突；人格三文件**拷贝**（非引用，行为冻结于创建时刻）
  到 `~/.tlon/{agent_id}/config/`；prototype 的 behavior.tlon 解析后在 **EDN 数据层**
  与 overrides 深合并（map 按键递归合并，其余替换，keyword 类型保留）再写为实例自有
  behavior.tlon；agent.yaml 走与 wizard 相同的 `_yaml_save` 路径，
  `AgentConfig.prototype` 记录来源 prototype；
- **生效时机**：不做 registry 热加载，重启 dream 后生效（与 wizard 现有创建行为一致）。

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
