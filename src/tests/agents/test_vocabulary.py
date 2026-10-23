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
    make_member_builtins,
)
from tlon.organization.mail.mail import Mail
from tlosure.edn import loads
from tlosure.eval import EvalError, eval_form

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
                _BUILTINS,
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
