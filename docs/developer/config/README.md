# Tlon 配置系统

> 显式配置，层级覆盖。

---

## 配置层级

```
┌─────────────────────────────────────────────────────────────────┐
│  Layer 1: 全局 Prompt 模板 (config/prompt_templates.yaml)        │
│  - 默认的 workflow、important_notes、discipline_rules          │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼ 被 Position 的 prompt_overrides 覆盖
┌─────────────────────────────────────────────────────────────────┐
│  Layer 2: Position 配置 (config/positions/{position_id}.yaml)    │
│  - 岗位基础信息 (id, title, jd, proactive, contact_when)        │
│  - 行为规则 (rules)                                             │
│  - 可选的 prompt_overrides (完全覆盖全局配置)                   │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼ 被 Agent 继承和扩展
┌─────────────────────────────────────────────────────────────────┐
│  Layer 3: Agent 配置 (config/agents/{agent_id}.yaml)             │
│  - Agent 基础信息 (id, name, position_id, personality)          │
│  - 运行时配置 (llm, docker, memory)                             │
│  - 技能列表 (skills) - 待废弃                                   │
│  - 响应策略 (respond_to_all)                                    │
└─────────────────────────────────────────────────────────────────┘
```

---

## 数据模型

以实际代码为准 (`src/tlon/base/config/models.py`):

```python
@dataclass
class AgentConfig:
    id: str
    name: str
    position_id: str              # 关联的 position ID
    personality: str = ""
    llm: LLMConfig = field(default_factory=LLMConfig)
    docker: DockerRuntimeConfig = field(default_factory=DockerRuntimeConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    skills: List[str] = field(default_factory=list)  # 待废弃
    respond_to_all: bool = False   # 是否主动响应

@dataclass
class LLMConfig:
    base_url: str = ""
    auth_token: str = ""
    model_name: str = "gpt-4o-mini"
    temperature: float = 0.7
    max_tokens: int = 4000

@dataclass
class DockerRuntimeConfig:
    image: str = "ubuntu:22.04"
    memory_limit: str = "512m"
    skills_dir: str = "/skills"
```

---

## 配置文件示例

### Agent 配置

```yaml
# config/agents/lin-zhiyao.yaml
agent:
  id: "lin-zhiyao"
  name: "林知遥"
  position_id: "developer"
  personality: "技术能力强、注重细节"
  llm:
    model_name: "kimi-k2.5"
    temperature: 0.7
  docker:
    image: "ubuntu:22.04"
    memory_limit: "512m"
  skills: []  # 待废弃
  respond_to_all: false
```

### Position 配置

```yaml
# config/positions/developer.yaml
position:
  id: "developer"
  type: "developer"
  title: "开发工程师"
  jd: |
    负责代码编写、Bug 修复和技术方案实现。
  proactive: false
  contact_when: "有技术问题时"
  rules:
    - "只在你被@时响应"
```

---

## 使用方式

```python
from tlon.base.config import ConfigLoader, ConfigSaver
from tlon.base.config.models import AgentConfig

# 加载配置
loader = ConfigLoader()
agent_config = loader.load_agent("lin-zhiyao")

# 保存配置
saver = ConfigSaver()
saver.save_agent(agent_config)
```

---

*配置系统基于显式依赖注入设计，所有配置通过 ConfigLoader/ConfigSaver 集中管理。*
