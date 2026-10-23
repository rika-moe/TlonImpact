"""Retirement lessons distillation (docs/design/memory.md 「退休蒸馏」).

When a member is deliberately stopped (``AGENT_STOPPED`` — the self-exit
or administrative path through ``AgentRegistry.stop_member``, never
the everyday waiting-instance unload), its experience
should not die with it: this component rebuilds the retired member's
archived memory **offline** (the member runtime is already shut down, so
there is no in-flight reflection to race — the reason this is not a
pre-shutdown hook), runs one LLM pass distilling the memory digest into
lessons for the next generation, and writes
``~/.tlon/{agent_id}/memory/lessons.md``.

The distillation call is attributed to the retired member itself
(``agent_id`` on the LLM client — its last ledger entry, ADR 0014). The
lessons file is raw material: packaging it into a prototype's
``LESSONS.md`` (the inheritance channel) is a separate, human/agent
decision via ``prototype-write`` / the admin API.

Skip conditions (retirement bookkeeping must never block or fail the
stop flow — the stop is already done, this is post-hoc):
- empty memory digest (nothing to distill)
- LLM failure — logged, never raised
"""

import asyncio
import hashlib
import json
import os
from collections.abc import Callable
from typing import Any

import aiofiles

from tlon.agents.assembly import _build_llm_config
from tlon.agents.program_store import ProgramStore
from tlon.agents.session_journal import SessionJournal
from tlon.base.component import Component
from tlon.base.config import yaml_load_file
from tlon.base.device import Device
from tlon.base.events.event import Event, EventType
from tlon.base.events.event_bus import EventBus
from tlon.base.execution import ExecutionScope, bind_execution
from tlon.base.llm_client import LLMClient
from tlon.base.logger import get_logger
from tlon.base.paths import TlonPaths
from tlon.memory.manager import MemoryManager
from tlon.memory.models import MemoryConfig

logger = get_logger(__name__)

LESSONS_SYSTEM_PROMPT = (
    "你是一个经验蒸馏助手。一个 Agent 成员刚刚退休（永久停止运行）。"
    "你的任务是从它的持久记忆中提炼「能传给下一代成员的教训」——"
    "这不是全量记忆的复述，而是经验蒸馏：哪些做法有效、哪些失败、什么该避免。\n\n"
    "原则：\n"
    "1. 只提炼可复用的经验教训，不复述事实清单\n"
    "2. 每条教训一句话，说清情境与结论\n"
    "3. 没有值得传承的经验时，返回空列表\n\n"
    "请用 Markdown 无序列表返回，每条一行。"
)


def build_retiree_llm_client(agent_id: str, tlon_paths: TlonPaths) -> LLMClient:
    """Build the LLM client for one final attributed call in the retired
    member's name (ADR 0014: its ledger gets this last entry).

    The config comes from the member's archived ``agent.yaml`` ``llm``
    section, same as ``assemble_member_services`` (member-level only, ADR 0016).
    """
    agent = yaml_load_file(tlon_paths.agent_config_file(agent_id))
    llm_config = _build_llm_config(agent.llm if agent is not None else None, agent_id)
    return LLMClient(llm_config, agent_id=agent_id, tlon_paths=tlon_paths)


class LessonsDistiller(Component, Device):
    """Distills a retired member's memory into ``memory/lessons.md``.

    A pure event consumer in the MetricsService mold: subscribes to
    ``AGENT_STOPPED`` in ``initialize()``, unsubscribes in ``shutdown()``.
    The bus awaits async handlers in the subscription's own consumer
    task, so a slow distillation never blocks the stop flow.

    Dependencies (constructor injected):
        - event_bus: EventBus to subscribe to
        - tlon_paths: path resolver (tests inject an isolated home)
        - llm_client_factory: ``agent_id -> LLMClient``; the default
          rebuilds the retired member's config from its archived
          ``agent.yaml`` (tests inject a canned-response fake)
    """

    def __init__(
        self,
        event_bus: EventBus,
        tlon_paths: TlonPaths,
        llm_client_factory: Callable[[str], LLMClient] | None = None,
    ):
        Component.__init__(self, name="LessonsDistiller")
        Device.__init__(self, event_bus=event_bus, name="LessonsDistiller")
        self._paths = tlon_paths
        self._client_factory = llm_client_factory or (
            lambda agent_id: build_retiree_llm_client(agent_id, tlon_paths)
        )

    async def initialize(self) -> None:
        """Start the distiller by subscribing to AGENT_STOPPED."""
        self.subscribe(EventType.AGENT_STOPPED, self._on_agent_stopped)
        self._initialized = True
        logger.info("lessons_distiller_initialized")

    async def shutdown(self) -> None:
        """Stop the distiller by unsubscribing from the bus."""
        self.unsubscribe_all()
        self._initialized = False
        logger.info("lessons_distiller_shutdown")

    async def _on_agent_stopped(self, event: Event) -> None:
        """Distill one stopped member; failures are logged, never raised."""
        agent_id = ""
        if isinstance(event.payload, dict):
            if event.payload.get("removed"):
                return
            agent_id = str(event.payload.get("agent_id") or "")
        if not agent_id:
            logger.warning("lessons_distill_no_agent_id")
            return
        try:
            await self._distill(agent_id)
        except Exception as e:
            logger.warning("lessons_distill_failed", agent_id=agent_id, error=str(e))

    async def _distill(self, agent_id: str) -> None:
        """Rebuild the archive offline and run the one distillation call."""
        if not self._paths.agent_dir(agent_id).exists():
            return
        memory = MemoryManager(
            agent_id,
            MemoryConfig(),
            data_dir=self._paths.agent_dir(agent_id),
            tlon_paths=self._paths,
        )
        journal = SessionJournal(agent_id, self._paths)
        digest = await memory.get_memory_digest(journal)
        if digest["claim_count"] == 0 and not digest["recent_interactions"]:
            logger.info("lessons_distill_skipped", agent_id=agent_id, reason="empty_digest")
            return

        client = self._client_factory(agent_id)
        # A duplicate stop reads the same model receipt instead of paying for
        # another final audit. The exact digest binds the maintenance identity.
        source = json.dumps(digest, sort_keys=True, ensure_ascii=False)
        batch = hashlib.sha256(source.encode()).hexdigest()
        store = ProgramStore(self._paths.agent_program_database_file(agent_id))
        config = yaml_load_file(self._paths.agent_config_file(agent_id))
        limit = config.llm.max_tokens if config is not None and config.llm is not None else 4096
        scope = ExecutionScope(store, f"maintenance:lessons:{batch}", limit, "output")
        with bind_execution(scope):
            response = await client.chat(
                system_prompt=LESSONS_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": self._build_prompt(digest)}],
                timeout=60.0,
                purpose="lessons",
            )
        if not response.content:
            logger.info("lessons_distill_skipped", agent_id=agent_id, reason="empty_response")
            return

        lessons_file = self._paths.memory_lessons_file(agent_id)
        lessons_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = lessons_file.with_suffix(".md.tmp")
        async with aiofiles.open(temporary, "w", encoding="utf-8") as f:
            await f.write(response.content)
            await f.flush()
            await asyncio.to_thread(os.fsync, f.fileno())
        os.replace(temporary, lessons_file)
        logger.info("lessons_distilled", agent_id=agent_id, path=str(lessons_file))

    @staticmethod
    def _build_prompt(digest: dict[str, Any]) -> str:
        """Render the memory digest as the distillation input."""
        parts = ["## 退休成员的记忆\n"]
        parts.append(f"记忆数：{digest['claim_count']}")
        parts.append("")

        if digest["claims"]:
            parts.append("### 记忆")
            for c in digest["claims"]:
                cid = str(c.get("id", ""))[:8]
                date = str(c.get("timestamp", ""))[:10]
                parts.append(f"- [{cid}] [{date}] {c.get('text', '')}")
            parts.append("")

        parts.append("")
        interactions = digest.get("recent_interactions", [])
        if interactions:
            parts.append("### 最近交互（来源为只读运行记录）")
            parts.append(json.dumps(interactions, ensure_ascii=False))
        parts.append("请从以上记忆和交互中提炼能传给下一代成员的教训。")
        return "\n".join(parts)
