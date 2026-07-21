"""Component Base Class for Tlon Server Architecture.

This module provides the foundation for a DAG-based component architecture:
- All dependencies must be injected via constructor
- No setter injection allowed
- Inter-component communication via EventBus only

DAG Architecture Rules:
1. Component as attribute → constructor injection only
2. Component communication → EventBus (indirect)
3. No circular dependencies allowed
"""

from abc import ABC, abstractmethod

from tlon.base.logger import get_logger

logger = get_logger(__name__)


class Component(ABC):
    """Base class for all ServerApp components.

        Design Principles:
        ------------------
        1. Constructor Injection Only
           All dependencies must be declared in __init__ parameters.
           No setter methods, no property injection, no runtime replacement.

        2. EventBus for Communication
           Components communicate indirectly via EventBus publish/subscribe.
           Direct method calls between components should be minimal and
    transparent (e.g., Facade pattern delegation).

        3. DAG Structure
           The component dependency graph must be a Directed Acyclic Graph.
           ServerApp acts as the sole assembler, creating components in
           topological order.

        Lifecycle:
        ----------
        1. Construction (__init__):
           - Store injected dependencies
           - Perform lightweight initialization only
           - Do NOT subscribe to EventBus here

        2. Initialization (initialize()):
           - Subscribe to EventBus events
           - Start background tasks
           - Connect to external resources

        3. Operation:
           - Handle events via callbacks
           - Process requests

        4. Shutdown (shutdown()):
           - Unsubscribe from EventBus
           - Stop background tasks
           - Release resources

        Example:
        --------
            class MessageRouter(Component):
                def __init__(
                    self,
                    event_bus: EventBus,  # ✅ Constructor injection
                    message_store: MessageStore,
                ):
                    super().__init__()
                    self._event_bus = event_bus  # Store as attribute
                    self._message_store = message_store
                    self._subscription_id: Optional[str] = None

                async def initialize(self) -> None:
                    # ✅ Subscribe in initialize(), not __init__
                    self._subscription_id = self._event_bus.subscribe(
                        EventType.MESSAGE_NEW, self._on_message
                    )

                async def shutdown(self) -> None:
                    # ✅ Unsubscribe in shutdown()
                    if self._subscription_id:
                        self._event_bus.unsubscribe(self._subscription_id)

        Anti-patterns (DO NOT USE):
        ---------------------------
            # ❌ Setter injection
            def set_event_bus(self, event_bus: EventBus):
                self._event_bus = event_bus

            # ❌ Property injection
            @property
            def event_bus(self) -> EventBus:
                return self._event_bus

            @event_bus.setter
            def event_bus(self, value: EventBus):
                self._event_bus = value

            # ❌ Runtime replacement
            def replace_event_bus(self, new_bus: EventBus):
                self._event_bus = new_bus

            # ❌ Implicit dependency (global state)
            def do_something(self):
                from some_module import global_bus  # Hidden dependency!
                global_bus.publish(...)
    """

    def __init__(self, name: str | None = None):
        """Initialize the component base.

        Args:
            name: Optional component name for logging.
                  Defaults to class name.
        """
        self._name = name or self.__class__.__name__
        self._initialized = False
        self._started = False
        logger.debug("component_constructed", name=self._name)

    @property
    def name(self) -> str:
        """Get component name."""
        return self._name

    @property
    def is_initialized(self) -> bool:
        """Check if component has been initialized."""
        return self._initialized

    @property
    def is_started(self) -> bool:
        """Check if component has been started."""
        return self._started

    @abstractmethod
    async def initialize(self) -> None:
        """Initialize the component.

        Called after all dependencies are injected via constructor.
        Use this method to:
        - Subscribe to EventBus events
        - Initialize internal state that depends on external resources
        - Start background tasks

        Do NOT:
        - Create new component dependencies
        - Modify constructor-injected dependencies
        - Perform heavy blocking operations
        """
        pass

    @abstractmethod
    async def shutdown(self) -> None:
        """Gracefully shutdown the component.

        Should:
        - Unsubscribe from EventBus
        - Stop background tasks
        - Release resources
        - Handle cleanup even if initialization failed
        """
        pass

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} name={self._name}>"


class ComponentRegistry:
    """Registry for tracking component lifecycle.

    Used by ServerApp to manage all components in a consistent way,
    ensuring proper initialization order and graceful shutdown.

    This is NOT a Service Locator - components are still created
    and injected by ServerApp, not looked up from the registry.
    """

    def __init__(self):
        """Initialize empty registry."""
        self._components: list[Component] = []

    def register(self, component: Component) -> None:
        """Register a component for lifecycle management.

        Args:
            component: Component to register
        """
        self._components.append(component)
        logger.debug("component_registered", name=component.name)

    async def initialize_all(self) -> None:
        """Initialize all registered components.

        Components are initialized in registration order,
        which should follow topological order of the DAG.
        """
        for component in self._components:
            logger.debug("component_initializing", name=component.name)
            await component.initialize()
            component._initialized = True
            logger.info("component_initialized", name=component.name)

    async def shutdown_all(self) -> None:
        """Shutdown all registered components.

                Components are shut down in reverse registration order
        to respect dependency direction.
        """
        for component in reversed(self._components):
            try:
                logger.debug("component_shutting_down", name=component.name)
                await component.shutdown()
                component._started = False
                component._initialized = False
                logger.info("component_shutdown", name=component.name)
            except Exception as e:
                logger.error(
                    "component_shutdown_failed",
                    name=component.name,
                    error=str(e),
                    exc_info=True,
                )

    def get_all(self) -> list[Component]:
        """Get all registered components."""
        return self._components.copy()
