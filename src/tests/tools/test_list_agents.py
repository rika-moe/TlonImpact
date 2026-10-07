"""Tests for the listAgents yellow page (tools/agents/list_agents.py, ADR 0024 决策 3)."""

from typing import Any

from tlon.tools.agents.list_agents import ListAgentsTool


class _RegistryStub:
    """Serves the registry's full status dicts (management surface shape) —
    the tool must project them down to the yellow page."""

    def get_all_agent_status(self) -> list[dict[str, Any]]:
        return [
            {
                "id": "b-002",
                "name": "乙",
                "state": "sleeping",
                "prototype": "developer",
                "spawned_by": "a-001",
                "spawn_depth": 1,
            },
            {
                "id": "a-001",
                "name": "甲",
                "state": "running",
                "prototype": None,
                "spawned_by": None,
                "spawn_depth": 0,
            },
        ]


class TestListAgentsYellowPage:
    async def test_output_is_exactly_id_name_address(self):
        """ADR 0024 决策 3: the member-facing roster is an addressing
        yellow page — identity content (prototype/lineage) and runtime
        state never leak into the tool output."""
        tool = ListAgentsTool(registry=_RegistryStub())

        result = await tool.execute()

        assert result.success is True
        assert result.data == [
            {"id": "a-001", "name": "甲", "address": "a-001@tlon.internal"},
            {"id": "b-002", "name": "乙", "address": "b-002@tlon.internal"},
        ]
        for entry in result.data:
            assert set(entry) == {"id", "name", "address"}

    async def test_sorted_by_id(self):
        tool = ListAgentsTool(registry=_RegistryStub())

        result = await tool.execute()

        assert [entry["id"] for entry in result.data] == ["a-001", "b-002"]
