"""System prompt builder for Agent.

Provides a clean, composable way to build agent system prompts.
"""

from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from .agent import Agent


class PromptSection:
    """A section of the system prompt.
    
    Attributes:
        title: Section title (e.g., "## 可用工具")
        content: Section content
    """
    
    def __init__(self, title: str, content: Optional[str] = None):
        self.title = title
        self.content = content or ""
    
    def render(self) -> str:
        """Render the section as a string."""
        if not self.content:
            return f"{self.title}\n"
        return f"{self.title}\n\n{self.content}"
    
    def __bool__(self) -> bool:
        """Section is truthy if it has content."""
        return bool(self.content.strip()) if self.content else False


class SystemPromptBuilder:
    """Builds system prompts by composing sections.
    
    Usage:
        builder = SystemPromptBuilder(agent)
        prompt = await builder.build()
    """
    
    def __init__(self, agent: "Agent"):
        self.agent = agent
        self._position = None
        self._position_rules: List[str] = []
        self._load_position_info()
    
    def _load_position_info(self) -> None:
        """Load position info from registry."""
        if not self.agent.position_registry or not self.agent.position_id:
            return
        
        self._position = self.agent.position_registry.get(self.agent.position_id)
        if self._position:
            self._position_rules = self._position.rules or []
    
    @property
    def position_title(self) -> str:
        """Get position title."""
        return self._position.title if self._position else self.agent.position_id
    
    @property
    def position_jd(self) -> str:
        """Get position job description."""
        return self._position.jd if self._position else ""
    
    # =========================================================================
    # Section Builders
    # =========================================================================
    
    def build_identity(self) -> str:
        """Build identity section: # 你是 {name}"""
        lines = [
            f"# 你是 {self.agent.name}",
            "",
            f"岗位: {self.position_title}",
        ]
        
        if self.position_jd:
            lines.extend(["", "岗位职责:", self.position_jd])
        
        if self.agent.personality:
            lines.extend(["", f"个人特点: {self.agent.personality}"])
        
        return "\n".join(lines)
    
    def build_capabilities(self) -> str:
        """Build capabilities/tools section."""
        tools = [
            "- sendMessage: 发送消息到聊天室",
            "- executeBashCommand: 在容器中执行 bash 命令",
            "- rememberFact: 记录重要事实到长期记忆",
            "- searchMemories: 搜索已记录的记忆",
            "- generateSummary: 生成当前会话摘要",
            "- forget: 删除不再需要的记忆",
        ]
        
        if self.agent.config.skills:
            tools.extend([
                "- activateSkill: 激活一个技能",
                "- loadSkillResource: 加载技能目录下的资源文件",
                "- deactivateSkill: 关闭技能释放资源",
            ])
        
        return PromptSection("## 可用工具", "\n".join(tools)).render()
    
    def build_role(self) -> Optional[str]:
        """Build role section from position jd."""
        if not self.position_jd:
            return None
        
        return PromptSection("## 角色职责", f"岗位职责:\n{self.position_jd}").render()
    
    def build_team(self) -> Optional[str]:
        """Build team members table."""
        team_table = self.agent._build_team_table()
        if not team_table:
            return None
        
        return PromptSection("## 团队成员（你可以 @他们）", team_table).render()
    
    def build_skills(self) -> Optional[str]:
        """Build skills section if agent has skills configured."""
        if not self.agent.config.skills:
            return None
        
        parts = []
        
        # Available skills table
        skills_table = self.agent.skill_manager.get_skills_table_for_prompt()
        if skills_table:
            parts.append(PromptSection("## 可用技能", skills_table).render())
        
        # Active skills context
        active_skills = self.agent.skill_manager.get_active_skills_context()
        if active_skills:
            parts.append(PromptSection("## 已激活技能", active_skills).render())
        
        return "\n\n".join(parts) if parts else None
    
    async def build_memory(self) -> Optional[str]:
        """Build memory context section."""
        try:
            context_blocks = await self.agent.memory.build_context_blocks()
            if not context_blocks:
                return None
            
            parts = ["## 历史记忆"]
            for block in context_blocks:
                msg = block.to_openai_message()
                parts.append(msg["content"])
            
            return "\n\n".join(parts)
        except Exception as e:
            from ..logger import get_logger
            logger = get_logger(__name__)
            logger.warning(f"Failed to build memory context: {e}")
            return None
    
    def build_guidelines(self) -> str:
        """Build guidelines section: discipline rules + workflow.
        
        Uses templates from config/prompt_templates.yaml
        """
        parts = []
        
        # Discipline rules (from template + position rules)
        discipline = self.agent._prompt_manager.build_discipline_rules_prompt(
            position_type=self.agent.position_id,
            position_rules=self._position_rules,
        )
        parts.append(discipline)
        
        # Workflow (from template)
        workflow = self.agent._prompt_manager.build_workflow_prompt(
            position_type=self.agent.position_id
        )
        parts.append(workflow)
        
        return "\n\n".join(parts)
    
    def build_tool_usage(self) -> str:
        """Build detailed tool usage section."""
        tools = [
            "- **completeDialog**: 完成对话并发送最终回复。**这是唯一结束处理的方式！**",
            "    - 参数: content（回复内容）, mentions（可选，要@的用户列表）",
            "- **sendMessage**: 发送中间消息（如'让我查一下'）",
            "- **executeBashCommand**: 在容器中执行 bash 命令",
            "- **rememberFact**: 记录重要事实到长期记忆",
            "- **searchMemories**: 搜索已记录的记忆",
            "- **generateSummary**: 生成当前会话摘要",
            "- **forget**: 删除不再需要的记忆",
        ]
        
        if self.agent.config.skills:
            tools.extend([
                "- **activateSkill**: 激活一个技能",
                "- **loadSkillResource**: 加载技能目录下的资源文件",
                "- **deactivateSkill**: 关闭技能释放资源",
            ])
        
        return PromptSection("## 工具说明", "\n".join(tools)).render()
    
    def build_important_notes(self) -> str:
        """Build important notes section (from template)."""
        return self.agent._prompt_manager.build_important_notes_prompt(
            position_type=self.agent.position_id
        )
    
    def build_mention_guide(self) -> str:
        """Build @mention mechanism guide."""
        notes = [
            "- ⚠️ **必须使用 completeDialog 结束**: 直接输出文本不会发送消息，必须调用 completeDialog 工具",
            "- ⚠️ **mentions 参数**: 只有通过工具的 `mentions` 参数指定的人才会收到通知",
            "  - 当你在 completeDialog 中设置 mentions=['林知遥', '江予白']，这些人会被唤醒",
            "  - 在 content 中写 '@林知遥' 只是显示效果，不会真正通知对方",
            "- 使用表格中'名字'列的完整姓名作为 mentions 参数（如 ['林知遥', '江予白']）",
        ]
        
        if self.agent.config.skills:
            notes.extend([
                "- 使用技能时，先调用 activateSkill 加载技能指南",
                "- 技能脚本执行 (在 Docker 容器中，skills 目录挂载于 /skills):",
                "  - Bash 脚本: `bash /skills/{skillName}/scripts/xxx.sh [args...]`",
                "  - Python 脚本: `python3 /skills/{skillName}/scripts/xxx.py [args...]`",
                "  - Python PEP 723 脚本 (带内联依赖): `uv run /skills/{skillName}/scripts/xxx.py`",
            ])
        
        return PromptSection("## @mention 机制说明", "\n".join(notes)).render()
    
    # =========================================================================
    # Main Build Method
    # =========================================================================
    
    async def build(self) -> str:
        """Build complete system prompt by composing all sections.
        
        Returns:
            Complete system prompt string
        """
        sections = [
            self.build_identity(),
            self.build_capabilities(),
            self.build_role(),
            self.build_team(),
            self.build_skills(),
            await self.build_memory(),
            self.build_guidelines(),
            self.build_tool_usage(),
            self.build_important_notes(),
            self.build_mention_guide(),
        ]
        
        # Filter out None/empty sections and join with double newlines
        return "\n\n".join(filter(None, sections))


__all__ = ["SystemPromptBuilder", "PromptSection"]
