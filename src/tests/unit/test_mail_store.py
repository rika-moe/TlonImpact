"""SQLite post-office persistence and atomicity contracts."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from tlon.base.paths import TlonPaths
from tlon.organization.mail.mail import Mail
from tlon.organization.mail.mailbox import Mailbox
from tlon.organization.mail.store import IdempotencyConflict, MailStore, RetiredRecipients


def mail() -> Mail:
    return Mail.create(
        from_address="alice@tlon.internal", to=["bob@tlon.internal"], content="hello"
    )


def test_missing_reads_do_not_create_database(tmp_path: Path) -> None:
    store = MailStore(tmp_path / "shared" / "mail.sqlite3")
    assert store.mails("bob") == []
    assert store.known_human_ids() == []
    assert not store.member_exists("bob")
    assert not store.database.exists()


def test_accept_restart_replay_and_conflict(tmp_path: Path) -> None:
    database = tmp_path / "mail.sqlite3"
    original = mail()
    post, created = MailStore(database).accept(
        original, "alice", ["bob", "bob"], "r", "f", ["github"]
    )
    assert created
    assert post.local_recipients == ["bob"]
    assert post.gateways[0]["status"] == "unknown"
    store = MailStore(database)
    replay, created = store.accept(mail(), "alice", ["bob"], "r", "f", ["github"])
    assert not created
    assert replay.mail.id == original.id
    assert store.mails("bob") == [original]
    with pytest.raises(IdempotencyConflict):
        store.accept(mail(), "alice", ["bob"], "r", "different", [])
    store.update_gateway(original.id, "github", "failed", "offline")
    assert store.get_request(original.from_address, "r", "f").gateways[0]["error"] == "offline"


def test_atomic_rollback_on_database_failure(tmp_path: Path) -> None:
    store = MailStore(tmp_path / "mail.sqlite3")
    store.register_member("bob")
    with sqlite3.connect(store.database) as connection:
        connection.execute(
            "CREATE TRIGGER fail_recipient BEFORE INSERT ON recipients WHEN NEW.member_id='carol' BEGIN SELECT RAISE(ABORT,'disk failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        store.accept(mail(), "alice", ["bob", "carol"], "r", "f", [])
    assert store.mails("bob") == []
    assert store.mails("alice", "sent") == []
    assert store.get_request("alice@tlon.internal", "r", "f") is None


def test_retired_rejects_entire_post_but_history_remains(tmp_path: Path) -> None:
    store = MailStore(tmp_path / "mail.sqlite3")
    original = mail()
    store.accept(original, "alice", ["bob"], "first", "f", [])
    store.retire("bob")
    store.register_member("bob")  # reloading registry cannot undo retirement
    with pytest.raises(RetiredRecipients) as error:
        store.accept(mail(), "alice", ["carol", "bob"], "second", "f", [])
    assert error.value.member_ids == ["bob"]
    assert store.mails("bob") == [original]
    assert store.mails("carol") == []
    assert store.is_retired("bob")


def test_concurrent_replays_commit_once(tmp_path: Path) -> None:
    store = MailStore(tmp_path / "mail.sqlite3")
    store.register_member("bob")
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(
            executor.map(lambda _: store.accept(mail(), "alice", ["bob"], "r", "f", []), range(8))
        )
    assert sum(created for _, created in results) == 1
    assert len(store.mails("bob")) == 1


async def test_read_is_explicit_member_scoped_and_self_mail_unique(tmp_path: Path) -> None:
    database = tmp_path / "mail.sqlite3"
    store = MailStore(database)
    original = mail()
    store.accept(original, "alice", ["alice", "bob"], "r", "f", [])
    alice = Mailbox(database, "alice")
    bob = Mailbox(database, "bob")
    assert await alice.recent() == [original]
    assert await alice.inbox() == [original]
    assert await alice.find(original.id) == original
    assert await alice.thread_incoming(original.thread_id) == [original]
    assert await alice.unread() == [original]
    await alice.mark_read(original.id)
    await alice.mark_read(original.id)
    assert await Mailbox(database, "alice").unread() == []
    assert await bob.unread() == [original]


async def test_legacy_files_are_ignored(tmp_path: Path) -> None:
    paths = TlonPaths(home=tmp_path)
    legacy = paths.member_mail_dir("human:old")
    legacy.mkdir(parents=True)
    (legacy / "inbox.jsonl").write_text("legacy data")
    assert paths.known_human_ids() == []
    assert await Mailbox(paths.mail_database_file(), "human:old").inbox() == []
    store = MailStore(paths.mail_database_file())
    store.register_member("human:new")
    assert paths.known_human_ids() == ["human:new"]


def test_retirement_and_post_are_serialized(tmp_path: Path) -> None:
    """Both racing outcomes are legal; retirement cannot leave a partial post."""
    store = MailStore(tmp_path / "mail.sqlite3")
    store.register_member("bob")

    def post() -> bool:
        try:
            store.accept(mail(), "alice", ["bob", "carol"], "r", "f", [])
        except RetiredRecipients:
            return False
        return True

    with ThreadPoolExecutor(max_workers=2) as executor:
        posted = executor.submit(post)
        retired = executor.submit(store.retire, "bob")
        accepted = posted.result()
        retired.result()
    assert store.is_retired("bob")
    assert len(store.mails("bob")) == len(store.mails("carol")) == int(accepted)
    with pytest.raises(RetiredRecipients):
        store.accept(mail(), "alice", ["bob"], "next", "f", [])


def test_targeted_queries_only_decode_selected_messages(tmp_path: Path) -> None:
    store = MailStore(tmp_path / "mail.sqlite3")
    mails = []
    for index in range(8):
        item = mail()
        item.timestamp = f"2026-10-16T00:00:{index:02d}+00:00"
        item.thread_id = "target-thread" if index in (2, 4) else item.id
        store.accept(item, "alice", ["bob"], str(index), "f", [])
        mails.append(item)
    from unittest.mock import patch

    with patch.object(Mail, "from_dict", wraps=Mail.from_dict) as decode:
        assert store.find("bob", mails[3].id) == mails[3]
        assert decode.call_count == 1
        decode.reset_mock()
        assert store.recent("bob", 2) == mails[-2:]
        assert decode.call_count == 2
        decode.reset_mock()
        assert store.earliest("bob", 2) == mails[:2]
        assert decode.call_count == 2
        decode.reset_mock()
        assert store.thread_incoming("bob", "target-thread") == [mails[2], mails[4]]
        assert decode.call_count == 2
    assert store.find("outsider", mails[3].id) is None
    assert store.thread_incoming("alice", "target-thread") == []
    assert store.recent("bob", 0) == []
    assert store.earliest("bob", -1) == []


def test_reads_are_readonly_without_schema_changes_and_connections_close(
    tmp_path: Path, monkeypatch
) -> None:
    store = MailStore(TlonPaths(home=tmp_path).mail_database_file())
    original = mail()
    store.accept(original, "alice", ["human:bob"], "r", "f", [])
    connect = sqlite3.connect
    statements: list[str] = []
    connections: list[sqlite3.Connection] = []

    def readonly_connect(database, *args, **kwargs):
        assert str(database).endswith("?mode=ro")
        assert kwargs["uri"] is True
        connection = connect(database, *args, **kwargs)
        connection.set_trace_callback(statements.append)
        connections.append(connection)
        return connection

    monkeypatch.setattr(sqlite3, "connect", readonly_connect)
    assert store.member_exists("human:bob")
    assert not store.is_retired("human:bob")
    assert store.known_human_ids() == ["human:bob"]
    assert store.get_request(original.from_address, "r", "f") is not None
    assert store.mails("human:bob") == [original]
    assert TlonPaths(home=tmp_path).known_human_ids() == ["human:bob"]
    for statement in statements:
        assert statement.lstrip().upper().startswith("SELECT") or statement == "PRAGMA user_version"
    for connection in connections:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connection.execute("SELECT 1")


def test_receipt_describes_actual_recipients_and_gateway_results(tmp_path: Path) -> None:
    store = MailStore(tmp_path / "mail.sqlite3")
    original = mail()
    post, _ = store.accept(original, "alice", ["bob"], "r", "f", ["github"])
    assert "bob" in post.to_dict()["message"]
    assert "结果未知" in post.to_dict()["message"]
    failed = store.update_gateway(original.id, "github", "failed", "network offline")
    assert "投递失败：network offline" in failed.to_dict()["message"]
    assert "投递成功" not in failed.to_dict()["message"]
    delivered = store.update_gateway(original.id, "github", "delivered")
    assert "信箱投递成功，不代表外部平台已收到" in delivered.to_dict()["message"]
    gateway_only, _ = store.accept(mail(), None, [], "external", "f", ["github"])
    assert "已存入" not in gateway_only.to_dict()["message"]


@pytest.mark.parametrize("locals", [[], ["bob"]])
async def test_gateway_membership_does_not_change_local_receipt_or_recovery(
    tmp_path: Path, locals: list[str]
) -> None:
    store = MailStore(tmp_path / "mail.sqlite3")
    original = mail()
    store.accept(original, "alice", locals, "r", "f", ["github@external.test"])
    mailbox = Mailbox(store.database, "github-gateway")
    await mailbox.append_inbox(original, gateway=True)
    updated = store.update_gateway(original.id, "github@external.test", "delivered")
    replay = store.get_request(original.from_address, "r", "f")
    assert replay is not None
    assert updated.local_recipients == replay.local_recipients == locals
    assert store.unread_member_ids() == locals
    assert await mailbox.unread() == [original]
    assert await mailbox.find(original.id) == original
    if not locals:
        assert "已存入" not in replay.to_dict()["message"]


def test_reads_during_atomic_schema_initialization(tmp_path: Path, monkeypatch) -> None:
    """A visible database file does not expose its uncommitted schema."""
    import threading

    paths = TlonPaths(home=tmp_path)
    store = MailStore(paths.mail_database_file())
    schema_ready = threading.Event()
    release = threading.Event()
    connect = sqlite3.connect

    def pause_before_commit(statement: str) -> None:
        if statement.strip().upper() == "PRAGMA USER_VERSION=1;":
            schema_ready.set()
            assert release.wait(5)

    def traced_connect(*args, **kwargs):
        connection = connect(*args, **kwargs)
        connection.set_trace_callback(pause_before_commit)
        return connection

    monkeypatch.setattr(sqlite3, "connect", traced_connect)
    with ThreadPoolExecutor(max_workers=1) as executor:
        initializing = executor.submit(store.initialize)
        try:
            assert schema_ready.wait(5)
            assert store.database.exists()
            assert store.get_request("alice@tlon.internal", "r", "f") is None
            assert store.known_human_ids() == []
            assert paths.known_human_ids() == []
            assert store.mails("bob") == []
        finally:
            release.set()
        initializing.result()
    store.register_member("human:bob")
    assert paths.known_human_ids() == ["human:bob"]


def test_concurrent_first_writes_from_multiple_store_instances(tmp_path: Path) -> None:
    database = tmp_path / "mail.sqlite3"

    def post(_: int) -> bool:
        return MailStore(database).accept(mail(), "alice", ["bob"], "r", "f", [])[1]

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(post, range(8)))
    assert sum(results) == 1
    assert len(MailStore(database).mails("bob")) == 1
