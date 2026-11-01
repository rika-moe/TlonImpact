"""Signed ingress, durable plans, and ordinary idempotent mail delivery."""

import asyncio
import hashlib
import hmac
import json
import threading
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock, patch

import pytest

from tlon.api.gitea_webhooks import GiteaWebhookCoordinator, GiteaWebhookError
from tlon.base.paths import TlonPaths
from tlon.gitea.members import member_username
from tlon.gitea.service import GiteaService
from tlon.gitea.webhook_remote import WebhookRepositoryError
from tlon.organization.mail.mail import Mail, address_for_member
from tlon.organization.mail.post_office import PostOffice

SECRET = "private-webhook-secret"


class IdempotentPostOffice:
    """Models PostOffice's durable request ID contract, with no agent execution API."""

    def __init__(self) -> None:
        self.store = MagicMock()
        self.retired: set[str] = set()
        self.store.is_retired.side_effect = lambda member: member in self.retired
        self.mails: dict[str, Mail] = {}
        self.calls = 0
        self.delivered = asyncio.Event()

    def list_members(self) -> list[dict[str, str]]:
        return [{"address": address_for_member(name)} for name in ("reader", "actor", "retired")]

    async def post(self, mail: Mail, *, request_id: str) -> None:
        self.calls += 1
        self.mails.setdefault(request_id, mail)
        self.delivered.set()


def setup(tmp_path: Path) -> tuple[GiteaWebhookCoordinator, IdempotentPostOffice, MagicMock]:
    paths = TlonPaths(tmp_path)
    folder = paths.gitea_dir() / "members"
    folder.mkdir(parents=True)
    for name in ("reader", "actor", "retired"):
        username = member_username(name)
        path = folder / f"{username}.json"
        path.write_text(json.dumps({"member_id": name, "username": username, "state": "active"}))
        path.chmod(0o600)
    post = IdempotentPostOffice()
    post.retired.add("retired")
    remote = MagicMock()
    remote.history.return_value = [member_username("retired"), "unknown"]
    remote.sync.return_value = []
    service = MagicMock(spec=GiteaService)
    service.status.return_value = {"url": "http://127.0.0.1:3000/"}
    service.prepare_webhooks.return_value = {
        "instance_id": "instance",
        "secret": SECRET,
        "callback_url": "http://host/gitea/instance",
    }
    service.webhook_remote.return_value = remote
    coordinator = GiteaWebhookCoordinator(
        paths,
        service,
        lambda: {name: name for name in ("reader", "actor", "retired")},
        cast(PostOffice, post),
        retry_seconds=0.01,
    )
    coordinator._transport = service.prepare_webhooks.return_value
    coordinator._remote = remote
    coordinator._store.initialize()
    return coordinator, post, remote


def raw_body() -> bytes:
    return json.dumps(
        {
            "action": "created",
            "repository": {"id": 1, "full_name": "tlon/project"},
            "sender": {"login": member_username("actor")},
            "issue": {
                "id": 2,
                "number": 3,
                "title": "Discuss",
                "user": {"login": member_username("reader")},
            },
            "comment": {
                "id": 4,
                "body": f"@{member_username('actor')} @unknown",
                "updated_at": "2026-11-01T00:00:00Z",
            },
        }
    ).encode()


def signature(body: bytes) -> str:
    return hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


async def receive(
    coordinator: GiteaWebhookCoordinator, delivery: str = "delivery"
) -> dict[str, object]:
    body = raw_body()
    return await coordinator.receive("instance", "issue_comment", delivery, signature(body), body)


@pytest.mark.parametrize(
    "instance,sig,status",
    [("wrong", "valid", 404), ("instance", "", 401), ("instance", "0" * 64, 401)],
)
async def test_signature_and_instance_rejected_before_remote(
    tmp_path: Path, instance: str, sig: str, status: int
) -> None:
    coordinator, _, remote = setup(tmp_path)
    body = raw_body()
    with pytest.raises(GiteaWebhookError) as raised:
        await coordinator.receive(
            instance, "issue_comment", "delivery", signature(body) if sig == "valid" else sig, body
        )
    assert raised.value.status == status
    remote.validate.assert_not_called()


async def test_raw_hmac_and_json_validation(tmp_path: Path) -> None:
    coordinator, _, remote = setup(tmp_path)
    body = raw_body()
    with pytest.raises(GiteaWebhookError) as raised:
        await coordinator.receive(
            "instance", "issue_comment", "delivery", signature(body), body + b" "
        )
    assert raised.value.status == 401
    for invalid in (b"\xff", b"[]", b"not-json"):
        with pytest.raises(GiteaWebhookError) as raised:
            await coordinator.receive(
                "instance", "issue_comment", "delivery", signature(invalid), invalid
            )
        assert raised.value.status == 400
    remote.validate.assert_not_called()


async def test_recipients_and_both_redelivery_ids_are_idempotent(tmp_path: Path) -> None:
    coordinator, post, _ = setup(tmp_path)
    assert await receive(coordinator) == {"accepted": True, "duplicate": False}
    assert (await receive(coordinator))["duplicate"] is True
    assert (await receive(coordinator, "new-delivery"))["duplicate"] is True
    assert len(coordinator._store.pending()) == 1
    await coordinator._deliver()
    assert len(post.mails) == 1
    mail = next(iter(post.mails.values()))
    assert mail.to == [address_for_member("reader")]
    assert "http://127.0.0.1:3000/tlon/project/issues/3" in mail.content
    assert coordinator._store.status()["deliveries"] == 2


async def test_restart_restores_accepted_queue(tmp_path: Path) -> None:
    first, post, _ = setup(tmp_path)
    await receive(first)
    second = GiteaWebhookCoordinator(
        first._paths, first._service, first._members, cast(PostOffice, post)
    )
    second._store.initialize()
    await second._deliver()
    assert len(post.mails) == 1
    assert second._store.status()["pending"] == 0


async def test_post_success_before_completion_failure_retries_same_mail(tmp_path: Path) -> None:
    first, post, _ = setup(tmp_path)
    await receive(first)
    with patch.object(
        first._store, "complete", side_effect=OSError("disk temporarily unavailable")
    ):
        await first._deliver()
    assert len(post.mails) == 1
    assert first._store.status()["pending"] == 1
    second = GiteaWebhookCoordinator(
        first._paths, first._service, first._members, cast(PostOffice, post)
    )
    await second._deliver()
    assert post.calls == 2
    assert len(post.mails) == 1
    assert second._store.status()["pending"] == 0


async def test_retired_after_acceptance_is_not_delivered(tmp_path: Path) -> None:
    coordinator, post, _ = setup(tmp_path)
    await receive(coordinator)
    post.retired.add("reader")
    await coordinator._deliver()
    assert not post.mails
    assert coordinator._store.status()["pending"] == 0


async def test_offline_gitea_still_delivers_accepted_mail(tmp_path: Path) -> None:
    coordinator, post, remote = setup(tmp_path)
    await receive(coordinator)
    remote.sync.side_effect = RuntimeError("secret-token-do-not-leak")
    await coordinator.start(3001)
    try:
        await asyncio.wait_for(post.delivered.wait(), timeout=3)
        assert len(post.mails) == 1
        assert "secret-token" not in str(coordinator.status())
    finally:
        await coordinator.shutdown()


async def test_unknown_remote_error_is_safe(tmp_path: Path) -> None:
    coordinator, _, remote = setup(tmp_path)
    remote.validate.side_effect = RuntimeError("secret-token-do-not-leak")
    with pytest.raises(GiteaWebhookError) as raised:
        await receive(coordinator)
    assert raised.value.status == 503
    assert "secret-token" not in str(raised.value)
    assert coordinator._store.status()["accepted"] == 0


async def test_cancellation_owns_worker_until_completion(tmp_path: Path) -> None:
    coordinator, _, _ = setup(tmp_path)
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def work() -> None:
        entered.set()
        release.wait(timeout=3)
        finished.set()

    task = asyncio.create_task(coordinator._blocking(work))
    assert await asyncio.to_thread(entered.wait, 2)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished.is_set()


async def test_wrong_repository_rejected_without_queueing(tmp_path: Path) -> None:
    coordinator, post, remote = setup(tmp_path)
    remote.validate.side_effect = WebhookRepositoryError("Gitea webhook repository is not managed")
    with pytest.raises(GiteaWebhookError) as raised:
        await receive(coordinator)
    assert raised.value.status == 403
    assert coordinator._store.status()["accepted"] == 0
    assert not post.mails


async def test_startup_cancel_signals_worker_before_waiting(tmp_path: Path) -> None:
    from tlon.gitea.control import StartupControl

    coordinator, _, _ = setup(tmp_path)
    entered = threading.Event()

    def prepare(port: int, control: StartupControl) -> dict[str, str]:
        entered.set()
        assert control.stop.wait(2)
        control.check()
        return {}

    with patch.object(coordinator._service, "prepare_webhooks", side_effect=prepare):
        task = asyncio.create_task(coordinator.start(3001))
        assert await asyncio.to_thread(entered.wait, 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)
    assert coordinator._worker_stop.is_set()
    assert not coordinator._lock.locked()


async def test_restart_recovers_accepted_mail_without_gitea_or_listener(tmp_path: Path) -> None:
    coordinator, post, remote = setup(tmp_path)
    await receive(coordinator)
    coordinator._transport = None
    coordinator._remote = None
    await coordinator.recover()
    try:
        await asyncio.wait_for(post.delivered.wait(), 1)
        assert len(post.mails) == 1
        remote.sync.assert_not_called()
    finally:
        await coordinator.shutdown()
