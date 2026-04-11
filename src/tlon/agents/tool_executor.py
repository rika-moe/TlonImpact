"""Tool Executor - EventBus-based async tool execution.

Manages tool registration and execution via EventBus.
Subscribes to TOOL_EXECUTE_REQUEST events and publishes TOOL_EXECUTE_RESULT events.
"""

import warnings
from typing import Any

from tlon.agents.tool_execution import ToolExecuteRequest, ToolExecuteResult
from tlon.base.component import Component
from tlon.base.events.event import Event, EventType
from tlon.base.events.event_bus import EventBus
from tlon.base.logger import get_logger
from tlon.tools import Tool, ToolResult

logger = get_logger(__name__)


class ToolExecutor(Component):
    """Tool executor that runs as an independent EventBus consumer.

    Subscribes to TOOL_EXECUTE_REQUEST events, executes tools,
    and publishes TOOL_EXECUTE_RESULT events.

    Can be deployed in a separate process for:
    - Resource isolation (Docker containers)
    - Shared resource pool (limit concurrent containers)
    - Scaling (multiple executor instances)

    Example:
        >>> executor = ToolExecutor(event_bus)
        >>> executor.register_tool(ExecuteBashTool(docker_runtime))
        >>> await executor.initialize()
        >>> # Now listens for TOOL_EXECUTE_REQUEST events
    """

    def __init__(self, event_bus: EventBus):
        """Initialize the tool executor.

        Args:
            event_bus: Event bus for receiving requests and sending results
        """
        super().__init__(name="ToolExecutor")
        self.event_bus = event_bus
        self._tools: dict[str, Tool] = {}
        self._subscription_id: str | None = None

    def register_tool(self, tool: Tool) -> None:
        """Register a tool.

        Args:
            tool: The tool to register

        Raises:
            ValueError: If a tool with the same name already exists
        """
        if tool.name in self._tools:
            logger.warning(f"Tool {tool.name} already registered, overwriting")

        self._tools[tool.name] = tool
        logger.debug(f"Registered tool: {tool.name}")

    def unregister_tool(self, tool_name: str) -> bool:
        """Unregister a tool.

        Args:
            tool_name: Name of the tool to unregister

        Returns:
            True if tool was found and removed
        """
        if tool_name in self._tools:
            del self._tools[tool_name]
            logger.debug(f"Unregistered tool: {tool_name}")
            return True
        return False

    def get_tool(self, tool_name: str) -> Tool:
        """Get a tool by name.

        Args:
            tool_name: Name of the tool

        Returns:
            The tool instance

        Raises:
            ValueError: If tool not found
        """
        if tool_name not in self._tools:
            raise ValueError(f"Tool not found: {tool_name}")
        return self._tools[tool_name]

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        """Get all tool definitions for LLM.

        Returns:
            List of OpenAI tool definitions
        """
        return [tool.to_openai_definition() for tool in self._tools.values()]

    def describe_tools(self) -> str:
        """Get human-readable description of all tools.

        Returns:
            Description string for use in prompts
        """
        if not self._tools:
            return "No tools available."

        lines = []
        for name, tool in self._tools.items():
            lines.append(f"\n{name}:")
            lines.append(f"  Description: {tool.description}")
            lines.append("  Parameters:")
            lines.append(f"{tool.get_parameter_description()}")

        return "\n".join(lines)

    def list_tools(self) -> list[str]:
        """Get list of registered tool names.

        Returns:
            List of tool names
        """
        return list(self._tools.keys())

    def has_tool(self, tool_name: str) -> bool:
        """Check if a tool is registered.

        Args:
            tool_name: Name of the tool

        Returns:
            True if tool is registered
        """
        return tool_name in self._tools

    async def execute(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        """Execute a tool directly (for testing/backward compatibility).

        Warning: This method bypasses EventBus and should only be used for testing.
        In production, tools should be executed via EventBus by publishing
        TOOL_EXECUTE_REQUEST events.

        Args:
            tool_name: Name of the tool to execute
            arguments: Tool arguments as a dictionary

        Returns:
            ToolResult with execution outcome

        Raises:
            ValueError: If tool not found
        """
        warnings.warn(
            "Direct execute() bypasses EventBus; use for testing only",
            UserWarning,
            stacklevel=2,
        )
        tool = self.get_tool(tool_name)
        logger.debug(f"Executing tool: {tool_name} with args: {arguments}")

        try:
            result = await tool.execute(**arguments)
            logger.debug(f"Tool {tool_name} execution: success={result.success}")
            return result
        except Exception as e:
            logger.error(f"Tool {tool_name} execution failed: {e}", exc_info=True)
            return ToolResult(success=False, data={}, error=f"Execution error: {str(e)}")

    async def initialize(self) -> None:
        """Initialize the executor and subscribe to events."""
        logger.info("Initializing ToolExecutor...")

        self._subscription_id = self.event_bus.subscribe(
            EventType.TOOL_EXECUTE_REQUEST, self._on_tool_execute_request
        )

        logger.info(
            f"ToolExecutor ready, listening for TOOL_EXECUTE_REQUEST events "
            f"({len(self.list_tools())} tools registered)"
        )

    async def shutdown(self) -> None:
        """Shutdown the executor and unsubscribe from events."""
        logger.info("Shutting down ToolExecutor...")

        if self._subscription_id:
            self.event_bus.unsubscribe(self._subscription_id)
            self._subscription_id = None

        logger.info("ToolExecutor shutdown complete")

    async def _on_tool_execute_request(self, event: Event) -> None:
        """Handle tool execution request.

        Args:
            event: TOOL_EXECUTE_REQUEST event
        """
        try:
            # Handle both dict payload and dataclass payload
            payload = event.payload
            if isinstance(payload, ToolExecuteRequest):
                request = payload
            elif isinstance(payload, dict):
                request = ToolExecuteRequest(**payload)
            else:
                logger.error(f"Unexpected payload type: {type(payload)}")
                return
        except (TypeError, KeyError) as e:
            logger.error(f"Invalid tool execute request payload: {e}")
            return

        logger.debug(
            f"Received tool execution request: {request.tool_name} "
            f"(correlation_id={request.correlation_id})"
        )

        # Execute the tool
        try:
            result = await self.execute(request.tool_name, request.arguments)

            # Publish success result
            result_payload = ToolExecuteResult(
                correlation_id=request.correlation_id,
                agent_id=request.agent_id,
                success=result.success,
                result=result.to_dict()
                if hasattr(result, "to_dict")
                else {"data": result.data, "error": result.error},
                error=result.error,
            )

            await self.event_bus.publish(
                Event(
                    type=EventType.TOOL_EXECUTE_RESULT,
                    payload=result_payload,
                    source="tool_executor",
                )
            )

            logger.debug(
                f"Tool execution completed: {request.tool_name} (success={result.success})"
            )

        except Exception as e:
            logger.error(f"Tool execution failed: {e}", exc_info=True)

            # Publish error result
            result_payload = ToolExecuteResult(
                correlation_id=request.correlation_id,
                agent_id=request.agent_id,
                success=False,
                error=str(e),
            )

            await self.event_bus.publish(
                Event(
                    type=EventType.TOOL_EXECUTE_RESULT,
                    payload=result_payload,
                    source="tool_executor",
                )
            )
