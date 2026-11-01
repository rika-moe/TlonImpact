"""Persistent bootstrap recovery and safe public lifecycle."""

import asyncio
import json
import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tlon.base.paths import TlonPaths
from tlon.gitea import service
from tlon.gitea.initialize import initialize
from tlon.gitea.storage import GiteaError, load_records


def test_records_reused_and_private(tmp_path: Path) -> None:
    paths = TlonPaths(tmp_path)
    state, credentials = load_records(paths, 3000)
    assert load_records(paths, 3000) == (state, credentials)
    assert paths.gitea_credentials_file().stat().st_mode & 0o777 == 0o600
    operator = json.loads(paths.gitea_operator_credentials_file().read_text())
    assert credentials["admin_password"] not in str(operator)
    other, _ = load_records(TlonPaths(tmp_path / "other"), 3001)
    assert other["instance_id"] != state["instance_id"]


@pytest.mark.parametrize("corruption", ["bad-json", "missing-credentials", "changed-port"])
def test_corruption_refused(tmp_path: Path, corruption: str) -> None:
    paths = TlonPaths(tmp_path)
    load_records(paths, 3000)
    paths.gitea_data_dir().mkdir()
    if corruption == "bad-json":
        paths.gitea_state_file().write_text("bad")
    elif corruption == "missing-credentials":
        paths.gitea_credentials_file().unlink()
    with pytest.raises(GiteaError):
        load_records(paths, 3001 if corruption == "changed-port" else 3000)


@pytest.mark.asyncio
async def test_lifecycle_retains_container(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = MagicMock()
    container = MagicMock()
    monkeypatch.setattr(service, "create_docker_client", lambda: client)
    monkeypatch.setattr(service, "ensure_container", lambda *args, **kwargs: container)
    monkeypatch.setattr(service, "initialize", lambda *args, **kwargs: None)
    subject = service.GiteaService(TlonPaths(tmp_path))
    assert (await subject.ensure_started())["state"] == "ready"
    await subject.shutdown()
    container.stop.assert_not_called()
    container.remove.assert_not_called()
    client.close.assert_called_once()
    assert "password" not in json.dumps(subject.status())


@pytest.mark.asyncio
async def test_failure_redacted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail() -> None:
        raise RuntimeError("secret-password")

    monkeypatch.setattr(service, "create_docker_client", fail)
    subject = service.GiteaService(TlonPaths(tmp_path))
    result = await subject.ensure_started()
    assert result["state"] == "unavailable"
    assert "secret-password" not in json.dumps(result)


@pytest.mark.asyncio
async def test_cancellation_waits_for_owned_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered, release = threading.Event(), threading.Event()

    def wait() -> dict[str, object]:
        entered.set()
        release.wait(5)
        return {"state": "ready"}

    subject = service.GiteaService(TlonPaths(tmp_path))
    monkeypatch.setattr(subject, "_ensure", wait)
    task = asyncio.create_task(subject.ensure_started())
    await asyncio.to_thread(entered.wait, 5)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    await subject.shutdown()


def test_existing_bootstrap_does_not_reset_password(monkeypatch: pytest.MonkeyPatch) -> None:
    from tlon.gitea import initialize as module

    monkeypatch.setattr(module, "health_ready", lambda url: True)
    responses = iter(
        [
            (200, {"is_admin": True, "login": "tlon-service"}),
            (200, {}),
            (200, {"visibility": "private"}),
            (
                200,
                [
                    {"name": "Owners", "id": 1},
                    {
                        "name": "members",
                        "permission": "none",
                        "units_map": {
                            "repo.code": "read",
                            "repo.issues": "read",
                            "repo.pulls": "read",
                        },
                        "can_create_org_repo": False,
                        "includes_all_repositories": True,
                    },
                ],
            ),
            (200, {}),
        ]
    )
    monkeypatch.setattr(module, "request", lambda *args, **kwargs: next(responses))
    container = MagicMock()
    initialize(container, "http://127.0.0.1:3000/", {"admin_password": "secret"})
    container.exec_run.assert_not_called()


def test_first_bootstrap_and_interrupted_resume(monkeypatch: pytest.MonkeyPatch) -> None:
    from tlon.gitea import initialize as module

    calls: list[str] = []
    monkeypatch.setattr(module, "health_ready", lambda url: True)
    responses = iter(
        [
            (401, None),
            (200, {"is_admin": True, "login": "tlon-service"}),
            (404, None),
            (201, {}),
            (404, None),
            (500, None),
        ]
    )

    def reply(url: str, path: str, password: str, **kwargs: object) -> tuple[int, object]:
        calls.append(path)
        return next(responses)

    monkeypatch.setattr(module, "request", reply)
    container = MagicMock()
    container.client.api.exec_inspect.return_value = {"Running": False, "ExitCode": 0}
    with pytest.raises(GiteaError, match="organization initialization"):
        initialize(
            container,
            "http://127.0.0.1:3000/",
            {"admin_password": "secret", "operator_password": "human"},
        )
    container.client.api.exec_create.assert_called_once()
    # After failure, real server state wins: existing accounts are reused.
    monkeypatch.setattr(module, "health_ready", lambda url: True)
    responses = iter(
        [
            (200, {"is_admin": True, "login": "tlon-service"}),
            (200, {}),
            (404, None),
            (201, {"visibility": "private"}),
            (200, [{"name": "Owners", "id": 1}]),
            (200, {}),
            (201, {}),
        ]
    )
    initialize(
        container,
        "http://127.0.0.1:3000/",
        {"admin_password": "secret", "operator_password": "human"},
    )
    container.client.api.exec_create.assert_called_once()
    assert calls.count("admin/users") == 1


def test_admin_exec_uses_detached_timeout_and_bounded_poll(monkeypatch: pytest.MonkeyPatch) -> None:
    from tlon.gitea import initialize as module
    from tlon.gitea.control import StartupControl

    container = MagicMock()
    container.client.api.exec_inspect.return_value = {"Running": True}
    ticks = iter([0.0, 36.0])
    control = MagicMock(spec=StartupControl)
    monkeypatch.setattr(module.time, "monotonic", lambda: next(ticks))
    with pytest.raises(GiteaError, match="timed out"):
        module.run_admin_command(container, ["gitea", "admin"], control)
    assert container.client.api.exec_create.call_args.args[1][:4] == [
        "/usr/bin/timeout",
        "-s",
        "KILL",
        "30",
    ]
    assert container.client.api.exec_start.call_args.kwargs["detach"] is True
    container.exec_run.assert_not_called()


@pytest.mark.asyncio
async def test_shutdown_signals_worker_before_waiting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    subject = service.GiteaService(TlonPaths(tmp_path))
    entered = threading.Event()

    def worker() -> dict[str, object]:
        entered.set()
        assert subject._stop.wait(2)
        return {"state": "unavailable"}

    monkeypatch.setattr(subject, "_ensure", worker)
    task = asyncio.create_task(subject.ensure_started())
    await asyncio.to_thread(entered.wait, 2)
    await subject.shutdown()
    assert (await task)["state"] == "unavailable"


def test_team_missing_units_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    from tlon.gitea import initialize as module

    monkeypatch.setattr(module, "health_ready", lambda url: True)
    responses = iter(
        [
            (200, {"is_admin": True, "login": "tlon-service"}),
            (200, {}),
            (200, {"visibility": "private"}),
            (
                200,
                [
                    {"name": "Owners", "id": 1},
                    {
                        "name": "members",
                        "permission": "read",
                        "can_create_org_repo": False,
                        "includes_all_repositories": True,
                        "units_map": {"repo.code": "read"},
                    },
                ],
            ),
            (200, {}),
        ]
    )
    monkeypatch.setattr(module, "request", lambda *args, **kwargs: next(responses))
    with pytest.raises(GiteaError, match="permissions mismatch"):
        initialize(MagicMock(), "http://127.0.0.1:3000/", {"admin_password": "secret"})


def test_health_request_is_unauthenticated_before_first_admin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tlon.gitea import initialize as module

    opener = MagicMock()
    response = opener.open.return_value.__enter__.return_value
    response.status = 200
    response.read.return_value = b'{"status":"pass"}'
    monkeypatch.setattr(module, "build_opener", lambda *args: opener)
    assert module.health_ready("http://127.0.0.1:3000/")
    req = opener.open.call_args.args[0]
    assert req.full_url == "http://127.0.0.1:3000/api/healthz"
    assert req.get_header("Authorization") is None
    assert req.data is None


@pytest.mark.parametrize("created", [False, True])
@pytest.mark.parametrize(
    "organization", [{"visibility": "public"}, {"visibility": "limited"}, {}, None]
)
def test_organization_visibility_drift_refused(
    monkeypatch: pytest.MonkeyPatch, created: bool, organization: object
) -> None:
    from tlon.gitea import initialize as module

    monkeypatch.setattr(module, "health_ready", lambda url: True)
    calls: list[str] = []
    responses = iter(
        [
            (200, {"is_admin": True, "login": "tlon-service"}),
            (200, {}),
            *(([(404, None), (201, organization)]) if created else [(200, organization)]),
        ]
    )

    def reply(url: str, path: str, password: str, **kwargs: object) -> tuple[int, object]:
        calls.append(path)
        return next(responses)

    monkeypatch.setattr(module, "request", reply)
    with pytest.raises(GiteaError, match="organization visibility mismatch"):
        initialize(MagicMock(), "http://127.0.0.1:3000/", {"admin_password": "secret"})
    assert calls == [
        "user",
        "users/tlon-operator",
        "orgs/tlon",
        *(["admin/users/tlon-operator/orgs"] if created else []),
    ]
