"""Ports for the observability tools (dependency inversion, ).

The mail-stats computation lives in the agents layer
(``tlon.agents.mail_stats``) because it reads the organization mail
domain; declaring the provider signature here keeps ``tlon.tools`` free
of any ``tlon.organization`` import (same precedent as
``tools/prototypes/ports.py``, ).
"""

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
