# Phase 4: Multi-Agent 系统设计方案

## 概述

本文档描述 Minions 多 Agent 协作系统的详细设计方案。基于前三个里程碑（单 Agent、Skills 系统、Memory 系统）的基础，实现一个模拟真实软件工程团队的多 Agent 协作环境。

---

## 1. 核心设计原则

1. **显式通信**：Agent 间通过 `@提及` 显式通信，避免隐式广播造成的混乱
2. **Token 效率**：只有被@或主动职责范围内的 Agent 才响应，减少无效 Token 消耗
3. **独立隔离**：每个 Agent 保持独立的 Docker 容器、长时记忆和 Skills
4. **自我节制**：依赖 System Prompt 规范约束 Agent 行为，不设计复杂的状态跟踪机制
5. **渐进启动**：客服常驻，其他 Agent 延迟启动，平衡响应速度与资源消耗

---

## 2. 响应规则设计

### 2.1 分级响应策略

| 角色 | 用户消息(无@) | 被@时 | 说明 |
|------|-------------|-------|------|
| **客服** | ✅ 主动响应 | ✅ 响应 | 首响应角色，负责问题分类和初步诊断 |
| **项目经理** | ❌ 不响应 | ✅ 响应 | 负责协调资源、进度把控 |
| **开发-A** | ❌ 不响应 | ✅ 响应 | 代码编写、Bug 修复 |
| **开发-B** | ❌ 不响应 | ✅ 响应 | 代码编写、Bug 修复 |
| **运维** | ❌ 不响应 | ✅ 响应 | 系统部署、环境配置、故障处理 |
| **产品经理** | ❌ 不响应 | ✅ 响应 | 需求分析、方案评审 |

### 2.2 实现逻辑

```python
def _should_respond(self, message: Message) -> bool:
    # 忽略自己的消息
    if message.sender_id == self.id:
        return False
    
    # 被@了，必须响应
    if self._is_mentioned(message):
        return True
    
    # 客服特殊规则：用户直接输入（无@）时主动响应
    if (self.config.role.name == "customer_service" and 
        message.sender_type == "human" and 
        not message.mentions):
        return True
    
    # 其他情况不响应
    return False
```

### 2.3 响应规则优势

- **减少 Token 消耗**：只有相关 Agent 参与对话
- **避免混乱**：不会出现所有 Agent 同时响应的情况
- **清晰分工**：客服作为"前台"，其他角色作为"后台专家"

---

## 3. Agent 启动策略

### 3.1 启动模式

| 启动模式 | 角色 | 说明 |
|---------|------|------|
| **常驻启动** | 客服 | 应用启动时立即初始化，保持 Docker 容器运行 |
| **延迟启动** | 项目经理、开发、运维、产品经理 | 首次被@时才初始化（创建 Docker、加载记忆） |
| **空闲回收** | 延迟启动的角色 | 30 分钟无活动后停止 Docker（保留记忆数据） |

### 3.2 启动流程

```
Application.initialize()
  ├── EventBus 启动
  ├── ChatRoom 初始化
  ├── 人类用户加入
  ├── 客服 Agent 立即启动（常驻）
  │     ├── Docker 容器创建
  │     ├── Memory 加载
  │     └── 订阅事件
  └── 其他角色进入"待机池"（未激活）
        └── 只有配置对象，没有实例

当收到消息 "@开发-A 帮我看看代码"
  ├── ChatRoom 发送消息
  ├── EventBus 广播 MESSAGE_NEW
  ├── 开发-A 检测到被@，但实例不存在
  ├── AgentRegistry 延迟创建开发-A 实例
  │     ├── 初始化 Docker、Memory
  │     └── 首次加载需要 3-5 秒
  └── 开发-A 处理消息
```

### 3.3 用户体验

首次唤醒延迟启动的 Agent 时，系统显示：

```
[系统] 正在唤醒 开发-A...
[开发-A] 来了，让我看看代码问题
```

---

## 4. 并发与消息顺序

### 4.1 并发模型

```
┌─────────────────────────────────────────────────────────────┐
│                      Event Bus (中央队列)                     │
│                    （组织层 Organization）                    │
└──────────────┬───────────────────────┬──────────────────────┘
               │                       │
         ┌─────▼─────┐           ┌─────▼─────┐
         │  客服      │           │  开发-A   │  ← 并行执行
         │ (独立Docker)│          │ (独立Docker)│
         └─────┬─────┘           └─────┬─────┘
               │                       │
    ┌──────────┼──────────┐   ┌───────┼───────┐
    ▼          ▼          ▼   ▼       ▼       ▼
  消息1      消息2      消息3  消息1   消息2   消息3
  (串行)    (等待)    (等待)  (串行)  (等待)  (等待)
               │                       │
               ▼                       ▼
┌─────────────────────────┐   ┌─────────────────────────┐
│     🔒 Sandbox 层        │   │     🔒 Sandbox 层        │
│  tlon-agent-cs-001      │   │  tlon-agent-dev-001     │
│  (隔离容器执行环境)       │   │  (隔离容器执行环境)       │
└─────────────────────────┘   └─────────────────────────┘
```

### 4.2 处理规则

1. **Agent 间**：完全并行，各自独立 Docker 容器
2. **Agent 内**：消息队列串行处理，先进先出
3. **不等待**：fire-and-forget 风格，不阻塞其他 Agent

### 4.3 单 Agent 消息队列实现

```python
class Agent:
    def __init__(self, ...):
        self._message_queue = asyncio.Queue()
        self._processing = False
        self._worker_task = None
    
    async def initialize(self):
        # 启动消息处理 worker
        self._worker_task = asyncio.create_task(self._message_worker())
    
    async def _on_message(self, event: Event):
        # 只把消息放入队列，立即返回
        message = event.payload
        if self._should_respond(message):
            await self._message_queue.put(message)
    
    async def _message_worker(self):
        """串行处理消息"""
        while True:
            message = await self._message_queue.get()
            try:
                await self._process_message(message)
            except Exception as e:
                logger.error(f"Error processing message: {e}")
            finally:
                self._message_queue.task_done()
```

---

## 5. 循环@解决方案

### 5.1 设计原则

不设计复杂的状态机或全局跟踪机制，完全依靠 **System Prompt 规范** 约束 Agent 行为。

### 5.2 通用节制规则（所有 Agent）

```markdown
## 对话节制规则

1. **收到任务时**：确认收到即可，不要重复@对方
   - ✅ "收到，我来处理"
   - ❌ "@客服 收到，我来处理"  （不要回@发送者）

2. **任务完成时**：向任务分派者汇报
   - 如果 @项目经理 派的活 → 完成后 @项目经理
   - 如果 @人类 直接派的活 → 完成后 @人类

3. **避免无意义回应**：
   - 不要对 "收到"、"好的"、"谢谢" 进行回复
   - 如果上一条是确认类内容，不需要再回复

4. **循环检测**：
   - 如果你发现你和另一个 Agent 来回@了超过 2 轮，停止回复
   - 让 @人类 来判断或介入
```

### 5.3 角色特定规则

**客服规则**：
```markdown
## 客服协作规则

1. **首响应原则**：用户进来时，你是第一个响应的
2. **转派原则**：
   - 技术问题 → @开发-A 或 @开发-B
   - 部署问题 → @运维
   - 需求问题 → @产品经理
   - 大问题协调 → @项目经理
3. **转派后**：不要持续跟进，相信同事会处理
   - 除非用户催促或被明确要求跟进
```

**开发/运维规则**：
```markdown
## 开发协作规则

1. **被动响应原则**：你不主动说话，只在被@时响应
2. **协作原则**：
   - 需要其他人协助时，@具体的人
   - 不要@ "所有人"
3. **完成汇报**：任务完成后，@最初给你派活的人
```

---

## 6. 角色认知与团队协作

### 6.1 核心设计

所有 Agent 的 System Prompt 都注入**团队信息**，让他们知道：
- 有哪些同事
- 每个同事的职责是什么
- 什么时候应该@谁

### 6.2 System Prompt 团队信息块

```markdown
# 你是 开发-A

## 你的角色
- 职责：负责代码编写和 Bug 修复
- 性格：技术能力强、注重细节、喜欢钻研
- 响应模式：仅在被@时响应，不主动插话

## 你的团队（要知道可以@谁）
┌──────────┬─────────┬─────────────────────────────┐
│ 角色      │  名字    │ 职责                         │
├──────────┼─────────┼─────────────────────────────┤
│ 客服      │  客服    │ 首响应用户，问题分类与安抚    │
│ 项目经理  │  项目经理 │ 任务协调、进度把控、资源分配  │
│ 开发      │  开发-A  │ 就是你，代码编写和 Bug 修复   │
│ 开发      │  开发-B  │ 你的搭档，可以分工协作        │
│ 运维      │  运维    │ 系统部署、环境配置、故障处理  │
│ 产品经理  │  产品经理 │ 需求分析、方案评审            │
└──────────┴─────────┴─────────────────────────────┘

## 协作指南
- 遇到部署问题 → @运维
- 需求不清楚 → @产品经理
- 需要协调资源 → @项目经理
- 完成用户任务后 → @人类 汇报结果
```

### 6.3 动态参与者列表

Agent 启动时从 ChatRoom 获取当前所有参与者，确保信息实时准确。

---

## 7. 协作流程示例

### 场景：用户报告系统故障

```
[人类] @客服 系统登录不上了，急！

[客服] 收到，我来看一下...
       初步判断可能是数据库或服务器问题，我帮您转给专业同事处理。
       @运维 @开发-A 请帮忙排查登录故障

[运维] [系统自动启动中...]
[开发-A] [系统自动启动中...]

[运维] 我在检查服务器状态...
       [executeBash] df -h && systemctl status nginx
       服务器资源正常，nginx 运行正常

[开发-A] 我检查数据库连接...
        [executeBash] python3 check_db.py
        发现数据库连接池满了，正在重启...
        已重启，连接恢复正常

[开发-A] @项目经理 @人类 问题已修复，数据库连接池满导致的，已重启恢复

[项目经理] @人类 故障已处理完成，开发-A 已修复数据库连接问题
           建议后续增加连接池监控，避免再次发生
           @运维 麻烦加个监控告警

[运维] 收到，我配置一下监控
```

---

## 8. 配置文件设计

### 8.1 agents.yaml（5 角色配置）

```yaml
agents:
  # 客服 - 常驻启动，主动响应
  - id: "cs-001"
    name: "客服"
    role:
      name: "customer_service"
      description: "客户服务专家，负责首响应、问题分类和初步诊断"
      personality: "友好、耐心、善于沟通"
      proactive: true  # 主动响应用户消息
    llm:
      model_name: "gpt-4"
      temperature: 0.7
    docker:
      image: "ubuntu:22.04"
      memory_limit: "512m"
    skills:
      - plan-i
    
  # 项目经理 - 延迟启动
  - id: "pm-001"
    name: "项目经理"
    role:
      name: "project_manager"
      description: "负责任务协调、进度把控和资源分配"
      personality: "统筹能力强、善于沟通、结果导向"
      proactive: false
    llm:
      model_name: "gpt-4"
      temperature: 0.7
    docker:
      image: "ubuntu:22.04"
      memory_limit: "512m"
    skills:
      - plan-i
      
  # 开发-A - 延迟启动
  - id: "dev-001"
    name: "开发-A"
    role:
      name: "developer"
      description: "负责代码编写和 Bug 修复"
      personality: "技术能力强、注重细节、喜欢钻研"
      proactive: false
    llm:
      model_name: "gpt-4"
      temperature: 0.7
    docker:
      image: "ubuntu:22.04"
      memory_limit: "1g"
    skills:
      - file-operations
      - git-operations
      - csv-pipeline
      
  # 开发-B - 延迟启动
  - id: "dev-002"
    name: "开发-B"
    role:
      name: "developer"
      description: "负责代码编写和 Bug 修复"
      personality: "思维活跃、善于创新、喜欢挑战"
      proactive: false
    llm:
      model_name: "gpt-4"
      temperature: 0.7
    docker:
      image: "ubuntu:22.04"
      memory_limit: "1g"
    skills:
      - file-operations
      - git-operations
      - frontend-design
      
  # 运维 - 延迟启动
  - id: "ops-001"
    name: "运维"
    role:
      name: "operator"
      description: "负责系统部署、环境配置和故障处理"
      personality: "谨慎细致、应急响应能力强"
      proactive: false
    llm:
      model_name: "gpt-4"
      temperature: 0.7
    docker:
      image: "ubuntu:22.04"
      memory_limit: "512m"
    skills:
      - file-operations
      - git-operations
      
  # 产品经理 - 延迟启动
  - id: "prd-001"
    name: "产品经理"
    role:
      name: "product_manager"
      description: "负责需求分析、功能规划和方案评审"
      personality: "用户导向、逻辑清晰、善于梳理"
      proactive: false
    llm:
      model_name: "gpt-4"
      temperature: 0.7
    docker:
      image: "ubuntu:22.04"
      memory_limit: "512m"
    skills:
      - plan-i
      - frontend-design
```

---

## 9. 关键组件设计

### 9.1 AgentRegistry（Agent 注册表）

负责管理多个 Agent 的生命周期：

```python
class AgentRegistry:
    """管理所有 Agent 的注册、启动和停止。"""
    
    def __init__(self):
        self._configs: Dict[str, AgentConfig] = {}  # 所有配置
        self._agents: Dict[str, Agent] = {}         # 已激活的 Agent
        self._standby: Set[str] = set()             # 待机中的 Agent ID
    
    def register(self, config: AgentConfig) -> None:
        """注册 Agent 配置（不启动）。"""
        self._configs[config.id] = config
        self._standby.add(config.id)
    
    async def activate(self, agent_id: str) -> Agent:
        """激活（启动）一个 Agent。"""
        if agent_id in self._agents:
            return self._agents[agent_id]
        
        config = self._configs[agent_id]
        agent = Agent(config, self.event_bus, self.chatroom)
        await agent.initialize()
        
        self._agents[agent_id] = agent
        self._standby.discard(agent_id)
        
        # 添加到 ChatRoom 参与者
        await self.chatroom.join(Participant(
            id=config.id,
            name=config.name,
            type="agent"
        ))
        
        return agent
    
    async def deactivate(self, agent_id: str) -> None:
        """停止一个 Agent（保留记忆）。"""
        if agent_id not in self._agents:
            return
        
        agent = self._agents.pop(agent_id)
        await agent.shutdown()
        self._standby.add(agent_id)
```

### 9.2 Application 启动流程调整

```python
class Application:
    async def initialize(self) -> None:
        # 1. 启动基础设施
        self.event_bus = EventBus()
        await self.event_bus.start()
        
        # 2. 创建 ChatRoom
        self.chatroom = ChatRoom(metadata, self.event_bus)
        await self.chatroom.initialize()
        
        # 3. 人类用户加入
        await self.chatroom.join(human_participant)
        
        # 4. 创建 AgentRegistry
        self.agent_registry = AgentRegistry(self.event_bus, self.chatroom)
        
        # 5. 加载所有 Agent 配置
        configs = load_agent_configs_from_yaml()
        for config in configs:
            self.agent_registry.register(config)
        
        # 6. 启动常驻 Agent（客服）
        cs_config = next(c for c in configs if c.role.name == "customer_service")
        await self.agent_registry.activate(cs_config.id)
        
        # 7. 其他 Agent 保持待机，等待被@时自动唤醒
        
        # 8. 启动 CLI
        self.cli_adapter = CLIAdapter()
        ...
```

---

## 10. 实现步骤

### Step 1: 配置扩展（1 天）
- [ ] 修改 `config/agents.yaml` 支持 5 个角色完整配置
- [ ] 在 `AgentConfig` 中添加 `proactive` 字段
- [ ] 修改 `main.py` 使用新的多 Agent 配置加载

### Step 2: 响应规则（1 天）
- [ ] 修改 `Agent._should_respond()` 实现分级响应逻辑
- [ ] 客服主动响应，其他被动响应
- [ ] 测试验证：无@时只有客服响应

### Step 3: AgentRegistry（1-2 天）
- [ ] 创建 `AgentRegistry` 类管理 Agent 生命周期
- [ ] 实现待机池和延迟激活机制
- [ ] 客服常驻启动，其他按需激活

### Step 4: 消息队列（1 天）
- [ ] 为每个 Agent 添加内部 `asyncio.Queue` 消息队列
- [ ] 实现 `_message_worker()` 串行处理消息
- [ ] 确保单 Agent 内消息顺序，Agent 间并行

### Step 5: 团队协作提示词（1 天）
- [ ] 为 5 个角色编写完整 System Prompt 模板
- [ ] 实现 `_build_team_table()` 动态生成同事列表
- [ ] 注入对话节制规则到所有 Prompt

### Step 6: 集成测试（2 天）
- [ ] 测试多 Agent 协作场景（故障处理流程）
- [ ] 验证循环@是否能被提示词有效抑制
- [ ] 性能测试（5 Agent 同时运行稳定性）
- [ ] 测试延迟启动的唤醒提示

---

## 11. 风险与应对

| 风险 | 影响 | 应对策略 |
|------|------|---------|
| Agent 循环@ | 中 | 依赖提示词约束 + 10轮思考限制 |
| 延迟启动时间过长 | 低 | 首次启动显示"正在唤醒..."提示 |
| 5 个 Docker 资源占用 | 中 | 空闲回收机制，默认只常驻1个 |
| Agent @错人 | 低 | Prompt 中注入完整团队信息 |
| 消息顺序混乱 | 低 | Agent 内队列保证串行处理 |

---

## 12. 后续优化方向

1. **智能路由**：根据消息内容自动判断该@谁（不依赖客服分派）
2. **共享记忆**：关键信息（如用户偏好）在 Agent 间共享
3. **私聊机制**：Agent 间可以私下讨论再回复（当前所有消息公开）
4. **动态角色**：支持运行时添加/移除 Agent
5. **负载均衡**：多个同角色 Agent 的任务分配

---

## 附录：角色配置参考

### 客服
- **核心职责**：首响应、问题分类、用户安抚、初步诊断
- **响应模式**：主动 + 被动
- **典型话术**："收到，我来看一下"、"这个问题需要转给开发同事"
- **禁用行为**：执行代码、修改配置

### 项目经理
- **核心职责**：协调资源、进度把控、任务分派、结果汇总
- **响应模式**：被动（被@时）
- **典型话术**："@开发-A @开发-B 你们两个处理一下"
- **禁用行为**：直接执行技术操作

### 开发（A/B）
- **核心职责**：代码编写、Bug 修复、技术方案实现
- **响应模式**：被动（被@时）
- **典型话术**："我来检查..."、"问题已修复"
- **核心能力**：executeBashCommand、Git 操作、文件操作

### 运维
- **核心职责**：系统部署、环境配置、监控告警、故障处理
- **响应模式**：被动（被@时）
- **典型话术**："服务器状态正常"、"正在部署..."
- **核心能力**：Docker 操作、系统命令、网络诊断

### 产品经理
- **核心职责**：需求分析、功能规划、方案评审、用户调研
- **响应模式**：被动（被@时）
- **典型话术**："这个需求可以这样理解..."、"建议方案是..."
- **核心能力**：需求文档编写、流程设计
