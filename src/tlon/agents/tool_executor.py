"""Tool Executor - simple direct tool execution.

Manages tool registration and direct async execution.
No EventBus involvement - Agent calls execute() directly.
"""

import json
from typing import Any

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from tlon.base.logger import get_logger
from tlon.base.tracing.trace_context import start_span
from tlon.tools.base import Tool, ToolResult

logger = get_logger(__name__)


class ToolExecutor:
    """Tool registry and direct executor.

    Simple, synchronous-style async tool execution.
    Agent calls execute() directly; no EventBus, no RPC, no futures.

    Example:
        >>> executor = ToolExecutor()
        >>> executor.register_tool(ExecuteBashTool(docker_config, docker_runtime))
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
            logger.warning("tool_overwritten", tool_name=tool.name)

        self._tools[tool.name] = tool
        logger.debug("tool_registered", tool_name=tool.name)

    def unregister_tool(self, tool_name: str) -> bool:
        """Unregister a tool.

        Args:
            tool_name: Name of the tool to unregister

        Returns:
            True if tool was found and removed
        """
        if tool_name in self._tools:
            del self._tools[tool_name]
            logger.debug("tool_unregistered", tool_name=tool_name)
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
        try:
            args_summary = json.dumps(arguments, ensure_ascii=False, default=str)[:200]
        except (TypeError, ValueError, RecursionError):
            # Logging must not bypass the tool's actual argument boundary.
            args_summary = "<unserializable arguments>"
        log_arguments: Any = (
            args_summary if args_summary == "<unserializable arguments>" else arguments
        )
        with start_span(
            f"tool.{tool_name}",
            source="tool_executor",
            attributes={"tool.name": tool_name, "tool.arguments": args_summary},
        ):
            logger.info("tool_call", tool_name=tool_name, arguments=log_arguments)
            span = trace.get_current_span()
            result = await self._execute_inner(tool_name, arguments)
            span.set_attribute("tool.success", result.success)
            if not result.success:
                span.set_attribute("tool.error", result.error)
                span.set_status(Status(StatusCode.ERROR, result.error))
            logger.info(
                "tool_result",
                tool_name=tool_name,
                success=result.success,
                result=result.to_dict() if hasattr(result, "to_dict") else str(result),
            )
            return result

    async def _execute_inner(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        """Validate arguments and run the tool, converting failures to ToolResult.

        Args:
            tool_name: Name of the tool to execute
            arguments: Tool arguments as a dictionary

        Returns:
            ToolResult with execution outcome (never raises)
        """
        tool = self._tools.get(tool_name)
        if not tool:
            logger.error("tool_not_found", tool_name=tool_name)
            return ToolResult(success=False, data={}, error=f"Tool not found: {tool_name}")

        # Validate arguments against JSON Schema before execution
        validation_errors = tool.validate_arguments(arguments)
        if isinstance(validation_errors, list) and len(validation_errors) > 0:
            error_msg = "; ".join(validation_errors)
            logger.warning("tool_validation_failed", tool_name=tool_name, error=error_msg)
            return ToolResult(
                success=False,
                data={},
                error=f"Argument validation failed: {error_msg}",
            )

        try:
            return await tool.execute(**arguments)
        except Exception as e:
            logger.error("tool_error", tool_name=tool_name, error=str(e), exc_info=True)
            return ToolResult(
                success=False,
                data={},
                error=f"Execution error: {str(e)}",
            )

    async def initialize(self) -> None:
        """Initialize all registered tools."""
        logger.info("tool_executor_initializing", tool_count=len(self._tools))
        for tool in self._tools.values():
            try:
                await tool.initialize()
            except Exception as e:
                logger.warning("tool_initialize_failed", tool_name=tool.name, error=str(e))
        logger.info("tool_executor_ready", tool_count=len(self._tools))

    async def shutdown(self) -> None:
        """Shutdown all registered tools."""
        logger.info("tool_executor_shutdown")
        for tool in self._tools.values():
            await tool.shutdown()
        logger.info("tool_executor_shutdown_complete")
