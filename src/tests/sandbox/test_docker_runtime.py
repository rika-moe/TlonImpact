"""Tests for DockerRuntime container creation (bind mounts)."""

from unittest.mock import MagicMock

from tlon.base.config import DockerRuntimeConfig
from tlon.base.paths import TlonPaths
from tlon.sandbox import docker_runtime as dr
from tlon.sandbox.docker_runtime import AGENT_HOME_CONTAINER_PATH, DockerRuntime


class TestCreateContainerMounts:
    async def test_agent_home_mounted_read_only(self, tmp_path, monkeypatch):
        monkeypatch.setattr(DockerRuntime, "_create_client", lambda self: MagicMock())
        tlon_paths = TlonPaths(home=tmp_path)
        monkeypatch.setattr(dr, "paths", tlon_paths)

        runtime = DockerRuntime(DockerRuntimeConfig(), agent_id="dev-001")
        runtime.client.images.get = MagicMock()
        runtime.client.containers.run = MagicMock(return_value=MagicMock())

        await runtime._create_container()

        volumes = runtime.client.containers.run.call_args.kwargs["volumes"]
        workspace = tlon_paths.agent_workspace_dir("dev-001")
        home = tlon_paths.agent_dir("dev-001")
        assert volumes == {
            str(workspace): {"bind": "/workspace", "mode": "rw"},
            str(home): {"bind": AGENT_HOME_CONTAINER_PATH, "mode": "ro"},
        }
