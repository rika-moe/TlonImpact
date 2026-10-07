"""Tests for the reputation derivation (agents/reputation.py,
docs/design/mail.md).

Reputation is derived, never stored: every number is recomputed from
the society's mail files. Covered: endorsements given/received across
members, (endorser, mail_id) dedup, the top-endorsed-mails ranking,
the embedded mail-behavior summary, and the read-only discipline (a
query never creates a mailbox).
"""

from pathlib import Path

import pytest

from tlon.agents.reputation import TOP_ENDORSED_CAP, collect_reputation, reputation_provider
from tlon.base.paths import TlonPaths
from tlon.organization.mail.mail import Mail
from tlon.organization.mail.mailbox import Mailbox

TARGET = "a-001"
TARGET_ADDR = "a-001@tlon.internal"
BOB = "b-002"
BOB_ADDR = "b-002@tlon.internal"
CAROL = "c-003"
CAROL_ADDR = "c-003@tlon.internal"
MEMBERS = [TARGET, BOB, CAROL]


def _mail(
    mail_id: str,
    from_address: str,
    to: list[str],
    *,
    subject: str | None = None,
    endorsing: str | None = None,
) -> Mail:
    return Mail(
        id=mail_id,
        from_address=from_address,
        to=to,
        cc=[],
        subject=subject,
        content="正文",
        in_reply_to=None,
        thread_id=mail_id,
        forward_of=None,
        timestamp="2026-09-06T10:00:00",
        endorsing=endorsing,
    )


@pytest.fixture
def tlon_paths(tmp_path: Path) -> TlonPaths:
    return TlonPaths(home=tmp_path)


async def _seed_society(tlon_paths: TlonPaths) -> None:
    """a-001's mail m1 gets endorsed by b-002 (twice) and c-003;
    a-001 endorses b-002's m2 once."""
    # m1: authored by the target; sits in each endorser's inbox.
    m1 = _mail("m1", TARGET_ADDR, [BOB_ADDR, CAROL_ADDR], subject="m1主题")
    await Mailbox(tlon_paths.member_mail_dir(TARGET)).append_sent(m1)
    for member in (BOB, CAROL):
        await Mailbox(tlon_paths.member_mail_dir(member)).append_inbox(m1)

    # b-002 endorses m1 twice: the (endorser, mail_id) pair counts once.
    bob = Mailbox(tlon_paths.member_mail_dir(BOB))
    await bob.append_sent(_mail("e1", BOB_ADDR, [TARGET_ADDR], endorsing="m1"))
    await bob.append_sent(_mail("e2", BOB_ADDR, [TARGET_ADDR], endorsing="m1"))
    # An endorsement of somebody else's mail does not count for a-001.
    m9 = _mail("m9", CAROL_ADDR, [BOB_ADDR])
    await bob.append_inbox(m9)
    await bob.append_sent(_mail("e9", BOB_ADDR, [CAROL_ADDR], endorsing="m9"))

    # c-003 endorses m1 once.
    await Mailbox(tlon_paths.member_mail_dir(CAROL)).append_sent(
        _mail("e3", CAROL_ADDR, [TARGET_ADDR], endorsing="m1")
    )

    # a-001 endorses b-002's m2 (given=1; m2 sits in a-001's inbox).
    m2 = _mail("m2", BOB_ADDR, [TARGET_ADDR])
    target = Mailbox(tlon_paths.member_mail_dir(TARGET))
    await target.append_inbox(m2)
    await target.append_sent(_mail("e4", TARGET_ADDR, [BOB_ADDR], endorsing="m2"))


class TestCollectReputation:
    async def test_given_and_received_across_members(self, tlon_paths: TlonPaths) -> None:
        await _seed_society(tlon_paths)

        report = await collect_reputation(TARGET, tlon_paths=tlon_paths, member_ids=MEMBERS)

        assert report["agent_id"] == TARGET
        assert report["address"] == TARGET_ADDR
        assert report["endorsements_given"] == 1
        # b-002 twice + c-003 once, deduplicated per (endorser, mail)
        assert report["endorsements_received"] == 2

    async def test_top_endorsed_mails_ranked_by_distinct_endorsers(
        self, tlon_paths: TlonPaths
    ) -> None:
        await _seed_society(tlon_paths)

        report = await collect_reputation(TARGET, tlon_paths=tlon_paths, member_ids=MEMBERS)

        assert report["top_endorsed_mails"] == [
            {"mail_id": "m1", "subject": "m1主题", "endorsers": 2}
        ]

        bob_report = await collect_reputation(BOB, tlon_paths=tlon_paths, member_ids=MEMBERS)
        assert bob_report["endorsements_received"] == 1
        assert bob_report["top_endorsed_mails"] == [
            {"mail_id": "m2", "subject": None, "endorsers": 1}
        ]

    async def test_mail_behavior_summary_embedded(self, tlon_paths: TlonPaths) -> None:
        await _seed_society(tlon_paths)

        report = await collect_reputation(TARGET, tlon_paths=tlon_paths, member_ids=MEMBERS)

        behavior = report["mail_behavior"]
        assert behavior["total_sent"] == 2  # m1 + the endorsement mail
        assert behavior["total_received"] == 1  # m2
        assert behavior["reply_rate"] == 0.0  # m1 never answered
        assert behavior["avg_first_reply_seconds"] is None

    async def test_top_endorsed_capped(self, tlon_paths: TlonPaths) -> None:
        target = Mailbox(tlon_paths.member_mail_dir(TARGET))
        bob = Mailbox(tlon_paths.member_mail_dir(BOB))
        for i in range(TOP_ENDORSED_CAP + 3):
            mail = _mail(f"m{i:02d}", TARGET_ADDR, [BOB_ADDR], subject=f"s{i}")
            await target.append_sent(mail)
            await bob.append_inbox(mail)
            await bob.append_sent(_mail(f"e{i:02d}", BOB_ADDR, [TARGET_ADDR], endorsing=mail.id))

        report = await collect_reputation(TARGET, tlon_paths=tlon_paths, member_ids=MEMBERS)

        assert len(report["top_endorsed_mails"]) == TOP_ENDORSED_CAP
        assert report["endorsements_received"] == TOP_ENDORSED_CAP + 3

    async def test_memberless_society_is_all_zero(self, tlon_paths: TlonPaths) -> None:
        report = await collect_reputation(TARGET, tlon_paths=tlon_paths, member_ids=MEMBERS)

        assert report["endorsements_given"] == 0
        assert report["endorsements_received"] == 0
        assert report["top_endorsed_mails"] == []

    async def test_read_only_scan_never_creates_mailboxes(
        self, tlon_paths: TlonPaths, tmp_path: Path
    ) -> None:
        await collect_reputation(TARGET, tlon_paths=tlon_paths, member_ids=[*MEMBERS, "ghost"])

        # The scan opens only mailboxes that already exist on disk.
        assert not (tmp_path / "ghost").exists()
        assert not (tmp_path / BOB).exists()


class TestReputationProvider:
    async def test_provider_binds_paths_and_member_enumeration(self, tlon_paths: TlonPaths) -> None:
        await _seed_society(tlon_paths)
        provider = reputation_provider(tlon_paths, member_ids=lambda: MEMBERS)

        report = await provider(TARGET)

        assert report["endorsements_received"] == 2
