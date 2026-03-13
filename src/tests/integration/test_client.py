#!/usr/bin/env python3
"""Integration Test Client for Tlon.

A non-interactive WebSocket client for automated integration testing.

Usage:
    python test_client.py --host 127.0.0.1 --port 8888 --test-file test_scenario.yaml
    python test_client.py --test-case TC001
    python test_client.py --health-check

Environment Variables:
    TLON_TEST_HOST: Server host (default: 127.0.0.1)
    TLON_TEST_WS_PORT: WebSocket port (default: 8888)
    TLON_TEST_REST_PORT: REST API port (default: 8080)
"""

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiohttp
import yaml


@dataclass
class TestResult:
    """Result of a single test case."""

    id: str
    name: str
    status: str  # passed, failed, skipped
    duration_ms: float
    error: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TestRunReport:
    """Complete test run report."""

    timestamp: str
    duration_ms: float
    server_version: str
    total: int
    passed: int
    failed: int
    skipped: int
    results: List[TestResult]
    logs: Dict[str, str]

    def to_json(self) -> str:
        """Convert to JSON string."""
        return json.dumps(
            {
                "test_run": {
                    "timestamp": self.timestamp,
                    "duration_ms": self.duration_ms,
                    "server_version": self.server_version,
                },
                "summary": {
                    "total": self.total,
                    "passed": self.passed,
                    "failed": self.failed,
                    "skipped": self.skipped,
                },
                "results": [asdict(r) for r in self.results],
                "logs": self.logs,
            },
            indent=2,
            ensure_ascii=False,
        )


class TestClient:
    """Non-interactive WebSocket client for testing."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        ws_port: int = 8888,
        rest_port: int = 8080,
        timeout: int = 60,
    ):
        self.host = host
        self.ws_port = ws_port
        self.rest_port = rest_port
        self.ws_url = f"ws://{host}:{ws_port}/ws"
        self.rest_url = f"http://{host}:{rest_port}"
        self.timeout = timeout

        self.session: Optional[aiohttp.ClientSession] = None
        self.ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self.connected = False
        self.messages: List[Dict] = []
        self.events: List[Dict] = []

    async def __aenter__(self):
        """Async context manager entry."""
        self.session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.disconnect()
        if self.session:
            await self.session.close()

    async def check_health(self) -> tuple[bool, Dict]:
        """Check server health via REST API.

        Returns:
            Tuple of (is_healthy, health_data)
        """
        try:
            async with self.session.get(
                f"{self.rest_url}/health", timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return True, data
                return False, {"status": resp.status, "error": "Non-200 response"}
        except Exception as e:
            return False, {"error": str(e)}

    async def get_agents(self) -> List[Dict]:
        """Get list of agents via REST API."""
        try:
            async with self.session.get(
                f"{self.rest_url}/agents", timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("agents", [])
                return []
        except Exception:
            return []

    async def wake_agent(self, agent_id: str) -> bool:
        """Wake up an agent via REST API."""
        try:
            async with self.session.post(
                f"{self.rest_url}/agents/{agent_id}/wake",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                return resp.status == 200
        except Exception:
            return False

    async def sleep_agent(self, agent_id: str) -> bool:
        """Put an agent to sleep via REST API."""
        try:
            async with self.session.post(
                f"{self.rest_url}/agents/{agent_id}/stop",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                return resp.status == 200
        except Exception:
            return False

    async def connect(self) -> bool:
        """Connect to WebSocket server.

        Returns:
            True if connection successful
        """
        try:
            self.ws = await self.session.ws_connect(self.ws_url)

            # Send authentication
            auth_msg = {
                "type": "auth",
                "client_type": "test",
                "metadata": {"version": "0.2.0", "test_id": str(uuid.uuid4())},
            }
            await self.ws.send_json(auth_msg)

            # Wait for auth response
            msg = await asyncio.wait_for(self.ws.receive(), timeout=10.0)
            if msg.type == aiohttp.WSMsgType.TEXT:
                data = json.loads(msg.data)
                if data.get("type") == "auth_success":
                    self.connected = True
                    return True

            await self.ws.close()
            self.ws = None
            return False

        except Exception as e:
            print(f"Connection failed: {e}")
            return False

    async def disconnect(self):
        """Disconnect from WebSocket."""
        if self.ws:
            await self.ws.close()
        self.connected = False

    async def send_message(self, content: str, mentions: List[str] = None) -> bool:
        """Send a message.

        Args:
            content: Message content
            mentions: List of agent IDs to mention

        Returns:
            True if sent successfully
        """
        if not self.connected or not self.ws:
            return False

        message = {
            "type": "message",
            "content": content,
            "mentions": mentions or [],
            "timestamp": time.time(),
        }

        try:
            await self.ws.send_json(message)
            return True
        except Exception as e:
            print(f"Failed to send message: {e}")
            return False

    async def wait_for_response(
        self,
        timeout: float = 30.0,
        predicate=None,
    ) -> Optional[Dict]:
        """Wait for a response matching predicate.

        Args:
            timeout: Maximum time to wait
            predicate: Function to check if message matches

        Returns:
            Matching message or None if timeout
        """
        if not self.connected:
            return None

        start_time = time.time()

        try:
            while time.time() - start_time < timeout:
                msg = await asyncio.wait_for(
                    self.ws.receive(), timeout=max(0.1, timeout - (time.time() - start_time))
                )

                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    self.events.append(data)

                    # Check if this is a response message
                    if data.get("type") in ("message", "event"):
                        payload = data.get("payload", {})
                        sender = payload.get("sender", {})
                        sender_type = sender.get("type", "unknown")

                        # Only count agent responses (not user echo)
                        if sender_type != "human":
                            if predicate is None or predicate(data):
                                return data

                elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
                    return None

        except asyncio.TimeoutError:
            return None

        return None


class TestRunner:
    """Run integration test cases."""

    def __init__(self, client: TestClient, verbose: bool = False):
        self.client = client
        self.verbose = verbose
        self.results: List[TestResult] = []

    def log(self, message: str):
        """Log a message if verbose mode."""
        if self.verbose:
            print(f"[TEST] {message}")

    async def run_test_case(self, test_id: str, test_name: str) -> TestResult:
        """Run a single test case.

        Args:
            test_id: Test case identifier (e.g., "TC001")
            test_name: Human-readable test name

        Returns:
            TestResult with outcome
        """
        print(f"\n{'='*60}")
        print(f"Running {test_id}: {test_name}")
        print(f"{'='*60}")

        start_time = time.time()

        # Map test IDs to test methods
        test_methods = {
            "TC001": self.test_basic_connection,
            "TC002": self.test_single_message,
            "TC003": self.test_multi_turn_conversation,
            "TC004": self.test_agent_lifecycle,
            "TC005": self.test_multi_agent_collaboration,
            "TC006": self.test_reconnection,
        }

        test_method = test_methods.get(test_id)
        if not test_method:
            return TestResult(
                id=test_id,
                name=test_name,
                status="skipped",
                duration_ms=0,
                error=f"Unknown test case: {test_id}",
            )

        try:
            await test_method()
            duration = (time.time() - start_time) * 1000

            print(f"✅ {test_id} PASSED ({duration:.0f}ms)")
            return TestResult(
                id=test_id, name=test_name, status="passed", duration_ms=duration
            )

        except AssertionError as e:
            duration = (time.time() - start_time) * 1000
            print(f"❌ {test_id} FAILED: {e}")
            return TestResult(
                id=test_id,
                name=test_name,
                status="failed",
                duration_ms=duration,
                error=str(e),
            )

        except Exception as e:
            duration = (time.time() - start_time) * 1000
            print(f"❌ {test_id} ERROR: {e}")
            return TestResult(
                id=test_id,
                name=test_name,
                status="failed",
                duration_ms=duration,
                error=f"{type(e).__name__}: {e}",
            )

    async def test_basic_connection(self):
        """TC001: Basic connection test."""
        self.log("Checking server health...")
        healthy, health_data = await self.client.check_health()
        assert healthy, f"Server health check failed: {health_data}"
        self.log(f"Server healthy: {health_data}")

        self.log("Connecting to WebSocket...")
        connected = await self.client.connect()
        assert connected, "Failed to connect to WebSocket"
        self.log("WebSocket connected")

        agents = await self.client.get_agents()
        self.log(f"Found {len(agents)} agents")
        assert len(agents) > 0, "No agents loaded"

    async def test_single_message(self):
        """TC002: Single message round-trip."""
        # Ensure connected
        if not self.client.connected:
            connected = await self.client.connect()
            assert connected, "Failed to connect"

        self.log("Sending test message...")
        sent = await self.client.send_message("你好，这是一个测试消息")
        assert sent, "Failed to send message"

        self.log("Waiting for response...")
        response = await self.client.wait_for_response(timeout=45.0)
        assert response is not None, "No response received within timeout"

        payload = response.get("payload", {})
        content = payload.get("content", "")
        self.log(f"Received response: {content[:50]}...")
        assert len(content) > 0, "Empty response content"

    async def test_multi_turn_conversation(self):
        """TC003: Multi-turn conversation."""
        if not self.client.connected:
            connected = await self.client.connect()
            assert connected, "Failed to connect"

        conversation = [
            "我想做一个简单的网页",
            "用什么技术比较好？",
            "就按这个方案",
        ]

        for i, message in enumerate(conversation):
            self.log(f"Turn {i+1}: Sending '{message[:30]}...'")
            sent = await self.client.send_message(message)
            assert sent, f"Failed to send message {i+1}"

            self.log(f"Turn {i+1}: Waiting for response...")
            response = await self.client.wait_for_response(timeout=45.0)
            assert response is not None, f"No response for turn {i+1}"

            payload = response.get("payload", {})
            content = payload.get("content", "")
            assert len(content) > 0, f"Empty response for turn {i+1}"

            # Small delay between turns
            await asyncio.sleep(0.5)

    async def test_agent_lifecycle(self):
        """TC004: Agent wake/sleep/restart."""
        agents = await self.client.get_agents()
        assert len(agents) > 0, "No agents available"

        agent = agents[0]
        agent_id = agent.get("id")
        self.log(f"Testing with agent: {agent_id}")

        # Test wake
        self.log(f"Waking agent {agent_id}...")
        result = await self.client.wake_agent(agent_id)
        assert result, f"Failed to wake agent {agent_id}"

        await asyncio.sleep(1)  # Give time for state change

        # Test sleep
        self.log(f"Putting agent {agent_id} to sleep...")
        result = await self.client.sleep_agent(agent_id)
        assert result, f"Failed to sleep agent {agent_id}"

    async def test_multi_agent_collaboration(self):
        """TC005: Multi-agent collaboration."""
        if not self.client.connected:
            connected = await self.client.connect()
            assert connected, "Failed to connect"

        # This test requires PM to coordinate
        self.log("Sending coordination request...")
        sent = await self.client.send_message("帮我安排一个简单的项目")
        assert sent, "Failed to send message"

        # Wait for multiple responses
        responses = []
        for _ in range(3):  # Expect up to 3 responses
            response = await self.client.wait_for_response(timeout=30.0)
            if response:
                responses.append(response)

        self.log(f"Received {len(responses)} responses")
        assert len(responses) > 0, "No responses received"

    async def test_reconnection(self):
        """TC006: Connection recovery."""
        if not self.client.connected:
            connected = await self.client.connect()
            assert connected, "Failed to connect"

        self.log("Simulating disconnect...")
        await self.client.disconnect()

        self.log("Reconnecting...")
        connected = await self.client.connect()
        assert connected, "Failed to reconnect"

        self.log("Testing message after reconnect...")
        sent = await self.client.send_message("重连后的测试消息")
        assert sent, "Failed to send message after reconnect"

        response = await self.client.wait_for_response(timeout=30.0)
        assert response is not None, "No response after reconnect"


async def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Tlon Integration Test Client")
    parser.add_argument("--host", default=os.getenv("TLON_TEST_HOST", "127.0.0.1"))
    parser.add_argument(
        "--ws-port", type=int, default=int(os.getenv("TLON_TEST_WS_PORT", "8888"))
    )
    parser.add_argument(
        "--rest-port", type=int, default=int(os.getenv("TLON_TEST_REST_PORT", "8080"))
    )
    parser.add_argument("--test", action="append", help="Test case ID (e.g., TC001)")
    parser.add_argument("--health-check", action="store_true", help="Only check health")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    parser.add_argument("--output", "-o", help="Output report file")
    parser.add_argument(
        "--timeout", type=int, default=60, help="Default timeout in seconds"
    )

    args = parser.parse_args()

    print("🧪 Tlon Integration Test Client")
    print(f"   Server: {args.host}:{args.ws_port}")
    print(f"   REST: {args.host}:{args.rest_port}")
    print()

    async with TestClient(
        host=args.host,
        ws_port=args.ws_port,
        rest_port=args.rest_port,
        timeout=args.timeout,
    ) as client:
        # Quick health check first
        print("Checking server health...")
        healthy, health_data = await client.check_health()
        if not healthy:
            print(f"❌ Server not healthy: {health_data}")
            return 1

        print(f"✅ Server healthy: {health_data.get('version', 'unknown')}")

        if args.health_check:
            return 0

        # Determine which tests to run
        tests_to_run = args.test or ["TC001", "TC002", "TC003", "TC004"]

        test_definitions = {
            "TC001": "基础连接测试",
            "TC002": "单轮对话测试",
            "TC003": "多轮对话测试",
            "TC004": "Agent 生命周期测试",
            "TC005": "多 Agent 协作测试",
            "TC006": "连接恢复测试",
        }

        # Run tests
        runner = TestRunner(client, verbose=args.verbose)
        start_time = time.time()

        for test_id in tests_to_run:
            test_name = test_definitions.get(test_id, "未知测试")
            result = await runner.run_test_case(test_id, test_name)
            runner.results.append(result)

        total_duration = (time.time() - start_time) * 1000

        # Generate report
        passed = sum(1 for r in runner.results if r.status == "passed")
        failed = sum(1 for r in runner.results if r.status == "failed")
        skipped = sum(1 for r in runner.results if r.status == "skipped")

        report = TestRunReport(
            timestamp=datetime.utcnow().isoformat() + "Z",
            duration_ms=total_duration,
            server_version=health_data.get("version", "unknown"),
            total=len(runner.results),
            passed=passed,
            failed=failed,
            skipped=skipped,
            results=runner.results,
            logs={},
        )

        # Print summary
        print(f"\n{'='*60}")
        print("TEST SUMMARY")
        print(f"{'='*60}")
        print(f"Total:   {report.total}")
        print(f"Passed:  {report.passed} ✅")
        print(f"Failed:  {report.failed} ❌")
        print(f"Skipped: {report.skipped} ⏭️")
        print(f"Duration: {total_duration/1000:.1f}s")

        # Save report
        if args.output:
            with open(args.output, "w") as f:
                f.write(report.to_json())
            print(f"\nReport saved to: {args.output}")

        return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
