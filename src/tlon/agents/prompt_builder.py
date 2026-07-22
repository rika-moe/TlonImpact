"""System prompt builder for Agent.

Composes agent system prompt from IDENTITY.md, SOUL.md, RULES.md files
under the agent's home directory and framework-level protocol rules.
"""

from pathlib import Path

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
   - 和同一 agent 来回超过 2 轮，停止回复，让人类介入

4. **主动记忆管理**
   - 你拥有持久记忆系统，可以在思考的任何阶段使用记忆工具
   - searchMemories：遇到相关话题时主动检索过去的记忆
   - rememberFact：发现重要的用户偏好、决策或事实时立即记录
   - generateSummary：当前会话信息密集时主动触发摘要巩固
   - forget：发现过时或矛盾的记忆时主动清理
   - 记忆管理不是收尾工作，而是贯穿整个思考过程的习惯

5. **人格文件来源**
   - 你的身份、角色和行为守则来自以下文件：
     IDENTITY.md — 你是谁、你的核心自我认知
     SOUL.md     — 你的角色定位、工作风格（队友也会看到这个文件）
     RULES.md    — 你的行为约束和工作流程
   - 这些文件位于宿主机的数据目录中
   - 如需更新自己的人格设定，告知用户修改对应文件，或建议用户运行 `tlon mirror` 编辑"""


class SystemPromptBuilder:
    """Builds system prompts by composing files + protocol.

    Reads IDENTITY.md, SOUL.md, RULES.md from the agent's home directory
    and composes them with team profiles and framework protocol rules.

    ``include_persona`` gates the identity/team/behavior (persona file)
    sections; ``include_protocol`` gates the framework protocol section.
    Both default to True, which reproduces the historical full prompt.

    Usage:
        context = AgentPromptContext(...)
        builder = SystemPromptBuilder(context)
        prompt = builder.build()
    """

    def __init__(
        self,
        context: AgentPromptContext,
        include_persona: bool = True,
        include_protocol: bool = True,
    ):
        self.context = context
        self.include_persona = include_persona
        self.include_protocol = include_protocol

    def _read(self, filename: str) -> str | None:
        p = Path(self.context.agent_home) / "config" / filename
        if p.exists():
            return p.read_text(encoding="utf-8").strip()
        return None

    def build_identity(self) -> str:
        parts = [f"你是 {self.context.agent_name}。"]
        identity = self._read("IDENTITY.md")
        if identity:
            parts.append("")
            parts.append("## IDENTITY.md")
            parts.append(identity)
        soul = self._read("SOUL.md")
        if soul:
            parts.append("")
            parts.append("## SOUL.md")
            parts.append(soul)
        return "\n".join(parts)

    def build_team(self) -> str | None:
        if not self.context.team_profiles:
            return None
        lines = ["## 团队成员"]
        for name, profile in self.context.team_profiles:
            lines.append("")
            lines.append(f"### {name}")
            lines.append(profile)
        return "\n".join(lines)

    def build_behavior(self) -> str | None:
        rules = self._read("RULES.md")
        if not rules:
            return None
        return f"## RULES.md\n\n{rules}"

    def build_protocol(self) -> str:
        return _PROTOCOL

    def build(self) -> str:
        sections: list[str | None] = []
        if self.include_persona:
            sections += [
                self.build_identity(),
                self.build_team(),
                self.build_behavior(),
            ]
        if self.include_protocol:
            sections.append(self.build_protocol())
        return "\n\n".join(filter(None, sections))


__all__ = ["SystemPromptBuilder"]
