"""Fault and replay contracts for the transactional post office."""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from tlon.base.config import AgentConfig, AgentInfoProvider
from tlon.base.events.event import EventType
from tlon.base.events.event_bus import EventBus
from tlon.base.paths import TlonPaths
from tlon.organization.mail.mailbox import Mailbox
from tlon.organization.mail.post_office import PostOffice, PostRejected
from tlon.organization.mail.resolver import RecipientResolver
from tlon.organization.mail.store import IdempotencyConflict


class Provider(AgentInfoProvider):
    def get_config(self, agent_id: str) -> AgentConfig | None:
        return AgentConfig(id=agent_id, name=agent_id.upper()) if agent_id in {"a", "b"} else None

    def get_all_ids(self) -> set[str]:
        return {"a", "b"}


def office_at(tmp_path: Path) -> PostOffice:
    paths = TlonPaths(home=tmp_path)
    resolver = RecipientResolver(
        Provider(), paths, lambda a: "gateway:g" if a.endswith("@g.com") else None
    )
    bus = EventBus()
    bus.publish = AsyncMock()
    return PostOffice(bus, resolver, paths)


async def test_reject_all_invalid_and_retired_without_any_writes(tmp_path: Path) -> None:
    office = office_at(tmp_path)
    office.store.retire("b")
    with pytest.raises(PostRejected) as caught:
        await office.send(
            from_address="a@tlon.internal",
            to=["a", "missing"],
            cc=["b@tlon.internal", "no@invalid.com"],
            content="test",
        )
    assert caught.value.invalid_recipients == ["missing", "b@tlon.internal", "no@invalid.com"]
    assert await Mailbox(office._paths.mail_database_file(), "a").inbox() == []


async def test_replay_survives_retirement_and_restart(tmp_path: Path) -> None:
    office = office_at(tmp_path)
    kwargs = {
        "from_address": "a@tlon.internal",
        "to": ["B"],
        "content": "test",
        "request_id": "stable",
    }
    first = await office.send(**kwargs)
    office.store.retire("b")
    restarted = office_at(tmp_path)
    replay = await restarted.send(**kwargs)
    assert replay.mail.id == first.mail.id
    restarted._bus.publish.assert_not_awaited()
    with pytest.raises(IdempotencyConflict):
        await restarted.send(**(kwargs | {"content": "changed"}))


async def test_notification_failure_does_not_lose_mail(tmp_path: Path) -> None:
    office = office_at(tmp_path)
    office._bus.publish.side_effect = RuntimeError("notification unavailable")
    receipt = await office.send(from_address="a@tlon.internal", to=["b"], content="test")
    inbox = await Mailbox(office._paths.mail_database_file(), "b").inbox()
    assert [m.id for m in inbox] == [receipt.mail.id]


async def test_gateway_failure_preserves_local_success_and_never_retries(
    tmp_path: Path, monkeypatch
) -> None:
    office = office_at(tmp_path)
    append = AsyncMock(side_effect=OSError("disk full"))
    monkeypatch.setattr(Mailbox, "append_inbox", append)
    kwargs = {
        "from_address": "a@tlon.internal",
        "to": ["b", "n@g.com"],
        "content": "test",
        "request_id": "g",
    }
    receipt = await office.send(**kwargs)
    assert receipt.gateways[0]["status"] == "failed"
    await office.send(**kwargs)
    assert append.await_count == 1
    assert len(await Mailbox(office._paths.mail_database_file(), "b").inbox()) == 1


async def test_gateway_aliases_attempt_owner_once(tmp_path: Path) -> None:
    office = office_at(tmp_path)
    receipt = await office.send(
        from_address="a@tlon.internal", to=["n@g.com", "m@g.com"], content="test"
    )
    assert all(g["status"] == "delivered" for g in receipt.gateways)
    deliveries = [
        call.args[0]
        for call in office._bus.publish.await_args_list
        if call.args[0].type == EventType.MAIL_DELIVERED
    ]
    assert len(deliveries) == 1


async def test_concurrent_retry_has_one_inbox_and_notification(tmp_path: Path) -> None:
    import asyncio

    office = office_at(tmp_path)

    async def send() -> str:
        receipt = await office.send(
            from_address="a@tlon.internal", to=["b"], content="test", request_id="concurrent"
        )
        return receipt.mail.id

    ids = await asyncio.gather(*(send() for _ in range(8)))
    assert len(set(ids)) == 1
    assert len(await Mailbox(office._paths.mail_database_file(), "b").inbox()) == 1
    assert office._bus.publish.await_count == 2


async def test_gateway_status_failure_returns_unknown_without_retry(
    tmp_path: Path, monkeypatch
) -> None:
    office = office_at(tmp_path)

    def fail_status(*args: object) -> None:
        raise OSError("status write failed")

    monkeypatch.setattr(office.store, "update_gateway", fail_status)
    receipt = await office.send(
        from_address="a@tlon.internal", to=["b", "n@g.com"], content="test", request_id="unknown"
    )
    assert receipt.gateways[0]["status"] == "unknown"
    replay = await office.send(
        from_address="a@tlon.internal", to=["b", "n@g.com"], content="test", request_id="unknown"
    )
    assert replay.gateways[0]["status"] == "unknown"
    deliveries = [
        call.args[0]
        for call in office._bus.publish.await_args_list
        if call.args[0].type == EventType.MAIL_DELIVERED
    ]
    assert len(deliveries) == 2


async def test_retirement_during_accept_becomes_structured_rejection(
    tmp_path: Path, monkeypatch
) -> None:
    from tlon.organization.mail.store import RetiredRecipients

    office = office_at(tmp_path)

    def reject(*args: object) -> None:
        raise RetiredRecipients(["b"])

    monkeypatch.setattr(office.store, "accept", reject)
    with pytest.raises(PostRejected) as caught:
        await office.send(from_address="a@tlon.internal", to=["b"], content="test")
    assert caught.value.invalid_recipients == ["b@tlon.internal"]
    assert caught.value.reason == "invalid_recipients"


async def test_notifications_continue_current_trace(tmp_path: Path) -> None:
    from tlon.base.tracing.trace_context import continue_trace

    office = office_at(tmp_path)
    trace_id = "12345678901234567890123456789012"
    span_id = "1234567890123456"
    with continue_trace(trace_id, span_id):
        await office.send(from_address="a@tlon.internal", to=["b"], content="traced")
    events = [call.args[0] for call in office._bus.publish.await_args_list]
    assert all(event.trace_id == trace_id for event in events)
    assert events[0].source == "a"


async def test_new_request_id_is_new_intent_even_with_identical_content(tmp_path: Path) -> None:
    office = office_at(tmp_path)
    first = await office.send(
        from_address="a@tlon.internal", to=["b"], content="same", request_id="one"
    )
    second = await office.send(
        from_address="a@tlon.internal", to=["b"], content="same", request_id="two"
    )
    assert first.mail.id != second.mail.id
    assert len(await Mailbox(office._paths.mail_database_file(), "b").inbox()) == 2


async def test_send_resolves_each_alias_once(tmp_path: Path, monkeypatch) -> None:
    from unittest.mock import Mock

    office = office_at(tmp_path)
    resolve = Mock(wraps=office._resolver.resolve_address)
    monkeypatch.setattr(office._resolver, "resolve_address", resolve)
    await office.send(from_address="a@tlon.internal", to=["B", "B"], cc=["a"], content="once")
    assert [call.args[0] for call in resolve.call_args_list] == ["B", "a"]


def test_post_office_constructor_has_no_filesystem_io(tmp_path: Path, monkeypatch) -> None:
    from tlon.organization.mail.store import MailStore

    def unexpected_initialize(self) -> None:
        raise AssertionError("constructor must not initialize SQLite")

    monkeypatch.setattr(MailStore, "initialize", unexpected_initialize)
    office = office_at(tmp_path)
    assert not office._paths.mail_database_file().exists()


async def test_recipient_lookup_uses_one_worker_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading

    from tlon.organization.mail.store import MailStore

    office = office_at(tmp_path)
    office.store.register_member("human:bob")
    office.store.register_member("human:carol")
    loop_thread = threading.get_ident()
    snapshots = 0
    known_humans = MailStore.known_human_ids
    member_exists = MailStore.member_exists

    def checked_snapshot(store: MailStore) -> list[str]:
        nonlocal snapshots
        assert threading.get_ident() != loop_thread
        snapshots += 1
        return known_humans(store)

    def checked_exists(store: MailStore, member_id: str) -> bool:
        assert threading.get_ident() != loop_thread
        return member_exists(store, member_id)

    monkeypatch.setattr(MailStore, "known_human_ids", checked_snapshot)
    monkeypatch.setattr(MailStore, "member_exists", checked_exists)
    await office.send(
        from_address="bob@tlon.internal", to=["bob", "carol"], cc=["bob"], content="batch"
    )
    assert snapshots == 1
    assert len(office.store.mails("human:carol")) == 1
