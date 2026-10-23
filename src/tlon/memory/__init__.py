"""Memory system for Agents.

Legacy journals and module program receipts provide completed interaction
sources, which are distilled in batches into claims — natural-language
assertions with provenance — under the claim model (ADR 0017).
Distillation tracks consumed source IDs and a frozen pending batch in a
durable checkpoint.

Usage:
    >>> from tlon.memory import MemoryManager, MemoryConfig
    >>> config = MemoryConfig()
    >>> memory = MemoryManager(agent_id="dev-001", config=config, data_dir=Path("./data"))
    >>> await memory.load()
    >>> await memory.maybe_distill_completed_sessions(journal)
"""
