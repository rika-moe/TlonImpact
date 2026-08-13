"""Field validation for the REST send-mail entry point.

REST ``POST /messages`` accepts a raw body dict; this module is the
single copy of the validation for that payload shape.
"""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SendMailFields:
    """Validated send-mail fields (R2 protocol, docs/design/mail.md)."""

    content: str
    to: list[str] | None
    cc: list[str] | None
    subject: str | None
    in_reply_to: str | None
    thread_id: str | None


@dataclass(frozen=True)
class SendMailError:
    """A field validation failure with a semantic reason code."""

    reason: str
    message: str


def parse_send_mail_fields(msg: dict[str, Any]) -> SendMailFields | SendMailError:
    """Validate raw send-mail fields (REST body).

    The shape stays lenient: unknown keys (e.g. the R1b ``mentions``
    alias, removed in PR-C) are silently ignored.

    Args:
        msg: The raw body dict

    Returns:
        The validated fields, or a SendMailError with a semantic reason
    """
    content = msg.get("content", "")
    if not isinstance(content, str) or not content.strip():
        return SendMailError("empty_content", "Message content is empty")

    to = msg.get("to")
    if to is not None and not isinstance(to, list):
        return SendMailError("invalid_to", "Field 'to' must be a list")
    cc = msg.get("cc")
    if cc is not None and not isinstance(cc, list):
        return SendMailError("invalid_cc", "Field 'cc' must be a list")
    in_reply_to = msg.get("in_reply_to")
    if in_reply_to is not None and not isinstance(in_reply_to, str):
        return SendMailError(
            "invalid_in_reply_to", "Field 'in_reply_to' must be a string"
        )
    thread_id = msg.get("thread_id")
    if thread_id is not None and not isinstance(thread_id, str):
        return SendMailError(
            "invalid_thread_id", "Field 'thread_id' must be a string"
        )

    return SendMailFields(
        content=content.strip(),
        to=to,
        cc=cc,
        subject=msg.get("subject"),
        in_reply_to=in_reply_to,
        thread_id=thread_id,
    )
