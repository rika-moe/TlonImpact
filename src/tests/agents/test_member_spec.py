"""MemberSpec: EDN roundtrip, derived runtime, and the single validator.

The unified spec (phase C) is the one semantic authority — this pins the
serialization roundtrip (the EDN front is the syntax, the dataclass is the
semantics), the derived ``member_type`` (any rule with a loop ⇒ react), and
the loader's single-validation gate.
"""

import pytest

from tlon.agents.edn import Keyword, loads
from tlon.agents.member_spec import (
    MemberSpec,
    MemberSpecValidationError,
    member_type,
    validate_member_spec,
)

COGNITIVE_EDN = """\
{:version 1
 :rules [{:name "respond"
          :on :mail-delivered
          :when (or (addressed-to-me? msg) (cc-ed? msg))
          :loop {:context {:sections [(persona) (protocol)]}
                 :stages [(think) (act)]
                 :until (or (iteration-limit-reached?) (error-limit-reached?))
                 :max-iterations 10
                 :max-consecutive-errors 3
                 :reflect {:enabled false :interval-minutes 5}}}]}
"""

SCRIPT_EDN = """\
{:version 1
 :rules [{:name "relay"
          :on :mail-delivered
          :actions [(forward {:to ["a@tlon.internal"]})]}]}
"""

FUELED_EDN = """\
{:version 1
 :rules [{:name "loopy"
          :on :mail-delivered
          :when (addressed-to-me? msg)
          :fuel 50
          :actions [(emit "x" ["sink@tlon.internal"])]}]}
"""


def _parse(text: str) -> MemberSpec:
    return MemberSpec.from_edn(loads(text))


def _action_kind(spec: MemberSpec) -> str:
    """Evaluate a rule's first action form and return its ActionRequest kind."""
    from tlon.agents.eval import eval_form, make_member_builtins

    request = eval_form(
        spec.rules[0].actions[0],
        {"msg": None, "event": None, "self": None},
        make_member_builtins(),
    )
    return request.kind


class TestRoundtrip:
    """EDN → MemberSpec → EDN → MemberSpec preserves semantics."""

    def test_cognitive_rule_roundtrip(self):
        spec = _parse(COGNITIVE_EDN)
        assert spec.version == 1
        rule = spec.rules[0]
        assert rule.name == "respond"
        assert rule.loop is not None
        assert rule.loop.max_iterations == 10
        assert rule.loop.reflect.enabled is False
        assert rule.actions == ()

        again = _parse(spec.to_edn())
        assert again == spec

    def test_deterministic_rule_roundtrip(self):
        spec = _parse(SCRIPT_EDN)
        rule = spec.rules[0]
        assert rule.loop is None
        assert len(rule.actions) == 1
        assert _action_kind(_parse(spec.to_edn())) == "forward"


class TestDerivedRuntime:
    """member_type is derived from the spec (phase C), never stored."""

    def test_cognitive_spec_is_react(self):
        assert member_type(_parse(COGNITIVE_EDN)) == "react"

    def test_deterministic_spec_is_script(self):
        assert member_type(_parse(SCRIPT_EDN)) == "script"

    def test_any_loop_rule_makes_react(self):
        """A mixed member (a loop rule + a deterministic one) is react."""
        spec = _parse(
            """\
{:version 1
 :rules [{:name "respond"
          :on :mail-delivered
          :when (addressed-to-me? msg)
          :loop {:context {:sections [(persona)]}
                 :stages [(think) (act)]
                 :until (or (iteration-limit-reached?) (error-limit-reached?))
                 :max-iterations 5
                 :max-consecutive-errors 3
                 :reflect {:enabled false :interval-minutes 5}}}
         {:name "heartbeat"
          :on :agent-idle
          :actions [(stop {:agent (get event :agent_id) :reason "drain"})]}]}
"""
        )
        assert member_type(spec) == "react"


class TestSerializationProvenance:
    """source_hash is provenance; never written back into a behavior file."""

    def test_source_hash_omitted_when_unset(self):
        data = _parse(SCRIPT_EDN).to_edn_data()
        assert "source-hash" not in data

    def test_source_hash_present_when_set(self):
        spec = _parse(SCRIPT_EDN)
        from dataclasses import replace

        spec = replace(spec, source_hash="deadbeef1234")
        assert spec.to_edn_data()["source-hash"] == "deadbeef1234"


class TestSingleValidator:
    """The loader's gate: unknown actions and bad shape are rejected."""

    def test_unknown_action_rejected_by_validator(self):
        """Unknown action heads are rejected by the single validator."""
        spec = _parse(
            """\
{:version 1
 :rules [{:name "wild"
          :on :mail-delivered
          :actions [(jump {:to ["x@tlon.internal"]})]}]}
"""
        )
        with pytest.raises(MemberSpecValidationError, match="unknown action"):
            validate_member_spec(spec)

    def test_unknown_when_builtin_rejected_by_validator(self):
        """Expression-level builtin scope is the validator's job (phase C)."""
        spec = _parse(
            """\
{:version 1
 :rules [{:name "wild"
          :on :mail-delivered
          :when (no-such-predicate? msg)
          :actions [(forward {:to ["x@tlon.internal"]})]}]}
"""
        )
        with pytest.raises(MemberSpecValidationError, match=":when"):
            validate_member_spec(spec)

    def test_unsupported_version_rejected(self):
        with pytest.raises(MemberSpecValidationError, match="version"):
            _parse("{:version 2 :rules [{:name \"x\" :on :boot :actions []}]}")

    def test_empty_rules_rejected(self):
        with pytest.raises(MemberSpecValidationError, match="rules"):
            _parse("{:version 1 :rules []}")

    def test_two_cognitive_rules_rejected(self):
        """D6.7 (phase F): at most one cognitive rule per member — the
        engine binds to a single loop rule."""
        spec = _parse(
            """\
{:version 1
 :rules [{:name "respond"
          :on :mail-delivered
          :when (addressed-to-me? msg)
          :loop {:context {:sections [(persona)]}
                 :stages [(think) (act)]
                 :until (or (iteration-limit-reached?) (error-limit-reached?))
                 :max-iterations 5
                 :max-consecutive-errors 3
                 :reflect {:enabled false :interval-minutes 5}}}
         {:name "second-brain"
          :on :mail-delivered
          :when (addressed-to-me? msg)
          :loop {:context {:sections [(persona)]}
                 :stages [(think)]
                 :until (or (iteration-limit-reached?) (error-limit-reached?))
                 :max-iterations 5
                 :max-consecutive-errors 3
                 :reflect {:enabled false :interval-minutes 5}}}]}
"""
        )
        with pytest.raises(MemberSpecValidationError, match="one cognitive rule"):
            validate_member_spec(spec)

    def test_valid_cognitive_spec_passes(self):
        validate_member_spec(_parse(COGNITIVE_EDN))

    def test_valid_script_spec_passes(self):
        validate_member_spec(_parse(SCRIPT_EDN))


class TestRuleFuel:
    """Phase D: rule-level :fuel roundtrips and is gated by MAX_FUEL."""

    def test_fuel_roundtrip(self):
        spec = _parse(FUELED_EDN)
        assert spec.rules[0].fuel == 50
        again = _parse(spec.to_edn())
        assert again == spec
        assert again.rules[0].fuel == 50

    def test_fuel_omitted_when_none(self):
        spec = _parse(SCRIPT_EDN)
        assert spec.rules[0].fuel is None
        assert Keyword("fuel") not in spec.to_edn_data()["rules"][0]

    def test_fuel_missing_uses_engine_default(self):
        # None passes the gate: the runtime falls back to its defaults
        # (when 200, actions 1000).
        spec = _parse(FUELED_EDN.replace(":fuel 50\n", ""))
        validate_member_spec(spec)

    def test_fuel_non_positive_rejected(self):
        for bad in (0, -1):
            spec = _parse(FUELED_EDN.replace(":fuel 50", f":fuel {bad}"))
            with pytest.raises(MemberSpecValidationError, match="positive"):
                validate_member_spec(spec)

    def test_fuel_over_hard_cap_rejected(self):
        from tlon.agents.eval import MAX_FUEL

        spec = _parse(FUELED_EDN.replace(":fuel 50", f":fuel {MAX_FUEL + 1}"))
        with pytest.raises(MemberSpecValidationError, match="hard cap"):
            validate_member_spec(spec)

    def test_loop_fuel_over_hard_cap_rejected(self):
        from tlon.agents.eval import MAX_FUEL

        edn = COGNITIVE_EDN.replace(
            ":max-iterations 10", f":max-iterations 10 :fuel {MAX_FUEL + 1}"
        )
        spec = _parse(edn)
        with pytest.raises(MemberSpecValidationError, match="hard cap"):
            validate_member_spec(spec)


class TestPhaseDActions:
    """Phase D: loop forms are valid action positions; the purity gate holds."""

    def test_while_action_loads(self):
        """The canonical form — a while of emits — is a loadable action."""
        spec = _parse(
            """\
{:version 1
 :rules [{:name "fanout"
          :on :mail-delivered
          :when (addressed-to-me? msg)
          :actions [(while [i 0] (< i 3) (emit "x" ["sink@tlon.internal"]) (recur (inc i)))]}]}
"""
        )
        validate_member_spec(spec)
        assert len(spec.rules[0].actions) == 1

    def test_map_action_loads(self):
        spec = _parse(
            """\
{:version 1
 :rules [{:name "fanout"
          :on :mail-delivered
          :actions [(map i (emit "x" ["sink@tlon.internal"]) [1 2 3])]}]}
"""
        )
        validate_member_spec(spec)

    def test_action_in_loop_test_rejected(self):
        """Purity invariant: a loop's test position must be pure, so an
        action there is rejected at validation."""
        spec = _parse(
            """\
{:version 1
 :rules [{:name "wild"
          :on :mail-delivered
          :actions [(while [i 0] (emit "x") (recur (inc i)))]}]}
"""
        )
        with pytest.raises(MemberSpecValidationError, match="unknown function"):
            validate_member_spec(spec)
