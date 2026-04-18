"""Base class for Tools."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class ToolResult:
    """Result of a tool execution."""

    success: bool
    data: Any
    error: str = ""
    message: str = ""  # 用户友好的消息，用于显示

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "success": self.success,
            "data": self.data,
            "error": self.error,
            "message": self.message,
        }


class Tool(ABC):
    """Base class for agent tools.

    Tools are executable functions that agents can invoke.
    Each tool has a name, description, and parameter schema.

    Example:
        >>> class MyTool(Tool):
        ...     def __init__(self):
        ...         super().__init__(
        ...             name="my_tool",
        ...             description="Does something useful",
        ...             parameters={
        ...                 "type": "object",
        ...                 "properties": {
        ...                     "input": {"type": "string"}
        ...                 },
        ...                 "required": ["input"]
        ...             }
        ...         )
        ...
        ...     async def execute(self, input: str) -> ToolResult:
        ...         return ToolResult(success=True, data={"result": input.upper()})
    """

    def __init__(self, name: str, description: str, parameters: dict[str, Any]):
        """Initialize the tool.

        Args:
            name: Tool name (used by LLM to reference it)
            description: Description of what the tool does
            parameters: JSON schema for tool parameters
        """
        self.name = name
        self.description = description
        self.parameters = parameters

    def to_openai_definition(self) -> dict[str, Any]:
        """Convert to OpenAI tool definition format.

        Returns:
            Dictionary in OpenAI function calling format
        """
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    @abstractmethod
    async def execute(self, **kwargs) -> ToolResult:
        """Execute the tool.

        Args:
            **kwargs: Parameters as defined in the schema

        Returns:
            ToolResult with execution outcome
        """
        pass

    async def initialize(self) -> None:  # noqa: B027
        """Lifecycle hook called when ToolExecutor starts.

        Tools that manage external resources (e.g., Docker containers)
        should override this to initialize those resources.
        """
        pass

    async def shutdown(self) -> None:  # noqa: B027
        """Lifecycle hook called when ToolExecutor stops.

        Tools that manage external resources should override this
        to clean up those resources.
        """
        pass

    def get_parameter_description(self) -> str:
        """Get a human-readable description of parameters.

        Returns:
            Parameter description string
        """
        props = self.parameters.get("properties", {})
        required = self.parameters.get("required", [])

        lines = []
        for name, schema in props.items():
            desc = schema.get("description", "")
            type_str = schema.get("type", "any")
            req_str = " (required)" if name in required else ""
            lines.append(f"  - {name} ({type_str}){req_str}: {desc}")

        return "\n".join(lines) if lines else "  (no parameters)"
