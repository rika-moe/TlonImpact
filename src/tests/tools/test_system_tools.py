"""Tests for system tools (GetCurrentTime)."""

from datetime import datetime

from tlon.tools.system.current_time import GetCurrentTimeTool


class TestGetCurrentTimeTool:
    async def test_returns_iso_time_with_timezone(self):
        tool = GetCurrentTimeTool()

        result = await tool.execute()

        assert result.success is True
        parsed = datetime.fromisoformat(result.data["iso"])
        assert parsed.tzinfo is not None
        assert parsed.utcoffset() is not None
        assert result.data["unix"] > 0
        assert result.data["timezone"]
        assert result.data["weekday"]

    async def test_unix_matches_iso(self):
        tool = GetCurrentTimeTool()

        result = await tool.execute()

        parsed = datetime.fromisoformat(result.data["iso"])
        assert int(parsed.timestamp()) == result.data["unix"]

    def test_no_parameters(self):
        tool = GetCurrentTimeTool()
        assert tool.parameters["properties"] == {}
        assert tool.validate_arguments({}) == []
