"""End-to-end integration tests for Tlon Client-Server protocol.

Tests the complete flow:
1. Server startup
2. Client connection with protocol version negotiation
3. Authentication
4. Message sending/receiving
5. Trace recording
6. REST API queries
"""

import asyncio
import json
import pytest
import aiohttp
from typing import Optional

from tlon.app.server.serverapp import ServerApp
from tlon.app.clients.sdk import TlonClient, TerminalAdapter
from tlon.base.settings import get_tlon_home


@pytest.fixture
async def server():
    """Start a test server."""
    app = ServerApp(
        host="127.0.0.1",
        rest_port=18080,
        ws_port=18888,
    )
    await app.initialize()

    # Start server in background
    server_task = asyncio.create_task(app.start())

    # Wait for server to be ready
    await asyncio.sleep(1)

    yield app

    # Cleanup
    await app.shutdown()
    server_task.cancel()
    try:
        await server_task
    except asyncio.CancelledError:
        pass


@pytest.fixture
async def client():
    """Create a test client."""
    client = TlonClient(
        "ws://127.0.0.1:18888/ws",
        adapter=TerminalAdapter()
    )
    yield client
    await client.disconnect()


@pytest.mark.asyncio
async def test_protocol_version_negotiation(server):
    """Test protocol version negotiation with new client."""
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect("ws://127.0.0.1:18888/ws") as ws:
            # Send auth with protocol version
            auth_msg = {
                "type": "auth",
                "client_type": "test",
                "protocol_version": "1.0.0",
                "supported_versions": ["1.0.0"],
                "capabilities": ["trace", "filter"],
                "metadata": {"test": True}
            }
            await ws.send_json(auth_msg)

            # Receive auth response
            msg = await ws.receive()
            assert msg.type == aiohttp.WSMsgType.TEXT

            data = json.loads(msg.data)
            assert data["type"] == "auth_success"
            assert data["protocol_version"] == "1.0.0"
            assert "session_id" in data
            assert "capabilities" in data


@pytest.mark.asyncio
async def test_backward_compatibility_old_client(server):
    """Test backward compatibility with old client (no protocol version)."""
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect("ws://127.0.0.1:18888/ws") as ws:
            # Send auth without protocol version (old client)
            auth_msg = {
                "type": "auth",
                "client_type": "tui",
                "metadata": {"version": "0.1.0"}
            }
            await ws.send_json(auth_msg)

            # Receive auth response
            msg = await ws.receive()
            assert msg.type == aiohttp.WSMsgType.TEXT

            data = json.loads(msg.data)
            assert data["type"] == "auth_success"
            # Should default to 1.0.0
            assert data["protocol_version"] == "1.0.0"


@pytest.mark.asyncio
async def test_sdk_client_connection(server):
    """Test SDK client can connect and authenticate."""
    client = TlonClient(
        "ws://127.0.0.1:18888/ws",
        adapter=TerminalAdapter()
    )

    try:
        # Connect
        result = await client.connect()
        assert result is True
        assert client.is_connected

        # Authenticate
        auth_result = await client.authenticate()
        assert auth_result is True
        assert client.is_authenticated
        assert client.session_id is not None
        assert client.protocol_version == "1.0.0"
    finally:
        await client.disconnect()


@pytest.mark.asyncio
async def test_rest_api_traces_endpoint(server):
    """Test REST API /traces endpoint."""
    async with aiohttp.ClientSession() as session:
        # Test /traces endpoint
        async with session.get("http://127.0.0.1:18080/traces") as resp:
            assert resp.status == 200
            data = await resp.json()
            assert "traces" in data
            assert isinstance(data["traces"], list)


@pytest.mark.asyncio
async def test_rest_api_test_endpoints(server):
    """Test REST API test endpoints."""
    async with aiohttp.ClientSession() as session:
        # Test /test/ping
        async with session.get("http://127.0.0.1:18080/test/ping") as resp:
            assert resp.status == 200
            data = await resp.json()
            assert data["status"] == "ok"
            assert data["pong"] is True

        # Test /test/echo
        test_data = {"message": "hello", "number": 42}
        async with session.post(
            "http://127.0.0.1:18080/test/echo",
            json=test_data
        ) as resp:
            assert resp.status == 200
            data = await resp.json()
            assert data["status"] == "ok"
            assert data["echo"] == test_data


@pytest.mark.asyncio
async def test_message_flow_with_trace(server, client):
    """Test complete message flow with trace recording."""
    events_received = []

    # Set up event handler
    @client.on_event()
    async def handle_event(event):
        events_received.append(event)

    try:
        # Connect and authenticate
        await client.connect()
        await client.authenticate()

        # Send a message
        result = await client.send_message("Hello, this is a test message!")
        assert result is True

        # Wait for events
        await asyncio.sleep(2)

        # Check that we received some events
        assert len(events_received) > 0

        # Check for trace-related events
        event_types = [e.get("event_type", "") for e in events_received]
        print(f"Received events: {event_types}")

    finally:
        await client.disconnect()


@pytest.mark.asyncio
async def test_health_endpoint(server):
    """Test health check endpoint."""
    async with aiohttp.ClientSession() as session:
        async with session.get("http://127.0.0.1:18080/health") as resp:
            assert resp.status == 200
            data = await resp.json()
            assert data["status"] == "healthy"
            assert data["running"] is True


@pytest.mark.asyncio
async def test_agents_endpoint(server):
    """Test /agents endpoint."""
    async with aiohttp.ClientSession() as session:
        async with session.get("http://127.0.0.1:18080/agents") as resp:
            assert resp.status == 200
            data = await resp.json()
            assert "agents" in data
            assert isinstance(data["agents"], list)
            assert "count" in data
