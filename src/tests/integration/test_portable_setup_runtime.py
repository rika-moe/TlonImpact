"""First setup drains already booted members before accepting their first mail."""

import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from tests.program_helpers import member_boot_idle
from tlon.base.paths import TlonPaths
from tlon.base.settings import Settings
from tlon.organization.mail.store import MailStore
from tlon_server.serverapp import ServerApp

from .conftest import WEBUI_HEADERS, _initialize_identity


@pytest.mark.full_initial_society
async def test_first_setup_replaces_booted_models_before_first_reply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool_turns = 0
    observed_models: list[str] = []

    async def completion(request: web.Request) -> web.Response:
        nonlocal tool_turns
        body = await request.json()
        observed_models.append(body["model"])
        message: dict[str, Any] = {"role": "assistant", "content": "OK"}
        finish = "stop"
        if body.get("tools"):
            tool_turns += 1
            name, arguments = (
                ("mail-send", {"to": ["alice@tlon.internal"], "content": "new-model-reply"})
                if tool_turns == 1
                else ("finish-turn", {})
            )
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": f"setup-{tool_turns}",
                        "type": "function",
                        "function": {"name": name, "arguments": json.dumps(arguments)},
                    }
                ],
            }
            finish = "tool_calls"
        return web.json_response(
            {
                "id": "setup-proof",
                "object": "chat.completion",
                "created": 1,
                "model": "gpt-4o-mini",
                "choices": [{"index": 0, "message": message, "finish_reason": finish}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
        )

    model_app = web.Application()
    model_app.router.add_post("/v1/chat/completions", completion)
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    monkeypatch.setattr(
        "tlon.agents.assembly.DockerRuntime",
        MagicMock(side_effect=RuntimeError("test without Docker")),
    )
    async with TestServer(model_app) as model_server:
        monkeypatch.setenv("OPENAI_API_BASE", str(model_server.make_url("/v1")))
        paths = TlonPaths(tmp_path)
        server = ServerApp(rest_port=0, tlon_paths=paths, settings=Settings.from_env())
        await server.initialize()
        running = asyncio.create_task(server.start())
        try:
            await asyncio.wait_for(server.ready.wait(), 10)
            base_url = f"http://127.0.0.1:{server.rest_port}"
            await _initialize_identity(base_url)
            async with aiohttp.ClientSession(headers=WEBUI_HEADERS) as http:
                async with asyncio.timeout(30):
                    while True:
                        async with http.get(base_url + "/agents") as response:
                            agents = (await response.json())["agents"]
                        if len(agents) == 5 and all(
                            member_boot_idle(tmp_path, agent) for agent in agents
                        ):
                            break
                        await asyncio.sleep(0.05)
                async with http.get(base_url + "/admin/llm/setup") as response:
                    initial = (await response.json())["initial_member_ids"]
                async with http.post(
                    base_url + "/admin/llm/setup",
                    json={
                        "provider": "openai",
                        "model": "openai/gpt-4o-mini",
                        "api_key": "test-setup-key",
                        "scope": "initial",
                        "agent_ids": [],
                    },
                ) as response:
                    setup = await response.json()
                    assert response.status == 200, setup
                    assert not setup["required"] and setup["pending_members"] == []
                async with http.post(
                    base_url + "/messages",
                    json={
                        "to": [initial[0]],
                        "content": "Reply to me.",
                        "request_id": "first-setup-mail",
                    },
                ) as response:
                    assert response.status == 202, await response.text()
            async with asyncio.timeout(30):
                while True:
                    replies = MailStore(paths.mail_database_file()).mails("human:alice")
                    database = paths.agent_dir(initial[0]) / "program.sqlite3"
                    completed = False
                    if database.exists():
                        with sqlite3.connect(database) as connection:
                            completed = bool(
                                connection.execute(
                                    "SELECT COUNT(*) FROM runs WHERE completed=1 AND run_id LIKE '%:handler:mail-delivered'"
                                ).fetchone()[0]
                            )
                    if completed and any(mail.content == "new-model-reply" for mail in replies):
                        break
                    await asyncio.sleep(0.05)
            assert tool_turns >= 2
            assert all(model == "gpt-4o-mini" for model in observed_models)
        finally:
            await server.shutdown()
            await asyncio.wait_for(running, 10)
