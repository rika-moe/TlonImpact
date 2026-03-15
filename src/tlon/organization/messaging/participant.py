from dataclasses import dataclass, field
from typing import Dict, Any


@dataclass
class Participant:
    """A chat room participant.

    This is a simple data class for representing participants
    in the UI. Participants are now aggregated dynamically by
    ClientGateway from AgentRegistry and active sessions.

    Attributes:
        id: Unique participant ID
        name: Display name
        type: Participant type ("human" | "agent")
        metadata: Additional metadata
    """
    id: str
    name: str
    type: str  # "human" | "agent"
    metadata: Dict[str, Any] = field(default_factory=dict)
