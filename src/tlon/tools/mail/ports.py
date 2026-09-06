"""Ports for the mail tools (dependency inversion, issue ).

``searchMails`` reads the agent's own mailbox, which lives in the
organization mail domain; declaring the provider signature here keeps
``tlon.tools`` free of any ``tlon.organization`` import (same precedent
as ``tools/observability/ports.py``, ). The mailbox-reading
implementation lives in the agents layer (``tlon.agents.mail_search``).
"""

from collections.abc import Awaitable, Callable
from typing import Any

#: Mailbox reader: takes an agent id, returns that agent's own mails
#: (inbox + sent) as JSON-safe dicts (Mail fields plus a ``box`` marker).
#: Read-only — the provider only touches the subject agent's own mailbox.
MailboxMailsProvider = Callable[[str], Awaitable[list[dict[str, Any]]]]
