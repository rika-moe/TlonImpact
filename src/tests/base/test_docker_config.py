"""DockerRuntimeConfig.level validation (issue #26).

The execution-device capability ladder is declared data: ``L0`` = no
execution device (no DockerRuntime, no device-dependent tools), ``L2`` =
full Docker userland (the status quo, and the default — 行为不变先行).
``L1`` (restricted runtime) is under investigation (issue #27) and not
implemented: declaring it is rejected with a message saying so.
"""

import pytest
from pydantic import ValidationError

from tlon.base.config import DockerRuntimeConfig


class TestExecutionLevel:
    def test_default_is_l2(self) -> None:
        assert DockerRuntimeConfig().level == "L2"

    @pytest.mark.parametrize("level", ["L0", "L2"])
    def test_accepts_declared_levels(self, level: str) -> None:
        assert DockerRuntimeConfig(level=level).level == level

    @pytest.mark.parametrize("level", ["L1", "l0", "L3", "", "none"])
    def test_rejects_undeclared_levels(self, level: str) -> None:
        with pytest.raises(ValidationError, match=r"docker\.level"):
            DockerRuntimeConfig(level=level)

    def test_l1_rejection_points_to_issue_27(self) -> None:
        with pytest.raises(ValidationError, match="issue #27"):
            DockerRuntimeConfig(level="L1")
