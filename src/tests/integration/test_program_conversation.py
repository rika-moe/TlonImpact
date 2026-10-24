"""Ordinary module conversation crosses HTTP, model, tool, and mail boundaries."""

import asyncio
import json
import sqlite3
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import aiohttp
import pytest

from .conftest import WEBUI_HEADERS, _read_inbox, _send_mail


async def test_nested_tool_conversation_reviews_and_sends_after_local_return(
    inproc_server: tuple[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, Any]] = []

    async def complete(**kwargs: Any) -> Any:
        calls.append(kwargs)
        turn = len(calls)
        tool_calls = None
        content = None
        if turn == 1:
            tool_calls = [
                SimpleNamespace(
                    id=identity, function=SimpleNamespace(name="time-now", arguments="{}")
                )
                for identity in ("clock-a", "clock-b")
            ]
        elif turn in (2, 7):
            tool_calls = [
                SimpleNamespace(
                    id=f"finish-{turn}",
                    function=SimpleNamespace(name="finish-turn", arguments="{}"),
                )
            ]
        elif turn in (3, 4, 5):
            content = ["draft", "review", "revised"][turn - 3]
        elif turn == 6:
            prompt = kwargs["messages"][-1]["content"]
            recipient = prompt.split("Send this reviewed answer to ", 1)[1].split(" using", 1)[0]
            tool_calls = [
                SimpleNamespace(
                    id="send-reviewed",
                    function=SimpleNamespace(
                        name="mail-send",
                        arguments=json.dumps({"to": [recipient], "content": "revised"}),
                    ),
                )
            ]
        else:
            raise AssertionError(f"unexpected model call {turn}")
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=content,
                        tool_calls=tool_calls,
                        reasoning_content="read clock first" if turn == 1 else None,
                    ),
                    finish_reason="tool_calls" if tool_calls else "stop",
                )
            ],
            usage=SimpleNamespace(
                model_dump=lambda: {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5}
            ),
        )

    monkeypatch.setenv("OPENAI_API_KEY", "fake")
    monkeypatch.setattr("litellm.acompletion", complete)
    base_url, home = inproc_server
    example = Path(__file__).parents[2] / "tlon/resources/examples/tool-reviewed-reply"
    modules = {
        path.relative_to(example / "modules").as_posix(): path.read_text()
        for path in (example / "modules").rglob("*.tlon")
    }
    token = uuid.uuid4().hex
    async with aiohttp.ClientSession(headers=WEBUI_HEADERS) as http:
        async with http.post(
            f"{base_url}/admin/prototypes",
            json={
                "display_name": f"工具核验-{token}",
                "behavior": (example / "behavior.tlon").read_text(),
                "modules": modules,
            },
        ) as response:
            body = await response.json()
            assert response.status == 201, body
            prototype = body["prototype"]["name"]
        async with http.post(
            f"{base_url}/admin/config/agents",
            json={
                "prototype": prototype,
                "name": f"核验员-{token[:8]}",
                "model": "openai/gpt-test",
            },
        ) as response:
            body = await response.json()
            assert response.status == 200, body
            agent = body["agent_id"]
        receipt = await _send_mail(http, base_url, token, to=[agent])
        async with asyncio.timeout(15):
            while len(calls) < 7 or not any(
                mail["content"] == "revised" and mail["from_address"] == f"{agent}@tlon.internal"
                for mail in _read_inbox(home, "human:alice")
            ):
                await asyncio.sleep(0.02)
        async with asyncio.timeout(15):
            while True:
                with sqlite3.connect(home / agent / "program.sqlite3") as connection:
                    run = connection.execute(
                        "SELECT completed, error FROM runs WHERE run_id = ?",
                        (f"{receipt['mail_id']}:handler:mail-delivered",),
                    ).fetchone()
                assert run is None or run[1] is None, run
                if run is not None and run[0]:
                    break
                await asyncio.sleep(0.02)
    assert len(calls) == 7
    history = calls[1]["messages"]
    assistant = [message for message in history if message.get("tool_calls")]
    assert len(assistant) == 1
    assert assistant[0]["content"] is None
    assert assistant[0]["reasoning_content"] == "read clock first"
    assert [call["id"] for call in assistant[0]["tool_calls"]] == ["clock-a", "clock-b"]
    results = [message for message in history if message["role"] == "tool"]
    assert [message["tool_call_id"] for message in results] == ["clock-a", "clock-b"]
    assert all(json.loads(message["content"])["success"] for message in results)
    with sqlite3.connect(home / agent / "program.sqlite3") as connection:
        budgets = connection.execute(
            "SELECT root_id, SUM(charged), COUNT(*) FROM model_allocations GROUP BY root_id"
        ).fetchall()
        leaves = connection.execute(
            "SELECT identity_json FROM invocations WHERE json_extract(identity_json, '$.name')='time-now'"
        ).fetchall()
    assert len(budgets) == 1
    assert budgets[0][1:] == (4096 * 7, 7)
    assert all(call["max_tokens"] == 4096 for call in calls)
    assert len(leaves) == 2
    identities = [json.loads(row[0]) for row in leaves]
    assert identities[0]["call_id"] != identities[1]["call_id"]
    assert all(identity["root_id"] == budgets[0][0] for identity in identities)
