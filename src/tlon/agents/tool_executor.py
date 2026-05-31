"""Tool Executor - simple direct tool execution.

Manages tool registration and direct async execution.
No EventBus involvement - Agent calls execute() directly.
"""

from typing import Any

from tlon.base.logger import get_logger
from tlon.tools.base import Tool, ToolResult

logger = get_logger(__name__)


class ToolExecutor:
    """Tool registry and direct executor.

    Simple, synchronous-style async tool execution.
    Agent calls execute() directly; no EventBus, no RPC, no futures.

    Example:
        >>> executor = ToolExecutor()
        >>> executor.register_tool(ExecuteBashTool(docker_runtime))
        >>> await executor.initialize()
        >>> result = await executor.execute("executeBashCommand", {"command": "ls"})
    """

    def __init__(self) -> None:
        """Initialize the tool executor."""
        self._tools: dict[str, Tool] = {}

    def register_tool(self, tool: Tool) -> None:
        """Register a tool.

        Args:
            tool: The tool to register
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
        """Execute a tool directly.

        Args:
            tool_name: Name of the tool to execute
            arguments: Tool arguments as a dictionary

        Returns:
            ToolResult with execution outcome
        """
        tool = self._tools.get(tool_name)
        if not tool:
            logger.error(f"Tool not found: {tool_name}")
            return ToolResult(success=False, data={}, error=f"Tool not found: {tool_name}")

        # Validate arguments against JSON Schema before execution
        validation_errors = tool.validate_arguments(arguments)
        if isinstance(validation_errors, list) and len(validation_errors) > 0:
            error_msg = "; ".join(validation_errors)
            logger.warning(f"Tool {tool_name} argument validation failed: {error_msg}")
            return ToolResult(
                success=False,
                data={},
                error=f"Argument validation failed: {error_msg}",
            )

        logger.debug(f"Executing tool: {tool_name} with args: {arguments}")

        try:
            result = await tool.execute(**arguments)
            logger.debug(f"Tool {tool_name} execution: success={result.success}")
            return result
        except Exception as e:
            logger.error(f"Tool {tool_name} execution failed: {e}", exc_info=True)
            return ToolResult(
                success=False,
                data={},
                error=f"Execution error: {str(e)}",
            )

    async def initialize(self) -> None:
        """Initialize all registered tools."""
        logger.info(f"Initializing ToolExecutor ({len(self._tools)} tools)...")
        for tool in self._tools.values():
            try:
                await tool.initialize()
            except Exception as e:
                logger.warning(f"Tool {tool.name} failed to initialize: {e}")
        logger.info(f"ToolExecutor ready: {len(self._tools)} tools registered")

    async def shutdown(self) -> None:
        """Shutdown all registered tools."""
        logger.info("Shutting down ToolExecutor...")
        for tool in self._tools.values():
            await tool.shutdown()
        logger.info("ToolExecutor shutdown complete")
