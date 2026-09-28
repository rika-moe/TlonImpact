"""Data models for Memory system (claim model, ADR 0017)."""

import uuid
from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class MemoryConfig:
    """Configuration for Memory system.

    Attributes:
        version: Config file version
        agent_id: Associated agent ID
        distillation_window: Completed undistilled sessions before a batch
            distillation runs (ADR 0017)
        context_max_claims: Claims to include in context
        reflection_min_interactions: Completed sessions before reflection
            may trigger
        retrieval_alpha_similarity / retrieval_beta_recency /
            retrieval_gamma_importance: JIT retrieval composite score
            weights (ADR 0017 stage 2)
        retrieval_recency_half_life_days: Recency decay half-life
        retrieval_pool_factor: KNN candidate pool size multiplier
        conflict_similarity_threshold: Similarity gate for conflict
            judgment (ADR 0017 stage 3)
        conflict_candidate_limit: Judgment candidates pulled per new claim
    """

    version: str = "1.0"
    agent_id: str = ""

    # Distillation trigger (ADR 0017: journal -> claims, batch by window)
    distillation_window: int = 3

    # Context building settings
    context_max_claims: int = 10

    # Reflection settings (agent self-driven memory audit). The on/off
    # switch and interval live in the behavior spec
    # (:on-complete/:reflect); only the interaction threshold stays here.
    reflection_min_interactions: int = 5

    # JIT retrieval composite score (ADR 0017 stage 2, Generative Agents
    # shape): ``score = α·similarity + β·recency + γ·importance``.
    # Similarity dominates — relevance to the current mail is the whole
    # point of JIT injection (); importance lets critical claims win
    # near-ties; recency is deliberately the weakest factor because the
    # recency-ordered fallback already owns "latest first" and an old but
    # relevant claim must not drown under fresh noise.
    retrieval_alpha_similarity: float = 1.0
    retrieval_beta_recency: float = 0.3
    retrieval_gamma_importance: float = 0.5
    # Recency decays exponentially with this half-life in days (the
    # Generative Agents decay, re-parameterized as a half-life).
    retrieval_recency_half_life_days: float = 7.0
    # The KNN query pulls a pool larger than k so the composite score can
    # re-rank candidates pure similarity order would miss.
    retrieval_pool_factor: int = 4

    # Conflict judgment (ADR 0017 stage 3): a new claim is only worth an
    # LLM judgment call when a similar active old claim exists. The
    # similarity gate is deliberately high — judgment exists to catch
    # near-synonymous facts that contradict each other (), and at
    # 0.75 cosine similarity two claims are almost always about the same
    # fact; below it they are merely related topics and ADD directly.
    conflict_similarity_threshold: float = 0.75
    # Candidates pulled per new claim: judgment needs the few closest
    # matches, not a ranking exercise.
    conflict_candidate_limit: int = 3

    def to_dict(self) -> dict[str, Any]:
        """Convert config to dictionary."""
        return {
            "version": self.version,
            "agent_id": self.agent_id,
            "distillation": {
                "window": self.distillation_window,
            },
            "context": {
                "max_claims_in_context": self.context_max_claims,
            },
            "reflection": {
                "min_interactions": self.reflection_min_interactions,
            },
            "retrieval": {
                "alpha_similarity": self.retrieval_alpha_similarity,
                "beta_recency": self.retrieval_beta_recency,
                "gamma_importance": self.retrieval_gamma_importance,
                "recency_half_life_days": self.retrieval_recency_half_life_days,
                "pool_factor": self.retrieval_pool_factor,
            },
            "conflict": {
                "similarity_threshold": self.conflict_similarity_threshold,
                "candidate_limit": self.conflict_candidate_limit,
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MemoryConfig":
        """Create config from dictionary.

        Sections from retired designs (``summary``/``episodic``/old
        ``context`` keys from the ADR 0009 summary model, ``working``/
        ``triggers`` from the working-memory layer) are simply not read —
        an old ``config.json`` on disk loads as the current defaults.
        """
        config = cls()

        if "version" in data:
            config.version = data["version"]
        if "agent_id" in data:
            config.agent_id = data["agent_id"]

        if "distillation" in data:
            config.distillation_window = data["distillation"].get("window", 3)

        if "context" in data:
            config.context_max_claims = data["context"].get("max_claims_in_context", 10)

        if "reflection" in data:
            config.reflection_min_interactions = data["reflection"].get("min_interactions", 5)

        if "retrieval" in data:
            retrieval = data["retrieval"]
            config.retrieval_alpha_similarity = retrieval.get("alpha_similarity", 1.0)
            config.retrieval_beta_recency = retrieval.get("beta_recency", 0.3)
            config.retrieval_gamma_importance = retrieval.get("gamma_importance", 0.5)
            config.retrieval_recency_half_life_days = retrieval.get(
                "recency_half_life_days", 7.0
            )
            config.retrieval_pool_factor = retrieval.get("pool_factor", 4)

        if "conflict" in data:
            conflict = data["conflict"]
            config.conflict_similarity_threshold = conflict.get("similarity_threshold", 0.75)
            config.conflict_candidate_limit = conflict.get("candidate_limit", 3)

        return config


@dataclass
class SessionProjection:
    """One completed session's distillation view.

    Rebuilt from the session's journal (the sole interaction truth):
    the trigger content plus, per round, the LLM response content and a
    one-line digest of each tool result.
    """

    session_id: str
    trigger_content: str
    llm_responses: list[str] = field(default_factory=list)
    tool_results: list[str] = field(default_factory=list)


@dataclass
class Claim:
    """One remembered assertion (ADR 0017).

    A claim is a natural-language statement distilled from completed
    sessions or recorded directly by the agent, with provenance
    (``source_session_ids``) and a validity window. Claims are
    append-only: supersession marks (``status``/``valid_to``/
    ``superseded_by``), never deletes — a superseded claim stays in
    ``claims.jsonl`` for audit.
    """

    text: str
    timestamp: str
    valid_from: str
    source_session_ids: list[str] = field(default_factory=list)
    valid_to: str | None = None
    status: Literal["active", "superseded"] = "active"
    superseded_by: str | None = None
    importance: float = 0.5
    id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "text": self.text,
            "source_session_ids": self.source_session_ids,
            "valid_from": self.valid_from,
            "valid_to": self.valid_to,
            "status": self.status,
            "superseded_by": self.superseded_by,
            "importance": self.importance,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Claim":
        """Create from dictionary.

        Missing fields fall back to sane defaults so hand-written or
        older-format lines stay loadable.
        """
        timestamp = data.get("timestamp", "")
        status = data.get("status", "active")
        return cls(
            id=data.get("id", uuid.uuid4().hex),
            timestamp=timestamp,
            text=data.get("text", ""),
            source_session_ids=data.get("source_session_ids", []),
            valid_from=data.get("valid_from", timestamp),
            valid_to=data.get("valid_to"),
            status=status if status in ("active", "superseded") else "active",
            superseded_by=data.get("superseded_by"),
            importance=float(data.get("importance", 0.5)),
        )


@dataclass
class ContextBlock:
    """A block of context to be injected into LLM context.

    This represents a formatted piece of memory that can be converted
    to an OpenAI-style message for inclusion in the conversation.
    """

    block_type: Literal["claims"]
    priority: int
    content: str
    source_ids: list[str] = field(default_factory=list)

    def to_openai_message(self) -> dict[str, Any]:
        """Convert to OpenAI message format."""
        type_labels = {
            "claims": "记忆",
        }
        label = type_labels.get(self.block_type, self.block_type)

        return {
            "role": "system",
            "content": f"[{label}]\n{self.content}",
            "name": "memory",
        }
