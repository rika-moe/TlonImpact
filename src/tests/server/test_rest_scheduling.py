"""Management scheduling uses the real facade and registry, preserving history."""

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tests.identity_helpers import make_identity
from tlon.agents.agent_registry import AgentRegistry
from tlon.agents.member_runtime import MemberRuntime
from tlon.api.members import MembersFacade
from tlon.base.config import AgentConfig, AgentInfoProvider, LLMConfig
from tlon.base.events.event_bus import EventBus
from tlon.base.paths import TlonPaths
from tlon.organization.mail.store import MailStore
from tlon_server.api.rest import RESTAPIHandler


class Configs(AgentInfoProvider):
    def __init__(self, config: AgentConfig) -> None:
        self.config = config

    def get_config(self, agent_id: str) -> AgentConfig | None:
        return self.config if agent_id == self.config.id else None

    def get_all_ids(self) -> set[str]:
        return {self.config.id}


async def test_admin_termination_waits_for_work_then_preserves_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = TlonPaths(home=tmp_path)
    behavior = paths.agent_behavior_file("worker")
    behavior.parent.mkdir(parents=True)
    behavior.write_text(
        '{:version 1 :rules [{:name "boot" :on :boot :actions []}]}', encoding="utf-8"
    )
    history = behavior.parent / "IDENTITY.md"
    history.write_text("历史身份", encoding="utf-8")
    bus = EventBus()
    await bus.initialize()
    registry = AgentRegistry(
        event_bus=bus,
        agent_info_provider=Configs(
            AgentConfig(id="worker", name="成员", llm=LLMConfig(model_name="test/model"))
        ),
        tlon_paths=paths,
    )
    await registry.initialize()
    try:
        await registry._start_agent_guarded("worker")
        work = {"count": 1}
        monkeypatch.setattr(
            MemberRuntime, "active_session_count", property(lambda self: work["count"])
        )
        monkeypatch.setattr(AgentRegistry, "_PENDING_STOP_POLL_SECONDS", 0.01)
        handler = RESTAPIHandler(
            None,
            identity=make_identity(),
            members=MembersFacade(agent_registry=registry, tlon_paths=paths),
        )
        request = MagicMock(match_info={"agent_id": "worker"})
        response = await handler.handle_terminate_agent(request)
        assert response.status == 202
        snapshot = json.loads(response.text)
        assert snapshot["agent"]["state"] == "active"
        assert snapshot["agent"]["stop_pending"] is True
        assert registry.get_agent("worker") is not None
        assert not MailStore(paths.mail_database_file()).is_retired("worker")
        work["count"] = 0
        async with asyncio.timeout(3):
            while (await registry.get_agent_status("worker"))["state"] != "dead":
                await asyncio.sleep(0.01)
        detail = json.loads((await handler.handle_get_agent(request)).text)
        assert detail["state"] == "dead"
        assert MailStore(paths.mail_database_file()).is_retired("worker")
        assert history.read_text(encoding="utf-8") == "历史身份"
    finally:
        await registry.shutdown()
        await bus.shutdown()
