"""The copyable author guide package remains executable, not just plausible prose."""

import json
import re
from pathlib import Path
from typing import Any

from tlon.agents.program_package import (
    check_program_sources,
    program_builtins,
    program_module_sources,
)
from tlosure.builtins import Builtin
from tlosure.edn import Keyword, loads
from tlosure.eval import eval_form
from tlosure.modules import ModulePackage


def test_author_guide_package_checks_and_replies() -> None:
    guide = Path(__file__).resolve().parents[3] / "docs/guides/writing-a-prototype.md"
    examples = [
        json.loads(block) for block in re.findall(r"```json\n(.*?)\n```", guide.read_text(), re.S)
    ]
    candidate, proposal = examples
    assert proposal["behavior"] == candidate["behavior"]
    assert proposal["modules"] == candidate["modules"]
    assert check_program_sources(**candidate)["valid"] is True
    sent: list[dict[Any, Any]] = []

    def send_mail(_env: Any, request: dict[Any, Any]) -> dict[str, str]:
        sent.append(request)
        return {"status": "accepted"}

    builtins = program_builtins()
    builtins["mail-send"] = Builtin(send_mail, pure=False)
    package = ModulePackage.from_sources(program_module_sources(candidate["modules"]), builtins)
    behavior = loads(candidate["behavior"])
    entry = str(behavior[Keyword("handlers")][Keyword("mail-delivered")])
    result = eval_form(
        loads('(handle {:sender "human@test" :mail {:content "guide-smoke-42"}})'),
        {"handle": package.entry(entry)},
        builtins,
        fuel=behavior[Keyword("fuel")],
    )
    assert result == {Keyword("result"): "收到：guide-smoke-42"}
    assert sent == [{Keyword("to"): ["human@test"], Keyword("content"): "收到：guide-smoke-42"}]
