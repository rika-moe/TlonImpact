"""Administrative state controls drive real HTTP, runtime, mailbox and timers."""

import asyncio
import sqlite3
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


async def test_admin_sleep_wake_drain_wait_and_terminal_death(tmp_path: Path) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = 0
    member_id = ""
    store: Any = None

    async def infer(**kwargs: Any) -> LLMResponse:
        nonlocal calls
        if kwargs.get("purpose", "think") != "think":
            return LLMResponse(content="[]")
        calls += 1
        unread = store.mails(member_id, "unread")
        if unread:
            entered.set()
            await release.wait()
            name, args = "mail-mark-read", {"mail_ids": [mail.id for mail in unread]}
        else:
            name, args = "finishTurn", {}
        return LLMResponse(
            content=None,
            tool_calls=[ToolCall(id=f"admin-control-{calls}", function_name=name, arguments=args)],
        )

    async with _server(tmp_path) as (url, app):
        assert app._runtime is not None
        registry = app._runtime.agent_registry
        store = app._runtime.post_office.store
        prototype = builtin_id("短回合交流")
        behavior = tmp_path / "prototypes" / prototype / "behavior.tlon"
        behavior.write_text(behavior.read_text().replace(":enabled true", ":enabled false"))
        async with aiohttp.ClientSession(headers=WEBUI_HEADERS) as http:
            async with http.post(
                f"{url}/admin/config/agents",
                json={"prototype": prototype, "name": "管理状态测试员", "model": "openai/gpt-test"},
            ) as created:
                assert created.status == 200
                member_id = (await created.json())["agent_id"]
            base = f"{url}/admin/agents/{member_id}"

            async def action(name: str, body: Any = None, expected: int = 200) -> dict[str, Any]:
                async with http.post(f"{base}/{name}", json=body) as response:
                    data = await response.json()
                    assert response.status == expected, data
                    return data

            async def state(expected: str) -> dict[str, Any]:
                async with asyncio.timeout(10):
                    while True:
                        snapshot = await registry.get_agent_status(member_id)
                        assert snapshot is not None
                        if snapshot["state"] == expected:
                            return snapshot
                        await asyncio.sleep(0.01)

            async def boot_idle() -> None:
                # Assembly returns before the worker commits its empty boot rule.
                database = tmp_path / member_id / "program.sqlite3"
                async with asyncio.timeout(10):
                    while True:
                        snapshot = await registry.get_agent_status(member_id)
                        assert snapshot is not None
                        if database.exists():
                            with sqlite3.connect(database) as connection:
                                initialized = connection.execute(
                                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='runs'"
                                ).fetchone()
                                runs = (
                                    connection.execute(
                                        "SELECT rule_edn,completed,error FROM runs"
                                    ).fetchall()
                                    if initialized
                                    else []
                                )
                            boot_finished = any(
                                ':name "boot"' in source and completed
                                for source, completed, _ in runs
                            )
                            settled = all(
                                completed or error is not None for _, completed, error in runs
                            )
                            if (
                                snapshot["state"] == "waiting"
                                and not snapshot["waking"]
                                and boot_finished
                                and settled
                            ):
                                return
                        await asyncio.sleep(0.01)

            def deadline(seconds: float = 3600) -> str:
                return (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat()

            with patch.object(LLMClient, "chat", new=AsyncMock(side_effect=infer)):
                # Empty-mailbox wake must not manufacture an active task.
                await boot_idle()
                await action("sleep", {"wake_at": deadline()})
                assert (await state("sleeping"))["wake_at"] is not None
                await action("wake")
                assert (await state("waiting"))["wake_at"] is None
                assert calls == 0
                assert SchedulingStore(store.database).load() == {}

                # A replaced timer must not wake the new sleep early.
                await action("sleep", {"wake_at": deadline(0.15)})
                later = deadline()
                await action("sleep", {"wake_at": later})
                await asyncio.sleep(0.2)
                assert (await state("sleeping"))["wake_at"] == later
                await _send_mail(http, url, "睡眠中排队的信", to=[member_id])
                assert calls == 0
                assert len(store.mails(member_id, "unread")) == 1
                await action("wake")
                async with asyncio.timeout(10):
                    await entered.wait()
                assert (await state("active"))["wake_at"] is None
                pending = await action("wait", expected=202)
                assert pending["agent"]["state"] == "active"
                assert pending["agent"]["scheduling_pending"] == "wait"
                assert not release.is_set()  # The admin action did not cancel inference.
                release.set()
                await state("waiting")
                assert calls == 2
                assert not store.mails(member_id, "unread")

                # Active -> sleep also drains work, and can later be woken early.
                entered.clear()
                release.clear()
                await _send_mail(http, url, "完成本轮信后睡眠", to=[member_id])
                async with asyncio.timeout(10):
                    await entered.wait()
                pending = await action("sleep", {"wake_at": deadline()}, expected=202)
                assert pending["agent"]["state"] == "active"
                assert pending["agent"]["scheduling_pending"] == "sleep"
                release.set()
                await state("sleeping")
                assert calls == 4
                await action("wake")
                await state("waiting")
                assert calls == 4
                assert store.mails("human:alice") == []  # Controls never send member mail.

                for body in (
                    {},
                    [],
                    {"wake_at": 42},
                    {"wake_at": "bad"},
                    {"wake_at": "2000-01-01T00:00:00Z"},
                    {"wake_at": "2030-01-01T00:00:00"},
                    {"wake_at": deadline(), "extra": True},
                ):
                    await action("sleep", body, expected=400)
                async with http.post(f"{base}/sleep", data="not-json") as invalid:
                    assert invalid.status == 400
                for name in ("wake", "wait", "sleep"):
                    async with http.post(f"{url}/admin/agents/missing/{name}") as missing:
                        assert missing.status == 404

                history = [mail.id for mail in store.mails(member_id)]
                await action("terminate")
                await state("dead")
                for name in ("wake", "wait"):
                    await action(name, expected=409)
                await action("sleep", {"wake_at": deadline()}, expected=409)
                assert [mail.id for mail in store.mails(member_id)] == history
                assert calls == 4
                async with http.post(
                    f"{url}/messages",
                    json={"to": [member_id], "content": "死亡后拒收", "request_id": "dead-admin"},
                ) as refused:
                    assert refused.status == 400
