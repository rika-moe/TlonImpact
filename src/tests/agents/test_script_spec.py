"""Tests for ScriptSpec (tlon.agents.script_spec) — policy as data, P5a.

Covers parsing and total validation: missing sections, unknown events,
illegal ``:when`` expressions, illegal actions, and non-pure action
arguments are all load-time ScriptSpecValidationErrors.
"""

from typing import Any

import pytest

from tlon.agents.edn import EDNParseError, loads
from tlon.agents.eval import make_script_builtins
from tlon.agents.script_spec import (
    ScriptSpec,
    ScriptSpecValidationError,
)
from tlon.base.events.event import EventType
from tlon.base.paths import TlonPaths

VALID_SPEC = """\
{:version 1
 :rules [{:name "boot"
          :on :boot
          :actions [(start-all {:reason "boot"})]}
         {:name "wake"
          :on :mail-delivered
          :when (sleeping? (get event :recipient))
          :actions [(start {:agent (get event :recipient) :reason "wake"})]}
         {:name "drain"
          :on :agent-idle
          :actions [(stop {:agent (get event :agent_id) :reason "drain"})]}]}
"""


class _FakeHost:
    """Host queries good enough for spec validation (names, not behavior)."""

    def is_sleeping(self, agent_id: Any) -> bool:
        return False

    def is_running(self, agent_id: Any) -> bool:
        return False

    def is_lazy(self, agent_id: Any) -> bool:
        return False

    def start_all_candidates(self) -> list[str]:
        return []


def _builtins_with_host() -> dict:
    return make_script_builtins(_FakeHost())


class TestValidSpec:
    def test_adam_shaped_spec_parses(self):
        spec = ScriptSpec.from_edn(loads(VALID_SPEC), builtins=_builtins_with_host())

        assert spec.version == 1
        assert [rule.name for rule in spec.rules] == ["boot", "wake", "drain"]
        boot, wake, drain = spec.rules
        assert boot.on is None  # :boot marker
        assert boot.when is None
        assert wake.on == EventType.MAIL_DELIVERED
        assert wake.when is not None
        assert drain.on == EventType.AGENT_IDLE
        assert drain.when is None
        assert all(len(rule.actions) >= 1 for rule in spec.rules)

    def test_when_defaults_to_none(self):
        spec = ScriptSpec.from_edn(
            loads(
                '{:version 1 :rules [{:name "r" :on :agent-idle '
                ':actions [(stop {:agent "a" :reason "drain"})]}]}'
            )
        )
        assert spec.rules[0].when is None

    def test_unknown_keys_are_tolerated(self):
        spec = ScriptSpec.from_edn(
            loads(
                '{:version 1 :rules [{:name "r" :on :agent-idle :future-key 42 '
                ':actions [(stop {:agent "a" :reason "drain"})]}]}'
            )
        )
        assert spec.rules[0].name == "r"


class TestTotalValidation:
    @pytest.mark.parametrize(
        "source",
        [
            "{:rules []}",  # missing :version
            "{:version 2 :rules []}",  # unsupported version
            '{:version "1" :rules []}',  # version type error
            "{:version 1}",  # missing :rules
            "[{:version 1}]",  # top level not a map
        ],
    )
    def test_missing_or_bad_sections(self, source: str):
        with pytest.raises(ScriptSpecValidationError):
            ScriptSpec.from_edn(loads(source))

    @pytest.mark.parametrize(
        "rules",
        [
            "{:name \"r\"}",  # :rules not a vector (map)
            "[]",  # empty vector
            "[42]",  # rule not a map
        ],
    )
    def test_bad_rules_vector(self, rules: str):
        with pytest.raises(ScriptSpecValidationError):
            ScriptSpec.from_edn(loads(f"{{:version 1 :rules {rules}}}"))


class TestRuleValidation:
    @pytest.mark.parametrize(
        "rule",
        [
            '{:on :agent-idle :actions [(stop {:agent "a" :reason "r"})]}',  # no :name
            '{:name "" :on :agent-idle :actions [(stop {:agent "a" :reason "r"})]}',
            '{:name "r" :actions [(stop {:agent "a" :reason "r"})]}',  # no :on
            '{:name "r" :on "agent-idle" :actions [(stop {:agent "a" :reason "r"})]}',
            '{:name "r" :on :no-such-event :actions [(stop {:agent "a" :reason "r"})]}',
            '{:name "r" :on :agent-idle}',  # no :actions
            '{:name "r" :on :agent-idle :actions []}',  # empty :actions
            '{:name "r" :on :agent-idle :actions "x"}',  # :actions not a vector
            '{:name "r" :on :agent-idle :actions [42]}',  # action not a form
            '{:name "r" :on :agent-idle :actions [()]}',  # empty action form
        ],
    )
    def test_bad_rule_fields(self, rule: str):
        with pytest.raises(ScriptSpecValidationError):
            ScriptSpec.from_edn(loads(f"{{:version 1 :rules [{rule}]}}"))

    def test_duplicate_rule_names(self):
        with pytest.raises(ScriptSpecValidationError, match="duplicate"):
            ScriptSpec.from_edn(
                loads(
                    '{:version 1 :rules [{:name "r" :on :agent-idle '
                    ':actions [(stop {:agent "a" :reason "r"})]} '
                    '{:name "r" :on :agent-idle '
                    ':actions [(stop {:agent "b" :reason "r"})]}]}'
                )
            )


class TestWhenValidation:
    @pytest.mark.parametrize(
        "when",
        [
            "(no-such-fn event)",  # unknown function
            "recipient",  # undefined symbol
            "(start {:agent \"a\" :reason \"r\"})",  # action in pure position
            '"not-an-expression"',  # not a Form
        ],
    )
    def test_bad_when(self, when: str):
        source = (
            '{:version 1 :rules [{:name "r" :on :agent-idle :when WHEN '
            ':actions [(stop {:agent "a" :reason "r"})]}]}'
        ).replace("WHEN", when)
        with pytest.raises(ScriptSpecValidationError):
            ScriptSpec.from_edn(loads(source), builtins=_builtins_with_host())

    def test_host_predicate_rejected_without_host(self):
        """sleeping? is host-injected: a host-less registry must reject it."""
        with pytest.raises(ScriptSpecValidationError):
            ScriptSpec.from_edn(loads(VALID_SPEC))  # no builtins -> no host

    def test_host_predicate_accepted_with_host(self):
        spec = ScriptSpec.from_edn(loads(VALID_SPEC), builtins=_builtins_with_host())
        assert spec.rules[1].when is not None


class TestActionValidation:
    @pytest.mark.parametrize(
        "action",
        [
            "(spawn \"proto\")",  # not a script action
            "(emit \"hi\")",
            "(no-such-action)",
            # non-pure argument: action in argument position
            '(start {:agent (stop {:agent "a" :reason "r"}) :reason "r"})',
            # unknown function in argument position
            '(start {:agent (no-such-fn event) :reason "r"})',
            # undefined symbol in argument position
            '(start {:agent recipient :reason "r"})',
            # host predicate in argument position is pure — but sleeping? is
            # unavailable without a host (see host test below)
        ],
    )
    def test_bad_actions(self, action: str):
        source = (
            '{:version 1 :rules [{:name "r" :on :agent-idle :actions [ACTION]}]}'
        ).replace("ACTION", action)
        with pytest.raises(ScriptSpecValidationError):
            ScriptSpec.from_edn(loads(source), builtins=_builtins_with_host())

    def test_pure_expression_arguments_accepted(self):
        source = (
            '{:version 1 :rules [{:name "r" :on :mail-delivered '
            ':actions [(start {:agent (get event :recipient) :reason "wake"})]}]}'
        )
        spec = ScriptSpec.from_edn(loads(source), builtins=_builtins_with_host())
        assert len(spec.rules[0].actions) == 1


class TestLoad:
    """P5b: ScriptSpec.load — the script file is total, like AgentSpec.load."""

    @pytest.fixture
    def test_paths(self, tmp_path, monkeypatch):
        paths = TlonPaths(home=tmp_path)
        monkeypatch.setattr("tlon.agents.script_spec.paths", paths)
        return paths

    def _write(self, test_paths: TlonPaths, agent_id: str, content: str) -> None:
        script_file = test_paths.agent_config_dir(agent_id) / "script.tlon"
        script_file.write_text(content, encoding="utf-8")

    def test_load_reads_script_file(self, test_paths):
        self._write(
            test_paths,
            "rl-001",
            '{:version 1 :rules [{:name "relay" :on :mail-delivered '
            ':actions [(forward {:to ["a@tlon.internal"]})]}]}',
        )

        spec = ScriptSpec.load("rl-001")

        assert spec.version == 1
        assert [rule.name for rule in spec.rules] == ["relay"]

    def test_load_missing_file_refuses_boot(self, test_paths):
        with pytest.raises(ScriptSpecValidationError, match="script file missing"):
            ScriptSpec.load("ghost")

    def test_load_invalid_edn_raises(self, test_paths):
        self._write(test_paths, "rl-002", "{:version 1 :rules [")
        with pytest.raises(EDNParseError):
            ScriptSpec.load("rl-002")

    def test_load_invalid_spec_raises(self, test_paths):
        self._write(test_paths, "rl-003", "{:version 1}")  # missing :rules
        with pytest.raises(ScriptSpecValidationError):
            ScriptSpec.load("rl-003")
