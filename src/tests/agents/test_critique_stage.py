"""critique stage verdict semantics (issue ).

A legal PASS/FAIL verdict is declared to the loop scratchpad as before.
An unparseable verdict is "we don't know the review outcome": it is NOT
recorded as PASS (fail-open) — the critique is skipped like a soft
failure (no verdict in scratch, so neither ``critique-passed?`` nor
``critique-failed?`` fires and revise is not triggered), with a warning
log as the visible signal.
"""

from unittest.mock import AsyncMock, MagicMock

from tlon.agents.session import AgentSession
from tlon.agents.stages.critique import critique_stage
from tlon.agents.stages.revise import revise_stage
from tlon.base.llm_client import LLMResponse


def _make_services(verdict_text: str) -> MagicMock:
    svc = MagicMock()
    svc.logger = MagicMock()
    svc.publish_event = AsyncMock()
    svc.think = AsyncMock(return_value=LLMResponse(content=verdict_text))
    return svc


def _make_session() -> AgentSession:
    return AgentSession(session_id="s1", trigger_content="trigger")


class TestCritiqueVerdict:
    async def test_pass_verdict_declares_passed(self) -> None:
        svc = _make_services("PASS")
        session = _make_session()
        response = LLMResponse(content="round output")

        outcome = await critique_stage(svc, session, response)

        assert outcome.response is response
        assert outcome.scratch_updates == {"critique": {"passed": True, "feedback": ""}}
        # Declared, not written: the scratchpad is the engine's job.
        assert session.loop_scratch == {}
        svc.logger.warning.assert_not_called()

    async def test_fail_verdict_declares_feedback(self) -> None:
        svc = _make_services("FAIL\n没有回答来信的问题\n缺少结论")
        session = _make_session()

        outcome = await critique_stage(svc, session, None)

        assert outcome.scratch_updates == {
            "critique": {"passed": False, "feedback": "没有回答来信的问题\n缺少结论"}
        }
        svc.logger.warning.assert_not_called()

    async def test_unparseable_verdict_skips_with_warning(self) -> None:
        svc = _make_services("我觉得写得还行吧")
        session = _make_session()
        response = LLMResponse(content="round output")

        outcome = await critique_stage(svc, session, response)

        # Not PASS, not REVISE: no verdict is declared at all.
        assert outcome.response is response
        assert outcome.scratch_updates == {}
        assert session.loop_scratch == {}
        svc.logger.warning.assert_called_once()
        assert svc.logger.warning.call_args.args[0] == "critique_verdict_unparseable"

    async def test_empty_verdict_skips_with_warning(self) -> None:
        svc = _make_services("")
        session = _make_session()

        outcome = await critique_stage(svc, session, None)

        assert outcome.scratch_updates == {}
        svc.logger.warning.assert_called_once()
        assert svc.logger.warning.call_args.args[0] == "critique_verdict_unparseable"

    async def test_skipped_critique_does_not_trigger_revise(self) -> None:
        """End-to-end of the new semantics: an unparseable verdict leaves
        no scratch entry, so the downstream revise stage is a no-op."""
        svc = _make_services("……")
        session = _make_session()

        outcome = await critique_stage(svc, session, None)
        session.loop_scratch.update(outcome.scratch_updates)
        revise_outcome = await revise_stage(svc, session, None)

        assert "critique" not in session.loop_scratch
        assert revise_outcome.context_appends == []
