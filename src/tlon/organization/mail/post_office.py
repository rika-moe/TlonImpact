"""Transactional local post office; events announce already committed mail."""

import asyncio
import hashlib
import json
from dataclasses import replace

from tlon.base.events.event import Event, EventType
from tlon.base.events.event_bus import EventBus
from tlon.base.logger import get_logger
from tlon.base.paths import TlonPaths
from tlon.base.tracing.trace_context import get_current_trace, start_span
from tlon.organization.mail.mail import Mail, is_external_address
from tlon.organization.mail.mailbox import Mailbox
from tlon.organization.mail.resolver import RecipientResolver
from tlon.organization.mail.store import MailStore, RetiredRecipients, StoredPost

logger = get_logger(__name__)


class PostRejected(ValueError):  # noqa: N818
    """The whole post was refused before any inbox was changed."""

    def __init__(self, reason: str, invalid_recipients: list[str]) -> None:
        super().__init__(reason)
        self.reason = "invalid_recipients"
        self.invalid_recipients = invalid_recipients


class PostOffice:
    """Commit all local inboxes together, then attempt gateways once."""

    def __init__(self, event_bus: EventBus, resolver: RecipientResolver, tlon_paths: TlonPaths):
        self._bus = event_bus
        self._resolver = resolver
        self._paths = tlon_paths
        self.store = MailStore(tlon_paths.mail_database_file())
        self.store.initialize()

    async def send(
        self,
        *,
        from_address: str,
        to: list[str],
        content: str,
        cc: list[str] | None = None,
        subject: str | None = None,
        in_reply_to: str | None = None,
        thread_id: str | None = None,
        forward_of: str | None = None,
        endorsing: str | None = None,
        request_id: str | None = None,
    ) -> StoredPost:
        """Accept aliases and refuse the entire post for any bad recipient."""
        # Mail requires concrete addresses; resolution errors must include every raw name.
        semantic: dict[str, object] = {
            "from_address": from_address,
            "to": list(dict.fromkeys(to)),
            "cc": list(dict.fromkeys(cc or [])),
            "content": content,
            "subject": subject,
            "in_reply_to": in_reply_to,
            "thread_id": thread_id,
            "forward_of": forward_of,
            "endorsing": endorsing,
        }
        fingerprint = self._fingerprint(semantic)
        if request_id is not None:
            replay = await asyncio.to_thread(
                self.store.get_request, from_address, request_id, fingerprint
            )
            if replay is not None:
                return replay
        raw_to, raw_cc = to, cc or []
        if not raw_to and not raw_cc:
            raise PostRejected("请指定至少一个收件人", [])
        resolved = {raw: self._resolver.resolve_address(raw) for raw in [*raw_to, *raw_cc]}
        invalid: list[str] = []
        for raw, address in resolved.items():
            member = self._resolver.resolve_member(address) if address else None
            if member is None or await asyncio.to_thread(self.store.is_retired, member):
                invalid.append(raw)
        if invalid:
            raise PostRejected("无效收件人：" + ", ".join(invalid), invalid)
        mail = Mail.create(
            from_address=from_address,
            to=[resolved[raw] or raw for raw in raw_to],
            cc=[resolved[raw] or raw for raw in raw_cc],
            content=content,
            subject=subject,
            in_reply_to=in_reply_to,
            thread_id=thread_id,
            forward_of=forward_of,
            endorsing=endorsing,
        )
        return await self._post(mail, request_id, fingerprint)

    async def post(self, mail: Mail, request_id: str | None = None) -> StoredPost:
        """Commit a concrete mail. Replay does not repeat notifications or gateways."""
        return await self._post(mail, request_id)

    async def _post(
        self, mail: Mail, request_id: str | None, fingerprint: str | None = None
    ) -> StoredPost:
        with start_span(
            "mail.post",
            source="post_office",
            attributes={"mail_id": mail.id, "sender": mail.from_address},
        ):
            return await self._submit(mail, request_id, fingerprint)

    async def _submit(
        self, mail: Mail, request_id: str | None, fingerprint: str | None
    ) -> StoredPost:
        data = mail.to_dict()
        for key in ("id", "timestamp"):
            data.pop(key)
        if mail.thread_id == mail.id:
            data["thread_id"] = None
        fingerprint = fingerprint or self._fingerprint(data)
        key = request_id if request_id is not None else mail.id
        replay = await asyncio.to_thread(
            self.store.get_request, mail.from_address, key, fingerprint
        )
        if replay is not None:
            return replay

        invalid: list[str] = []
        local_ids: list[str] = []
        gateways: list[str] = []
        normalized: dict[str, str] = {}
        for address in dict.fromkeys([*mail.to, *mail.cc]):
            concrete = self._resolver.resolve_address(address)
            member = self._resolver.resolve_member(concrete) if concrete else None
            if member is None or await asyncio.to_thread(self.store.is_retired, member):
                invalid.append(address)
                continue
            normalized[address] = concrete or address
            if is_external_address(concrete or address):
                gateways.append(concrete or address)
            elif member not in local_ids:
                local_ids.append(member)
        if invalid:
            raise PostRejected("无效或已退休的收件人：" + ", ".join(invalid), invalid)
        mail = replace(
            mail,
            to=list(dict.fromkeys(normalized[a] for a in mail.to)),
            cc=list(dict.fromkeys(normalized[a] for a in mail.cc)),
        )
        sender_id = self._resolver.resolve_member(mail.from_address)
        try:
            receipt, created = await asyncio.to_thread(
                self.store.accept, mail, sender_id, local_ids, key, fingerprint, gateways
            )
        except RetiredRecipients as exc:
            retired = [
                address
                for address in normalized
                if self._resolver.resolve_member(normalized[address]) in exc.member_ids
            ]
            raise PostRejected("收件人已退休：" + ", ".join(retired), retired) from exc
        if not created:
            return receipt
        await self._notify(EventType.MAIL_NEW, mail, sender_id or mail.from_address)
        for member in local_ids:
            await self._notify(EventType.MAIL_DELIVERED, {"mail": mail, "recipient": member})
        outcomes: dict[str, tuple[str, str]] = {}
        for address in gateways:
            owner = self._resolver.resolve_member(address)
            if owner is not None and owner in outcomes:
                status, error = outcomes[owner]
            else:
                try:
                    if owner is None:
                        raise ValueError("Gateway claim disappeared")
                    await Mailbox(self._paths.mail_database_file(), owner).append_inbox(
                        mail, gateway=True
                    )
                except Exception as exc:
                    status, error = "failed", str(exc)
                else:
                    status, error = "delivered", ""
                    await self._notify(EventType.MAIL_DELIVERED, {"mail": mail, "recipient": owner})
                if owner is not None:
                    outcomes[owner] = (status, error)
            try:
                receipt = await asyncio.to_thread(
                    self.store.update_gateway, mail.id, address, status, error
                )
            except Exception:
                logger.exception("post_office_gateway_status_failed", mail_id=mail.id)
        if mail.endorsing:
            try:
                endorsed = (
                    await Mailbox(self._paths.mail_database_file(), sender_id).find(mail.endorsing)
                    if sender_id
                    else None
                )
                await self._notify(
                    EventType.MAIL_ENDORSED,
                    {
                        "endorser": sender_id or mail.from_address,
                        "mail_id": mail.endorsing,
                        "author": endorsed.from_address if endorsed else None,
                        "thread_id": mail.thread_id,
                    },
                )
            except Exception:
                logger.exception("post_office_endorsement_notification_failed", mail_id=mail.id)
        return receipt

    @staticmethod
    def _fingerprint(data: dict[str, object]) -> str:
        return hashlib.sha256(
            json.dumps(data, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()

    async def _notify(
        self, event_type: EventType, payload: object, source: str = "post_office"
    ) -> None:
        try:
            ctx = get_current_trace()
            await self._bus.publish(
                Event(
                    type=event_type,
                    payload=payload,
                    source=source,
                    trace_id=ctx.trace_id if ctx else None,
                    span_id=ctx.span_id if ctx else None,
                    parent_span_id=ctx.parent_span_id if ctx else None,
                )
            )
        except Exception:
            logger.exception("post_office_notification_failed", event_type=event_type.value)
