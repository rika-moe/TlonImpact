"""Tests for AgentConfig.runtime (P5b: react/script runtime split)."""

import pytest
from pydantic import ValidationError

from tlon.base.config import AgentConfig


class TestRuntimeField:
    def test_runtime_defaults_to_react(self):
        config = AgentConfig(id="a-001", name="甲")
        assert config.runtime == "react"

    def test_runtime_explicit_script(self):
        config = AgentConfig(id="a-001", name="甲", runtime="script")
        assert config.runtime == "script"

    def test_runtime_invalid_value_rejected(self):
        with pytest.raises(ValidationError):
            AgentConfig(id="a-001", name="甲", runtime="other")
