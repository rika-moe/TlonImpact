"""Regression tests for the quickstart wizard's standard-team preview.

The preview used to read ``member["proactive"]`` — a key the team dicts
never carried — crashing with KeyError (issue ). The startup mode is
now derived from the member's behavior spec (``:on :boot`` rule).
"""

import io
import tempfile
from pathlib import Path
from typing import Any

from rich.console import Console

from tlon.agents.behavior_templates import standard_behavior_edn
from tlon.app.wizard.agent_store import AgentStore, startup_mode_from_behavior
from tlon.app.wizard.menus.quickstart import QuickstartWizard
from tlon.base.config import AgentConfig
from tlon.base.default_team import STANDARD_TEAM_DICTS
from tlon.base.paths import TlonPaths


class _FakeAsk:
    def __init__(self, value: Any):
        self._value = value

    def ask(self) -> Any:
        return self._value


class _FakeQuestionary:
    """Minimal questionary stand-in: always declines the confirm prompt."""

    def confirm(self, *_args: Any, **_kwargs: Any) -> _FakeAsk:
        return _FakeAsk(False)


class TestStartupModeFromBehavior:
    def test_standard_team_members_all_resident(self):
        """Every standard team member's behavior declares :on :boot → 常驻."""
        assert STANDARD_TEAM_DICTS, "standard team must not be empty"
        for member in STANDARD_TEAM_DICTS:
            mode = startup_mode_from_behavior(standard_behavior_edn(member["id"]))
            assert mode == "常驻", f"{member['id']} should boot at startup"

    def test_unparseable_behavior_is_lazy(self):
        """Garbage behavior text falls back to 延迟 instead of raising."""
        assert startup_mode_from_behavior("not edn at all {{{") == "延迟"
        assert startup_mode_from_behavior("") == "延迟"


class TestStandardTeamPreview:
    def test_preview_path_does_not_raise(self):
        """Issue : the preview must not KeyError on team dicts."""
        wizard = QuickstartWizard(
            Console(file=io.StringIO()),
            tlon_home=tempfile.mkdtemp(),
        )
        wizard.questionary = _FakeQuestionary()

        # Declining the confirm returns None after printing the table; the
        # regression was a KeyError before the prompt was ever reached.
        assert wizard._configure_standard_team("kimi-k2.5") is None


class TestStoreStartupMode:
    def test_written_behavior_is_resident(self):
        """A wizard-created agent's on-disk behavior derives to 常驻."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = AgentStore(paths=TlonPaths(home=Path(tmpdir)))
            store.save_agent(AgentConfig(id="booty", name="常驻者"))
            store.write_behavior_file("booty")

            assert store.startup_mode("booty") == "常驻"

    def test_missing_behavior_is_lazy(self):
        """No behavior file on disk → 延迟 (never raises)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = AgentStore(paths=TlonPaths(home=Path(tmpdir)))
            store.save_agent(AgentConfig(id="sleepy", name="延迟者"))

            assert store.startup_mode("sleepy") == "延迟"
