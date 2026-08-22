"""Memory system for Agents.

The working-memory layer is retired (ADR 0009): the session journal is
the sole interaction truth, and summaries flow journal -> episodic
directly. What remains is the episodic layer — long-term memory
persistence and retrieval: cross-session summaries and key facts.

Usage:
    >>> from tlon.memory import MemoryManager, MemoryConfig
    >>> config = MemoryConfig()
    >>> memory = MemoryManager(agent_id="dev-001", config=config, data_dir=Path("./data"))
    >>> await memory.load()
    >>> await memory.maybe_summarize_completed_sessions(journal)
"""
