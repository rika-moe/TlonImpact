"""Read-only provider signatures for member and society observations."""

from collections.abc import Awaitable, Callable
from typing import Any

#: Read-only view over the process-wide metrics: the assembler injects
#: ``MetricsService.snapshot`` (``tlon.base.metrics``).
MetricsSnapshot = Callable[[], dict[str, Any]]

#: Mail social statistics provider: takes an agent id, returns that
#: agent's per-correspondent mail statistics as a JSON-safe dict.
MailStatsProvider = Callable[[str], Awaitable[dict[str, Any]]]

#: Reputation provider (): takes a member id, returns that member's
#: reputation derived from the society's mail files as a JSON-safe dict.
ReputationProvider = Callable[[str], Awaitable[dict[str, Any]]]

#: Orphan-mail scan provider (): runs an on-demand society-wide scan
#: and returns the report (scanned_members (public ids) / scanned_count / failed_members / orphan_count / orphans) as a
#: JSON-safe dict. Derived, never stored.
OrphanMailProvider = Callable[[], Awaitable[dict[str, Any]]]
