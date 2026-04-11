# 导入路径速查

> 状态: ✅ 已验证
> 最后验证: 2026-03-22

---

## 基础层

```python
from tlon.base.config import ConfigLoader, ConfigSaver
from tlon.base.config.models import AgentConfig, RoleConfig
from tlon.base.events import Event, EventType, EventBus
from tlon.base.logger import get_logger
from tlon.base.settings import settings
```

## 组织层

```python
from tlon.organization.messaging import Congress, Message
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

## 功能模块

```python
from tlon.memory import MemoryManager, MemoryConfig
from tlon.tools import Tool, ToolResult, ExecuteBashTool
from tlon.tools.tasks import TaskManager, Task
```

---

*导入路径与代码一致，如有变更请更新本文档*
