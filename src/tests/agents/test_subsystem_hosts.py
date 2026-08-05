"""P4 subsystem-decoupling demonstration tests.

Subsystems no longer take a back-reference to the whole Agent:

- B side (service/data injection): ReflectionEngine is constructed with
  memory + llm_client + spec — plain mocks suffice, no Agent at all.
- A side (narrow host protocol): SessionScheduler runs against a tiny
  hand-written SchedulerHost fake implementing only the declared
  protocol surface (agents/host.py), with the LoopDriver injected.

See also: test_on_complete.py's _FakeHost (OnCompleteHost) and
test_eval.py's data-only TriggerEvaluator construction.
"""

import asyncio
import contextlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from tlon.agents.edn import loads
from tlon.agents.reflection import ReflectionEngine
from tlon.agents.scheduler import SessionScheduler
from tlon.agents.session import AgentSession
from tlon.agents.spec import AgentSpec, standard_behavior_edn
from tlon.base.config import AgentConfig
from tlon.organization.mail.mail import Mail


def _make_engine(min_interactions: int = 2) -> tuple[ReflectionEngine, MagicMock, MagicMock]:
    """Build a ReflectionEngine from injected services (no Agent)."""
    memory = MagicMock()
    memory.config = SimpleNamespace(reflection_min_interactions=min_interactions)
    llm_client = MagicMock()
    spec = AgentSpec.from_edn(loads(standard_behavior_edn()))
    return ReflectionEngine(memory, llm_client, spec), memory, llm_client


class TestReflectionEngineServices:
    """B side: the engine needs only memory, llm_client and the spec."""

    async def test_disabled_never_triggers(self):
        engine, memory, _ = _make_engine()
        engine.enabled = False
        for _ in range(5):
            engine.note_interaction()
        await engine.check_trigger()
        assert engine._running is False
        memory.get_memory_digest.assert_not_called()

    async def test_below_min_interactions_does_not_trigger(self):
        engine, memory, _ = _make_engine(min_interactions=2)
        engine.note_interaction()
        await engine.check_trigger()
        assert engine._running is False
        memory.get_memory_digest.assert_not_called()

    async def test_triggers_after_min_interactions(self):
        engine, memory, _ = _make_engine(min_interactions=2)
        engine._run = AsyncMock()
        engine.note_interaction()
        engine.note_interaction()
        await engine.check_trigger()
        assert engine._running is True
        for _ in range(10):
            if engine._run.await_count:
                break
            await asyncio.sleep(0)
        engine._run.assert_awaited_once()
        # The trigger resets its counters when it fires
        assert engine._interaction_count_since_reflection == 0
        assert engine._last_reflection_time is not None


class _FakeSchedulerHost:
    """Minimal SchedulerHost fake: only the declared protocol surface."""

    def __init__(self):
        self.id = "a-001"
        self.config = AgentConfig(id="a-001", name="fake")
        self.memory = AsyncMock()
        self.derived: list[int] = []
        self.marked_read: list[str | None] = []
        self.reflection_checks = 0

    @contextlib.contextmanager
    def _session_span(self, session: AgentSession):
        yield

    async def _mark_mail_read(self, mail: Mail | None) -> None:
        self.marked_read.append(mail.id if mail else None)

    def _derive_state(self, sessions: dict) -> None:
        self.derived.append(len(sessions))

    async def _check_reflection_trigger(self) -> None:
        self.reflection_checks += 1


class TestSchedulerHostFake:
    """A side: the scheduler drives a small fake host + injected driver."""

    async def test_one_mail_stepped_and_marked_read(self):
        host = _FakeSchedulerHost()
        driver = MagicMock()

        async def complete(session: AgentSession, mail: Mail) -> None:
            session.mark_completed()

        driver.step_session = AsyncMock(side_effect=complete)
        driver.collect_session_results = AsyncMock()

        scheduler = SessionScheduler(host, driver)
        mail = Mail.create(
            from_address="u-1@tlon.internal", to=["a-001@tlon.internal"], content="hi"
        )
        scheduler._mail_queue.put_nowait((mail, {}))
        scheduler._worker_running = True

        task = asyncio.create_task(scheduler.run())
        try:
            for _ in range(200):
                if host.marked_read:
                    break
                await asyncio.sleep(0.01)
            driver.step_session.assert_awaited_once()
            session, stepped_mail = driver.step_session.await_args.args
            assert stepped_mail is mail
            assert session.id == f"a-001-{mail.id}"
            assert host.marked_read == [mail.id]
        finally:
            scheduler._worker_running = False
            await asyncio.wait_for(task, timeout=2)
