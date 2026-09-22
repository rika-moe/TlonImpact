"""Pure logic for the Telegram bot (design D1/D2, docs/design/telegram.md).

No python-telegram-bot dependency — everything here is unit-testable in
every environment:

- ``ReplyMap``: bounded ``telegram_message_id -> ReplyTarget`` mapping so
  a Telegram reply can be threaded back onto the upstream mail;
- ``parse_allowed_users`` / ``is_allowed``: the TELEGRAM_ALLOWED_USERS
  user-id whitelist;
- ``parse_send_command``: ``/send <名字> <内容>`` parsing.
"""

from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

# How many pushed messages keep their reply mapping (LRU eviction).
DEFAULT_CAPACITY = 500


@dataclass(frozen=True)
class ReplyTarget:
    """What a Telegram reply needs to address the upstream mail.

    Attributes:
        mail_id: Id of the mail the pushed message carries (in_reply_to)
        from_address: Original sender — the reply's recipient
        thread_id: Thread root inherited from the upstream mail
    """

    mail_id: str
    from_address: str
    thread_id: str


class ReplyMap:
    """Bounded telegram_message_id -> ReplyTarget mapping (LRU eviction).

    In-memory only (design D2): a restart loses the mapping and replies
    degrade to plain mail. The most recent correspondent is tracked so a
    reply whose mapping was lost can still go out — as a new mail without
    in_reply_to, to the last sender, never an error.
    """

    def __init__(self, capacity: int = DEFAULT_CAPACITY):
        """Initialize the mapping.

        Args:
            capacity: Maximum number of remembered messages (LRU eviction)

        Raises:
            ValueError: If capacity < 1
        """
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self._capacity = capacity
        self._entries: OrderedDict[int, ReplyTarget] = OrderedDict()
        self._last_from: str | None = None

    def __len__(self) -> int:
        return len(self._entries)

    @property
    def last_from_address(self) -> str | None:
        """Sender of the most recently recorded mail (degradation target)."""
        return self._last_from

    def record(self, telegram_message_id: int, mail: dict[str, Any]) -> None:
        """Remember which mail a pushed Telegram message carries.

        Args:
            telegram_message_id: Id of the Telegram message the bot sent
            mail: The mail dict that message renders (id, from_address,
                thread_id — thread_id defaults to the mail's own id)
        """
        target = ReplyTarget(
            mail_id=str(mail["id"]),
            from_address=str(mail["from_address"]),
            thread_id=str(mail.get("thread_id") or mail["id"]),
        )
        self._entries[telegram_message_id] = target
        self._entries.move_to_end(telegram_message_id)
        self._last_from = target.from_address
        while len(self._entries) > self._capacity:
            self._entries.popitem(last=False)

    def lookup(self, telegram_message_id: int) -> ReplyTarget | None:
        """Resolve a replied-to Telegram message back to its mail.

        Args:
            telegram_message_id: ``reply_to_message.message_id``

        Returns:
            The ReplyTarget, or None if the mapping was evicted/lost
        """
        return self._entries.get(telegram_message_id)


def parse_allowed_users(raw: str) -> list[int]:
    """Parse the TELEGRAM_ALLOWED_USERS whitelist (comma-separated user ids).

    Args:
        raw: The raw env value, e.g. "123, 456 ,789"

    Returns:
        The user ids, in order

    Raises:
        ValueError: If a non-empty entry is not an integer
    """
    users: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            users.append(int(part))
        except ValueError:
            raise ValueError(f"invalid Telegram user id: {part!r}") from None
    return users


def is_allowed(user_id: int | None, allowed_users: list[int]) -> bool:
    """Whitelist check (design D1): messages from outside are ignored.

    Args:
        user_id: The Telegram user id of the incoming message (None when
            the update carries no user)
        allowed_users: The whitelist

    Returns:
        True if the message may be processed
    """
    return user_id is not None and user_id in allowed_users


def parse_send_command(text: str) -> tuple[str, str] | None:
    """Parse a /send command body: ``<名字> <内容>`` -> (recipient, content).

    Args:
        text: The raw text after the ``/send`` command token

    Returns:
        (recipient name, content), or None for malformed input (missing
        content, empty fields) — callers answer with a usage hint
    """
    parts = text.strip().split(None, 1)
    if len(parts) != 2:
        return None
    name, content = parts[0], parts[1].strip()
    if not name or not content:
        return None
    return name, content
