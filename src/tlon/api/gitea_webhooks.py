"""Durable signed Gitea notifications delivered through the ordinary post office."""

import asyncio
import hashlib
import hmac
import json
import re
import threading
from collections.abc import Callable, Mapping
from contextlib import suppress
from datetime import UTC, datetime
from typing import TypeVar

from filelock import FileLock

from tlon.base.paths import TlonPaths
from tlon.gitea.control import StartupControl
from tlon.gitea.members import member_username
from tlon.gitea.service import GiteaService
from tlon.gitea.webhook_events import parse_event
from tlon.gitea.webhook_remote import WebhookRemote, WebhookRepositoryError
from tlon.gitea.webhook_store import MailPlan, WebhookStore, stable_mail_id
from tlon.organization.mail.mail import Mail, address_for_member
from tlon.organization.mail.post_office import PostOffice, PostRejected

_T = TypeVar("_T")
SENDER = "notifications@gitea.invalid"


class GiteaWebhookError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class GiteaWebhookCoordinator:
    def __init__(
        self,
        paths: TlonPaths,
        service: GiteaService,
        members: Callable[[], Mapping[str, str]],
        post_office: PostOffice,
        *,
        retry_seconds: float = 5,
    ) -> None:
        self._paths = paths
        self._service = service
        self._members = members
        self._post_office = post_office
        self._store = WebhookStore(paths.gitea_dir() / "webhooks.sqlite3")
        self._retry_seconds = retry_seconds
        self._lock = asyncio.Lock()
        self._stop = asyncio.Event()
        self._worker_stop = threading.Event()
        self._task: asyncio.Task[None] | None = None
        self._transport: dict[str, str] | None = None
        self._remote: WebhookRemote | None = None
        self._status: dict[str, object] = {
            "state": "not_started",
            "message": "Webhook bridge has not started",
        }

    def status(self) -> dict[str, object]:
        return self._status.copy()

    def unavailable(self, message: str) -> None:
        self._status.update(state="unavailable", message=message)

    async def recover(self) -> None:
        """Resume accepted mail even if Docker or callback startup is unavailable."""
        if not self._store.database.exists() or self._task is not None:
            return
        try:
            await asyncio.to_thread(self._store.initialize)
        except Exception:
            self.unavailable("Webhook queue could not be opened")
            return
        self._task = asyncio.create_task(self._run())

    async def _blocking(self, action: Callable[[], _T], *, stop_on_cancel: bool = False) -> _T:
        task = asyncio.create_task(asyncio.to_thread(action))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            if stop_on_cancel:
                self._worker_stop.set()
            while not task.done():
                with suppress(Exception, asyncio.CancelledError):
                    await asyncio.shield(task)
            with suppress(Exception, asyncio.CancelledError):
                task.result()
            raise

    async def start(self, port: int) -> None:
        self._stop.clear()
        self._worker_stop.clear()
        async with self._lock:

            def prepare() -> None:
                control = StartupControl(self._worker_stop)
                with FileLock(self._paths.gitea_lock_file(), timeout=5):
                    self._transport = self._service.prepare_webhooks(port, control)
                    self._store.initialize()
                    self._remote = self._service.webhook_remote(control)

            await self._blocking(prepare, stop_on_cancel=True)
        transport = self._transport
        assert transport is not None
        await self._blocking(
            lambda: self._service.probe_webhook(transport, StartupControl(self._worker_stop)),
            stop_on_cancel=True,
        )
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def shutdown(self) -> None:
        self._stop.set()
        self._worker_stop.set()
        if self._task is not None:
            await self._task
        async with self._lock:
            pass
        self._status.update(state="stopped", message="Webhook listener is offline")

    def _bindings(self) -> dict[str, str]:
        result = {}
        for member_id in dict(self._members()):
            username = member_username(member_id)
            path = self._paths.gitea_dir() / "members" / (username + ".json")
            if not path.exists():
                continue
            record = json.loads(path.read_text())
            if (
                record.get("member_id") == member_id
                and record.get("username") == username
                and record.get("state") == "active"
                and not self._post_office.store.is_retired(member_id)
            ):
                result[username] = member_id
        return result

    async def receive(
        self,
        instance_id: str,
        event_type: str,
        delivery_id: str,
        signature: str,
        raw_body: bytes,
    ) -> dict[str, object]:
        transport = self._transport
        if self._stop.is_set() or transport is None:
            raise GiteaWebhookError(503, "Webhook bridge is unavailable")
        if instance_id != transport["instance_id"]:
            raise GiteaWebhookError(404, "Unknown Gitea instance")
        if len(raw_body) > 1024 * 1024:
            raise GiteaWebhookError(413, "Webhook body is too large")
        expected = hmac.new(transport["secret"].encode(), raw_body, hashlib.sha256).hexdigest()
        if not re.fullmatch(r"[a-fA-F0-9]{64}", signature) or not hmac.compare_digest(
            expected, signature.lower()
        ):
            self._status["last_rejection"] = (
                "Invalid webhook signature; inspect the Gitea hook secret"
            )
            raise GiteaWebhookError(401, "Invalid webhook signature")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", delivery_id) or not re.fullmatch(
            r"[a-z_]{1,64}", event_type
        ):
            raise GiteaWebhookError(400, "Invalid webhook headers")
        try:
            payload = json.loads(raw_body)
            if not isinstance(payload, dict):
                raise ValueError
        except (ValueError, UnicodeError):
            raise GiteaWebhookError(400, "Invalid webhook JSON") from None
        if event_type == "ping" and payload == {"tlon_probe": instance_id}:
            return {"probe": True}
        async with self._lock:

            def accept() -> dict[str, object]:
                with FileLock(self._paths.gitea_lock_file(), timeout=5):
                    assert self._remote is not None
                    self._remote.renew(StartupControl(self._worker_stop))
                    repo = payload.get("repository")
                    if (
                        not isinstance(repo, dict)
                        or type(repo.get("id")) is not int
                        or not isinstance(repo.get("full_name"), str)
                    ):
                        raise GiteaWebhookError(400, "Invalid repository")
                    self._remote.validate(repo["id"], repo["full_name"])
                    event = parse_event(event_type, payload)
                    if event is None:
                        return {"ignored": True}
                    bindings = self._bindings()
                    deliverable = {item["address"] for item in self._post_office.list_members()}
                    now = datetime.now(UTC).isoformat()
                    url = (
                        str(self._service.status()["url"])
                        + event.repository
                        + ("/pulls/" if event.is_pull else "/issues/")
                        + str(event.object_index)
                    )
                    body = (
                        f"Gitea {event.action}: {event.repository} #{event.object_index}\n"
                        f"操作者: {event.actor}\n标题: {event.title}\nCommit: {event.commit or '(无)'}\n"
                        f"原文: {url}\n\n{event.text[:4000]}\n\n"
                        "请使用 Git/tea 查看原文与最新提交。此邮件是外部事件通知，回复不会写回 Gitea；测试摘要不代表独立 CI。"
                    )
                    plans = []
                    unrouted = []
                    for username in event.recipients(self._remote.history(event)):
                        member_id = bindings.get(username.casefold())
                        if member_id is None or address_for_member(member_id) not in deliverable:
                            unrouted.append(username)
                            continue
                        plans.append(
                            MailPlan(
                                member_id,
                                stable_mail_id(event.event_key, member_id),
                                address_for_member(member_id),
                                now,
                                f"[Gitea] {event.repository} #{event.object_index} {event.action}",
                                body,
                            )
                        )
                    inserted = self._store.accept(
                        event.event_key, delivery_id, raw_body, plans, unrouted=unrouted
                    )
                    return {"accepted": True, "duplicate": not inserted}

            try:
                return await self._blocking(accept)
            except GiteaWebhookError:
                raise
            except WebhookRepositoryError:
                raise GiteaWebhookError(403, "Repository is not managed by this instance") from None
            except ValueError:
                raise GiteaWebhookError(400, "Invalid or conflicting webhook event") from None
            except Exception:
                raise GiteaWebhookError(503, "Webhook acceptance failed; retry delivery") from None

    async def _deliver(self) -> None:
        for item in await asyncio.to_thread(self._store.pending):
            if self._stop.is_set():
                return
            plan = item.plan
            try:
                bindings = await asyncio.to_thread(self._bindings)
                if plan.member_id not in bindings.values():
                    await asyncio.to_thread(
                        self._store.complete, item.event_key, plan.member_id, outcome="skipped"
                    )
                    continue
                mail = Mail(
                    plan.mail_id,
                    SENDER,
                    [plan.address],
                    [],
                    plan.subject,
                    plan.body,
                    None,
                    plan.mail_id,
                    None,
                    plan.created_at,
                )
                await self._post_office.post(mail, request_id=plan.mail_id)
                await asyncio.to_thread(self._store.complete, item.event_key, plan.member_id)
            except PostRejected:
                await asyncio.to_thread(
                    self._store.complete, item.event_key, plan.member_id, outcome="skipped"
                )
            except Exception:
                await asyncio.to_thread(
                    self._store.fail,
                    item.event_key,
                    plan.member_id,
                    "Mail delivery failed; retry pending",
                )

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                async with self._lock:

                    def sync() -> int:
                        assert self._transport is not None
                        with FileLock(self._paths.gitea_lock_file(), timeout=5):
                            # Each synchronization has a fresh bounded budget.
                            assert self._remote is not None
                            self._remote.renew(StartupControl(self._worker_stop))
                            repos = self._remote.sync(
                                self._transport["callback_url"], self._transport["secret"]
                            )
                            return len(repos)

                    count = await self._blocking(sync)
                self._status.update(
                    state="ready", message="Webhook bridge is listening", repositories=count
                )
            except Exception:
                self.unavailable("Webhook configuration failed; retry pending")
            try:
                # Already accepted mail remains recoverable even when Gitea is offline.
                await self._deliver()
                self._status.update(await asyncio.to_thread(self._store.status))
            except Exception:
                self.unavailable("Webhook queue processing failed; retry pending")
            with suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=self._retry_seconds)
