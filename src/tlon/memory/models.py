"""Data models for Memory system."""

import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal


class FactType(StrEnum):
    """Types of facts that can be stored."""

    USER_PREFERENCE = "USER_PREFERENCE"
    PROJECT_STATE = "PROJECT_STATE"
    TECH_STACK = "TECH_STACK"
    DECISION = "DECISION"
    GENERAL = "GENERAL"


@dataclass
class MemoryConfig:
    """Configuration for Memory system.

    Attributes:
        version: Config file version
        agent_id: Associated agent ID
        summary_window: Completed unsummarized sessions before a batch
            summary is generated (ADR 0009)
        episodic_max_summaries: Maximum summaries to keep
        episodic_max_facts: Maximum facts to keep
        context_max_summaries: Summaries to include in context
        context_max_facts: Facts to include in context
        reflection_min_interactions: Completed sessions before reflection
            may trigger
    """

    version: str = "1.0"
    agent_id: str = ""

    # Summarization trigger (ADR 0009: journal -> episodic, batch by window)
    summary_window: int = 3

    # Episodic Memory settings
    episodic_max_summaries: int = 20
    episodic_max_facts: int = 50

    # Context building settings
    context_max_summaries: int = 5
    context_max_facts: int = 10

    # Reflection settings (agent self-driven memory audit). The on/off
    # switch and interval live in the behavior spec
    # (:on-complete/:reflect); only the interaction threshold stays here.
    reflection_min_interactions: int = 5

    def to_dict(self) -> dict[str, Any]:
        """Convert config to dictionary."""
        return {
            "version": self.version,
            "agent_id": self.agent_id,
            "summary": {
                "window": self.summary_window,
            },
            "episodic": {
                "max_summaries": self.episodic_max_summaries,
                "max_facts": self.episodic_max_facts,
            },
            "context": {
                "max_summaries_in_context": self.context_max_summaries,
                "max_facts_in_context": self.context_max_facts,
            },
            "reflection": {
                "min_interactions": self.reflection_min_interactions,
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MemoryConfig":
        """Create config from dictionary.

        Sections that no longer exist (``working``, ``triggers`` from the
        retired working-memory layer) are simply not read — an old
        ``config.json`` on disk loads as the current defaults.
        """
        config = cls()

        if "version" in data:
            config.version = data["version"]
        if "agent_id" in data:
            config.agent_id = data["agent_id"]

        if "summary" in data:
            config.summary_window = data["summary"].get("window", 3)

        if "episodic" in data:
            config.episodic_max_summaries = data["episodic"].get("max_summaries", 20)
            config.episodic_max_facts = data["episodic"].get("max_facts", 50)

        if "context" in data:
            ctx = data["context"]
            config.context_max_summaries = ctx.get("max_summaries_in_context", 5)
            config.context_max_facts = ctx.get("max_facts_in_context", 10)

        if "reflection" in data:
            config.reflection_min_interactions = data["reflection"].get("min_interactions", 5)

        return config


@dataclass
class SessionProjection:
    """One completed session's summarization view (ADR 0009).

    Rebuilt from the session's journal (the sole interaction truth):
    the trigger content plus, per round, the LLM response content and a
    one-line digest of each tool result.
    """

    session_id: str
    trigger_content: str
    llm_responses: list[str] = field(default_factory=list)
    tool_results: list[str] = field(default_factory=list)


@dataclass
class Summary:
    """A batch summary generated from completed sessions' journals."""

    timestamp: str
    summary_text: str
    source_session_ids: list[str] = field(default_factory=list)
    interaction_count: int = 0
    key_decisions: list[str] = field(default_factory=list)
    action_items: list[str] = field(default_factory=list)
    participants: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "summary_text": self.summary_text,
            "source_session_ids": self.source_session_ids,
            "interaction_count": self.interaction_count,
            "key_decisions": self.key_decisions,
            "action_items": self.action_items,
            "participants": self.participants,
            "keywords": self.keywords,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Summary":
        """Create from dictionary.

        Old ``summaries.jsonl`` lines predate ``source_session_ids``
        (they carry ``session_id`` / ``source_interactions`` instead):
        a missing field reads as an empty list — the old lines stay
        loadable, and their sessions are simply never deduped against.
        """
        return cls(
            id=data.get("id", str(uuid.uuid4())),
            timestamp=data["timestamp"],
            summary_text=data["summary_text"],
            source_session_ids=data.get("source_session_ids", []),
            interaction_count=data.get("interaction_count", 0),
            key_decisions=data.get("key_decisions", []),
            action_items=data.get("action_items", []),
            participants=data.get("participants", []),
            keywords=data.get("keywords", []),
        )


@dataclass
class Fact:
    """A key fact extracted from interactions.

    Facts represent structured knowledge like user preferences,
    project state, or important decisions.
    """

    timestamp: str
    session_id: str
    fact_type: FactType
    subject: str
    predicate: str
    object: str
    description: str = ""
    confidence: float = 0.8
    source_interaction: str | None = None
    version: int = 1
    superseded_by: str | None = None
    id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "session_id": self.session_id,
            "fact_type": self.fact_type.value,
            "subject": self.subject,
            "predicate": self.predicate,
            "object": self.object,
            "description": self.description,
            "confidence": self.confidence,
            "source_interaction": self.source_interaction,
            "version": self.version,
            "superseded_by": self.superseded_by,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Fact":
        """Create from dictionary."""
        fact_type = data.get("fact_type", "GENERAL")
        if isinstance(fact_type, str):
            fact_type = FactType(fact_type)

        return cls(
            id=data.get("id", str(uuid.uuid4())),
            timestamp=data["timestamp"],
            session_id=data["session_id"],
            fact_type=fact_type,
            subject=data["subject"],
            predicate=data["predicate"],
            object=data["object"],
            description=data.get("description", ""),
            confidence=data.get("confidence", 0.8),
            source_interaction=data.get("source_interaction"),
            version=data.get("version", 1),
            superseded_by=data.get("superseded_by"),
        )

    def to_natural_language(self) -> str:
        """Convert fact to natural language sentence."""
        if self.description:
            return self.description
        return f"{self.subject} {self.predicate} {self.object}"


@dataclass
class ContextBlock:
    """A block of context to be injected into LLM context.

    This represents a formatted piece of memory that can be converted
    to an OpenAI-style message for inclusion in the conversation.
    """

    block_type: Literal["episodic_summaries", "key_facts"]
    priority: int
    content: str
    source_ids: list[str] = field(default_factory=list)

    def to_openai_message(self) -> dict[str, Any]:
        """Convert to OpenAI message format."""
        type_labels = {
            "episodic_summaries": "过往会话摘要",
            "key_facts": "已知事实",
        }
        label = type_labels.get(self.block_type, self.block_type)

        return {
            "role": "system",
            "content": f"[{label}]\n{self.content}",
            "name": "memory",
        }
