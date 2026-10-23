"""Only ordinary module handler declarations are admitted."""

import pytest

from tlon.agents.member_spec import MemberSpec, MemberSpecValidationError
from tlon.agents.program_package import validate_program_sources
from tlosure.edn import loads


def test_handler_roundtrip_and_event_projection() -> None:
    spec = MemberSpec.from_edn(
        loads(
            "{:version 2 :handlers {:boot work/start :mail-delivered work/mail} :execution {:level :L0}}"
        )
    )
    assert spec.handlers[0].on is None
    assert spec.handlers[1].event_name == "mail-delivered"
    assert spec.handlers[1].entry == "work/mail"
    assert MemberSpec.from_edn(loads(spec.to_edn())) == spec


@pytest.mark.parametrize(
    "source",
    [
        "{:version 1 :rules []}",
        "{:version 2 :rules [] :handlers {:boot work/start}}",
        "{:version 2 :handlers {}}",
        "{:version 2 :handlers {:boot start}}",
        "{:version 2 :handlers {:unknown work/start}}",
        "{:version 2 :handlers {:boot work/start} :fuel 0}",
    ],
)
def test_legacy_and_invalid_declarations_rejected(source: str) -> None:
    with pytest.raises(MemberSpecValidationError):
        MemberSpec.from_edn(loads(source))


@pytest.mark.parametrize("word", ["run-loop", "sleep-until", "exit-agent"])
def test_removed_words_are_not_module_vocabulary(word: str) -> None:
    with pytest.raises(MemberSpecValidationError):
        validate_program_sources(
            "{:version 2 :handlers {:boot work/start}}",
            {"work.tlon": "(ns work (:export start)) (defn start [event] (" + word + " {}))"},
        )
