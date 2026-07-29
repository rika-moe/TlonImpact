"""Context assembly for Agent.

Builds the LLM conversation context (memory knowledge blocks + mailbox
history + current mail) and the system prompt (persona files with
mtime-keyed caching). Extracted from the Agent god class; complements
prompt_builder's pure SystemPromptBuilder with the agent-bound
fetching/caching side.
"""

from pathlib import Path
from typing import TYPE_CHECKING, Any

from tlon.agents.prompt_builder import SystemPromptBuilder
from tlon.agents.prompt_context import AgentPromptContext
from tlon.base.logger import get_logger
from tlon.base.paths import paths
from tlon.organization.mail.mail import Mail

if TYPE_CHECKING:
    from tlon.agents.agent import Agent

logger = get_logger(__name__)


def _render_mail(mail: Mail) -> str:
    """Render one mail in envelope form for the LLM context."""
    lines = [f"From: {mail.from_address}"]
    if mail.to:
        lines.append(f"To: {', '.join(mail.to)}")
    if mail.cc:
        lines.append(f"CC: {', '.join(mail.cc)}")
    if mail.subject:
        lines.append(f"Subject: {mail.subject}")
    lines.append("")
    lines.append(mail.content)
    return "\n".join(lines)


class ContextBuilder:
    """Builds conversation context and the cached system prompt for an agent.

    The system prompt cache itself lives on the Agent
    (``_system_prompt_cache`` / ``_system_prompt_mtime``) and is keyed on
    the mtime signature of all persona files.
    """

    def __init__(self, agent: "Agent"):
        self._agent = agent

    async def build_knowledge_context(self) -> str:
        """Build knowledge text from memory blocks for user prompt injection."""
        try:
            blocks = await self._agent.memory.build_context_blocks()
            if not blocks:
                return ""
            parts = []
            for block in blocks:
                msg = block.to_openai_message()
                parts.append(msg["content"])
            return "\n\n".join(parts)
        except Exception:
            logger.warning("knowledge_context_build_failed", exc_info=True)
            return ""

    async def build_context(self, current_mail: Mail) -> list[dict[str, Any]]:
        """Build conversation context for LLM (sections gated by spec.context).

        The history is the agent's own correspondence (mailbox inbox +
        sent), never a shared room log: my own mail maps to the assistant
        role, everyone else's to the user role.
        """
        context = []
        context_spec = self._agent.spec.context

        if context_spec.knowledge:
            knowledge = await self.build_knowledge_context()
            if knowledge:
                context.append({"role": "user", "content": f"[已知信息]\n\n{knowledge}"})

        history: list[Mail] = []
        if self._agent.mailbox is not None:
            if context_spec.mail_history > 0:
                history = await self._agent.mailbox.recent(limit=context_spec.mail_history)
                logger.debug("context_history_retrieved", mail_count=len(history))
        else:
            logger.warning("mailbox_missing")

        for mail in history:
            if mail.id == current_mail.id:
                continue
            if not mail.content:
                continue

            if mail.from_address == self._agent.address:
                context.append({"role": "assistant", "content": _render_mail(mail)})
            else:
                context.append({"role": "user", "content": _render_mail(mail)})

        context.append({"role": "user", "content": _render_mail(current_mail)})

        return context

    async def build_prompt_context(self) -> AgentPromptContext:
        """Build AgentPromptContext by pre-fetching team SOUL.md files."""
        team_profiles: list[tuple[str, str]] = []
        for m in self._agent.team_members:
            if m.id == self._agent.config.id:
                continue
            soul_file = paths.agent_soul_file(m.id)
            profile = soul_file.read_text(encoding="utf-8").strip() if soul_file.exists() else m.name
            team_profiles.append((m.name, profile))

        return AgentPromptContext(
            agent_name=self._agent.config.name,
            agent_home=str(paths.agent_dir(self._agent.config.id)),
            team_profiles=team_profiles,
        )

    def persona_files(self) -> list[Path]:
        """Persona files that feed the system prompt (self + team SOUL files)."""
        files = [
            paths.agent_identity_file(self._agent.config.id),
            paths.agent_soul_file(self._agent.config.id),
            paths.agent_rules_file(self._agent.config.id),
        ]
        files.extend(
            paths.agent_soul_file(m.id)
            for m in self._agent.team_members
            if m.id != self._agent.config.id
        )
        return files

    @staticmethod
    def persona_mtime_signature(files: list[Path]) -> tuple[tuple[str, float | None], ...]:
        """Snapshot persona file mtimes; missing files map to None."""
        signature: list[tuple[str, float | None]] = []
        for f in files:
            try:
                signature.append((str(f), f.stat().st_mtime))
            except OSError:
                signature.append((str(f), None))
        return tuple(signature)

    async def get_system_prompt(self) -> str:
        """Get system prompt, rebuilding the cache when persona files change.

        The cache is keyed on the mtime signature of all persona files
        (own IDENTITY/SOUL/RULES plus team SOUL files), so editing any of
        them takes effect without restarting the process.
        """
        agent = self._agent
        signature = self.persona_mtime_signature(self.persona_files())
        if agent._system_prompt_cache is None or signature != agent._system_prompt_mtime:
            # Go through the Agent delegate so tests can patch the seam
            context = await agent._build_prompt_context()
            context_spec = agent.spec.context
            builder = SystemPromptBuilder(
                context,
                include_persona=context_spec.persona,
                include_protocol=context_spec.protocol,
            )
            agent._system_prompt_cache = builder.build()
            agent._system_prompt_mtime = signature
        return agent._system_prompt_cache
