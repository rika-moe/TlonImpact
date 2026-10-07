"""GitHub webhook endpoint: POST /webhooks/github
(docs/design/mail.md 「外部来信与信任面」).

Ingress direction only: a signed GitHub webhook delivery is verified
(HMAC-SHA256 over the raw body, ``X-Hub-Signature-256``), decoded and
handed to the runtime through the events facade
(``EventsFacade.receive_github_event``), which maps supported events onto
external incoming mail (the external-mail mechanism of
docs/design/mail.md). The route is registered only when
a webhook secret is configured (settings ``github_webhook_secret``) and the
runtime ingress is enabled, so an unconfigured server exposes no endpoint
at all.
"""

import hashlib
import hmac
import json

from aiohttp import web

from tlon.base.logger import get_logger

from .common import RestHandlerBase

logger = get_logger("tlon_server.api.rest")


class GitHubWebhookMixin(RestHandlerBase):
    """GitHub webhook route handler, mixed into ``RESTAPIHandler``."""

    async def handle_github_webhook(self, request: web.Request) -> web.Response:
        """POST /webhooks/github - Receive a GitHub webhook delivery.

        Headers:
            X-Hub-Signature-256: ``sha256=<hex HMAC of the raw body>``
            X-GitHub-Event: Event type (issues, issue_comment, ...)

        Returns:
            202 {"status": "accepted", "mail_id": ...} when the event was
            mapped to an incoming mail, 200 {"status": "ignored"} when the
            adapter skipped it (unsupported event/action), 401 on a bad
            or missing signature, 400 on a missing event header or
            invalid JSON body
        """
        secret = self.github_webhook_secret
        if not secret or not self.github_ingress or self.events is None:
            # Unreachable via the route table (conditional registration);
            # defensive guard for direct handler use.
            return self._json_response(
                {"status": "error", "message": "GitHub webhook not configured"},
                status=503,
            )

        body = await request.read()

        signature = request.headers.get("X-Hub-Signature-256", "")
        expected = "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            logger.warning("github_webhook_rejected", reason="bad_signature")
            return self._json_response(
                {"error": "Invalid signature", "reason": "bad_signature"}, status=401
            )

        event_type = request.headers.get("X-GitHub-Event", "")
        if not event_type:
            return self._json_response(
                {"error": "Missing X-GitHub-Event header", "reason": "missing_event"},
                status=400,
            )

        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            return self._json_response(
                {"error": "Invalid JSON", "reason": "invalid_json"}, status=400
            )
        if not isinstance(payload, dict):
            return self._json_response(
                {"error": "Request body must be a JSON object", "reason": "invalid_json"},
                status=400,
            )

        logger.info("github_webhook_received", event_type=event_type)
        mail_id = await self.events.receive_github_event(event_type, payload)
        if mail_id is None:
            return self._json_response({"status": "ignored"})
        return self._json_response({"status": "accepted", "mail_id": mail_id}, status=202)
