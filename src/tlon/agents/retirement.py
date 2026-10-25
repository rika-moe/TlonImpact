"""Offline handover inventory after permanent member exit.

AGENT_STOPPED triggers read-only inventory of unfinished tasks, unreplied
threads and private note filenames. AGENT_RETIRED publishes the inventory without
running the dead member's model or sending any mail. Administrative deletion
has no archive to inspect and is skipped. Historical records remain unchanged.
"""

import os
from datetime import UTC, datetime
from pathlib import Path

from tlon.base.component import Component
from tlon.base.config import yaml_load_file
from tlon.base.device import Device
from tlon.base.events.event import Event, EventType
from tlon.base.events.event_bus import EventBus
from tlon.base.logger import get_logger
from tlon.base.paths import TlonPaths
from tlon.organization.mail.mail import Mail, address_for_member
from tlon.organization.mail.mailbox import Mailbox
from tlon.tasks.manager import TaskManager
from tlon.tasks.models import TaskStatus

logger = get_logger(__name__)


def _parse_timestamp(value: str) -> datetime | None:
    """Parse a Mail timestamp; naive values read as UTC, junk as None."""
    try:
        ts = datetime.fromisoformat(value)
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts


class RetirementCeremony(Component, Device):
    """System archive inventory; never resumes or impersonates the dead member."""

    def __init__(self, event_bus: EventBus, tlon_paths: TlonPaths) -> None:
        Component.__init__(self, name="RetirementCeremony")
        Device.__init__(self, event_bus=event_bus, name="RetirementCeremony")
        self._paths = tlon_paths

    async def initialize(self) -> None:
        """Start the ceremony by subscribing to AGENT_STOPPED."""
        self.subscribe(EventType.AGENT_STOPPED, self._on_agent_stopped)
        self._initialized = True
        logger.info("retirement_ceremony_initialized")

    async def shutdown(self) -> None:
        """Stop the ceremony by unsubscribing from the bus."""
        self.unsubscribe_all()
        self._initialized = False
        logger.info("retirement_ceremony_shutdown")

    async def _on_agent_stopped(self, event: Event) -> None:
        """Hold one stopped member's ceremony; never raised failures.

        Administrative removal is not retirement: ``removed=True`` in the
        payload (AgentRegistry.remove_member) means the member's home was
        already deleted, so there is no archive to inventory — the whole
        ceremony is skipped (no inventory or AGENT_RETIRED).
        """
        agent_id = ""
        stopped_by = ""
        removed = False
        if isinstance(event.payload, dict):
            agent_id = str(event.payload.get("agent_id") or "")
            stopped_by = str(event.payload.get("stopped_by") or "")
            removed = bool(event.payload.get("removed"))
        if not agent_id:
            logger.warning("retirement_no_agent_id")
            return
        if removed:
            logger.info("retirement_skipped_removed", agent_id=agent_id)
            return
        try:
            await self._hold_ceremony(agent_id, stopped_by)
        except Exception as e:
            logger.warning("retirement_ceremony_failed", agent_id=agent_id, error=str(e))

    async def _hold_ceremony(self, agent_id: str, stopped_by: str) -> None:
        """Read the archive and publish its handover inventory."""
        config = yaml_load_file(self._paths.agent_config_file(agent_id))
        name = config.name if config is not None else agent_id

        open_tasks = self._open_tasks(agent_id)
        open_threads = await self._open_threads(agent_id)
        memory_files = self._memory_files(agent_id)
        await self.publish(
            Event(
                type=EventType.AGENT_RETIRED,
                payload={
                    "agent_id": agent_id,
                    "name": name,
                    "stopped_by": stopped_by,
                    "open_tasks": open_tasks,
                    "open_threads": open_threads,
                    "memory_files": memory_files,
                },
                source="retirement_ceremony",
            )
        )
        logger.info(
            "agent_retired",
            agent_id=agent_id,
            open_tasks=len(open_tasks),
            open_threads=len(open_threads),
        )

    # ── Offline inventory (the member runtime is already shut down) ──

    def _open_tasks(self, agent_id: str) -> list[str]:
        """Unfinished task titles (todo/doing/paused) from the archive."""
        tasks = TaskManager(agent_id, self._paths)
        tasks.load()
        return [t.title for t in tasks.list_tasks() if t.status != TaskStatus.DONE]

    async def _open_threads(self, agent_id: str) -> list[dict[str, str]]:
        """Unreplied inbox threads: the thread's last inbox mail is from
        someone else and the retiree's sent file holds no later reply in
        that thread (the mirror image of the orphan-mail scan, which
        finds "I wrote and nobody answered")."""
        mailbox = Mailbox(self._paths.mail_database_file(), agent_id)
        own_address = address_for_member(agent_id)

        # File order is chronological: the last write wins per thread.
        last_inbox: dict[str, Mail] = {}
        for mail in await mailbox.inbox():
            last_inbox[mail.thread_id] = mail

        last_sent: dict[str, datetime] = {}
        for mail in await mailbox.sent():
            ts = _parse_timestamp(mail.timestamp)
            if ts is None:
                continue
            previous = last_sent.get(mail.thread_id)
            if previous is None or ts > previous:
                last_sent[mail.thread_id] = ts

        open_threads: list[dict[str, str]] = []
        for thread_id, mail in last_inbox.items():
            if mail.from_address == own_address:
                continue  # own last word is not "awaiting my reply"
            ts = _parse_timestamp(mail.timestamp)
            if ts is not None and last_sent.get(thread_id, datetime.min.replace(tzinfo=UTC)) > ts:
                continue  # already answered
            open_threads.append(
                {
                    "thread_id": thread_id,
                    "subject": mail.subject or "(无主题)",
                    "from": mail.from_address,
                }
            )
        return open_threads

    def _memory_files(self, agent_id: str) -> list[str]:
        """List existing private notes without reading them or following symlinks."""
        workspace = self._paths.agent_workspace_dir(agent_id)
        notes = workspace / "memory"
        if workspace.is_symlink() or notes.is_symlink() or not notes.is_dir():
            return []
        files: list[str] = []
        for directory_name, dirs, names in os.walk(notes, followlinks=False):
            directory = Path(directory_name)
            dirs[:] = [name for name in dirs if not (directory / name).is_symlink()]
            for name in names:
                path = directory / name
                if not path.is_symlink() and path.is_file():
                    files.append(path.relative_to(workspace).as_posix())
        return sorted(files)
