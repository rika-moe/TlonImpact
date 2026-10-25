"""Standard agents preserve committed model prefixes across independent mail runs."""

import asyncio
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


async def test_standard_agent_preserves_raw_calls_across_restart_and_resets_changed_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, Any]] = []
    sent = 0

    async def complete(**kwargs: Any) -> Any:
        nonlocal sent
        if not kwargs.get("tools"):
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content='{"claims":[]}', reasoning_content=None, tool_calls=None
                        ),
                        finish_reason="stop",
                    )
                ],
                usage=SimpleNamespace(
                    model_dump=lambda: {
                        "prompt_tokens": 2,
                        "completion_tokens": 3,
                        "total_tokens": 5,
                    }
                ),
            )
        calls.append(kwargs)
        if len(calls) % 2:
            sent += 1
            name = "mail-send"
            arguments = {"to": ["alice@tlon.internal"], "content": f"raw response {sent}"}
        else:
            name, arguments = "finish-turn", {}
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=None,
                        reasoning_content=f"reasoning {len(calls)}",
                        tool_calls=[
                            SimpleNamespace(
                                id=f"call-{len(calls)}",
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
                model_dump=lambda: {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5}
            ),
        )

    monkeypatch.setenv("OPENAI_API_KEY", "fake")
    monkeypatch.setattr("litellm.acompletion", complete)
    member = ""

    async def deliver(url: str, sequence: int) -> None:
        async with aiohttp.ClientSession(headers=WEBUI_HEADERS) as http:
            await _send_mail(http, url, f"incoming task {sequence}", to=[member])
        database = tmp_path / member / "program.sqlite3"
        async with asyncio.timeout(30):
            while True:
                if database.exists():
                    with sqlite3.connect(database) as connection:
                        completed = connection.execute(
                            "SELECT COUNT(*) FROM runs WHERE completed=1 AND run_id LIKE '%:handler:mail-delivered'"
                        ).fetchone()[0]
                    if completed >= sequence:
                        break
                await asyncio.sleep(0.02)
        replies = [mail["content"] for mail in _read_inbox(tmp_path, "human:alice")]
        assert replies == [f"raw response {index}" for index in range(1, sequence + 1)]

    async with _server(tmp_path) as (url, _):
        async with (
            aiohttp.ClientSession(headers=WEBUI_HEADERS) as http,
            http.post(
                url + "/admin/config/agents",
                json={
                    "prototype": builtin_id("标准工具迭代"),
                    "name": "连续消息测试员",
                    "model": "openai/gpt-test",
                },
            ) as response,
        ):
            body = await response.json()
            assert response.status == 200, body
            member = body["agent_id"]
        await deliver(url, 1)

    # A process-local continuation would disappear here; the next app reads the prior run.
    async with _server(tmp_path) as (url, _):
        await deliver(url, 2)
    assert len(calls) == 4
    assert calls[2]["messages"][: len(calls[1]["messages"])] == calls[1]["messages"]
    for first, second in [(calls[0], calls[1]), (calls[2], calls[3])]:
        assert second["messages"][: len(first["messages"])] == first["messages"]
        assistant = second["messages"][-2]
        assert assistant["reasoning_content"].startswith("reasoning ")
        assert json.loads(assistant["tool_calls"][0]["function"]["arguments"])[
            "content"
        ].startswith("raw response ")

    # Changing frozen model input resets the continuation instead of rewriting old messages.
    paths = TlonPaths(tmp_path)
    paths.agent_identity_file(member).write_text(
        "新的身份契约：CONTINUATION-RESET", encoding="utf-8"
    )
    async with _server(tmp_path) as (url, _):
        await deliver(url, 3)
    assert len(calls) == 6
    assert "CONTINUATION-RESET" in calls[4]["messages"][0]["content"]
    assert calls[4]["messages"][: len(calls[3]["messages"])] != calls[3]["messages"]
    with sqlite3.connect(tmp_path / member / "program.sqlite3") as connection:
        budgets = connection.execute(
            "SELECT root_id,SUM(charged),COUNT(*) FROM model_allocations "
            "WHERE root_id LIKE '%:handler:mail-delivered' GROUP BY root_id"
        ).fetchall()
    assert len(budgets) == 3
    assert all(row[1:] == (6, 2) for row in budgets)
