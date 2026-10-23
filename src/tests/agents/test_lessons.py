"""LessonsDistiller — retirement lessons distillation (docs/design/memory.md).

Pins the closed loop: publishing AGENT_STOPPED for a member
distills its archived memory into ``memory/lessons.md`` via one LLM call
attributed to the retired member; empty digests
are skipped; an LLM failure is logged, never raised (retirement
bookkeeping must not break the bus).
"""

import asyncio
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from tests.program_helpers import write_program
from tlon.agents.lessons import LessonsDistiller
from tlon.base.events.event import Event, EventType
from tlon.base.events.event_bus import EventBus
from tlon.base.llm_client import LLMResponse
from tlon.base.paths import TlonPaths
from tlon.memory.manager import MemoryManager
from tlon.memory.models import MemoryConfig

_SESSION_PROGRAM = "{:version 2 :handlers {:mail-delivered work/handle}}"
_RELAY_PROGRAM = "{:version 2 :handlers {:boot work/handle}}"


class _FakeLLM:
    """Duck-typed LLMClient returning one canned response."""

    def __init__(self, content: str) -> None:
        self._content = content
        self.calls: list[dict[str, Any]] = []

    async def chat(self, **kwargs: Any) -> LLMResponse:
        self.calls.append(kwargs)
        return LLMResponse(content=self._content)


class _FailingLLM:
    """Duck-typed LLMClient whose chat always raises."""

    async def chat(self, **kwargs: Any) -> LLMResponse:
        raise RuntimeError("provider down")


def _write_behavior(p: TlonPaths, agent_id: str, spec: str) -> None:
    write_program(p.agent_behavior_file(agent_id), spec)


async def _remember(p: TlonPaths, agent_id: str, fact: str) -> None:
    manager = MemoryManager(agent_id, MemoryConfig(), data_dir=p.agent_dir(agent_id))
    await manager.remember_fact(fact)


def _stopped_event(agent_id: str) -> Event:
    return Event(
        type=EventType.AGENT_STOPPED,
        payload={"agent_id": agent_id, "stopped_by": "adam"},
        source="agent_registry",
    )


async def _wait_for(predicate: Callable[[], bool], timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return False


@pytest.mark.parametrize("program", [_SESSION_PROGRAM, _RELAY_PROGRAM])
async def test_agent_stopped_distills_lessons(tmp_path: Path, program: str) -> None:
    """Full bus path: AGENT_STOPPED -> offline digest -> LLM -> lessons.md."""
    p = TlonPaths(home=tmp_path)
    _write_behavior(p, "retiree", program)
    await _remember(p, "retiree", "部署前先跑质量门")

    bus = EventBus()
    await bus.initialize()
    llm = _FakeLLM("- 部署前先跑质量门\n")
    seen_by_factory: list[str] = []

    def factory(agent_id: str) -> Any:
        seen_by_factory.append(agent_id)
        return llm

    distiller = LessonsDistiller(
        event_bus=bus,
        tlon_paths=p,
        llm_client_factory=factory,
    )
    await distiller.initialize()
    try:
        await bus.publish(_stopped_event("retiree"))

        lessons_file = p.memory_lessons_file("retiree")
        assert await _wait_for(lessons_file.exists)
        assert lessons_file.read_text(encoding="utf-8") == "- 部署前先跑质量门\n"
        # Attribution: the call is made in the retired member's name.
        assert seen_by_factory == ["retiree"]
        assert llm.calls[0]["purpose"] == "lessons"
        assert "temperature" not in llm.calls[0]
        assert llm.calls[0]["timeout"] == 60.0
    finally:
        await distiller.shutdown()
        await bus.shutdown()


async def test_empty_relay_memory_is_skipped(tmp_path: Path) -> None:
    p = TlonPaths(home=tmp_path)
    _write_behavior(p, "relay-1", _RELAY_PROGRAM)
    llm = _FakeLLM("- 不该被调用\n")
    distiller = LessonsDistiller(
        event_bus=EventBus(), tlon_paths=p, llm_client_factory=lambda _id: llm
    )

    await distiller._on_agent_stopped(_stopped_event("relay-1"))

    assert llm.calls == []
    assert not (tmp_path / "relay-1" / "memory" / "lessons.md").exists()


async def test_empty_digest_skipped(tmp_path: Path) -> None:
    p = TlonPaths(home=tmp_path)
    _write_behavior(p, "fresh", _SESSION_PROGRAM)
    llm = _FakeLLM("- 不该被调用\n")
    distiller = LessonsDistiller(
        event_bus=EventBus(), tlon_paths=p, llm_client_factory=lambda _id: llm
    )

    await distiller._on_agent_stopped(_stopped_event("fresh"))

    assert llm.calls == []
    assert not (tmp_path / "fresh" / "memory" / "lessons.md").exists()


async def test_missing_behavior_file_skipped(tmp_path: Path) -> None:
    p = TlonPaths(home=tmp_path)
    llm = _FakeLLM("- 不该被调用\n")
    distiller = LessonsDistiller(
        event_bus=EventBus(), tlon_paths=p, llm_client_factory=lambda _id: llm
    )

    await distiller._on_agent_stopped(_stopped_event("ghost"))

    assert llm.calls == []


async def test_llm_failure_is_logged_never_raised(tmp_path: Path) -> None:
    p = TlonPaths(home=tmp_path)
    _write_behavior(p, "retiree", _SESSION_PROGRAM)
    await _remember(p, "retiree", "一条事实")
    distiller = LessonsDistiller(
        event_bus=EventBus(), tlon_paths=p, llm_client_factory=lambda _id: _FailingLLM()
    )

    await distiller._on_agent_stopped(_stopped_event("retiree"))  # must not raise

    assert not (tmp_path / "retiree" / "memory" / "lessons.md").exists()


async def test_removed_member_does_not_recreate_archive(tmp_path: Path) -> None:
    paths = TlonPaths(home=tmp_path)
    llm = _FakeLLM("不得调用")
    distiller = LessonsDistiller(
        event_bus=EventBus(), tlon_paths=paths, llm_client_factory=lambda _: llm
    )
    event = _stopped_event("removed")
    event.payload["removed"] = True
    await distiller._on_agent_stopped(event)
    assert not paths.agent_dir("removed").exists()
    assert llm.calls == []
