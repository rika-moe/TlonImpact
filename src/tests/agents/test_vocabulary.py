"""tlon's DSL domain vocabulary (``tlon/agents/vocabulary.py``, ADR 0018 修订).

The host layer over the tlosure language core: the mail predicates
(reading explicit mail records and a supplied ``self.address``),
the action family and the unified member registry
(:func:`make_member_builtins`), host query injection
(:class:`ScriptHostQueries`), and the action-position static gate
(:func:`validate_member_actions`, ADR 0021). The pure language semantics
are tested in ``src/tests/tlosure/test_eval.py``.
"""

import pytest

from tlon.agents.vocabulary import (
    condition_builtins,
    make_member_builtins,
    stage_cond_env,
    validate_member_actions,
)
from tlon.organization.mail.mail import Mail
from tlosure.edn import loads
from tlosure.eval import EvalError, eval_form, validate_form

_CONDITION_BUILTINS = condition_builtins(make_member_builtins())

#: The unified member registry (pure predicates + arithmetic + actions) —
#: the full registry the ADR 0021 action gate validates against.
_BUILTINS = make_member_builtins()


def _mail(
    to: list[str] | None = None,
    cc: list[str] | None = None,
    from_address: str = "someone@tlon.internal",
    content: str = "hello",
) -> Mail:
    return Mail.create(from_address=from_address, content=content, to=to or [], cc=cc or [])


class TestMailPredicates:
    """Mail predicates receive explicit materialized mail records."""

    def _env(self, msg: Mail | None) -> dict:
        return {"msg": msg.to_dict() if msg else None, "self": {"address": "me@tlon.internal"}}

    def test_addressed_to_me(self):
        mine = _mail(to=["me@tlon.internal"])
        assert (
            eval_form(
                loads("(contains? (or (mail-field msg :to) []) (get self :address))"),
                self._env(mine),
                _BUILTINS,
            )
            is True
        )
        other = _mail(to=["you@tlon.internal"])
        assert (
            eval_form(
                loads("(contains? (or (mail-field msg :to) []) (get self :address))"),
                self._env(other),
                _BUILTINS,
            )
            is False
        )

    def test_cc_ed(self):
        mail = _mail(to=["you@tlon.internal"], cc=["me@tlon.internal"])
        assert (
            eval_form(
                loads("(contains? (or (mail-field msg :cc) []) (get self :address))"),
                self._env(mail),
                _BUILTINS,
            )
            is True
        )

    def test_missing_self_is_an_explicit_error(self) -> None:
        with pytest.raises(EvalError, match="undefined symbol: self"):
            eval_form(
                loads("(contains? (or (mail-field msg :to) []) (get self :address))"),
                {"msg": _mail(to=["me@tlon.internal"]).to_dict()},
                _CONDITION_BUILTINS,
            )

    def test_from(self):
        mail = _mail(from_address="postmaster@tlon.internal", to=["me@tlon.internal"])
        env = self._env(mail)
        assert eval_form(loads('(from? msg "postmaster@tlon.internal")'), env, _BUILTINS) is True
        assert eval_form(loads('(from? msg "alice@tlon.internal")'), env, _BUILTINS) is False

    def test_content_matches_mail_and_string(self):
        mail = _mail(to=["me@tlon.internal"], content="URGENT: reply needed")
        assert (
            eval_form(loads('(content-matches? msg "URGENT")'), self._env(mail), _BUILTINS) is True
        )
        # A plain string target (e.g. ``result``) works too.
        env = {"result": "all quiet", "self": {}}
        assert eval_form(loads('(content-matches? result "quiet")'), env, _BUILTINS) is True
        assert eval_form(loads('(content-matches? result "URGENT")'), env, _BUILTINS) is False

    def test_content_matches_none_is_false(self):
        assert eval_form(loads('(content-matches? msg "x")'), self._env(None), _BUILTINS) is False


class TestMemberRoster:
    """Issue : the host-injected social roster query (same injection
    pattern as ``sleeping?`` — absent from the host-less registry)."""

    class _Host:
        def is_sleeping(self, agent_id: object) -> bool:
            return False

        def is_running(self, agent_id: object) -> bool:
            return True

        def is_lazy(self, agent_id: object) -> bool:
            return False

        def start_all_candidates(self) -> list[str]:
            return []

        def member_roster(self) -> str:
            return "阿白（a-bai-1a2b3c4d） — a-bai-1a2b3c4d@tlon.internal"

    def test_with_host_returns_roster(self):
        builtins = make_member_builtins(self._Host())
        assert (
            eval_form(loads("(member-roster)"), {}, builtins)
            == "阿白（a-bai-1a2b3c4d） — a-bai-1a2b3c4d@tlon.internal"
        )

    def test_without_host_is_unregistered(self):
        assert "member-roster" not in _BUILTINS
        with pytest.raises(EvalError, match="unknown function: member-roster"):
            eval_form(loads("(member-roster)"), {}, _BUILTINS)


class TestValidateMemberActions:
    """The ADR 0021 action-position gate, in its host-layer home.

    The syntax purity partition is gone: any expression may sit in
    action position (a function body can carry actions, so "where
    actions may occur" is not statically enumerable). What remains is
    full-registry validation — unregistered vocabulary and undefined
    symbols are still rejected at load — plus sequential ``defn``
    threading across the form sequence (define-before-use).
    """

    def test_direct_action_form_passes(self):
        validate_member_actions(
            (loads('(mail-send {:to ["sink@tlon.internal"] :content "x"})'),),
            registry=_BUILTINS,
            scope={"msg", "self"},
        )

    def test_any_expression_is_a_valid_action_position(self):
        # if / let / pure computation — everything the full member
        # registry validates is legal in action position now (the old
        # partition admitted only when/while/map wrappers).
        validate_member_actions(
            (
                loads(
                    '(if (contains? (or (mail-field msg :to) []) (get self :address)) (mail-send {:to ["sink@tlon.internal"] :content "a"}) (mail-send {:to ["sink@tlon.internal"] :content "b"}))'
                ),
                loads('(let [x "hi"] (mail-send {:to ["sink@tlon.internal"] :content x}))'),
                loads('(str "just" " compute")'),
            ),
            registry=_BUILTINS,
            scope={"msg", "self"},
        )

    def test_unregistered_vocabulary_still_rejected(self):
        with pytest.raises(EvalError, match="unknown function or special form: frobnicate"):
            validate_member_actions((loads("(frobnicate 1)"),), registry=_BUILTINS, scope=set())

    def test_undefined_symbol_still_rejected(self):
        with pytest.raises(EvalError, match="undefined symbol: foo"):
            validate_member_actions(
                (loads('(mail-send {:to ["sink@tlon.internal"] :content foo})'),),
                registry=_BUILTINS,
                scope=set(),
            )

    def test_defn_threads_to_later_forms(self):
        validate_member_actions(
            (
                loads('(defn notify [x] (mail-send {:to ["sink@tlon.internal"] :content x}))'),
                loads('(notify "hi")'),
            ),
            registry=_BUILTINS,
            scope=set(),
        )

    def test_defn_use_before_define_rejected(self):
        with pytest.raises(EvalError, match="unknown function or special form: notify"):
            validate_member_actions(
                (
                    loads('(notify "hi")'),
                    loads('(defn notify [x] (mail-send {:to ["sink@tlon.internal"] :content x}))'),
                ),
                registry=_BUILTINS,
                scope=set(),
            )


class TestValidateForm:
    """``validate_form`` against the member registry (mail predicates are
    host vocabulary — the core suite uses the general vocabulary)."""

    def test_accepts_legit_expression(self):
        form = loads(
            '(and (contains? (or (mail-field msg :to) []) (get self :address)) (content-matches? msg "hi"))'
        )
        validate_form(form, _BUILTINS, scope={"msg", "self"})

    def test_rejects_non_whitelisted_form(self):
        # A genuinely unknown form is rejected; action names are valid
        # (impure) expressions here — action position is validated
        # against the full registry (validate_member_actions, ADR 0021),
        # which is validate_form plus defn threading.
        with pytest.raises(EvalError, match="unknown function or special form: frobnicate"):
            validate_form(loads("(frobnicate msg)"), _BUILTINS, scope={"msg", "self"})

    def test_rejects_undefined_symbol(self):
        with pytest.raises(EvalError, match="undefined symbol: foo"):
            validate_form(
                loads("(contains? (mail-field foo :to) (get self :address))"),
                _BUILTINS,
                scope={"msg", "self"},
            )

    def test_let_extends_scope(self):
        validate_form(
            loads('(let [x msg] (contains? (mail-field x :to) "me@tlon.internal"))'),
            _BUILTINS,
            scope={"msg"},
        )
        with pytest.raises(EvalError, match="undefined symbol: y"):
            validate_form(
                loads('(let [x 1] (contains? (mail-field y :to) "me@tlon.internal"))'),
                _BUILTINS,
                scope={"msg"},
            )


class TestExplicitTokenLimit:
    @pytest.mark.parametrize("used,expected", [(999, False), (1000, True), (1500, True)])
    def test_budget_comparison(self, used: int, expected: bool) -> None:
        assert (
            eval_form(
                loads("(>= (get state :token_usage) (get limits :max_tokens))"),
                {"state": {"token_usage": used}, "limits": {"max_tokens": 1000}},
                _CONDITION_BUILTINS,
            )
            is expected
        )

    def test_missing_scope_is_an_explicit_error(self) -> None:
        expression = loads("(>= (get state :token_usage) (get limits :max_tokens))")
        with pytest.raises(EvalError, match="undefined symbol: state"):
            eval_form(expression, {}, _CONDITION_BUILTINS)
        with pytest.raises(EvalError, match="undefined symbol: state"):
            validate_form(expression, _CONDITION_BUILTINS, scope=set())


class TestIndependentRoundFacts:
    @pytest.mark.parametrize("sending,ending", [(False, False), (True, False), (False, True)])
    def test_mail_and_completion_conditions_are_independent(
        self, sending: bool, ending: bool
    ) -> None:
        env = dict(
            stage_cond_env(
                iteration=0,
                error_count=0,
                token_usage=0,
                scratch={},
                completing=ending,
                sending_mail=sending,
            )
        )
        assert eval_form(loads("(get round :sending_mail)"), env, _CONDITION_BUILTINS) is sending
        assert eval_form(loads("(get round :completing)"), env, _CONDITION_BUILTINS) is ending

    def test_absent_round_is_an_explicit_error(self) -> None:
        for expression in ["(get round :sending_mail)", "(get round :completing)"]:
            with pytest.raises(EvalError, match="undefined symbol: round"):
                eval_form(loads(expression), {}, _CONDITION_BUILTINS)
