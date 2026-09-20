"""tlon's DSL domain vocabulary (``tlon/agents/vocabulary.py``, ADR 0018 修订).

The host layer over the tlosure language core: the mail predicates
(reading real :class:`Mail` objects and the injected ``self.address``),
the action family and the unified member registry
(:func:`make_member_builtins`), host query injection
(:class:`ScriptHostQueries`), and the action-position static gate
(:func:`validate_member_action`). The pure language semantics are tested
in ``src/tests/tlosure/test_eval.py``.
"""

import pytest

from tlon.agents.vocabulary import make_member_builtins, validate_member_action
from tlon.organization.mail.mail import Mail
from tlosure.edn import loads
from tlosure.eval import ActionRequest, EvalError, eval_form, validate_form

#: The unified member registry (pure predicates + arithmetic + actions).
_BUILTINS = make_member_builtins()

#: The pure subset, as ``member_spec.validate_member_spec`` derives it.
_PURE = {name: builtin for name, builtin in _BUILTINS.items() if builtin.pure}


def _mail(
    to: list[str] | None = None,
    cc: list[str] | None = None,
    from_address: str = "someone@tlon.internal",
    content: str = "hello",
) -> Mail:
    return Mail.create(from_address=from_address, content=content, to=to or [], cc=cc or [])


class TestMailPredicates:
    """Mail predicates see real ``Mail`` objects directly (ADR 0018 修订)."""

    def _env(self, msg: Mail | None) -> dict:
        return {"msg": msg, "self": {"address": "me@tlon.internal"}}

    def test_addressed_to_me(self):
        mine = _mail(to=["me@tlon.internal"])
        assert eval_form(loads("(addressed-to-me? msg)"), self._env(mine), _BUILTINS) is True
        other = _mail(to=["you@tlon.internal"])
        assert eval_form(loads("(addressed-to-me? msg)"), self._env(other), _BUILTINS) is False

    def test_cc_ed(self):
        mail = _mail(to=["you@tlon.internal"], cc=["me@tlon.internal"])
        assert eval_form(loads("(cc-ed? msg)"), self._env(mail), _BUILTINS) is True

    def test_non_mail_msg_is_never_addressed(self):
        assert eval_form(loads("(addressed-to-me? msg)"), self._env(None), _BUILTINS) is False
        assert eval_form(loads("(cc-ed? msg)"), self._env(None), _BUILTINS) is False

    def test_from(self):
        mail = _mail(from_address="postmaster@tlon.internal", to=["me@tlon.internal"])
        env = self._env(mail)
        assert (
            eval_form(loads('(from? msg "postmaster@tlon.internal")'), env, _BUILTINS) is True
        )
        assert eval_form(loads('(from? msg "alice@tlon.internal")'), env, _BUILTINS) is False

    def test_content_matches_mail_and_string(self):
        mail = _mail(to=["me@tlon.internal"], content="URGENT: reply needed")
        assert (
            eval_form(loads('(content-matches? msg "URGENT")'), self._env(mail), _BUILTINS)
            is True
        )
        # A plain string target (e.g. ``result``) works too.
        env = {"result": "all quiet", "self": {}}
        assert eval_form(loads('(content-matches? result "quiet")'), env, _BUILTINS) is True
        assert eval_form(loads('(content-matches? result "URGENT")'), env, _BUILTINS) is False

    def test_content_matches_none_is_false(self):
        assert (
            eval_form(loads('(content-matches? msg "x")'), self._env(None), _BUILTINS) is False
        )


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
            return "阿白（a-bai-1a2b3c4d）"

    def test_with_host_returns_roster(self):
        builtins = make_member_builtins(self._Host())
        assert eval_form(loads("(member-roster)"), {}, builtins) == "阿白（a-bai-1a2b3c4d）"

    def test_without_host_is_unregistered(self):
        assert "member-roster" not in _BUILTINS
        with pytest.raises(EvalError, match="unknown function: member-roster"):
            eval_form(loads("(member-roster)"), {}, _BUILTINS)


class TestActionCollection:
    """Phase D invariant: loop body actions are just more recorded requests."""

    def test_while_body_collects_each_emit(self):
        collector: list[ActionRequest] = []
        builtins = make_member_builtins(collector=collector)
        result = eval_form(
            loads('(while [i 0] (< i 3) (emit "x") (recur (inc i)))'), {}, builtins
        )
        assert result is None
        assert len(collector) == 3
        assert all(request.kind == "emit" for request in collector)

    def test_map_returns_request_list(self):
        # Deterministic rules collect the action's return value: a map of
        # emits is a list of requests, all of which reach the engine.
        result = eval_form(loads('(map i (emit "x") [1 2 3])'), {}, make_member_builtins())
        assert len(result) == 3
        assert all(request.kind == "emit" for request in result)


class TestValidateMemberAction:
    """The phase C action-position gate, in its host-layer home."""

    def test_single_action_with_pure_args(self):
        validate_member_action(
            loads('(emit "x" ["sink@tlon.internal"])'), pure=_PURE, scope={"msg", "self"}
        )

    def test_when_wraps_actions_under_pure_condition(self):
        validate_member_action(
            loads('(when (= 1 1) (emit "x") (spawn "proto"))'), pure=_PURE, scope=set()
        )

    def test_impure_condition_rejected(self):
        with pytest.raises(EvalError, match="unknown function or special form: spawn"):
            validate_member_action(
                loads('(when (spawn "proto") (emit "x"))'), pure=_PURE, scope=set()
            )

    def test_unknown_action_rejected(self):
        with pytest.raises(EvalError, match="unknown action"):
            validate_member_action(loads("(frobnicate 1)"), pure=_PURE, scope=set())


class TestValidateForm:
    """``validate_form`` against the member registry (mail predicates are
    host vocabulary — the core suite uses the general vocabulary)."""

    def test_accepts_legit_expression(self):
        form = loads('(and (addressed-to-me? msg) (content-matches? msg "hi"))')
        validate_form(form, _BUILTINS, scope={"msg", "self"})

    def test_rejects_non_whitelisted_form(self):
        # A genuinely unknown form is rejected; action names are valid
        # (impure) expressions here — purity is enforced at the member
        # action layer (validate_member_action), not in validate_form.
        with pytest.raises(EvalError, match="unknown function or special form: frobnicate"):
            validate_form(loads("(frobnicate msg)"), _BUILTINS, scope={"msg", "self"})

    def test_rejects_undefined_symbol(self):
        with pytest.raises(EvalError, match="undefined symbol: foo"):
            validate_form(loads("(addressed-to-me? foo)"), _BUILTINS, scope={"msg", "self"})

    def test_let_extends_scope(self):
        validate_form(loads("(let [x msg] (addressed-to-me? x))"), _BUILTINS, scope={"msg"})
        with pytest.raises(EvalError, match="undefined symbol: y"):
            validate_form(loads("(let [x 1] (addressed-to-me? y))"), _BUILTINS, scope={"msg"})
