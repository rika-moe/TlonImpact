"""Declared cron mail drives the ordinary Lisp social newspaper end to end."""

import asyncio
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from tests.mail_helpers import TestMembers as MemberDirectory
from tests.mail_helpers import make_office
from tlon.agents.cron import CronScheduler
from tlon.agents.member_runtime import MemberRuntime
from tlon.agents.program_package import read_module_sources, validate_program_sources
from tlon.agents.vocabulary import make_member_builtins
from tlon.base.events.event_bus import EventBus
from tlon.base.paths import TlonPaths
from tlon.evolution.list_staging import StagingCatalog
from tlon.evolution.ports import PrototypeSummary
from tlon.operations.core import CallContext, OperationExecutor, OperationRegistry
from tlon.operations.dsl import DSLAdapter
from tlon.operations.evolution import make_evolution_operations
from tlon.operations.model import make_model_operations
from tlon.operations.observability import make_observability_operations
from tlon.organization.mail.mail import POSTMASTER_ADDRESS, Mail
from tlon.organization.mail.mailbox import Mailbox
from tlon.organization.mail.store import StoredPost
from tlosure.edn import Keyword

EXAMPLE = Path(__file__).parents[2] / "tlon/resources/examples/social-newspaper/behavior.tlon"
ADDRESS = "newspaper@tlon.internal"


class SocialNewspaper:
    """Real runtime, durable effects and post office, with deterministic model/observations."""

    def __init__(self, home: Path) -> None:
        self.home = home
        self.bus = EventBus()
        self.members = MemberDirectory(["newspaper", "subscriber", "operator", "outsider"])
        self.office = make_office(self.bus, home, list(self.members.get_all_ids()))
        self.llm = SimpleNamespace(
            chat=AsyncMock(
                return_value=SimpleNamespace(
                    content="本期社会文摘：观测指标仅描述活动，不证明成员已阅读或完成任务。",
                    usage={"completion_tokens": 20},
                    tool_calls=[],
                    finish_reason="stop",
                    reasoning_content=None,
                )
            )
        )
        self.observations: list[str] = []
        self.member = self.runtime()
        (home / "cron.yaml").write_text(
            "- name: society-paper\n  to: newspaper\n  subject: publish\n"
            "  content: 执行周期出版。\n  interval_seconds: 86400\n",
            encoding="utf-8",
        )
        self.cron = CronScheduler(
            self.bus, self.members, TlonPaths(home=home), post_office=self.office
        )

    def metrics(self) -> dict[str, Any]:
        self.observations.append("metrics-read")
        return {
            "metrics": {
                "tlon.mail.sent": {
                    "description": "Accepted mails",
                    "points": [{"attributes": {}, "value": 7}],
                }
            }
        }

    async def health(self) -> dict[str, Any]:
        self.observations.append("social-health")
        return {
            "scanned_members": ["newspaper"],
            "scanned_count": 1,
            "failed_members": [],
            "orphan_count": 0,
            "orphans": [],
        }

    def staging(self) -> Sequence[PrototypeSummary]:
        self.observations.append("prototype-staging-list")
        return [
            SimpleNamespace(
                name="bounded-reporter",
                display_name="有界采集",
                description="测试提案",
                author="operator",
                parent=None,
                generation=0,
            )
        ]

    def runtime(self) -> MemberRuntime:
        # Bootstrap the host with a minimal admitted program, then bind the same
        # real operation registry to the shipped, validated member program.
        member = MemberRuntime(
            member_id="newspaper",
            name="社会文摘报",
            event_bus=self.bus,
            spec=validate_program_sources(
                "{:version 2 :handlers {:mail-delivered fixture/handle}}",
                {"fixture.tlon": "(ns fixture (:export handle)) (defn handle [e] {:result nil})"},
            ),
            builtins=make_member_builtins(),
            mailbox=Mailbox(self.home / "shared/mail.sqlite3", "newspaper"),
            post_office=self.office,
            tlon_paths=TlonPaths(home=self.home),
        )
        observations = make_observability_operations(
            member_id="newspaper",
            mail_stats_provider=None,
            reputation_provider=None,
            orphan_provider=self.health,
            metrics_snapshot=self.metrics,
        )
        evolution = make_evolution_operations(
            "newspaper", staging=StagingCatalog(self.staging)
        )
        adapter = DSLAdapter(
            OperationExecutor(
                OperationRegistry(
                    (
                        *member._operations._executor.registry.definitions(),
                        *make_model_operations(
                            self.llm, AsyncMock(return_value="据实编辑，保留来源。")
                        ),
                        *(
                            op
                            for op in observations
                            if op.name in {"metrics-read", "social-health"}
                        ),
                        *(op for op in evolution if op.name == "prototype-staging-list"),
                    )
                ),
                CallContext("newspaper", "member", "member"),
                store=member._program_store,
            )
        )
        member._builtins.update(adapter.builtins())
        member._spec = validate_program_sources(
            EXAMPLE.read_text(), read_module_sources(EXAMPLE.parent / "modules"), member._builtins
        )
        member._operation_executor = adapter._executor
        member._operations = adapter
        return member

    async def settle(self, mail: Mail) -> None:
        mailbox = Mailbox(self.home / "shared/mail.sqlite3", "newspaper")
        async with asyncio.timeout(5):
            while True:
                run = self.member._program_store.get_run(f"{mail.id}:handler:mail-delivered")
                if run is not None and run.error:
                    raise AssertionError(run.error)
                if (
                    run is not None
                    and run.completed
                    and mail.id not in [item.id for item in await mailbox.unread()]
                ):
                    return
                await asyncio.sleep(0.01)

    async def send(self, sender: str, subject: str, content: str = "命令") -> Mail:
        receipt = await self.office.send(
            from_address=sender, to=[ADDRESS], subject=subject, content=content
        )
        await self.settle(receipt.mail)
        return receipt.mail

    async def tick(self, timestamp: str) -> Mail:
        create = Mail.create

        def stamped(**kwargs: Any) -> Mail:
            return replace(create(**kwargs), timestamp=timestamp)

        # Freeze the incoming task's timestamp, not the member's recovery clock.
        with patch("tlon.agents.cron.Mail.create", side_effect=stamped):
            mail = await self.cron.tick(self.cron._entries[0])
        assert mail is not None
        await self.settle(mail)
        return mail

    async def inbox(self, member_id: str) -> list[Mail]:
        return await Mailbox(self.home / "shared/mail.sqlite3", member_id).inbox()

    async def restart(self) -> None:
        await self.member.shutdown()
        self.member = self.runtime()
        await self.member.initialize()


@asynccontextmanager
async def newspaper(home: Path) -> AsyncIterator[SocialNewspaper]:
    paper = SocialNewspaper(home)
    await paper.bus.initialize()
    await paper.member.initialize()
    await paper.cron.initialize()
    try:
        yield paper
    finally:
        await paper.cron.shutdown()
        await paper.member.shutdown()
        await paper.bus.shutdown()


async def test_declared_cron_publishes_only_to_explicit_subscribers_and_reuses_utc_issue(
    tmp_path: Path,
) -> None:
    async with newspaper(tmp_path) as paper:
        assert len(paper.cron._entries) == 1
        first_tick = await paper.tick("2026-10-25T00:01:00+00:00")
        assert first_tick.from_address == POSTMASTER_ADDRESS
        assert first_tick.subject == "publish"
        assert paper.observations == []
        paper.llm.chat.assert_not_awaited()

        await paper.send("subscriber@tlon.internal", "subscribe", "请替 outsider 订阅")
        await paper.send("outsider@tlon.internal", "publish:forbidden")
        assert paper.member._program_store.state_get("edition:forbidden") is None
        assert paper.observations == []

        await paper.tick("2026-10-25T01:00:00+00:00")
        assert paper.observations == ["metrics-read", "social-health", "prototype-staging-list"]
        paper.llm.chat.assert_awaited_once()
        assert "bounded-reporter" in str(paper.llm.chat.call_args)
        first = (await paper.inbox("subscriber"))[0]
        frozen = paper.member._program_store.state_get("edition:2026-10-25")
        assert frozen[Keyword("content")] == first.content
        assert "observation:2026-10-25:metrics-read" in first.content
        assert "observation:2026-10-25:social-health" in first.content
        assert "observation:2026-10-25:prototype-staging-list" in first.content
        assert await paper.inbox("outsider") == []
        assert await paper.inbox("operator") == []

        await paper.restart()
        await paper.tick("2026-10-25T23:59:00+00:00")
        assert paper.observations == ["metrics-read", "social-health", "prototype-staging-list"]
        assert len(await paper.inbox("subscriber")) == 1
        paper.llm.chat.assert_awaited_once()

        await paper.tick("2026-10-26T00:00:00+00:00")
        assert paper.observations == ["metrics-read", "social-health", "prototype-staging-list"] * 2
        assert paper.llm.chat.await_count == 2
        assert len(await paper.inbox("subscriber")) == 2
        assert paper.member._program_store.state_get("edition:2026-10-26") is not None


async def test_runtime_restart_after_publication_preserves_body_and_delivery_receipt(
    tmp_path: Path,
) -> None:
    async with newspaper(tmp_path) as paper:
        await paper.send("subscriber@tlon.internal", "subscribe")
        crashed = asyncio.Event()
        send = paper.office.send
        request_ids: list[str] = []

        async def interrupt_after_delivery(**kwargs: Any) -> StoredPost:
            receipt = await send(**kwargs)
            if kwargs["from_address"] == ADDRESS:
                request_ids.append(kwargs["request_id"])
                if len(request_ids) == 1:
                    # Mail is committed, but mail-send has not returned and neither
                    # its program receipt nor the DSL delivered map can be written.
                    crashed.set()
                    raise asyncio.CancelledError()
            return receipt

        paper.office.send = interrupt_after_delivery
        trigger = await paper.office.send(
            from_address=POSTMASTER_ADDRESS,
            to=[ADDRESS],
            subject="publish:recovery",
            content="出版并保留投递回执。",
        )
        await asyncio.wait_for(crashed.wait(), 5)
        assert len(await paper.inbox("subscriber")) == 1
        frozen = paper.member._program_store.state_get("edition:recovery")
        first = (await paper.inbox("subscriber"))[0]
        assert frozen[Keyword("content")] == first.content
        assert frozen[Keyword("delivered")] == {}
        assert len(paper.member._program_store.pending()) == 1

        await paper.restart()
        await paper.settle(trigger.mail)
        await paper.send("operator@tlon.internal", "publish:recovery")
        assert paper.member._program_store.pending() == []
        assert paper.observations == ["metrics-read", "social-health", "prototype-staging-list"]
        paper.llm.chat.assert_awaited_once()
        inbox = await paper.inbox("subscriber")
        assert len(inbox) == 1
        assert inbox[0].id == first.id
        assert inbox[0].content == frozen[Keyword("content")]
        assert request_ids == ["edition:recovery:subscriber@tlon.internal"] * 2
