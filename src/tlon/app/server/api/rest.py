"""REST API for Tlon Server.

Provides HTTP endpoints for:
- Configuration queries
- Message history retrieval
- Agent management
- Session information
- WebUI static files
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from aiohttp import web

from tlon.base.logger import get_logger
from tlon.base.settings import get_tlon_home
from tlon.base.tracing import FileTraceStore

logger = get_logger(__name__)


def get_webui_path() -> Optional[Path]:
    """Get the path to WebUI static files.
    
    Returns:
        Path to webui directory if it exists, None otherwise
    """
    # Try to find webui directory relative to project root
    # Uses TLON_HOME environment variable if set
    webui_path = get_tlon_home() / "src" / "tlon" / "webui"
    if webui_path.exists() and webui_path.is_dir():
        # Check if it has index.html
        if (webui_path / "index.html").exists():
            return webui_path
    return None


class RESTAPIHandler:
    """Handler for REST API requests.
    
    Routes:
    - GET /health - Health check
    - GET /config - Server configuration
    - GET /history - Message history
    - GET /agents - List all agents
    - POST /agents/{id}/wake - Wake an agent
    - POST /agents/{id}/stop - Stop an agent
    - GET /sessions - List active sessions
    - GET /events/history - Event history (from persistence)
    
    Example:
        >>> handler = RESTAPIHandler(server, agent_registry)
        >>> app = handler.create_app()
        >>> runner = web.AppRunner(app)
        >>> await runner.setup()
        >>> site = web.TCPSite(runner, 'localhost', 8080)
        >>> await site.start()
    """
    
    def __init__(self, client_gateway, agent_registry, config_manager=None, trace_store=None, debug: bool = False):
        """Initialize the REST API handler.

        Args:
            client_gateway: The ClientGateway instance
            agent_registry: The AgentRegistry instance for agent operations
            config_manager: Optional ConfigManager for hot-reload support
            trace_store: Optional TraceStore for trace operations
            debug: Whether to enable debug/test endpoints (never enable in production)
        """
        self.client_gateway = client_gateway
        self.agent_registry = agent_registry
        self.config_manager = config_manager
        self.debug = debug
        self.trace_store = trace_store
    
    def create_app(self) -> web.Application:
        """Create and configure the aiohttp application.
        
        Returns:
            Configured aiohttp Application
        """
        app = web.Application()
        
        # Add API routes first
        app.router.add_get('/health', self.handle_health)
        app.router.add_get('/config', self.handle_config)
        app.router.add_get('/history', self.handle_history)
        app.router.add_get('/agents', self.handle_list_agents)
        app.router.add_get('/agents/{agent_id}', self.handle_get_agent)
        # Wake/stop endpoints removed - all agents start at system startup
        app.router.add_get('/sessions', self.handle_list_sessions)
        app.router.add_get('/participants', self.handle_list_participants)
        
        # Admin routes for hot-reload (only if config_manager is available)
        if self.config_manager:
            app.router.add_get('/admin/config/positions', self.handle_list_positions)
            app.router.add_get('/admin/config/agents', self.handle_list_agent_configs)

        # Trace routes (only if trace_store is available)
        if self.trace_store:
            app.router.add_get('/traces', self.handle_list_traces)
            app.router.add_get('/traces/{trace_id}', self.handle_get_trace)

        # Agent logs route
        app.router.add_get('/agents/{agent_id}/logs', self.handle_get_agent_logs)

        # Test endpoints (only in debug mode — never enable in production)
        if self.debug:
            app.router.add_get('/test/ping', self.handle_test_ping)
            app.router.add_post('/test/echo', self.handle_test_echo)

        # Add WebUI static files
        webui_path = get_webui_path()
        if webui_path:
            logger.info(f"Serving WebUI from {webui_path}")
            # Serve static files
            app.router.add_static('/ui/', path=webui_path, name='webui-static')
            # SPA fallback: serve index.html for all /ui/* routes
            app.router.add_get('/ui/{path:.*}', self.handle_webui_spa)
        else:
            logger.warning("WebUI not found, serving without static files")
            app.router.add_get('/ui', self.handle_webui_not_found)
        
        # CORS
        app.router.add_options('/{path:.*}', self.handle_options)
        
        return app
    
    async def handle_webui_spa(self, request: web.Request) -> web.Response:
        """Serve index.html for SPA routes.
        
        This allows React Router to handle client-side routing.
        """
        webui_path = get_webui_path()
        if webui_path:
            index_path = webui_path / "index.html"
            if index_path.exists():
                return web.FileResponse(index_path)
        return web.Response(
            text="WebUI not found",
            status=404,
            content_type='text/plain'
        )
    
    async def handle_webui_not_found(self, request: web.Request) -> web.Response:
        """Return helpful message when WebUI is not built."""
        return web.Response(
            text="""WebUI not available.

To use the WebUI, you need to build it first:
    cd web && npm install && npm run build

Or use the build script:
    ./scripts/build.sh
""",
            status=404,
            content_type='text/plain'
        )
    
    def _json_response(self, data: Any, status: int = 200) -> web.Response:
        """Create a JSON response.
        
        Args:
            data: Data to serialize
            status: HTTP status code
            
        Returns:
            JSON response
        """
        return web.Response(
            text=json.dumps(data, ensure_ascii=False, default=str),
            status=status,
            content_type='application/json',
            headers={
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
                'Access-Control-Allow-Headers': 'Content-Type',
            }
        )
    
    async def handle_options(self, request: web.Request) -> web.Response:
        """Handle CORS preflight requests."""
        return web.Response(
            status=200,
            headers={
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
                'Access-Control-Allow-Headers': 'Content-Type',
            }
        )
    
    async def handle_health(self, request: web.Request) -> web.Response:
        """GET /health - Health check endpoint.
        
        Returns:
            200 OK with server status
        """
        return self._json_response({
            "status": "healthy",
            "running": self.client_gateway.is_running(),
            "timestamp": datetime.now().isoformat(),
            "version": "0.2.0",
        })
    
    async def handle_config(self, request: web.Request) -> web.Response:
        """GET /config - Get server configuration.
        
        Returns:
            Server configuration (sanitized)
        """
        # Return non-sensitive configuration
        config = {
            "server": {
                "host": self.client_gateway.host,
                "port": self.client_gateway.port,
            },
            "agents": {
                "count": len(self.agent_registry.get_all_agent_status()),
            },
            "sessions": {
                "active": len(self.client_gateway.get_all_sessions()),
            }
        }
        return self._json_response(config)
    
    async def handle_history(self, request: web.Request) -> web.Response:
        """GET /history - Get message history.
        
        Query Parameters:
            limit: Maximum number of messages (default: 100, max: 1000)
            before: Message ID to get messages before
            
        Returns:
            List of messages
        """
        try:
            limit = int(request.query.get('limit', 100))
            limit = min(limit, 1000)  # Cap at 1000
        except ValueError:
            limit = 100
        
        before_id = request.query.get('before')
        
        messages = await self.client_gateway.get_history(limit, before_id)
        
        return self._json_response({
            "messages": [msg.to_dict() for msg in messages],
            "count": len(messages),
        })
    
    async def handle_list_agents(self, request: web.Request) -> web.Response:
        """GET /agents - List all agents.
        
        Returns:
            List of agent statuses
        """
        agents = self.agent_registry.get_all_agent_status()
        return self._json_response({
            "agents": agents,
            "count": len(agents),
        })
    
    async def handle_get_agent(self, request: web.Request) -> web.Response:
        """GET /agents/{agent_id} - Get a specific agent's status.
        
        Returns:
            Agent status or 404 if not found
        """
        agent_id = request.match_info['agent_id']
        
        config = self.agent_registry.get_config(agent_id)
        if config is None:
            return self._json_response(
                {"error": f"Agent '{agent_id}' not found"},
                status=404
            )
        
        agent = self.agent_registry.get_agent(agent_id)
        state = agent.get_state() if agent else "unknown"
        
        status = {
            "id": agent_id,
            "name": config.name,
            "state": state,
            "config": {
                "role": config.role.name if hasattr(config.role, 'name') else str(config.role),
                "skills": config.skills,
            }
        }
        return self._json_response(status)
    
    async def handle_list_sessions(self, request: web.Request):
        """GET /sessions - List active client sessions.
        
        Returns:
            List of session info
        """
        sessions = self.client_gateway.get_all_sessions()
        
        session_info = []
        for session in sessions:
            info = {
                "id": session.id,
                "client_type": session.client_type,
                "connected_at": session.connected_at,
                "mode": session.mode,
                "filters": session.filters,
            }
            session_info.append(info)
        
        return self._json_response({
            "sessions": session_info,
            "count": len(session_info),
        })
    
    async def handle_list_participants(self, request: web.Request) -> web.Response:
        """GET /participants - List chat room participants.
        
        Returns:
            List of participants
        """
        participants = self.client_gateway.get_all_participants()
        
        participant_info = []
        for p in participants:
            info = {
                "id": p.id,
                "name": p.name,
                "type": p.type,
                "metadata": p.metadata,
            }
            participant_info.append(info)
        
        return self._json_response({
            "participants": participant_info,
            "count": len(participant_info),
        })

    async def handle_list_positions(self, request: web.Request) -> web.Response:
        """GET /admin/config/positions - List current position configurations.
        
        Returns:
            List of position configurations
        """
        if not self.config_manager:
            return self._json_response({
                "status": "error",
                "message": "Config manager not available"
            }, status=503)
        
        positions = []
        for pos in self.config_manager.positions.values():
            positions.append({
                "id": pos.id,
                "type": pos.type,
                "title": pos.title,
                "department": pos.department,
                "description": pos.description,
            })
        
        return self._json_response({
            "status": "success",
            "positions": positions,
            "count": len(positions)
        })
    
    async def handle_list_agent_configs(self, request: web.Request) -> web.Response:
        """GET /admin/config/agents - List current agent configurations.
        
        Returns:
            List of agent configurations (sanitized)
        """
        if not self.config_manager:
            return self._json_response({
                "status": "error",
                "message": "Config manager not available"
            }, status=503)
        
        agents = []
        for agent in self.config_manager.agents.values():
            agents.append({
                "id": agent.id,
                "name": agent.name,
                "position_id": agent.position_id,
                "personality": agent.personality,
                "skills": agent.skills,
            })
        
        return self._json_response({
            "status": "success",
            "agents": agents,
            "count": len(agents)
        })

    async def handle_list_traces(self, request: web.Request) -> web.Response:
        """GET /traces - List traces with optional filtering.

        Query Parameters:
            limit: Maximum number of traces (default: 100, max: 1000)
            source: Filter by source component

        Returns:
            List of trace records
        """
        if not self.trace_store:
            return self._json_response({
                "status": "error",
                "message": "Trace store not available"
            }, status=503)

        try:
            limit = int(request.query.get('limit', 100))
            limit = min(limit, 1000)
        except ValueError:
            limit = 100

        source = request.query.get('source')

        traces = await self.trace_store.list_traces(limit=limit, source=source)

        return self._json_response({
            "traces": [trace.to_dict() for trace in traces],
            "count": len(traces),
        })

    async def handle_get_trace(self, request: web.Request) -> web.Response:
        """GET /traces/{trace_id} - Get a specific trace.

        Returns:
            Complete trace record with all spans
        """
        if not self.trace_store:
            return self._json_response({
                "status": "error",
                "message": "Trace store not available"
            }, status=503)

        trace_id = request.match_info['trace_id']
        trace = await self.trace_store.get_trace(trace_id)

        if not trace:
            return self._json_response(
                {"error": f"Trace '{trace_id}' not found"},
                status=404
            )

        return self._json_response({
            "trace": trace.to_dict(),
            "span_tree": trace.get_span_tree(),
        })

    async def handle_get_agent_logs(self, request: web.Request) -> web.Response:
        """GET /agents/{agent_id}/logs - Get agent logs.

        Query Parameters:
            lines: Number of lines to return (default: 100)
            level: Minimum log level (debug, info, warning, error)

        Returns:
            Agent log entries
        """
        agent_id = request.match_info['agent_id']

        # Check if agent exists
        config = self.agent_registry.get_config(agent_id)
        if config is None:
            return self._json_response(
                {"error": f"Agent '{agent_id}' not found"},
                status=404
            )

        try:
            lines = int(request.query.get('lines', 100))
            lines = min(lines, 10000)  # Cap at 10k lines
        except ValueError:
            lines = 100

        level = request.query.get('level', 'info').lower()

        # TODO: Implement actual log retrieval from log files
        # For now, return placeholder
        return self._json_response({
            "agent_id": agent_id,
            "agent_name": config.name,
            "logs": [],
            "message": "Agent log retrieval not yet implemented",
            "lines_requested": lines,
            "level_filter": level,
        })

    async def handle_test_ping(self, request: web.Request) -> web.Response:
        """GET /test/ping - Simple ping endpoint for testing."""
        return self._json_response({
            "status": "ok",
            "pong": True,
            "timestamp": datetime.now().isoformat(),
        })

    async def handle_test_echo(self, request: web.Request) -> web.Response:
        """POST /test/echo - Echo back the request body."""
        try:
            body = await request.json()
            return self._json_response({
                "status": "ok",
                "echo": body,
                "timestamp": datetime.now().isoformat(),
            })
        except json.JSONDecodeError:
            return self._json_response(
                {"error": "Invalid JSON"},
                status=400
            )


class RESTServer:
    """REST API server for Tlon.

    Wraps the aiohttp server with lifecycle management.

    Example:
        >>> rest = RESTServer(server, config_manager, port=8080)
        >>> await rest.start()
        >>> # Server is running
        >>> await rest.stop()
    """

    def __init__(
        self,
        client_gateway,
        agent_registry,
        config_manager=None,
        trace_store=None,
        host: str = "127.0.0.1",
        port: int = 8080,
        debug: bool = False,
    ):
        """Initialize the REST server.

        Args:
            client_gateway: The ClientGateway instance
            agent_registry: The AgentRegistry instance for agent operations
            config_manager: Optional ConfigManager for hot-reload support
            trace_store: Optional TraceStore for trace operations
            host: Host to bind to
            port: Port to listen on
            debug: Whether to enable debug/test endpoints (never enable in production)
        """
        self.client_gateway = client_gateway
        self.host = host
        self.port = port
        self._handler = RESTAPIHandler(client_gateway, agent_registry, config_manager, trace_store, debug)
        self._app: Optional[web.Application] = None
        self._runner: Optional[web.AppRunner] = None
        self._site: Optional[web.TCPSite] = None
        self._running = False
    
    async def start(self) -> None:
        """Start the REST server."""
        if self._running:
            logger.warning("REST server already running")
            return
        
        self._app = self._handler.create_app()
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        
        self._site = web.TCPSite(self._runner, self.host, self.port)
        await self._site.start()
        
        self._running = True
        logger.info(f"REST API server started on http://{self.host}:{self.port}")
    
    async def stop(self) -> None:
        """Stop the REST server."""
        if not self._running:
            return
        
        logger.info("Stopping REST API server...")
        
        if self._runner:
            await self._runner.cleanup()
        
        self._running = False
        logger.info("REST API server stopped")
    
    def is_running(self) -> bool:
        """Check if the REST server is running.
        
        Returns:
            True if running
        """
        return self._running
