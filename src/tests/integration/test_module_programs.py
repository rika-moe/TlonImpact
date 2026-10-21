"""HTTP prototype publication and member adoption execute a two-module reply."""

import asyncio
import uuid
from pathlib import Path

import aiohttp

from .conftest import WEBUI_HEADERS, _read_inbox, _send_mail


async def test_module_prototype_member_replies_through_real_mail_store(
    inproc_server: tuple[str, Path],
) -> None:
    base_url, home = inproc_server
    token = uuid.uuid4().hex
    behavior = "{:version 2 :handlers {:mail-delivered work.reply/handle} :fuel 1000}"
    modules = {
        "work/reply.tlon": """
        (ns work.reply (:require [work.text :as text]) (:export handle))
        (defn handle [event]
          (let [content (text/reply (get (get event :mail) :content))]
            (mail-send {:to [(get event :sender)] :content content})
            {:result content}))
        """,
        "work/text.tlon": """
        (ns work.text (:export reply))
        (defn reply [content] (str "module-reply:" content))
        """,
    }
    async with aiohttp.ClientSession(headers=WEBUI_HEADERS) as http:
        async with http.post(
            f"{base_url}/admin/prototypes",
            json={"display_name": f"模块回信-{token}", "behavior": behavior, "modules": modules},
        ) as response:
            body = await response.json()
            assert response.status == 201, body
            prototype = body["prototype"]["name"]
        async with http.get(f"{base_url}/admin/prototypes/{prototype}") as response:
            assert response.status == 200
            assert (await response.json())["prototype"]["modules"] == modules
        async with http.post(
            f"{base_url}/admin/config/agents",
            json={
                "prototype": prototype,
                "name": f"程序员-{token[:8]}",
                "model": "openai/gpt-test",
            },
        ) as response:
            body = await response.json()
            assert response.status == 200, body
            agent = body["agent_id"]
        config = home / agent / "config"
        for relative, source in modules.items():
            assert (config / "modules" / relative).read_text() == source
        await _send_mail(http, base_url, token, to=[agent])
        async with asyncio.timeout(10):
            while not any(
                mail["content"] == f"module-reply:{token}"
                and mail["from_address"] == f"{agent}@tlon.internal"
                for mail in _read_inbox(home, "human:alice")
            ):
                await asyncio.sleep(0.02)
        replies = [
            mail
            for mail in _read_inbox(home, "human:alice")
            if mail["content"] == f"module-reply:{token}"
        ]
        assert len(replies) == 1


async def test_http_rejects_module_entry_without_sources(inproc_server: tuple[str, Path]) -> None:
    base_url, _ = inproc_server
    async with (
        aiohttp.ClientSession(headers=WEBUI_HEADERS) as http,
        http.post(
            f"{base_url}/admin/prototypes",
            json={
                "display_name": "缺失模块",
                "behavior": "{:version 2 :handlers {:mail-delivered work.reply/handle}}",
            },
        ) as response,
    ):
        assert response.status == 400
        assert (await response.json())["reason"] == "invalid_prototype"
