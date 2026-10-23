"""Static checking exposes repairable source errors, without running source."""

from tlon.agents.program_package import check_program_sources
from tlon.operations.core import CallContext
from tlon.operations.program import program_check_operation


async def test_program_check_reports_invalid_then_accepts_single_module() -> None:
    operation = program_check_operation(check_program_sources)
    context = CallContext("alice", "run", "call")
    behavior = "{:version 2 :handlers {:mail-delivered work/handle} :fuel 1000}"
    invalid = await operation.handler(
        context,
        {
            "behavior": behavior,
            "modules": {
                "work.tlon": "(ns work (:export handle)) (defn handle [event] (missing event))",
            },
        },
    )
    assert invalid["valid"] is False and invalid["diagnostics"]
    valid = await operation.handler(
        context,
        {
            "behavior": behavior,
            "modules": {
                "work.tlon": '(ns work (:export handle)) (defn handle [event] (assert false "not run"))',
            },
        },
    )
    assert valid["valid"] is True and valid["diagnostics"] == []
    assert valid["digest"] != invalid["digest"]
    assert operation.effects == frozenset({"read"})


async def test_checker_does_not_disguise_infrastructure_type_error() -> None:
    from unittest.mock import patch

    import pytest

    operation = program_check_operation(check_program_sources)
    with patch(
        "tlon.agents.program_package.validate_program_sources", side_effect=TypeError("bug")
    ), pytest.raises(TypeError, match="bug"):
        await operation.handler(
            CallContext("alice", "run", "call"),
            {
                "behavior": "{:version 2 :handlers {:mail-delivered work/handle} :fuel 1000}",
                "modules": {
                    "work.tlon": "(ns work (:export handle)) (defn handle [event] event)"
                },
            },
        )
