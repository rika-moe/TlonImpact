# Agent & Position 加载流程

## 目录结构

```
config/
├── positions/          # 岗位配置
│   ├── customer_service.yaml
│   ├── developer.yaml
│   ├── operator.yaml
│   ├── product_manager.yaml
│   ├── project_manager.yaml
│   └── data_analyst.yaml
└── agents/             # 人员配置
    ├── gu-yanxi.yaml
    ├── jiang-yubai.yaml
    ├── lin-zhiyao.yaml
    ├── shen-qingyan.yaml
    ├── su-wanqing.yaml
    └── xie-yunshu.yaml
```

## 加载流程

### Phase 1: ServerApp 初始化

```
ServerApp.initialize()
    │
    ├─ Step 1: 加载 Positions
    │   PositionRegistry.load_from_directory(config/positions/)
    │       ├── 读取每个 YAML 文件
    │       ├── 解析为 Position 对象
    │       └── 存入 registry (内存)
    │
    ├─ Step 2: 创建 TlonServer (核心)
    │   TlonServer.__init__(position_registry=...)
    │
    ├─ Step 3: 初始化核心组件
    │   TlonServer.initialize()
    │       ├── 启动 EventBus
    │       ├── 创建 Congress (聊天室)
    │       └── 创建 AgentRegistry
    │
    ├─ Step 4: 加载并注册 Agents ⭐
    │   _load_agent_configs()
    │       └── _load_agents_from_directory(config/agents/)
    │           ├── 读取 lin-zhiyao.yaml → AgentConfig
    │           ├── 读取 su-wanqing.yaml → AgentConfig
    │           ├── 读取 gu-yanxi.yaml → AgentConfig
    │           ├── 读取 shen-qingyan.yaml → AgentConfig
    │           ├── 读取 xie-yunshu.yaml → AgentConfig
    │           └── 读取 jiang-yubai.yaml → AgentConfig
    │   
    │   register_agents([config1, config2, ...])
    │       └── AgentRegistry.register_all(configs)
    │           ├── AgentRegistry.register(config1)
    │           │   └── _configs["lin-zhiyao"] = config1
    │           │   └── _standby.add("lin-zhiyao")  ← 待机状态
    │           ├── AgentRegistry.register(config2)
    │           │   └── _configs["su-wanqing"] = config2
    │           │   └── _standby.add("su-wanqing")
    │           └── ... (共6个)
    │
    └─ Step 5: 初始化 Agents
        initialize_agents()
            └── AgentRegistry.initialize()
                ├── 订阅 MESSAGE_NEW 事件 (用于激活 lazy agents)
                └── 启动 resident agents (proactive=True)
                    └── activate(agent_id)
                        └── Agent.__init__(config)  ← 创建对象
                        └── Agent.initialize()      ← 启动 Docker、订阅事件
```

### Phase 2: Agent 激活流程

```
用户发送消息: @林知遥 你好
    │
    ├─ WebSocket → Server.send_message()
    ├─ Congress.send_message() 
    ├─ EventBus.publish(MESSAGE_NEW)
    │
    ├─ AgentRegistry._on_message_for_activation(event)
    │   ├── 检查 mentions: ["林知遥"] 
    │   ├── 匹配到 config.name == "林知遥"
    │   ├── 检查状态: _standby 中有 "lin-zhiyao"
    │   ├── 调用 activate("lin-zhiyao")
    │   │   └── Agent.__init__(config)     ← 创建 Agent 对象
    │   │       ├── LLMClient(config.llm)  ← 使用合并后的配置
    │   │       ├── DockerRuntime(config.docker)
    │   │       ├── MemoryManager(config.memory)
    │   │       └── TaskManager()
    │   │
    │   └── Agent.initialize()
    │       ├── docker_runtime.ensure_running()  ← 启动容器
    │       ├── memory.load()                    ← 加载记忆
    │       ├── skill_manager.discover_skills()  ← 发现技能
    │       ├── tool_executor.register_tools()   ← 注册工具
    │       ├── event_bus.subscribe(MESSAGE_NEW) ← 订阅消息
    │       └── _message_worker() 启动           ← 消息处理循环
    │
    └─ Agent._on_message(event) 重新投递触发消息
        └── _message_queue.put(message)
        └── _message_worker 处理
            └── _process_message(message)
                ├── _build_context()    ← 构建对话上下文
                ├── think()             ← 调用 LLM
                ├── execute tools       ← 执行工具
                └── send_message()      ← 发送回复
```

## 关键状态流转

### Agent 生命周期状态

```
AgentConfig (配置)
    │
    ▼ register()
STANDBY (待机)
    │  存储在: AgentRegistry._standby
    │
    ▼ activate() - 被 @mention 或 proactive 启动
INITIALIZING (初始化中)
    │  启动 Docker、加载记忆
    │
    ▼ initialize() 完成
ACTIVE (就绪)
    │  等待消息、处理消息
    │
    ▼ deactivate() 或 shutdown()
SHUTDOWN (关闭)
```

### AgentRegistry 内部存储

```python
AgentRegistry:
    _configs: Dict[str, AgentConfig]
        # "lin-zhiyao" -> AgentConfig(name="林知遥", position_id="developer")
        # "su-wanqing" -> AgentConfig(name="苏晚晴", position_id="product_manager")
        # ... 共6个
    
    _standby: Set[str]
        # {"lin-zhiyao", "su-wanqing", "gu-yanxi", ...}  (未激活的)
    
    _agents: Dict[str, Agent]
        # 只有激活后的 Agent 对象
        # 初始为空, @mention 后才会加入
    
    _resident_ids: Set[str]
        # proactive=True 的 position 对应的 agent IDs
        # 这些会在 server 启动时自动 activate()
```

## 为什么只看到林知遥？

### 正常现象

**默认情况下，所有 Agent 都处于 STANDBY（待机）状态**：
- Agent 配置已加载到 `AgentRegistry._configs`
- 但 Agent 对象 **尚未创建**
- 只有被 `@mention` 时才会创建并激活

### 查看所有已加载的 Agent

```bash
# TUI 中按 `a` 键可以看到所有 agents
Agents (6):
- 顾言蹊 (customer_service)
- 江予白 (operator)
- 林知遥 (developer) [已激活]
- 沈清晏 (project_manager)
- 苏晚晴 (product_manager)
- 谢云舒 (data_analyst)
```

### 激活其他 Agent

```
# 在 TUI 中 @ 任意 Agent
@苏晚晴 你好
@谢云舒 分析这个数据集
@顾言蹊 有问题咨询
```

### 让 Agent 自动启动

修改 position 配置：`proactive: true`

```yaml
# config/positions/customer_service.yaml
position:
  id: "customer_service"
  title: "客服"
  proactive: true  # ← 改为 true
```

这样属于该 position 的 Agent 会在 server 启动时自动激活。

## 配置读取链路

```
config/agents/lin-zhiyao.yaml
    │
    ▼ yaml.safe_load()
{'agent': {
    'id': 'lin-zhiyao',
    'name': '林知遥',
    'position_id': 'developer',
    'personality': '技术能力强...',
    'skills': ['file-operations', ...],
    'llm': {'model_name': 'kimi-k2.5', 'temperature': 0.7},
    'docker': {'image': 'ubuntu:22.04', 'memory_limit': '512m'}
}}
    │
    ▼ AgentConfig.from_dict()
AgentConfig(
    id='lin-zhiyao',
    name='林知遥',
    position_id='developer',
    llm=LLMConfig(model_name='kimi-k2.5', ...),  # 缺少 auth_token
    docker=DockerRuntimeConfig(image='ubuntu:22.04', ...),
    memory=MemoryConfig(...)  # 自动创建
)
    │
    ▼ Agent.__init__(config)
    # 合并 LLM 配置
    llm_config = _merge_llm_config(config.llm, settings)
    # → LLMConfig(
    #     model_name='kimi-k2.5',      # 来自 YAML
    #     auth_token='sk-...',         # 来自 settings (env)
    #     base_url='https://...',      # 来自 settings (env)
    #   )
    
    self.llm_client = LLMClient(llm_config)
```

---

*最后更新: 2026-03-13*
