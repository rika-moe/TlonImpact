"""critique stage verdict semantics (docs/design/runtime.md).

A legal PASS/FAIL verdict is declared to the loop scratchpad as before.
An unparseable verdict is "we don't know the review outcome": it is NOT
recorded as PASS (fail-open) — the critique is skipped like a soft
failure (no verdict in scratch, so neither explicit true nor false comparison matches and revise is not triggered), with a warning
log as the visible signal.
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from tlon.agents.session import AgentSession
from tlon.agents.stages.critique import critique_stage
from tlon.agents.stages.revise import revise_stage
from tlon.agents.vocabulary import condition_builtins, make_member_builtins
from tlon.base.llm_client import ContextWindowExceededError, LLMResponse, ToolCall
from tlon.tools.base import ToolResult
from tlosure.edn import loads
from tlosure.eval import eval_form

_CONDITION_BUILTINS = condition_builtins(make_member_builtins())


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

        # Not PASS, not REVISE: clear any previous verdict.
        assert outcome.response is response
        assert outcome.scratch_updates == {"critique": None}
        assert session.loop_scratch == {}
        svc.logger.warning.assert_called_once()
        assert svc.logger.warning.call_args.args[0] == "critique_verdict_unparseable"

    async def test_empty_verdict_skips_with_warning(self) -> None:
        svc = _make_services("")
        session = _make_session()

        outcome = await critique_stage(svc, session, None)

        assert outcome.scratch_updates == {"critique": None}
        svc.logger.warning.assert_called_once()
        assert svc.logger.warning.call_args.args[0] == "critique_verdict_unparseable"

    @pytest.mark.parametrize(
        "verdict", ["PASSING", "PASS但没有核实", "PASS 只是猜测", "FAILED", "FAIL缺少来源"]
    )
    async def test_verdict_prefix_is_not_a_valid_verdict(self, verdict: str) -> None:
        svc = _make_services(verdict)
        session = _make_session()
        session.loop_scratch["critique"] = {"passed": True, "feedback": ""}

        outcome = await critique_stage(svc, session, None)

        assert outcome.scratch_updates == {"critique": None}
        session.loop_scratch.update(outcome.scratch_updates)
        env = {"scratch": session.loop_scratch}
        assert not eval_form(
            loads("(= (get (get scratch :critique) :passed) true)"), env, _CONDITION_BUILTINS
        )
        assert not eval_form(
            loads("(= (get (get scratch :critique) :passed) false)"), env, _CONDITION_BUILTINS
        )
        assert svc.logger.warning.call_args.args[0] == "critique_verdict_unparseable"

    async def test_skipped_critique_does_not_trigger_revise(self) -> None:
        """End-to-end of the new semantics: an unparseable verdict leaves
        no scratch entry, so the downstream revise stage is a no-op."""
        svc = _make_services("……")
        session = _make_session()

        outcome = await critique_stage(svc, session, None)
        session.loop_scratch.update(outcome.scratch_updates)
        revise_outcome = await revise_stage(svc, session, None)

        assert session.loop_scratch["critique"] is None
        assert revise_outcome.context_appends == []

    @pytest.mark.parametrize("previous_passed", [True, False])
    @pytest.mark.parametrize("failure", ["unknown", "error"])
    async def test_failed_review_clears_previous_verdict(
        self, previous_passed: bool, failure: str
    ) -> None:
        svc = _make_services("未知")
        if failure == "error":
            svc.think.side_effect = RuntimeError("provider unavailable")
        session = _make_session()
        previous = {"passed": previous_passed, "feedback": "旧评审"}
        session.loop_scratch["critique"] = previous
        response = LLMResponse(content="新草稿")

        outcome = await critique_stage(svc, session, response)

        assert outcome.response is response
        assert outcome.scratch_updates == {"critique": None}
        assert session.loop_scratch["critique"] == previous  # pure stage
        session.loop_scratch.update(outcome.scratch_updates)
        env = {"scratch": session.loop_scratch}
        assert not eval_form(
            loads("(= (get (get scratch :critique) :passed) true)"), env, _CONDITION_BUILTINS
        )
        assert not eval_form(
            loads("(= (get (get scratch :critique) :passed) false)"), env, _CONDITION_BUILTINS
        )
        svc.logger.warning.assert_called_once()

    async def test_context_overflow_propagates_for_engine_retry(self) -> None:
        svc = _make_services("unused")
        svc.think.side_effect = ContextWindowExceededError("too long")

        with pytest.raises(ContextWindowExceededError):
            await critique_stage(svc, _make_session(), None)


class TestCritiqueReviewInput:
    """The reviewer sees the round's pending draft (the pre-submission
    gate reviews the reply about to be sent, not just the context)."""

    async def test_custom_instructions_keep_verdict_protocol(self) -> None:
        svc = _make_services("FAIL\n缺少计算依据")
        session = _make_session()
        instructions = "检查来源、计算或工具结果；区分事实、推断与未核实事项。"

        outcome = await critique_stage(svc, session, None, {"instructions": instructions})

        prompt = svc.think.await_args.args[0][-1]["content"]
        assert instructions in prompt
        assert "充分回应了当前邮件" in prompt
        assert prompt.endswith("第一行只写 PASS 或 FAIL；FAIL 时从第二行起给出具体改进意见。")
        assert outcome.scratch_updates == {
            "critique": {"passed": False, "feedback": "缺少计算依据"}
        }

    async def test_pending_draft_is_included_in_review_input(self) -> None:
        svc = _make_services("PASS")
        session = _make_session()
        session.context.append({"role": "user", "content": "来信正文"})
        response = LLMResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="c1",
                    function_name="mail-send",
                    arguments={"content": "草稿回复"},
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
        assert "mail-send" in messages[-2]["content"]
        assert "邮件尚未发送" in messages[-2]["content"]
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
    @pytest.mark.parametrize("verdict", [None, {"passed": True, "feedback": ""}])
    async def test_pass_or_skip_does_not_repeat_draft(
        self, verdict: dict[str, object] | None
    ) -> None:
        session = _make_session()
        session.loop_scratch["critique"] = verdict
        response = LLMResponse(
            content=None,
            tool_calls=[
                ToolCall(id="c1", function_name="mail-send", arguments={"content": "正文"})
            ],
        )

        outcome = await revise_stage(_make_services("unused"), session, response)

        assert outcome.context_appends == []
        assert outcome.scratch_updates == {}
        assert session.loop_scratch["critique"] == verdict

    @pytest.mark.parametrize("feedback", ["前函已经发出，需更正", ""])
    async def test_blocked_mail_drafts_survive_as_plain_text(self, feedback: str) -> None:
        svc = _make_services(f"FAIL\n{feedback}")
        session = _make_session()
        session.context = [{"role": "user", "content": "来信"}]
        calls = [
            ToolCall(
                id=f"draft-{index}",
                function_name="mail-send",
                arguments={
                    "to": [f"agent-{index}@tlon"],
                    "cc": ["observer@tlon"],
                    "subject": f"主题 {index}",
                    "in_reply_to": f"original-{index}",
                    "content": f"完整草稿 {index}\n第二段正文",
                },
            )
            for index in range(2)
        ]
        response = LLMResponse(content="本轮说明", tool_calls=calls)
        review = await critique_stage(svc, session, response)
        session.loop_scratch.update(review.scratch_updates)

        outcome = await revise_stage(svc, session, response)

        assert outcome.response is response
        assert outcome.scratch_updates == {"critique": None}
        assert len(outcome.context_appends) == 1
        message = outcome.context_appends[0]
        assert set(message) == {"role", "content"}
        assert message["role"] == "user"
        for call in calls:
            for rendered in (svc.think.await_args.args[0][-2]["content"], message["content"]):
                assert call.id in rendered
                assert call.function_name in rendered
                assert json.dumps(call.arguments, ensure_ascii=False) in rendered
                assert "邮件尚未发送" in rendered
        assert "已被评审拦截" in message["content"]
        assert "评审意见不构成执行回执" in message["content"]
        assert session.context == [{"role": "user", "content": "来信"}]
        assert session.loop_scratch == {"critique": {"passed": False, "feedback": feedback}}
        assert session.all_tool_results == []
        assert not session.has_pending_tools
        assert outcome.tool_dispatches == []
        assert outcome.tool_results == []

    @pytest.mark.parametrize(
        "result",
        [
            ToolResult(success=True, data={"mail_id": "sent-mail"}),
            ToolResult(success=False, data={}, error="拒收"),
            ToolResult(success=False, data={"delivery_status": "unknown"}, error="timeout"),
        ],
    )
    async def test_post_act_review_preserves_actual_receipt(self, result: ToolResult) -> None:
        svc = _make_services("FAIL\n请核查")
        session = _make_session()
        call = ToolCall(id="executed", function_name="mail-send", arguments={"content": "正文"})
        session.add_tool_result(call, result)
        response = LLMResponse(content=None, tool_calls=[call])

        review = await critique_stage(svc, session, response)
        session.loop_scratch.update(review.scratch_updates)
        revision = await revise_stage(svc, session, response)

        for rendered in (
            svc.think.await_args.args[0][-2]["content"],
            revision.context_appends[0]["content"],
        ):
            assert "已收集工具回执" in rendered
            assert json.dumps(result.to_dict(), ensure_ascii=False) in rendered
            assert "邮件尚未发送" not in rendered
            assert "已被评审拦截" not in rendered
        assert session.all_tool_results == [{"tool_call": call, "result": result}]

    @pytest.mark.parametrize("done", [False, True])
    async def test_dispatched_uncollected_call_is_not_an_unsent_draft(self, done: bool) -> None:
        svc = _make_services("FAIL")
        session = _make_session()
        call = ToolCall(id="pending", function_name="mail-send", arguments={"content": "正文"})
        response = LLMResponse(content=None, tool_calls=[call])
        gate = asyncio.Event()

        async def execute() -> ToolResult:
            await gate.wait()
            return ToolResult(success=True, data={"mail_id": "actual-mail"})

        task = asyncio.create_task(execute())
        session.dispatch_tools([call], {call.id: task})
        try:
            if done:
                gate.set()
                await task
            review = await critique_stage(svc, session, response)
            session.loop_scratch.update(review.scratch_updates)
            revision = await revise_stage(svc, session, response)

            for rendered in (
                svc.think.await_args.args[0][-2]["content"],
                revision.context_appends[0]["content"],
            ):
                assert "已派发，结果尚未收集" in rendered
                assert "邮件尚未发送" not in rendered
                assert "已被评审拦截" not in rendered
            assert session.get_pending_tasks() == {call.id: task}
            assert session.all_tool_results == []
        finally:
            await session.cancel_pending()

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
