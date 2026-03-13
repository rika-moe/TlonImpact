"""Tool Executor for Agents.

Manages tool registration and execution.
"""

from typing import Any, Dict, List

from tlon.base.logger import get_logger
from tlon.subsystem.tools.base import Tool, ToolResult

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
    
    def __init__(self):
        """Initialize the tool executor."""
        self._tools: Dict[str, Tool] = {}
    
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
    
    async def execute(self, tool_name: str, arguments: Dict[str, Any]) -> ToolResult:
        """Execute a tool.
        
        Args:
            tool_name: Name of the tool to execute
            arguments: Tool arguments as a dictionary
            
        Returns:
            ToolResult with execution outcome
            
        Raises:
            ValueError: If tool not found
        """
        tool = self.get_tool(tool_name)
        
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
