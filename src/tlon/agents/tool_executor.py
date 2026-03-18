"""Tool Executor for Agents.

Manages tool registration and execution.
"""

from typing import Any, Dict, List

from tlon.base.logger import get_logger
from tlon.subsystem.tools.base import Tool, ToolResult
from tlon.base.events.event import Event, EventType
from tlon.base.events.event_bus import EventBus
from tlon.base.tracing import get_current_trace

logger = get_logger(__name__)


class ToolExecutor:
    """Executor for agent tools.

    Manages tool registration and execution. Tools can be registered
    at initialization or dynamically added.

    Example:
        >>> executor = ToolExecutor()
        >>> executor.register_tool(ExecuteBashTool(docker_runtime))
        >>> executor.register_tool(SendMessageTool(chatroom, ...))
        >>>
        >>> # Get tool definitions for LLM
        >>> tool_defs = executor.get_tool_definitions()
        >>>
        >>> # Execute a tool
        >>> result = await executor.execute("executeBashCommand", {"command": "ls"})
    """

    def __init__(self, event_bus: EventBus = None):
        """Initialize the tool executor.

        Args:
            event_bus: Optional event bus for emitting tool execution events
        """
        self._tools: Dict[str, Tool] = {}
        self._event_bus = event_bus
    
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
    
    def get_tool_definitions(self) -> List[Dict[str, Any]]:
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
            lines.append(f"  Parameters:")
            lines.append(f"{tool.get_parameter_description()}")
        
        return "\n".join(lines)
    
    def list_tools(self) -> List[str]:
        """Get list of registered tool names.
        
        Returns:
            List of tool names
        """
        return list(self._tools.keys())
    
    async def execute(self, tool_name: str, arguments: Dict[str, Any], source: str = "system") -> ToolResult:
        """Execute a tool.

        Args:
            tool_name: Name of the tool to execute
            arguments: Tool arguments as a dictionary
            source: Source component (for event publishing)

        Returns:
            ToolResult with execution outcome

        Raises:
            ValueError: If tool not found
        """
        tool = self.get_tool(tool_name)

        logger.debug(f"Executing tool: {tool_name} with args: {arguments}")

        # Get current trace context
        current_trace = get_current_trace()

        # Emit tool execution start event
        if self._event_bus:
            start_event = Event(
                type=EventType.TOOL_EXECUTE_START,
                payload={
                    "tool": tool_name,
                    "arguments": arguments,
                    "tool_type": getattr(tool, 'tool_type', 'unknown'),
                },
                source=source,
            )
            if current_trace:
                start_event.trace_id = current_trace.trace_id
                start_event.span_id = current_trace.span_id
                if current_trace.parent_span_id:
                    start_event.parent_span_id = current_trace.parent_span_id
            await self._event_bus.publish(start_event)

        import time
        start_time = time.time()

        try:
            result = await tool.execute(**arguments)
            duration_ms = int((time.time() - start_time) * 1000)
            logger.debug(f"Tool {tool_name} execution: success={result.success}")

            # Emit tool execution end event
            if self._event_bus:
                end_event = Event(
                    type=EventType.TOOL_EXECUTE_END,
                    payload={
                        "tool": tool_name,
                        "success": result.success,
                        "duration_ms": duration_ms,
                        "error": result.error if not result.success else None,
                    },
                    source=source,
                )
                if current_trace:
                    end_event.trace_id = current_trace.trace_id
                    end_event.span_id = current_trace.span_id
                    if current_trace.parent_span_id:
                        end_event.parent_span_id = current_trace.parent_span_id
                await self._event_bus.publish(end_event)

            return result
        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            logger.error(f"Tool {tool_name} execution failed: {e}", exc_info=True)

            # Emit tool error event
            if self._event_bus:
                error_event = Event(
                    type=EventType.TOOL_ERROR,
                    payload={
                        "tool": tool_name,
                        "error": str(e),
                        "duration_ms": duration_ms,
                    },
                    source=source,
                )
                if current_trace:
                    error_event.trace_id = current_trace.trace_id
                    error_event.span_id = current_trace.span_id
                    if current_trace.parent_span_id:
                        error_event.parent_span_id = current_trace.parent_span_id
                await self._event_bus.publish(error_event)

            return ToolResult(
                success=False,
                data={},
                error=f"Execution error: {str(e)}"
            )
    
    def has_tool(self, tool_name: str) -> bool:
        """Check if a tool is registered.
        
        Args:
            tool_name: Name of the tool
            
        Returns:
            True if tool is registered
        """
        return tool_name in self._tools
