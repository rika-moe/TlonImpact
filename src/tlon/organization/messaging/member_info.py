from dataclasses import dataclass, field
from typing import Any


@dataclass
class MemberInfo:
    """Read-model DTO of a Congress member for the UI / wire protocol.

    This is NOT the runtime interface — that is ``Member``
    (see member.py and docs/design/members.md). MemberInfo is a simple
    data class for representing members in the UI, aggregated dynamically
    by ClientGateway from AgentRegistry and active sessions.

    Attributes:
        id: Unique member ID
        name: Display name
        type: Member type on the wire ("human" | "agent")
        metadata: Additional metadata
    """

    id: str
    name: str
    type: str  # "human" | "agent"
    metadata: dict[str, Any] = field(default_factory=dict)
