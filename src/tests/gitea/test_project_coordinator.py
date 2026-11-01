"""Host governance resolves stable identities and owns its blocking worker lifetime."""

import asyncio
import threading
from pathlib import Path
from unittest.mock import MagicMock, call

import pytest

from tlon.api.gitea_projects import GiteaProjectError, GiteaProjectsCoordinator
from tlon.base.paths import TlonPaths
from tlon.gitea.members import member_username
from tlon.gitea.storage import GiteaError
from tlon.organization.mail.store import MailStore


def coordinator(
    tmp_path: Path, names: dict[str, str] | None = None
) -> tuple[GiteaProjectsCoordinator, MagicMock, MagicMock, MailStore]:
    paths = TlonPaths(tmp_path)
    paths.gitea_dir().mkdir(parents=True)
    store = MailStore(paths.mail_database_file())
    store.initialize()
    manager = MagicMock()
    manager.list_projects.return_value = []
    manager.configure.return_value = {"name": "example", "managed": True}
    manager.set_reviewer.return_value = {"reviewers": []}
    service = MagicMock()
    service.status.return_value = {"state": "ready"}
    service.project_manager.return_value = manager
    return GiteaProjectsCoordinator(paths, service, lambda: names or {}), manager, service, store


@pytest.mark.parametrize(
    "body",
    [
        None,
        [],
        {},
        {"name": "repo"},
        {"name": "repo", "create": 1, "default_branch": "main", "protected_branches": []},
        {"name": "repo", "create": True, "default_branch": "main", "protected_branches": "main"},
        {"name": "repo", "create": True, "default_branch": "main", "protected_branches": [1]},
        {
            "name": "repo",
            "create": True,
            "default_branch": "main",
            "protected_branches": ["x"] * 33,
        },
        {
            "name": "repo",
            "create": True,
            "default_branch": "main",
            "protected_branches": [],
            "token": "bad",
        },
    ],
)
async def test_strict_project_input_never_reaches_worker(tmp_path: Path, body: object) -> None:
    subject, manager, service, _ = coordinator(tmp_path)
    with pytest.raises(GiteaProjectError) as caught:
        await subject.configure(body)
    assert caught.value.status == 400
    manager.configure.assert_not_called()
    service.project_manager.assert_not_called()


@pytest.mark.parametrize(
    "body",
    [
        None,
        [],
        {},
        {"enabled": 1},
        {"enabled": "true"},
        {"enabled": True, "username": "tlon-operator"},
    ],
)
async def test_strict_reviewer_input(tmp_path: Path, body: object) -> None:
    subject, manager, _, _ = coordinator(tmp_path, {"id": "Name"})
    with pytest.raises(GiteaProjectError) as caught:
        await subject.set_reviewer("example", "id", body)
    assert caught.value.status == 400
    manager.set_reviewer.assert_not_called()


async def test_identity_mapping_uses_id_not_display_name_and_filters_retirement(
    tmp_path: Path,
) -> None:
    names = {"stable-id": "Original", "retired-id": "Former"}
    subject, manager, _, store = coordinator(tmp_path, names)
    store.retire("retired-id")
    result = await subject.list_projects()
    assert result["members"] == [
        {"id": "stable-id", "name": "Original", "username": member_username("stable-id")}
    ]
    names["stable-id"] = "Renamed"
    await subject.set_reviewer("example", "stable-id", {"enabled": True})
    manager.set_reviewer.assert_called_once_with("example", member_username("stable-id"), True)
    for member_id in ("retired-id", "missing", member_username("stable-id")):
        with pytest.raises(GiteaProjectError, match="不存在或已退休"):
            await subject.set_reviewer("example", member_id, {"enabled": True})
    assert manager.set_reviewer.call_count == 1


async def test_retirement_during_grant_is_immediately_revoked(tmp_path: Path) -> None:
    subject, manager, _, store = coordinator(tmp_path, {"member": "Name"})

    def grant(name: str, username: str, enabled: bool) -> dict[str, object]:
        if enabled:
            store.retire("member")
        return {"reviewers": [username] if enabled else []}

    manager.set_reviewer.side_effect = grant
    with pytest.raises(GiteaProjectError, match="已退休"):
        await subject.set_reviewer("example", "member", {"enabled": True})
    assert manager.set_reviewer.call_args_list == [
        call("example", member_username("member"), True),
        call("example", member_username("member"), False),
    ]


async def test_queued_grant_reads_current_membership_after_lock(tmp_path: Path) -> None:
    names = {"member": "Name"}
    subject, manager, _, _ = coordinator(tmp_path, names)
    await subject._lock.acquire()
    task = asyncio.create_task(subject.set_reviewer("example", "member", {"enabled": True}))
    await asyncio.sleep(0)
    names.clear()
    subject._lock.release()
    with pytest.raises(GiteaProjectError, match="不存在或已退休"):
        await task
    manager.set_reviewer.assert_not_called()


@pytest.mark.parametrize("state", ["starting", "unavailable", "not_started"])
async def test_unavailable_service_is_503(tmp_path: Path, state: str) -> None:
    subject, manager, service, _ = coordinator(tmp_path)
    service.status.return_value = {"state": state}
    with pytest.raises(GiteaProjectError) as caught:
        await subject.list_projects()
    assert caught.value.status == 503
    manager.list_projects.assert_not_called()


async def test_safe_domain_conflict_and_unknown_error_redaction(tmp_path: Path) -> None:
    subject, manager, _, _ = coordinator(tmp_path)
    manager.list_projects.side_effect = GiteaError("Permissions conflict")
    with pytest.raises(GiteaProjectError, match="Permissions conflict") as caught:
        await subject.list_projects()
    assert caught.value.status == 409
    manager.list_projects.side_effect = RuntimeError("token-secret")
    with pytest.raises(GiteaProjectError) as caught:
        await subject.list_projects()
    assert caught.value.status == 503
    assert "token-secret" not in str(caught.value)


@pytest.mark.parametrize("repeat_cancel", [False, True])
async def test_cancelled_request_keeps_lock_until_worker_exits(
    tmp_path: Path, repeat_cancel: bool
) -> None:
    subject, manager, service, _ = coordinator(tmp_path)
    entered, release, exited = threading.Event(), threading.Event(), threading.Event()

    def slow() -> list[object]:
        entered.set()
        try:
            assert release.wait(2)
            service.project_manager.call_args.args[0].check()
            return []
        finally:
            exited.set()

    manager.list_projects.side_effect = slow
    task = asyncio.create_task(subject.list_projects())
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        task.cancel()
        await asyncio.sleep(0)
        assert subject._control is not None and subject._control.stop.is_set()
        assert subject._lock.locked() and not task.done()
        if repeat_cancel:
            task.cancel()
            await asyncio.sleep(0)
            assert subject._lock.locked() and not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)
        assert exited.is_set() and not subject._lock.locked()
        assert subject._control is None
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


async def test_shutdown_waits_for_inflight_worker_and_rejects_further_work(tmp_path: Path) -> None:
    subject, manager, service, _ = coordinator(tmp_path)
    entered, release = threading.Event(), threading.Event()

    def slow() -> list[object]:
        entered.set()
        assert release.wait(2)
        service.project_manager.call_args.args[0].check()
        return []

    manager.list_projects.side_effect = slow
    task = asyncio.create_task(subject.list_projects())
    shutdown: asyncio.Task[None] | None = None
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        shutdown = asyncio.create_task(subject.shutdown())
        await asyncio.sleep(0)
        assert not shutdown.done()
        release.set()
        with pytest.raises(GiteaProjectError):
            await task
        await asyncio.wait_for(shutdown, 1)
        with pytest.raises(GiteaProjectError) as caught:
            await subject.list_projects()
        assert caught.value.status == 503
    finally:
        release.set()
        await asyncio.gather(task, *([shutdown] if shutdown else []), return_exceptions=True)
