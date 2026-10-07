"""MemberSpec: EDN roundtrip and the single validator.

The unified spec (phase C) is the one semantic authority — this pins the
serialization roundtrip (the EDN front is the syntax, the dataclass is the
semantics) and the loader's single-validation gate.
"""

import pytest

from tlon.agents.member_spec import (
    MemberSpec,
    MemberSpecValidationError,
    validate_member_spec,
)
from tlosure.edn import Keyword, loads

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
                 :max-tokens 1000000
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
    from tlon.agents.vocabulary import make_member_builtins
    from tlosure.eval import eval_form

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
        assert rule.loop.max_tokens == 1000000
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


class TestLlmKeyRejected:
    """Top-level ``:llm`` was removed from the grammar (ADR 0016): model
    selection is deployment policy, not genotype — any residual ``:llm``
    key is a load error, not a warning."""

    RULES = """\
 :rules [{:name "relay"
          :on :mail-delivered
          :actions [(forward {:to ["x@tlon.internal"]})]}]}
"""

    def test_llm_map_rejected(self):
        with pytest.raises(MemberSpecValidationError, match=":llm"):
            _parse('{:version 1\n :llm {:model "deepseek/deepseek-chat"}\n' + self.RULES)

    def test_llm_empty_map_rejected(self):
        with pytest.raises(MemberSpecValidationError, match=":llm"):
            _parse("{:version 1\n :llm {}\n" + self.RULES)

    def test_llm_non_map_rejected(self):
        with pytest.raises(MemberSpecValidationError, match=":llm"):
            _parse('{:version 1\n :llm "deepseek/deepseek-chat"\n' + self.RULES)

    def test_no_llm_key_in_serialization(self):
        spec = _parse(SCRIPT_EDN)
        assert "llm" not in spec.to_edn_data()
        assert "llm" not in spec.to_dict()

    def test_unknown_top_level_key_still_tolerated(self):
        """Warn-only forward compatibility still holds for other keys."""
        spec = _parse(
            """\
{:version 1
 :future-key 42
 :rules [{:name "relay"
          :on :mail-delivered
          :actions [(forward {:to ["x@tlon.internal"]})]}]}
"""
        )
        assert spec.rules[0].name == "relay"


class TestExecutionSection:
    """Top-level ``:execution`` — the execution-device capability declaration
    (constitution 1, docs/design/execution-device.md): genotype, reviewed
    with the proposal, projected once into agent.yaml at instantiation."""

    RULES = """\
 :rules [{:name "relay"
          :on :mail-delivered
          :actions [(forward {:to ["x@tlon.internal"]})]}]}
"""

    @pytest.mark.parametrize("level", ["L0", "L1", "L2"])
    def test_levels_parse(self, level: str):
        spec = _parse(f"{{:version 1\n :execution {{:level :{level}}}\n" + self.RULES)
        assert spec.execution_level == level

    def test_absent_section_is_none(self):
        assert _parse(SCRIPT_EDN).execution_level is None

    def test_edn_roundtrip_preserves_section(self):
        spec = _parse("{:version 1\n :execution {:level :L1}\n" + self.RULES)
        again = _parse(spec.to_edn())
        assert again == spec
        assert again.execution_level == "L1"

    def test_dict_roundtrip_preserves_section(self):
        spec = _parse("{:version 1\n :execution {:level :L0}\n" + self.RULES)
        assert spec.to_dict()["execution"] == {"level": "L0"}
        assert MemberSpec.from_dict(spec.to_dict()) == spec

    def test_invalid_level_rejected(self):
        with pytest.raises(MemberSpecValidationError, match=":execution/:level"):
            _parse("{:version 1\n :execution {:level :L3}\n" + self.RULES)

    def test_non_map_rejected(self):
        with pytest.raises(MemberSpecValidationError, match=":execution must be a map"):
            _parse("{:version 1\n :execution :L1\n" + self.RULES)

    def test_missing_level_rejected(self):
        with pytest.raises(MemberSpecValidationError, match=":execution/:level"):
            _parse("{:version 1\n :execution {}\n" + self.RULES)

    def test_unknown_key_inside_section_tolerated(self):
        """Warn-only forward compatibility holds inside :execution too."""
        spec = _parse("{:version 1\n :execution {:level :L2 :future-key 42}\n" + self.RULES)
        assert spec.execution_level == "L2"


class TestSingleValidator:
    """The loader's gate: unknown actions and bad shape are rejected."""

    def test_unknown_action_rejected_by_validator(self):
        """Unregistered vocabulary in action position is rejected by the
        single validator (ADR 0021: full-registry validation)."""
        spec = _parse(
            """\
{:version 1
 :rules [{:name "wild"
          :on :mail-delivered
          :actions [(jump {:to ["x@tlon.internal"]})]}]}
"""
        )
        with pytest.raises(
            MemberSpecValidationError, match="unknown function or special form: jump"
        ):
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
            _parse('{:version 2 :rules [{:name "x" :on :boot :actions []}]}')

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
                 :max-tokens 1000000
                 :reflect {:enabled false :interval-minutes 5}}}
         {:name "second-brain"
          :on :mail-delivered
          :when (addressed-to-me? msg)
          :loop {:context {:sections [(persona)]}
                 :stages [(think)]
                 :until (or (iteration-limit-reached?) (error-limit-reached?))
                 :max-iterations 5
                 :max-consecutive-errors 3
                 :max-tokens 1000000
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
        from tlosure.eval import MAX_FUEL

        spec = _parse(FUELED_EDN.replace(":fuel 50", f":fuel {MAX_FUEL + 1}"))
        with pytest.raises(MemberSpecValidationError, match="hard cap"):
            validate_member_spec(spec)

    def test_loop_fuel_over_hard_cap_rejected(self):
        from tlosure.eval import MAX_FUEL

        edn = COGNITIVE_EDN.replace(
            ":max-iterations 10", f":max-iterations 10 :fuel {MAX_FUEL + 1}"
        )
        spec = _parse(edn)
        with pytest.raises(MemberSpecValidationError, match="hard cap"):
            validate_member_spec(spec)


class TestLoopMaxTokens:
    """:loop/:max-tokens (ADR 0023): session token budget — required int,
    rejected at load when missing or mistyped."""

    def test_max_tokens_missing_rejected(self):
        edn = COGNITIVE_EDN.replace("                 :max-tokens 1000000\n", "")
        with pytest.raises(MemberSpecValidationError, match=":max-tokens"):
            _parse(edn)

    def test_max_tokens_non_int_rejected(self):
        edn = COGNITIVE_EDN.replace(":max-tokens 1000000", ':max-tokens "many"')
        with pytest.raises(MemberSpecValidationError, match=":max-tokens"):
            _parse(edn)

    def test_max_tokens_bool_rejected(self):
        edn = COGNITIVE_EDN.replace(":max-tokens 1000000", ":max-tokens true")
        with pytest.raises(MemberSpecValidationError, match=":max-tokens"):
            _parse(edn)

    def test_max_tokens_roundtrip(self):
        spec = _parse(COGNITIVE_EDN)
        assert spec.to_dict()["rules"][0]["loop"]["max_tokens"] == 1000000
        assert _parse(spec.to_edn()) == spec


class TestLoopReplies:
    """:loop/:replies：成员可覆盖三条面向用户的终止回信措辞（纯数据，
    引擎内置文案兜底）。加载期 total 校验（同 stage opts 的严格先例）：
    未知键、非字符串、空串均拒载。"""

    REPLIES = (
        ':replies {:iteration-limit "想太久了，简单说。" :error "出错了。" :timeout "超时了。"}'
    )

    @staticmethod
    def _with_replies(replies: str) -> str:
        return COGNITIVE_EDN.replace(
            ":reflect {:enabled false :interval-minutes 5}",
            f":reflect {{:enabled false :interval-minutes 5}} {replies}",
        )

    def test_replies_roundtrip(self):
        spec = _parse(self._with_replies(self.REPLIES))
        loop = spec.rules[0].loop
        assert loop is not None
        assert loop.replies == {
            "iteration_limit": "想太久了，简单说。",
            "error": "出错了。",
            "timeout": "超时了。",
        }
        again = _parse(spec.to_edn())
        assert again == spec
        assert again.rules[0].loop is not None
        assert again.rules[0].loop.replies == loop.replies

    def test_replies_undeclared_defaults_empty(self):
        spec = _parse(COGNITIVE_EDN)
        assert spec.rules[0].loop is not None
        assert spec.rules[0].loop.replies == {}
        assert Keyword("replies") not in spec.to_edn_data()["rules"][0]["loop"]

    def test_replies_partial_override_roundtrip(self):
        spec = _parse(self._with_replies(':replies {:timeout "超时了。"}'))
        loop = spec.rules[0].loop
        assert loop is not None
        assert loop.replies == {"timeout": "超时了。"}
        assert _parse(spec.to_edn()) == spec

    def test_replies_non_map_rejected(self):
        with pytest.raises(MemberSpecValidationError, match=":replies must be a map"):
            _parse(self._with_replies(':replies "出错了。"'))

    def test_replies_unknown_key_rejected(self):
        with pytest.raises(MemberSpecValidationError, match="unknown :loop/:replies key"):
            _parse(self._with_replies(':replies {:on-limit "x"}'))

    def test_replies_empty_string_rejected(self):
        with pytest.raises(MemberSpecValidationError, match="non-empty string"):
            _parse(self._with_replies(':replies {:error ""}'))

    def test_replies_non_string_value_rejected(self):
        with pytest.raises(MemberSpecValidationError, match="non-empty string"):
            _parse(self._with_replies(":replies {:error 42}"))

    def test_replies_valid_spec_passes_validator(self):
        validate_member_spec(_parse(self._with_replies(self.REPLIES)))


class TestPhaseDActions:
    """Phase D: loop forms are valid action positions.

    ADR 0021 dropped the syntax purity partition: the action position
    accepts any expression the full member registry validates.
    """

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

    def test_action_in_loop_test_loads(self):
        """ADR 0021: no purity partition — an action inside a loop's test
        position validates like any other expression (action convergence
        is a runtime fact, not a static one)."""
        spec = _parse(
            """\
{:version 1
 :rules [{:name "wild"
          :on :mail-delivered
          :actions [(while [i 0] (emit "x") (recur (inc i)))]}]}
"""
        )
        validate_member_spec(spec)


class TestDefnThreading:
    """ADR 0021: defn names thread across a rule's :actions sequence
    (define-before-use), at load validation and in evaluation order."""

    DEFN_EDN = """\
{:version 1
 :rules [{:name "notify"
          :on :mail-delivered
          :actions [(defn notify [x] (emit x ["sink@tlon.internal"]))
                    (notify "hi")]}]}
"""

    def test_defn_define_before_use_loads(self):
        spec = _parse(self.DEFN_EDN)
        validate_member_spec(spec)
        assert len(spec.rules[0].actions) == 2

    def test_defn_use_before_define_rejected(self):
        spec = _parse(
            """\
{:version 1
 :rules [{:name "notify"
          :on :mail-delivered
          :actions [(notify "hi")
                    (defn notify [x] (emit x))]}]}
"""
        )
        with pytest.raises(
            MemberSpecValidationError, match="unknown function or special form: notify"
        ):
            validate_member_spec(spec)


class TestMailHistorySection:
    """``(mail-history N)``（滑动窗口）与 ``(mail-history F R)``（两段式，
    ADR 0025）均合法；参数个数、负数、bool、非 int 一律加载拒载。"""

    @staticmethod
    def _spec_with_sections(sections: str) -> MemberSpec:
        return _parse(COGNITIVE_EDN.replace("[(persona) (protocol)]", sections))

    def test_sliding_form_accepted(self):
        validate_member_spec(self._spec_with_sections("[(mail-history 20)]"))

    def test_two_tier_form_accepted(self):
        validate_member_spec(self._spec_with_sections("[(mail-history 20 10)]"))

    def test_zero_counts_accepted(self):
        """Zero is a legal non-negative count (renders nothing)."""
        validate_member_spec(self._spec_with_sections("[(mail-history 0 0)]"))

    def test_no_argument_rejected(self):
        with pytest.raises(MemberSpecValidationError, match="one or two"):
            validate_member_spec(self._spec_with_sections("[(mail-history)]"))

    def test_three_arguments_rejected(self):
        with pytest.raises(MemberSpecValidationError, match="one or two"):
            validate_member_spec(self._spec_with_sections("[(mail-history 20 10 5)]"))

    def test_negative_count_rejected(self):
        with pytest.raises(MemberSpecValidationError, match="non-negative int"):
            validate_member_spec(self._spec_with_sections("[(mail-history -1)]"))

    def test_negative_recent_count_rejected(self):
        with pytest.raises(MemberSpecValidationError, match="non-negative int"):
            validate_member_spec(self._spec_with_sections("[(mail-history 20 -1)]"))

    def test_bool_count_rejected(self):
        with pytest.raises(MemberSpecValidationError, match="non-negative int"):
            validate_member_spec(self._spec_with_sections("[(mail-history true)]"))

    def test_non_int_count_rejected(self):
        with pytest.raises(MemberSpecValidationError, match="non-negative int"):
            validate_member_spec(self._spec_with_sections('[(mail-history "20")]'))
