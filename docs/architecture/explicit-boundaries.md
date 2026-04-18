# 显式的架构边界 (Explicit Architecture Boundaries)

> 架构边界是系统的"宪法"——看不见的时候最容易被违反。

---

## 什么是架构边界？

架构边界是系统不同部分之间的**契约**和**约束**:

```
┌─────────────────────────────────────────────────────────────┐
│                      架构边界 = 规则 + 机制                    │
├─────────────────────────────────────────────────────────────┤
│  规则 (Rules): 什么可以做，什么不可以做                         │
│  机制 (Mechanism): 如何强制执行这些规则                        │
└─────────────────────────────────────────────────────────────┘
```

### 隐式边界 vs 显式边界

| 维度 | 隐式边界 (Implicit) | 显式边界 (Explicit) |
|------|---------------------|---------------------|
| **存在形式** | "大家都知道" | 代码 + 工具 + 文档 |
| **传播方式** | 口口相传 | 自动化检查 |
| **违反成本** | 后期重构 | 立即失败 |
| **维护负担** | 高 (知识在脑中) | 低 (知识在代码中) |
| **一致性** | 随时间衰减 | 随时间保持 |

---

## 具体对比示例

### 1. 依赖注入规则

#### ❌ 隐式边界
```python
# config_service.py
class ConfigService:
    def __init__(self, config_dir: Path):
        self._config_dir = config_dir

# 规则存在吗？存在，在代码审查者的脑中
# 新成员怎么知道？看之前的代码、问同事
# 违反会怎样？审查时发现，或者发现不了
```

#### ✅ 显式边界
```python
# base/component.py
class Component(ABC):
    """所有 ServerApp 组件的基类。
    
    设计原则：
    1. Constructor Injection Only
       All dependencies must be declared in __init__ parameters.
       No setter methods, no property injection, no runtime replacement.
    """
    
    @abstractmethod
    async def initialize(self) -> None: ...

# config_service.py
class ConfigService(Component, Device, AgentInfoProvider):  # <-- 显式：我是 Component
    def __init__(self, event_bus: EventBus, config_dir: Path | None = None):
        super().__init__(name="ConfigService")
        self._event_bus = event_bus
        self._config_dir = config_dir  # 构造函数注入

# 工具检查
$ python scripts/check_architecture.py
✅ Constructor injection only (no setters)
```

### 2. 组件通信规则

#### ❌ 隐式边界
```python
# agent.py
class Agent:
    def __init__(self, config, event_bus):
        self.config = config
        self.event_bus = event_bus
        
    async def send_message(self, content: str):
        # 规则：应该通过 EventBus 发消息
        # 但也可以直接调用其他组件的方法...
        some_service.process_directly(content)  # 没人阻止我
```

#### ✅ 显式边界
```python
# base/component.py
class Component(ABC):
    """
    2. EventBus for Communication
       Components communicate indirectly via EventBus publish/subscribe.
       Direct method calls between components should be minimal and
documented (e.g., Facade pattern).
    """

# agent.py
class Agent(Component):
    def __init__(self, config, event_bus: EventBus):
        super().__init__(name="Agent")
        self.event_bus = event_bus  # 显式：我只能通过 EventBus
    
    async def send_message(self, content: str):
        # 显式：EventBus 是我唯一的通信手段
        await self.event_bus.publish(Event(
            type=EventType.MESSAGE_NEW,
            payload=message,
            source=self.id,
        ))
        
        # 如果我想直接调用？
        # some_service.process_directly()  # 没有 this，代码审查会阻止
```

### 3. DAG 无环规则

#### ❌ 隐式边界
```python
# A.py
class A:
    def __init__(self):
        self.b = B()  # A -> B

# B.py
class B:
    def __init__(self):
        self.a = A()  # B -> A (循环！但没人检查)
```

#### ✅ 显式边界
```python
# 架构检查工具
$ python scripts/check_architecture.py --check-dag
Checking component dependencies...
❌ CIRCULAR_DEPENDENCY detected:
   A -> B -> A
   
   Suggestion: Use EventBus for decoupling

# ServerApp 显式组装
class ServerApp:
    async def initialize(self) -> None:
        # 顺序很重要，这是显式的 DAG 拓扑序
        event_bus = EventBus()           # Level 0: 基础设施
        message_store = MessageStore()   # Level 0: 基础设施
        message_router = MessageRouter(  # Level 1: 依赖 Level 0
            event_bus=event_bus,
            message_store=message_store,
        )
```

---

## 显式边界的三个层次

```
┌─────────────────────────────────────────────────────────────┐
│  Level 3: 工具化 (Tools)                                     │
│  - 自动化检查脚本                                            │
│  - CI/CD 集成                                               │
│  - IDE 插件                                                 │
├─────────────────────────────────────────────────────────────┤
│  Level 2: 代码化 (Code)                                      │
│  - 抽象基类 (Component)                                      │
│  - 类型注解                                                │
│  - 命名约定                                                │
├─────────────────────────────────────────────────────────────┤
│  Level 1: 文档化 (Docs)                                      │
│  - 架构文档                                                │
│  - 代码注释                                                │
│  - 示例代码                                                │
└─────────────────────────────────────────────────────────────┘
```

### Level 1: 文档化 (最弱但必要)

```markdown
<!-- docs/architecture/dag-components.md -->
## 规则：构造函数注入唯一

所有组件依赖必须通过构造函数注入。

✅ DO:
```python
class MessageRouter(Component):
    def __init__(self, event_bus: EventBus):
        self._event_bus = event_bus
```

❌ DON'T:
```python
class MessageRouter(Component):
    def set_event_bus(self, event_bus: EventBus):  # 禁止！
        self._event_bus = event_bus
```
```

**问题**: 开发者可能不看文档，看了也可能忘记。

### Level 2: 代码化 (中等强度)

```python
# 抽象基类强制接口
class Component(ABC):
    @abstractmethod
    async def initialize(self) -> None: ...
    
    @abstractmethod  
    async def shutdown(self) -> None: ...

# 类型系统强制依赖显式
class MessageRouter(Component):
    def __init__(
        self,
        event_bus: EventBus,      # 类型注解 = 显式契约
        message_store: MessageStore,
    ):
        ...

# 命名约定
_session_manager   # _prefix = 私有，不应被外部访问
event_bus          # 无 _prefix = 可以访问但不应修改
```

**问题**: 编译器/解释器无法检查所有架构规则。

### Level 3: 工具化 (最强)

```python
# scripts/check_architecture.py

def check_setter_injection(tree: ast.AST, file: Path):
    """检查 setter 注入。"""
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            if node.name.startswith('set_'):
                # 🚨 架构边界违反！
                raise ArchitectureViolation(
                    rule="SETTER_INJECTION",
                    message=f"{file}:{node.lineno} 使用 setter 注入",
                    suggestion="使用构造函数注入"
                )

# CI 集成
# .github/workflows/architecture.yml
- name: Check Architecture
  run: |
    python scripts/check_architecture.py --ci
    # 如果违反，CI 失败，PR 无法合并
```

**价值**: 违反即失败，无法绕过。

---

## 我们的显式边界实现

### 1. 文档层

```
docs/architecture/
├── dag-components.md      # 架构原则文档
├── explicit-boundaries.md # 本文件
└── README.md
```

### 2. 代码层

```python
# src/tlon/base/component.py
class Component(ABC):
    """显式基类，声明所有组件必须遵守的契约。"""
    
    def __init__(self, name: Optional[str] = None):
        """子类必须调用 super().__init__()。"""
        self._name = name or self.__class__.__name__
        self._initialized = False  # 显式状态跟踪
    
    @abstractmethod
    async def initialize(self) -> None:
        """显式生命周期方法，必须在依赖注入后调用。"""
        pass
    
    @abstractmethod
    async def shutdown(self) -> None:
        """显式生命周期方法，必须优雅关闭。"""
        pass
```

### 3. 工具层

```bash
# 本地检查
$ python scripts/check_architecture.py src/tlon

# CI 集成  
$ python scripts/check_architecture.py --ci
```

---

## 为什么显式边界如此重要？

### 场景：6个月后，新成员加入

#### 没有显式边界
```
新成员: "我可以用 setter 注入 EventBus 吗？"
老员工: "最好不要，但我们之前有人这么做过..."
新成员: "那到底行不行？"
老员工: "应该不行，但我不确定"

# 结果: 新成员尝试 setter 注入
# 代码审查时: "这个为什么不行？那个代码也有啊"
# 技术债务积累...
```

#### 有显式边界
```
新成员: 尝试写 setter 方法
IDE: 🚨 警告 - 架构检查失败
CI: ❌ 构建失败 - SETTER_INJECTION 违反

新成员查看:
  - 文档: docs/architecture/dag-components.md
  - 代码: class MessageRouter(Component):
  - 工具: python scripts/check_architecture.py
  
# 结果: 问题在编写时就解决
# 一致性保持
```

### 场景：架构腐化检测

```python
# 第1个月: 干净的代码
class MessageRouter(Component):
    def __init__(self, event_bus: EventBus):
        self._event_bus = event_bus

# 第3个月: "临时"修复
class MessageRouter(Component):
    def __init__(self, event_bus: EventBus):
        self._event_bus = event_bus
    
    def replace_event_bus(self, new_bus):  # 紧急修复！
        self._event_bus = new_bus

# 第6个月: 越来越多...
def update_event_bus(self, bus): pass
def switch_event_bus(self, bus): pass

# 没有显式边界: 慢慢腐化，没人察觉
# 有显式边界: 🚨 CI 失败，立即发现
```

---

## 架构边界的 ROI

| 阶段 | 隐式边界成本 | 显式边界成本 |
|------|-------------|-------------|
| 初期开发 | 低 | 中 (写工具) |
| 日常开发 | 中 (口头沟通) | 低 (自动检查) |
| 新人入职 | 高 (1对1培训) | 低 (文档+工具) |
| 架构腐化 | 高 (重构) | 低 (预防) |
| **总计** | **高** | **低** |

---

## 总结

**显式的架构边界 = 规则文档化 + 约束代码化 + 检查自动化**

```
规则在哪里？
├── 脑中 ❌ (会忘记、会不同步)
├── 文档 ⚠️ (会不看、会过时)
├── 代码 ✅ (会编译、会报错)
└── 工具 ✅ (会阻止、会提醒)
```

**我们的实现**:
1. ✅ 文档: `docs/architecture/dag-components.md`
2. ✅ 代码: `class Component(ABC)`
3. ✅ 工具: `scripts/check_architecture.py`

**效果**: 架构规则从"大家应该知道"变成了"违反即失败"。
