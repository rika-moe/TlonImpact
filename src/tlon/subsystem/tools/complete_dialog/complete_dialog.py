"""Complete dialog tool.

This tool allows agents to explicitly mark the end of their thinking loop
and deliver their final response.
"""

from typing import List, Optional

from tlon.subsystem.tools.base import Tool, ToolResult


class CompleteDialogTool(Tool):
    """Tool to complete the task and deliver final response.
    
    This tool is used by agents to explicitly signal the end of their
    thinking loop. When called, the agent's response is sent to the chat
    and the processing loop terminates.
    
    Example:
        >>> tool = CompleteTaskTool()
        >>> result = await tool.execute(
        ...     content="问题已解决，数据库连接已恢复",
        ...     mentions=["人类"]
        ... )
    """
    
    def __init__(self):
        """Initialize the tool."""
        super().__init__(
            name="completeDialog",
            description="完成对话并发送最终回复。当你思考完毕、执行完所有必要操作后，使用此工具发送最终回复给团队。这是结束本轮处理的唯一方式。",
            parameters={
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "最终回复内容，总结你的思考过程、执行结果和结论"
                    },
                    "mentions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "可选。需要@提及的用户ID或名字列表（如 ['人类', '沈清晏']）。当你需要向特定人员汇报时使用。",
                        "default": []
                    }
                },
                "required": ["content"]
            }
        )
    
    async def execute(
        self, 
        content: str, 
        mentions: Optional[List[str]] = None
    ) -> ToolResult:
        """Complete the task with final response.
        
        Note: The actual message sending is handled by the agent's
        processing loop when it detects this tool was called.
        
        Args:
            content: Final response content
            mentions: Optional list of users to mention
            
        Returns:
            ToolResult containing the response data
        """
        return ToolResult(
            success=True,
            data={
                "content": content,
                "mentions": mentions or [],
                "action": "complete_task"
            }
        )
