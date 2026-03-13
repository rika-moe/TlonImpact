"""Tool for recording facts to memory."""

from tlon.base.logger import get_logger
from tlon.subsystem.tools.base import Tool, ToolResult

logger = get_logger(__name__)


class RememberFactTool(Tool):
    """Tool to record a fact to agent's memory.
    
    This tool allows the agent to remember important information
    about the user, project state, decisions, etc.
    
    Example:
        >>> tool = RememberFactTool(memory_manager)
        >>> result = await tool.execute(
        ...     fact="User prefers Python over JavaScript",
        ...     fact_type="USER_PREFERENCE",
        ...     importance=0.8
        ... )
    """
    
    def __init__(self, memory_manager):
        """Initialize with memory manager.
        
        Args:
            memory_manager: The MemoryManager instance
        """
        super().__init__(
            name="rememberFact",
            description="记录一个重要事实到长期记忆中",
            parameters={
                "type": "object",
                "properties": {
                    "fact": {
                        "type": "string",
                        "description": "事实的自然语言描述，例如：用户喜欢使用Python",
                    },
                    "fact_type": {
                        "type": "string",
                        "enum": ["USER_PREFERENCE", "PROJECT_STATE", "TECH_STACK", "DECISION", "GENERAL"],
                        "description": "事实类型：USER_PREFERENCE(用户偏好), PROJECT_STATE(项目状态), TECH_STACK(技术栈), DECISION(决策), GENERAL(一般)",
                    },
                    "subject": {
                        "type": "string",
                        "description": "事实主体，例如：用户",
                    },
                    "predicate": {
                        "type": "string",
                        "description": "谓词/关系，例如：喜欢使用",
                    },
                    "object": {
                        "type": "string",
                        "description": "对象，例如：Python",
                    },
                    "importance": {
                        "type": "number",
                        "description": "重要程度 (0.0-1.0)，默认0.5",
                    },
                },
                "required": ["fact"],
            },
        )
        self.memory = memory_manager
    
    async def execute(
        self,
        fact: str,
        fact_type: str = "GENERAL",
        subject: str = "用户",
        predicate: str = "提到",
        object: str = "",
        importance: float = 0.5,
    ) -> ToolResult:
        """Execute the tool to remember a fact.
        
        Args:
            fact: Natural language description of the fact
            fact_type: Type of fact (USER_PREFERENCE, PROJECT_STATE, etc.)
            subject: Subject of the fact
            predicate: Predicate/relationship
            object: Object of the fact
            importance: Importance score (0-1)
            
        Returns:
            ToolResult with the fact ID
        """
        try:
            fact_id = await self.memory.remember_fact(
                fact=fact,
                fact_type=fact_type,
                subject=subject,
                predicate=predicate,
                object=object or fact[:50],
                importance=importance,
            )
            
            return ToolResult(
                success=True,
                data={"fact_id": fact_id},
                message=f"✅ 已记录事实: {fact[:50]}...",
            )
        except Exception as e:
            logger.error(f"Error remembering fact: {e}")
            return ToolResult(
                success=False,
                error=str(e),
                message=f"❌ 记录事实失败: {e}",
            )
