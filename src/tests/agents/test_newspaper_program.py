"""Pure publication policy in the shipped Lisp newspaper module."""

from pathlib import Path
from typing import Any

import pytest

from tlon.agents.program_package import (
    program_builtins,
    program_module_sources,
    read_module_sources,
    validate_program_sources,
)
from tlosure.edn import dumps, loads
from tlosure.eval import eval_form
from tlosure.modules import ModulePackage


@pytest.mark.parametrize(
    ("timestamp", "expected"),
    [
        ("2026-10-25T23:59:59.999999+00:00", "2026-10-25"),
        ("2026-10-26T00:00:00Z", "2026-10-26"),
        ("2024-02-29T00:00:00+00:00", "2024-02-29"),
        ("2000-02-29T00:00:00Z", "2000-02-29"),
        ("1900-02-29T00:00:00Z", None),
        ("2026-02-29T00:00:00Z", None),
        ("2026-04-31T00:00:00Z", None),
        ("2026-10-25T24:00:00Z", None),
        ("2026-10-25T12:00:00+08:00", None),
        ("2026-10-25garbage", None),
        (None, None),
    ],
)
def test_period_uses_valid_utc_trigger_timestamp(
    timestamp: str | None, expected: str | None
) -> None:
    assert publication_period("postmaster@tlon.internal", "publish", timestamp) == expected


def publication_period(sender: str, subject: str, timestamp: str | None = None) -> Any:
    builtins = program_builtins()
    sources = program_module_sources(
        {"check.tlon": "(ns check (:require [tlon.newspaper :as paper]))"}
    )
    package = ModulePackage.from_sources(sources, builtins)
    mail = dumps({"from_address": sender, "subject": subject, "timestamp": timestamp})
    return eval_form(
        loads(f'(entry {mail} ["operator@tlon.internal" "postmaster@tlon.internal"])'),
        {"entry": package.exports["tlon.newspaper/publication-period"]},
        builtins,
        fuel=10000,
    )


def test_publishing_is_explicitly_authorized_for_both_commands() -> None:
    assert publication_period("reader@tlon.internal", "publish:001") is None
    assert publication_period("reader@tlon.internal", "publish", "2026-10-25T00:00:00Z") is None
    assert publication_period("operator@tlon.internal", "publish:001") == "001"
    assert publication_period("postmaster@tlon.internal", "publish:001") == "001"
    assert publication_period("operator@tlon.internal", "publish:") is None


@pytest.mark.parametrize("example", ["newspaper", "social-newspaper"])
def test_shipped_program_links_shared_newspaper_module(example: str) -> None:
    root = Path(__file__).parents[2] / "tlon/resources/examples" / example
    spec = validate_program_sources(
        (root / "behavior.tlon").read_text(), read_module_sources(root / "modules")
    )
    assert spec.execution_level == "L0"
