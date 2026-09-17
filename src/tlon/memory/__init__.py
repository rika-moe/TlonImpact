"""Memory system for Agents.

The session journal is the sole interaction truth (ADR 0007); completed
sessions are distilled in batches into claims — natural-language
assertions with provenance — under the claim model (ADR 0017).
Distillation progress is tracked by a scalar timestamp watermark, never
by scanning the stored claims.

Usage:
    >>> from tlon.memory import MemoryManager, MemoryConfig
    >>> config = MemoryConfig()
    >>> memory = MemoryManager(agent_id="dev-001", config=config, data_dir=Path("./data"))
    >>> await memory.load()
    >>> await memory.maybe_distill_completed_sessions(journal)
"""
