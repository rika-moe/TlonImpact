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
        >>> handler = RESTAPIHandler(server)
        >>> app = handler.create_app()
        >>> runner = web.AppRunner(app)
        >>> await runner.setup()
        >>> site = web.TCPSite(runner, 'localhost', 8080)
        >>> await site.start()
    """
    
    def __init__(self, server, config_manager=None):
        """Initialize the REST API handler.
        
        Args:
            server: The TlonServer instance
            config_manager: Optional ConfigManager for hot-reload support
        """
        self.server = server
        self.config_manager = config_manager
    
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
        app.router.add_post('/agents/{agent_id}/wake', self.handle_wake_agent)
        app.router.add_post('/agents/{agent_id}/stop', self.handle_stop_agent)
        app.router.add_get('/sessions', self.handle_list_sessions)
        app.router.add_get('/participants', self.handle_list_participants)
        
        # Admin routes for hot-reload (only if config_manager is available)
        if self.config_manager:
            app.router.add_post('/admin/reload/positions', self.handle_reload_positions)
            app.router.add_post('/admin/reload/agents', self.handle_reload_agents)
            app.router.add_get('/admin/config/positions', self.handle_list_positions)
            app.router.add_get('/admin/config/agents', self.handle_list_agent_configs)
        
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
            "running": self.server.is_running(),
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
                "host": self.server.host,
                "port": self.server.port,
            },
            "chatroom": {
                "id": self.server.chatroom.metadata.id,
                "name": self.server.chatroom.metadata.name,
                "participant_count": len(self.server.chatroom.participants),
                "message_count": len(self.server.chatroom.history),
            },
            "agents": {
                "count": len(self.server.agent_registry.get_all_agent_status()),
            },
            "sessions": {
                "active": len(self.server.get_all_sessions()),
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
        
        messages = await self.server.get_history(limit, before_id)
        
        return self._json_response({
            "messages": [msg.to_dict() for msg in messages],
            "count": len(messages),
        })
    
    async def handle_list_agents(self, request: web.Request) -> web.Response:
        """GET /agents - List all agents.
        
        Returns:
            List of agent statuses
        """
        agents = self.server.get_all_agent_statuses()
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
        status = self.server.get_agent_status(agent_id)
        
        if status:
            return self._json_response(status)
        else:
            return self._json_response(
                {"error": f"Agent '{agent_id}' not found"},
                status=404
            )
    
    async def handle_wake_agent(self, request: web.Request) -> web.Response:
        """POST /agents/{agent_id}/wake - Wake up a lazy agent.
        
        Returns:
            Success status or 404 if not found
        """
        agent_id = request.match_info['agent_id']
        
        success = await self.server.wake_agent(agent_id)
        
        if success:
            return self._json_response({
                "success": True,
                "message": f"Agent '{agent_id}' woken successfully",
            })
        else:
            # Check if agent exists
            status = self.server.get_agent_status(agent_id)
            if status:
                return self._json_response({
                    "success": False,
                    "message": f"Agent '{agent_id}' is already active or cannot be woken",
                    "status": status,
                }, status=400)
            else:
                return self._json_response(
                    {"error": f"Agent '{agent_id}' not found"},
                    status=404
                )
    
    async def handle_stop_agent(self, request: web.Request) -> web.Response:
        """POST /agents/{agent_id}/stop - Stop an active agent.
        
        Returns:
            Success status or 404 if not found
        """
        agent_id = request.match_info['agent_id']
        
        success = await self.server.stop_agent(agent_id)
        
        if success:
            return self._json_response({
                "success": True,
                "message": f"Agent '{agent_id}' stopped successfully",
            })
        else:
            status = self.server.get_agent_status(agent_id)
            if status:
                return self._json_response({
                    "success": False,
                    "message": f"Agent '{agent_id}' is not active or cannot be stopped",
                    "status": status,
                }, status=400)
            else:
                return self._json_response(
                    {"error": f"Agent '{agent_id}' not found"},
                    status=404
                )
    
    async def handle_list_sessions(self, request: web.Request) -> web.Response:
        """GET /sessions - List active client sessions.
        
        Returns:
            List of session info
        """
        sessions = self.server.get_all_sessions()
        
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
        participants = self.server.chatroom.get_all_participants()
        
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
    
    # ========== Admin Routes for Hot-Reload ==========
    
    async def handle_reload_positions(self, request: web.Request) -> web.Response:
        """POST /admin/reload/positions - Hot-reload position configuration.
        
        Returns:
            Success or error response
        """
        if not self.config_manager:
            return self._json_response({
                "status": "error",
                "message": "Config manager not available"
            }, status=503)
        
        try:
            success = await self.config_manager.reload_positions()
            if success:
                return self._json_response({
                    "status": "success",
                    "message": "Positions reloaded successfully",
                    "count": len(self.config_manager.positions)
                })
            else:
                return self._json_response({
                    "status": "error",
                    "message": "Failed to reload positions"
                }, status=500)
        except Exception as e:
            logger.error(f"Error reloading positions: {e}")
            return self._json_response({
                "status": "error",
                "message": str(e)
            }, status=500)
    
    async def handle_reload_agents(self, request: web.Request) -> web.Response:
        """POST /admin/reload/agents - Hot-reload agent configuration.
        
        Returns:
            Success or error response
        """
        if not self.config_manager:
            return self._json_response({
                "status": "error",
                "message": "Config manager not available"
            }, status=503)
        
        try:
            success = await self.config_manager.reload_agents()
            if success:
                return self._json_response({
                    "status": "success",
                    "message": "Agents reloaded successfully",
                    "count": len(self.config_manager.agents)
                })
            else:
                return self._json_response({
                    "status": "error",
                    "message": "Failed to reload agents"
                }, status=500)
        except Exception as e:
            logger.error(f"Error reloading agents: {e}")
            return self._json_response({
                "status": "error",
                "message": str(e)
            }, status=500)
    
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
        server,
        config_manager=None,
        host: str = "127.0.0.1",
        port: int = 8080,
    ):
        """Initialize the REST server.
        
        Args:
            server: The TlonServer instance
            config_manager: Optional ConfigManager for hot-reload support
            host: Host to bind to
            port: Port to listen on
        """
        self.server = server
        self.host = host
        self.port = port
        self._handler = RESTAPIHandler(server, config_manager)
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
