"""An agent authors a complete program through the real mail and HTTP boundaries."""

import asyncio
import json
import sqlite3
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import aiohttp
import pytest

from tlosure.edn import loads

from .conftest import WEBUI_HEADERS, _read_inbox, _send_mail


async def test_agent_authors_reviewed_bundle_then_child_runs_frozen_sources(
    inproc_server: tuple[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    base_url, home = inproc_server
    token = uuid.uuid4().hex
    behavior = "{:version 2 :handlers {:mail-delivered work.reply/handle} :fuel 10000}"
    child_modules = {
        "work/reply.tlon": """
        (ns work.reply (:require [work.text :as text]) (:export handle))
        (defn handle [event]
          (let [answer (text/reply (get (get event :mail) :content))]
            (mail-send {:to [(get event :sender)] :content answer})
            {:result answer}))
        """,
        "work/text.tlon": """
        (ns work.text (:export reply))
        (defn reply [task] (str "authored:" task))
        """,
    }
    author_modules = {
        "work/reply.tlon": """
        (ns work.reply (:require [tlon.authoring :as authoring]) (:export handle))
        (defn handle [event]
          (let [candidate (authoring/draft {:messages [{:role "user" :content (get (get event :mail) :content)}]
                                            :max_tokens 512 :max_rounds 6})
                proposal (prototype-write {:behavior (get candidate :behavior)
                                           :modules (get candidate :modules)
                                           :display_name "机器起草的完整回信程序"})]
            (mail-send {:to [(get event :sender)] :content (str "proposal:" (get proposal :name))})
            {:result (get proposal :name)}))
        """
    }
    calls: list[dict[str, Any]] = []

    invalid_modules = {
        **child_modules,
        "work/text.tlon": "(ns work.text (:export reply)) (defn reply [task] (missing task))",
    }
    script = [
        ("file-read", {"file_path": "/workspace/tlon/docs/README.md"}),
        ("file-read", {"file_path": "/workspace/tlon/stdlib/messages.tlon"}),
        ("program-check", {"behavior": behavior, "modules": invalid_modules}),
        ("program-check", {"behavior": behavior, "modules": child_modules}),
    ]

    async def complete(**kwargs: Any) -> Any:
        index = len(calls)
        calls.append(kwargs)
        assert index < len(script), "unexpected additional model request"
        if index:
            report = json.loads(kwargs["messages"][-1]["content"])
            assert report["success"], report
            if index == 1:
                assert report["data"]["file_path"] == script[0][1]["file_path"]
                assert "tlon" in report["data"]["content"].lower()
            elif index == 2:
                assert "(ns tlon.messages" in report["data"]["content"]
            else:
                assert report["data"]["valid"] is False
                assert report["data"]["diagnostics"]
        name, arguments = script[index]
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=None,
                        tool_calls=[
                            SimpleNamespace(
                                id=f"author-{index}",
                                type="function",
                                function=SimpleNamespace(
                                    name=name, arguments=json.dumps(arguments)
                                ),
                            )
                        ],
                        reasoning_content=None,
                    ),
                    finish_reason="tool_calls",
                )
            ],
            usage=SimpleNamespace(
                model_dump=lambda: {
                    "prompt_tokens": 10,
                    "completion_tokens": 20,
                    "total_tokens": 30,
                }
            ),
        )

    async def reply_from(agent: str, expected: str | None = None) -> str:
        async with asyncio.timeout(15):
            while True:
                for mail in _read_inbox(home, "human:alice"):
                    if mail["from_address"] == f"{agent}@tlon.internal" and (
                        expected is None or mail["content"] == expected
                    ):
                        return str(mail["content"])
                await asyncio.sleep(0.02)

    monkeypatch.setenv("OPENAI_API_KEY", "fake")
    monkeypatch.setattr("litellm.acompletion", complete)
    async with aiohttp.ClientSession(headers=WEBUI_HEADERS) as http:
        async with http.post(
            f"{base_url}/admin/prototypes",
            json={
                "display_name": f"起草者-{token}",
                "behavior": behavior,
                "modules": author_modules,
            },
        ) as response:
            body = await response.json()
            assert response.status == 201, body
            parent = body["prototype"]["name"]
        async with http.post(
            f"{base_url}/admin/config/agents",
            json={
                "prototype": parent,
                "name": f"起草者-{token[:8]}",
                "model": "openai/gpt-test",
            },
        ) as response:
            body = await response.json()
            assert response.status == 200, body
            author = body["agent_id"]
        await _send_mail(http, base_url, "从经验起草两个可复用模块：" + token, to=[author])
        proposal_reply = await reply_from(author)
        assert proposal_reply.startswith("proposal:"), proposal_reply
        proposed = proposal_reply.removeprefix("proposal:")
        assert len(calls) == 4
        assert token in calls[0]["messages"][-2]["content"]
        assert all(
            "behavior-propose" not in {tool["function"]["name"] for tool in call["tools"]}
            for call in calls
        )
        assert calls[0]["max_tokens"] > 0
        async with http.get(f"{base_url}/admin/prototypes/staging/{proposed}") as response:
            detail = (await response.json())["prototype"]
            assert response.status == 200
            assert detail["trusted"] is False
            assert detail["modules"] == child_modules
            assert detail["parent"] is None
        async with http.post(
            f"{base_url}/admin/config/agents",
            json={
                "prototype": proposed,
                "name": "未经批准",
                "model": "openai/gpt-test",
            },
        ) as response:
            assert response.status == 400
        async with http.post(f"{base_url}/admin/prototypes/staging/{proposed}/approve") as response:
            assert response.status == 200, await response.text()
        async with http.post(
            f"{base_url}/admin/config/agents",
            json={
                "prototype": proposed,
                "name": f"子代-{token[:8]}",
                "model": "openai/gpt-test",
            },
        ) as response:
            body = await response.json()
            assert response.status == 200, body
            child = body["agent_id"]
        for relative, source in child_modules.items():
            assert (home / child / "config/modules" / relative).read_text() == source
        await _send_mail(http, base_url, token, to=[child])
        assert await reply_from(child, "authored:" + token) == "authored:" + token
        database = home / child / "program.sqlite3"
        async with asyncio.timeout(5):
            while True:
                with sqlite3.connect(database) as connection:
                    snapshots = connection.execute(
                        "SELECT r.run_id,e.package_edn FROM runs r JOIN entry_programs e ON r.run_id=e.run_id WHERE r.completed=1"
                    ).fetchall()
                if snapshots:
                    break
                await asyncio.sleep(0.02)
        run_id, frozen = snapshots[0]
        assert loads(frozen)["modules"]["work/text.tlon"] == child_modules["work/text.tlon"]
        # Tampering with the trusted library cannot change its approval digest,
        # copied instance, or the already committed source snapshot.
        (home / "prototypes" / proposed / "modules/work/text.tlon").write_text("(ns broken)")
        async with http.post(
            f"{base_url}/admin/config/agents",
            json={
                "prototype": proposed,
                "name": "篡改后实例",
                "model": "openai/gpt-test",
            },
        ) as response:
            assert response.status == 400
            assert "approved package" in (await response.json())["message"]
        await _send_mail(http, base_url, token + "-again", to=[child])
        assert (
            await reply_from(child, "authored:" + token + "-again")
            == "authored:" + token + "-again"
        )
        with sqlite3.connect(database) as connection:
            assert (
                connection.execute(
                    "SELECT package_edn FROM entry_programs WHERE run_id=?", (run_id,)
                ).fetchone()[0]
                == frozen
            )
    assert len(calls) == 4  # The child executes authored functions without another model call.
