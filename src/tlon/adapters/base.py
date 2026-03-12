"""Base adapter interface for Tlon."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional


@dataclass
class UserInput:
    """User input in standard format.
    
    Attributes:
        content: Input text content
        user_id: User identifier
        timestamp: When the input was received
        metadata: Additional metadata
    """
    content: str
    user_id: str = "human"
    timestamp: datetime = None
    metadata: Optional[dict] = None
    
    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now()


class AdapterInterface(ABC):
    """Base interface for all adapters.
    
    Adapters connect the core system to external interfaces
    like CLI, Telegram, Web, etc.
    
    Example:
        >>> class MyAdapter(AdapterInterface):
        ...     async def start(self):
        ...         # Start listening for input
        ...     
        ...     async def stop(self):
        ...         # Stop listening
        ...     
        ...     async def send_message(self, message):
        ...         # Display message to user
    """
    
    def __init__(self):
        """Initialize the adapter."""
        self._message_handler: Optional[Callable[[UserInput], None]] = None
    
    @abstractmethod
    async def start(self) -> None:
        """Start the adapter.
        
        Begin listening for user input.
        """
        pass
    
    @abstractmethod
    async def stop(self) -> None:
        """Stop the adapter.
        
        Stop listening and cleanup resources.
        """
        pass
    
    @abstractmethod
    async def send_message(self, message: "Message") -> None:
        """Send a message to the user.
        
        Args:
            message: The message to display
        """
        pass
    
    def on_message(self, handler: Callable[[UserInput], None]) -> None:
        """Register a message handler.
        
        The handler will be called when user input is received.
        
        Args:
            handler: Callback function for user input
        """
        self._message_handler = handler
    
    async def _handle_input(self, user_input: UserInput) -> None:
        """Handle user input.
        
        Internal method to route input to the registered handler.
        
        Args:
            user_input: The user input to handle
        """
        if self._message_handler:
            try:
                result = self._message_handler(user_input)
                if hasattr(result, '__await__'):
                    await result
            except Exception as e:
                from ..logger import get_logger
                logger = get_logger(__name__)
                logger.error(f"Error handling input: {e}", exc_info=True)
