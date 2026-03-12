# Minions 配置模块架构设计

> 版本: 1.0  
> 日期: 2026-03-08  
> 状态: 设计中

---

## 1. 问题分析

### 1.1 当前问题

现有的配置管理分散在多个模块中：

```
minions/
├── agents/
│   ├── config.py          # AgentConfig 定义
│   └── agent_registry.py  # Agent 配置加载
├── positions/
│   ├── models.py          # Position 定义
│   └── registry.py        # Position 加载
└── wizard/
    ├── agent_store.py     # 读写 agent 配置
    └── position_store.py  # 读写 position 配置
```

**问题**:
1. **代码重复** - Wizard 和 AgentRegistry 都实现了配置加载/保存逻辑
2. **不一致** - 不同模块对配置的处理方式可能不同
3. **难以维护** - 修改配置格式需要改多个地方
4. **循环依赖风险** - Wizard 依赖 Agent，Agent 可能依赖 Wizard

### 1.2 目标

创建独立的 `config` 模块，作为配置管理的单一入口。

---

## 2. 模块架构

### 2.1 整体架构图

```
┌─────────────────────────────────────────────────────────────────────────┐
│                              入口层 (Entry Points)                       │
│  ┌───────────────┐  ┌───────────────┐  ┌───────────────────────────────┐│
│  │  __main__.py  │  │   cli/main.py │  │    cli/wizard.py              ││
│  └───────────────┘  └───────────────┘  └───────────────────────────────┘│
└───────────────────────────────┬─────────────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────────────┐
│                          交互层 (CLI/Adapters)                          │
│  ┌─────────────┐  ┌───────────────┐  ┌─────────────┐  ┌───────────────┐ │
│  │ cli/tui_    │  │ cli/tui.py    │  │ adapters/   │  │ clients/sdk.py│ │
│  │ simple.py   │  │ (富文本TUI)   │  │ base.py     │  │ (SDK客户端)   │ │
│  └─────────────┘  └───────────────┘  └─────────────┘  └───────────────┘ │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │ WebSocket / REST API
┌───────────────────────────────▼─────────────────────────────────────────┐
│                          服务端层 (Server)                               │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │                      server/app.py (ServerApp)                    │  │
│  └──────────────────────────────────────────────────────────────────┘  │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────────────┐
│                          组织层 (Organization)                           │
│                    （原「核心层 Core」，管理协作基础设施）                 │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────────┐ │
│  │ messaging/      │  │  protocol/      │  │  positions/             │ │
│  │ ├─ congress.py  │  │  ├─ event.py    │  │  ├─ models.py           │ │
│  │ ├─ event_bus.py │  │  ├─ message.py  │  │  ├─ registry.py         │ │
│  │                 │  │                 │  │  └─ prompt_renderer.py  │ │
│  │ Congress        │  │ Event, Message  │  │                         │ │
│  │ EventBus        │  │                 │  │ PositionRegistry        │ │
│  └─────────────────┘  └─────────────────┘  └─────────────────────────┘ │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────────────┐
│                         Agent 层 (agents/)                               │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌─────────────────┐ │
│  │ agent.py     │ │ llm_client.py│ │docker_runtime│ │ tool_executor.py│ │
│  │ Agent类      │ │ LLMClient    │ │DockerRuntime │ │ ToolExecutor    │ │
│  └──────┬───────┘ └──────────────┘ └──────┬───────┘ └─────────────────┘ │
└─────────┼──────────────────────────────────┼────────────────────────────┘
          │                                  │ Docker API
          │                                  ▼
┌─────────┴───────────────────────────────────────────────────────────────┐
│                    🔒 Sandbox 层 (Docker 容器)                           │
│  - 每个 Agent 独立的隔离执行环境                                           │
│  - executeBashCommand 在此层执行                                          │
└─────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────┐
│                         配置模块 (config/)                               │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────────┐ │
│  │   models    │  │   loader    │  │    saver    │  │    prompts      │ │
│  │  (数据模型)  │  │  (加载配置)  │  │  (保存配置)  │  │ (Prompt模板)    │ │
│  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────────┘ │
│  ┌─────────────────────────────────────────────────────────────────────┐ │
│  │  validator (配置验证)                                                │ │
│  └─────────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────┘
```

### 2.2 模块依赖关系

```
config/             # 配置模块（基础设施层）
├── models          # 无依赖（纯数据类）
├── validator       # 依赖 models
├── loader          # 依赖 models, validator
├── saver           # 依赖 models, validator
├── prompts         # 依赖 loader
└── registry        # 依赖 loader, models

agents/             # Agent 层
└── agent           # 依赖 config.models

messaging/          # 组织层 (Organization)
├── event_bus.py    # 依赖 config
└── congress.py

wizard/             # 向导模块（基础设施层）
└── menus           # 依赖 config.loader, config.saver, config.validator

server/             # 服务端层
└── app             # 依赖 config.registry
```

**层次说明**:
- **服务端层** → **组织层** → **Agent 层** → **🔒 Sandbox 层**
- 组织层（原核心层 Core）包含 EventBus、ChatRoom(Congress)、PositionRegistry 等协作基础设施

---

## 3. 各模块详细设计

### 3.1 models.py - 数据模型

职责: 定义所有配置相关的数据类

```python
# tlon/config/models.py

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any


@dataclass
class PositionConfig:
    """Position 配置模型"""
    id: str
    type: str
    title: str = ""
    jd: str = ""  # Job Description
    department: str = ""
    proactive: bool = False
    contact_when: str = ""
    rules: List[str] = field(default_factory=list)
    default_skills: List[str] = field(default_factory=list)
    prompt_overrides: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> dict:
        """转换为字典（用于 YAML 序列化）"""
        return {
            "id": self.id,
            "type": self.type,
            "title": self.title,
            "jd": self.jd,
            "department": self.department,
            "proactive": self.proactive,
            "contact_when": self.contact_when,
            "rules": self.rules,
            "default_skills": self.default_skills,
            "prompt_overrides": self.prompt_overrides,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "PositionConfig":
        """从字典创建（用于 YAML 反序列化）"""
        return cls(
            id=data["id"],
            type=data.get("type", data["id"]),
            title=data.get("title", ""),
            jd=data.get("jd", data.get("description", "")),
            department=data.get("department", ""),
            proactive=data.get("proactive", False),
            contact_when=data.get("contact_when", ""),
            rules=data.get("rules", []),
            default_skills=data.get("default_skills", data.get("skills", [])),
            prompt_overrides=data.get("prompt_overrides", {}),
        )


@dataclass
class LLMConfig:
    """LLM 配置"""
    model: str = "gpt-4o-mini"
    temperature: float = 0.7
    max_tokens: int = 4000


@dataclass
class DockerConfig:
    """Docker 运行时配置"""
    image: str = "tlon-agent:latest"
    work_dir: str = "/workspace"
    timeout: int = 300


@dataclass
class MemoryConfig:
    """记忆系统配置"""
    working_memory_limit: int = 100
    summary_threshold: int = 50


@dataclass
class AgentConfig:
    """Agent 配置模型"""
    id: str
    name: str
    position_id: str = ""
    personality: str = ""
    llm: LLMConfig = field(default_factory=LLMConfig)
    docker: DockerConfig = field(default_factory=DockerConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    skills: List[str] = field(default_factory=list)
    loading: str = "lazy"  # "resident" | "lazy"
    
    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "id": self.id,
            "name": self.name,
            "position_id": self.position_id,
            "personality": self.personality,
            "llm": {
                "model": self.llm.model,
                "temperature": self.llm.temperature,
                "max_tokens": self.llm.max_tokens,
            },
            "docker": {
                "image": self.docker.image,
                "work_dir": self.docker.work_dir,
                "timeout": self.docker.timeout,
            },
            "memory": {
                "working_memory_limit": self.memory.working_memory_limit,
                "summary_threshold": self.memory.summary_threshold,
            },
            "skills": self.skills,
            "loading": self.loading,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "AgentConfig":
        """从字典创建"""
        llm_data = data.get("llm", {})
        docker_data = data.get("docker", {})
        memory_data = data.get("memory", {})
        
        return cls(
            id=data["id"],
            name=data["name"],
            position_id=data.get("position_id", ""),
            personality=data.get("personality", ""),
            llm=LLMConfig(
                model=llm_data.get("model", "gpt-4o-mini"),
                temperature=llm_data.get("temperature", 0.7),
                max_tokens=llm_data.get("max_tokens", 4000),
            ),
            docker=DockerConfig(
                image=docker_data.get("image", "tlon-agent:latest"),
                work_dir=docker_data.get("work_dir", "/workspace"),
                timeout=docker_data.get("timeout", 300),
            ),
            memory=MemoryConfig(
                working_memory_limit=memory_data.get("working_memory_limit", 100),
                summary_threshold=memory_data.get("summary_threshold", 50),
            ),
            skills=data.get("skills", []),
            loading=data.get("loading", "lazy"),
        )


@dataclass
class PromptTemplates:
    """Prompt 模板配置"""
    workflow: str = ""
    important_notes: List[str] = field(default_factory=list)
    discipline_rules: List[str] = field(default_factory=list)
    
    @classmethod
    def from_dict(cls, data: dict) -> "PromptTemplates":
        return cls(
            workflow=data.get("workflow", ""),
            important_notes=data.get("important_notes", []),
            discipline_rules=data.get("discipline_rules", []),
        )
```

### 3.2 validator.py - 配置验证

职责: 验证配置数据的有效性和一致性

```python
# tlon/config/validator.py

from typing import List, Tuple, Optional
from .models import PositionConfig, AgentConfig, PromptTemplates


class ValidationError:
    """验证错误"""
    def __init__(self, field: str, message: str, severity: str = "error"):
        self.field = field
        self.message = message
        self.severity = severity  # "error" | "warning"


class ConfigValidator:
    """配置验证器"""
    
    VALID_POSITION_IDS = {
        "customer_service", "developer", "project_manager",
        "operator", "product_manager", "data_analyst"
    }
    
    @classmethod
    def validate_position(cls, config: PositionConfig) -> List[ValidationError]:
        """验证 Position 配置"""
        errors = []
        
        # 必填字段
        if not config.id:
            errors.append(ValidationError("id", "Position ID 不能为空"))
        
        if not config.title:
            errors.append(ValidationError("title", "Position 标题不能为空"))
        
        # 检查 ID 格式
        if config.id and not config.id.replace("_", "").isalnum():
            errors.append(ValidationError("id", "Position ID 只能包含字母、数字和下划线"))
        
        return errors
    
    @classmethod
    def validate_agent(cls, config: AgentConfig, 
                       valid_position_ids: Optional[set] = None) -> List[ValidationError]:
        """验证 Agent 配置"""
        errors = []
        
        # 必填字段
        if not config.id:
            errors.append(ValidationError("id", "Agent ID 不能为空"))
        
        if not config.name:
            errors.append(ValidationError("name", "Agent 名称不能为空"))
        
        if not config.position_id:
            errors.append(ValidationError("position_id", "必须指定 Position ID"))
        
        # 检查 position_id 是否有效
        if config.position_id and valid_position_ids:
            if config.position_id not in valid_position_ids:
                errors.append(ValidationError(
                    "position_id", 
                    f"无效的 Position ID: {config.position_id}",
                    "error"
                ))
        
        # 检查 loading 值
        if config.loading not in ("resident", "lazy"):
            errors.append(ValidationError("loading", "loading 必须是 'resident' 或 'lazy'"))
        
        return errors
    
    @classmethod
    def validate_prompt_templates(cls, templates: PromptTemplates) -> List[ValidationError]:
        """验证 Prompt 模板配置"""
        errors = []
        
        if not templates.workflow.strip():
            errors.append(ValidationError("workflow", "Workflow 不能为空", "warning"))
        
        if not templates.important_notes:
            errors.append(ValidationError("important_notes", "Important notes 为空", "warning"))
        
        return errors
```

### 3.3 loader.py - 配置加载

职责: 从 YAML 文件加载配置到数据模型

```python
# tlon/config/loader.py

import yaml
from pathlib import Path
from typing import Dict, List, Optional
from .models import PositionConfig, AgentConfig, PromptTemplates
from .validator import ConfigValidator, ValidationError


class ConfigLoadError(Exception):
    """配置加载错误"""
    pass


class ConfigLoader:
    """配置加载器"""
    
    def __init__(self, config_dir: Optional[Path] = None):
        self.config_dir = config_dir or Path("config")
        self.positions_dir = self.config_dir / "positions"
        self.agents_dir = self.config_dir / "agents"
    
    def load_position(self, position_id: str) -> Optional[PositionConfig]:
        """加载单个 Position 配置"""
        file_path = self.positions_dir / f"{position_id}.yaml"
        
        if not file_path.exists():
            return None
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f)
            
            if not data or "position" not in data:
                raise ConfigLoadError(f"Invalid position config: {file_path}")
            
            config = PositionConfig.from_dict(data["position"])
            
            # 验证
            errors = ConfigValidator.validate_position(config)
            if any(e.severity == "error" for e in errors):
                error_msgs = [f"{e.field}: {e.message}" for e in errors if e.severity == "error"]
                raise ConfigLoadError(f"Validation failed: {', '.join(error_msgs)}")
            
            return config
            
        except yaml.YAMLError as e:
            raise ConfigLoadError(f"YAML parse error in {file_path}: {e}")
    
    def load_all_positions(self) -> Dict[str, PositionConfig]:
        """加载所有 Position 配置"""
        positions = {}
        
        if not self.positions_dir.exists():
            return positions
        
        for file_path in self.positions_dir.glob("*.yaml"):
            position_id = file_path.stem
            try:
                config = self.load_position(position_id)
                if config:
                    positions[position_id] = config
            except ConfigLoadError as e:
                print(f"Warning: Failed to load position {position_id}: {e}")
        
        return positions
    
    def load_agent(self, agent_id: str) -> Optional[AgentConfig]:
        """加载单个 Agent 配置"""
        file_path = self.agents_dir / f"{agent_id}.yaml"
        
        if not file_path.exists():
            return None
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f)
            
            if not data or "agent" not in data:
                raise ConfigLoadError(f"Invalid agent config: {file_path}")
            
            config = AgentConfig.from_dict(data["agent"])
            return config
            
        except yaml.YAMLError as e:
            raise ConfigLoadError(f"YAML parse error in {file_path}: {e}")
    
    def load_all_agents(self) -> Dict[str, AgentConfig]:
        """加载所有 Agent 配置"""
        agents = {}
        
        if not self.agents_dir.exists():
            return agents
        
        for file_path in self.agents_dir.glob("*.yaml"):
            agent_id = file_path.stem
            try:
                config = self.load_agent(agent_id)
                if config:
                    agents[agent_id] = config
            except ConfigLoadError as e:
                print(f"Warning: Failed to load agent {agent_id}: {e}")
        
        return agents
    
    def load_prompt_templates(self) -> PromptTemplates:
        """加载 Prompt 模板配置"""
        file_path = self.config_dir / "prompt_templates.yaml"
        
        if not file_path.exists():
            return PromptTemplates()
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f)
            
            if not data or "templates" not in data:
                return PromptTemplates()
            
            return PromptTemplates.from_dict(data["templates"])
            
        except yaml.YAMLError as e:
            raise ConfigLoadError(f"YAML parse error in {file_path}: {e}")
```

### 3.4 saver.py - 配置保存

职责: 将数据模型保存到 YAML 文件

```python
# tlon/config/saver.py

import yaml
from pathlib import Path
from typing import Optional
from .models import PositionConfig, AgentConfig
from .validator import ConfigValidator


class ConfigSaver:
    """配置保存器"""
    
    def __init__(self, config_dir: Optional[Path] = None):
        self.config_dir = config_dir or Path("config")
        self.positions_dir = self.config_dir / "positions"
        self.agents_dir = self.config_dir / "agents"
    
    def save_position(self, config: PositionConfig) -> bool:
        """保存 Position 配置"""
        # 验证
        errors = ConfigValidator.validate_position(config)
        if any(e.severity == "error" for e in errors):
            raise ValueError(f"Validation failed: {errors}")
        
        # 确保目录存在
        self.positions_dir.mkdir(parents=True, exist_ok=True)
        
        # 保存
        file_path = self.positions_dir / f"{config.id}.yaml"
        data = {"position": config.to_dict()}
        
        with open(file_path, 'w', encoding='utf-8') as f:
            yaml.dump(data, f, allow_unicode=True, sort_keys=False)
        
        return True
    
    def save_agent(self, config: AgentConfig) -> bool:
        """保存 Agent 配置"""
        # 验证
        errors = ConfigValidator.validate_agent(config)
        if any(e.severity == "error" for e in errors):
            raise ValueError(f"Validation failed: {errors}")
        
        # 确保目录存在
        self.agents_dir.mkdir(parents=True, exist_ok=True)
        
        # 保存
        file_path = self.agents_dir / f"{config.id}.yaml"
        data = {"agent": config.to_dict()}
        
        with open(file_path, 'w', encoding='utf-8') as f:
            yaml.dump(data, f, allow_unicode=True, sort_keys=False)
        
        return True
    
    def delete_position(self, position_id: str) -> bool:
        """删除 Position 配置"""
        file_path = self.positions_dir / f"{position_id}.yaml"
        
        if file_path.exists():
            file_path.unlink()
            return True
        return False
    
    def delete_agent(self, agent_id: str) -> bool:
        """删除 Agent 配置"""
        file_path = self.agents_dir / f"{agent_id}.yaml"
        
        if file_path.exists():
            file_path.unlink()
            return True
        return False
```

### 3.5 prompts.py - Prompt 模板管理

职责: 管理 Prompt 模板，处理 Position 覆盖逻辑

```python
# tlon/config/prompts.py

from typing import Optional, List
from pathlib import Path
from .models import PositionConfig, PromptTemplates
from .loader import ConfigLoader


class PromptTemplateManager:
    """Prompt 模板管理器"""
    
    def __init__(self, config_dir: Optional[Path] = None):
        self.loader = ConfigLoader(config_dir)
        self._templates: Optional[PromptTemplates] = None
        self._load_templates()
    
    def _load_templates(self):
        """加载全局模板"""
        try:
            self._templates = self.loader.load_prompt_templates()
        except Exception:
            self._templates = PromptTemplates()
    
    def get_workflow(self, position: Optional[PositionConfig] = None) -> str:
        """获取 Workflow（支持 Position 覆盖）"""
        if position and position.prompt_overrides:
            override = position.prompt_overrides.get("workflow")
            if override:
                return override
        
        return self._templates.workflow
    
    def get_important_notes(self, position: Optional[PositionConfig] = None) -> List[str]:
        """获取 Important Notes（支持 Position 覆盖）"""
        if position and position.prompt_overrides:
            override = position.prompt_overrides.get("important_notes")
            if override is not None:
                return override
        
        return self._templates.important_notes
    
    def get_discipline_rules(self, position: Optional[PositionConfig] = None) -> List[str]:
        """获取 Discipline Rules（支持 Position 覆盖）"""
        if position and position.prompt_overrides:
            override = position.prompt_overrides.get("discipline_rules")
            if override is not None:
                return override
        
        return self._templates.discipline_rules
```

### 3.6 __init__.py - 模块入口

```python
# tlon/config/__init__.py

"""Minions 配置模块

提供统一的配置管理功能，包括：
- 配置数据模型 (PositionConfig, AgentConfig)
- 配置加载和保存
- 配置验证
- Prompt 模板管理

使用示例:
    >>> from tlon.config import ConfigLoader, ConfigSaver
    >>> from tlon.config.models import AgentConfig
    >>> 
    >>> # 加载配置
    >>> loader = ConfigLoader()
    >>> agent = loader.load_agent("gu-yanxi")
    >>> 
    >>> # 保存配置
    >>> saver = ConfigSaver()
    >>> saver.save_agent(agent)
"""

from .models import (
    PositionConfig,
    AgentConfig,
    LLMConfig,
    DockerConfig,
    MemoryConfig,
    PromptTemplates,
)
from .loader import ConfigLoader, ConfigLoadError
from .saver import ConfigSaver
from .validator import ConfigValidator, ValidationError
from .prompts import PromptTemplateManager

__all__ = [
    # 模型
    "PositionConfig",
    "AgentConfig",
    "LLMConfig",
    "DockerConfig",
    "MemoryConfig",
    "PromptTemplates",
    # 加载/保存
    "ConfigLoader",
    "ConfigSaver",
    "ConfigLoadError",
    # 验证
    "ConfigValidator",
    "ValidationError",
    # Prompt 管理
    "PromptTemplateManager",
]
```

---

## 4. 现有代码迁移计划

### 4.1 Wizard 模块迁移

**当前代码** (minions/wizard/agent_store.py):
```python
# 当前 - 自己实现加载/保存逻辑
class AgentStore:
    def _load_agent(self, agent_id: str) -> dict:
        # ... 重复的实现 ...
    
    def save_agent(self, agent: Agent) -> bool:
        # ... 重复的实现 ...
```

**迁移后**:

```python
# 迁移后 - 使用 config 模块
from tlon.config import ConfigLoader, ConfigSaver
from tlon.config.models import AgentConfig


class AgentStore:
    def __init__(self):
        self.loader = ConfigLoader()
        self.saver = ConfigSaver()

    def load_agent(self, agent_id: str) -> Optional[AgentConfig]:
        return self.loader.load_agent(agent_id)

    def save_agent(self, config: AgentConfig) -> bool:
        return self.saver.save_agent(config)
```

### 4.2 Agent 模块迁移

**当前代码** (minions/agents/config.py):
```python
# 当前 - 独立的 AgentConfig
@dataclass
class AgentConfig:
    # ... 定义 ...
```

**迁移后**:

```python
# 迁移后 - 从 config 模块导入
from tlon.config.models import AgentConfig  # 直接导入
```

### 4.3 Positions 模块迁移

**当前代码** (minions/positions/models.py):
```python
# 当前 - 独立的 Position 类
@dataclass
class Position:
    # ... 定义 ...
```

**迁移后**:

```python
# 迁移后 - 从 config 模块导入
from tlon.config.models import PositionConfig as Position
```

---

## 5. 实施步骤

### Phase 1: 创建 config 模块
1. 创建 `minions/config/` 目录
2. 实现 `models.py` - 数据模型
3. 实现 `validator.py` - 验证器
4. 实现 `loader.py` - 加载器
5. 实现 `saver.py` - 保存器
6. 实现 `prompts.py` - Prompt 管理
7. 实现 `__init__.py` - 模块入口

### Phase 2: 迁移现有配置模型
1. 将 `agents/config.py` 的 `AgentConfig` 迁移到 `config/models.py`
2. 将 `positions/models.py` 的 `Position` 迁移到 `config/models.py`（重命名为 `PositionConfig`）
3. 在原有位置保留兼容导入（可选）

### Phase 3: 迁移 Wizard 模块
1. 修改 `wizard/agent_store.py` 使用 `ConfigLoader` 和 `ConfigSaver`
2. 修改 `wizard/position_store.py` 使用 `ConfigLoader` 和 `ConfigSaver`
3. 删除重复的配置加载/保存逻辑

### Phase 4: 迁移 Agent 模块
1. 修改 `agents/agent.py` 使用 `PromptTemplateManager`
2. 重构 `_build_system_prompt()` 方法
3. 删除 `_build_role_prompt()` 方法

### Phase 5: 测试验证
1. 单元测试
2. 集成测试
3. Wizard 功能测试
4. Agent 功能测试

---

## 6. 收益

### 6.1 代码质量
- **消除重复**: 配置加载/保存逻辑只写一次
- **一致性**: 所有模块使用相同的配置处理方式
- **可测试性**: 配置模块可以独立测试

### 6.2 维护性
- **单一职责**: 配置管理集中在 config 模块
- **易于修改**: 修改配置格式只需改一处
- **清晰依赖**: 模块依赖关系明确

### 6.3 扩展性
- **新功能**: 添加新的配置类型更容易
- **工具支持**: 可以基于 config 模块开发配置验证工具
- **IDE 支持**: 类型提示和自动补全更好
