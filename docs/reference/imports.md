# 导入路径速查

> 状态: ✅ 已验证
> 最后验证: 2026-03-17

---

## 基础层

```python
from tlon.base.config import ConfigLoader, ConfigSaver
from tlon.base.config.models import AgentConfig, PositionConfig
from tlon.base.events import Event, EventType, EventBus
from tlon.base.logger import get_logger
from tlon.base.settings import settings
```

## 组织层

```python
from tlon.organization.messaging import Congress, Message
from tlon.organization.positions import PositionRegistry, Position
```

## Agent 层

```python
from tlon.agents import Agent, AgentRegistry
from tlon.agents.llm_client import LLMClient
from tlon.agents.tool_executor import ToolExecutor
from tlon.agents.prompt_builder import SystemPromptBuilder
```

## Sandbox 层

```python
from tlon.sandbox.docker_runtime import DockerRuntime
```

## 子系统层

```python
from tlon.subsystem.memory import MemoryManager
from tlon.subsystem.skills import SkillManager
from tlon.subsystem.tools.base import Tool, ToolResult
from tlon.subsystem.tools.tasks import TaskManager, Task
```

---

*导入路径与代码一致，如有变更请更新本文档*
