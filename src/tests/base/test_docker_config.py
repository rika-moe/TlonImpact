"""DockerRuntimeConfig.level validation (issue #26, L1 landed in #40).

The execution-device capability ladder is declared data: ``L0`` = no
execution device (no DockerRuntime, no device-dependent tools), ``L1`` =
文件层 (archive-only file I/O, structurally zero code execution), ``L2``
= full Docker userland. The default is ``L1`` (flipped in issue #31,
2026-10-05): members without an ``:execution`` declaration get the
lowest sufficient file layer.
"""

import pytest
from pydantic import ValidationError

from tlon.base.config import AgentConfig, DockerRuntimeConfig


class TestExecutionLevel:
    def test_default_is_l1(self) -> None:
        assert DockerRuntimeConfig().level == "L1"

    @pytest.mark.parametrize("level", ["L0", "L1", "L2"])
    def test_accepts_declared_levels(self, level: str) -> None:
        assert DockerRuntimeConfig(level=level).level == level

    @pytest.mark.parametrize("level", ["l0", "l1", "L3", "", "none"])
    def test_rejects_undeclared_levels(self, level: str) -> None:
        with pytest.raises(ValidationError, match=r"docker\.level"):
            DockerRuntimeConfig(level=level)


def test_new_members_default_to_gitea() -> None:
    assert AgentConfig(id="new-member", name="New member").docker.network_mode == "gitea"


def test_explicit_offline_configuration_is_preserved() -> None:
    config = AgentConfig(
        id="offline", name="Offline", docker=DockerRuntimeConfig(network_mode="none")
    )
    assert config.docker.network_mode == "none"
