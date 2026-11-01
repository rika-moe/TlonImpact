"""Callback networking and existing-container configuration regression tests."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tlon.gitea.storage import GiteaError
from tlon.gitea.webhook_transport import _configure, _secret, callback_address


def test_configure_preserves_global_values_and_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "app.ini"
    path.write_text(
        "APP_NAME = Example\n[security]\nINSTALL_LOCK = true\n[server]\nHTTP_PORT = 3000\n"
    )
    path.chmod(0o640)
    assert _configure(path, "192.168.5.2:33276")
    content = path.read_text()
    assert "APP_NAME = Example" in content
    assert "INSTALL_LOCK = true" in content
    assert "[server]\nHTTP_PORT = 3000" in content
    assert path.stat().st_mode & 0o777 == 0o640
    assert not _configure(path, "192.168.5.2:33276")
    assert _configure(path, "172.18.0.1:33276")
    assert "192.168.5.2" not in path.read_text()
    assert path.read_text().count("ALLOWED_HOST_LIST") == 1


def test_configure_rejects_duplicate_security_settings(tmp_path: Path) -> None:
    path = tmp_path / "app.ini"
    original = "[security]\nALLOWED_HOST_LIST = private\nALLOWED_HOST_LIST = *\n"
    path.write_text(original)
    with pytest.raises(GiteaError, match="Duplicate"):
        _configure(path, "192.168.5.2:33276")
    assert path.read_text() == original


def test_secret_persists_and_corruption_never_rotates(tmp_path: Path) -> None:
    path = tmp_path / "webhook.json"
    secret = _secret(path)
    assert _secret(path) == secret
    assert path.stat().st_mode & 0o777 == 0o600
    path.write_text('{"secret":"corrupt"}')
    with pytest.raises(GiteaError, match="Invalid"):
        _secret(path)
    assert path.read_text() == '{"secret":"corrupt"}'


def test_desktop_resolves_host_from_container(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tlon.gitea.webhook_transport.platform.system", lambda: "Darwin")
    client = MagicMock()
    client.info.return_value = {"OperatingSystem": "Ubuntu"}
    container = MagicMock()
    container.exec_run.return_value = SimpleNamespace(
        exit_code=0, output=b"192.168.5.2 host.docker.internal\n"
    )
    assert callback_address(client, container) == "192.168.5.2"
    container.exec_run.return_value = SimpleNamespace(exit_code=1, output=b"")
    with pytest.raises(GiteaError, match="resolve"):
        callback_address(client, container)


def test_linux_uses_owned_bridge_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tlon.gitea.webhook_transport.platform.system", lambda: "Linux")
    client = MagicMock()
    client.info.return_value = {"OperatingSystem": "Ubuntu"}
    container = MagicMock()
    container.attrs = {"NetworkSettings": {"Networks": {"owned": {"Gateway": "172.18.0.1"}}}}
    assert callback_address(client, container) == "172.18.0.1"
    container.exec_run.assert_not_called()


def test_transport_rejects_environment_override_before_mutation(tmp_path: Path) -> None:
    from tlon.base.paths import TlonPaths
    from tlon.gitea.control import StartupControl
    from tlon.gitea.webhook_transport import prepare_transport

    container = MagicMock()
    container.attrs = {"Config": {"Env": ["GITEA__security__ALLOWED_HOST_LIST=private"]}}
    with pytest.raises(GiteaError, match="environment override"):
        prepare_transport(
            MagicMock(),
            container,
            TlonPaths(tmp_path),
            "instance",
            33276,
            "http://localhost/",
            StartupControl(),
        )
    assert not (tmp_path / "infrastructure/gitea/webhook.json").exists()
    container.restart.assert_not_called()


def test_transport_restarts_once_and_retains_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tlon.base.paths import TlonPaths
    from tlon.gitea.control import StartupControl
    from tlon.gitea.webhook_transport import prepare_transport

    paths = TlonPaths(tmp_path)
    paths.gitea_config_dir().mkdir(parents=True)
    (paths.gitea_config_dir() / "app.ini").write_text("[security]\nINSTALL_LOCK=true\n")
    monkeypatch.setattr("tlon.gitea.webhook_transport.callback_address", lambda *_: "192.168.5.2")
    monkeypatch.setattr("tlon.gitea.webhook_transport.health_ready", lambda _: True)
    container = MagicMock()
    container.attrs = {"Config": {"Env": []}}
    first = prepare_transport(
        MagicMock(), container, paths, "instance", 33276, "http://localhost/", StartupControl()
    )
    second = prepare_transport(
        MagicMock(), container, paths, "instance", 33276, "http://localhost/", StartupControl()
    )
    assert first == second
    assert first["callback_url"] == "http://192.168.5.2:33276/gitea/instance"
    container.restart.assert_called_once_with(timeout=5)


def test_probe_signs_exact_body_and_requires_ack() -> None:
    import hashlib
    import hmac

    from tlon.gitea.control import StartupControl
    from tlon.gitea.webhook_transport import probe_transport

    container = MagicMock()
    container.exec_run.return_value = SimpleNamespace(exit_code=0, output=b'{"probe":true}')
    transport = {
        "instance_id": "instance",
        "secret": "secret",
        "callback_url": "http://192.168.5.2:33276/gitea/instance",
    }
    probe_transport(container, transport, StartupControl())
    command = container.exec_run.call_args.args[0]
    body = command[command.index("--post-data") + 1]
    signature = hmac.new(b"secret", body.encode(), hashlib.sha256).hexdigest()
    assert f"X-Gitea-Signature: {signature}" in command
    assert command[:5] == ["/usr/bin/timeout", "-s", "KILL", "10", "wget"]
    container.exec_run.return_value = SimpleNamespace(exit_code=0, output=b'{"probe":false}')
    with pytest.raises(GiteaError, match="verification failed"):
        probe_transport(container, transport, StartupControl())


def test_failed_restart_is_retried_even_when_disk_setting_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tlon.base.paths import TlonPaths
    from tlon.gitea.control import StartupControl
    from tlon.gitea.webhook_transport import prepare_transport

    paths = TlonPaths(tmp_path)
    paths.gitea_config_dir().mkdir(parents=True)
    (paths.gitea_config_dir() / "app.ini").write_text("[security]\nINSTALL_LOCK=true\n")
    monkeypatch.setattr("tlon.gitea.webhook_transport.callback_address", lambda *_: "192.168.5.2")
    monkeypatch.setattr("tlon.gitea.webhook_transport.health_ready", lambda _: True)
    container = MagicMock()
    container.attrs = {"Config": {"Env": []}}
    container.restart.side_effect = RuntimeError("restart failed")
    with pytest.raises(RuntimeError):
        prepare_transport(MagicMock(), container, paths, "instance", 33276, "url", StartupControl())
    assert (paths.gitea_dir() / "webhook-restart.json").exists()
    container.restart.side_effect = None
    prepare_transport(MagicMock(), container, paths, "instance", 33276, "url", StartupControl())
    assert container.restart.call_count == 2
    assert not (paths.gitea_dir() / "webhook-restart.json").exists()
