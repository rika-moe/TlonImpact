"""critique stage verdict semantics (docs/design/runtime.md).

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
from tlon.base.llm_client import LLMResponse, ToolCall


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


class TestCritiqueReviewInput:
    """The reviewer sees the round's pending draft (the pre-submission
    gate reviews the reply about to be sent, not just the context)."""

    async def test_pending_draft_is_included_in_review_input(self) -> None:
        svc = _make_services("PASS")
        session = _make_session()
        session.context.append({"role": "user", "content": "来信正文"})
        response = LLMResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="c1",
                    function_name="sendEmail",
                    arguments={"content": "草稿回复", "is_complete": True},
                )
            ],
        )

        await critique_stage(svc, session, response)

        messages = svc.think.await_args.args[0]
        # The draft precedes the review instruction; the session context
        # itself is untouched (the render is review-input only).
        assert messages[0] == {"role": "user", "content": "来信正文"}
        assert messages[-2]["role"] == "assistant"
        assert "草稿回复" in messages[-2]["content"]
        assert "sendEmail" in messages[-2]["content"]
        assert session.context == [{"role": "user", "content": "来信正文"}]

    async def test_no_pending_response_keeps_context_only_input(self) -> None:
        svc = _make_services("PASS")
        session = _make_session()
        session.context.append({"role": "user", "content": "来信正文"})

        await critique_stage(svc, session, None)

        messages = svc.think.await_args.args[0]
        assert len(messages) == 2  # context + review instruction
        assert messages[-1]["role"] == "user"


class TestReviseConsumesVerdict:
    async def test_feedback_appended_and_verdict_cleared(self) -> None:
        svc = _make_services("unused")
        session = _make_session()
        session.loop_scratch["critique"] = {"passed": False, "feedback": "缺少结论"}

        outcome = await revise_stage(svc, session, None)

        assert outcome.context_appends[0]["content"].startswith("[评审反馈]")
        assert "缺少结论" in outcome.context_appends[0]["content"]
        # The consumed verdict is declared cleared, so a later round where
        # critique is skipped cannot re-fire revise on a stale FAIL.
        assert outcome.scratch_updates == {"critique": None}
        session.loop_scratch.update(outcome.scratch_updates)
        followup = await revise_stage(svc, session, None)
        assert followup.context_appends == []

    async def test_no_feedback_stays_noop(self) -> None:
        svc = _make_services("unused")
        session = _make_session()
        session.loop_scratch["critique"] = {"passed": True, "feedback": ""}

        outcome = await revise_stage(svc, session, None)

        assert outcome.context_appends == []
        assert outcome.scratch_updates == {}
