"""Centralized path resolution for Tlon filesystem layout.

All filesystem paths MUST be constructed through this module.
No other module should hardcode directory structure assumptions.

Layout:
    ~/.tlon/
    ├── shared/
    │   ├── chat_history/
    │   ├── traces/
    │   │   └── trace-{uuid}.jsonl
    │   └── logs/
    │       └── server-{date}.jsonl
    ├── {agent_id}/
    │   ├── config/
    │   │   └── agent.yaml
    │   ├── memory/
    │   │   ├── config.json
    │   │   ├── working/
    │   │   │   └── interactions.jsonl
    │   │   └── episodic/
    │   │       ├── summaries.jsonl
    │   │       └── facts.jsonl
    │   ├── tasks.jsonl
    │   ├── workspace/
    │   └── mail/
    │       ├── inbox.jsonl
    │       ├── sent.jsonl
    │       └── inbox.cursor
    ├── human-{username}/
    │   └── mail/
    │       └── ...
    └── ...

Usage:
    >>> from tlon.base.paths import paths
    >>> cfg = paths.agent_config_file("dev-001")

    # For custom tlon_home (e.g., in tests):
    >>> from tlon.base.paths import paths
    >>> tmp = TlonPaths(home=Path("/tmp/test-tlon"))
    >>> cfg = tmp.agent_config_file("dev-001")
"""

import os
from pathlib import Path


def _resolve_tlon_home() -> Path:
    """Resolve TLON_HOME from environment or default to ~/.tlon."""
    tlon_home = os.getenv("TLON_HOME")
    if tlon_home:
        path = Path(tlon_home).expanduser().resolve()
        if path.exists() and path.is_dir():
            return path
    default = Path.home() / ".tlon"
    default.mkdir(parents=True, exist_ok=True)
    return default


class TlonPaths:
    """Centralized path resolver for the ~/.tlon/ filesystem layout.

    Singleton-like: import ``paths`` from this module for the standard
    ``~/.tlon`` instance. Construct your own ``TlonPaths(home=...)``
    for isolated environments (tests, etc.).
    """

    def __init__(self, home: Path | str | None = None):
        self._home = Path(home) if home is not None else _resolve_tlon_home()

    # ===== Root =====

    def tlon_home(self) -> Path:
        return self._home

    def shared_dir(self) -> Path:
        d = self._home / "shared"
        d.mkdir(parents=True, exist_ok=True)
        return d

    # ===== Per-agent =====

    def agent_dir(self, agent_id: str) -> Path:
        d = self._home / agent_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def agent_config_dir(self, agent_id: str) -> Path:
        d = self._home / agent_id / "config"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def agent_config_file(self, agent_id: str) -> Path:
        return self.agent_config_dir(agent_id) / "agent.yaml"

    def agent_memory_dir(self, agent_id: str) -> Path:
        d = self._home / agent_id / "memory"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def agent_workspace_dir(self, agent_id: str) -> Path:
        d = self._home / agent_id / "workspace"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def agent_tasks_file(self, agent_id: str) -> Path:
        return self.agent_dir(agent_id) / "tasks.jsonl"

    def agent_mail_dir(self, agent_id: str) -> Path:
        d = self._home / agent_id / "mail"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def member_mail_dir(self, member_id: str) -> Path:
        """Resolve the mailbox directory for any member id.

        Human ids (``human:<username>``) map to ``human-<username>/mail/``
        — the colon is replaced by a hyphen here, and only here. All other
        ids are agent ids and reuse the agent home.
        """
        if member_id.startswith("human:"):
            username = member_id[len("human:"):]
            d = self._home / f"human-{username}" / "mail"
            d.mkdir(parents=True, exist_ok=True)
            return d
        return self.agent_mail_dir(member_id)

    def member_mail_dir_existing(self, member_id: str) -> Path | None:
        """Like :meth:`member_mail_dir` but never creates anything.

        Returns the mailbox directory when it already exists on disk,
        ``None`` otherwise. Used for existence checks (e.g. "is this a
        known human") where calling ``member_mail_dir`` would create the
        directory and defeat the check.
        """
        if member_id.startswith("human:"):
            username = member_id[len("human:"):]
            d = self._home / f"human-{username}" / "mail"
        else:
            d = self._home / member_id / "mail"
        return d if d.is_dir() else None

    def known_human_ids(self) -> list[str]:
        """List human member ids that have a persistent mailbox directory.

        A human becomes "known" once their mailbox exists (created on
        first session attach, first delivery, or first ``/inbox`` read).
        Mail to a known-but-offline human waits in their inbox
        (docs/design/mail.md 人类侧).
        """
        ids: list[str] = []
        for child in sorted(self._home.glob("human-*")):
            if child.is_dir() and (child / "mail").is_dir():
                ids.append(f"human:{child.name[len('human-'):]}")
        return ids

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

    def agent_behavior_file(self, agent_id: str) -> Path:
        """~/.tlon/{agent_id}/config/behavior.tlon (unified rules-form spec).

        Read-only resolution: unlike the persona helpers this does NOT
        create directories, so checking ``exists()`` stays side-effect free.
        Every member — cognitive or deterministic — carries exactly this
        one file (phase C unified the format; ``script.tlon`` is gone).
        """
        return self._home / agent_id / "config" / "behavior.tlon"

    # ===== Behavior prototypes =====

    def prototypes_dir(self) -> Path:
        """~/.tlon/prototypes/ — behavior prototype library root.

        Read-only resolution: does NOT create the directory, so checking
        ``exists()`` stays side-effect free.
        """
        return self._home / "prototypes"

    def prototypes_staging_dir(self) -> Path:
        """~/.tlon/prototypes-staging/ — LLM-authored prototypes awaiting approval.

        Read-only resolution: does NOT create the directory, so checking
        ``exists()`` stays side-effect free.
        """
        return self._home / "prototypes-staging"

    # ===== Memory =====

    def memory_config_file(self, agent_id: str) -> Path:
        return self.agent_memory_dir(agent_id) / "config.json"

    def memory_working_dir(self, agent_id: str) -> Path:
        d = self.agent_memory_dir(agent_id) / "working"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def memory_episodic_dir(self, agent_id: str) -> Path:
        d = self.agent_memory_dir(agent_id) / "episodic"
        d.mkdir(parents=True, exist_ok=True)
        return d

    # ===== Shared =====

    def chat_history_dir(self) -> Path:
        d = self.shared_dir() / "chat_history"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def traces_dir(self) -> Path:
        d = self.shared_dir() / "traces"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def logs_dir(self) -> Path:
        d = self.shared_dir() / "logs"
        d.mkdir(parents=True, exist_ok=True)
        return d


# Module-level default instance for convenience
paths = TlonPaths()
