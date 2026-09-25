"""Tests for the Tool base layer (tlon.tools.base)."""

from typing import Any

import pytest

from tlon.tools.base import Tool, ToolResult


class DummyTool(Tool):
    """Concrete tool with one parameter per supported schema type."""

    def __init__(self) -> None:
        super().__init__(
            name="dummy",
            description="A tool for testing the base layer",
            parameters={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "标题"},
                    "count": {
                        "type": "integer",
                        "description": "次数",
                        "minimum": 1,
                        "maximum": 10,
                    },
                    "ratio": {"type": "number", "description": "比例", "minimum": 0.0},
                    "flag": {"type": "boolean", "description": "开关"},
                    "items": {"type": "array", "description": "列表"},
                    "config": {"type": "object", "description": "配置"},
                },
                "required": ["title"],
            },
        )

    async def execute(self, **kwargs: Any) -> ToolResult:
        return ToolResult(success=True, data=kwargs)


class TestToolResult:
    def test_defaults(self):
        result = ToolResult(success=True, data={"x": 1})

        assert result.error == ""
        assert result.message == ""

    def test_to_dict(self):
        result = ToolResult(success=False, data={"x": 1}, error="boom", message="失败")

        assert result.to_dict() == {
            "success": False,
            "data": {"x": 1},
            "error": "boom",
            "message": "失败",
        }

    def test_from_dict_roundtrip(self):
        result = ToolResult(success=True, data=[1, 2], error="", message="ok")

        assert ToolResult.from_dict(result.to_dict()) == result

    def test_from_dict_fills_defaults(self):
        result = ToolResult.from_dict({"success": True})

        assert result.success is True
        assert result.data is None
        assert result.error == ""
        assert result.message == ""

    def test_from_dict_coerces_success_to_bool(self):
        result = ToolResult.from_dict({"success": 1, "data": None})

        assert result.success is True


class TestOpenAIDefinition:
    def test_shape(self):
        definition = DummyTool().to_openai_definition()

        assert definition["type"] == "function"
        assert definition["function"]["name"] == "dummy"
        assert definition["function"]["description"] == "A tool for testing the base layer"
        assert definition["function"]["parameters"]["required"] == ["title"]

    def test_parameters_passed_by_reference(self):
        tool = DummyTool()

        assert tool.to_openai_definition()["function"]["parameters"] is tool.parameters


class TestGetParameterDescription:
    def test_marks_required_and_types(self):
        description = DummyTool().get_parameter_description()

        assert "- title (string) (required): 标题" in description
        assert "- count (integer): 次数" in description
        assert "- flag (boolean): 开关" in description

    def test_no_parameters(self):
        class EmptyTool(Tool):
            def __init__(self) -> None:
                super().__init__(name="empty", description="d", parameters={"type": "object"})

            async def execute(self, **kwargs: Any) -> ToolResult:
                return ToolResult(success=True, data={})

        assert EmptyTool().get_parameter_description() == "  (no parameters)"


class TestValidateArguments:
    def test_valid_arguments(self):
        errors = DummyTool().validate_arguments(
            {
                "title": "t",
                "count": 5,
                "ratio": 0.5,
                "flag": False,
                "items": [1],
                "config": {"k": "v"},
            }
        )

        assert errors == []

    def test_optional_parameters_may_be_omitted(self):
        assert DummyTool().validate_arguments({"title": "t"}) == []

    def test_missing_required(self):
        assert DummyTool().validate_arguments({}) == ["Missing required parameter: 'title'"]

    def test_none_counts_as_missing_required(self):
        assert DummyTool().validate_arguments({"title": None}) == [
            "Missing required parameter: 'title'"
        ]

    def test_unknown_parameter(self):
        assert DummyTool().validate_arguments({"title": "t", "nope": 1}) == [
            "Unknown parameter: 'nope'"
        ]

    def test_unknown_parameter_skips_type_check(self):
        # An unknown field reports only the unknown-parameter error, even if
        # the value would also fail a type check.
        errors = DummyTool().validate_arguments({"title": "t", "nope": object()})

        assert errors == ["Unknown parameter: 'nope'"]

    @pytest.mark.parametrize(
        ("field", "value", "actual"),
        [
            ("title", 1, "int"),
            ("count", "1", "str"),
            ("count", 1.5, "float"),
            ("ratio", "0.5", "str"),
            ("flag", 1, "int"),
            ("flag", "true", "str"),
            ("items", {"a": 1}, "dict"),
            ("config", [1], "list"),
        ],
    )
    def test_type_mismatch(self, field: str, value: Any, actual: str):
        expected = DummyTool().parameters["properties"][field]["type"]

        errors = DummyTool().validate_arguments({"title": "t", field: value})

        assert errors == [f"Parameter '{field}': expected {expected}, got {actual}"]

    def test_number_accepts_int_and_float(self):
        tool = DummyTool()

        assert tool.validate_arguments({"title": "t", "ratio": 3}) == []
        assert tool.validate_arguments({"title": "t", "ratio": 0.5}) == []

    def test_bool_is_accepted_as_integer_and_number(self):
        # bool is a subclass of int, so True passes the isinstance check for
        # both "integer" and "number" schemas. This pins down the current
        # (loose) behavior.
        tool = DummyTool()

        assert tool.validate_arguments({"title": "t", "count": True}) == []
        assert tool.validate_arguments({"title": "t", "ratio": True}) == []

    def test_optional_none_value_skips_type_check(self):
        assert DummyTool().validate_arguments({"title": "t", "count": None}) == []

    def test_below_minimum(self):
        assert DummyTool().validate_arguments({"title": "t", "count": 0}) == [
            "Parameter 'count': value 0 is below the schema minimum 1"
        ]

    def test_above_maximum(self):
        assert DummyTool().validate_arguments({"title": "t", "count": 11}) == [
            "Parameter 'count': value 11 exceeds the schema maximum 10"
        ]

    def test_bounds_are_inclusive(self):
        tool = DummyTool()

        assert tool.validate_arguments({"title": "t", "count": 1}) == []
        assert tool.validate_arguments({"title": "t", "count": 10}) == []

    def test_number_minimum_applies(self):
        assert DummyTool().validate_arguments({"title": "t", "ratio": -0.1}) == [
            "Parameter 'ratio': value -0.1 is below the schema minimum 0.0"
        ]

    def test_multiple_errors_collected(self):
        errors = DummyTool().validate_arguments({"count": 0, "nope": 1})

        assert errors == [
            "Missing required parameter: 'title'",
            "Parameter 'count': value 0 is below the schema minimum 1",
            "Unknown parameter: 'nope'",
        ]

    def test_tool_without_required_or_properties_accepts_anything(self):
        class OpenTool(Tool):
            def __init__(self) -> None:
                super().__init__(name="open", description="d", parameters={})

            async def execute(self, **kwargs: Any) -> ToolResult:
                return ToolResult(success=True, data={})

        # Fields still unknown because there are no declared properties.
        assert OpenTool().validate_arguments({"anything": 1}) == ["Unknown parameter: 'anything'"]
        assert OpenTool().validate_arguments({}) == []


class TestLifecycleHooks:
    async def test_initialize_and_shutdown_are_noops_by_default(self):
        tool = DummyTool()

        assert await tool.initialize() is None
        assert await tool.shutdown() is None
