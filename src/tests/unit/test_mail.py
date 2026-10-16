"""Tests for the mail data model and per-member mailbox storage."""

import pytest

from tlon.organization.mail.mail import (
    POSTMASTER_ADDRESS,
    Mail,
    address_for_member,
    is_external_address,
    is_mail_address,
    member_id_for_address,
)

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

    def test_is_external_address(self):
        """The trust boundary derives from the address domain
        (docs/design/mail.md)."""
        # Internal: members, humans, system addresses
        assert not is_external_address(GU)
        assert not is_external_address(ALICE)
        assert not is_external_address(POSTMASTER_ADDRESS)
        # External: any other domain
        assert is_external_address("notifications@github.com")
        assert is_external_address("alerts@monitoring.example.org")
        # Malformed addresses count as external (untrusted by default)
        assert is_external_address("not-an-address")
        assert is_external_address("")


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
        mail = _mail(subject="s", in_reply_to="abc", thread_id="abc", forward_of="def", cc=[ADA])
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


class TestMailEndorsing:
    """The endorsement signal field (docs/design/mail.md)."""

    def test_create_accepts_endorsing(self):
        mail = _mail(endorsing="m-helpful")
        assert mail.endorsing == "m-helpful"

    def test_endorsing_defaults_to_none(self):
        assert _mail().endorsing is None

    def test_endorsing_round_trip(self):
        mail = _mail(endorsing="m-helpful", in_reply_to="m-helpful")
        assert Mail.from_dict(mail.to_dict()) == mail

    def test_from_dict_tolerates_missing_endorsing(self):
        """Old jsonl rows have no endorsing field: they read as None."""
        mail = _mail()
        data = mail.to_dict()
        del data["endorsing"]
        assert Mail.from_dict(data).endorsing is None


class TestMailInvolves:
    def test_involves_sender_and_recipients(self):
        mail = _mail(cc=[ADA])
        assert mail.involves(GU)
        assert mail.involves(ALICE)
        assert mail.involves(ADA)
        assert not mail.involves("nobody@tlon.internal")
