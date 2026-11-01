"""Execution auth stays ephemeral and is restricted to the injected Git origin."""

import io
import os
import subprocess
import tarfile
from importlib.resources import files
from unittest.mock import Mock

import pytest
from docker.errors import APIError, ImageNotFound

from tlon.sandbox.gitea_image import (
    ensure_gitea_image,
    execution_environment,
    gitea_image_tag,
)


def _helper(operation: str, credential: str, env: dict[str, str]) -> str:
    if os.name == "nt":
        pytest.skip("Linux image helper requires a POSIX shell")
    helper = files("tlon.sandbox").joinpath("gitea_assets/git-credential-tlon")
    result = subprocess.run(
        ["/bin/sh", str(helper), operation],
        input=credential,
        text=True,
        capture_output=True,
        env=env,
        check=True,
    )
    assert result.stderr == ""
    return result.stdout


@pytest.mark.parametrize(
    "credential",
    [
        "protocol=http\nhost=internal:3000\n\n",
        "protocol=http\nhost=internal:3000\nusername=m-alice\npath=tlon/test.git\n\n",
    ],
)
def test_helper_supplies_credentials_only_for_internal_origin(credential: str) -> None:
    env = execution_environment("http://internal:3000/", "m-alice", "secret-token")
    assert _helper("get", credential, env) == "username=m-alice\npassword=secret-token\n\n"


@pytest.mark.parametrize(
    "credential",
    [
        "protocol=https\nhost=internal:3000\n\n",
        "protocol=http\nhost=other:3000\n\n",
        "protocol=http\nhost=internal:3001\n\n",
        "protocol=http\nhost=internal:3000.evil\n\n",
        "protocol=http\nhost=internal:3000\nusername=other\n\n",
        "host=internal:3000\n\n",
        "protocol=http\n\n",
    ],
)
def test_helper_refuses_origin_and_identity_mismatches(credential: str) -> None:
    env = execution_environment("http://internal:3000", "m-alice", "secret-token")
    assert _helper("get", credential, env) == ""


@pytest.mark.parametrize("operation", ["store", "erase"])
def test_helper_never_persists_credentials(operation: str) -> None:
    env = execution_environment("http://internal:3000", "m-alice", "secret-token")
    assert _helper(operation, "protocol=http\nhost=internal:3000\n\n", env) == ""


def test_environment_uses_native_tea_auth_and_disables_git_redirects() -> None:
    env = execution_environment("http://internal:3000", "m-alice", "secret-token")
    assert env["GITEA_INSTANCE_URL"] == "http://internal:3000"
    assert env["GITEA_TOKEN"] == "secret-token"
    assert env["XDG_CONFIG_HOME"] == "/tmp/tlon-config"
    assert env["GIT_CONFIG_KEY_0"] == "credential.helper"
    assert env["GIT_CONFIG_VALUE_0"] == ""
    assert env["GIT_CONFIG_KEY_2"] == "http.followRedirects"
    assert env["GIT_CONFIG_VALUE_2"] == "false"
    assert env["GIT_AUTHOR_EMAIL"] == "m-alice@tlon.invalid"
    assert all("secret-token" not in value for key, value in env.items() if "TOKEN" not in key)


@pytest.mark.parametrize(
    "url",
    ["file:///tmp", "http://a/path", "http://u:p@a", "http://a?q=1", "http://a#f", "http://a\n"],
)
def test_environment_rejects_non_origin_url(url: str) -> None:
    with pytest.raises(ValueError):
        execution_environment(url, "m-alice", "secret")


def test_reuses_recipe_image_without_building() -> None:
    client = Mock()
    assert ensure_gitea_image(client) == gitea_image_tag()
    client.images.get.assert_called_once_with(gitea_image_tag())
    client.api.build.assert_not_called()


def test_build_context_contains_packaged_assets_without_host_directory() -> None:
    client = Mock()
    client.images.get.side_effect = [ImageNotFound("absent"), Mock()]
    client.api.build.return_value = iter([{"stream": "done"}])
    ensure_gitea_image(client)
    kwargs = client.api.build.call_args.kwargs
    assert kwargs["custom_context"] is True
    with tarfile.open(fileobj=io.BytesIO(kwargs["fileobj"].getvalue())) as archive:
        assert set(archive.getnames()) == {"Dockerfile", "git-credential-tlon"}
        dockerfile = archive.extractfile("Dockerfile")
        assert dockerfile is not None
        recipe = dockerfile.read()
        assert b"git=1:2.34.1-1ubuntu1.18" in recipe
        assert b"tea-0.16.0-linux-${arch}" in recipe
        assert b"sha256sum --check" in recipe


def test_daemon_errors_do_not_trigger_build() -> None:
    client = Mock()
    client.images.get.side_effect = APIError("unavailable")
    with pytest.raises(APIError):
        ensure_gitea_image(client)
    client.api.build.assert_not_called()


def test_cancelled_image_build_closes_stream() -> None:
    import threading

    stop = threading.Event()
    closed: list[bool] = []

    def stream():
        try:
            stop.set()
            yield {"stream": "building"}
        finally:
            closed.append(True)

    client = Mock()
    client.images.get.side_effect = ImageNotFound("absent")
    client.api.build.return_value = stream()
    with pytest.raises(RuntimeError, match="stopped"):
        ensure_gitea_image(client, stop=stop)
    assert closed == [True]


def test_image_build_error_hides_build_output() -> None:
    client = Mock()
    client.images.get.side_effect = ImageNotFound("absent")
    client.api.build.return_value = iter([{"error": "sensitive build detail"}])
    with pytest.raises(RuntimeError, match="^Gitea execution image build failed$"):
        ensure_gitea_image(client)
