"""Real HTTP → mailbox → session request → scheduling → mailbox integration."""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import aiohttp

from tests.prototype_helpers import builtin_id
from tlon.agents.scheduling_store import SchedulingStore
from tlon.base.llm_client import LLMClient, LLMResponse, ToolCall

from .conftest import WEBUI_HEADERS, _send_mail
from .test_post_office import _server


async def test_real_tools_sleep_wake_finish_and_permanent_exit(tmp_path: Path) -> None:
    """Only inference is mocked; delivery, tools, journal and timers are real."""
    second_think = asyncio.Event()
    allow_sleep = asyncio.Event()
    woke = asyncio.Event()
    member_id = ""
    calls = 0
    store: Any = None

    def response(name: str, arguments: dict[str, Any]) -> LLMResponse:
        return LLMResponse(
            content=None,
            tool_calls=[
                ToolCall(id=f"tool-{calls}", function_name=name, arguments=arguments),
            ],
        )

    async def infer(**kwargs: Any) -> LLMResponse:
        nonlocal calls
        if kwargs.get("purpose", "think") != "think":
            return LLMResponse(content="[]")
        calls += 1
        if calls == 1:
            return response("mail-send", {"to": ["alice@tlon.internal"], "content": "收到"})
        if calls == 2:
            second_think.set()
            await allow_sleep.wait()
            unread = store.mails(member_id, "unread")
            return response("mail-mark-read", {"mail_ids": [mail.id for mail in unread]})
        if calls == 3:
            return response(
                "sleepUntil",
                {
                    "wake_at": (datetime.now(UTC) + timedelta(seconds=2)).isoformat(),
                    "reason": "稍后继续",
                },
            )
        if calls == 4:
            woke.set()
            unread = store.mails(member_id, "unread")
            return response("mail-mark-read", {"mail_ids": [mail.id for mail in unread]})
        if calls == 5:
            return response("finishTurn", {})
        if calls == 6:
            return response("exitAgent", {"reason": "测试完成"})
        raise AssertionError(f"Unexpected inference after call {calls}: {kwargs}")

    async with _server(tmp_path) as (url, app):
        assert app._runtime is not None
        runtime = app._runtime
        store = runtime.post_office.store
        registry = runtime.agent_registry
        prototype = builtin_id("短回合交流")
        behavior = tmp_path / "prototypes" / prototype / "behavior.tlon"
        behavior.write_text(
            behavior.read_text()
            .replace(":max-iterations 3", ":max-iterations 10")
            .replace(":enabled true", ":enabled false"),
            encoding="utf-8",
        )
        async with aiohttp.ClientSession(headers=WEBUI_HEADERS) as http:
            async with http.post(
                f"{url}/admin/config/agents",
                json={
                    "prototype": prototype,
                    "name": "四态集成测试员",
                    "model": "openai/gpt-test",
                },
            ) as created:
                assert created.status == 200
                member_id = (await created.json())["agent_id"]

            async def wait_state(state: str) -> dict[str, Any]:
                async with asyncio.timeout(10):
                    while True:
                        status = await registry.get_agent_status(member_id)
                        assert status is not None
                        if status["state"] == state:
                            return status
                        await asyncio.sleep(0.01)

            with patch.object(LLMClient, "chat", new=AsyncMock(side_effect=infer)):
                await _send_mail(http, url, "先回复，再休息", to=[member_id])
                async with asyncio.timeout(10):
                    await second_think.wait()
                assert calls == 2
                assert (await registry.get_agent_status(member_id))["state"] == "active"
                assert [mail.content for mail in store.mails("human:alice")] == ["收到"]
                assert len(store.mails(member_id, "unread")) == 1
                allow_sleep.set()
                sleeping = await wait_state("sleeping")
                assert sleeping["wake_at"] is not None
                durable = SchedulingStore(store.database).load()[member_id]
                assert durable[:2] == ("sleep", sleeping["wake_at"])
                assert not store.mails(member_id, "unread")
                await _send_mail(http, url, "睡眠期间来信", to=[member_id])
                assert len(store.mails(member_id, "unread")) == 1
                assert (await registry.get_agent_status(member_id))["state"] == "sleeping"
                assert calls == 3
                async with asyncio.timeout(10):
                    await woke.wait()
                assert datetime.now(UTC) >= datetime.fromisoformat(sleeping["wake_at"])
                await wait_state("waiting")
                assert calls == 5
                assert not store.mails(member_id, "unread")
                assert len(store.mails("human:alice")) == 1  # finishTurn sent no mail
                await _send_mail(http, url, "现在永久退出", to=[member_id])
                await wait_state("dead")
                assert calls == 6
                history = [mail.id for mail in store.mails(member_id)]
                assert len(history) == 3
                async with http.post(
                    f"{url}/messages",
                    json={
                        "request_id": "after-death",
                        "content": "不应投递",
                        "to": [member_id],
                    },
                ) as refused:
                    assert refused.status == 400
                assert [mail.id for mail in store.mails(member_id)] == history
                assert calls == 6
