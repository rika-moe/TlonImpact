"""EventBus-based Tool Executor (Phase 3).

ToolExecutor as an independent Component that subscribes to
TOOL_EXECUTE_REQUEST events and publishes TOOL_EXECUTE_RESULT events.

Can run in a separate process for resource isolation and scaling.
"""

from typing import Any

from tlon.agents.tool_execution import ToolExecuteRequest, ToolExecuteResult
from tlon.agents.tool_executor import ToolExecutor
from tlon.base.component import Component
from tlon.base.events.event import Event, EventType
from tlon.base.events.event_bus import EventBus
from tlon.base.logger import get_logger

logger = get_logger(__name__)


class EventBusToolExecutor(Component):
    """Tool executor that runs as an independent EventBus consumer.

    Subscribes to TOOL_EXECUTE_REQUEST events, executes tools,
    and publishes TOOL_EXECUTE_RESULT events.

    Can be deployed in a separate process for:
    - Resource isolation (Docker containers)
    - Shared resource pool (limit concurrent containers)
    - Scaling (multiple executor instances)

    Example:
        >>> executor = EventBusToolExecutor(event_bus)
        >>> executor.register_tool(ExecuteBashTool(docker_runtime))
        >>> await executor.initialize()
        >>> # Now listens for TOOL_EXECUTE_REQUEST events
    """

    def __init__(self, event_bus: EventBus):
        """Initialize the EventBus tool executor.

        Args:
            event_bus: Event bus for receiving requests and sending results
        """
        super().__init__(name="EventBusToolExecutor")
        self.event_bus = event_bus
        self._tool_executor = ToolExecutor()
        self._subscription_id: str | None = None

    def register_tool(self, tool: Any) -> None:
        """Register a tool.

        Args:
            tool: Tool instance to register
        """
        self._tool_executor.register_tool(tool)

    def unregister_tool(self, tool_name: str) -> bool:
        """Unregister a tool.

        Args:
            tool_name: Name of tool to unregister

        Returns:
            True if tool was found and removed
        """
        return self._tool_executor.unregister_tool(tool_name)

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        """Get all tool definitions for LLM.

        Returns:
            List of OpenAI tool definitions
        """
        return self._tool_executor.get_tool_definitions()

    def list_tools(self) -> list[str]:
        """Get list of registered tool names.

        Returns:
            List of tool names
        """
        return self._tool_executor.list_tools()

    def has_tool(self, tool_name: str) -> bool:
        """Check if a tool is registered.

        Args:
            tool_name: Name of the tool

        Returns:
            True if tool is registered
        """
        return self._tool_executor.has_tool(tool_name)

    async def initialize(self) -> None:
        """Initialize the executor and subscribe to events."""
        logger.info("Initializing EventBusToolExecutor...")

        self._subscription_id = self.event_bus.subscribe(
            EventType.TOOL_EXECUTE_REQUEST, self._on_tool_execute_request
        )

        logger.info(
            f"EventBusToolExecutor ready, listening for TOOL_EXECUTE_REQUEST events "
            f"({len(self.list_tools())} tools registered)"
        )

    async def shutdown(self) -> None:
        """Shutdown the executor and unsubscribe from events."""
        logger.info("Shutting down EventBusToolExecutor...")

        if self._subscription_id:
            self.event_bus.unsubscribe(self._subscription_id)
            self._subscription_id = None

        logger.info("EventBusToolExecutor shutdown complete")

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
            result = await self._tool_executor.execute(request.tool_name, request.arguments)

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
