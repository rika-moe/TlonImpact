"""Account synchronization follows durable membership, not activation state."""

import asyncio
from pathlib import Path
from unittest.mock import ANY, MagicMock

import pytest

from tlon.api.gitea_members import GiteaMembersCoordinator
from tlon.base.paths import TlonPaths
from tlon.gitea.members import MemberCredentials
from tlon.gitea.storage import GiteaError
from tlon.organization.mail.store import MailStore


def make_coordinator(
    tmp_path: Path, names: dict[str, str]
) -> tuple[GiteaMembersCoordinator, MagicMock, MailStore]:
    paths = TlonPaths(tmp_path)
    paths.gitea_dir().mkdir(parents=True)
    store = MailStore(paths.mail_database_file())
    store.initialize()
    manager = MagicMock()
    manager.reconcile.return_value = {}
    manager.credentials.return_value = MemberCredentials(
        "bound-user", "secret-token", "m@tlon.local"
    )
    service = MagicMock()
    service.status.return_value = {"state": "ready"}
    service.member_manager.return_value = manager, "tlon-gitea-instance-network"
    coordinator = GiteaMembersCoordinator(paths, service, lambda: names, retry_seconds=0.01)
    return coordinator, manager, store


@pytest.mark.asyncio
async def test_sleeping_present_members_keep_identity_and_retired_are_removed(
    tmp_path: Path,
) -> None:
    names = {"sleeping": "Display Name", "retired": "Former", "successor": "New"}
    coordinator, manager, store = make_coordinator(tmp_path, names)
    store.retire("retired")
    await coordinator.reconcile()
    manager.reconcile.assert_called_with(
        {"sleeping": "Display Name", "successor": "New"}, control=ANY
    )
    names["sleeping"] = "Renamed"
    access = await coordinator.execution_access("sleeping")
    manager.credentials.assert_called_with("sleeping")
    assert access.username == "bound-user"
    assert access.url == "http://tlon-gitea-instance:3000"
    assert "secret-token" not in str(coordinator.status())
    store.retire("sleeping")
    with pytest.raises(GiteaError):
        await coordinator.execution_access("sleeping")
    await coordinator.reconcile()
    manager.reconcile.assert_called_with({"successor": "New"}, control=ANY)


@pytest.mark.asyncio
async def test_removed_member_cannot_use_cached_credentials(tmp_path: Path) -> None:
    names = {"member": "Name"}
    coordinator, manager, _ = make_coordinator(tmp_path, names)
    await coordinator.execution_access("member")
    names.clear()
    with pytest.raises(GiteaError):
        await coordinator.execution_access("member")
    await coordinator.reconcile()
    manager.reconcile.assert_called_with({}, control=ANY)
    assert manager.credentials.call_count == 1


@pytest.mark.asyncio
async def test_failure_is_visible_redacted_and_periodically_retried(tmp_path: Path) -> None:
    coordinator, manager, _ = make_coordinator(tmp_path, {"member": "Name"})
    manager.reconcile.side_effect = RuntimeError("password-secret")
    await coordinator.reconcile()
    assert coordinator.status()["state"] == "unavailable"
    assert "password-secret" not in str(coordinator.status())
    manager.reconcile.side_effect = None
    coordinator.start()
    for _ in range(100):
        if coordinator.status()["state"] == "ready":
            break
        await asyncio.sleep(0.01)
    assert coordinator.status()["state"] == "ready"
    await coordinator.shutdown()
    calls = manager.reconcile.call_count
    await asyncio.sleep(0.03)
    assert manager.reconcile.call_count == calls
    with pytest.raises(GiteaError):
        await coordinator.execution_access("member")


@pytest.mark.asyncio
async def test_one_member_failure_keeps_other_access_and_reports_error(tmp_path: Path) -> None:
    coordinator, manager, _ = make_coordinator(tmp_path, {"good": "Good", "bad": "Bad"})
    manager.reconcile.return_value = {"bad": "Gitea member synchronization failed"}
    await coordinator.reconcile()
    await coordinator.execution_access("good")
    assert coordinator.status() == {
        "state": "degraded",
        "errors": {"bad": "Gitea member synchronization failed"},
    }


@pytest.mark.asyncio
async def test_each_execution_checks_retirement_without_reconciling_every_member(
    tmp_path: Path,
) -> None:
    coordinator, manager, store = make_coordinator(tmp_path, {"member": "Name"})
    await coordinator.reconcile()
    await coordinator.execution_access("member")
    await coordinator.execution_access("member")
    assert manager.reconcile.call_count == 1
    store.retire("member")
    with pytest.raises(GiteaError, match="absent or retired"):
        await coordinator.execution_access("member")
    assert manager.credentials.call_count == 2
    assert coordinator.status()["state"] == "ready"


@pytest.mark.asyncio
async def test_new_member_is_provisioned_when_background_poll_observes_it(tmp_path: Path) -> None:
    names: dict[str, str] = {}
    coordinator, manager, _ = make_coordinator(tmp_path, names)
    await coordinator.reconcile()
    names["new"] = "New Member"
    coordinator.start()
    try:
        for _ in range(100):
            if manager.reconcile.call_args.args[0] == names:
                break
            await asyncio.sleep(0.01)
        manager.reconcile.assert_called_with({"new": "New Member"}, control=ANY)
    finally:
        await coordinator.shutdown()


@pytest.mark.asyncio
async def test_shutdown_stops_after_inflight_http_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading

    from tlon.gitea.members import MemberManager

    coordinator, _, _ = make_coordinator(tmp_path, {f"member-{i}": "Name" for i in range(30)})
    manager = MemberManager(TlonPaths(tmp_path), "http://localhost/", "host-secret")
    coordinator._manager = manager
    entered = threading.Event()
    release = threading.Event()
    calls: list[str] = []

    def blocking_request(url: str, path: str, password: str, **kwargs: object) -> tuple[int, None]:
        calls.append(path)
        entered.set()
        assert release.wait(timeout=2)
        return 404, None

    monkeypatch.setattr("tlon.gitea.members.request", blocking_request)
    coordinator.start()
    assert await asyncio.to_thread(entered.wait, 1)
    shutdown = asyncio.create_task(coordinator.shutdown())
    await asyncio.sleep(0)
    release.set()
    await asyncio.wait_for(shutdown, timeout=1)
    assert len(calls) == 1
    assert coordinator.status()["state"] == "unavailable"
    with pytest.raises(GiteaError):
        manager.credentials("member-0")
