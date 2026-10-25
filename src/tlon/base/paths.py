"""Centralized path resolution for Tlon filesystem layout.

All filesystem paths MUST be constructed through this module.
No other module should hardcode directory structure assumptions.

Pure resolver: every accessor on ``TlonPaths`` is side-effect free — it
computes and returns a :class:`Path` and never touches the filesystem.
Directory creation lives at the actual write points (each writer creates
its own parent). The single exception is the process bootstrap in
``_resolve_tlon_home``, which materializes the default ``~/.tlon`` root.

Layout:
    ~/.tlon/
    ├── cron.yaml
    ├── shared/
    │   ├── traces/
    │   │   └── trace-{uuid}.jsonl
    │   ├── logs/
    │   │   └── server-{date}.jsonl
    │   ├── mail.sqlite3
    │   └── spawn-quota.json
    ├── {agent_id}/
    │   ├── config/
    │   │   ├── agent.yaml
    │   │   └── LESSONS.md
    │   ├── skills/
    │   │   └── {skill}/SKILL.md
    │   ├── tasks.jsonl
    │   ├── ledger.jsonl
    │   ├── workspace/
    │   │   ├── memory/ (optional member-authored notes)
    │   │   └── .agents/skills/{skill}/SKILL.md
    │   └── mail/ (legacy offline archive; never read by the post office)
    ├── skills/
    │   └── {skill}/SKILL.md
    └── ...

There is no module-level instance: every consumer holds an explicit
``TlonPaths`` (constructor injection). Process entry points (server,
launcher) construct one from the environment (``TLON_HOME``, default
``~/.tlon``) and hand it down; tests construct isolated ones.

Usage:
    >>> from tlon.base.paths import TlonPaths
    >>> paths = TlonPaths()  # $TLON_HOME or ~/.tlon
    >>> cfg = paths.agent_config_file("dev-001")

    # For custom tlon_home (e.g., in tests):
    >>> tmp = TlonPaths(home=Path("/tmp/test-tlon"))
    >>> cfg = tmp.agent_config_file("dev-001")
"""

import os
from pathlib import Path


def _resolve_tlon_home() -> Path:
    """Resolve TLON_HOME from environment or default to ~/.tlon.

    An explicitly set ``TLON_HOME`` must be honored or fail loudly:
    a missing/non-directory value raises instead of silently falling
    back to ``~/.tlon`` — the fallback direction is *into* the real
    data root, so a typo would run against the live society. The one
    side effect in this module: a missing default ``~/.tlon`` root is
    created here (process bootstrap behavior).
    """
    tlon_home = os.getenv("TLON_HOME")
    if tlon_home:
        path = Path(tlon_home).expanduser().resolve()
        if not path.is_dir():
            raise RuntimeError(
                f"TLON_HOME={tlon_home!r} 指向的目录不存在或不是目录；"
                f"显式设置的环境变量不会被静默忽略（请先创建该目录，或取消设置以使用默认 ~/.tlon）"
            )
        return path
    default = Path.home() / ".tlon"
    default.mkdir(parents=True, exist_ok=True)
    return default


class TlonPaths:
    """Centralized path resolver for the ~/.tlon/ filesystem layout.

    Explicitly injected everywhere — construct one at the process entry
    point (``TlonPaths()`` resolves ``$TLON_HOME`` / ``~/.tlon``) and
    pass it down; construct your own ``TlonPaths(home=...)`` for
    isolated environments (tests, multi-runtime embedding).

    Pure resolver: every accessor below is side-effect free. Writers
    create their own parent directories at the actual write point.
    """

    def __init__(self, home: Path | str | None = None):
        self._home = Path(home) if home is not None else _resolve_tlon_home()

    # ===== Root =====

    def tlon_home(self) -> Path:
        return self._home

    def env_file(self) -> Path:
        """The process-level env config (``{TLON_HOME}/.env``).

        Loaded by ``base/settings.py`` as a fallback source of environment
        variables; real environment variables always win. Does not create
        the file — an absent .env is the normal first-run state.
        """
        return self._home / ".env"

    def webui_identity_file(self) -> Path:
        """Instance-local WebUI member identity (ADR 0033)."""
        return self._home / "webui-identity.json"

    def initial_society_state_file(self) -> Path:
        """One-time initial society plan and completion status (ADR 0036)."""
        return self._home / "initial-society.json"

    def initial_society_lock_file(self) -> Path:
        """SQLite initialization mutex, independent of application databases."""
        return self._home / "initial-society-lock.sqlite3"

    def initial_society_staging_dir(self) -> Path:
        """Private staging root; members are published only when complete."""
        return self._home / ".initial-society-staging"

    def shared_dir(self) -> Path:
        return self._home / "shared"

    # ===== Per-agent =====

    def agent_dir(self, agent_id: str) -> Path:
        return self._home / agent_id

    def agent_config_dir(self, agent_id: str) -> Path:
        return self._home / agent_id / "config"

    def agent_config_file(self, agent_id: str) -> Path:
        return self.agent_config_dir(agent_id) / "agent.yaml"

    def agent_workspace_dir(self, agent_id: str) -> Path:
        return self._home / agent_id / "workspace"

    def agent_tasks_file(self, agent_id: str) -> Path:
        return self.agent_dir(agent_id) / "tasks.jsonl"

    def agent_ledger_file(self, agent_id: str) -> Path:
        """~/.tlon/{agent_id}/ledger.jsonl — per-member token ledger (ADR 0014).

        Append-only JSONL written by ``base/ledger.py`` (one record per
        attributed ``LLMClient.chat`` call); the writer creates the
        parent directory on append.
        """
        return self._home / agent_id / "ledger.jsonl"

    def agent_program_database_file(self, agent_id: str) -> Path:
        """Member private structured state and program effect records (ADR 0043)."""
        return self.agent_dir(agent_id) / "program.sqlite3"

    def agent_mail_dir(self, agent_id: str) -> Path:
        return self._home / agent_id / "mail"

    def member_mail_dir(self, member_id: str) -> Path:
        """Resolve the mailbox directory for any member id.

        Human ids (``human:<username>``) map to ``human-<username>/mail/``
        — the colon is replaced by a hyphen here, and only here. All other
        ids are agent ids and reuse the agent home.
        """
        if member_id.startswith("human:"):
            username = member_id[len("human:") :]
            return self._home / f"human-{username}" / "mail"
        return self.agent_mail_dir(member_id)

    def mail_database_file(self) -> Path:
        """Shared authoritative SQLite post office (ADR 0030)."""
        return self.shared_dir() / "mail.sqlite3"

    # ===== Society-level config =====

    def cron_file(self) -> Path:
        """~/.tlon/cron.yaml — declared periodic task mails
        (docs/design/mail.md 「周期任务：tick 即邮件」).

        Read once at ``CronScheduler.initialize``; edits take effect on
        restart. An absent file is the normal state (no declared entries).
        """
        return self._home / "cron.yaml"

    def policy_file(self) -> Path:
        """~/.tlon/policy.tlon — the society policy spec (ADR 0022).

        Human-written only: the file lives at the society root, outside
        every member's ``allowed_paths`` and the prototype staging
        channel. Read on each policy evaluation (spawn/assembly are
        low-frequency); an absent file means the built-in default policy
        (``agents/policy.py`` ``DEFAULT_POLICY_EDN``) — the normal state.
        """
        return self._home / "policy.tlon"

    # ===== Agent persona files =====

    def agent_identity_file(self, agent_id: str) -> Path:
        """~/.tlon/{agent_id}/config/IDENTITY.md"""
        return self.agent_config_dir(agent_id) / "IDENTITY.md"

    def agent_soul_file(self, agent_id: str) -> Path:
        """~/.tlon/{agent_id}/config/SOUL.md"""
        return self.agent_config_dir(agent_id) / "SOUL.md"

    def agent_rules_file(self, agent_id: str) -> Path:
        """~/.tlon/{agent_id}/config/RULES.md"""
        return self.agent_config_dir(agent_id) / "RULES.md"

    def agent_contacts_file(self, agent_id: str) -> Path:
        """The member's ordinary private workspace contact book (ADR 0047).

        Its Markdown format is owned by the member. Missing files are not
        synthesized: members may create one using ordinary file tools.
        """
        return self.agent_workspace_dir(agent_id) / "CONTACTS.md"

    def agent_behavior_file(self, agent_id: str) -> Path:
        """~/.tlon/{agent_id}/config/behavior.tlon (unified rules-form spec).

        Every member carries exactly this
        one file (phase C unified the format; ``script.tlon`` is gone).
        """
        return self._home / agent_id / "config" / "behavior.tlon"

    def agent_lessons_file(self, agent_id: str) -> Path:
        """~/.tlon/{agent_id}/config/LESSONS.md — lessons inherited at birth
        (see docs/design/prototypes.md).

        Copied from the prototype's ``LESSONS.md`` by ``instantiate`` and
        injected into the conversation by the DSL ``(lessons)`` context
        section; an absent file is the normal state for a member whose
        prototype carries no lessons.
        """
        return self._home / agent_id / "config" / "LESSONS.md"

    # ===== Skills (ADR 0010) =====

    def skills_dir(self) -> Path:
        """~/.tlon/skills/ — global skill library root (shared across agents)."""
        return self._home / "skills"

    def agent_skills_dir(self, agent_id: str) -> Path:
        """~/.tlon/{agent_id}/skills/ — agent-private skills."""
        return self._home / agent_id / "skills"

    def agent_workspace_skills_dir(self, agent_id: str) -> Path:
        """~/.tlon/{agent_id}/workspace/.agents/skills/ — project-level skills.

        The workspace bind-mounts into the agent's container, so this is
        also the container's ``/workspace/.agents/skills/``; the engine
        reads it from the host, never via container exec.
        """
        return self._home / agent_id / "workspace" / ".agents" / "skills"

    # ===== Behavior prototypes =====

    def prototypes_dir(self) -> Path:
        """~/.tlon/prototypes/ — behavior prototype library root."""
        return self._home / "prototypes"

    def prototypes_staging_dir(self) -> Path:
        """~/.tlon/prototypes-staging/ — LLM-authored prototypes awaiting approval."""
        return self._home / "prototypes-staging"

    # ===== Shared =====

    def traces_dir(self) -> Path:
        return self.shared_dir() / "traces"

    def logs_dir(self) -> Path:
        return self.shared_dir() / "logs"

    def spawn_quota_ledger_file(self) -> Path:
        """~/.tlon/shared/spawn-quota.json — persisted spawn quota ledger.

        The (spawner, run) spawn quota ledger (``agents/spawn.py``
        ``SpawnManager``) survives process restarts: runs themselves
        persist in ProgramStore, so the anti-runaway quota
        must persist too.
        """
        return self.shared_dir() / "spawn-quota.json"
