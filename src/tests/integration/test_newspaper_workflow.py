"""The shipped newspaper DSL against durable program state and the real post office."""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from tests.mail_helpers import make_office
from tlon.agents.program_driver import ProgramDriver
from tlon.agents.program_package import (
    program_builtins,
    program_module_sources,
    read_module_sources,
    validate_program_sources,
)
from tlon.agents.program_store import ProgramStore
from tlon.agents.vocabulary import make_member_builtins
from tlon.base.events.event_bus import EventBus
from tlon.operations.core import CallContext, OperationExecutor, OperationRegistry
from tlon.operations.dsl import DSLAdapter
from tlon.operations.invocation import Invocation, registry_identity
from tlon.operations.mail import make_mail_operations
from tlon.operations.model import make_model_operations
from tlon.operations.schema import UnknownOutcomeError
from tlon.operations.state import make_state_operations
from tlon.organization.mail.mail import Mail
from tlon.organization.mail.mailbox import Mailbox
from tlon.organization.mail.member_mail import MemberMailService
from tlosure.edn import Keyword, dumps
from tlosure.modules import ModulePackage

EXAMPLE = Path(__file__).parents[2] / "tlon/resources/examples/newspaper/behavior.tlon"
ADDRESS = "newspaper@tlon.internal"
ADMIN = "operator@tlon.internal"


class PowerLoss(BaseException):
    """A process interruption, including the committed-mail/unrecorded-result window."""


class NewspaperHarness:
    """Generic program interpreter fixture; all business decisions stay in the DSL."""

    def __init__(self, home: Path) -> None:
        self.home = home
        self.modules = read_module_sources(EXAMPLE.parent / "modules")
        self.spec = validate_program_sources(EXAMPLE.read_text(), self.modules)
        self.store = ProgramStore(
            home / "newspaper/program.sqlite3", build_id=registry_identity(make_member_builtins())
        )
        self.office = make_office(
            EventBus(), home, ["newspaper", "a", "b", "c", "d", "reporter", "operator"]
        )
        self.llm = SimpleNamespace(
            chat=AsyncMock(
                return_value=SimpleNamespace(
                    content="来信简报：两位投稿人报告了各自观察，相关陈述尚待独立核实。",
                    usage={"completion_tokens": 30},
                    tool_calls=[],
                    finish_reason="stop",
                    reasoning_content=None,
                )
            )
        )
        self.crash_after_address: str | None = None
        self.fail_before_address: str | None = None

    @staticmethod
    def message(sender: str, subject: str, content: str = "命令") -> Mail:
        return Mail.create(from_address=sender, to=[ADDRESS], subject=subject, content=content)

    async def execute(self, mail: Mail, invocation: str = "") -> None:
        run_id = f"mail:{mail.id}:handler:mail-delivered:{invocation}"
        run = self.store.ensure_entry(
            run_id,
            self.spec.to_edn(),
            dumps(mail.to_dict()),
            {"modules": program_module_sources(self.modules)},
        )
        if run.completed:
            return
        event = {
            "mail": mail.to_dict(),
            "sender": mail.from_address,
            "self": {"id": "newspaper", "address": ADDRESS, "name": "来信报"},
        }

        mailbox = Mailbox(self.home / "shared/mail.sqlite3", "newspaper")
        await mailbox.append_inbox(mail)
        service = MemberMailService("newspaper", mailbox, self.office)
        send = service.send

        async def interrupted_send(arguments: dict[str, Any], request_id: str) -> dict[str, Any]:
            address = arguments["to"][0]
            if address == self.fail_before_address:
                self.fail_before_address = None
                raise OSError("temporary delivery failure")
            receipt = await send(arguments, request_id)
            if address == self.crash_after_address:
                self.crash_after_address = None
                raise PowerLoss()
            return receipt

        service.send = interrupted_send
        operations = DSLAdapter(
            OperationExecutor(
                OperationRegistry(
                    (
                        *make_mail_operations(service),
                        *make_state_operations(self.store),
                        *make_model_operations(
                            self.llm, AsyncMock(return_value="保留投稿来源，诚实编辑。")
                        ),
                    )
                ),
                CallContext("newspaper", run_id, run_id),
                store=self.store,
            )
        )

        builtins = {**program_builtins(), **operations.builtins()}
        package = ModulePackage.from_sources(program_module_sources(self.modules), builtins)
        await ProgramDriver(
            self.store,
            run_id,
            builtins,
            operations=operations,
            member_id="newspaper",
        ).run_entry(package.entry("newspaper.main/handle"), event, fuel=10000)
        self.store.complete_entry(run_id, None)

    def restart(self) -> None:
        self.store = ProgramStore(
            self.home / "newspaper/program.sqlite3",
            build_id=registry_identity(make_member_builtins()),
        )

    async def inbox(self, member: str) -> list[Mail]:
        return await Mailbox(self.home / "shared/mail.sqlite3", member).inbox()


async def test_newsroom_freezes_sources_and_recovers_each_real_delivery(tmp_path: Path) -> None:
    paper = NewspaperHarness(tmp_path)
    for person in ("a", "b", "c"):
        await paper.execute(paper.message(f"{person}@tlon.internal", "subscribe"))
    await paper.execute(paper.message("c@tlon.internal", "unsubscribe"))
    first = paper.message("reporter@tlon.internal", "submit", "第一条观察。")
    second = paper.message("a@tlon.internal", "submit", "第二条观察。")
    await paper.execute(first)
    # Reimporting the same mail in a distinct invocation also exercises DSL de-duplication.
    await paper.execute(first, invocation="reimport")
    await paper.execute(second)
    assert paper.store.state_get("pending") == [first.id, second.id]
    publish = paper.message(ADMIN, "publish:001")
    paper.crash_after_address = "a@tlon.internal"
    with pytest.raises(PowerLoss):
        await paper.execute(publish)
    frozen = paper.store.state_get("edition:001")
    assert frozen[Keyword("source-ids")] == [first.id, second.id]
    assert frozen[Keyword("subscribers")] == ["a@tlon.internal", "b@tlon.internal"]
    assert len(await paper.inbox("a")) == 1
    assert await paper.inbox("b") == []
    assert frozen[Keyword("delivered")] == {}

    paper.restart()
    paper.fail_before_address = "b@tlon.internal"
    with pytest.raises(UnknownOutcomeError, match="temporary delivery failure"):
        await paper.execute(publish)
    assert len(await paper.inbox("a")) == 1
    assert "a@tlon.internal" in paper.store.state_get("edition:001")[Keyword("delivered")]
    paper.restart()
    await paper.execute(publish)
    assert paper.llm.chat.await_count == 1
    for person in ("a", "b"):
        inbox = await paper.inbox(person)
        assert len(inbox) == 1
        assert inbox[0].content == frozen[Keyword("content")]
        assert first.id in inbox[0].content and second.id in inbox[0].content
    assert await paper.inbox("c") == []

    # A new command for an existing issue must use its frozen recipient/content snapshot.
    await paper.execute(paper.message("d@tlon.internal", "subscribe"))
    third = paper.message("reporter@tlon.internal", "submit", "后续投稿。")
    await paper.execute(third)
    await paper.execute(paper.message(ADMIN, "publish:001"))
    assert paper.llm.chat.await_count == 1
    assert await paper.inbox("d") == []
    assert paper.store.state_get("pending") == [third.id]
    assert len(await paper.inbox("a")) == 1
    assert len(await paper.inbox("b")) == 1
    await paper.execute(paper.message(ADMIN, "publish:002"))
    assert paper.llm.chat.await_count == 2
    assert paper.store.state_get("edition:002")[Keyword("source-ids")] == [third.id]
    assert len(await paper.inbox("d")) == 1


async def test_empty_issue_and_unprivileged_publish_do_not_call_model(tmp_path: Path) -> None:
    paper = NewspaperHarness(tmp_path)
    await paper.execute(paper.message("a@tlon.internal", "subscribe", "替 b 订阅"))
    assert paper.store.state_get("subscribers") == ["a@tlon.internal"]
    await paper.execute(paper.message(ADMIN, "publish:empty"))
    assert paper.store.state_get("edition:empty")[Keyword("source-ids")] == []
    paper.llm.chat.assert_not_awaited()
    await paper.execute(paper.message("reporter@tlon.internal", "submit", "消息"))
    await paper.execute(paper.message("a@tlon.internal", "publish:forbidden"))
    assert paper.store.state_get("edition:forbidden") is None
    paper.llm.chat.assert_not_awaited()
    assert await paper.inbox("a") == []


@pytest.mark.parametrize("body", ["", " \n\t"])
async def test_blank_editorial_is_not_frozen_or_sent(tmp_path: Path, body: str) -> None:
    paper = NewspaperHarness(tmp_path)
    paper.llm.chat.return_value.content = body
    await paper.execute(paper.message("a@tlon.internal", "subscribe"))
    story = paper.message("reporter@tlon.internal", "submit", "消息")
    await paper.execute(story)
    await paper.execute(paper.message(ADMIN, "publish:invalid"))
    assert paper.store.state_get("edition:invalid") is None
    assert paper.store.state_get("pending") == [story.id]
    assert await paper.inbox("a") == []


async def test_member_runtime_recovers_a_read_publication_before_program_completion(
    tmp_path: Path,
) -> None:
    """Real event delivery and startup recovery do not depend on unread mail."""
    import asyncio

    from tlon.agents.member_runtime import MemberRuntime
    from tlon.base.paths import TlonPaths

    paper = NewspaperHarness(tmp_path)
    bus = EventBus()
    paper.office = make_office(bus, tmp_path, ["newspaper", "a", "reporter", "operator"])

    def runtime() -> MemberRuntime:
        member = MemberRuntime(
            member_id="newspaper",
            name="来信报",
            event_bus=bus,
            spec=validate_program_sources(
                "{:version 2 :handlers {:mail-delivered fixture/handle}}",
                {"fixture.tlon": "(ns fixture (:export handle)) (defn handle [e] {:result nil})"},
            ),
            builtins=make_member_builtins(),
            mailbox=Mailbox(tmp_path / "shared/mail.sqlite3", "newspaper"),
            post_office=paper.office,
            tlon_paths=TlonPaths(home=tmp_path),
        )
        adapter = DSLAdapter(
            OperationExecutor(
                OperationRegistry(
                    (
                        *member._operations._executor.registry.definitions(),
                        *make_model_operations(
                            paper.llm, AsyncMock(return_value="诚实编辑，保留来源。")
                        ),
                    )
                ),
                CallContext("newspaper", "member", "member"),
                store=member._program_store,
            )
        )
        member._spec = paper.spec
        member._operation_executor = adapter._executor
        member._operations = adapter
        member._builtins.update(adapter.builtins())
        return member

    member = runtime()
    await bus.initialize()
    await member.initialize()
    mailbox = Mailbox(tmp_path / "shared/mail.sqlite3", "newspaper")

    async def send_and_drain(sender: str, subject: str, content: str = "命令") -> Mail:
        receipt = await paper.office.send(
            from_address=sender, to=[ADDRESS], subject=subject, content=content
        )

        async def completed() -> None:
            for _ in range(300):
                if (
                    receipt.mail.id not in [mail.id for mail in await mailbox.unread()]
                    and not member._program_store.pending()
                ):
                    return
                await asyncio.sleep(0.01)
            raise AssertionError(f"program did not complete: {subject}")

        await asyncio.wait_for(completed(), 5)
        return receipt.mail

    try:
        await send_and_drain("a@tlon.internal", "subscribe")
        story = await send_and_drain("reporter@tlon.internal", "submit", "真实触发投稿。")
        crashed = asyncio.Event()
        loop = asyncio.get_running_loop()

        def interrupt_before_complete(run_id: str, result: Any, scheduling: Any) -> None:
            loop.call_soon_threadsafe(crashed.set)
            raise asyncio.CancelledError()

        member._program_store.complete_entry = interrupt_before_complete
        await paper.office.send(
            from_address=ADMIN, to=[ADDRESS], subject="publish:runtime", content="出版"
        )
        await asyncio.wait_for(crashed.wait(), 5)
        assert await mailbox.unread() == []
        assert len(member._program_store.pending()) == 1
        assert len(await paper.inbox("a")) == 1
        await member.shutdown()

        member = runtime()
        await asyncio.wait_for(member.initialize(), 5)

        async def recovery_completed() -> None:
            for _ in range(300):
                # Recovery precedes deferred boot work. An empty mail queue
                # or a gap between those runs does not mean startup is idle.
                if (
                    not member._deferred_boot
                    and not member.is_busy
                    and not member._program_store.pending()
                ):
                    return
                await asyncio.sleep(0.01)
            raise AssertionError("startup recovery did not complete")

        await asyncio.wait_for(recovery_completed(), 5)
        await asyncio.wait_for(member._queue.join(), 5)
        assert member._program_store.pending() == []
        assert await mailbox.unread() == []
        assert len(await paper.inbox("a")) == 1
        assert story.id in (await paper.inbox("a"))[0].content
        paper.llm.chat.assert_awaited_once()
    finally:
        await member.shutdown()
        await bus.shutdown()


@pytest.mark.parametrize("mail_event", [True, False], ids=["read-mail", "non-mail-event"])
async def test_runtime_recovers_later_program_effect_after_first_effect_interrupts(
    tmp_path: Path, mail_event: bool
) -> None:
    """One ordinary entry restores its cursor after a prior effect consumes mail."""
    import asyncio

    from tlon.agents.member_runtime import MemberRuntime
    from tlon.base.events.event import Event, EventType
    from tlon.base.paths import TlonPaths

    on = "mail-delivered" if mail_event else "agent-idle"
    mark_read = "(mail-mark-read {:mail_ids [(get (get event :mail) :id)]})" if mail_event else ""
    spec = validate_program_sources(
        "{:version 2 :handlers {:" + on + " work.main/handle}}",
        {
            "work/main.tlon": "(ns work.main (:export handle)) (defn handle [event] "
            '(state-put {:key "first" :value true}) '
            + mark_read
            + ' (state-put {:key "second" :value true}) {:result nil})'
        },
    )
    bus = EventBus()
    office = make_office(bus, tmp_path, ["newspaper", "operator"])
    mailbox = Mailbox(tmp_path / "shared/mail.sqlite3", "newspaper")

    def runtime() -> MemberRuntime:
        return MemberRuntime(
            member_id="newspaper",
            name="程序恢复",
            event_bus=bus,
            spec=spec,
            builtins=make_member_builtins(),
            mailbox=mailbox,
            post_office=office,
            tlon_paths=TlonPaths(home=tmp_path),
        )

    member = runtime()
    await bus.initialize()
    await member.initialize()
    crashed = asyncio.Event()
    loop = asyncio.get_running_loop()
    begin = member._program_store.begin_invocation

    def interrupt_second(invocation: Invocation) -> Any:
        if (
            invocation.name == "state-put"
            and json.loads(invocation.arguments_json)["key"] == "second"
        ):
            loop.call_soon_threadsafe(crashed.set)
            raise asyncio.CancelledError()
        return begin(invocation)

    member._program_store.begin_invocation = interrupt_second
    try:
        if mail_event:
            await office.send(from_address=ADMIN, to=[ADDRESS], content="真实事件")
        else:
            await bus.publish(
                Event(type=EventType.AGENT_IDLE, payload={"agent_id": "operator"}, source="test")
            )
        await asyncio.wait_for(crashed.wait(), 5)
        assert member._program_store.state_get("first") is True
        assert member._program_store.state_get("second") is None
        assert len(member._program_store.pending()) == 1
        assert await mailbox.unread() == []
        await member.shutdown()
        member = runtime()
        await asyncio.wait_for(member.initialize(), 5)
        async with asyncio.timeout(5):
            while (
                member._program_store.state_get("second") is not True
                or member._program_store.pending()
            ):
                await asyncio.sleep(0.01)
        assert await mailbox.unread() == []
        assert member._program_store.state_get("first") is True
    finally:
        await member.shutdown()
        await bus.shutdown()


async def test_terminal_expression_error_preserves_effects_and_does_not_block_new_mail(
    tmp_path: Path,
) -> None:
    """A failed program remains visibly failed, while later mail can make progress."""
    import asyncio

    from tlon.agents.member_runtime import MemberRuntime
    from tlon.base.paths import TlonPaths

    spec = validate_program_sources(
        "{:version 2 :handlers {:mail-delivered work.main/handle}}",
        {
            "work/main.tlon": """(ns work.main (:export handle))
        (defn handle [event]
          (let [mail (get event :mail)]
            (state-put {:key "seen" :value (get mail :id)})
            (if (= (get mail :subject) "broken") (/ 1 0)
              (state-put {:key "healthy" :value true}))
            (mail-mark-read {:mail_ids [(get mail :id)]})
            {:result nil}))"""
        },
    )
    bus = EventBus()
    office = make_office(bus, tmp_path, ["newspaper", "operator"])
    mailbox = Mailbox(tmp_path / "shared/mail.sqlite3", "newspaper")
    member = MemberRuntime(
        member_id="newspaper",
        name="失败隔离",
        event_bus=bus,
        spec=spec,
        builtins=make_member_builtins(),
        mailbox=mailbox,
        post_office=office,
        tlon_paths=TlonPaths(home=tmp_path),
    )
    await bus.initialize()
    await member.initialize()

    async def settled(mail_id: str) -> None:
        for _ in range(300):
            if (
                member._program_store.state_get("seen") == mail_id
                and not member._program_store.pending()
            ):
                return
            await asyncio.sleep(0.01)
        raise AssertionError("program never reached completed or failed state")

    try:
        broken = await office.send(
            from_address=ADMIN, to=[ADDRESS], subject="broken", content="表达式将失败"
        )
        await asyncio.wait_for(settled(broken.mail.id), 5)
        run_id = f"{broken.mail.id}:handler:mail-delivered"
        failed = member._program_store.get_run(run_id)
        assert failed is not None
        assert not failed.completed
        assert failed.error
        assert member._program_store.pending() == []
        operation = member._operations._executor.registry.get("state-put")
        invocation = Invocation(
            call_id=f"program:{run_id}:0:state-put",
            root_id=run_id,
            path=("0",),
            name="state-put",
            arguments_json=json.dumps(
                {"key": "seen", "value": broken.mail.id},
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ),
            definition_id=OperationExecutor.definition_id(operation),
            build_id=member._program_store.build_id,
        )
        recorded = member._program_store.lookup_invocation(invocation)
        assert recorded is not None and recorded.success and recorded.value == broken.mail.id
        assert [mail.id for mail in await mailbox.unread()] == [broken.mail.id]

        good = await office.send(
            from_address=ADMIN, to=[ADDRESS], subject="healthy", content="后续信仍须运行"
        )
        await asyncio.wait_for(settled(good.mail.id), 5)
        assert member._program_store.state_get("healthy") is True
        assert member._program_store.pending() == []
        failed_after = member._program_store.get_run(run_id)
        assert failed_after is not None
        assert failed_after.error == failed.error
        assert not failed_after.completed
        assert member._program_store.lookup_invocation(invocation) == recorded

    finally:
        await member.shutdown()
        await bus.shutdown()
