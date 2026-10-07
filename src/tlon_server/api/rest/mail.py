"""Mail endpoints: GET /inbox (human member mailbox), GET
/members/{member_id}/mail (read-only observer view of any member's
mailbox) and POST /messages (send a mail as a human member, protocol v2
PR-1)."""

import json

from aiohttp import web

from tlon.api.mail import DEFAULT_USERNAME
from tlon.base.logger import get_logger
from tlon_server.api.send_mail import SendMailError, parse_send_mail_fields

from .common import RestHandlerBase, _parse_limit

logger = get_logger("tlon_server.api.rest")


class MailHandlers(RestHandlerBase):
    """Mail route handlers, mixed into ``RESTAPIHandler``."""

    async def handle_inbox(self, request: web.Request) -> web.Response:
        """GET /inbox - The human member's own mail (mail model, R2).

        Query Parameters:
            username: Human username (default: "guest"); the inbox of
                "human:<username>" is served
            limit: Maximum number of mails (default: 50, max: 200)

        Returns:
            The member's recent mail (inbox + sent merged, chronological)
            or 400 if the username is invalid
        """
        if not self.mail:
            return self._json_response(
                {"status": "error", "message": "Mail storage not available"}, status=503
            )

        username_raw = request.query.get("username")
        if username_raw is None:
            username = DEFAULT_USERNAME
        else:
            username = username_raw.strip()
            # The username becomes a directory name — reject empty and
            # anything carrying a path separator (traversal guard)
            if not username or "/" in username or "\\" in username:
                return self._json_response(
                    {"error": f"Invalid username: {username_raw!r}"}, status=400
                )

        limit = _parse_limit(request, default=50, maximum=200)
        result = await self.mail.inbox(username, limit)

        return self._json_response(
            {
                "mails": result,
                "count": len(result),
            }
        )

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
                {"error": f"Invalid member_id: {member_id!r}"}, status=400
            )

        limit = _parse_limit(request, default=50, maximum=200)
        result = await self.mail.member_inbox(member_id, limit)
        if result is None:
            return self._json_response({"error": f"Member '{member_id}' not found"}, status=404)

        return self._json_response(
            {
                "mails": result,
                "count": len(result),
            }
        )

    async def handle_send_message(self, request: web.Request) -> web.Response:
        """POST /messages - Send a mail as a human member (protocol v2, PR-1).

        The sender's identity comes from the body's ``username``. If the
        human has no live adapter, a transient one is created — first
        contact also creates the persistent mailbox, same semantics as
        the first session attach.

        Request Body:
            username: Human username (default: "guest")
            content: Mail content (required, non-empty)
            to: Recipients (names, ids or full addresses)
            cc: Carbon-copy recipients (optional)
            subject: Optional subject
            in_reply_to: Id of the mail being replied to (optional)
            thread_id: Thread root id inherited from the upstream mail
                (optional)

        Returns:
            202 Accepted with {"mail_id": ...}, or 4xx with
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

        username_raw = body.get("username", DEFAULT_USERNAME)
        if not isinstance(username_raw, str):
            return self._json_response(
                {"error": "Field 'username' must be a string", "reason": "invalid_username"},
                status=400,
            )
        username = username_raw.strip()
        # The username becomes a directory name — reject empty and
        # anything carrying a path separator (traversal guard)
        if not username or "/" in username or "\\" in username:
            return self._json_response(
                {"error": f"Invalid username: {username_raw!r}", "reason": "invalid_username"},
                status=400,
            )

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
            mail_id = await self.mail.send(
                username,
                parsed.content,
                to=parsed.to,
                cc=parsed.cc,
                subject=parsed.subject,
                in_reply_to=parsed.in_reply_to,
                thread_id=parsed.thread_id,
            )
        except ValueError as e:
            return self._json_response(
                {"error": str(e), "reason": "recipient_unresolved"}, status=400
            )

        return self._json_response({"mail_id": mail_id}, status=202)
