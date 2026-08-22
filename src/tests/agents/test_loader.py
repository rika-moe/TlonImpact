"""Unified loader (phase C): one rules-form behavior.tlon read path.

Pins the total semantics — a missing / invalid / unvalidatable behavior
file refuses to boot (raises) — and the source hash provenance computed
for every member over the raw file bytes.
"""

import hashlib

import pytest

from tlon.agents.behavior_template import analyze
from tlon.agents.edn import EDNParseError, loads
from tlon.agents.loader import load_member_spec
from tlon.agents.member_spec import MemberSpec, MemberSpecValidationError
from tlon.base.paths import TlonPaths

SCRIPT_EDN = """\
{:version 1
 :rules [{:name "relay"
          :on :mail-delivered
          :actions [(forward {:to ["a@tlon.internal"]})]}]}
"""


@pytest.fixture
def tlon(tmp_path, monkeypatch) -> TlonPaths:
    """Isolated ~/.tlon; the loader's ``paths`` reference is patched to it."""
    p = TlonPaths(home=tmp_path)
    monkeypatch.setattr("tlon.agents.loader.paths", p)
    return p


def _write_behavior(p: TlonPaths, agent_id: str, text: str) -> None:
    behavior = p.agent_behavior_file(agent_id)
    behavior.parent.mkdir(parents=True, exist_ok=True)
    behavior.write_text(text, encoding="utf-8")


class TestLoadMemberSpec:
    def test_reads_rules_form_with_source_hash(self, tlon):
        _write_behavior(tlon, "relay-1", SCRIPT_EDN)
        spec = load_member_spec("relay-1")
        assert spec.version == 1
        assert spec.rules[0].name == "relay"
        assert spec.rules[0].on is not None
        # Source hash over the raw bytes, first 12 hex chars (every member).
        assert spec.source_hash == hashlib.sha256(SCRIPT_EDN.encode("utf-8")).hexdigest()[:12]

    def test_reads_cognitive_rules_form(self, tlon):
        _write_behavior(
            tlon,
            "thinker-1",
            """\
{:version 1
 :rules [{:name "respond"
          :on :mail-delivered
          :when (addressed-to-me? msg)
          :loop {:context {:sections [(persona) (protocol)]}
                 :stages [(think) (act)]
                 :until (or (iteration-limit-reached?) (error-limit-reached?))
                 :max-iterations 10
                 :max-consecutive-errors 3
                 :reflect {:enabled false :interval-minutes 5}}}]}
""",
        )
        spec = load_member_spec("thinker-1")
        assert spec.rules[0].loop is not None
        assert spec.source_hash is not None

    def test_missing_file_refuses_boot(self, tlon):
        with pytest.raises(FileNotFoundError):
            load_member_spec("nobody")

    def test_invalid_edn_refuses_boot(self, tlon):
        _write_behavior(tlon, "broken", "{:version 1 :rules [")
        with pytest.raises(EDNParseError):
            load_member_spec("broken")

    def test_spec_failing_validator_refuses_boot(self, tlon):
        _write_behavior(
            tlon,
            "wild",
            """\
{:version 1
 :rules [{:name "wild"
          :on :mail-delivered
          :actions [(jump {:to ["x@tlon.internal"]})]}]}
""",
        )
        with pytest.raises(MemberSpecValidationError, match="unknown action"):
            load_member_spec("wild")

    def test_ignores_top_level_parameters(self, tlon):
        """A write-side template declaration is not runtime semantics.

        A concrete spec (no placeholders) is parsed verbatim; the runtime
        ignores the ``:parameters`` map (an unknown-top-key warning).
        """
        _write_behavior(
            tlon,
            "relay-2",
            """\
{:version 1
 :parameters {:to {:type :string-list :required true}}
 :rules [{:name "relay"
          :on :mail-delivered
          :actions [(forward {:to ["a@tlon.internal"]})]}]}
""",
        )
        spec = load_member_spec("relay-2")
        assert spec.rules[0].actions

    def test_parses_standin_text_when_template(self, tlon):
        """The write-side template's stand-in text parses as a valid spec."""
        template = """\
{:version 1
 :parameters {:to {:type :string-list :required true}}
 :rules [{:name "relay"
          :on :mail-delivered
          :actions [(forward {:to ${to}})]}]}
"""
        analyzed = analyze(template)
        member = MemberSpec.from_edn(loads(analyzed.standin_text))
        assert member.rules[0].actions
