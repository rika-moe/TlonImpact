"""Tool result envelope serialization."""

from tlon.tools.base import ToolResult


class TestToolResult:
    def test_defaults(self) -> None:
        result = ToolResult(success=True, data={"x": 1})

        assert result.error == ""
        assert result.message == ""

    def test_to_dict(self) -> None:
        result = ToolResult(success=False, data={"x": 1}, error="boom", message="失败")

        assert result.to_dict() == {
            "success": False,
            "data": {"x": 1},
            "error": "boom",
            "message": "失败",
        }

    def test_from_dict_roundtrip(self) -> None:
        result = ToolResult(success=True, data=[1, 2], error="", message="ok")

        assert ToolResult.from_dict(result.to_dict()) == result

    def test_from_dict_fills_defaults(self) -> None:
        result = ToolResult.from_dict({"success": True})

        assert result.success is True
        assert result.data is None
        assert result.error == ""
        assert result.message == ""

    def test_from_dict_coerces_success_to_bool(self) -> None:
        result = ToolResult.from_dict({"success": 1, "data": None})

        assert result.success is True
