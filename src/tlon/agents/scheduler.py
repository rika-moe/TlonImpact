"""Session scheduler for Agent — the single-session loop (ADR 0028).

One cognitive member runs at most one live session at a time; the mailbox
is the only queue. The scheduler is one sentence: *no live session and
wakeable unread mail > 0 → open one session*. Its inputs:

- ``notify_wake`` — set by the member runtime when a gate-passed mail is
  delivered and no session is live, or by the boot/periodic scan. The
  event is idempotent (setting an already-set event dedups concurrent
  deliveries); the actual admission decision always re-reads the mailbox
  through the injected ``wakeable_unread`` provider, so a stale wake is a
  no-op.
- ``wakeable_unread`` — the member runtime's single implementation of
  "可唤醒未读" (unread minus own-mail minus gate-rejected), shared by the
  delivery path, the scan, and this loop (both the admission check and
  the chain-wake comparison at reap, so the two can never drift apart).
- ``journal.find_unfinished`` — boot recovery: a session journal without
  ``session/end`` is resumed (ADR 0007), with its journaled
  responsibility set, before any new wake is honored.

Session identity is a fresh uuid (ADR 0028 决策 8). The trigger content is
the fixed template rendered from the *same* provider snapshot that becomes
the session's responsibility set
(R5: N and the responsibility set are one read).

The whole loop task runs inside bound_contextvars(agent_id=...) so all
logs emitted from session advances carry the agent binding; tasks spawned
from here (e.g. reflection) inherit the context. The agent-level state
derives on read (Agent/MemberRuntime properties), never by callback from
here.
"""

import asyncio
import contextlib
import uuid
from collections.abc import Awaitable, Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime

from structlog.contextvars import bound_contextvars

from tlon.agents.loop_driver import WAKE_TRIGGER_TEMPLATE, LoopDriver
from tlon.agents.session import AgentSession, SessionState
from tlon.agents.session_journal import SessionJournal, SessionReplay
from tlon.base.config import AgentConfig
from tlon.base.llm_client import ToolCall
from tlon.base.logger import get_logger
from tlon.memory.manager import MemoryManager
from tlon.organization.mail.mail import Mail
from tlon.organization.mail.mailbox import Mailbox
from tlon.tools.base import ToolResult

logger = get_logger(__name__)

# Injected delegate signatures (assembled from StageServices /
# ReflectionEngine / MemberRuntime at Agent construction).
SessionSpan = Callable[[AgentSession], AbstractContextManager[None]]
CheckReflection = Callable[[], Awaitable[None]]
NoteInteraction = Callable[[], None]
#: The member runtime's "可唤醒未读" provider (ADR 0028 决策 5/6): unread
#: inbox mails minus own mail, minus gate-rejected ones (which the
#: provider marks read as a side effect). One implementation, read by the
#: delivery path, the scan, and this scheduler.
WakeableUnread = Callable[[], Awaitable[list[Mail]]]


class SessionScheduler:
    """Single-session scheduling loop for one agent (ADR 0028)."""

    def __init__(
        self,
        config: AgentConfig,
        memory: MemoryManager,
        session_span: SessionSpan,
        check_reflection: CheckReflection,
        note_interaction: NoteInteraction,
        loop_driver: LoopDriver,
        journal: SessionJournal,
        wakeable_unread: WakeableUnread,
        mailbox: Mailbox | None,
    ):
        self._id = config.id
        self._config = config
        self._memory = memory
        self._session_span = session_span
        self._check_reflection = check_reflection
        self._note_interaction = note_interaction
        self._loop_driver = loop_driver
        self._journal = journal
        self._wakeable_unread = wakeable_unread
        # Read side of crash recovery only: a restored session's
        # responsibility ids are resolved back to Mail objects through it.
        self._mailbox = mailbox
        # The one live-session slot (ADR 0028 决策 1). Read by the
        # registry's idle monitor via the properties below.
        self._session: AgentSession | None = None
        # Wake signal (delivery / scan / chain-wake at reap). Setting an
        # already-set event is the dedup of concurrent deliveries (R6) —
        # the admission check always re-reads the mailbox anyway.
        self._wake = asyncio.Event()
        # Trace carrier of the wake (from the delivering event); consumed
        # by the next session start.
        self._wake_carrier: dict[str, str | None] | None = None
        # Floor for last activity: loop start time, bumped on session
        # creation and session completion (see run()).
        self._last_activity_at: datetime = datetime.now(UTC)
        self._worker_running = False
        self._worker_task: asyncio.Task[None] | None = None

    def notify_wake(self, carrier: dict[str, str | None] | None = None) -> None:
        """Signal that wakeable unread mail may exist.

        Pure signal, no payload: the loop re-reads the mailbox through the
        provider before opening a session. With a live session this is a
        no-op (ADR 0028 决策 1 — the running session is never disturbed;
        the mail waits unread in the inbox).
        """
        if self._session is not None:
            return
        if carrier is not None:
            self._wake_carrier = carrier
        self._wake.set()

    def start(self) -> None:
        """Start the main-loop worker task."""
        self._worker_running = True
        self._worker_task = asyncio.create_task(self.run())

    async def stop(self) -> None:
        """Stop the main-loop worker task."""
        self._worker_running = False
        if self._worker_task:
            self._worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker_task

    @property
    def active_session_count(self) -> int:
        """0 or 1 — the single-session model (kept numeric for the registry's
        idle monitor, which only compares against 0)."""
        return 1 if self._session is not None else 0

    @property
    def last_activity_at(self) -> datetime:
        """Most recent session activity timestamp.

        "Activity" means: a session was created, the live session's
        ``last_active_at`` (updated on every state change/step), or a
        session completed. With no session at all, this is the loop start
        time — so a freshly started agent is considered recently active.
        """
        last = self._last_activity_at
        if self._session is not None and self._session.last_active_at > last:
            last = self._session.last_active_at
        return last

    @property
    def idle_seconds(self) -> float:
        """Seconds since the last session activity (see last_activity_at)."""
        return (datetime.now(UTC) - self.last_activity_at).total_seconds()

    async def run(self) -> None:
        """The single-session main loop (ADR 0028).

        Each pass:
        0. Reflection trigger (timer-driven, no mail needed)
        1. No live session → admission, edge-triggered by the wake event
           (R6 order: wake first, then the wakeable-unread provider —
           the provider is never polled on its own, so shelved leftover
           mail cannot re-open a session by itself, ADR 0028 决策 5)
        2. Live session → advance it (ReAct step / tool-result collect /
           crash-recovered pending collect), or end it on timeout/error
        3. Completed → reap (reflection counter, the batch distillation
           trigger ADR 0017, and the chain-wake check: unread ids outside
           the reaped session's responsibility set wake the next session
           immediately; leftover responsibility ids — mail the agent saw
           and shelved — are left to the slow rescan, breaking the
           re-wake hot loop)
        """
        # This loop runs in its own task per agent, so binding agent_id for
        # its whole lifetime is safe and also covers logs inside session
        # advances; tasks spawned here (e.g. reflection) inherit the context.
        with bound_contextvars(agent_id=self._id):
            logger.info("agent_main_loop_started")
            self._last_activity_at = datetime.now(UTC)
            # Crash recovery (ADR 0007) comes first: a journaled
            # unfinished session resumes before any new wake is honored,
            # and its responsibility set comes back from the journal.
            pending_collect = await self._restore_unfinished()
            if self._session is not None:
                # A wake signaled before the restore (e.g. the boot scan)
                # is stale: the restored session supersedes it — anything
                # newer than its responsibility set chains at reap.
                self._wake.clear()
                self._wake_carrier = None

            while self._worker_running:
                # ── 0. Reflection trigger (timer-driven, no mail needed) ──
                await self._check_reflection()

                session = self._session
                if session is None:
                    # ── 1. Admission: edge-triggered by the wake event ──
                    # R6 order: wake first, then re-read the provider. The
                    # provider is never polled on its own — only a wake
                    # edge (delivery / scan / chain-wake) opens a session,
                    # so shelved leftover mail (ADR 0028 决策 5) cannot
                    # re-open one by itself.
                    if not self._wake.is_set():
                        with contextlib.suppress(TimeoutError):
                            await asyncio.wait_for(self._wake.wait(), timeout=0.5)
                        continue
                    self._wake.clear()
                    wakeable = await self._wakeable_unread()
                    if not wakeable:
                        continue  # stale wake (mail read meanwhile)
                    self._open_session(wakeable)
                    continue

                # ── 2. Advance the live session ──
                if self._is_session_timed_out(session):
                    logger.warning(
                        "session_timeout",
                        session_id=session.id,
                        state=session.state.value,
                        idle_seconds=int(
                            (datetime.now(UTC) - session.last_active_at).total_seconds()
                        ),
                    )
                    await self._end_session(session, "timeout")
                    await self._notify_failure_reply(session, "timeout")
                else:
                    try:
                        with self._session_span(session):
                            if pending_collect is not None:
                                # Crash recovery (ADR 0007): harvest the
                                # journaled tool results once, then the
                                # session rejoins the normal flow.
                                await self._loop_driver.resume_collect(session, pending_collect)
                                pending_collect = None
                            elif session.state == SessionState.WAITING:
                                await self._loop_driver.collect_session_results(session)
                            elif session.state == SessionState.ACTIVE:
                                await self._loop_driver.step_session(session)
                    except Exception as e:
                        logger.error(
                            "session_error", session_id=session.id, error=str(e), exc_info=True
                        )
                        await self._end_session(session, "error")
                        await self._notify_failure_reply(session, "error")

                # ── 3. Reap + chain-wake check ──
                if session.is_completed:
                    await self._ensure_session_end(session)
                    await session.cancel_pending()
                    self._session = None
                    self._last_activity_at = datetime.now(UTC)
                    # Session completed: feed the reflection counter
                    # and check the batch distillation trigger (ADR 0017 —
                    # journal -> claims, threshold config.distillation_window).
                    self._note_interaction()
                    await self._check_session_distillation()
                    # Chain wake (ADR 0028 决策 5): only unread ids
                    # *outside* the reaped session's responsibility set
                    # wake the next session immediately; leftover
                    # responsibility ids (the agent saw them and chose to
                    # shelve) do not — the slow rescan owns those.
                    await self._check_chain_wake(session)

                # Agent state derives on read — the scheduler never calls
                # back into the MemberRuntime.

                # Yield control
                await asyncio.sleep(0.01 if self._session else 0.05)

            logger.info("agent_main_loop_stopped")

    def _open_session(self, wakeable: list[Mail]) -> None:
        """Open the one session over the given wakeable-unread snapshot.

        R5: the trigger text's N and the responsibility set come from this
        one provider snapshot — never two reads. The responsibility ids are
        always logged (R4: a carrier-less wake — boot scan, rescan — still
        leaves the responsibility trail).
        """
        sid = f"{self._id}-{uuid.uuid4().hex[:12]}"
        trigger = WAKE_TRIGGER_TEMPLATE.format(n=len(wakeable))
        session = AgentSession(sid, trigger, carrier=self._wake_carrier, responsibility=wakeable)
        self._wake_carrier = None
        self._session = session
        self._last_activity_at = datetime.now(UTC)
        logger.info(
            "session_wake",
            session_id=sid,
            unread_count=len(wakeable),
            responsibility=[mail.id for mail in wakeable],
        )

    async def _check_chain_wake(self, session: AgentSession) -> None:
        """Compare post-reap unread against the reaped session's
        responsibility set (ADR 0028 决策 5).

        Uses the same wakeable-unread provider as delivery/scan (R1) —
        gate-rejection read-marking as a provider side effect is
        acceptable in the reap context. Never raises: a provider failure
        just skips this wake (the rescan remains as the backstop).
        """
        try:
            wakeable = await self._wakeable_unread()
        except Exception as e:
            logger.error("chain_wake_check_failed", session_id=session.id, error=str(e))
            return
        if not wakeable:
            return
        responsibility_ids = {mail.id for mail in session.responsibility}
        new_ids = [mail.id for mail in wakeable if mail.id not in responsibility_ids]
        if new_ids:
            logger.info(
                "session_chain_wake",
                session_id=session.id,
                new_mail_ids=new_ids,
            )
            self._wake.set()
        else:
            logger.info(
                "session_leftover_unread_deferred",
                session_id=session.id,
                leftover_mail_ids=[mail.id for mail in wakeable],
            )

    async def _restore_unfinished(self) -> list[tuple[ToolCall, ToolResult]] | None:
        """Resume a journaled unfinished session, if one exists (ADR 0007).

        Returns the pending-collect list for the first post-resume
        collect, or None. A restore failure (corrupt log, mailbox read
        error) is logged and skipped — a broken journal must not wedge
        the member's whole loop; the session is simply never resumed and
        its journal keeps no ``session/end``.
        """
        try:
            unfinished = await self._journal.find_unfinished()
        except Exception as e:
            logger.error("session_restore_scan_failed", error=str(e), exc_info=True)
            return None
        if not unfinished:
            return None
        if len(unfinished) > 1:
            logger.warning(
                "session_restore_multiple_unfinished",
                count=len(unfinished),
                resumed=unfinished[0],
            )
        sid = unfinished[0]
        try:
            replay = await self._journal.resume(sid)
        except Exception as e:
            logger.error("session_restore_failed", session_id=sid, error=str(e), exc_info=True)
            return None
        if replay is None:
            return None
        self._session = await self._restore_session(replay)
        self._last_activity_at = datetime.now(UTC)
        logger.info(
            "session_resumed",
            session_id=sid,
            iteration=replay.iteration,
            pending_collect=len(replay.pending_collect),
            responsibility=replay.responsibility,
        )
        return replay.pending_collect or None

    async def _restore_session(self, replay: SessionReplay) -> AgentSession:
        """Rebuild the in-memory session from a journal replay.

        Data fields only — side effects (mails, on-complete actions) are
        never re-executed (ADR 0007). The responsibility id list is
        resolved back to Mail objects through the mailbox; an id whose
        mail is gone is dropped (the set is only used for failure-reply
        anchoring and chain-wake comparison).
        """
        responsibility: list[Mail] = []
        if self._mailbox is not None:
            for mail_id in replay.responsibility:
                mail = await self._mailbox.find(mail_id)
                if mail is not None:
                    responsibility.append(mail)
        session = AgentSession(
            replay.session_id,
            replay.trigger_content,
            carrier=replay.carrier,
            responsibility=responsibility,
        )
        session.context = replay.context
        session.anchor_len = replay.anchor_len
        session._iteration = replay.iteration
        session._error_count = replay.error_count
        session._token_usage = replay.token_usage
        session._last_llm_response = replay.last_llm_response
        session._all_tool_results = replay.all_tool_results
        session.loop_scratch = replay.loop_scratch
        session.state = replay.state
        return session

    async def _notify_failure_reply(self, session: AgentSession, reason: str) -> None:
        """Best-effort failure notification when a session ends abnormally
        — one mail per human sender in the responsibility set (ADR 0027 as
        revised by ADR 0028 决策 7); agent senders are logged, not answered.

        Never raises: the reply is courtesy; a failure here must not
        break the scheduler loop.
        """
        try:
            await self._loop_driver.send_failure_reply(session, reason)
        except Exception as e:
            logger.error("session_failure_reply_failed", session_id=session.id, error=str(e))

    async def _end_session(self, session: AgentSession, reason: str) -> None:
        """Journal ``session/end``, then mark the session completed.

        A journal failure must not wedge the session: the reap proceeds
        and the session is re-run on the next boot.
        """
        try:
            await self._journal.append(session.id, "session/end", reason=reason)
        except Exception as e:
            logger.error("session_journal_end_failed", session_id=session.id, error=str(e))
        session.mark_completed()

    async def _ensure_session_end(self, session: AgentSession) -> None:
        """Backstop: every reaped session must carry a ``session/end``.

        The loop driver journals the end on its own completion paths;
        this covers any completion that bypassed them. Idempotent — a
        log that already ends is left alone.
        """
        try:
            if await self._journal.end_reason(session.id) is None:
                await self._journal.append(session.id, "session/end", reason="complete")
        except Exception as e:
            logger.error("session_journal_end_failed", session_id=session.id, error=str(e))

    async def _check_session_distillation(self) -> None:
        """Run the batch distillation trigger at the reap point (ADR 0017).

        Never raises: a distillation failure (LLM outage, disk error)
        is logged and retried at the next session's reap — the pending
        sessions stay pending because the watermark never advanced.
        """
        try:
            claim_count = await self._memory.maybe_distill_completed_sessions(self._journal)
        except Exception as e:
            logger.error("session_distillation_check_failed", error=str(e), exc_info=True)
            return
        if claim_count:
            logger.info("session_distillation_completed", claim_count=claim_count)

    def _is_session_timed_out(self, session: AgentSession) -> bool:
        """A session idle beyond the configured timeout is considered stuck."""
        timeout = self._config.session_timeout_seconds
        idle = (datetime.now(UTC) - session.last_active_at).total_seconds()
        return idle > timeout
