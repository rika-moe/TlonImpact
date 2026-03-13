"""Execute Bash command tool."""

from typing import TYPE_CHECKING

from tlon.subsystem.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from tlon.sandbox.docker_runtime import DockerRuntime


class ExecuteBashTool(Tool):
    """Tool to execute bash commands in the agent's Docker container.
    
    This tool allows agents to run arbitrary shell commands in their
    isolated container environment.
    
    Example:
        >>> tool = ExecuteBashTool(docker_runtime)
        >>> result = await tool.execute(command="ls -la", timeout=30)
        >>> print(result.data["stdout"])
    """
    
    def __init__(self, docker_runtime: "DockerRuntime"):
        """Initialize the tool.
        
        Args:
            docker_runtime: Docker runtime for command execution
        """
        super().__init__(
            name="executeBashCommand",
            description="Execute a bash command in the Docker container. Use this to run shell commands, install packages, check system status, etc. The command runs in the agent's isolated container with full network access.",
            parameters={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The bash command to execute. Can include pipes, redirects, and multiple commands separated by semicolons."
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Maximum execution time in seconds. Default is 60 seconds. For long-running commands like installations, use 120 or more.",
                        "default": 60,
                        "minimum": 1,
                        "maximum": 300,
                    }
                },
                "required": ["command"]
            }
        )
        self.docker_runtime = docker_runtime
    
    async def execute(self, command: str, timeout: int = 60) -> ToolResult:
        """Execute a bash command.
        
        Args:
            command: The bash command to execute
            timeout: Maximum execution time in seconds
            
        Returns:
            ToolResult with stdout, stderr, and exit code
        """
        try:
            result = await self.docker_runtime.execute(command, timeout=timeout)
            
            return ToolResult(
                success=result.exit_code == 0,
                data={
                    "exit_code": result.exit_code,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                },
                error=result.stderr if result.exit_code != 0 else ""
            )
            
        except Exception as e:
            return ToolResult(
                success=False,
                data={},
                error=f"Failed to execute command: {str(e)}"
            )
