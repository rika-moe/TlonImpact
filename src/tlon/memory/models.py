"""Data models for Memory system."""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Literal, Optional


class FactType(str, Enum):
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
        working_max_items: Maximum interactions in working memory
        working_summary_window: Interactions before generating summary
        episodic_max_summaries: Maximum summaries to keep
        episodic_max_facts: Maximum facts to keep
        context_max_summaries: Summaries to include in context
        context_max_facts: Facts to include in context
        context_max_working: Working interactions in context
        context_max_chat_history: ChatRoom messages in context
        idle_timeout_minutes: Idle time before auto-summary
    """
    
    version: str = "1.0"
    agent_id: str = ""
    
    # Working Memory settings
    working_max_items: int = 10
    working_summary_window: int = 10
    
    # Episodic Memory settings
    episodic_max_summaries: int = 20
    episodic_max_facts: int = 50
    
    # Context building settings
    context_max_summaries: int = 5
    context_max_facts: int = 10
    context_max_working: int = 5
    context_max_chat_history: int = 20
    
    # Trigger settings
    idle_timeout_minutes: int = 30
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert config to dictionary."""
        return {
            "version": self.version,
            "agent_id": self.agent_id,
            "working": {
                "max_items": self.working_max_items,
                "summary_window": self.working_summary_window,
            },
            "episodic": {
                "max_summaries": self.episodic_max_summaries,
                "max_facts": self.episodic_max_facts,
            },
            "context": {
                "max_summaries_in_context": self.context_max_summaries,
                "max_facts_in_context": self.context_max_facts,
                "max_working_in_context": self.context_max_working,
                "max_chat_history": self.context_max_chat_history,
            },
            "triggers": {
                "idle_timeout_minutes": self.idle_timeout_minutes,
            },
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MemoryConfig":
        """Create config from dictionary."""
        config = cls()
        
        if "version" in data:
            config.version = data["version"]
        if "agent_id" in data:
            config.agent_id = data["agent_id"]
        
        if "working" in data:
            config.working_max_items = data["working"].get("max_items", 10)
            config.working_summary_window = data["working"].get("summary_window", 10)
        
        if "episodic" in data:
            config.episodic_max_summaries = data["episodic"].get("max_summaries", 20)
            config.episodic_max_facts = data["episodic"].get("max_facts", 50)
        
        if "context" in data:
            ctx = data["context"]
            config.context_max_summaries = ctx.get("max_summaries_in_context", 5)
            config.context_max_facts = ctx.get("max_facts_in_context", 10)
            config.context_max_working = ctx.get("max_working_in_context", 5)
            config.context_max_chat_history = ctx.get("max_chat_history", 20)
        
        if "triggers" in data:
            config.idle_timeout_minutes = data["triggers"].get("idle_timeout_minutes", 30)
        
        return config


@dataclass
class Interaction:
    """A single interaction between user and agent.
    
    This represents one complete turn: user message -> agent response (possibly with tool calls).
    """
    
    timestamp: str
    session_id: str
    message: Dict[str, Any]
    response: Optional[Dict[str, Any]] = None
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    tool_results: List[Dict[str, Any]] = field(default_factory=list)
    importance_score: float = 0.5
    tags: List[str] = field(default_factory=list)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "session_id": self.session_id,
            "message": self.message,
            "response": self.response,
            "tool_calls": self.tool_calls,
            "tool_results": self.tool_results,
            "importance_score": self.importance_score,
            "tags": self.tags,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Interaction":
        """Create from dictionary."""
        return cls(
            id=data.get("id", str(uuid.uuid4())),
            timestamp=data["timestamp"],
            session_id=data["session_id"],
            message=data["message"],
            response=data.get("response"),
            tool_calls=data.get("tool_calls", []),
            tool_results=data.get("tool_results", []),
            importance_score=data.get("importance_score", 0.5),
            tags=data.get("tags", []),
        )


@dataclass
class Summary:
    """A session summary generated from multiple interactions."""
    
    timestamp: str
    session_id: str
    summary_text: str
    source_interactions: List[str] = field(default_factory=list)
    interaction_count: int = 0
    key_decisions: List[str] = field(default_factory=list)
    action_items: List[str] = field(default_factory=list)
    participants: List[str] = field(default_factory=list)
    keywords: List[str] = field(default_factory=list)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "session_id": self.session_id,
            "summary_text": self.summary_text,
            "source_interactions": self.source_interactions,
            "interaction_count": self.interaction_count,
            "key_decisions": self.key_decisions,
            "action_items": self.action_items,
            "participants": self.participants,
            "keywords": self.keywords,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Summary":
        """Create from dictionary."""
        return cls(
            id=data.get("id", str(uuid.uuid4())),
            timestamp=data["timestamp"],
            session_id=data["session_id"],
            summary_text=data["summary_text"],
            source_interactions=data.get("source_interactions", []),
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
    source_interaction: Optional[str] = None
    version: int = 1
    superseded_by: Optional[str] = None
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    
    def to_dict(self) -> Dict[str, Any]:
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
    def from_dict(cls, data: Dict[str, Any]) -> "Fact":
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
    
    block_type: Literal["episodic_summaries", "key_facts", "recent_working"]
    priority: int
    content: str
    source_ids: List[str] = field(default_factory=list)
    
    def to_openai_message(self) -> Dict[str, Any]:
        """Convert to OpenAI message format."""
        type_labels = {
            "episodic_summaries": "过往会话摘要",
            "key_facts": "已知事实",
            "recent_working": "最近工作记录",
        }
        label = type_labels.get(self.block_type, self.block_type)
        
        return {
            "role": "system",
            "content": f"[{label}]\n{self.content}",
            "name": "memory",
        }
