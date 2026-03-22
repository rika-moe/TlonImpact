"""System prompt builder for Agent.

Provides a clean, composable way to build agent system prompts.
Accepts AgentPromptContext — a plain data object — with no reference
to Agent or any infrastructure component.
"""

from tlon.agents.prompt_context import AgentPromptContext


class PromptSection:
    """A section of the system prompt.

    Attributes:
        title: Section title (e.g., "## 可用工具")
        content: Section content
    """

    def __init__(self, title: str, content: str | None = None):
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
        context = AgentPromptContext(...)
        builder = SystemPromptBuilder(context)
        prompt = builder.build()
    """

    def __init__(self, context: AgentPromptContext):
        self.context = context

    @property
    def _config(self):
        return self.context.config

    @property
    def role_title(self) -> str:
        role = self._config.role
        return role.title if role and role.title else self._config.name

    @property
    def role_jd(self) -> str:
        role = self._config.role
        return role.jd if role else ""

    @property
    def role_rules(self) -> list[str]:
        role = self._config.role
        return role.rules if role else []

    # =========================================================================
    # Team Building Helpers
    # =========================================================================

    def _get_role_info_from_config(self, member_config) -> dict[str, str]:
        role = member_config.role
        if role:
            jd_summary = role.jd.strip().split("\n")[0] if role.jd else ""
            if len(jd_summary) > 30:
                jd_summary = jd_summary[:30] + "..."
            return {
                "role": role.title or member_config.name,
                "desc": jd_summary or "协助处理问题",
                "when": role.contact_when or "需要协助时",
            }
        return {"role": member_config.name, "desc": "协助处理问题", "when": "需要协助时"}

    def _build_team_table(self) -> str:
        lines = []
        lines.append("| 名字（@mention时用这个名字） | 职务 | 职责 | 何时@他 |")
        lines.append("|---------------------------|------|------|---------|")
        lines.append("| 用户 | 用户 | 提出需求和问题 | 任务完成时汇报 |")

        for member_config in self.context.team_members:
            if member_config.id == self._config.id:
                continue
            role_info = self._get_role_info_from_config(member_config)
            lines.append(
                f"| {member_config.name} | {role_info['role']} | {role_info['desc']} | {role_info['when']} |"
            )

        return "\n".join(lines) if len(lines) > 2 else ""

    # =========================================================================
    # Section Builders
    # =========================================================================

    def build_identity(self) -> str:
        lines = [
            f"# 你是 {self._config.name}",
            "",
            f"岗位: {self.role_title}",
        ]
        if self.role_jd:
            lines.extend(["", "岗位职责:", self.role_jd])
        if self._config.personality:
            lines.extend(["", f"个人特点: {self._config.personality}"])
        return "\n".join(lines)

    def build_capabilities(self) -> str:
        """Build capabilities/tools section from dynamic tool definitions."""
        lines = [
            f"- {t['function']['name']}: {t['function']['description']}"
            for t in self.context.tool_definitions
        ]
        return PromptSection("## 可用工具", "\n".join(lines)).render()

    def build_role(self) -> str | None:
        if not self.role_jd:
            return None
        return PromptSection("## 角色职责", f"岗位职责:\n{self.role_jd}").render()

    def build_team(self) -> str | None:
        team_table = self._build_team_table()
        if not team_table:
            return None
        return PromptSection("## 团队成员（你可以 @他们）", team_table).render()

    def build_skills(self) -> str | None:
        return None

    def build_memory(self) -> str | None:
        """Build memory context section from pre-fetched memory blocks."""
        try:
            if not self.context.memory_blocks:
                return None
            parts = ["## 历史记忆"]
            for block in self.context.memory_blocks:
                msg = block.to_openai_message()
                parts.append(msg["content"])
            return "\n\n".join(parts)
        except Exception:
            from tlon.base.logger import get_logger

            logger = get_logger(__name__)
            logger.warning("Failed to build memory context from pre-fetched blocks")
            return None

    def build_discipline_rules(self) -> str:
        rules = [
            "## 协作纪律",
            "",
            "1. **收到任务时**: 确认收到即可，不要重复 @发送者",
            '   - ✅ 正确: "收到，我来处理"',
            '   - ❌ 错误: "@客服 收到，我来处理"（不要回@）',
            "",
            "2. **任务完成时**: 向任务分派者汇报",
            "   - 如果 @项目经理 派的活 → 完成后 @项目经理",
            "   - 如果 @人类 直接派的活 → 完成后 @人类",
            "",
            '3. **避免无意义回应**: 不要对 "收到"、"好的"、"谢谢"进行回复',
            "",
            "4. **循环检测**: 如果你和另一个 Agent 来回@超过2轮，停止回复，让人类介入",
            "",
            "5. **@mention 规则**: 使用 completeDialog 的 mentions 参数指定负责人",
            "   - 只有被 mentions 的人才会收到通知并响应",
        ]
        return "\n".join(rules)

    def build_guidelines(self) -> str:
        parts = [self.build_discipline_rules()]
        workflow = self.context.prompt_manager.build_workflow_prompt(role=self._config.role)
        parts.append(workflow)
        return "\n\n".join(parts)

    def build_tool_usage(self) -> str:
        """Build detailed tool usage section from dynamic tool definitions."""
        lines = [
            f"- **{t['function']['name']}**: {t['function']['description']}"
            for t in self.context.tool_definitions
        ]
        return PromptSection("## 工具说明", "\n".join(lines)).render()

    def build_important_notes(self) -> str:
        return self.context.prompt_manager.build_important_notes_prompt(role=self._config.role)

    def build_mention_guide(self) -> str:
        notes = [
            "- ⚠️ **必须使用 completeDialog 结束**: 直接输出文本不会发送消息，必须调用 completeDialog 工具",
            "- ⚠️ **mentions 参数**: 只有通过工具的 `mentions` 参数指定的人才会收到通知",
            "  - 当你在 completeDialog 中设置 mentions=['林知遥', '江予白']，这些人会被唤醒",
            "  - 在 content 中写 '@林知遥' 只是显示效果，不会真正通知对方",
            "- 使用表格中'名字'列的完整姓名作为 mentions 参数（如 ['林知遥', '江予白']）",
        ]
        return PromptSection("## @mention 机制说明", "\n".join(notes)).render()

    # =========================================================================
    # Main Build Method
    # =========================================================================

    def build(self) -> str:
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
            self.build_memory(),
            self.build_guidelines(),
            self.build_tool_usage(),
            self.build_important_notes(),
            self.build_mention_guide(),
        ]
        return "\n\n".join(filter(None, sections))


__all__ = ["SystemPromptBuilder", "PromptSection"]
