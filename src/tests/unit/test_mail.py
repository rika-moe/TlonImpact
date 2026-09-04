"""Tests for the mail data model and per-member mailbox storage."""

import json
from pathlib import Path

import pytest

import tlon.organization.mail.mailbox as mailbox_module
from tlon.base.paths import TlonPaths
from tlon.organization.mail.mail import (
    ALL_ADDRESS,
    POSTMASTER_ADDRESS,
    Mail,
    address_for_member,
    is_mail_address,
    member_id_for_address,
)
from tlon.organization.mail.mailbox import Mailbox

GU = "gu-yanxi@tlon.internal"
ALICE = "alice@tlon.internal"
ADA = "ada@tlon.internal"


def _mail(content: str = "hello", **kwargs) -> Mail:
    kwargs.setdefault("from_address", GU)
    kwargs.setdefault("to", [ALICE])
    return Mail.create(content=content, **kwargs)


class TestAddresses:
    def test_address_for_agent_member(self):
        assert address_for_member("gu-yanxi") == GU

    def test_address_for_human_member(self):
        assert address_for_member("human:alice") == ALICE

    def test_member_id_round_trip(self):
        assert member_id_for_address(address_for_member("gu-yanxi")) == "gu-yanxi"
        assert member_id_for_address(address_for_member("human:alice")) == "alice"

    def test_member_id_for_special_addresses(self):
        assert member_id_for_address(ALL_ADDRESS) == "all"
        assert member_id_for_address(POSTMASTER_ADDRESS) == "postmaster"

    def test_member_id_for_foreign_domain(self):
        assert member_id_for_address("someone@example.com") is None

    def test_member_id_for_malformed(self):
        assert member_id_for_address("not-an-address") is None
        assert member_id_for_address("@tlon.internal") is None
        assert member_id_for_address("") is None

    def test_is_mail_address(self):
        assert is_mail_address(GU)
        assert not is_mail_address("gu-yanxi")
        assert not is_mail_address("a@b@c")
        assert not is_mail_address("")


class TestMailCreate:
    def test_new_thread_defaults_thread_id_to_own_id(self):
        mail = _mail()
        assert mail.thread_id == mail.id
        assert mail.in_reply_to is None

    def test_reply_inherits_thread_id(self):
        root = _mail()
        reply = _mail(in_reply_to=root.id, thread_id=root.thread_id)
        assert reply.thread_id == root.id
        assert reply.in_reply_to == root.id
        assert reply.id != root.id

    def test_to_cc_deduplicated_order_preserved(self):
        mail = _mail(to=[ALICE, ADA, ALICE], cc=[ADA, ADA])
        assert mail.to == [ALICE, ADA]
        assert mail.cc == [ADA]

    def test_empty_content_rejected(self):
        with pytest.raises(ValueError, match="content"):
            _mail(content="   ")

    def test_no_recipient_rejected(self):
        with pytest.raises(ValueError, match="recipient"):
            Mail.create(from_address=GU, content="hi", to=[], cc=[])

    def test_cc_only_is_accepted(self):
        mail = Mail.create(from_address=GU, content="hi", cc=[ALICE])
        assert mail.to == []
        assert mail.cc == [ALICE]

    def test_invalid_address_rejected(self):
        with pytest.raises(ValueError, match="address"):
            _mail(to=["not-an-address"])
        with pytest.raises(ValueError, match="address"):
            Mail.create(from_address="bad", content="hi", to=[ALICE])


class TestMailSerialization:
    def test_round_trip(self):
        mail = _mail(
            subject="s", in_reply_to="abc", thread_id="abc", forward_of="def", cc=[ADA]
        )
        assert Mail.from_dict(mail.to_dict()) == mail

    def test_from_dict_tolerates_missing_optional_fields(self):
        mail = _mail()
        data = {
            "id": mail.id,
            "from_address": mail.from_address,
            "to": mail.to,
            "content": mail.content,
            "timestamp": mail.timestamp,
        }
        restored = Mail.from_dict(data)
        assert restored.cc == []
        assert restored.subject is None
        assert restored.in_reply_to is None
        assert restored.forward_of is None
        assert restored.thread_id == mail.id


class TestMailInvolves:
    def test_involves_sender_and_recipients(self):
        mail = _mail(cc=[ADA])
        assert mail.involves(GU)
        assert mail.involves(ALICE)
        assert mail.involves(ADA)
        assert not mail.involves("nobody@tlon.internal")


class TestPaths:
    def test_agent_mail_dir(self, tmp_path: Path):
        paths = TlonPaths(home=tmp_path)
        assert paths.agent_mail_dir("gu-yanxi") == tmp_path / "gu-yanxi" / "mail"
        assert paths.agent_mail_dir("gu-yanxi").is_dir()

    def test_member_mail_dir_for_agent(self, tmp_path: Path):
        paths = TlonPaths(home=tmp_path)
        assert paths.member_mail_dir("gu-yanxi") == tmp_path / "gu-yanxi" / "mail"

    def test_member_mail_dir_for_human(self, tmp_path: Path):
        paths = TlonPaths(home=tmp_path)
        mail_dir = paths.member_mail_dir("human:alice")
        assert mail_dir == tmp_path / "human-alice" / "mail"
        assert mail_dir.is_dir()

    def test_member_mail_dir_existing_never_creates(self, tmp_path: Path):
        paths = TlonPaths(home=tmp_path)
        assert paths.member_mail_dir_existing("human:alice") is None
        assert not (tmp_path / "human-alice").exists()

        paths.member_mail_dir("human:alice")  # creates
        assert (
            paths.member_mail_dir_existing("human:alice")
            == tmp_path / "human-alice" / "mail"
        )

    def test_known_human_ids_lists_only_mailboxed_humans(self, tmp_path: Path):
        paths = TlonPaths(home=tmp_path)
        assert paths.known_human_ids() == []

        paths.member_mail_dir("human:alice")
        paths.member_mail_dir("human:alice")
        (tmp_path / "human-ghost").mkdir()  # dir without mail/ is not known
        assert paths.known_human_ids() == ["human:alice", "human:alice"]


class TestMailbox:
    async def test_append_and_unread(self, tmp_path: Path):
        mailbox = Mailbox(tmp_path)
        first = _mail(content="one")
        second = _mail(content="two")
        await mailbox.append_inbox(first)
        await mailbox.append_inbox(second)

        unread = await mailbox.unread()
        assert [m.id for m in unread] == [first.id, second.id]

    def test_instances_over_same_directory_share_a_lock(self, tmp_path: Path):
        """Delivery cache, REST /inbox and agent runtimes each construct
        their own Mailbox — writers must still be serialized."""
        first = Mailbox(tmp_path / "a")
        second = Mailbox(tmp_path / "a")
        other = Mailbox(tmp_path / "b")
        assert first._lock is second._lock
        assert first._lock is not other._lock

    async def test_sent_mail_is_not_unread(self, tmp_path: Path):
        mailbox = Mailbox(tmp_path)
        own = _mail()
        await mailbox.append_sent(own)
        assert await mailbox.unread() == []

    async def test_mark_read(self, tmp_path: Path):
        mailbox = Mailbox(tmp_path)
        mail = _mail()
        await mailbox.append_inbox(mail)
        await mailbox.mark_read(mail.id)
        assert await mailbox.unread() == []

    async def test_mark_read_is_idempotent(self, tmp_path: Path):
        mailbox = Mailbox(tmp_path)
        mail = _mail()
        await mailbox.append_inbox(mail)
        await mailbox.mark_read(mail.id)
        await mailbox.mark_read(mail.id)

        lines = (tmp_path / "inbox.cursor").read_text(encoding="utf-8").splitlines()
        assert lines == [mail.id]

    async def test_cursor_survives_reopen(self, tmp_path: Path):
        mail = _mail()
        await Mailbox(tmp_path).append_inbox(mail)
        await Mailbox(tmp_path).mark_read(mail.id)

        # A fresh Mailbox instance still remembers the read state.
        reopened = Mailbox(tmp_path)
        assert await reopened.unread() == []

    async def test_recent_merges_inbox_and_sent_chronologically(self, tmp_path: Path):
        mailbox = Mailbox(tmp_path)
        received = _mail(content="received")
        sent = _mail(content="sent")
        await mailbox.append_inbox(received)
        await mailbox.append_sent(sent)

        recent = await mailbox.recent()
        assert [m.id for m in recent] == [received.id, sent.id]

    async def test_recent_respects_limit(self, tmp_path: Path):
        mailbox = Mailbox(tmp_path)
        mails = [_mail(content=f"m{i}") for i in range(5)]
        for mail in mails:
            await mailbox.append_inbox(mail)

        recent = await mailbox.recent(limit=2)
        assert [m.id for m in recent] == [mails[3].id, mails[4].id]

    async def test_malformed_lines_are_skipped(self, tmp_path: Path):
        mailbox = Mailbox(tmp_path)
        good = _mail()
        await mailbox.append_inbox(good)
        with open(tmp_path / "inbox.jsonl", "a", encoding="utf-8") as f:
            f.write("not json\n")
            f.write(json.dumps({"broken": True}) + "\n")

        unread = await mailbox.unread()
        assert [m.id for m in unread] == [good.id]

    async def test_empty_mailbox(self, tmp_path: Path):
        mailbox = Mailbox(tmp_path)
        assert await mailbox.unread() == []
        assert await mailbox.recent() == []


class TestIncrementalScan:
    """unread() parses only the appended tail once an inbox was scanned."""

    async def test_incremental_matches_fresh_full_scan(self, tmp_path: Path):
        mailbox = Mailbox(tmp_path)
        mails = [_mail(content=f"m{i}") for i in range(3)]
        for mail in mails:
            await mailbox.append_inbox(mail)
        await mailbox.unread()  # warm the scan cache

        more = [_mail(content=f"n{i}") for i in range(2)]
        for mail in more:
            await mailbox.append_inbox(mail)
        incremental = [m.id for m in await mailbox.unread()]

        mailbox_module._SCANS.clear()  # force a cold full scan
        full = [m.id for m in await Mailbox(tmp_path).unread()]
        assert incremental == full == [m.id for m in [*mails, *more]]

    async def test_incremental_parses_only_appended_lines(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        mailbox = Mailbox(tmp_path)
        first = _mail(content="first")
        await mailbox.append_inbox(first)
        await mailbox.unread()  # warm the scan cache

        calls = 0
        real_from_dict = Mail.from_dict

        def counting_from_dict(data):
            nonlocal calls
            calls += 1
            return real_from_dict(data)

        monkeypatch.setattr(mailbox_module.Mail, "from_dict", counting_from_dict)

        second = _mail(content="second")
        await mailbox.append_inbox(second)
        unread = await mailbox.unread()

        assert [m.id for m in unread] == [first.id, second.id]
        assert calls == 1  # only the appended line was parsed

    async def test_mark_read_then_incremental_append(self, tmp_path: Path):
        mailbox = Mailbox(tmp_path)
        first = _mail(content="first")
        await mailbox.append_inbox(first)
        await mailbox.mark_read(first.id)
        assert await mailbox.unread() == []  # warm with everything read

        second = _mail(content="second")
        await mailbox.append_inbox(second)
        assert [m.id for m in await mailbox.unread()] == [second.id]

    async def test_malformed_appended_lines_are_skipped(self, tmp_path: Path):
        mailbox = Mailbox(tmp_path)
        first = _mail(content="first")
        await mailbox.append_inbox(first)
        await mailbox.unread()  # warm the scan cache

        with open(tmp_path / "inbox.jsonl", "a", encoding="utf-8") as f:
            f.write("not json\n")
            f.write(json.dumps({"broken": True}) + "\n")
        good = _mail(content="good")
        await mailbox.append_inbox(good)

        unread = await mailbox.unread()
        assert [m.id for m in unread] == [first.id, good.id]

        # A cold full scan agrees with the incremental result.
        mailbox_module._SCANS.clear()
        assert [m.id for m in await Mailbox(tmp_path).unread()] == [first.id, good.id]

    async def test_partial_trailing_line_is_retried(self, tmp_path: Path):
        """A crash-torn append (no trailing newline) is not consumed; it
        is parsed once the rest of the line lands."""
        mailbox = Mailbox(tmp_path)
        first = _mail(content="first")
        await mailbox.append_inbox(first)
        assert [m.id for m in await mailbox.unread()] == [first.id]

        second = _mail(content="second")
        line = json.dumps(second.to_dict(), ensure_ascii=False) + "\n"
        half = len(line) // 2
        with open(tmp_path / "inbox.jsonl", "a", encoding="utf-8") as f:
            f.write(line[:half])  # torn write: no newline yet
        assert [m.id for m in await mailbox.unread()] == [first.id]

        with open(tmp_path / "inbox.jsonl", "a", encoding="utf-8") as f:
            f.write(line[half:])  # the line completes
        assert [m.id for m in await mailbox.unread()] == [first.id, second.id]

    async def test_truncated_inbox_falls_back_to_full_scan(self, tmp_path: Path):
        mailbox = Mailbox(tmp_path)
        kept = _mail(content="kept")
        await mailbox.append_inbox(kept)
        await mailbox.append_inbox(_mail(content="dropped"))
        assert len(await mailbox.unread()) == 2  # warm the scan cache

        (tmp_path / "inbox.jsonl").write_text(
            json.dumps(kept.to_dict(), ensure_ascii=False) + "\n", encoding="utf-8"
        )
        assert [m.id for m in await mailbox.unread()] == [kept.id]

    async def test_replaced_inbox_falls_back_to_full_scan(self, tmp_path: Path):
        """An inode change (file replaced) invalidates the scan cache."""
        mailbox = Mailbox(tmp_path)
        old = _mail(content="old")
        await mailbox.append_inbox(old)
        assert [m.id for m in await mailbox.unread()] == [old.id]  # warm

        new = _mail(content="new")
        replacement = tmp_path / "inbox.jsonl.new"
        replacement.write_text(
            json.dumps(new.to_dict(), ensure_ascii=False) + "\n", encoding="utf-8"
        )
        replacement.replace(tmp_path / "inbox.jsonl")  # new inode

        assert [m.id for m in await mailbox.unread()] == [new.id]

    async def test_deleted_inbox_clears_scan(self, tmp_path: Path):
        mailbox = Mailbox(tmp_path)
        await mailbox.append_inbox(_mail())
        assert len(await mailbox.unread()) == 1

        (tmp_path / "inbox.jsonl").unlink()
        assert await mailbox.unread() == []


class TestLockLRU:
    """The shared per-directory lock table is a bounded LRU."""

    @pytest.fixture
    def small_lock_cap(self, monkeypatch: pytest.MonkeyPatch):
        mailbox_module._LOCKS.clear()
        monkeypatch.setattr(mailbox_module, "_LOCKS_CAP", 2)
        yield
        mailbox_module._LOCKS.clear()

    def test_evicts_oldest_over_cap(self, tmp_path: Path, small_lock_cap):
        a = Mailbox(tmp_path / "a")
        Mailbox(tmp_path / "b")
        Mailbox(tmp_path / "c")  # overflow: "a" is oldest, evicted

        assert len(mailbox_module._LOCKS) == 2
        assert (tmp_path / "a") not in mailbox_module._LOCKS

        # A rebuilt mailbox gets a fresh lock (evicts "b" in turn).
        rebuilt = Mailbox(tmp_path / "a")
        assert rebuilt._lock is not a._lock
        assert (tmp_path / "b") not in mailbox_module._LOCKS

    async def test_locked_lock_is_not_evicted(
        self, tmp_path: Path, small_lock_cap
    ):
        """A lock that is held or awaited must stay: evicting it would
        break mutual exclusion for its waiters."""
        a = Mailbox(tmp_path / "a")
        Mailbox(tmp_path / "b")
        await a._lock.acquire()
        try:
            Mailbox(tmp_path / "c")  # overflow: "a" locked, so "b" evicted
        finally:
            a._lock.release()

        assert (tmp_path / "a") in mailbox_module._LOCKS
        assert (tmp_path / "b") not in mailbox_module._LOCKS

    def test_hit_refreshes_recency(self, tmp_path: Path, small_lock_cap):
        """LRU refresh keeps mutual exclusion: a revisited directory
        keeps the same lock and is not the eviction candidate."""
        a = Mailbox(tmp_path / "a")
        Mailbox(tmp_path / "b")
        revisited = Mailbox(tmp_path / "a")  # hit: "a" is most recent now
        Mailbox(tmp_path / "c")  # overflow: "b" is oldest, evicted

        assert revisited._lock is a._lock
        assert (tmp_path / "b") not in mailbox_module._LOCKS
        assert (tmp_path / "a") in mailbox_module._LOCKS
