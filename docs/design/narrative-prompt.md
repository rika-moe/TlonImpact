# Narrative-Driven Prompt 架构设计

> 状态: ✅ 已验证
> 最后验证: 2026-04-19

---

## 背景与动机

当前 prompt 架构存在以下问题：

1. **中间层幻觉**：`role.title/jd/rules`、`personality`、`workflow`、`discipline_rules` 等字段看似结构化，实则在 `SystemPromptBuilder` 中被简单拼接为字符串。开发者在 YAML 中配置字段，以为自己控制了 prompt，实际上只控制了字符串拼接器的输入。
2. **动静不分**：`memory_blocks` 注入在 system prompt 中，导致每次记忆变化都要重建 system prompt，需要 `_system_prompt_dirty` 机制来跟踪状态。
3. **重复冗余**：`capabilities` 和 `tool_usage` 两个 section 遍历同一组 `tool_definitions`，且 OpenAI API 的 `tools` 参数已携带完整描述。
4. **信息泄露**：`AgentPromptContext.team_members` 传递的是完整的 `list[AgentConfig]`，其他 agent 的私有工作流暴露在 team section 构建过程中。
5. **section 过多过碎**：9 个 section 的拼接顺序是"按代码遍历顺序"，而非"按 LLM 认知层次"。

本方案将 prompt 从"配置驱动"推向"narrative 驱动"——更接近 prompt engineering 的本质。

---

## 核心原则

| 原则 | 说明 |
|------|------|
| **一段式配置** | `identity` / `profile` / `behavior` 各是一段自由文本，不是字段集合 |
| **动静分离** | system prompt 完全静态（只来自配置），user prompt 承载所有动态内容 |
| **工具外置** | tool 描述完全交给 OpenAI `tools` 参数，system prompt 中零提及 |
| **协议统一** | 交互协议（输入格式、completeDialog 强制、@mention 机制）框架硬编码，所有 agent 共享 |
| **队友裸奔** | team section 直接放入队友完整 `profile`，接受 token 爆炸 |

---

## 配置模型

### AgentConfig

```python
class AgentConfig(BaseModel):
    # === 路由键（系统使用，不进入 prompt）===
    id: str
    name: str

    # === Prompt 原料（一段式文本）===
    identity: str = ""
    profile: str = ""
    behavior: str = ""

    # === 运行时配置 ===
    llm: LLMConfig = Field(default_factory=LLMConfig)
    docker: DockerRuntimeConfig = Field(default_factory=DockerRuntimeConfig)
    proactive: bool = False
```

### 删除的字段与模型

| 删除项 | 原因 |
|--------|------|
| `RoleConfig`（整个模型） | `title/jd/rules/contact_when` 全部属于"我是谁/我做什么"，合并进 narrative 文本 |
| `personality` | 合并进 `identity` |
| `workflow` | 合并进 `behavior` |
| `important_notes` | 格式规范进硬编码 protocol，可定制部分进 `behavior` |
| `discipline_rules` | 合并进 `behavior` |
| `respond_to_all` | 语义与 `proactive` 重叠，已废弃 |

---

## Prompt 分层架构

```
┌─────────────────────────────────────────────────────────────┐
│  SYSTEM PROMPT（完全静态，Agent 生命周期内只构建一次）          │
│                                                             │
│  你是 {name}。                                               │
│                                                             │
│  [身份]                                                      │
│  {identity}                                                  │
│                                                             │
│  [公开档案]（队友看你时，也看这段）                             │
│  {profile}                                                   │
│                                                             │
│  [行为守则]                                                  │
│  {behavior}                                                  │
│                                                             │
│  [交互协议]（框架硬编码，所有 agent 完全一致）                   │
│  {protocol}                                                  │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  USER PROMPT（每次请求动态组装）                              │
│                                                             │
│  [已知信息]                                                  │
│  {knowledge}  ← memory blocks 注入                          │
│                                                             │
│  [对话历史]                                                  │
│  ...conversation history...                                  │
│                                                             │
│  [当前消息]                                                  │
│  {current_message}                                           │
└─────────────────────────────────────────────────────────────┘
```

---

## System Prompt：4 个 Section

### Section 1: Identity

```markdown
你是 {name}。

{identity}
```

如果 `identity` 为空，则只保留 `你是 {name}。`

### Section 2: Team（基于队友的 profile）

```markdown
## 团队成员

### 苏晚晴
产品经理，产品部。负责需求分析、功能规划和方案评审。需求不清楚或需要方案评审时可以找她。

### 江予白
运维工程师，技术部。负责系统部署、环境配置和故障处理。有部署或服务器问题可以找他。
```

**构建逻辑**：遍历 `team_members`，每个队友的 `profile` 原样放入，用 `### {name}` 作标题。不截断、不摘要。

### Section 3: Behavior

```markdown
## 行为守则

{behavior}
```

如果 `behavior` 为空，此 section 不渲染。

### Section 4: Protocol（框架硬编码）

```markdown
## 交互协议

1. **消息格式**
   - 用户消息：`user: 消息内容`
   - 同事广播：`agent_name: 消息内容`
   - 被@消息：`agent_name @你: 消息内容`

2. **输出机制**
   - 必须使用 `completeDialog` 工具结束对话
   - 直接输出文本不会发送消息
   - 只有通过 `completeDialog` 的 `mentions` 参数指定的人才会收到通知
   - content 中写 `@林知遥` 只是显示效果，不会真正通知对方

3. **协作规则**
   - 收到任务时确认即可，不要重复 @发送者
   - 任务完成向分派者汇报
   - 和同一 agent 来回超过 2 轮，停止回复，让人类介入
```

**设计决策**：protocol 绝对不可配置。它是系统的物理定律，不是 agent 的选择。

---

## User Prompt：Knowledge 注入

Knowledge（memory blocks）从 system prompt 迁移到 user prompt，作为首条 `role="user"` 消息：

```python
async def _build_context(self, current_message: Message) -> list[dict]:
    context = []

    # 1. 动态 knowledge（前置上下文）
    knowledge = await self._build_knowledge_context()
    if knowledge:
        context.append({
            "role": "user",
            "content": f"[已知信息]\n\n{knowledge}"
        })

    # 2. 对话历史
    for msg in room_history:
        ...

    # 3. 当前消息
    context.append({
        "role": "user",
        "content": current_message.content
    })

    return context
```

**为什么放 user role？**

- system prompt 现在完全静态，永不过期
- memory 变化不需要重建 system prompt
- LLM 能理解 `[已知信息]` 是上下文补充，不是用户的提问

**knowledge 内容格式**（来自 MemoryManager）：

```markdown
## 历史记忆

- 用户曾经提到想做一个数据分析平台。
- 上次会话中完成了 Docker 环境配置。

## 近期交互

1. [用户] 帮我看一下这个日志
   [Agent] 我已经定位到问题在...
```

---

## AgentPromptContext 新形状

```python
@dataclass
class AgentPromptContext:
    config: AgentConfig
    team_profiles: list[tuple[str, str]]  # [(name, profile), ...]
```

**删除了**：`tool_definitions`, `memory_blocks`

- `tool_definitions` 不再进入 prompt，直接从 `ToolExecutor` 传给 `LLMClient.chat(tools=...)`
- `memory_blocks` 移到 user prompt 组装阶段

---

## 缓存策略：极致简化

```python
class Agent:
    def __init__(self, config: AgentConfig, ...):
        self.config = config
        self._system_prompt_cache: str | None = None
        # 删除了 _system_prompt_dirty

    async def _get_system_prompt(self) -> str:
        if self._system_prompt_cache is None:
            context = AgentPromptContext(
                config=self.config,
                team_profiles=[
                    (m.name, m.profile)
                    for m in self.team_members
                    if m.id != self.config.id
                ]
            )
            self._system_prompt_cache = SystemPromptBuilder(context).build()
        return self._system_prompt_cache
```

**关键变化**：

- 没有 dirty 机制了
- `rememberFact`, `forget`, `generateSummary` 不再触发重建
- 只有配置热重载时才需要清空 cache

---

## 配置 YAML 新格式

```yaml
agent:
  id: lin-zhiyao
  name: 林知遥

  identity: |
    你是一名技术能力强的开发工程师，注重细节，喜欢钻研。
    你的习惯是先通过 bash 命令探查环境，再给出解决方案。

  profile: |
    开发工程师，技术部。负责代码编写、Bug 修复和技术方案实现。
    有技术问题或 Bug 需要处理时可以找我。我可以执行任意 bash 命令来完成任务。

  behavior: |
    收到直接@时，必须响应。
    未被@时，观察对话内容决定是否参与讨论。
    被@时的工作流程：
    1. 分析问题背景和需求
    2. 制定解决思路
    3. 如需验证：调用 bash/python 工具
    4. 给出完整、准确的回复
    完成任务后，向给你派活的人汇报。

  proactive: false

  llm:
    model_name: kimi-k2.5
    temperature: 1.0

  docker:
    image: ubuntu:22.04
    memory_limit: 512m
```

### 字段语义边界

| 字段 | 内容 | 给谁看 |
|------|------|--------|
| `identity` | 你是谁，你的性格、习惯、自我认知 | 只给 agent 自己 |
| `profile` | 你能做什么，你的职责、联系方式 | 给 agent 自己 + 所有队友 |
| `behavior` | 你怎么做，你的工作流程、响应规则 | 只给 agent 自己 |

---

## 现有配置迁移路径

| 旧字段 | 迁移到 | 处理方式 |
|--------|--------|----------|
| `personality` | `identity` | 直接合并进 identity 文本 |
| `role.title` | `identity` / `profile` | title 放入 identity 第一句；部门放 profile |
| `role.jd` | `profile` | 职责描述直接进 profile |
| `role.rules` | `behavior` | 规则合并进 behavior |
| `role.contact_when` | `profile` | "有 XX 问题可以找我"进 profile |
| `workflow` | `behavior` | 工作流程进 behavior |
| `important_notes` | `protocol`（硬编码）+ `behavior` | 格式规范进 protocol，可定制部分进 behavior |
| `discipline_rules` | `behavior` | 合并 |

---

## 实施改动清单

| 文件 | 改动内容 |
|------|----------|
| `src/tlon/base/config.py` | 删除 `RoleConfig`，重构 `AgentConfig`（删除旧字段，新增 `identity/profile/behavior`） |
| `src/tlon/agents/prompt_context.py` | 删除 `tool_definitions` / `memory_blocks`，改为 `team_profiles` |
| `src/tlon/agents/prompt_builder.py` | 重写 section 构建逻辑（4 个 section），删除 tool 相关 section |
| `src/tlon/agents/agent.py` | 删除 `_system_prompt_dirty`；knowledge 移到 `_build_context`；`_build_prompt_context` 简化 |
| `src/tlon/agents/agent_registry.py` | 删除 `RoleConfig` 引用，调整 Agent 构造参数 |
| `src/tlon/app/wizard/menus/agent_menu.py` | Wizard 支持 `identity` / `profile` / `behavior` 的编辑和展示 |
| `src/tlon/app/wizard/agent_store.py` | 适配新配置模型 |
| `config/agents/*.yaml` | 全部迁移到新格式（6 个文件） |
| 测试文件 | `test_prompt_builder.py`, `test_agent_injection.py`, `test_agent_prompt_cache.py` 等全面更新 |

---

## 设计决策边界

| 决策 | 选择 | 理由 |
|------|------|------|
| `name` 保留顶层字段 | 是 | 系统路由、@mention 匹配、YAML 文件名都依赖它 |
| profile 截断放入 team | 否 | 接受 token 爆炸；截断会破坏叙事完整性 |
| protocol 可配置 | 否 | 它是系统的物理定律，不是 agent 的选择 |
| identity 允许为空 | 是 | 如果用户没填，`你是 {name}` 已足够 |
| knowledge 放 user role | 是 | 保持 system prompt 绝对静态，消灭 dirty 机制 |
| tool 描述从 system 中删除 | 是 | OpenAI `tools` 参数已包含完整描述，重复浪费 token |
| behavior 允许为空 | 是 | 某些简单 agent 可能不需要额外行为约束 |

---

## 与现有设计的关联

- 本方案建立在 [config.md](./config.md) 的显式配置原则之上
- 与 [agents.md](./agents.md) 的依赖注入架构兼容：所有基础设施仍通过构造函数注入
- 与 [memory.md](./memory.md) 的记忆系统兼容：只是将 memory blocks 的注入位置从 system prompt 移到 user prompt

---

## 核心理念

1. **一段式配置**: `identity` / `profile` / `behavior` 各是一段自由文本，不是字段集合
2. **动静分离**: system prompt 完全静态（只来自配置），user prompt 承载所有动态内容
3. **工具外置**: tool 描述完全交给 OpenAI `tools` 参数，system prompt 中零提及
4. **协议统一**: 交互协议框架硬编码，所有 agent 共享

---

## 文档索引

| 文档 | 目的 | 深度 |
|------|------|------|
| [config.md](./config.md) | 配置系统设计 | L1 |
| [agents.md](./agents.md) | Agent 系统设计 | L1 |
| [memory.md](./memory.md) | 记忆系统设计 | L1 |
| [architecture/domains/agents.md](../architecture/domains/agents.md) | Agent 层架构 | L2 |

---

## 验证清单

- [x] `RoleConfig` 已完全移除
- [x] `AgentConfig` 字段与代码一致（`identity`/`profile`/`behavior`）
- [x] SystemPromptBuilder 4 个 section 与代码一致
- [x] Knowledge 注入 user prompt，非 system prompt
- [x] 无 `_system_prompt_dirty` 机制
- [x] 工具描述从 system prompt 中移除

---

*代码为准，文档为映射*
