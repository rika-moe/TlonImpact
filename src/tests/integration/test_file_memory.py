"""Private notes are optional ordinary tool effects, never automatic model input."""

import asyncio
import copy
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import aiohttp
import pytest

from tests.prototype_helpers import builtin_id
from tlon.base.paths import TlonPaths

from .conftest import WEBUI_HEADERS, _read_inbox, _send_mail
from .test_post_office import _server


async def test_optional_file_memory_survives_restart_without_automatic_injection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests: list[dict[str, Any]] = []
    steps: list[tuple[str, dict[str, Any]]] = [
        ("mail-send", {"to": ["alice@tlon.internal"], "content": "completed without notes"}),
        ("finish-turn", {}),
        ("file-write", {"file_path": "memory/preferences.md", "content": "EXPLICIT-NOTE: tea"}),
        ("file-list", {"path": "memory"}),
        ("file-search", {"path": "memory", "pattern": "EXPLICIT-NOTE"}),
        ("file-read", {"file_path": "memory/preferences.md"}),
        (
            "file-edit",
            {"file_path": "memory/preferences.md", "old_string": "tea", "new_string": "coffee"},
        ),
        ("mail-send", {"to": ["alice@tlon.internal"], "content": "notes maintained"}),
        ("finish-turn", {}),
        ("file-read", {"file_path": "memory/preferences.md"}),
        ("mail-send", {"to": ["alice@tlon.internal"], "content": "notes recovered"}),
        ("finish-turn", {}),
    ]

    async def complete(**kwargs: Any) -> Any:
        assert kwargs.get("tools"), "No implicit distillation or reflection model request"
        requests.append(copy.deepcopy(kwargs))
        name, arguments = steps[len(requests) - 1]
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=None,
                        reasoning_content=None,
                        tool_calls=[
                            SimpleNamespace(
                                id=f"file-memory-{len(requests)}",
                                function=SimpleNamespace(
                                    name=name, arguments=json.dumps(arguments)
                                ),
                            )
                        ],
                    ),
                    finish_reason="tool_calls",
                )
            ],
            usage=SimpleNamespace(
                model_dump=lambda: {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}
            ),
        )

    monkeypatch.setenv("OPENAI_API_KEY", "fake")
    monkeypatch.setattr("litellm.acompletion", complete)
    paths = TlonPaths(tmp_path)
    member = ""

    async def deliver(url: str, sequence: int) -> None:
        async with aiohttp.ClientSession(headers=WEBUI_HEADERS) as http:
            await _send_mail(http, url, f"task {sequence}", to=[member])
        async with asyncio.timeout(30):
            while True:
                database = tmp_path / member / "program.sqlite3"
                if database.exists():
                    with sqlite3.connect(database) as connection:
                        count = connection.execute(
                            "SELECT COUNT(*) FROM runs WHERE completed=1 "
                            "AND run_id LIKE '%:handler:mail-delivered'"
                        ).fetchone()[0]
                    if count >= sequence:
                        break
                await asyncio.sleep(0.02)

    async with _server(tmp_path) as (url, _):
        async with (
            aiohttp.ClientSession(headers=WEBUI_HEADERS) as http,
            http.post(
                url + "/admin/config/agents",
                json={
                    "prototype": builtin_id("标准智能体"),
                    "name": "私有笔记测试员",
                    "model": "openai/gpt-test",
                },
            ) as response,
        ):
            body = await response.json()
            assert response.status == 200, body
            member = body["agent_id"]
        notes = paths.agent_workspace_dir(member) / "memory"
        notes.mkdir(parents=True)
        hidden = notes / "private.md"
        hidden.write_text("HIDDEN-NOTE-ONE", encoding="utf-8")
        await deliver(url, 1)
        assert len(requests) == 2
        assert all("HIDDEN-NOTE" not in json.dumps(call) for call in requests)
        assert not (notes / "preferences.md").exists()
        frozen = copy.deepcopy(requests)
        hidden.write_text("HIDDEN-NOTE-TWO", encoding="utf-8")
        await deliver(url, 2)
        assert requests[:2] == frozen
        assert (notes / "preferences.md").read_text(encoding="utf-8") == "EXPLICIT-NOTE: coffee"

    async with _server(tmp_path) as (url, _):
        await deliver(url, 3)
    assert len(requests) == len(steps)
    assert [mail["content"] for mail in _read_inbox(tmp_path, "human:alice")] == [
        "completed without notes",
        "notes maintained",
        "notes recovered",
    ]
    # Notes changing on disk never invalidate the stable system contract or get injected.
    assert all(call["messages"][0] == requests[0]["messages"][0] for call in requests)
    assert all("HIDDEN-NOTE" not in json.dumps(call) for call in requests)
    # Every provider request extends its predecessor, including across server restart.
    for earlier, later in zip(requests, requests[1:], strict=False):
        prefix = earlier["messages"]
        assert later["messages"][: len(prefix)] == prefix
    assert "EXPLICIT-NOTE" not in json.dumps(requests[2]["messages"])
    tools = [message for message in requests[-1]["messages"] if message["role"] == "tool"]
    results = [json.loads(message["content"])["data"] or {} for message in tools]
    assert any(result.get("content") == "EXPLICIT-NOTE: tea" for result in results)
    assert any(result.get("content") == "EXPLICIT-NOTE: coffee" for result in results)
    assert any(result.get("count") == 1 and result.get("matches") for result in results)
