"""Real HTTP mail delivery feeds durable program history and maintenance."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import aiohttp
import pytest

from tlon.agents.run_journal import RunJournal
from tlon.base.paths import TlonPaths

from .conftest import WEBUI_HEADERS, _read_inbox, _send_mail


async def test_module_mail_distills_once_and_reads_provenance(
    inproc_server: tuple[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    url, home = inproc_server
    calls: list[dict[str, Any]] = []

    async def complete(**kwargs: Any) -> Any:
        calls.append(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content='{"claims":[{"text":"发布前先核验","importance":0.7}]}',
                        tool_calls=[],
                        reasoning_content=None,
                    ),
                    finish_reason="stop",
                )
            ],
            usage=SimpleNamespace(model_dump=lambda: {"completion_tokens": 20, "total_tokens": 40}),
        )

    monkeypatch.setenv("OPENAI_API_KEY", "fake")
    monkeypatch.setattr("litellm.acompletion", complete)
    modules = {
        "work.tlon": """
    (ns work (:export handle))
    (defn handle [event]
      (let [task (get (get event :mail) :content)
            answer (if (contains? task ":handler:")
                     (get (run-read {:run_id task}) :digest)
                     (str "done:" task))]
        (mail-send {:to [(get event :sender)] :content answer})
        {:result answer}))
    """
    }
    async with aiohttp.ClientSession(headers=WEBUI_HEADERS) as http:
        async with http.post(
            url + "/admin/prototypes",
            json={
                "display_name": "来源消费测试",
                "behavior": "{:version 2 :handlers {:mail-delivered work/handle} :fuel 10000}",
                "modules": modules,
            },
        ) as response:
            body = await response.json()
            assert response.status == 201, body
            prototype = body["prototype"]["name"]
        async with http.post(
            url + "/admin/config/agents",
            json={
                "prototype": prototype,
                "name": "来源消费测试",
                "model": "openai/gpt-test",
            },
        ) as response:
            body = await response.json()
            assert response.status == 200, body
            member = body["agent_id"]
        sources = []
        for index in range(3):
            receipt = await _send_mail(http, url, f"核验-{index}", to=[member])
            sources.append(f"{receipt['mail_id']}:handler:mail-delivered")
        progress = home / member / "memory/distillation.json"
        async with asyncio.timeout(15):
            while True:
                state = json.loads(progress.read_text()) if progress.exists() else {}
                if len(state.get("consumed_source_ids", [])) == 3 and state.get("pending") is None:
                    break
                await asyncio.sleep(0.02)
        assert len(calls) == 1
        assert set(state["consumed_source_ids"]) == set(sources)
        await _send_mail(http, url, sources[0], to=[member])
        async with asyncio.timeout(10):
            while True:
                replies = _read_inbox(home, "human:alice")
                if any(sources[0] in item["content"] for item in replies):
                    break
                await asyncio.sleep(0.02)
        assert any("程序返回" in item["content"] for item in replies)
        journal = RunJournal(member, TlonPaths(home=home))
        async with asyncio.timeout(10):
            while await journal.list_pending():
                await asyncio.sleep(0.01)
        assert await journal.list_pending() == []
        assert len(calls) == 1  # reading a source did not regenerate its program or distill again
        assert not (home / member / "sessions.sqlite3").exists()
