"""Real HTTP mail delivery preserves history without automatic memory maintenance."""

import asyncio
from pathlib import Path
from typing import Any

import aiohttp
import pytest

from tlon.agents.run_journal import RunJournal
from tlon.base.paths import TlonPaths

from .conftest import WEBUI_HEADERS, _read_inbox, _send_mail


async def test_module_mail_needs_no_memory_or_background_models_and_reads_provenance(
    inproc_server: tuple[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    url, home = inproc_server
    calls: list[dict[str, Any]] = []

    async def complete(**kwargs: Any) -> Any:
        calls.append(kwargs)
        raise AssertionError("No model required for this member program")

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
        async with asyncio.timeout(15):
            while len(_read_inbox(home, "human:alice")) < 3:
                await asyncio.sleep(0.02)
        assert calls == []
        assert not (home / member / "memory").exists()
        assert not (home / member / "workspace/memory").exists()
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
        assert calls == []  # Reading history does not regenerate the program or call a model.
        assert not (home / member / "sessions.sqlite3").exists()
