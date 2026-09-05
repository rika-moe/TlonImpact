"""Context assembly for Agent.

Builds the LLM conversation context (memory knowledge blocks + skills
index + current doing task + mailbox history + current mail) and the
system prompt (persona files with mtime-keyed caching). Extracted from
the Agent god class; complements prompt_builder's pure
SystemPromptBuilder with the agent-bound fetching/caching side.

P4: the builder receives its dependencies (config, team members,
mailbox, memory, task manager, rule, address) explicitly and owns the
system prompt cache — no back-reference to the Agent.
"""

from pathlib import Path
from typing import Any

from tlon.agents.member_spec import MemberRule, has_section
from tlon.agents.prompt_builder import SystemPromptBuilder
from tlon.agents.prompt_context import AgentPromptContext
from tlon.agents.skills import Signature, render_skills_index, scan_signature, scan_skills
from tlon.base.config import AgentConfig
from tlon.base.logger import get_logger
from tlon.base.paths import paths
from tlon.memory.manager import MemoryManager
from tlon.organization.mail.mail import Mail
from tlon.organization.mail.mailbox import Mailbox
from tlon.tools.tasks.manager import TaskManager

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

    Owns the system prompt cache (``_system_prompt_cache`` /
    ``_system_prompt_mtime``), keyed on the mtime signature of all
    persona files.
    """

    def __init__(
        self,
        config: AgentConfig,
        team_members: list[AgentConfig],
        mailbox: Mailbox | None,
        memory: MemoryManager,
        task_manager: TaskManager,
        rule: MemberRule,
        address: str,
    ):
        self._config = config
        self._team_members = team_members
        self._mailbox = mailbox
        self._memory = memory
        self._task_manager = task_manager
        # A context builder serves a cognitive agent — its rule carries a loop.
        assert rule.loop is not None, "ContextBuilder requires a cognitive rule"
        self._rule = rule
        self._loop = rule.loop
        self._address = address
        # Prompt cache — rebuilt when persona file mtimes change
        self._system_prompt_cache: str | None = None
        self._system_prompt_mtime: tuple[tuple[str, float | None], ...] | None = None
        # Skills index cache — rebuilt when the skill landscape's mtime
        # signature changes (ADR 0010)
        self._skills_index_cache: str | None = None
        self._skills_signature: Signature | None = None

    async def build_knowledge_context(self) -> str:
        """Build knowledge text from memory blocks for user prompt injection."""
        try:
            blocks = await self._memory.build_context_blocks()
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

    async def build_skills_index(self) -> str:
        """Build the skills index text (mtime-signature cached, ADR 0010).

        Empty string when the agent has no skills in any layer. Scan or
        render failures degrade to empty — a broken skill dir must never
        block context assembly.
        """
        try:
            signature = scan_signature(self._config.id)
            if self._skills_index_cache is None or signature != self._skills_signature:
                self._skills_index_cache = render_skills_index(scan_skills(self._config.id))
                self._skills_signature = signature
            return self._skills_index_cache
        except Exception:
            logger.warning("skills_index_build_failed", exc_info=True)
            return ""

    def build_current_task(self) -> str:
        """Build the current-task text (the ``(current-task)`` section).

        Light by design: only the doing task (at most one — the
        TaskManager pauses any other when a new task starts), never the
        full task list, and unlike ``TaskManager.get_current_task`` no
        paused-task fallback — a paused task is not what the agent is
        doing. Empty string when no task is doing (the section is then
        skipped, same empty-state convention as knowledge/skills). Task
        reads are in-memory, but failures still degrade to empty — a
        broken task store must never block context assembly.

        Anchor caveat: the context is built once per session (the first
        ``LoopDriver`` step), so a task started or completed mid-session
        is only reflected from the next session on.
        """
        try:
            doing = self._task_manager.list_tasks(status_filter="doing")
            if not doing:
                return ""
            task = doing[0]
            lines = ["[当前任务]", f"{task.id}: {task.title}（{task.status.value}）"]
            if task.description:
                lines.append(f"描述：{task.description}")
            return "\n".join(lines)
        except Exception:
            logger.warning("current_task_build_failed", exc_info=True)
            return ""

    async def build_context(self, current_mail: Mail) -> list[dict[str, Any]]:
        """Build conversation context for LLM (sections interpreted in order).

        The ``:context`` section vector (phase 7b) is a program: each
        conversational section appends its messages in vector order.
        ``persona``/``protocol`` feed the system prompt instead and are
        skipped here. The current mail is always appended last — the
        engine owns this; the DSL only describes what comes before it.
        """
        context: list[dict[str, Any]] = []
        for form in self._loop.context_sections:
            name = str(form[0])
            if name == "knowledge":
                knowledge = await self.build_knowledge_context()
                if knowledge:
                    context.append({"role": "user", "content": f"[已知信息]\n\n{knowledge}"})
            elif name == "skills":
                index = await self.build_skills_index()
                if index:
                    context.append({"role": "user", "content": index})
            elif name == "current-task":
                current_task = self.build_current_task()
                if current_task:
                    context.append({"role": "user", "content": current_task})
            elif name == "mail-history":
                context.extend(await self._build_mail_history(form[1], current_mail))

        context.append({"role": "user", "content": _render_mail(current_mail)})
        return context

    async def _build_mail_history(
        self, count: int, current_mail: Mail
    ) -> list[dict[str, Any]]:
        """Render up to ``count`` mailbox history mails as context messages.

        The history is the agent's own correspondence (mailbox inbox +
        sent), never a shared room log: my own mail maps to the
        assistant role, everyone else's to the user role.
        """
        if self._mailbox is None:
            logger.warning("mailbox_missing")
            return []
        if count <= 0:
            return []
        history = await self._mailbox.recent(limit=count)
        logger.debug("context_history_retrieved", mail_count=len(history))
        messages: list[dict[str, Any]] = []
        for mail in history:
            if mail.id == current_mail.id:
                continue
            if not mail.content:
                continue
            role = "assistant" if mail.from_address == self._address else "user"
            messages.append({"role": role, "content": _render_mail(mail)})
        return messages

    async def build_prompt_context(self) -> AgentPromptContext:
        """Build AgentPromptContext by pre-fetching team SOUL.md files."""
        team_profiles: list[tuple[str, str]] = []
        for m in self._team_members:
            if m.id == self._config.id:
                continue
            soul_file = paths.agent_soul_file(m.id)
            profile = soul_file.read_text(encoding="utf-8").strip() if soul_file.exists() else m.name
            team_profiles.append((m.name, profile))

        return AgentPromptContext(
            agent_name=self._config.name,
            agent_home=str(paths.agent_dir(self._config.id)),
            team_profiles=team_profiles,
        )

    def persona_files(self) -> list[Path]:
        """Persona files that feed the system prompt (self + team SOUL files)."""
        files = [
            paths.agent_identity_file(self._config.id),
            paths.agent_soul_file(self._config.id),
            paths.agent_rules_file(self._config.id),
        ]
        files.extend(
            paths.agent_soul_file(m.id)
            for m in self._team_members
            if m.id != self._config.id
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
        signature = self.persona_mtime_signature(self.persona_files())
        if self._system_prompt_cache is None or signature != self._system_prompt_mtime:
            context = await self.build_prompt_context()
            builder = SystemPromptBuilder(
                context,
                include_persona=has_section(self._loop.context_sections, "persona"),
                include_protocol=has_section(self._loop.context_sections, "protocol"),
            )
            self._system_prompt_cache = builder.build()
            self._system_prompt_mtime = signature
        return self._system_prompt_cache
