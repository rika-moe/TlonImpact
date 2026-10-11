"""Context pressure management (ADR 0008): estimate, prune, summary, replay.

Pins the token heuristic and threshold formula; the two relief grades
(prune first, LLM summary as fallback, running summary rolling forward,
summarizer failure degrading to a placeholder); the journal invariant
(compaction is journaled with the full post-compaction context before the
in-memory session changes, and replaying the log reproduces the compacted
context); and the emergency path (a context-length 4xx is never retried
by the client; the driver force-compacts and retries the stage once).
"""

import contextlib
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import litellm
import pytest

from tlon.agents.context_builder import BuiltContext
from tlon.agents.context_pressure import (
    PRUNED_PLACEHOLDER,
    SUMMARY_MARKER,
    ContextPressure,
    compaction_threshold,
    estimate_tokens,
)
from tlon.agents.loop_driver import LoopDriver
from tlon.agents.member_spec import MemberSpec, validate_member_spec
from tlon.agents.session import AgentSession
from tlon.agents.session_journal import SessionJournal
from tlon.agents.trigger import AgentIdentity
from tlon.base.config import AgentConfig, LLMConfig
from tlon.base.llm_client import ContextWindowExceededError, LLMClient, LLMResponse
from tlon.base.paths import TlonPaths
from tlon.tools.spill import ToolSpill
from tlosure.edn import loads

#: Small window for tests: threshold = max(1000 - 40000, 800) = 800.
TEST_WINDOW = 1000
TEST_THRESHOLD = 800

COGNITIVE_EDN = """\
{:version 1
 :rules [{:name "respond"
          :on :mail-delivered
          :loop {:context {:sections [(persona) (protocol)]}
                 :stages [(think) (act)]
                 :until (or (iteration-limit-reached?) (error-limit-reached?))
                 :max-iterations 10
                 :max-consecutive-errors 3
                 :max-tokens 1000000
                 :reflect {:enabled false :interval-minutes 5}}}]}
"""


@pytest.fixture
def journal(tmp_path: Path) -> SessionJournal:
    return SessionJournal("agent1", TlonPaths(home=tmp_path))


def _make_pressure(
    journal: SessionJournal, llm_client: object = None, context_window: int = TEST_WINDOW
) -> ContextPressure:
    return ContextPressure(llm_client or MagicMock(), journal, context_window)  # type: ignore[arg-type]


def _user(content: str) -> dict[str, Any]:
    return {"role": "user", "content": content}


def _assistant_call(tc_id: str) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": tc_id,
                "type": "function",
                "function": {"name": "someTool", "arguments": "{}"},
            }
        ],
    }


def _tool(tc_id: str, content: str) -> dict[str, Any]:
    return {"role": "tool", "tool_call_id": tc_id, "content": content}


def _session(context: list[dict[str, Any]], anchor_len: int) -> AgentSession:
    session = AgentSession(session_id="agent1-s1", trigger_content="t")
    session.context = context
    session.anchor_len = anchor_len
    return session


async def _compact_events(journal: SessionJournal, session_id: str) -> list[dict[str, Any]]:
    return [
        event
        for event in await journal.read_events(session_id)
        if event.get("type") == "context/compact"
    ]


# ── Token estimation & threshold ────────────────────────────────────────


class TestEstimate:
    def test_pure_english_counts_four_chars_per_token(self) -> None:
        messages = [_user("hello world")]
        text = json.dumps(messages, ensure_ascii=False)
        assert estimate_tokens(messages) == (len(text) + 3) // 4

    def test_pure_chinese_counts_one_token_per_char(self) -> None:
        messages = [_user("你好世界")]
        text = json.dumps(messages, ensure_ascii=False)
        non_cjk = len(text) - 4
        assert estimate_tokens(messages) == 4 + (non_cjk + 3) // 4

    def test_mixed_content(self) -> None:
        mixed = [_user("你好 ab"), _tool("c1", "结果 ok")]
        assert estimate_tokens(mixed) > estimate_tokens([_user("abcd ab"), _tool("c1", "ab ok")])
        # Exact: every CJK char contributes 1, the rest ceil(chars/4).
        text = json.dumps(mixed, ensure_ascii=False)
        cjk = sum(1 for ch in text if "一" <= ch <= "鿿")
        assert estimate_tokens(mixed) == cjk + (len(text) - cjk + 3) // 4

    def test_threshold_formula(self) -> None:
        assert compaction_threshold(262144) == 262144 - 40_000
        assert compaction_threshold(32768) == int(32768 * 0.8)
        assert compaction_threshold(TEST_WINDOW) == TEST_THRESHOLD


# ── Grade 1: prune ──────────────────────────────────────────────────────


class TestPrune:
    def _context_over_threshold(self) -> list[dict[str, Any]]:
        """anchor(2) + middle(assistant + 2 big tools) + keep window(20)."""
        middle = [
            _assistant_call("c1"),
            _tool("c1", "x" * 4000),
            _tool("c1", "y" * 4000),
        ]
        window = [_user(f"recent {i}") for i in range(20)]
        return [_user("knowledge"), _user("触发邮件"), *middle, *window]

    async def test_under_threshold_is_noop(self, journal: SessionJournal) -> None:
        session = _session([_user("small")], anchor_len=1)
        pressure = _make_pressure(journal)

        assert await pressure.maybe_compact(session) is False
        assert session.context == [_user("small")]
        assert await _compact_events(journal, session.id) == []

    async def test_prune_replaces_old_tool_results(
        self, journal: SessionJournal, tmp_path: Path
    ) -> None:
        context = self._context_over_threshold()
        original_anchor = [dict(m) for m in context[:2]]
        original_window = [dict(m) for m in context[-20:]]
        session = _session(context, anchor_len=2)
        llm_client = MagicMock()
        pressure = _make_pressure(journal, llm_client=llm_client)

        # Journal-before-memory invariant: at append time the session must
        # still hold the pre-compaction context.
        seen: dict[str, Any] = {}
        original_append = journal.append

        async def recording_append(session_id: str, event_type: str, **payload: Any) -> None:
            if event_type == "context/compact":
                seen["memory_at_journal_time"] = session.context
                seen["journaled"] = payload
            await original_append(session_id, event_type, **payload)

        journal.append = recording_append  # type: ignore[method-assign]

        assert await pressure.maybe_compact(session) is True

        # Anchor and keep window are untouched.
        assert session.context[:2] == original_anchor
        assert session.context[-20:] == original_window
        # Middle tool results are placeholders; the pairing metadata stays.
        assert session.context[3]["content"] == PRUNED_PLACEHOLDER
        assert session.context[3]["tool_call_id"] == "c1"
        assert session.context[4]["content"] == PRUNED_PLACEHOLDER
        assert session.context[2]["tool_calls"]  # assistant pairing intact
        # Prune sufficed: the LLM was never called.
        llm_client.chat.assert_not_called()
        # Journaled first, memory second.
        assert seen["memory_at_journal_time"] is context  # old list object
        assert seen["journaled"]["context"] == session.context
        event = seen["journaled"]
        assert event["kind"] == "prune"
        assert event["reason"] == "threshold_exceeded"
        assert event["tokens_before"] > TEST_THRESHOLD
        assert event["tokens_after"] <= TEST_THRESHOLD
        # The log re-derives exactly the compacted context.
        assert await journal.derive_context(session.id) == session.context

    async def test_tool_pair_never_split_at_window_edge(self, journal: SessionJournal) -> None:
        # The natural window start lands ON a tool message: the window
        # extends back to its assistant, so this pair is kept whole.
        anchor = [_user("knowledge"), _user("触发邮件")]
        middle = [_assistant_call("c1"), _tool("c1", "x" * 4000)]
        edge = [_assistant_call("c2"), _tool("c2", "y" * 100)]
        tail = [_user(f"recent {i}") for i in range(19)]
        context = [*anchor, *middle, *edge, *tail]
        assert context[-20]["role"] == "tool"  # window starts on tool c2
        session = _session(context, anchor_len=2)

        assert await _make_pressure(journal).maybe_compact(session) is True

        # The edge pair joined the keep window: c2's result is NOT pruned.
        c2_messages = [m for m in session.context if m.get("tool_call_id") == "c2"]
        assert c2_messages[0]["content"] == "y" * 100
        # The middle pair was pruned (both halves still present).
        assert session.context[2]["tool_calls"][0]["id"] == "c1"
        assert session.context[3]["content"] == PRUNED_PLACEHOLDER

    async def test_anchor_only_context_is_left_alone(self, journal: SessionJournal) -> None:
        context = [_user("k" * 4000)]  # over threshold, but all anchor
        session = _session(context, anchor_len=1)

        assert await _make_pressure(journal).maybe_compact(session) is False
        assert session.context is context
        assert await _compact_events(journal, session.id) == []


# ── Grade 2: summary ────────────────────────────────────────────────────


class TestSummary:
    def _context_without_tools(self) -> list[dict[str, Any]]:
        """anchor(2) + prose middle (prune can shrink nothing) + window(20)."""
        middle = [_user(f"历史讨论 {i} " + "说" * 300) for i in range(4)]
        window = [_user(f"recent {i}") for i in range(20)]
        return [_user("knowledge"), _user("触发邮件"), *middle, *window]

    async def test_summary_when_prune_is_not_enough(self, journal: SessionJournal) -> None:
        context = self._context_without_tools()
        window = [dict(m) for m in context[-20:]]
        session = _session(context, anchor_len=2)
        llm_client = MagicMock()
        llm_client.chat = AsyncMock(return_value=LLMResponse(content="这是一段摘要"))
        pressure = _make_pressure(journal, llm_client=llm_client)

        assert await pressure.maybe_compact(session) is True

        llm_client.chat.assert_awaited_once()
        # The summary input is the middle segment plus the instruction.
        summary_input = llm_client.chat.await_args.kwargs["messages"]
        assert summary_input[:-1] == context[2:-20]
        # anchor + one [前文摘要] + keep window
        assert len(session.context) == 2 + 1 + 20
        assert session.context[2]["role"] == "user"
        assert session.context[2]["content"].startswith(SUMMARY_MARKER)
        assert "这是一段摘要" in session.context[2]["content"]
        assert session.context[-20:] == window
        events = await _compact_events(journal, session.id)
        assert len(events) == 1 and events[0]["kind"] == "summary"
        assert await journal.derive_context(session.id) == session.context

    async def test_running_summary_folds_previous_summary(self, journal: SessionJournal) -> None:
        context = self._context_without_tools()
        context[2] = _user(f"{SUMMARY_MARKER}\n\n旧摘要内容")
        session = _session(context, anchor_len=2)
        llm_client = MagicMock()
        llm_client.chat = AsyncMock(return_value=LLMResponse(content="新摘要"))
        pressure = _make_pressure(journal, llm_client=llm_client)

        assert await pressure.maybe_compact(session) is True

        # The old summary is part of the summary input...
        summary_input = llm_client.chat.await_args.kwargs["messages"]
        assert any("旧摘要内容" in str(m.get("content")) for m in summary_input)
        # ...and the result holds exactly one summary message (no stacking).
        summaries = [
            m for m in session.context if str(m.get("content", "")).startswith(SUMMARY_MARKER)
        ]
        assert len(summaries) == 1
        assert "新摘要" in summaries[0]["content"]

    async def test_summarizer_failure_falls_back_to_placeholder(
        self, journal: SessionJournal
    ) -> None:
        session = _session(self._context_without_tools(), anchor_len=2)
        llm_client = MagicMock()
        llm_client.chat = AsyncMock(side_effect=RuntimeError("summarizer down"))
        pressure = _make_pressure(journal, llm_client=llm_client)

        assert await pressure.maybe_compact(session) is True  # never raises

        assert len(session.context) == 2 + 1 + 20
        assert session.context[2]["content"].startswith(SUMMARY_MARKER)
        assert "已省略" in session.context[2]["content"]
        assert await journal.derive_context(session.id) == session.context


# ── ADR 0017: knowledge lives outside the anchor, so it is compressible ─


class TestKnowledgeOutsideAnchor:
    async def test_big_knowledge_is_summarized_away(self, journal: SessionJournal) -> None:
        """The old layout froze knowledge into the anchor: a big knowledge
        section made the middle empty and compaction hit the no-middle
        dead end (). With knowledge after the anchor it is ordinary
        middle and the summary grade relieves it."""
        knowledge = _user("[已知信息]\n\n" + "知" * 4000)
        context = [_user("触发邮件"), knowledge, *[_user(f"recent {i}") for i in range(20)]]
        session = _session(context, anchor_len=1)  # anchor = the trigger mail only
        llm_client = MagicMock()
        llm_client.chat = AsyncMock(return_value=LLMResponse(content="知识摘要"))
        pressure = _make_pressure(journal, llm_client=llm_client)

        assert await pressure.maybe_compact(session) is True

        # The summary input is exactly the knowledge message (the middle).
        summary_input = llm_client.chat.await_args.kwargs["messages"]
        assert summary_input[:-1] == [knowledge]
        # anchor + one [前文摘要] + keep window; the knowledge text is gone.
        assert session.context[0] == _user("触发邮件")
        assert session.context[1]["content"].startswith(SUMMARY_MARKER)
        assert "知识摘要" in session.context[1]["content"]
        assert len(session.context) == 1 + 1 + 20
        assert not any("知" * 100 in str(m.get("content")) for m in session.context)
        events = await _compact_events(journal, session.id)
        assert len(events) == 1 and events[0]["kind"] == "summary"
        assert await journal.derive_context(session.id) == session.context


# ── Replay: the compacted log is the truth ──────────────────────────────


class TestReplay:
    async def test_compact_event_replaces_context_on_replay(self, journal: SessionJournal) -> None:
        sid = "agent1-compacted"
        start_context = [_user("knowledge"), _user("触发邮件")]
        await journal.append(
            sid,
            "session/start",
            agent_id="agent1",
            mail_id="m1",
            trigger_content="hi",
            carrier=None,
            context=start_context,
        )
        await journal.append(sid, "context/append", messages=[_tool("c1", "x" * 100)])
        compacted = [*start_context, _tool("c1", PRUNED_PLACEHOLDER)]
        await journal.append(
            sid,
            "context/compact",
            kind="prune",
            reason="threshold_exceeded",
            tokens_before=100,
            tokens_after=50,
            context=compacted,
        )
        # Appends after the compact land on the compacted context.
        await journal.append(sid, "context/append", messages=[_user("later")])

        replay = await journal.resume(sid)

        assert replay is not None
        assert replay.context == [*compacted, _user("later")]
        assert replay.anchor_len == len(start_context)
        assert await journal.derive_context(sid) == replay.context

    async def test_restore_session_carries_anchor_len(
        self, journal: SessionJournal, tmp_path: Path
    ) -> None:
        from tlon.agents.scheduler import SessionScheduler

        sid = "agent1-anchor"
        await journal.append(
            sid,
            "session/start",
            agent_id="agent1",
            mail_id="m1",
            trigger_content="hi",
            carrier=None,
            context=[_user("a"), _user("b"), _user("c")],
        )
        replay = await journal.resume(sid)
        assert replay is not None

        scheduler = SessionScheduler(
            AgentConfig(id="agent1", name="agent1"),
            memory=MagicMock(),
            session_span=lambda session: contextlib.nullcontext(),
            check_reflection=AsyncMock(),
            note_interaction=MagicMock(),
            loop_driver=MagicMock(),
            journal=journal,
            wakeable_unread=AsyncMock(return_value=[]),
            mailbox=None,
        )
        session = await scheduler._restore_session(replay)

        assert session.anchor_len == 3
        assert session.context == replay.context


# ── Emergency path: no client retry, driver force-compacts ──────────────


def _bad_request(message: str) -> litellm.BadRequestError:
    request = httpx.Request("POST", "https://api.test/chat")
    return litellm.BadRequestError(
        message,
        llm_provider="openai",
        model="m",
        response=httpx.Response(400, request=request),
        body=None,
    )


def _make_llm_client() -> LLMClient:
    return LLMClient(
        LLMConfig(
            model_name="openai/m",
            max_retries=3,
            base_delay=0.0,
        )
    )


def _patch_acompletion(monkeypatch: pytest.MonkeyPatch, error: Exception) -> AsyncMock:
    # The preflight (ADR 0016) requires the provider key env var; litellm
    # itself is mocked, so the value never leaves the test.
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    create = AsyncMock(side_effect=error)
    monkeypatch.setattr(litellm, "acompletion", create)
    return create


class TestClientEmergency:
    async def test_context_length_error_never_retried(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _make_llm_client()
        create = _patch_acompletion(
            monkeypatch,
            _bad_request("Error code: 400 - {'error': {'code': 'context_length_exceeded'}}"),
        )

        with pytest.raises(ContextWindowExceededError):
            await client.chat(system_prompt="s", messages=[_user("hi")])

        assert create.await_count == 1  # no retry

    async def test_context_window_wording_also_detected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _make_llm_client()
        _patch_acompletion(
            monkeypatch, _bad_request("This model's maximum context length is exceeded")
        )

        with pytest.raises(ContextWindowExceededError):
            await client.chat(system_prompt="s", messages=[_user("hi")])

    async def test_litellm_context_window_error_mapped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """litellm's own ContextWindowExceededError maps by type, no text match."""
        client = _make_llm_client()
        request = httpx.Request("POST", "https://api.test/chat")
        _patch_acompletion(
            monkeypatch,
            litellm.ContextWindowExceededError(
                "provider-specific message without any known marker",
                llm_provider="openai",
                model="m",
                response=httpx.Response(400, request=request),
            ),
        )

        with pytest.raises(ContextWindowExceededError):
            await client.chat(system_prompt="s", messages=[_user("hi")])

    async def test_other_bad_requests_fail_fast(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Permanent 4xx (bad params, balance, ...) can never succeed on
        retry: the client raises without retrying."""
        client = _make_llm_client()
        create = _patch_acompletion(monkeypatch, _bad_request("invalid argument: temperature"))

        with pytest.raises(litellm.BadRequestError):
            await client.chat(system_prompt="s", messages=[_user("hi")])

        assert create.await_count == 1  # fail fast, no retry


class TestDriverEmergency:
    async def test_force_compact_and_retry_once(self, tmp_path: Path) -> None:
        """think raises ContextWindowExceededError: the driver force-compacts
        (tightened window) and retries the stage once; the retry succeeds."""
        spec = MemberSpec.from_edn(loads(COGNITIVE_EDN))
        validate_member_spec(spec)
        rule = spec.rules[0]

        services = MagicMock()
        services.logger = MagicMock()
        services.publish_event = AsyncMock()
        services.build_context = AsyncMock(
            return_value=BuiltContext(messages=[_user("hi")], anchor_len=1)
        )
        services.think = AsyncMock(
            side_effect=[ContextWindowExceededError("too long"), LLMResponse(content="hi")]
        )
        services.session_span = lambda session: contextlib.nullcontext()

        executor = MagicMock()
        executor.send_mail = AsyncMock()
        executor.publish = AsyncMock()

        journal = SessionJournal("agent1", TlonPaths(home=tmp_path))
        driver = LoopDriver(
            identity=AgentIdentity(id="agent1", name="Agent", address="agent1@tlon.internal"),
            rule=rule,
            services=services,
            executor=executor,
            tool_executor=MagicMock(),
            per_tool_timeout=5.0,
            on_complete=MagicMock(),
            journal=journal,
            spill=ToolSpill(TlonPaths(home=tmp_path), "agent1"),
            pressure=_make_pressure(journal, context_window=TEST_WINDOW),
            is_human_sender=lambda address: True,
        )

        # Under the proactive threshold (no pre-step compaction), with a
        # prunable middle the force path can shrink (window tightens to 10).
        context = [
            _user("knowledge"),
            _user("触发邮件"),
            _assistant_call("c1"),
            _tool("c1", "x" * 500),
            *[_user(f"recent {i}") for i in range(12)],
        ]
        session = _session(context, anchor_len=2)

        await driver.step_session(session)

        assert services.think.await_count == 2  # retried exactly once
        # The retry saw the compacted context.
        assert services.think.await_args_list[1].args[0] == session.context
        events = await _compact_events(journal, session.id)
        assert len(events) == 1
        assert events[0]["kind"] == "prune"
        assert events[0]["reason"] == "context_window_exceeded"
        assert session.context[3]["content"] == PRUNED_PLACEHOLDER
        assert await journal.derive_context(session.id) == session.context
        # The round completed its stage vector (act advanced the iteration).
        assert session.iteration == 1
