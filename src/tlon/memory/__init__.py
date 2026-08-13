"""Memory system for Agents.

Provides long-term memory persistence and retrieval with a two-layer architecture:
- Working Memory: Recent interactions in the current session
- Episodic Memory: Cross-session summaries and key facts

Usage:
    >>> from tlon.memory import MemoryManager, MemoryConfig
    >>> config = MemoryConfig()
    >>> memory = MemoryManager(agent_id="dev-001", config=config, data_dir=Path("./data"))
    >>> await memory.load()
    >>> await memory.add_interaction(message, response, tool_results)
"""
