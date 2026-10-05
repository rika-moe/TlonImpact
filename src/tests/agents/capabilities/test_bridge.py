"""The vocabulary bridge (bridge.py): trusted capability library → member
registries — word loading with purity markers, fail-soft exclusion of
bad artifacts, host-word namespace reservation, deprecated words
registering normally, and the end-to-end path through the member-spec
loader (purity partition into the spec's evaluation contexts).
"""

import pytest

from tlon.agents.capabilities.bridge import load_capability_builtins
from tlon.agents.loader import load_member_spec
from tlon.agents.member_spec import MemberSpecValidationError
from tlon.agents.vocabulary import make_member_builtins
from tlon.base.paths import TlonPaths

PURE_CAPABILITY = """\
{:version 1
 :name "thread-digest"
 :description "把邮件线程蒸馏成三段式摘要文本"
 :args [{:name "mails" :type :list :doc "按时间升序的邮件列表"}]
 :impl (fn [mails] (str "digest:" (count mails)))
 :pure true}
"""

IMPURE_CAPABILITY = """\
{:version 1
 :name "notify-adam"
 :description "给 Adam 发一封通知邮件"
 :args [{:name "content" :type :string :doc "通知正文"}]
 :impl (fn [content] (emit content ["adam@tlon.internal"]))
 :pure false}
"""

#: Fails the load gate: the impl composes unregistered vocabulary.
INVALID_CAPABILITY = """\
{:version 1
 :name "wild"
 :description "组合未注册词汇"
 :args []
 :impl (fn [] (jump "x"))
 :pure false}
"""

#: Unparseable EDN.
BROKEN_CAPABILITY = "{:version 1 :name "

#: Collides with a capability-registry builtin word.
BUILTIN_COLLIDING_CAPABILITY = """\
{:version 1
 :name "count"
 :description "与内置词同名"
 :args [{:name "xs" :type :list :doc "..."}]
 :impl (fn [xs] xs)
 :pure true}
"""

#: Legal word name that is also a closure-injected host word (absent from
#: the host-less make_member_builtins()).
HOST_WORD_CAPABILITY = """\
{:version 1
 :name "member-roster"
 :description "与宿主查询词同名"
 :args []
 :impl (fn [] "roster")
 :pure true}
"""


def _write_trusted(p: TlonPaths, name: str, text: str, metadata: str | None = None) -> None:
    """Handwrite a capability directly into the trusted library."""
    cap_dir = p.capabilities_dir() / name
    cap_dir.mkdir(parents=True)
    (cap_dir / "capability.tlon").write_text(text, encoding="utf-8")
    if metadata is not None:
        (cap_dir / "capability.edn").write_text(metadata, encoding="utf-8")


def _write_behavior(p: TlonPaths, agent_id: str, text: str) -> None:
    behavior = p.agent_behavior_file(agent_id)
    behavior.parent.mkdir(parents=True, exist_ok=True)
    behavior.write_text(text, encoding="utf-8")


def _merged(p: TlonPaths) -> dict:
    """The registry AgentRegistry._member_builtins() builds."""
    host = make_member_builtins()
    return {**host, **load_capability_builtins(p, reserved=host)}


class TestRuntimeVocabularyContract:
    """The bridge's runtime contract: every word the capability admission
    registry allows in an ``:impl`` must resolve in the member registry —
    otherwise an approved artifact would pass staging and fail at runtime
    in every member."""

    def test_capability_vocabulary_is_subset_of_member_registry(self):
        from tlon.agents.capabilities.spec import CAPABILITY_FULL_BUILTINS

        member = make_member_builtins()
        assert set(CAPABILITY_FULL_BUILTINS) <= set(member)


class TestLoadCapabilityBuiltins:
    def test_empty_library_loads_nothing(self, tmp_path):
        assert load_capability_builtins(TlonPaths(home=tmp_path)) == {}

    def test_loads_words_as_closure_backed_with_purity(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        _write_trusted(p, "thread-digest", PURE_CAPABILITY)
        _write_trusted(p, "notify-adam", IMPURE_CAPABILITY)

        words = load_capability_builtins(p)

        assert set(words) == {"thread-digest", "notify-adam"}
        pure, impure = words["thread-digest"], words["notify-adam"]
        assert pure.pure is True and impure.pure is False
        for entry in words.values():
            assert entry.fn is None
            assert entry.closure is not None
            assert entry.closure.env == {}  # zero injection
        assert pure.closure is not None and pure.closure.name == "thread-digest"
        assert pure.closure.params == ["mails"]

    def test_deprecated_word_registers_normally(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        _write_trusted(
            p, "thread-digest", PURE_CAPABILITY, metadata='{:deprecated true}'
        )
        assert set(load_capability_builtins(p)) == {"thread-digest"}

    def test_bad_artifact_excluded_others_unaffected(self, tmp_path):
        """Fail-soft: one bad trusted artifact never fails the registry —
        a spec referencing the excluded word fails its own load instead."""
        p = TlonPaths(home=tmp_path)
        _write_trusted(p, "thread-digest", PURE_CAPABILITY)
        _write_trusted(p, "wild", INVALID_CAPABILITY)
        _write_trusted(p, "broken", BROKEN_CAPABILITY)

        assert set(load_capability_builtins(p)) == {"thread-digest"}

    def test_builtin_name_collision_excluded(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        _write_trusted(p, "count", BUILTIN_COLLIDING_CAPABILITY)
        assert load_capability_builtins(p) == {}

    def test_host_word_collision_excluded_via_reserved(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        _write_trusted(p, "member-roster", HOST_WORD_CAPABILITY)

        # Host-less default: member-roster is absent from the host-less
        # registry, so the caller must reserve it explicitly.
        assert set(load_capability_builtins(p)) == {"member-roster"}
        assert load_capability_builtins(p, reserved=["member-roster"]) == {}


SPEC_USING_CAPABILITY = """\
{:version 1
 :rules [{:name "digest"
          :on :mail-delivered
          :when (= (thread-digest ["a" "b"]) "digest:2")
          :actions [(notify-adam "new mail")]}]}
"""

SPEC_WHEN_USING_IMPURE = """\
{:version 1
 :rules [{:name "bad-gate"
          :on :mail-delivered
          :when (notify-adam "x")
          :actions []}]}
"""


class TestLoaderEndToEnd:
    """The merged registry through the member-spec loader: capability words
    validate in the contexts their purity admits."""

    def test_spec_referencing_capability_words_loads(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        _write_trusted(p, "thread-digest", PURE_CAPABILITY)
        _write_trusted(p, "notify-adam", IMPURE_CAPABILITY)
        _write_behavior(p, "digester-1", SPEC_USING_CAPABILITY)

        spec = load_member_spec("digester-1", tlon_paths=p, builtins=_merged(p))
        assert spec.rules[0].name == "digest"

    def test_when_referencing_impure_word_rejected(self, tmp_path):
        """The purity partition: a non-pure capability word is invisible to
        the :when gate (unknown word in the pure subset)."""
        p = TlonPaths(home=tmp_path)
        _write_trusted(p, "notify-adam", IMPURE_CAPABILITY)
        _write_behavior(p, "bad-1", SPEC_WHEN_USING_IMPURE)

        with pytest.raises(MemberSpecValidationError, match="unknown function"):
            load_member_spec("bad-1", tlon_paths=p, builtins=_merged(p))

    def test_spec_referencing_excluded_word_rejected(self, tmp_path):
        """The blast-radius contract: a rejected capability word leaves its
        users failing their own load validation, nobody else."""
        p = TlonPaths(home=tmp_path)
        _write_trusted(p, "wild", INVALID_CAPABILITY)
        _write_behavior(
            p,
            "wild-1",
            """\
{:version 1
 :rules [{:name "uses-wild"
          :on :mail-delivered
          :actions [(wild)]}]}
""",
        )
        with pytest.raises(MemberSpecValidationError, match="unknown function"):
            load_member_spec("wild-1", tlon_paths=p, builtins=_merged(p))

    def test_capability_body_evaluates_under_member_registry(self, tmp_path):
        """A pure capability word evaluates inside a rule gate: the body
        composes host vocabulary through the caller's registry."""
        from tlosure.edn import loads
        from tlosure.eval import eval_form

        p = TlonPaths(home=tmp_path)
        _write_trusted(p, "thread-digest", PURE_CAPABILITY)
        result = eval_form(
            loads('(thread-digest ["m1" "m2" "m3"])'), {}, _merged(p)
        )
        assert result == "digest:3"
