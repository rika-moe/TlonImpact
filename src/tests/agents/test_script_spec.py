"""Tests for ScriptSpec (tlon.agents.script_spec) — policy as data, P5a.

Covers parsing and total validation: missing sections, unknown events,
illegal ``:when`` expressions, illegal actions, and non-pure action
arguments are all load-time ScriptSpecValidationErrors.
"""

from typing import Any

import pytest

from tlon.agents.edn import EDNParseError, Form, loads
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


# ==================== Template parameters (parameterized prototypes) ====================

TEMPLATE_RELAY = """\
{:version 1
 :parameters {:to {:type :string-list :required true}}
 :rules [{:name "relay"
          :on :mail-delivered
          :actions [(forward {:to ${to}})]}]}
"""

TEMPLATE_NO_PARAMS = """\
{:version 1
 :rules [{:name "ping"
          :on :mail-delivered
          :actions [(forward {:to ["a@tlon.internal"]})]}]}
"""

TEMPLATE_OPTIONAL = """\
{:version 1
 :parameters {:reason_text {:type :string :required false}}
 :rules [{:name "greet"
          :on :boot
          :actions [(start {:agent "fixed" :reason ${reason_text}})]}]}
"""

TEMPLATE_UNUSED = """\
{:version 1
 :parameters {:reason_text {:type :string}}
 :rules [{:name "greet"
          :on :boot
          :actions [(start {:agent "fixed" :reason "test"})]}]}
"""


class TestTemplateLoads:
    """loads() with :parameters / ${var} template support."""

    def test_non_template_direct_parse(self):
        """No :parameters → parsed directly, has_parameters() is False."""
        spec = ScriptSpec.loads(TEMPLATE_NO_PARAMS)
        assert not spec.has_parameters()
        assert spec.parameters == ()

    def test_template_detects_parameters(self):
        """Template is parsed and has_parameters() is True."""
        spec = ScriptSpec.loads(TEMPLATE_RELAY)
        assert spec.has_parameters()
        assert len(spec.parameters) == 1
        assert spec.parameters[0].name == "to"
        assert spec.parameters[0].type == "string-list"
        assert spec.parameters[0].required is True

    def test_template_rules_validated(self):
        """Template rules are validated after placeholder substitution."""
        spec = ScriptSpec.loads(TEMPLATE_RELAY)
        assert spec.version == 1
        assert [r.name for r in spec.rules] == ["relay"]
        assert spec.rules[0].on is EventType.MAIL_DELIVERED

    def test_undeclared_placeholder_rejected(self):
        """${var} not declared in :parameters → error."""
        bad = """\
{:version 1
 :parameters {:to {:type :string-list}}
 :rules [{:name "r" :on :boot :actions [(forward {:to ${unknown}})]}]}
"""
        with pytest.raises(ScriptSpecValidationError, match="not declared"):
            ScriptSpec.loads(bad)

    def test_unused_parameter_silently_ignored(self):
        """:parameters without ${var} → fast path, parameters ignored.
        This is a valid state — no template vars means the spec is
        concrete, and :parameters is treated as benign extra metadata."""
        spec = ScriptSpec.loads(TEMPLATE_UNUSED)
        assert not spec.has_parameters()
        assert spec.parameters == ()

    def test_empty_parameters_rejected(self):
        """Empty :parameters map with ${var} → error. Needs ${var}
        to force the template path."""
        bad = """\
{:version 1
 :parameters {}
 :rules [{:name "r" :on :boot :actions [(start {:agent ${x} :reason "test"})]}]}
"""
        with pytest.raises(ScriptSpecValidationError, match=":parameters must not be empty"):
            ScriptSpec.loads(bad)

    def test_placeholders_without_parameters_rejected(self):
        """${var} found but no :parameters section declared."""
        bad = """\
{:version 1
 :rules [{:name "r" :on :boot :actions [(start {:agent ${msg} :reason "test"})]}]}
"""
        with pytest.raises(ScriptSpecValidationError, match="no :parameters"):
            ScriptSpec.loads(bad)

    def test_optional_parameter_parse(self):
        """Optional parameter (required: false)."""
        spec = ScriptSpec.loads(TEMPLATE_OPTIONAL)
        assert len(spec.parameters) == 1
        assert spec.parameters[0].name == "reason_text"
        assert spec.parameters[0].required is False

    def test_invalid_param_type_rejected(self):
        """Unknown parameter type — caught in _parse_parameters before action
        validation."""
        bad = """\
{:version 1
 :parameters {:x {:type :integer}}
 :rules [{:name "r" :on :boot :actions [(start {:agent ${x} :reason "test"})]}]}
"""
        with pytest.raises(ScriptSpecValidationError, match=":type must be one of"):
            ScriptSpec.loads(bad)


class TestTemplateRender:
    """ScriptSpec.render() — parameter substitution."""

    def test_render_basic(self):
        """Render substitutes ${var} with EDN values."""
        spec = ScriptSpec.loads(TEMPLATE_RELAY)
        result = spec.render({"to": ["alice@tlon.internal"]})
        assert "${to}" not in result
        assert "alice@tlon.internal" in result
        # Result is valid EDN
        parsed = loads(result)
        assert isinstance(parsed, dict)

    def test_render_multiple_addresses(self):
        """string-list renders as EDN vector."""
        spec = ScriptSpec.loads(TEMPLATE_RELAY)
        result = spec.render({"to": ["a@x.com", "b@x.com"]})
        parsed = loads(result)
        # Navigate to the :to value
        rule = parsed["rules"][0]
        assert isinstance(rule, dict)
        # The action is: (forward {:to [...]})
        action = rule["actions"][0]
        assert isinstance(action, Form)
        # Second element is the opts map {:to [...]}
        opts = action[1]
        assert isinstance(opts, dict)
        to_key = [k for k in opts if str(k) == "to"][0]
        assert opts[to_key] == ["a@x.com", "b@x.com"]

    def test_render_missing_required(self):
        """Missing required param → error."""
        spec = ScriptSpec.loads(TEMPLATE_RELAY)
        with pytest.raises(ScriptSpecValidationError, match="Missing required"):
            spec.render({})

    def test_render_unknown_key(self):
        """Unknown key in params → error."""
        spec = ScriptSpec.loads(TEMPLATE_RELAY)
        with pytest.raises(ScriptSpecValidationError, match="Unknown"):
            spec.render({"to": ["a@x.com"], "extra": "bad"})

    def test_render_wrong_type_string(self):
        """string param gets a list → error."""
        spec = ScriptSpec.loads(TEMPLATE_OPTIONAL)
        with pytest.raises(ScriptSpecValidationError, match="expected string"):
            spec.render({"reason_text": ["not", "a", "string"]})

    def test_render_wrong_type_string_list(self):
        """string-list param gets a string → error."""
        spec = ScriptSpec.loads(TEMPLATE_RELAY)
        with pytest.raises(ScriptSpecValidationError, match="expected list of strings"):
            spec.render({"to": "not-a-list"})

    def test_render_non_template_raises(self):
        """Calling render() on a non-template spec raises."""
        spec = ScriptSpec.loads(TEMPLATE_NO_PARAMS)
        with pytest.raises(ScriptSpecValidationError, match="not loaded from"):
            spec.render({"x": "y"})

    def test_render_shaped_result_is_valid_spec(self):
        """Rendered output is a valid, loadable ScriptSpec (no template vars)."""
        spec = ScriptSpec.loads(TEMPLATE_RELAY)
        rendered = spec.render({"to": ["alice@tlon.internal"]})
        # The rendered text should be loadable as a non-template spec
        reloaded = ScriptSpec.loads(rendered)
        assert not reloaded.has_parameters()
        assert reloaded.version == 1
        assert [r.name for r in reloaded.rules] == ["relay"]
