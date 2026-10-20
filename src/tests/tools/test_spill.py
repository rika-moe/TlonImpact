"""Tool output spill (harness-review item 2): oversized results leave the
context and land in the member workspace as a pageable file; the model and
the session journal only ever see the locator.
"""

import json
from pathlib import Path

import pytest

from tlon.base.llm_client import ToolCall
from tlon.base.paths import TlonPaths
from tlon.tools.base import ToolResult
from tlon.tools.spill import PREVIEW_CHARS, SPILL_THRESHOLD_CHARS, ToolSpill


@pytest.fixture
def spill(tmp_path: Path) -> ToolSpill:
    return ToolSpill(TlonPaths(home=tmp_path), "agent1")


def _tc(tc_id: str = "c1") -> ToolCall:
    return ToolCall(id=tc_id, function_name="shell-run", arguments={})


class TestToolSpill:
    async def test_small_result_passes_through_untouched(self, spill: ToolSpill) -> None:
        result = ToolResult(success=True, data={"stdout": "hello"})

        out = await spill.maybe_spill("s1", _tc(), result)

        assert out is result

    async def test_threshold_boundary(self, spill: ToolSpill, tmp_path: Path) -> None:
        # Exactly at the threshold: not spilled.
        payload = {"stdout": "x" * (SPILL_THRESHOLD_CHARS - len('{"stdout": ""}'))}
        out = await spill.maybe_spill("s1", _tc(), ToolResult(success=True, data=payload))
        assert out.data == payload
        assert not (tmp_path / "agent1" / "workspace" / "spills").exists()

    async def test_oversized_result_spills_to_workspace(
        self, spill: ToolSpill, tmp_path: Path
    ) -> None:
        big = {"exit_code": 0, "stdout": "y" * (SPILL_THRESHOLD_CHARS * 2), "stderr": ""}
        result = ToolResult(success=True, data=big, message="done")

        out = await spill.maybe_spill("s1", _tc("c-big"), result)

        # The model sees a locator, not the payload.
        assert out.success is True
        assert out.message == "done"
        locator = out.data
        assert locator["_spilled"] is True
        assert locator["spill_path"] == "spills/s1/c-big.json"
        assert locator["size_chars"] > SPILL_THRESHOLD_CHARS
        assert locator["preview"] == json.dumps(big, ensure_ascii=False)[:PREVIEW_CHARS]
        assert locator["hint"]
        assert "y" * (PREVIEW_CHARS + 1) not in json.dumps(locator)

        # The file holds the full payload, pretty-printed for pagination.
        spill_file = tmp_path / "agent1" / "workspace" / "spills" / "s1" / "c-big.json"
        assert json.loads(spill_file.read_text(encoding="utf-8")) == big
        assert "\n" in spill_file.read_text(encoding="utf-8")

    async def test_failed_result_with_big_stderr_also_spills(self, spill: ToolSpill) -> None:
        big = {"exit_code": 1, "stdout": "", "stderr": "e" * (SPILL_THRESHOLD_CHARS * 2)}
        result = ToolResult(success=False, data=big, error=big["stderr"][:100])

        out = await spill.maybe_spill("s1", _tc(), result)

        assert out.success is False
        assert out.data["_spilled"] is True
        assert out.error == result.error  # error field is preserved as-is

    async def test_spill_write_failure_falls_back_to_original(
        self, spill: ToolSpill, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        big = {"stdout": "z" * (SPILL_THRESHOLD_CHARS * 2)}
        result = ToolResult(success=True, data=big)

        import aiofiles

        def boom(*args: object, **kwargs: object) -> object:
            raise OSError("read-only filesystem")

        monkeypatch.setattr(aiofiles, "open", boom)

        out = await spill.maybe_spill("s1", _tc(), result)

        assert out is result  # a big context beats a lost one
