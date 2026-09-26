"""DockerRuntimeConfig.level validation (issue #26, L1 landed in #40).

The execution-device capability ladder is declared data: ``L0`` = no
execution device (no DockerRuntime, no device-dependent tools), ``L1`` =
文件层 (archive-only file I/O, structurally zero code execution), ``L2``
= full Docker userland (the status quo, and the default — 行为不变先行).
"""

import pytest
from pydantic import ValidationError

from tlon.base.config import DockerRuntimeConfig


class TestExecutionLevel:
    def test_default_is_l2(self) -> None:
        assert DockerRuntimeConfig().level == "L2"

    @pytest.mark.parametrize("level", ["L0", "L1", "L2"])
    def test_accepts_declared_levels(self, level: str) -> None:
        assert DockerRuntimeConfig(level=level).level == level

    @pytest.mark.parametrize("level", ["l0", "l1", "L3", "", "none"])
    def test_rejects_undeclared_levels(self, level: str) -> None:
        with pytest.raises(ValidationError, match=r"docker\.level"):
            DockerRuntimeConfig(level=level)
