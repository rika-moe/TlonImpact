"""System prompt builder for Agent.

Provides a clean, composable way to build agent system prompts.
Accepts AgentPromptContext — a plain data object — with no reference
to Agent or any infrastructure component.
"""

from tlon.agents.prompt_context import AgentPromptContext

_PROTOCOL = """## 交互协议

1. **消息格式**
   - 用户消息：`user: 消息内容`
   - 同事广播：`agent_name: 消息内容`
   - 被@消息：`agent_name @你: 消息内容`

2. **输出机制**
   - 必须使用 `sendMessage` 工具发送所有消息
   - 直接输出文本不会发送消息
   - sendMessage 的 `is_complete` 参数控制循环：true=最终回复（结束本轮），false=中间进度（继续思考）
   - 当 `is_complete=true` 时，本轮只能调用 sendMessage，不能同时调用其他工具
   - 只有通过 `sendMessage` 的 `mentions` 参数指定的人才会收到通知
   - content 中写 `@林知遥` 只是显示效果，不会真正通知对方

3. **协作规则**
   - 收到任务时确认即可，不要重复 @发送者
   - 任务完成向分派者汇报
   - 和同一 agent 来回超过 2 轮，停止回复，让人类介入"""


class SystemPromptBuilder:
    """Builds system prompts by composing sections.

    Usage:
        context = AgentPromptContext(...)
        builder = SystemPromptBuilder(context)
        prompt = builder.build()
    """

    def __init__(self, context: AgentPromptContext):
        self.context = context

    def build_identity(self) -> str:
        """Build identity section."""
        parts = [f"你是 {self.context.config.name}。"]
        if self.context.config.identity:
            parts.append("")
            parts.append(self.context.config.identity)
        return "\n".join(parts)

    def build_team(self) -> str | None:
        """Build team section from teammates' profiles."""
        if not self.context.team_profiles:
            return None

        lines = ["## 团队成员"]
        for name, profile in self.context.team_profiles:
            lines.append("")
            lines.append(f"### {name}")
            lines.append(profile)

        return "\n".join(lines)

    def build_behavior(self) -> str | None:
        """Build behavior section."""
        if not self.context.config.behavior:
            return None
        return f"## 行为守则\n\n{self.context.config.behavior}"

    def build_protocol(self) -> str:
        """Build protocol section (framework hardcoded)."""
        return _PROTOCOL

    def build(self) -> str:
        """Build complete system prompt by composing all sections.

        Returns:
            Complete system prompt string
        """
        sections = [
            self.build_identity(),
            self.build_team(),
            self.build_behavior(),
            self.build_protocol(),
        ]
        return "\n\n".join(filter(None, sections))


__all__ = ["SystemPromptBuilder"]
