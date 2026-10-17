#!/usr/bin/env python3
"""Memory regression eval (ADR 0017 stage 5).

Runs the scenario dataset (``scripts/memory_eval/scenarios.json``) against
the real memory stack — distillation (LLM), vector indexing (embedding),
JIT injection and search — and asserts per-scenario expectations on what
must and must not surface. Four dimensions, aligned with LongMemEval:

- ``fact-recall``: a fact buried in a session must be retrieved by a
  related query;
- ``knowledge-update``: a newer fact supersedes the contradicting older
  one — retrieval surfaces the new, never the invalidated old;
- ``temporal``: across several state changes, "current/latest" queries
  surface the latest value (valid_from/valid_to end to end);
- ``refusal``: a query about something memory does not contain must not
  surface fabricated or unrelated claims.

This is a manual gate, not CI: it needs real LLM + embedding calls (costs
money, needs API keys, and LLM output varies — one retry per scenario is
allowed before a FAIL is recorded). Run it whenever the memory system
changes. See scripts/memory_eval/README.md.

Usage:
    uv run python scripts/eval_memory.py \
        --model deepseek/deepseek-chat \
        --embedding-model openai/text-embedding-3-small

Exit codes:
    0 - all scenarios passed
    1 - at least one scenario failed (after one retry)
    2 - usage/configuration error (missing embedding model, bad dataset)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from tlon.base.config import LLMConfig
from tlon.base.paths import TlonPaths
from tlon.memory.manager import MemoryManager
from tlon.memory.models import MemoryConfig

DIMENSIONS = ("fact-recall", "knowledge-update", "temporal", "refusal")
SURFACES = ("injection", "search", "both")

DEFAULT_DATASET = Path(__file__).parent / "memory_eval" / "scenarios.json"
DEFAULT_MODEL = "deepseek/deepseek-chat"

#: Eval tuning: every completed session distills immediately (per-scenario
#: control), and the injection top-k stays small enough that the refusal
#: dimension can assert selectivity (memory must hold more claims than k).
_EVAL_DISTILLATION_WINDOW = 1
_EVAL_CONTEXT_MAX_CLAIMS = 5

#: Header line of ``MemoryManager.search_memories`` output — it echoes the
#: query verbatim, so it is chrome, not memory content, and is stripped
#: before assertions (a forbidden token appearing in the query itself must
#: not false-positive).
_SEARCH_HEADER_PREFIX = "搜索结果 for"
_SEARCH_EMPTY_MARKER = "未找到相关记忆"


# ── Dataset model ────────────────────────────────────────────────────────


@dataclass
class SessionSpec:
    """One scripted completed session: a trigger mail plus agent replies."""

    ts: str
    trigger: str
    responses: list[str]


@dataclass
class Expectation:
    """Assertions on the retrieval surfaces.

    ``must_contain`` items are either a string (must appear) or a list of
    strings (at least one must appear — LLM paraphrase tolerance).
    """

    surface: str
    must_contain: list[str | list[str]] = field(default_factory=list)
    must_not_contain: list[str] = field(default_factory=list)
    expect_empty: bool = False


@dataclass
class Scenario:
    name: str
    dimension: str
    sessions: list[SessionSpec]
    query: str
    expect: Expectation


def _parse_session(raw: Any, scenario_name: str) -> SessionSpec:
    if not isinstance(raw, dict):
        raise ValueError(f"场景 {scenario_name}: session 必须是对象")
    ts = raw.get("ts")
    trigger = raw.get("trigger")
    if not isinstance(ts, str) or not ts:
        raise ValueError(f"场景 {scenario_name}: session 缺少 ts")
    if not isinstance(trigger, str) or not trigger:
        raise ValueError(f"场景 {scenario_name}: session 缺少 trigger")
    responses = raw.get("responses", [])
    if not isinstance(responses, list) or not all(isinstance(r, str) for r in responses):
        raise ValueError(f"场景 {scenario_name}: responses 必须是字符串列表")
    return SessionSpec(ts=ts, trigger=trigger, responses=list(responses))


def _parse_expectation(raw: Any, scenario_name: str) -> Expectation:
    if not isinstance(raw, dict):
        raise ValueError(f"场景 {scenario_name}: 缺少 expect 对象")
    surface = raw.get("surface", "both")
    if surface not in SURFACES:
        raise ValueError(f"场景 {scenario_name}: surface 必须是 {SURFACES} 之一，得到 {surface!r}")
    must_contain = raw.get("must_contain", [])
    if not isinstance(must_contain, list) or not all(
        isinstance(item, str)
        or (isinstance(item, list) and item and all(isinstance(alt, str) for alt in item))
        for item in must_contain
    ):
        raise ValueError(f"场景 {scenario_name}: must_contain 必须是字符串或字符串列表的列表")
    must_not_contain = raw.get("must_not_contain", [])
    if not isinstance(must_not_contain, list) or not all(
        isinstance(item, str) for item in must_not_contain
    ):
        raise ValueError(f"场景 {scenario_name}: must_not_contain 必须是字符串列表")
    return Expectation(
        surface=surface,
        must_contain=list(must_contain),
        must_not_contain=list(must_not_contain),
        expect_empty=bool(raw.get("expect_empty", False)),
    )


def _parse_scenario(raw: Any) -> Scenario:
    if not isinstance(raw, dict):
        raise ValueError("场景必须是对象")
    name = raw.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError("场景缺少 name")
    dimension = raw.get("dimension")
    if dimension not in DIMENSIONS:
        raise ValueError(f"场景 {name}: dimension 必须是 {DIMENSIONS} 之一，得到 {dimension!r}")
    sessions_raw = raw.get("sessions", [])
    if not isinstance(sessions_raw, list):
        raise ValueError(f"场景 {name}: sessions 必须是列表")
    query = raw.get("query")
    if not isinstance(query, str) or not query:
        raise ValueError(f"场景 {name}: 缺少 query")
    return Scenario(
        name=name,
        dimension=dimension,
        sessions=[_parse_session(s, name) for s in sessions_raw],
        query=query,
        expect=_parse_expectation(raw.get("expect"), name),
    )


def load_scenarios(dataset_path: Path) -> list[Scenario]:
    """Load and validate the scenario dataset; ValueError on any malformation."""
    try:
        raw = json.loads(dataset_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise ValueError(f"数据集读取失败 {dataset_path}: {e}") from e
    if not isinstance(raw, list) or not raw:
        raise ValueError(f"数据集 {dataset_path} 必须是非空场景列表")
    scenarios = [_parse_scenario(item) for item in raw]
    names = [s.name for s in scenarios]
    if len(names) != len(set(names)):
        raise ValueError("场景 name 有重复")
    return scenarios


# ── Fake journal (in-memory SessionLogReader) ────────────────────────────


class FakeJournal:
    """In-memory ``SessionLogReader``: completed sessions scripted by the
    scenario, in the real journal's event shape (ADR 0007).

    The ``session/start`` timestamp is the scenario's ``ts`` — distillation
    pending-order and the watermark compare against it, so scenarios
    control distillation order through timestamps.
    """

    def __init__(self) -> None:
        self._logs: dict[str, list[dict[str, Any]]] = {}

    def add_completed_session(
        self, session_id: str, ts: str, trigger: str, responses: list[str]
    ) -> None:
        events: list[dict[str, Any]] = [
            {"type": "session/start", "ts": ts, "trigger_content": trigger}
        ]
        events.extend(
            {"type": "llm/response", "response": {"content": content}} for content in responses
        )
        events.append({"type": "session/end", "ts": ts})
        self._logs[session_id] = events

    async def list_completed(self, started_after: datetime | None = None) -> list[str]:
        completed: list[str] = []
        for session_id, events in self._logs.items():
            if not any(e.get("type") == "session/end" for e in events):
                continue
            if started_after is not None:
                start = next((e for e in events if e.get("type") == "session/start"), None)
                raw_ts = start.get("ts") if start is not None else None
                try:
                    started_at = datetime.fromisoformat(str(raw_ts)) if raw_ts else None
                except ValueError:
                    started_at = None
                if started_at is not None and started_at <= started_after:
                    continue
            completed.append(session_id)
        return completed

    async def count_completed(self, started_after: datetime | None = None) -> int:
        return len(await self.list_completed(started_after=started_after))

    async def read_events(self, session_id: str) -> list[dict[str, Any]]:
        return [dict(event) for event in self._logs[session_id]]


# ── Evaluation ───────────────────────────────────────────────────────────


def _search_memory_content(search_text: str) -> str:
    """The memory-content lines of a ``search_memories`` output (the query
    echo header stripped — it is chrome, not retrieved memory)."""
    return "\n".join(
        line for line in search_text.splitlines() if not line.startswith(_SEARCH_HEADER_PREFIX)
    )


def evaluate(expect: Expectation, injection_text: str, search_text: str) -> list[str]:
    """Assert the expectation against the selected surfaces.

    Returns the list of failure reasons (empty = pass).
    """
    texts: list[str] = []
    if expect.surface in ("injection", "both"):
        texts.append(injection_text)
    if expect.surface in ("search", "both"):
        texts.append(_search_memory_content(search_text))
    combined = "\n".join(t for t in texts if t)

    failures: list[str] = []
    for item in expect.must_contain:
        alternatives = [item] if isinstance(item, str) else list(item)
        if not any(alt in combined for alt in alternatives):
            failures.append(f"缺少期望内容: {' / '.join(alternatives)}")
    for token in expect.must_not_contain:
        if token in combined:
            failures.append(f"出现禁止内容: {token}")
    if expect.expect_empty and combined and _SEARCH_EMPTY_MARKER not in combined:
        failures.append("期望检索面为空，但实际返回了内容")
    return failures


# ── Scenario runner ──────────────────────────────────────────────────────

#: Builds an isolated MemoryManager rooted at a fresh directory.
ManagerFactory = Callable[[Path], MemoryManager]


@dataclass
class ScenarioResult:
    scenario: Scenario
    passed: bool
    failures: list[str]
    injection_text: str
    search_text: str
    claim_count: int


async def run_scenario(scenario: Scenario, manager_factory: ManagerFactory) -> ScenarioResult:
    """Run one scenario end to end: fresh isolated memory -> scripted
    sessions distilled one by one (window=1, timestamp-ordered) -> query
    both retrieval surfaces -> evaluate."""
    with tempfile.TemporaryDirectory(prefix="tlon-memory-eval-") as tmp:
        manager = manager_factory(Path(tmp))
        await manager.load()
        journal = FakeJournal()
        for i, session in enumerate(scenario.sessions):
            journal.add_completed_session(
                f"eval-{scenario.name}-{i}", session.ts, session.trigger, session.responses
            )
            await manager.maybe_distill_completed_sessions(journal)

        blocks = await manager.build_context_blocks(scenario.query)
        injection_text = "\n".join(block.content for block in blocks)
        search_text = await manager.search_memories(scenario.query)
        claim_count = len(await manager.claims.get_all_claims())

    failures = evaluate(scenario.expect, injection_text, search_text)
    return ScenarioResult(
        scenario=scenario,
        passed=not failures,
        failures=failures,
        injection_text=injection_text,
        search_text=search_text,
        claim_count=claim_count,
    )


# ── Reporting ────────────────────────────────────────────────────────────


def _print_estimate(scenarios: list[Scenario]) -> None:
    session_count = sum(len(s.sessions) for s in scenarios)
    print(f"即将运行 {len(scenarios)} 个场景（共 {session_count} 个 session）。")
    print(
        "预估调用量："
        f"蒸馏 LLM 调用 {session_count} 次（window=1，每 session 一批），"
        f"冲突判定 LLM 调用至多 {session_count} 次（仅有相似旧 claim 时发起）；"
        f"embedding 调用约 {len(scenarios) * 2} 次检索 query + 每场景数条 claim 写入。"
        "失败场景会整体重试一次，调用量最多翻倍。"
    )


def _print_result(index: int, total: int, result: ScenarioResult, retried: bool) -> None:
    scenario = result.scenario
    if result.passed:
        suffix = "（重试后通过）" if retried else ""
        print(f"[{index}/{total}] {scenario.dimension} / {scenario.name} ... PASS{suffix}")
        return
    suffix = "（重试一次后仍失败）" if retried else ""
    print(f"[{index}/{total}] {scenario.dimension} / {scenario.name} ... FAIL{suffix}")
    for reason in result.failures:
        print(f"    - {reason}")
    print("    --- 注入面 build_context_blocks ---")
    print("    " + (result.injection_text.replace("\n", "\n    ") or "（空）"))
    print("    --- 检索面 search_memories ---")
    print("    " + (result.search_text.replace("\n", "\n    ") or "（空）"))


def _print_summary(results: list[ScenarioResult]) -> None:
    print("\n======== 汇总 ========")
    for dimension in DIMENSIONS:
        in_dimension = [r for r in results if r.scenario.dimension == dimension]
        if not in_dimension:
            continue
        passed = sum(1 for r in in_dimension if r.passed)
        print(f"{dimension}: {passed}/{len(in_dimension)}")
    passed_total = sum(1 for r in results if r.passed)
    rate = passed_total / len(results) * 100 if results else 0.0
    print(f"总计: {passed_total}/{len(results)} 通过 ({rate:.1f}%)")


async def _run_all(
    scenarios: list[Scenario], manager_factory: ManagerFactory, allow_retry: bool
) -> list[ScenarioResult]:
    results: list[ScenarioResult] = []
    total = len(scenarios)
    for index, scenario in enumerate(scenarios, 1):
        result = await run_scenario(scenario, manager_factory)
        retried = False
        if not result.passed and allow_retry:
            # Real LLM distillation/judgment varies run to run — one full
            # rerun (fresh memory) before the FAIL is recorded.
            retried = True
            result = await run_scenario(scenario, manager_factory)
        _print_result(index, total, result, retried)
        results.append(result)
    return results


# ── CLI ──────────────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="记忆系统回归集（ADR 0017 阶段 5）：真实 LLM/embedding 的手动门禁，不进 CI。"
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("TLON_EVAL_MODEL", DEFAULT_MODEL),
        help=f"蒸馏/判定模型，provider/model 形式（默认 $TLON_EVAL_MODEL 或 {DEFAULT_MODEL}）",
    )
    parser.add_argument(
        "--embedding-model",
        default=os.environ.get("TLON_EVAL_EMBEDDING_MODEL"),
        help="embedding 模型，provider/model 形式（也可设 $TLON_EVAL_EMBEDDING_MODEL）；必填",
    )
    parser.add_argument(
        "--api-base",
        default=None,
        help="OpenAI 兼容端点覆盖（同时作用于 chat 与 embedding，成员级 api_base 同款语义）",
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=DEFAULT_DATASET,
        help=f"场景数据集路径（默认 {DEFAULT_DATASET}）",
    )
    parser.add_argument("--scenario", action="append", default=[], help="只跑指定 name，可重复")
    parser.add_argument("--dimension", choices=DIMENSIONS, help="只跑指定维度")
    parser.add_argument("--no-retry", action="store_true", help="失败不重试，直接判 FAIL")
    parser.add_argument("--list", action="store_true", help="列出场景后退出（不触网）")
    return parser


def _filter_scenarios(
    scenarios: list[Scenario], names: list[str], dimension: str | None
) -> list[Scenario]:
    selected = scenarios
    if names:
        unknown = set(names) - {s.name for s in scenarios}
        if unknown:
            raise ValueError(f"未知场景: {', '.join(sorted(unknown))}")
        selected = [s for s in selected if s.name in names]
    if dimension:
        selected = [s for s in selected if s.dimension == dimension]
    return selected


def _manager_factory(llm_config: LLMConfig) -> ManagerFactory:
    def factory(data_dir: Path) -> MemoryManager:
        return MemoryManager(
            "memory-eval",
            MemoryConfig(
                distillation_window=_EVAL_DISTILLATION_WINDOW,
                context_max_claims=_EVAL_CONTEXT_MAX_CLAIMS,
            ),
            data_dir=data_dir,
            llm_config=llm_config,
            # Ledger attribution lands in the throwaway dir too — an eval
            # run must not write into the real ~/.tlon.
            tlon_paths=TlonPaths(home=data_dir),
        )

    return factory


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    try:
        scenarios = load_scenarios(args.dataset)
        selected = _filter_scenarios(scenarios, args.scenario, args.dimension)
    except ValueError as e:
        print(f"错误: {e}", file=sys.stderr)
        return 2

    if args.list:
        for scenario in selected:
            print(f"{scenario.dimension} / {scenario.name}（{len(scenario.sessions)} sessions）")
        return 0
    if not selected:
        print("错误: 过滤后没有可运行的场景", file=sys.stderr)
        return 2

    # Same env convention as the runtime: real env wins, ~/.tlon/.env fills
    # in the {PROVIDER}_API_KEY variables litellm reads natively.
    load_dotenv(TlonPaths().env_file(), override=False)

    if not args.embedding_model:
        print(
            "错误: 未指定 embedding 模型——JIT 检索无从评测。\n"
            "用 --embedding-model provider/model 指定（如 openai/text-embedding-3-small），"
            "或设 $TLON_EVAL_EMBEDDING_MODEL；对应 provider 的 API key 放在 ~/.tlon/.env。",
            file=sys.stderr,
        )
        return 2

    llm_config = LLMConfig(
        model_name=args.model,
        embedding_model_name=args.embedding_model,
        api_base=args.api_base,
    )

    _print_estimate(selected)
    results = asyncio.run(
        _run_all(selected, _manager_factory(llm_config), allow_retry=not args.no_retry)
    )
    _print_summary(results)
    return 0 if all(r.passed for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
