# Memory 系统设计文档

## 概述

本文档描述 Minions 项目的 Agent Memory 机制设计。本设计遵循以下约束：
- **简单优先**：先实现核心功能，复杂特性（如向量检索）预留接口但暂不实现
- **独立存储**：每个 Agent 拥有独立的记忆空间，通过 ChatRoom History 间接共享消息
- **文件存储**：开发阶段使用 JSONL 文件存储

---

## 1. Context 构建策略

### 1.1 分层架构

Context 由以下四层组成（从远到近）：

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ LAYER 1: System Context (静态)                                               │
│ ├─ Agent 角色定义                                                             │
│ ├─ 可用技能列表 (Discovery 元数据)                                            │
│ ├─ 已激活技能内容                                                             │
│ └─ 工具定义                                                                   │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ LAYER 2: Memory Context (情节记忆)                                           │
│ ├─ 会话摘要列表 (Session Summaries)                                          │
│ │   └─ 每轮会话结束时生成的 LLM 摘要                                          │
│ └─ 关键事实列表 (Key Facts)                                                  │
│     └─ 用户偏好、项目状态、重要决策等结构化信息                                │
│                                                                              │
│ 长度控制: 固定数量，最近 N 条摘要 + M 条事实                                   │
│ 来源: Episodic Memory Store                                                  │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ LAYER 3: Working Context (工作记忆 + 聊天历史)                                │
│ ├─ 当前 Agent 的最近交互 (含 tool_results)                                    │
│ │   └─ 最近 K 条完整交互记录                                                  │
│ └─ ChatRoom 最近消息 (全局视图)                                              │
│     └─ 最近 P 条聊天室消息 (其他 Agent + 人类)                                │
│                                                                              │
│ 长度控制: K <= 10, P <= 20                                                    │
│ 来源: Working Memory Store + ChatRoom.get_history()                          │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ LAYER 4: Immediate Context (即时上下文)                                       │
│ ├─ 当前用户消息                                                               │
│ └─ 进行中的工具调用链 (如有)                                                  │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 1.2 信息压缩策略

| 层级 | 内容 | 压缩策略 | 最大长度 |
|------|------|---------|---------|
| L1 | 角色定义、技能、工具 | 不压缩 | 无限制 |
| L2 | 会话摘要 | 取最近 N 条 | 5 条摘要 |
| L2 | 关键事实 | 取最近 M 条 | 10 条事实 |
| L3 | 工作记忆 | 滚动窗口，保留最近 K 条 | 10 条交互 |
| L3 | ChatRoom 历史 | 固定取最近 P 条 | 20 条消息 |
| L4 | 当前消息 | 不压缩 | 完整保留 |

### 1.3 Context 注入格式

```
[系统指令]
你是 {name}，一个 {role}...
[可用技能]
...

[历史记忆]
## 过往会话摘要
- [2024-01-15] 用户创建了项目 Minions，使用 Python + Docker 技术栈
- [2024-01-16] 完成了 Skill 系统的开发和测试

## 已知事实
- 用户偏好: 使用 Python 3.11+
- 项目名称: Minions
- 当前阶段: Milestone 3

[最近工作记录]
1. [用户] 帮我检查一下配置文件
2. [Agent] 执行: cat config.yaml
   结果: [配置内容...]
3. [Agent] 配置检查完成，数据库连接配置正确

[聊天室历史]
[客服] 10:30: 有用户报告登录问题
[项目经理] 10:32: @开发-A 请排查一下

[当前消息]
[用户] 11:00: 找到问题了吗？
```

---

## 2. 触发时机

### 2.1 Memory 生命周期

```
AGENT_START
    │
    ▼
┌─────────────────┐
│  Load Episodic  │ ◀── 从文件加载会话摘要和关键事实
│  Memory         │
└─────────────────┘
    │
    ▼
进入消息处理循环
    │
    ├──▶ INTERACTION_RECEIVED (收到新交互)
    │       ├── 存入 Working Memory (立即)
    │       └── 触发条件检查 (是否生成摘要)
    │
    ├──▶ SESSION_SUMMARY_TRIGGER (会话摘要触发)
    │       ├── 条件1: Working Memory 达到 10 条交互
    │       ├── 条件2: 会话空闲超过 30 分钟
    │       ├── 条件3: LLM 主动调用 generateSummary 工具
    │       └── 条件4: 用户发送 /summary 命令
    │
    └──▶ AGENT_SHUTDOWN
            └── 强制生成会话摘要并保存
```

### 2.2 各动作触发条件

| 动作 | 触发条件 | 优先级 |
|------|---------|--------|
| **加载记忆** | Agent 初始化时 | 必须 |
| **存储交互** | 每次交互完成后 | 必须 |
| **生成摘要** | 窗口满(10条) / 空闲30分钟 / LLM主动 / 用户命令 | 推荐 |
| **归档整理** | Agent Shutdown 时 | 必须 |
| **遗忘** | 用户显式命令 / LLM 主动调用 | 可选 |

---

## 3. 触发主体

### 3.1 权限矩阵

| 动作 | Agent System | LLM | User | 说明 |
|------|-------------|-----|------|------|
| 读取记忆 | ✅ 自动 | ❌ | ✅ /history 命令 | System 构建 Context 时自动读取 |
| 存储交互 | ✅ 自动 | ❌ | ❌ | 交互完成后 System 自动存储 |
| 生成摘要 | ✅ 自动 | ✅ 工具 | ✅ /summary | 满足条件时 System 触发；LLM/User 可主动触发 |
| 标记重要 | ❌ | ✅ 工具 | ❌ | LLM 可标记某条记忆为高优先级 |
| 遗忘/删除 | ❌ | ✅ 工具 | ✅ /forget | LLM 发现错误时可遗忘；用户可强制遗忘 |

### 3.2 触发说明

- **System 自动处理**：高频、确定性的操作（构建 Context、存储交互）
- **LLM 处理**：语义层面的操作（判断重要性、发现错误时遗忘）
- **User 控制**：隐私相关和紧急操作（查看历史、强制遗忘）

---

## 4. 数据结构

### 4.1 文件存储结构

```
data/agents/{agent_id}/
├── memory/
│   ├── config.json              # Memory 配置
│   ├── working/
│   │   └── interactions.jsonl   # 工作记忆（原始交互）
│   └── episodic/
│       ├── summaries.jsonl      # 会话摘要
│       └── facts.jsonl          # 关键事实
```

### 4.2 Working Memory - 原始交互

存储文件: `working/interactions.jsonl`

```python
{
    "id": "uuid",
    "timestamp": "2024-01-20T10:30:00",
    "session_id": "session_uuid",
    
    # 输入
    "message": {
        "id": "msg_uuid",
        "sender_id": "human",
        "sender_name": "用户",
        "content": "帮我检查配置文件"
    },
    
    # 输出
    "response": {
        "content": "我来检查一下...",
        "finish_reason": "tool_calls"
    },
    
    # 工具调用过程
    "tool_calls": [
        {
            "id": "call_1",
            "function": "executeBashCommand",
            "arguments": {"command": "cat config.yaml"}
        }
    ],
    "tool_results": [
        {
            "tool_call_id": "call_1",
            "exit_code": 0,
            "stdout": "[配置内容]",
            "stderr": ""
        }
    ],
    
    # 元数据
    "importance_score": 0.5,      # 重要性评分 (0-1)
    "tags": ["config", "check"]   # 标签
}
```

### 4.3 Episodic Memory - 会话摘要

存储文件: `episodic/summaries.jsonl`

```python
{
    "id": "uuid",
    "timestamp": "2024-01-20T11:00:00",
    "session_id": "session_uuid",
    
    # 摘要内容 (由 LLM 生成)
    "summary_text": "用户请求检查项目配置文件，Agent 执行了 cat config.yaml，
                     确认数据库连接配置正确，无异常发现。",
    
    # 结构化信息
    "key_decisions": [],          # 本会话的关键决策
    "action_items": [],           # 待办事项
    "participants": ["用户"],      # 参与者
    
    # 来源追溯
    "source_interactions": ["interaction_id_1", "interaction_id_2"],
    "interaction_count": 10,      # 包含多少条交互
    
    # 检索用关键词 (简单场景下用于关键词匹配)
    "keywords": ["config", "yaml", "database"]
}
```

### 4.4 Episodic Memory - 关键事实

存储文件: `episodic/facts.jsonl`

```python
{
    "id": "uuid",
    "timestamp": "2024-01-20T11:00:00",
    "session_id": "session_uuid",
    
    # 事实内容
    "fact_type": "USER_PREFERENCE",  # 类型: USER_PREFERENCE, PROJECT_STATE, TECH_STACK
    "subject": "用户",
    "predicate": "使用",
    "object": "Python 3.11+",
    
    # 额外描述
    "description": "用户明确要求使用 Python 3.11 或更高版本",
    
    # 元数据
    "confidence": 0.9,             # 置信度 (0-1)
    "source_interaction": "uuid",  # 来源交互 ID
    "version": 1,                  # 版本号 (支持事实更新)
    "superseded_by": null          # 被哪个新事实取代
}
```

### 4.5 Memory Config

存储文件: `memory/config.json`

```python
{
    "version": "1.0",
    "agent_id": "dev-001",
    
    # Working Memory 配置
    "working": {
        "max_items": 10,           # 最大保留交互数
        "summary_window": 10       # 多少条交互生成摘要
    },
    
    # Episodic Memory 配置
    "episodic": {
        "max_summaries": 20,       # 最大保留摘要数
        "max_facts": 50            # 最大保留事实数
    },
    
    # Context 构建配置
    "context": {
        "max_summaries_in_context": 5,    # Context 中最多包含的摘要数
        "max_facts_in_context": 10,       # Context 中最多包含的事实数
        "max_working_in_context": 5,      # Context 中最多包含的工作记忆数
        "max_chat_history": 20            # 从 ChatRoom 获取的历史消息数
    },
    
    # 触发配置
    "triggers": {
        "idle_timeout_minutes": 30   # 空闲多久触发摘要生成
    }
}
```

---

## 5. API 设计

### 5.1 MemoryStore 抽象接口

```python
class MemoryStore(ABC):
    """记忆存储抽象基类，支持不同后端实现"""
    
    # ===== 基础 CRUD =====
    @abstractmethod
    async def add(self, item: Dict) -> str: 
        """添加记忆项，返回 ID"""
        pass
    
    @abstractmethod
    async def get(self, id: str) -> Optional[Dict]: 
        """根据 ID 获取记忆"""
        pass
    
    @abstractmethod
    async def get_recent(self, limit: int = 10) -> List[Dict]: 
        """获取最近 N 条记忆"""
        pass
    
    @abstractmethod
    async def delete(self, id: str) -> bool: 
        """删除记忆"""
        pass
    
    # ===== 检索 (简单场景) =====
    async def search_by_keyword(self, keywords: List[str], limit: int = 5) -> List[Dict]:
        """关键词搜索 (简单实现：匹配 keywords 字段)"""
        pass
    
    # ===== 维护 =====
    async def clear(self) -> None:
        """清空所有记忆"""
        pass
```

### 5.2 MemoryManager (Agent 使用的主接口)

```python
class MemoryManager:
    """Agent 的记忆管理器"""
    
    def __init__(self, agent_id: str, config: MemoryConfig):
        self.working = WorkingMemoryStore(agent_id)
        self.episodic = EpisodicMemoryStore(agent_id)
    
    # ===== System 自动调用 =====
    
    async def load(self) -> None:
        """Agent 启动时加载记忆"""
        pass
    
    async def on_interaction_complete(
        self,
        message: Message,
        response: Response,
        tool_results: List[ToolResult]
    ) -> None:
        """
        交互完成后自动存储
        触发: 每次 Agent 完成一次响应后
        主体: Agent System
        """
        pass
    
    async def build_context_blocks(
        self,
        current_message: Message
    ) -> List[ContextBlock]:
        """
        构建 Context 记忆块
        触发: 每次 _build_context() 时
        主体: Agent System
        
        Returns:
            按优先级排序的 ContextBlock 列表
        """
        pass
    
    async def maybe_generate_summary(self) -> Optional[str]:
        """
        检查并生成会话摘要
        触发: 
            - System: Working Memory 达到阈值
            - System: 会话空闲超时
            - LLM: 调用 generate_summary 工具
            - User: /summary 命令
        """
        pass
    
    # ===== LLM 工具调用 =====
    
    async def remember_fact(
        self,
        fact: str,
        fact_type: str = "GENERAL",
        importance: float = 0.5
    ) -> str:
        """
        Tool: rememberFact
        触发: LLM 主动调用
        作用: 记录一个重要事实
        """
        pass
    
    async def search_memories(
        self,
        query: str,
        limit: int = 5
    ) -> str:
        """
        Tool: searchMemories
        触发: LLM 主动调用
        作用: 搜索相关记忆 (简单场景下使用关键词匹配)
        """
        pass
    
    async def forget(
        self,
        query: str,
        confirm: bool = False
    ) -> str:
        """
        Tool: forget
        触发:
            - LLM: 发现错误记忆时主动调用
            - User: /forget 命令
        作用: 遗忘/删除记忆
        """
        pass
    
    # ===== User 命令 =====
    
    async def get_history_text(self, limit: int = 20) -> str:
        """
        Command: /history
        触发: 用户命令
        作用: 查看原始交互历史
        """
        pass
    
    async def get_summary_text(self) -> str:
        """
        Command: /summary
        触发: 用户命令
        作用: 立即生成并查看会话摘要
        """
        pass
```

### 5.3 暴露给 LLM 的工具定义

```json
{
  "tools": [
    {
      "name": "rememberFact",
      "description": "记录一个重要事实或用户偏好，供将来参考",
      "parameters": {
        "fact": {
          "type": "string",
          "description": "要记录的事实内容，例如'用户喜欢使用Python'"
        },
        "fact_type": {
          "type": "string",
          "enum": ["USER_PREFERENCE", "PROJECT_STATE", "TECH_STACK", "DECISION", "GENERAL"],
          "description": "事实类别"
        },
        "importance": {
          "type": "number",
          "minimum": 0,
          "maximum": 1,
          "description": "重要性评分 0-1"
        }
      },
      "required": ["fact"]
    },
    {
      "name": "searchMemories",
      "description": "搜索历史记忆中的相关信息",
      "parameters": {
        "query": {
          "type": "string",
          "description": "搜索关键词，例如'上次说的数据库配置'"
        },
        "limit": {
          "type": "integer",
          "default": 5,
          "description": "返回结果数量"
        }
      },
      "required": ["query"]
    },
    {
      "name": "generateSummary",
      "description": "立即生成本轮会话的摘要",
      "parameters": {},
      "required": []
    },
    {
      "name": "forget",
      "description": "遗忘/删除错误的或过时的记忆",
      "parameters": {
        "query": {
          "type": "string",
          "description": "要遗忘的内容描述"
        },
        "confirm": {
          "type": "boolean",
          "default": false,
          "description": "是否确认删除"
        }
      },
      "required": ["query"]
    }
  ]
}
```

### 5.4 用户命令

| 命令 | 作用 | 示例 |
|------|------|------|
| `/history [N]` | 查看最近 N 条交互历史 | `/history 10` |
| `/summary` | 立即生成并显示会话摘要 | `/summary` |
| `/facts` | 查看所有记录的关键事实 | `/facts` |
| `/forget <描述>` | 遗忘匹配的记忆 | `/forget 关于测试的那个配置` |
| `/remember <内容>` | 手动记录事实 | `/remember 用户名字叫张三` |

---

## 6. Context Block 数据结构

用于在构建 Context 时传递记忆内容：

```python
@dataclass
class ContextBlock:
    """注入到 LLM Context 的记忆块"""
    
    block_type: Literal[
        "episodic_summaries",   # 会话摘要列表
        "key_facts",            # 关键事实列表
        "recent_working"        # 最近工作记忆
    ]
    
    priority: int              # 优先级（用于截断时排序）
    content: str               # 格式化后的文本
    source_ids: List[str]      # 来源记忆 ID（用于溯源）
    
    def to_openai_message(self) -> Dict:
        return {
            "role": "system",
            "content": f"[{self.block_type}]\n{self.content}",
            "name": "memory"
        }
```

---

## 7. 扩展性设计

### 7.1 策略接口（预留）

为后续 Benchmark 和优化预留接口：

```python
# 摘要生成策略
class SummarizationStrategy(ABC):
    @abstractmethod
    async def summarize(self, interactions: List[Interaction]) -> Summary: ...

# 检索策略 (当前使用关键词匹配，未来可替换为向量检索)
class RetrievalStrategy(ABC):
    @abstractmethod
    async def retrieve(self, query: str, memories: List[Memory]) -> List[Memory]: ...

# 遗忘策略
class ForgettingStrategy(ABC):
    @abstractmethod
    def should_forget(self, memory: Memory) -> bool: ...
```

### 7.2 配置驱动

通过 `memory/config.json` 驱动行为，便于 A/B 测试：

```json
{
  "version": "1.0",
  "strategies": {
    "summarizer": "simple_llm",
    "retriever": "keyword_match",
    "forgetting": "time_decay"
  },
  "parameters": {
    "summary_window": 10,
    "max_history": 100
  }
}
```

### 7.3 存储后端扩展

当前实现 `JSONLMemoryStore`，未来可扩展：
- `SQLiteMemoryStore`: 更好的查询性能
- `VectorMemoryStore`: 支持语义检索

---

## 8. 使用示例

### 8.1 正常对话流程

```
1. Agent 启动
   └── MemoryManager.load()
       ├── 加载 summaries.jsonl
       └── 加载 facts.jsonl

2. 用户发送消息: "帮我检查配置"
   
3. Agent._build_context()
   └── MemoryManager.build_context_blocks()
       ├── 添加最近 5 条摘要
       ├── 添加最近 10 条事实  
       └── 添加最近 5 条工作记忆

4. Agent 处理完成
   └── MemoryManager.on_interaction_complete()
       └── 存入 working/interactions.jsonl

5. 第 10 条交互完成后
   └── MemoryManager.maybe_generate_summary()
       ├── 读取 10 条工作记忆
       ├── 调用 LLM 生成摘要
       ├── 存入 episodic/summaries.jsonl
       └── 清空 working memory
```

### 8.2 跨会话记忆

```
会话 1:
  用户: "我叫张三，记下来"
  Agent: [rememberFact] 记录 "用户名字叫张三"
  └── 存入 episodic/facts.jsonl

[Agent 重启]

会话 2:
  用户: "我叫什么名字？"
  Agent: [build_context]
  └── 加载 facts.jsonl，包含 "用户名字叫张三"
  Agent: "您叫张三"
```

---

## 9. 注意事项

1. **文件并发**: 多个 Agent 同时写入各自目录，无冲突；但同一 Agent 的并发写入需要文件锁

2. **文件增长控制**: 
   - Working Memory: 固定 10 条，自动清理
   - Episodic Memory: 配置最大数量，超出时归档或删除最旧的

3. **错误处理**:
   - 文件读取失败: 视为空记忆，继续运行
   - 文件写入失败: 记录错误，不阻塞主流程

4. **隐私考虑**:
   - 所有记忆存储在本地文件
   - 用户可通过 `/forget` 命令删除特定记忆
   - 提供 `/export` 和 `/import` 命令备份记忆

---

## 10. 待决策事项

1. **摘要质量**: 当前使用 LLM 生成，是否需要提供简单版（拼接关键信息）作为备选？
2. **事实提取**: 是由 LLM 在生成摘要时自动提取，还是单独调用一次 LLM？
3. **关键词生成**: 当前存储时预生成，还是检索时动态计算？
4. **多会话关联**: 是否需要 Session ID 来关联同一用户的多次对话？
