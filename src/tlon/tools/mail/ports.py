"""Ports for the mail tools (dependency inversion, issue ).

``searchMails``/``checkInbox``/``markRead`` read or update the agent's
own mailbox, which lives in the
organization mail domain; declaring the provider/actor signatures here keeps
``tlon.tools`` free of any ``tlon.organization`` import (same precedent
as ``tools/observability/ports.py``, ). The mailbox-facing
implementations live in the agents layer (``tlon.agents.mail_search`` /
``tlon.agents.mail_inbox``).
"""

from collections.abc import Awaitable, Callable
from typing import Any

#: Mailbox reader: takes an agent id, returns that agent's own mails
#: (inbox + sent) as JSON-safe dicts (Mail fields plus a ``box`` marker).
#: Read-only — the provider only touches the subject agent's own mailbox.
MailboxMailsProvider = Callable[[str], Awaitable[list[dict[str, Any]]]]

#: Unread reader (ADR 0028 收件箱驱动): takes an agent id, returns that
#: agent's unread inbox mails as JSON-safe dicts (Mail fields plus an
#: ``external`` marker), chronological order (oldest first = processing
#: order). Read-only.
UnreadMailsProvider = Callable[[str], Awaitable[list[dict[str, Any]]]]

#: Read-marker actor (ADR 0028 已读归 agent 显式管理): takes an agent id
#: and a list of mail ids, marks the existing unread ones read, and
#: reports per-id outcomes as ``{"marked": [...], "already_read": [...],
#: "not_found": [...]}`` — ids not in the inbox never reach the cursor.
#: The actor only touches the subject agent's own mailbox.
MarkReadActor = Callable[[str, list[str]], Awaitable[dict[str, list[str]]]]
