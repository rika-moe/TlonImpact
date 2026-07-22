"""Agent Registry for managing multiple agents.

This module provides AgentRegistry class that manages:
- Agent lifecycle (all agents start at system startup)
- Agent shutdown

Configuration is delegated to AgentInfoProvider (typically ConfigManager).

Architecture:
    AgentRegistry is a Component that depends on:
    - EventBus (for agent event publishing)
    - AgentInfoProvider (for configuration queries)
    - MessageStore (for agent history access)

    All dependencies are injected via constructor (DAG rule).
"""

import asyncio
from contextlib import suppress
from typing import TYPE_CHECKING

from tlon.agents.agent import Agent
from tlon.agents.tool_executor import ToolExecutor
from tlon.base.component import Component
from tlon.base.config import AgentConfig, LLMConfig
from tlon.base.events.event import Event, EventType
from tlon.base.events.event_bus import EventBus
from tlon.base.llm_client import LLMClient
from tlon.base.logger import get_logger
from tlon.base.paths import TlonPaths, paths
from tlon.memory.manager import MemoryManager
from tlon.memory.models import MemoryConfig
from tlon.organization.messaging.store import MessageStore
from tlon.sandbox.docker_runtime import DockerRuntime
from tlon.tools.filesystem.edit_file import EditFileTool
from tlon.tools.filesystem.read_file import ReadFileTool
from tlon.tools.filesystem.write_file import WriteFileTool
from tlon.tools.memory.compact import CompactTool
from tlon.tools.memory.forget_memory import ForgetMemoryTool
from tlon.tools.memory.remember_fact import RememberFactTool
from tlon.tools.memory.search_memories import SearchMemoriesTool
from tlon.tools.messaging.send_message import SendMessageTool
from tlon.tools.prototypes.list_prototypes import ListPrototypesTool
from tlon.tools.shell.execute_bash import CheckBashTaskTool, ExecuteBashTool
from tlon.tools.tasks.complete_task import CompleteTaskTool
from tlon.tools.tasks.create_task import CreateTaskTool
from tlon.tools.tasks.get_current_task import GetCurrentTaskTool
from tlon.tools.tasks.list_tasks import ListTasksTool
from tlon.tools.tasks.manager import TaskManager
from tlon.tools.tasks.pause_task import PauseTaskTool
from tlon.tools.tasks.remove_task import RemoveTaskTool
from tlon.tools.tasks.start_task import StartTaskTool

if TYPE_CHECKING:
    from tlon.base.config import AgentInfoProvider

logger = get_logger(__name__)


def _build_llm_config(file_config: LLMConfig | None, settings) -> LLMConfig:
    """Merge LLM config from YAML file with runtime settings.

    File config provides model parameters (model_name, temperature, etc.)
    Settings provide runtime parameters (base_url, auth_token, etc.)

    Args:
        file_config: LLMConfig from agent YAML (may be None)
        settings: Application settings from environment variables

    Returns:
        Merged LLMConfig with all fields populated
    """
    if file_config is None:
        file_config = LLMConfig()

    return LLMConfig(
        model_name=file_config.model_name,
        temperature=file_config.temperature,
        max_tokens=file_config.max_tokens,
        top_p=file_config.top_p,
        base_url=settings.llm_base_url,
        auth_token=settings.llm_api_key,
        max_retries=settings.llm_max_retries,
        base_delay=settings.llm_base_delay,
    )


class AgentRegistry(Component):
    """Registry for managing multiple agents.

    All agents are started at system initialization.
    No lazy loading support.

    Configuration is provided by AgentInfoProvider (typically ConfigManager),
    allowing this class to focus on agent lifecycle management only.

    Dependencies (constructor injected):
        - event_bus: EventBus for agent event publishing
        - agent_info_provider: AgentInfoProvider for configuration queries
        - message_store: MessageStore (optional)

    Example:
        >>> registry = AgentRegistry(
        ...     event_bus=event_bus,
        ...     agent_info_provider=config_manager,
        ...     message_store=message_store,
        ... )
        >>> registry.register("dev-001")  # Register by ID
        >>> await registry.initialize()  # Start all agents
        >>> agent = registry.get_agent("dev-001")  # Get running agent
    """

    def __init__(
        self,
        event_bus: EventBus,
        agent_info_provider: "AgentInfoProvider",
        message_store: MessageStore | None = None,
        paths: "TlonPaths | None" = None,
    ):
        """Initialize the agent registry.

        All dependencies are injected via constructor (DAG architecture).

        Args:
            event_bus: Event bus for message routing
            agent_info_provider: Provider for agent configuration queries
            message_store: Message store for agent history access
            paths: TlonPaths instance (defaults to module singleton)
        """
        super().__init__(name="AgentRegistry")
        self.event_bus = event_bus
        self._agent_info = agent_info_provider
        self.message_store = message_store
        self._paths = paths  # None means use module singleton

        # Registered agent IDs (to be started during initialize)
        self._registered_ids: list[str] = []

        # Active agents (all agents are active after initialize)
        self._agents: dict[str, Agent] = {}

        logger.info("agent_registry_constructed")

    def register(self, agent_id: str) -> None:
        """Register an agent to be started.

        The agent configuration is looked up from AgentInfoProvider.
        The agent will be started during initialize().

        Args:
            agent_id: The agent ID to register

        Raises:
            ValueError: If agent configuration is not found
        """
        if not self._agent_info.get_config(agent_id):
            raise ValueError(f"Cannot register unknown agent: {agent_id}")

        if agent_id not in self._registered_ids:
            self._registered_ids.append(agent_id)
            logger.info("agent_registered", agent_id=agent_id)

    def register_all(self, agent_ids: list[str]) -> None:
        """Register multiple agents.

        Args:
            agent_ids: List of agent IDs
        """
        for agent_id in agent_ids:
            self.register(agent_id)

    async def initialize(self) -> None:
        """Initialize the registry and start all registered agents in parallel.

        Each agent gets up to `_agent_start_timeout` seconds to start.
        Agents that fail or time out are logged and skipped — the server
        continues operating with the agents that did start successfully.
        """
        logger.info("agent_registry_initializing", agent_ids=self._registered_ids)

        agent_ids = [aid for aid in self._registered_ids if aid not in self._agents]
        if not agent_ids:
            self._initialized = True
            return

        async def _start_one(agent_id: str) -> tuple[str, Exception | None]:
            try:
                logger.info("agent_starting", agent_id=agent_id)
                await self._start_agent(agent_id)
                logger.info("agent_started", agent_id=agent_id)
                return agent_id, None
            except Exception as e:
                logger.error("agent_start_failed", agent_id=agent_id, error=str(e))
                return agent_id, e

        per_agent_timeout = 60
        overall_timeout = len(agent_ids) * per_agent_timeout

        tasks = [asyncio.create_task(_start_one(aid)) for aid in agent_ids]
        try:
            await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=False),
                timeout=overall_timeout,
            )
        except TimeoutError:
            logger.error(
                "agent_startup_timeout",
                timeout_seconds=overall_timeout,
            )
            for t in tasks:
                if not t.done():
                    t.cancel()
            for t in tasks:
                with suppress(asyncio.CancelledError, Exception):
                    await t

        self._initialized = True
        active_count = len(self._agents)
        total = len(agent_ids)
        logger.info(
            "agent_registry_initialized",
            active_count=active_count,
            total=total,
            active_agents=list(self._agents.keys()),
        )

    async def shutdown(self) -> None:
        """Shutdown all agents and cleanup."""
        logger.info("agent_registry_shutdown_started")

        # Shutdown all active agents
        for agent_id, agent in list(self._agents.items()):
            try:
                await agent.shutdown()
                logger.info("agent_shutdown", agent_id=agent_id)
            except Exception as e:
                logger.error("agent_shutdown_failed", agent_id=agent_id, error=str(e))

        self._agents.clear()
        self._registered_ids.clear()
        self._initialized = False

        logger.info("agent_registry_shutdown_completed")

    async def _start_agent(self, agent_id: str) -> Agent | None:
        """Assemble all dependencies and start an agent.

        This is the sole assembler for Agent and its infrastructure.
        All deps are constructed here and injected via Agent's constructor.

        Args:
            agent_id: ID of the agent to start

        Returns:
            The started Agent instance, or None if not found
        """
        if agent_id in self._agents:
            # Already active
            return self._agents[agent_id]

        config = self._agent_info.get_config(agent_id)
        if not config:
            logger.error("unknown_agent", agent_id=agent_id)
            return None

        try:
            logger.info("agent_starting", agent_id=agent_id, agent_name=config.name)

            from tlon.base.settings import get_settings

            # Build infrastructure dependencies
            llm_config = _build_llm_config(config.llm, get_settings())
            llm_client = LLMClient(llm_config)

            agent_home = (self._paths or paths).agent_dir(config.id)
            memory_config = (
                config.memory if isinstance(config.memory, MemoryConfig) else MemoryConfig()
            )
            memory_config.agent_id = config.id
            memory = MemoryManager(
                agent_id=config.id,
                config=memory_config,
                data_dir=agent_home,
                llm_config=llm_config,
            )

            task_manager = TaskManager(
                agent_id=config.id,
                data_dir=str(agent_home),
            )

            # Create Docker runtime (shared by shell + filesystem tools)
            docker_runtime: DockerRuntime | None = None
            try:
                docker_runtime = DockerRuntime(config.docker, config.id)
                await docker_runtime.ensure_running()
            except Exception as e:
                logger.warning("docker_unavailable", agent_name=config.name, error=str(e))

            # Create ToolExecutor (direct execution, no EventBus)
            tool_executor = ToolExecutor()

            # Register tools
            tool_executor.register_tool(ExecuteBashTool(config.docker, docker_runtime))
            tool_executor.register_tool(CheckBashTaskTool())
            allowed_paths = config.tool.allowed_paths
            tool_executor.register_tool(ReadFileTool(config.docker, docker_runtime, allowed_paths))
            tool_executor.register_tool(WriteFileTool(config.docker, docker_runtime, allowed_paths))
            tool_executor.register_tool(EditFileTool(config.docker, docker_runtime, allowed_paths))
            tool_executor.register_tool(SendMessageTool())
            tool_executor.register_tool(ListPrototypesTool())
            tool_executor.register_tool(RememberFactTool(memory))
            tool_executor.register_tool(SearchMemoriesTool(memory))
            tool_executor.register_tool(CompactTool(memory))
            tool_executor.register_tool(ForgetMemoryTool(memory))
            tool_executor.register_tool(CreateTaskTool(task_manager))
            tool_executor.register_tool(ListTasksTool(task_manager))
            tool_executor.register_tool(StartTaskTool(task_manager))
            tool_executor.register_tool(PauseTaskTool(task_manager))
            tool_executor.register_tool(CompleteTaskTool(task_manager))
            tool_executor.register_tool(RemoveTaskTool(task_manager))
            tool_executor.register_tool(GetCurrentTaskTool(task_manager))
            logger.debug(
                "tools_registered",
                agent_name=config.name,
                tool_count=len(tool_executor.list_tools()),
            )

            # Initialize tools
            await tool_executor.initialize()

            # Prepare team members config (all other agents)
            all_ids = self._agent_info.get_all_ids()
            team_members = [
                self._agent_info.get_config(aid)
                for aid in all_ids
                if aid != agent_id and self._agent_info.get_config(aid)
            ]

            # Read tool timeout from agent config
            per_tool_timeout = config.tool.per_timeout

            # Create and initialize agent with all deps injected
            agent = Agent(
                config=config,
                event_bus=self.event_bus,
                llm_client=llm_client,
                memory=memory,
                task_manager=task_manager,
                tool_executor=tool_executor,
                team_members=team_members,
                message_store=self.message_store,
                per_tool_timeout=per_tool_timeout,
            )
            await agent.initialize()

            # Track active agent
            self._agents[agent_id] = agent

            # Publish agent started event
            event = Event(
                type=EventType.AGENT_STARTED,
                payload={"agent_id": agent_id, "name": config.name},
                source="agent_registry",
            )
            await self.event_bus.publish(event)

            logger.info("agent_started", agent_id=agent_id, agent_name=config.name)
            return agent

        except Exception as e:
            logger.error("agent_start_failed", agent_id=agent_id, error=str(e), exc_info=True)
            return None

    def get_agent(self, agent_id: str) -> Agent | None:
        """Get an active agent by ID.

        Args:
            agent_id: Agent ID

        Returns:
            The Agent instance if active, None otherwise
        """
        return self._agents.get(agent_id)

    def get_all_agents(self) -> list[Agent]:
        """Get all currently active agents.

        Returns:
            List of active Agent instances
        """
        return list(self._agents.values())

    def get_all_ids(self) -> list[str]:
        """Get all registered agent IDs.

        Returns:
            List of all agent IDs from AgentInfoProvider
        """
        return list(self._agent_info.get_all_ids())

    def get_all(self) -> list[str]:
        """Get all registered agent IDs.

        Alias for get_all_ids() for backward compatibility.

        Returns:
            List of all agent IDs
        """
        return self.get_all_ids()

    def get_config(self, agent_id: str) -> AgentConfig | None:
        """Get an agent's configuration.

        Delegates to AgentInfoProvider.

        Args:
            agent_id: Agent ID

        Returns:
            AgentConfig or None if not found
        """
        return self._agent_info.get_config(agent_id)

    def unregister(self, agent_id: str) -> bool:
        """Unregister and stop an agent.

        Args:
            agent_id: Agent ID to unregister

        Returns:
            True if agent was found and removed
        """
        # Check if registered or active
        is_registered = agent_id in self._registered_ids
        is_active = agent_id in self._agents

        if not is_registered and not is_active:
            return False

        # Stop if active
        if is_active:
            agent = self._agents.pop(agent_id)
            asyncio.create_task(agent.shutdown())

        # Remove from registered list
        if is_registered:
            self._registered_ids.remove(agent_id)

        logger.info("agent_unregistered", agent_id=agent_id)
        return True

    def update_config(self, agent_id: str, config: AgentConfig) -> bool:
        """Update an agent's configuration.

        Note: Configuration updates should be done through AgentInfoProvider
        (e.g., ConfigManager). This method only validates that the agent
        is registered and logs a warning if the agent is active.

        Args:
            agent_id: Agent ID to update
            config: New configuration (for validation only)

        Returns:
            True if agent is registered
        """
        if agent_id not in self._registered_ids and agent_id not in self._agents:
            return False

        # If agent is active, we should restart it
        # For now, just log the warning
        if agent_id in self._agents:
            logger.warning("agent_restart_required", agent_id=agent_id)

        logger.info("agent_config_updated", agent_id=agent_id)
        return True

    def get_agent_status(self, agent_id: str) -> dict | None:
        """Get status of a specific agent.

        Args:
            agent_id: Agent ID

        Returns:
            Agent status dictionary or None if not found
        """
        config = self._agent_info.get_config(agent_id)
        if not config:
            return None

        agent = self._agents.get(agent_id)

        return {
            "id": agent_id,
            "name": config.name,
            "state": agent.get_state() if agent else "unknown",
        }

    def get_all_agent_status(self) -> list[dict]:
        """Get status of all registered agents.

        Returns:
            List of agent status dictionaries
        """
        return [self.get_agent_status(agent_id) for agent_id in self._agent_info.get_all_ids()]
