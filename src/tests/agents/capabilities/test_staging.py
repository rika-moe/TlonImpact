"""Staging area + trusted library flow for capability artifacts
(staging.py / catalog.py): validation before disk, listing, review
records, approval (whole-directory move + trusted flip), rejection with
the path-injection guard.
"""

import pytest

from tlon.agents.capabilities.catalog import (
    get_capability_detail,
    get_capability_info,
    list_trusted_capabilities,
)
from tlon.agents.capabilities.spec import CapabilityError
from tlon.agents.capabilities.staging import (
    append_staging_review,
    approve_capability,
    list_staging_capabilities,
    reject_capability,
    write_staging_capability,
)
from tlon.base.paths import TlonPaths

VALID_CAPABILITY = """\
{:version 1
 :name "thread-digest"
 :description "把邮件线程蒸馏成三段式摘要文本"
 :args [{:name "mails" :type :list :doc "按时间升序的邮件列表"}]
 :impl (fn [mails] (str "digest:" (count mails)))
 :pure true}
"""

VALID_IMPURE_CAPABILITY = '{:version 1\n :name "notify-adam"\n :description "给 Adam 发一封通知邮件"\n :args [{:name "content" :type :string :doc "通知正文"}]\n :impl (fn [content] (mail-send {:to ["sink@tlon.internal"] :content content}))\n :pure false}\n'

INVALID_CAPABILITY = """\
{:version 1
 :name "wild"
 :description "组合未注册词汇"
 :args []
 :impl (fn [] (jump "x"))
 :pure false}
"""


def _write_trusted(p: TlonPaths, name: str, text: str = VALID_CAPABILITY) -> None:
    """Handwrite a capability directly into the trusted library."""
    cap_dir = p.capabilities_dir() / name
    cap_dir.mkdir(parents=True)
    (cap_dir / "capability.tlon").write_text(text, encoding="utf-8")


class TestWriteStaging:
    def test_writes_untrusted_with_author(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        info = write_staging_capability(
            VALID_CAPABILITY, author_id="adam", proposal_note="新增摘要词", tlon_paths=p
        )

        assert info.name == "thread-digest"
        assert info.trusted is False
        assert info.author == "adam"
        assert info.proposal_note == "新增摘要词"
        assert info.description == "把邮件线程蒸馏成三段式摘要文本"
        assert info.pure is True
        cap_dir = p.capabilities_staging_dir() / "thread-digest"
        assert (cap_dir / "capability.tlon").read_text(encoding="utf-8") == VALID_CAPABILITY
        metadata = (cap_dir / "meta.tlon").read_text(encoding="utf-8")
        assert ":trusted false" in metadata
        assert ':author "adam"' in metadata
        assert ":proposal-note" in metadata

    def test_invalid_capability_nothing_on_disk(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        with pytest.raises(CapabilityError, match="unknown function or special form: jump"):
            write_staging_capability(INVALID_CAPABILITY, author_id="adam", tlon_paths=p)
        assert not (p.capabilities_staging_dir() / "wild").exists()

    def test_pure_declaration_with_action_impl_nothing_on_disk(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        lying = VALID_IMPURE_CAPABILITY.replace(":pure false", ":pure true")
        with pytest.raises(CapabilityError, match="failed pure validation"):
            write_staging_capability(lying, author_id="adam", tlon_paths=p)
        assert not (p.capabilities_staging_dir() / "notify-adam").exists()

    def test_impure_capability_with_action_word_passes(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        info = write_staging_capability(VALID_IMPURE_CAPABILITY, tlon_paths=p)
        assert info.pure is False

    def test_staging_name_conflict_rejected(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        write_staging_capability(VALID_CAPABILITY, tlon_paths=p)
        with pytest.raises(CapabilityError, match="already exists in the staging area"):
            write_staging_capability(VALID_CAPABILITY, tlon_paths=p)

    def test_trusted_name_conflict_rejected(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        _write_trusted(p, "thread-digest")
        with pytest.raises(CapabilityError, match="collides with a trusted capability"):
            write_staging_capability(VALID_CAPABILITY, tlon_paths=p)
        assert not (p.capabilities_staging_dir() / "thread-digest").exists()

    def test_builtin_name_collision_nothing_on_disk(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        shadowing = VALID_CAPABILITY.replace('"thread-digest"', '"count"')
        with pytest.raises(CapabilityError, match="collides with a builtin word"):
            write_staging_capability(shadowing, tlon_paths=p)
        assert not (p.capabilities_staging_dir() / "count").exists()


class TestListing:
    def test_list_staging_and_trusted(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        write_staging_capability(VALID_CAPABILITY, author_id="adam", tlon_paths=p)
        write_staging_capability(VALID_IMPURE_CAPABILITY, tlon_paths=p)
        _write_trusted(p, "hand-written")

        staging = list_staging_capabilities(p)
        assert [c.name for c in staging] == ["notify-adam", "thread-digest"]
        assert all(not c.trusted for c in staging)

        trusted = list_trusted_capabilities(p)
        assert [c.name for c in trusted] == ["hand-written"]
        assert trusted[0].trusted is True
        # A hand-written trusted capability carries no metadata file: the
        # directory name is the fallback identity (handwritten implies trusted).
        assert trusted[0].author is None

    def test_list_empty_areas(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        assert list_staging_capabilities(p) == []
        assert list_trusted_capabilities(p) == []

    def test_get_capability_info(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        _write_trusted(p, "thread-digest")
        info = get_capability_info("thread-digest", p)
        assert info is not None
        assert info.name == "thread-digest"
        assert get_capability_info("nope", p) is None


class TestReview:
    def test_append_review_creates_and_accumulates(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        write_staging_capability(VALID_CAPABILITY, tlon_paths=p)

        append_staging_review("thread-digest", "## 评审 1\nverdict: pass", tlon_paths=p)
        append_staging_review("thread-digest", "## 评审 2\nverdict: concern", tlon_paths=p)

        text = (p.capabilities_staging_dir() / "thread-digest" / "REVIEW.md").read_text(
            encoding="utf-8"
        )
        assert text.startswith("# 评审记录")
        assert "评审 1" in text and "评审 2" in text

    def test_review_missing_staged_rejected(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        with pytest.raises(CapabilityError, match="no staged capability named 'nope'"):
            append_staging_review("nope", "entry", tlon_paths=p)


class TestApprove:
    def test_approve_moves_and_flips_trusted(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        write_staging_capability(VALID_CAPABILITY, author_id="adam", tlon_paths=p)
        append_staging_review("thread-digest", "## 评审\nverdict: pass", tlon_paths=p)

        info = approve_capability("thread-digest", p)

        assert info.trusted is True
        assert info.author == "adam"
        assert not (p.capabilities_staging_dir() / "thread-digest").exists()
        cap_dir = p.capabilities_dir() / "thread-digest"
        assert (cap_dir / "capability.tlon").exists()
        # The review trail rides along with the whole-directory move.
        assert (cap_dir / "REVIEW.md").exists()
        metadata = (cap_dir / "meta.tlon").read_text(encoding="utf-8")
        assert ":trusted true" in metadata
        assert ':author "adam"' in metadata
        assert [c.name for c in list_trusted_capabilities(p)] == ["thread-digest"]

    def test_approve_missing_rejected(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        with pytest.raises(CapabilityError, match="no staged capability named 'nope'"):
            approve_capability("nope", p)

    def test_approve_trusted_conflict_rejected(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        _write_trusted(p, "thread-digest")
        staging_dir = p.capabilities_staging_dir() / "thread-digest"
        staging_dir.mkdir(parents=True)
        (staging_dir / "capability.tlon").write_text(VALID_CAPABILITY, encoding="utf-8")
        with pytest.raises(CapabilityError, match="already exists in the trusted library"):
            approve_capability("thread-digest", p)
        # The staged copy is untouched.
        assert (staging_dir / "capability.tlon").exists()

    def test_invalid_name_rejected(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        with pytest.raises(CapabilityError, match="invalid capability name"):
            approve_capability("a/b", p)


class TestReject:
    def test_reject_deletes_staged(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        write_staging_capability(VALID_CAPABILITY, tlon_paths=p)
        reject_capability("thread-digest", p)
        assert not (p.capabilities_staging_dir() / "thread-digest").exists()
        assert list_staging_capabilities(p) == []

    def test_reject_missing_rejected(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        with pytest.raises(CapabilityError, match="no staged capability named 'nope'"):
            reject_capability("nope", p)

    def test_path_injection_rejected(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        with pytest.raises(CapabilityError, match="invalid capability name"):
            reject_capability("../capabilities", p)
        with pytest.raises(CapabilityError, match="invalid capability name"):
            reject_capability("a/b", p)


class TestDetail:
    def test_trusted_detail(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        _write_trusted(p, "thread-digest")
        info, capability, reviews = get_capability_detail("thread-digest", tlon_paths=p)
        assert info.trusted is True
        assert capability == VALID_CAPABILITY
        assert reviews is None

    def test_staging_detail(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        write_staging_capability(VALID_CAPABILITY, author_id="adam", tlon_paths=p)
        append_staging_review("thread-digest", "entry", tlon_paths=p)
        info, capability, reviews = get_capability_detail(
            "thread-digest", staging=True, tlon_paths=p
        )
        assert info.trusted is False
        assert info.author == "adam"
        assert capability == VALID_CAPABILITY
        assert reviews is not None and "entry" in reviews

    def test_missing_raises(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        with pytest.raises(CapabilityError, match="no capability named 'nope'"):
            get_capability_detail("nope", tlon_paths=p)
        with pytest.raises(CapabilityError, match="no capability named 'nope'"):
            get_capability_detail("nope", staging=True, tlon_paths=p)
