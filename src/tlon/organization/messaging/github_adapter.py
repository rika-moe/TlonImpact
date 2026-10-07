"""GitHub Adapter: GitHub webhook events as external mail (see
docs/design/mail.md).

The first external gateway of the "外部来信" mechanism (see
docs/design/mail.md 「外部来信与信任面」), covering both directions:

- **Ingress**: the REST endpoint ``POST /webhooks/github`` verifies the
  HMAC signature and hands the decoded event here; the adapter maps
  supported events onto one ``Mail`` from the real external address
  ``notifications@github.com`` and publishes ``MAIL_NEW`` directly on
  the bus (the cron.py bare-publish precedent). Delivery needs no
  special-casing for external senders: no ``sent`` copy, bounce safely
  dropped, ``MAIL_DELIVERED`` normal, and every rendering point marks
  the mail as untrusted external input.
- **Egress**: ``initialize`` claims ``notifications@github.com`` for the
  gateway member ``gateway:github`` in the ExternalGatewayRegistry, so
  replies to that address resolve and land in the gateway mailbox (the
  outbound audit trail); the adapter's ``MAIL_DELIVERED`` subscription
  then posts the mail body as a GitHub issue/PR comment. Thread mapping
  is "in-memory table + subject anchor fallback" (documented limits in
  docs/design/mail.md); unmapped mail stays in the mailbox, logged, no
  bounce, no retry.

v1 ingress supports a fixed event mapping (no per-event routing
configuration): ``issues`` (opened/assigned), ``issue_comment``
(created), ``pull_request_review`` (submitted) and ``check_run``
(completed with a failure/timed_out conclusion). Everything else —
unknown event types, unsupported actions, malformed payloads — is
skipped (returns None, logged); an unsupported delivery is acknowledged
with 200 "ignored" so GitHub does not retry.

Addressing: the configured ``recipient_id`` member when set, otherwise
all known humans (``known_human_ids()``); with neither, the event is
skipped with a warning. The issue/PR number goes into the subject
(``[owner/repo]``) — the anchor the egress direction falls back to
when its in-memory thread table has no entry (e.g. after a restart).
"""

import re
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

import aiohttp

from tlon.base.device import Device
from tlon.base.events.event import Event, EventType
from tlon.base.logger import get_logger
from tlon.base.paths import TlonPaths
from tlon.organization.mail.gateways import ExternalGatewayRegistry
from tlon.organization.mail.mail import HUMAN_ID_PREFIX, Mail, address_for_member

if TYPE_CHECKING:
    from tlon.base.config import AgentInfoProvider
    from tlon.base.events.event_bus import EventBus

logger = get_logger(__name__)

#: Real-world sender address of GitHub notification mail; the external
#: trust boundary derives from the address domain ().
GITHUB_FROM_ADDRESS = "notifications@github.com"

#: Member id of this gateway on the bus; claimed external addresses
#: resolve to it, and its mailbox is the outbound audit trail.
GATEWAY_MEMBER_ID = "gateway:github"

#: check_run conclusions that warrant a mail (CI failure signals).
_FAILURE_CONCLUSIONS = frozenset({"failure", "timed_out"})

#: Body excerpt cap — a webhook payload body can be arbitrarily long.
_BODY_EXCERPT_CHARS = 1000

#: The ``[owner/repo]`` subject anchor (inbound and outbound share it).
_ANCHOR_RE = re.compile(r"\[([\w.-]+/[\w.-]+)#(\d+)\]")

#: GitHub REST API base (comments endpoint).
_GITHUB_API = "https://api.github.com"

#: Egress HTTP timeout — a wedged connection must not stall the handler.
_HTTP_TIMEOUT = aiohttp.ClientTimeout(total=30)

#: Egress POST injection point: (url, headers, json body) -> status code.
#: Tests fake it; the default implementation uses aiohttp.
HttpPost = Callable[[str, dict[str, str], dict[str, Any]], Awaitable[int]]


async def _aiohttp_post(url: str, headers: dict[str, str], body: dict[str, Any]) -> int:
    """Default egress transport: one short-lived session per call."""
    async with (
        aiohttp.ClientSession(timeout=_HTTP_TIMEOUT) as session,
        session.post(url, headers=headers, json=body) as response,
    ):
        return response.status


def _str(value: Any) -> str:
    """Best-effort string coercion for untrusted payload fields."""
    return value if isinstance(value, str) else ""


def _excerpt(body: Any) -> str:
    """Trim a payload body to a bounded excerpt."""
    text = _str(body).strip()
    if len(text) > _BODY_EXCERPT_CHARS:
        return text[:_BODY_EXCERPT_CHARS] + "…"
    return text


class GitHubAdapter(Device):
    """Maps GitHub webhook events onto external mail, both directions.

    A Device (not a managed Component): constructed at assembly time with
    the event bus, the agent info provider (recipient existence check),
    the paths resolver (known humans), the external gateway registry and
    the egress configuration; ``initialize``/``shutdown`` are called by
    the assembly site (serverapp). Ingress events arrive via the REST
    endpoint calling ``receive``; egress is driven by the
    ``MAIL_DELIVERED`` subscription ``initialize`` installs.
    """

    def __init__(
        self,
        event_bus: "EventBus",
        agent_info_provider: "AgentInfoProvider",
        gateway_registry: ExternalGatewayRegistry,
        tlon_paths: TlonPaths,
        recipient_id: str | None = None,
        github_token: str | None = None,
        http_post: HttpPost | None = None,
    ):
        """Initialize the GitHub adapter.

        Args:
            event_bus: Event bus to publish/subscribe on
            agent_info_provider: Agent existence queries (recipient check)
            gateway_registry: External address claim table; ``initialize``
                claims ``notifications@github.com`` for ``gateway:github``
            tlon_paths: Path resolver (known-humans enumeration)
            recipient_id: Default recipient member id from settings;
                None routes GitHub mail to all known humans
            github_token: GitHub API token for egress comments; None or
                empty disables egress only (ingress unaffected)
            http_post: Egress HTTP injection point (tests); None uses
                the default aiohttp implementation
        """
        super().__init__(event_bus=event_bus, name="GitHubAdapter")
        self._agent_info = agent_info_provider
        self._registry = gateway_registry
        self._paths = tlon_paths
        self._recipient_id = recipient_id
        self._github_token = github_token
        self._http_post: HttpPost = http_post or _aiohttp_post
        # In-memory thread table: inbound mail id -> (repo, issue/PR
        # number). Lost on restart by design — the subject anchor is
        # the fallback (docs/design/mail.md 外部收件人与出口方向).
        self._threads: dict[str, tuple[str, int]] = {}

    async def initialize(self) -> None:
        """Claim the gateway address and subscribe to deliveries."""
        self._registry.claim(GITHUB_FROM_ADDRESS, GATEWAY_MEMBER_ID)
        self.subscribe(EventType.MAIL_DELIVERED, self._on_mail_delivered)
        logger.info("github_adapter_initialized", egress_enabled=bool(self._github_token))

    async def shutdown(self) -> None:
        """Stop the adapter (the address claim is assembly-lifetime)."""
        self.unsubscribe_all()

    async def receive(self, event_type: str, payload: dict[str, Any]) -> Mail | None:
        """Map one GitHub webhook event onto an incoming mail and publish it.

        Args:
            event_type: The ``X-GitHub-Event`` header value
            payload: The decoded JSON delivery body

        Returns:
            The published mail, or None when the event was skipped
            (unsupported type/action, malformed payload, no routable
            recipient)
        """
        mapped = self._map_event(event_type, payload)
        if mapped is None:
            return None
        subject, content = mapped

        to = self._recipient_addresses()
        if not to:
            logger.warning("github_event_skipped", reason="no_recipient", event_type=event_type)
            return None

        mail = Mail.create(
            from_address=GITHUB_FROM_ADDRESS,
            to=to,
            subject=subject,
            content=content,
        )
        await self._event_bus.publish(
            Event(type=EventType.MAIL_NEW, payload=mail, source="github_adapter")
        )
        anchor = self._parse_anchor(mail.subject)
        if anchor is not None:
            self._threads[mail.id] = anchor
        logger.info("github_mail_published", event_type=event_type, mail_id=mail.id, to=mail.to)
        return mail

    # ==================== Egress (reply -> GitHub comment) ====================

    async def _on_mail_delivered(self, event: Event) -> None:
        """Post mail delivered to this gateway as a GitHub comment.

        Errors never propagate: the bus isolates handler failures, but
        the failure facts (mail id, target, status) must be logged here
        — the mail itself stays in the gateway mailbox as the audit
        trail, with no bounce and no retry (docs/design/mail.md).
        """
        payload = event.payload
        if not isinstance(payload, dict) or payload.get("recipient") != GATEWAY_MEMBER_ID:
            return  # not this gateway's delivery
        mail = payload.get("mail")
        if not isinstance(mail, Mail):
            logger.warning("github_egress_bad_payload", payload_type=type(mail).__name__)
            return
        if not self._github_token:
            logger.warning("github_egress_skipped", reason="no_token", mail_id=mail.id)
            return
        if not any(self._registry.owner_of(a) == GATEWAY_MEMBER_ID for a in [*mail.to, *mail.cc]):
            logger.warning("github_egress_skipped", reason="no_claimed_recipient", mail_id=mail.id)
            return
        target = self._thread_target(mail)
        if target is None:
            logger.error("github_egress_unmapped", mail_id=mail.id, subject=mail.subject)
            return
        repo, number = target
        url = f"{_GITHUB_API}/repos/{repo}/issues/{number}/comments"
        headers = {
            "Authorization": f"Bearer {self._github_token}",
            "Accept": "application/vnd.github+json",
        }
        try:
            status = await self._http_post(url, headers, {"body": mail.content})
        except Exception as e:
            logger.error(
                "github_egress_failed", mail_id=mail.id, repo=repo, number=number, error=str(e)
            )
            return
        if not 200 <= status < 300:
            logger.error(
                "github_egress_failed", mail_id=mail.id, repo=repo, number=number, status=status
            )
            return
        logger.info("github_egress_sent", mail_id=mail.id, repo=repo, number=number)

    def _thread_target(self, mail: Mail) -> tuple[str, int] | None:
        """Map an outbound mail to ``(repo, issue number)``.

        The in-memory thread table (keyed by ``in_reply_to``) wins; the
        subject anchor is the fallback — it survives restarts as long as
        the reply keeps the ``[owner/repo#N]`` subject.
        """
        if mail.in_reply_to:
            hit = self._threads.get(mail.in_reply_to)
            if hit is not None:
                return hit
        return self._parse_anchor(mail.subject)

    @staticmethod
    def _parse_anchor(subject: str | None) -> tuple[str, int] | None:
        """Extract ``(repo, number)`` from a ``[owner/repo#N]`` subject."""
        if not subject:
            return None
        match = _ANCHOR_RE.search(subject)
        if match is None:
            return None
        return match.group(1), int(match.group(2))

    # ==================== Event mapping ====================

    def _map_event(self, event_type: str, payload: dict[str, Any]) -> tuple[str, str] | None:
        """Map an event to ``(subject, content)``; None = skip."""
        repository = payload.get("repository")
        repo = _str(repository.get("full_name")) if isinstance(repository, dict) else ""
        if not repo:
            # Ping events carry no repository; other repo-less payloads are
            # malformed for our purposes — either way, skip quietly.
            logger.debug("github_event_skipped", reason="no_repository", event_type=event_type)
            return None

        action = _str(payload.get("action"))
        if event_type == "issues":
            return self._map_issues(repo, action, payload)
        if event_type == "issue_comment":
            return self._map_issue_comment(repo, action, payload)
        if event_type == "pull_request_review":
            return self._map_pull_request_review(repo, action, payload)
        if event_type == "check_run":
            return self._map_check_run(repo, action, payload)

        logger.debug("github_event_skipped", reason="unsupported_event", event_type=event_type)
        return None

    def _map_issues(
        self, repo: str, action: str, payload: dict[str, Any]
    ) -> tuple[str, str] | None:
        if action not in ("opened", "assigned"):
            logger.debug(
                "github_event_skipped",
                reason="unsupported_action",
                event_type="issues",
                action=action,
            )
            return None
        issue = payload.get("issue")
        if not isinstance(issue, dict):
            return self._bad_payload("issues", "issue")
        number = issue.get("number")
        title = _str(issue.get("title"))
        if not isinstance(number, int) or not title:
            return self._bad_payload("issues", "issue.number/title")
        author = _str(self._user_login(issue))
        assignee_obj = payload.get("assignee")
        assignee = _str(assignee_obj.get("login") if isinstance(assignee_obj, dict) else None)
        url = _str(issue.get("html_url"))
        if action == "opened":
            summary = f"新 issue: {title}"
        else:
            summary = f"issue 指派给 {assignee or '?'}: {title}"
        content = self._content(
            f"GitHub issue 事件（{action}）。",
            [
                ("仓库", repo),
                ("标题", title),
                ("作者", author),
                ("指派给", assignee),
                ("链接", url),
            ],
            issue.get("body"),
        )
        return f"[{repo}#{number}] {summary}", content

    def _map_issue_comment(
        self, repo: str, action: str, payload: dict[str, Any]
    ) -> tuple[str, str] | None:
        if action != "created":
            logger.debug(
                "github_event_skipped",
                reason="unsupported_action",
                event_type="issue_comment",
                action=action,
            )
            return None
        issue = payload.get("issue")
        comment = payload.get("comment")
        if not isinstance(issue, dict) or not isinstance(comment, dict):
            return self._bad_payload("issue_comment", "issue/comment")
        number = issue.get("number")
        if not isinstance(number, int):
            return self._bad_payload("issue_comment", "issue.number")
        kind = "PR" if isinstance(issue.get("pull_request"), dict) else "issue"
        author = _str(self._user_login(comment))
        url = _str(comment.get("html_url"))
        title = _str(issue.get("title"))
        content = self._content(
            f"GitHub {kind} 新评论。",
            [("仓库", repo), (f"{kind} 标题", title), ("评论者", author), ("链接", url)],
            comment.get("body"),
        )
        return f"[{repo}#{number}] {kind} 新评论 ({author})", content

    def _map_pull_request_review(
        self, repo: str, action: str, payload: dict[str, Any]
    ) -> tuple[str, str] | None:
        if action != "submitted":
            logger.debug(
                "github_event_skipped",
                reason="unsupported_action",
                event_type="pull_request_review",
                action=action,
            )
            return None
        pr = payload.get("pull_request")
        review = payload.get("review")
        if not isinstance(pr, dict) or not isinstance(review, dict):
            return self._bad_payload("pull_request_review", "pull_request/review")
        number = pr.get("number")
        if not isinstance(number, int):
            return self._bad_payload("pull_request_review", "pull_request.number")
        state = _str(review.get("state"))
        author = _str(self._user_login(review))
        url = _str(review.get("html_url"))
        title = _str(pr.get("title"))
        content = self._content(
            "GitHub PR review 提交。",
            [
                ("仓库", repo),
                ("PR 标题", title),
                ("review 状态", state),
                ("reviewer", author),
                ("链接", url),
            ],
            review.get("body"),
        )
        return f"[{repo}#{number}] PR review {state or 'submitted'} ({author})", content

    def _map_check_run(
        self, repo: str, action: str, payload: dict[str, Any]
    ) -> tuple[str, str] | None:
        check_run = payload.get("check_run")
        if not isinstance(check_run, dict):
            return self._bad_payload("check_run", "check_run")
        conclusion = _str(check_run.get("conclusion"))
        if action != "completed" or conclusion not in _FAILURE_CONCLUSIONS:
            logger.debug(
                "github_event_skipped",
                reason="not_a_failure",
                event_type="check_run",
                action=action,
                conclusion=conclusion,
            )
            return None
        name = _str(check_run.get("name"))
        url = _str(check_run.get("html_url"))
        branch = _str(self._head_branch(check_run))
        output = check_run.get("output")
        summary = output.get("summary") if isinstance(output, dict) else None
        content = self._content(
            "GitHub CI check 失败。",
            [
                ("仓库", repo),
                ("check", name),
                ("结论", conclusion),
                ("分支", branch),
                ("链接", url),
            ],
            summary,
        )
        return f"[{repo}] CI 失败: {name or '?'} ({conclusion})", content

    # ==================== Helpers ====================

    def _recipient_addresses(self) -> list[str]:
        """Route: configured recipient, else all known humans; [] = unroutable."""
        if self._recipient_id:
            if self._member_exists(self._recipient_id):
                return [address_for_member(self._recipient_id)]
            logger.warning("github_recipient_not_found", recipient=self._recipient_id)
            return []
        return [address_for_member(human_id) for human_id in self._paths.known_human_ids()]

    def _member_exists(self, member_id: str) -> bool:
        """Recipient existence: agent ids from the registry, humans from disk."""
        if member_id.startswith(HUMAN_ID_PREFIX):
            return member_id in self._paths.known_human_ids()
        return member_id in self._agent_info.get_all_ids()

    @staticmethod
    def _user_login(obj: Any) -> Any:
        """``obj["user"]["login"]`` with dict guards."""
        if not isinstance(obj, dict):
            return None
        user = obj.get("user")
        return user.get("login") if isinstance(user, dict) else None

    @staticmethod
    def _head_branch(check_run: dict[str, Any]) -> Any:
        pull_requests = check_run.get("pull_requests")
        if isinstance(pull_requests, list) and pull_requests:
            first = pull_requests[0]
            head = first.get("head") if isinstance(first, dict) else None
            if isinstance(head, dict):
                return head.get("ref")
        return check_run.get("head_branch")

    @staticmethod
    def _bad_payload(event_type: str, field: str) -> tuple[str, str] | None:
        """Log a malformed payload; callers return its None right away."""
        logger.warning(
            "github_event_skipped", reason="bad_payload", event_type=event_type, field=field
        )
        return None

    @staticmethod
    def _content(lead: str, fields: list[tuple[str, str]], body: Any) -> str:
        """Assemble the mail body: lead line, key fields, body excerpt."""
        lines = [lead, "这是 GitHub webhook 的外部来信。", ""]
        lines.extend(f"{label}: {value}" for label, value in fields if value)
        excerpt = _excerpt(body)
        if excerpt:
            lines.extend(["", "---", excerpt])
        return "\n".join(lines)
