# 导入路径速查

> 状态: ✅ 已验证
> 最后验证: 2026-04-19

---

## 基础层

```python
from tlon.base.config import AgentConfig, LLMConfig, DockerRuntimeConfig, ConfigService
from tlon.base.events.event import Event, EventType
from tlon.base.events.event_bus import EventBus
from tlon.base.component import Component, ComponentRegistry
from tlon.base.device import Device
from tlon.base.logger import get_logger
from tlon.base.settings import settings
from tlon.base.tracing.trace_context import start_span, get_current_trace
from tlon.base.tracing.trace_store import FileTraceStore
```

## 组织层

```python
from tlon.organization.messaging.message import Message
from tlon.organization.messaging.router import MessageRouter, RoutedMessage
from tlon.organization.messaging.store import FileMessageStore
from tlon.organization.messaging.participant import Participant
```

## Agent 层

```python
from tlon.agents.agent import Agent
from tlon.agents.agent_registry import AgentRegistry
from tlon.agents.llm_client import LLMClient
from tlon.agents.tool_executor import ToolExecutor
from tlon.agents.prompt_builder import SystemPromptBuilder
from tlon.agents.prompt_context import AgentPromptContext
from tlon.agents.session import AgentSession, SessionState
# ReAct loop: _main_loop → _step_session / _collect_session_results
```

## Sandbox 层

```python
from tlon.sandbox.docker_runtime import DockerRuntime
```

## 功能模块

```python
from tlon.memory.manager import MemoryManager
from tlon.memory.models import MemoryConfig
from tlon.tools.base import Tool, ToolResult
from tlon.tools.shell.execute_bash import ExecuteBashTool
from tlon.tools.tasks.manager import TaskManager
from tlon.tools.tasks.models import Task
```

---

*导入路径与代码一致，如有变更请更新本文档*
