"""Centralized path resolution for Tlon filesystem layout.

All filesystem paths MUST be constructed through this module.
No other module should hardcode directory structure assumptions.

Layout:
    ~/.tlon/
    ├── shared/
    │   ├── chat_history/
    │   │   └── messages.jsonl
    │   └── traces/
    │       └── trace-{uuid}.jsonl
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
    │   └── workspace/
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


# Module-level default instance for convenience
paths = TlonPaths()
