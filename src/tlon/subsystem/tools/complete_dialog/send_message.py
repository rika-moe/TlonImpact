"""Send message tool."""

from typing import List, Optional

from tlon.subsystem.tools.base import Tool, ToolResult
from tlon.organization.messaging import Congress
from tlon.organization.messaging.message import Message


class SendMessageTool(Tool):
    """Tool to send messages to the chat room.
    
    This tool allows agents to send messages back to the chat room,
    optionally mentioning other participants.
    
    Example:
        >>> tool = SendMessageTool(chatroom, agent_id="dev-001", agent_name="开发-A")
        >>> result = await tool.execute(
        ...     content="我发现了问题",
        ...     mentions=["pm-001"]
        ... )
    """
    
    def __init__(
        self, 
        chatroom: Congress,
        agent_id: str, 
        agent_name: str
    ):
        """Initialize the tool.
        
        Args:
            chatroom: The chat room to send messages to
            agent_id: ID of the sending agent
            agent_name: Name of the sending agent
        """
        super().__init__(
            name="sendMessage",
            description="Send a message to the chat room. Use this to communicate with other agents and the human user. You can @mention specific participants to get their attention.",
            parameters={
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "The message content to send. Be clear and concise."
                    },
                    "mentions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional list of participant IDs or names to mention (e.g., ['pm-001', 'dev-002']). They will be notified of your message.",
                        "default": []
                    }
                },
                "required": ["content"]
            }
        )
        self.chatroom = chatroom
        self.agent_id = agent_id
        self.agent_name = agent_name
    
    async def execute(
        self, 
        content: str, 
        mentions: Optional[List[str]] = None
    ) -> ToolResult:
        """Send a message to the chat room.
        
        Args:
            content: Message content
            mentions: Optional list of user IDs to mention
            
        Returns:
            ToolResult indicating success
        """
        try:
            message = Message.create(
                sender_id=self.agent_id,
                sender_name=self.agent_name,
                sender_type="agent",
                content=content,
                mentions=mentions or [],
            )
            
            await self.chatroom.send_message(message)
            
            return ToolResult(
                success=True,
                data={"message_id": message.id}
            )
            
        except Exception as e:
            return ToolResult(
                success=False,
                data={},
                error=f"Failed to send message: {str(e)}"
            )
