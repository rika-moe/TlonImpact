"""Unified loader (phase C): one rules-form behavior.tlon read path.

Pins the total semantics — a missing / invalid / unvalidatable behavior
file refuses to boot (raises) — and the source hash provenance computed
for every member over the raw file bytes.
"""

import hashlib

import pytest

from tests.recipe_helpers import recipe_for
from tlon.agents.behavior_template import analyze
from tlon.agents.loader import load_member_spec
from tlon.agents.member_spec import MemberSpec, MemberSpecValidationError
from tlon.base.paths import TlonPaths
from tlosure.edn import EDNParseError, loads

SCRIPT_EDN = '{:version 1\n :rules [{:name "relay"\n          :on :mail-delivered\n          :actions [(mail-forward {:mail_id (mail-field msg :id) :to ["a@tlon.internal"]})]}]}\n'


@pytest.fixture
def tlon(tmp_path) -> TlonPaths:
    """Isolated ~/.tlon home handed to the loader explicitly."""
    return TlonPaths(home=tmp_path)


def _write_behavior(p: TlonPaths, agent_id: str, text: str) -> None:
    behavior = p.agent_behavior_file(agent_id)
    behavior.parent.mkdir(parents=True, exist_ok=True)
    behavior.write_text(text, encoding="utf-8")


class TestLoadMemberSpec:
    def test_reads_rules_form_with_source_hash(self, tlon):
        _write_behavior(tlon, "relay-1", SCRIPT_EDN)
        spec = load_member_spec("relay-1", tlon_paths=tlon)
        assert spec.version == 1
        assert spec.rules[0].name == "relay"
        assert spec.rules[0].on is not None
        # Source hash over the raw bytes, first 12 hex chars (every member).
        assert spec.source_hash == hashlib.sha256(SCRIPT_EDN.encode("utf-8")).hexdigest()[:12]

    def test_reads_session_request_form(self, tlon):
        _write_behavior(
            tlon,
            "thinker-1",
            '{:version 1 :rules [{:name "respond" :on :mail-delivered :when (contains? (or (mail-field msg :to) []) (get self :address)) :actions [(run-loop (quote {:context {:sections [(persona) (protocol)]} :stages [(think) (act)] :until (or (>= (get state :iteration) (get limits :max_iterations)) (>= (get state :error_count) (get limits :max_consecutive_errors))) :max-iterations 10 :max-consecutive-errors 3 :max-tokens 1000000 :reflect {:enabled false :interval-minutes 5}}))]}]}',
        )
        spec = load_member_spec("thinker-1", tlon_paths=tlon)
        assert recipe_for(spec.rules[0]) is not None
        assert spec.source_hash is not None

    def test_missing_file_refuses_boot(self, tlon):
        with pytest.raises(FileNotFoundError):
            load_member_spec("nobody", tlon_paths=tlon)

    def test_invalid_edn_refuses_boot(self, tlon):
        _write_behavior(tlon, "broken", "{:version 1 :rules [")
        with pytest.raises(EDNParseError):
            load_member_spec("broken", tlon_paths=tlon)

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
        with pytest.raises(
            MemberSpecValidationError, match="unknown function or special form: jump"
        ):
            load_member_spec("wild", tlon_paths=tlon)

    def test_ignores_top_level_parameters(self, tlon):
        """A write-side template declaration is not runtime semantics.

        A concrete spec (no placeholders) is parsed verbatim; the runtime
        ignores the ``:parameters`` map (an unknown-top-key warning).
        """
        _write_behavior(
            tlon,
            "relay-2",
            '{:version 1\n :parameters {:to {:type :string-list :required true}}\n :rules [{:name "relay"\n          :on :mail-delivered\n          :actions [(mail-forward {:mail_id (mail-field msg :id) :to ["a@tlon.internal"]})]}]}\n',
        )
        spec = load_member_spec("relay-2", tlon_paths=tlon)
        assert spec.rules[0].actions

    def test_parses_standin_text_when_template(self, tlon):
        """The write-side template's stand-in text parses as a valid spec."""
        template = '{:version 1\n :parameters {:to {:type :string-list :required true}}\n :rules [{:name "relay"\n          :on :mail-delivered\n          :actions [(mail-forward {:mail_id (mail-field msg :id) :to ${to}})]}]}\n'
        analyzed = analyze(template)
        member = MemberSpec.from_edn(loads(analyzed.standin_text))
        assert member.rules[0].actions
