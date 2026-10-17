"""Mail endpoints: GET /inbox (human member mailbox), GET
/members/{member_id}/mail (read-only observer view of any member's
mailbox) and POST /messages (send a mail as a human member, protocol v2
PR-1)."""

import json
import sqlite3

from aiohttp import web

from tlon.api.mail import MailSubmissionError
from tlon.base.logger import get_logger
from tlon_server.api.send_mail import SendMailError, parse_send_mail_fields

from .common import RestHandlerBase, _parse_limit

logger = get_logger("tlon_server.api.rest")


class MailHandlers(RestHandlerBase):
    """Mail route handlers, mixed into ``RESTAPIHandler``."""

    async def handle_inbox(self, request: web.Request) -> web.Response:
        """GET /inbox - The human member's own mail (mail model, R2).

        Query Parameters:
            limit: Maximum number of mails (default: 50, max: 200)
            before: Earliest mail id from previous page; must be visible in this mailbox

        Returns:
            Chronological mails, count, has_more and next_before.
            Invalid username or inaccessible cursor returns 400.
        """
        if not self.mail:
            return self._json_response(
                {"status": "error", "message": "Mail storage not available"}, status=503
            )

        if "username" in request.query:
            return self._json_response(
                {"error": "Identity is managed by the server", "reason": "identity_managed"},
                status=400,
            )
        username = self.identity.require_username()

        limit = _parse_limit(request, default=50, maximum=200)
        if limit <= 0:
            return self._json_response(
                {"error": "limit must be positive", "reason": "invalid_limit"}, status=400
            )
        try:
            result = await self.mail.inbox_page(username, limit, request.query.get("before"))
        except ValueError as exc:
            return self._json_response({"error": str(exc), "reason": "invalid_cursor"}, status=400)
        return self._json_response(result)

    async def handle_member_mail(self, request: web.Request) -> web.Response:
        """GET /members/{member_id}/mail - Any member's mailbox, read-only.

        Observer view: same merge semantics as GET /inbox (inbox + sent,
        chronological), but no ``read`` flag — the read cursor is the
        member's own state. No side effects (no mailbox mkdir).

        Path Parameters:
            member_id: Agent id (``gu-yanxi``) or human id
                (``human:<username>``)

        Query Parameters:
            limit: Maximum number of mails (default: 50, max: 200)
            before: Earliest mail id from previous page; must be visible in this mailbox

        Returns:
            The member's recent mail, 404 when the member does not exist,
            400 when the member id is invalid
        """
        if not self.mail:
            return self._json_response(
                {"status": "error", "message": "Mail storage not available"}, status=503
            )

        member_id = request.match_info["member_id"].strip()
        # The id becomes a directory name — reject empty and anything
        # carrying a path separator (traversal guard)
        if not member_id or "/" in member_id or "\\" in member_id:
            return self._json_response(
                {"error": f"Invalid member_id: {member_id!r}", "reason": "invalid_member_id"},
                status=400,
            )

        limit = _parse_limit(request, default=50, maximum=200)
        if limit <= 0:
            return self._json_response(
                {"error": "limit must be positive", "reason": "invalid_limit"}, status=400
            )
        try:
            result = await self.mail.member_inbox_page(
                member_id, limit, request.query.get("before")
            )
        except ValueError as exc:
            return self._json_response({"error": str(exc), "reason": "invalid_cursor"}, status=400)
        if result is None:
            return self._json_response(
                {"error": f"Member '{member_id}' not found", "reason": "member_not_found"},
                status=404,
            )

        return self._json_response(result)

    async def handle_inbox_read(self, request: web.Request) -> web.Response:
        """POST /inbox/read explicitly changes this human's incoming read state.

        JSON requires read (boolean), and exactly one non-empty
        mail_id or thread_id. A thread includes all incoming history in one
        transaction. Returns mail_ids/read; malformed input is 400 and no
        matching incoming relation is 404. Sent-only copies are not writable.
        """
        if not self.mail:
            return self._json_response({"error": "Mail storage not available"}, status=503)
        try:
            body = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            return self._json_response(
                {"error": "Invalid JSON", "reason": "invalid_json"}, status=400
            )
        if not isinstance(body, dict):
            return self._json_response(
                {"error": "Request body must be a JSON object", "reason": "invalid_body"},
                status=400,
            )
        if "username" in body or "username" in request.query:
            return self._json_response(
                {"error": "Identity is managed by the server", "reason": "identity_managed"},
                status=400,
            )
        username = self.identity.require_username()
        if not isinstance(body.get("read"), bool):
            return self._json_response(
                {"error": "Field 'read' must be a boolean", "reason": "invalid_read"}, status=400
            )
        selectors = [key for key in ("mail_id", "thread_id") if key in body]
        if len(selectors) != 1:
            return self._json_response(
                {
                    "error": "Exactly one of mail_id and thread_id is required",
                    "reason": "invalid_selector",
                },
                status=400,
            )
        selector = selectors[0]
        value = body[selector]
        if not isinstance(value, str) or not value.strip():
            return self._json_response(
                {"error": f"Invalid {selector}", "reason": f"invalid_{selector}"}, status=400
            )
        mail_ids = await self.mail.set_read(
            username.strip(),
            body["read"],
            **{selector: value.strip()},
        )
        if not mail_ids:
            return self._json_response(
                {"error": "Incoming mail not found", "reason": "mail_not_found"}, status=404
            )
        return self._json_response({"mail_ids": mail_ids, "read": body["read"]})

    async def handle_send_message(self, request: web.Request) -> web.Response:
        """POST /messages - Send a mail as a human member (protocol v2, PR-1).

        The sender identity comes from the instance identity. If the
        human has no live adapter, a transient one is created — first
        contact also creates the persistent mailbox, same semantics as
        the first session attach.

        Request Body:
            content: Mail content (required, non-empty)
            to: Recipients (names, ids or full addresses)
            cc: Carbon-copy recipients (optional)
            subject: Optional subject
            in_reply_to: Id of the mail being replied to (optional)
            thread_id: Thread root id inherited from the upstream mail
                (optional)

        Returns:
            202 with a post-office receipt (local inboxes and gateway attempts), or 4xx with
            {"error", "reason"} (e.g. "recipient_unresolved",
            "empty_content", "invalid_to")
        """
        if not self.mail:
            return self._json_response(
                {"status": "error", "message": "Messaging not available"}, status=503
            )

        try:
            body = await request.json()
        except json.JSONDecodeError:
            return self._json_response(
                {"error": "Invalid JSON", "reason": "invalid_json"}, status=400
            )
        if not isinstance(body, dict):
            return self._json_response(
                {"error": "Request body must be a JSON object", "reason": "invalid_json"},
                status=400,
            )

        if "username" in body or "username" in request.query:
            return self._json_response(
                {"error": "Identity is managed by the server", "reason": "identity_managed"},
                status=400,
            )
        username = self.identity.require_username()

        parsed = parse_send_mail_fields(body)
        if isinstance(parsed, SendMailError):
            return self._json_response(
                {"error": parsed.message, "reason": parsed.reason}, status=400
            )

        logger.info(
            "rest_message_received",
            username=username,
            content=parsed.content,
            to=parsed.to,
            cc=parsed.cc,
        )

        try:
            receipt = await self.mail.send(
                username,
                parsed.content,
                to=parsed.to,
                cc=parsed.cc,
                subject=parsed.subject,
                in_reply_to=parsed.in_reply_to,
                thread_id=parsed.thread_id,
                request_id=parsed.request_id,
            )
        except MailSubmissionError as e:
            return self._json_response(
                {"error": str(e), "reason": e.reason, "invalid_recipients": e.invalid_recipients},
                status=409 if e.reason == "idempotency_conflict" else 400,
            )
        except (OSError, sqlite3.Error):
            return self._json_response(
                {
                    "error": "Submission result unknown; retry with the same request_id",
                    "reason": "submission_unknown",
                    "request_id": parsed.request_id,
                },
                status=503,
            )

        return self._json_response(receipt, status=202)
