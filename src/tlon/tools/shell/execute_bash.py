"""Execute Bash command tool."""

from typing import TYPE_CHECKING

from tlon.tools.base import Tool, ToolResult

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
            description="在 Docker 容器中执行 bash 命令。用于运行 shell 命令、安装软件包、检查系统状态等。命令在 Agent 的隔离容器中执行，拥有完整的网络访问权限。",
            parameters={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "要执行的 bash 命令，支持管道、重定向及用分号分隔的多条命令。",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "最大执行时间（秒），默认 60 秒。对于安装等长时间运行的命令，可设为 120 或更大。",
                        "default": 60,
                        "minimum": 1,
                        "maximum": 300,
                    },
                },
                "required": ["command"],
            },
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
                error=result.stderr if result.exit_code != 0 else "",
            )

        except Exception as e:
            return ToolResult(success=False, data={}, error=f"Failed to execute command: {str(e)}")
