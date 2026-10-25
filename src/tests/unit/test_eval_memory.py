"""Offline tests for the memory regression harness (scripts/eval_memory.py).

The runner itself needs real LLM/embedding (a manual gate, not CI); these
tests pin the offline parts: dataset loading/validation, the fake journal,
the assertion logic, and the full scenario flow with a fake embedder and
a deterministic model fixture — no network anywhere.
"""

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from tlon.memory.manager import MemoryManager
from tlon.memory.models import MemoryConfig

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "eval_memory.py"
_spec = importlib.util.spec_from_file_location("tlon_eval_memory_script", _SCRIPT)
assert _spec is not None and _spec.loader is not None
eval_memory = importlib.util.module_from_spec(_spec)
sys.modules.setdefault(_spec.name, eval_memory)
_spec.loader.exec_module(eval_memory)


# ── Dataset loading & validation ─────────────────────────────────────────


class TestLoadScenarios:
    def test_real_dataset_is_valid_and_covers_all_dimensions(self) -> None:
        scenarios = eval_memory.load_scenarios(eval_memory.DEFAULT_DATASET)
        assert len(scenarios) >= 12
        assert {s.dimension for s in scenarios} == set(eval_memory.DIMENSIONS)
        for scenario in scenarios:
            assert scenario.name
            assert scenario.query

    def test_runs_and_expectation_parsed(self, tmp_path: Path) -> None:
        dataset = tmp_path / "s.json"
        dataset.write_text(
            json.dumps(
                [
                    {
                        "name": "s1",
                        "dimension": "fact-recall",
                        "runs": [{"ts": "2026-08-01T10:00:00", "trigger": "t", "responses": ["r"]}],
                        "query": "q",
                        "expect": {
                            "surface": "injection",
                            "must_contain": ["a", ["b", "c"]],
                            "must_not_contain": ["d"],
                            "expect_empty": False,
                        },
                    }
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (scenario,) = eval_memory.load_scenarios(dataset)
        assert scenario.runs[0].ts == "2026-08-01T10:00:00"
        assert scenario.expect.surface == "injection"
        assert scenario.expect.must_contain == ["a", ["b", "c"]]

    @pytest.mark.parametrize(
        ("raw", "reason"),
        [
            ({"dimension": "fact-recall", "query": "q", "expect": {}}, "missing name"),
            (
                {"name": "s", "dimension": "nope", "query": "q", "expect": {}},
                "bad dimension",
            ),
            ({"name": "s", "dimension": "fact-recall", "expect": {}}, "missing query"),
            (
                {
                    "name": "s",
                    "dimension": "fact-recall",
                    "query": "q",
                    "expect": {"surface": "nowhere"},
                },
                "bad surface",
            ),
            (
                {
                    "name": "s",
                    "dimension": "fact-recall",
                    "query": "q",
                    "runs": [{"trigger": "t"}],
                    "expect": {},
                },
                "run missing ts",
            ),
            (
                {
                    "name": "s",
                    "dimension": "fact-recall",
                    "query": "q",
                    "expect": {"must_contain": [42]},
                },
                "bad must_contain item",
            ),
        ],
    )
    def test_malformed_scenarios_raise(self, tmp_path: Path, raw: dict, reason: str) -> None:
        dataset = tmp_path / "s.json"
        dataset.write_text(json.dumps([raw], ensure_ascii=False), encoding="utf-8")
        with pytest.raises(ValueError):
            eval_memory.load_scenarios(dataset)

    def test_duplicate_names_raise(self, tmp_path: Path) -> None:
        raw = {"name": "s", "dimension": "refusal", "query": "q", "expect": {}}
        dataset = tmp_path / "s.json"
        dataset.write_text(json.dumps([raw, raw], ensure_ascii=False), encoding="utf-8")
        with pytest.raises(ValueError):
            eval_memory.load_scenarios(dataset)


# ── Fake journal ─────────────────────────────────────────────────────────


class TestFakeJournal:
    async def test_completed_runs_listed_in_insertion_order(self) -> None:
        journal = eval_memory.FakeJournal()
        journal.add_completed_run("s1", "2026-08-01T10:00:00", "t1", ["r1"])
        journal.add_completed_run("s2", "2026-08-02T10:00:00", "t2", [])
        assert await journal.list_completed() == ["s1", "s2"]
        assert await journal.count_completed() == 2

    async def test_events_carry_start_ts_trigger_responses_and_end(self) -> None:
        journal = eval_memory.FakeJournal()
        journal.add_completed_run("s1", "2026-08-01T10:00:00", "真触发", ["回复一", "回复二"])
        events = await journal.read_events("s1")
        assert events[0] == {
            "type": "run/start",
            "ts": "2026-08-01T10:00:00",
            "trigger_content": "真触发",
        }
        assert [e["response"]["content"] for e in events if e["type"] == "llm/response"] == [
            "回复一",
            "回复二",
        ]
        assert events[-1]["type"] == "run/end"

    async def test_read_events_returns_copies(self) -> None:
        journal = eval_memory.FakeJournal()
        journal.add_completed_run("s1", "2026-08-01T10:00:00", "t", [])
        (await journal.read_events("s1"))[0]["trigger_content"] = "篡改"
        assert (await journal.read_events("s1"))[0]["trigger_content"] == "t"


# ── Assertion logic ───────────────────────────────────────────────────────


def _expect(**kwargs) -> "eval_memory.Expectation":
    defaults = {"surface": "both"}
    defaults.update(kwargs)
    return eval_memory.Expectation(**defaults)


class TestEvaluate:
    def test_must_contain_string_and_any_group(self) -> None:
        expect = _expect(must_contain=["uv", ["3 楼", "三楼"]])
        assert eval_memory.evaluate(expect, "依赖用 uv 管理", "工位在三楼") == []
        failures = eval_memory.evaluate(expect, "依赖用 uv 管理", "工位在二楼")
        assert len(failures) == 1 and "3 楼" in failures[0]

    def test_must_not_contain(self) -> None:
        expect = _expect(must_not_contain=["阿里"])
        assert eval_memory.evaluate(expect, "部署在腾讯云", "") == []
        assert eval_memory.evaluate(expect, "部署在阿里云", "")

    def test_surface_selection(self) -> None:
        # injection-only: a forbidden token in the search text is irrelevant.
        expect = _expect(surface="injection", must_not_contain=["阿里"])
        assert eval_memory.evaluate(expect, "腾讯云", "阿里云") == []
        # search-only: a missing token in the injection text is irrelevant.
        expect = _expect(surface="search", must_contain=["腾讯"])
        assert eval_memory.evaluate(expect, "", "搜索结果 for 'x':\n- 腾讯云") == []

    def test_search_query_echo_is_stripped(self) -> None:
        """The search header echoes the query verbatim; a forbidden token
        appearing only in the echoed query must not fail the assertion."""
        expect = _expect(surface="search", must_not_contain=["科幻"])
        search_text = "搜索结果 for '推荐科幻电影':\n\n未找到相关记忆。"
        assert eval_memory.evaluate(expect, "", search_text) == []

    def test_expect_empty(self) -> None:
        expect = _expect(expect_empty=True)
        assert eval_memory.evaluate(expect, "", "") == []
        assert eval_memory.evaluate(expect, "", "搜索结果 for 'x':\n未找到相关记忆。") == []
        assert eval_memory.evaluate(expect, "- [2026-08-01] 有内容", "")


# ── Offline end-to-end: full distill -> index -> JIT flow, no network ────


class _FakeEmbedder:
    """Deterministic bag-of-characters embedder: texts sharing characters
    embed close together, so retrieval is meaningful offline."""

    model_name = "fake-embed"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    @staticmethod
    def _vec(text: str, dim: int = 256) -> list[float]:
        vec = [0.0] * dim
        for ch in text:
            vec[ord(ch) % dim] += 1.0
        return vec


def _offline_factory(data_dir: Path) -> MemoryManager:
    manager = MemoryManager(
        "eval-test",
        MemoryConfig(distillation_window=1, context_max_claims=5),
        data_dir=data_dir,
        embedder=_FakeEmbedder(),
    )
    manager.distiller = cast(
        Any,
        SimpleNamespace(
            distill=AsyncMock(
                side_effect=lambda runs: [
                    {"text": run.trigger_content, "importance": 0.7} for run in runs
                ]
            )
        ),
    )
    return manager


class TestRunScenarioOffline:
    def _scenario(self) -> "eval_memory.Scenario":
        return eval_memory.Scenario(
            name="offline-recall",
            dimension="fact-recall",
            runs=[
                eval_memory.RunSpec(
                    ts="2026-08-01T10:00:00",
                    trigger="项目依赖统一用 uv 管理",
                    responses=["收到"],
                ),
                eval_memory.RunSpec(
                    ts="2026-08-02T10:00:00",
                    trigger="文档站用 mkdocs 搭建",
                    responses=["收到"],
                ),
            ],
            query="依赖管理工具",
            expect=eval_memory.Expectation(surface="both", must_contain=["uv", "mkdocs"]),
        )

    async def test_scenario_passes_and_every_run_distilled_once(self) -> None:
        result = await eval_memory.run_scenario(self._scenario(), _offline_factory)
        assert result.passed, result.failures
        # window=1 with increasing timestamps: one distilled claim per
        # run through the deterministic model fixture; both sources are consumed.
        assert result.claim_count == 2
        assert "uv" in result.injection_text
        assert "uv" in result.search_text

    async def test_unmet_expectation_fails_with_surfaces_captured(self) -> None:
        scenario = self._scenario()
        scenario.expect.must_contain = ["不存在的token"]
        scenario.expect.must_not_contain = ["uv"]
        result = await eval_memory.run_scenario(scenario, _offline_factory)
        assert not result.passed
        assert len(result.failures) == 2
        assert result.injection_text  # failure output carries the actual surfaces


# ── CLI ───────────────────────────────────────────────────────────────────


class TestMain:
    def test_list_does_not_require_embedding_model(self, capsys) -> None:
        assert eval_memory.main(["--list"]) == 0
        out = capsys.readouterr().out
        assert "update-cloud-migration" in out

    def test_missing_embedding_model_exits_2(self, monkeypatch, capsys) -> None:
        monkeypatch.delenv("TLON_EVAL_EMBEDDING_MODEL", raising=False)
        assert eval_memory.main(["--embedding-model", ""]) == 2
        assert "embedding" in capsys.readouterr().err

    def test_unknown_scenario_filter_exits_2(self) -> None:
        assert eval_memory.main(["--list", "--scenario", "no-such-scenario"]) == 2

    def test_bad_dataset_path_exits_2(self, tmp_path: Path) -> None:
        assert eval_memory.main(["--list", "--dataset", str(tmp_path / "nope.json")]) == 2
