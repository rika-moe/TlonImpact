"""Participant Directory for Tlon Server.

Aggregates and provides information about all participants in the system:
- Agents (from AgentRegistry)
- Humans (from connected sessions)
"""

from typing import List, Optional

from tlon.agents.agent_registry import AgentRegistry
from tlon.organization.messaging.participant import Participant
from tlon.base.logger import get_logger
from .session_manager import SessionManager

logger = get_logger(__name__)


class ParticipantDirectory:
    """Discovers and aggregates all chat participants.
    
    Responsibilities:
    - Aggregate agents from AgentRegistry
    - Aggregate humans from SessionManager
    - Provide participant lookup
    
    Note: This performs real-time aggregation on each call.
    If performance becomes an issue, consider adding caching.
    
    Example:
        >>> directory = ParticipantDirectory(agent_registry, session_manager)
        >>> all_participants = directory.list_all()
        >>> agents = directory.list_agents()
        >>> humans = directory.list_humans()
    """
    
    def __init__(
        self,
        agent_registry: AgentRegistry,
        session_manager: SessionManager,
    ):
        """Initialize the participant directory.
        
        Args:
            agent_registry: Registry for agent information
            session_manager: Manager for human session information
        """
        self._agent_registry = agent_registry
        self._session_manager = session_manager
        
        logger.info("ParticipantDirectory initialized")
    
    def list_all(self) -> List[Participant]:
        """Get all participants (agents + humans).
        
        This method performs real-time aggregation from both
        AgentRegistry and SessionManager.
        
        Returns:
            List of all participants
        """
        participants: List[Participant] = []
        
        # Add all agents
        participants.extend(self._list_agents())
        
        # Add humans if there are connected sessions
        if self._session_manager.get_session_count() > 0:
            participants.append(self._create_human_participant())
        
        return participants
    
    def list_agents(self) -> List[Participant]:
        """Get all agent participants.
        
        Returns:
            List of agent participants
        """
        return self._list_agents()
    
    def list_humans(self) -> List[Participant]:
        """Get all human participants.
        
        Returns:
            List of human participants (0 or 1, representing all connected humans)
        """
        if self._session_manager.get_session_count() > 0:
            return [self._create_human_participant()]
        return []
    
    def get_participant(self, participant_id: str) -> Optional[Participant]:
        """Get a specific participant by ID.
        
        Args:
            participant_id: The participant ID
            
        Returns:
            The participant if found, None otherwise
        """
        # Check if it's the human pseudo-participant
        if participant_id == "human":
            if self._session_manager.get_session_count() > 0:
                return self._create_human_participant()
            return None
        
        # Check agents
        config = self._agent_registry.get_config(participant_id)
        if config:
            return Participant(
                id=participant_id,
                name=config.name,
                type="agent",
                metadata={"position_id": config.position_id}
            )
        
        return None
    
    def get_participant_names(self) -> List[str]:
        """Get names of all participants.
        
        Returns:
            List of participant display names
        """
        return [p.name for p in self.list_all()]
    
    def _list_agents(self) -> List[Participant]:
        """Internal: List all agent participants.
        
        Returns:
            List of agent participants
        """
        participants: List[Participant] = []
        
        for agent_id in self._agent_registry.get_all():
            config = self._agent_registry.get_config(agent_id)
            if config:
                participants.append(Participant(
                    id=agent_id,
                    name=config.name,
                    type="agent",
                    metadata={"position_id": config.position_id}
                ))
        
        return participants
    
    def _create_human_participant(self) -> Participant:
        """Internal: Create the human participant representation.
        
        Returns:
            Human participant
        """
        session_count = self._session_manager.get_session_count()
        
        return Participant(
            id="human",
            name="人类",
            type="human",
            metadata={
                "session_count": session_count,
                "description": f"{session_count} connected session(s)"
            }
        )
    
    def count_agents(self) -> int:
        """Get the number of agents.
        
        Returns:
            Number of registered agents
        """
        return len(self._agent_registry.get_all())
    
    def count_humans(self) -> int:
        """Get the number of human sessions.
        
        Returns:
            Number of connected human sessions
        """
        return self._session_manager.get_session_count()
    
    def count_all(self) -> int:
        """Get the total number of participants.
        
        Note: Humans are counted as 1 if any sessions exist.
        
        Returns:
            Total participant count
        """
        count = self.count_agents()
        if self._session_manager.get_session_count() > 0:
            count += 1  # Human pseudo-participant
        return count
