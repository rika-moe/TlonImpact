"""EDN parser tests (``tlosure.edn``).

The parser turns EDN source text into plain Python data — no evaluation.
This suite pins the literal syntax (nil/bool/number/string/keyword/
symbol), the container types (vector -> list, list -> :class:`Form`,
map -> dict), the comment/whitespace rules, every parse error path with
its 1-based line/column position, and the ``dumps``/``loads`` roundtrip.
"""

from typing import Any

import pytest

from tlosure.edn import EDNParseError, Form, Keyword, Symbol, dumps, loads


class TestLiterals:
    def test_nil_and_booleans(self):
        assert loads("nil") is None
        assert loads("true") is True
        assert loads("false") is False

    def test_integers(self):
        assert loads("42") == 42
        assert loads("+42") == 42
        assert loads("-7") == -7
        assert type(loads("42")) is int

    def test_floats(self):
        assert loads("3.14") == 3.14
        assert loads("-0.5") == -0.5
        assert loads("+1.25") == 1.25
        assert type(loads("3.14")) is float

    def test_scientific_notation(self):
        assert loads("1e3") == 1000.0
        assert loads("1E3") == 1000.0
        assert loads("1.5e-2") == 0.015
        assert loads("2e+4") == 20000.0

    def test_bare_sign_is_a_symbol(self):
        # A sign not followed by a digit is a symbol, not a number.
        assert loads("+") == Symbol("+")
        assert isinstance(loads("+"), Symbol)
        assert loads("-") == Symbol("-")

    def test_trailing_dot_is_not_a_float(self):
        # "1." — a dot must be followed by a digit, so parsing stops at
        # the integer and the dot is reported as trailing content.
        with pytest.raises(EDNParseError, match="unexpected trailing content"):
            loads("1.")

    def test_exponent_without_digits_rejected(self):
        with pytest.raises(EDNParseError, match="invalid number: exponent has no digits"):
            loads("1e")
        with pytest.raises(EDNParseError, match="invalid number: exponent has no digits"):
            loads("1e+")

    def test_string(self):
        assert loads('"hello"') == "hello"
        assert loads('""') == ""

    def test_keyword(self):
        value = loads(":foo-bar")
        assert isinstance(value, Keyword)
        assert type(value) is Keyword
        assert value == "foo-bar"  # stored without the leading colon
        assert str(value) == "foo-bar"

    def test_symbol(self):
        value = loads("react")
        assert isinstance(value, Symbol)
        assert type(value) is Symbol
        assert value == "react"

    def test_keyword_symbol_string_stay_distinct(self):
        assert type(loads(":a")) is Keyword
        assert type(loads("a")) is Symbol
        assert type(loads('"a"')) is str


class TestStrings:
    def test_all_escapes(self):
        assert loads(r'"a\nb\tc\rd"') == "a\nb\tc\rd"
        assert loads(r'"quote: \" backslash: \\ "') == 'quote: " backslash: \\ '
        assert loads(r'"\b\f"') == "\b\f"

    def test_unicode_escape(self):
        assert loads(r'"\u0041\u00e9"') == "Aé"
        assert loads(r'"\u4e2d\u6587"') == "中文"

    def test_literal_unicode_passthrough(self):
        assert loads('"héllo 中文"') == "héllo 中文"


class TestCommentsAndWhitespace:
    def test_line_comment(self):
        assert loads("; leading comment\n42") == 42
        assert loads("[1 ; trailing comment\n 2]") == [1, 2]

    def test_comment_runs_to_end_of_input(self):
        assert loads("42 ; no newline at the end") == 42

    def test_comma_is_whitespace(self):
        assert loads("[1, 2,,3]") == [1, 2, 3]
        assert loads("{:a 1, :b 2}") == {Keyword("a"): 1, Keyword("b"): 2}


class TestContainers:
    def test_vector_parses_to_plain_list(self):
        value = loads("[1 2 3]")
        assert value == [1, 2, 3]
        assert type(value) is list
        assert not isinstance(value, Form)

    def test_list_parses_to_form(self):
        value = loads("(+ 1 2)")
        assert isinstance(value, Form)
        assert value == [Symbol("+"), 1, 2]

    def test_empty_containers(self):
        assert loads("[]") == []
        assert loads("()") == Form()
        assert loads("{}") == {}

    def test_map_parses_to_dict(self):
        assert loads("{:a 1 :b 2}") == {Keyword("a"): 1, Keyword("b"): 2}

    def test_nesting(self):
        value = loads("{:items [1 (f :x) {:nested true}]}")
        assert value == {
            Keyword("items"): [1, Form([Symbol("f"), Keyword("x")]), {Keyword("nested"): True}]
        }


class TestErrors:
    def test_empty_input(self):
        with pytest.raises(EDNParseError, match="unexpected end of input"):
            loads("")
        with pytest.raises(EDNParseError, match="unexpected end of input"):
            loads("   \n ")

    def test_unterminated_containers(self):
        with pytest.raises(EDNParseError, match="unterminated vector"):
            loads("[1 2")
        with pytest.raises(EDNParseError, match="unterminated list"):
            loads("(1 2")
        with pytest.raises(EDNParseError, match="unterminated map"):
            loads("{:a 1")
        with pytest.raises(EDNParseError, match="unterminated string"):
            loads('"abc')

    def test_unterminated_string_escape(self):
        with pytest.raises(EDNParseError, match="unterminated string escape"):
            loads('"\\')

    def test_unmatched_delimiter(self):
        for source in ("]", ")", "}"):
            with pytest.raises(EDNParseError, match="unmatched delimiter"):
                loads(source)

    def test_map_odd_number_of_forms(self):
        with pytest.raises(EDNParseError, match="map literal must contain an even number of forms"):
            loads("{:a 1 :b}")

    def test_trailing_content(self):
        with pytest.raises(EDNParseError, match="unexpected trailing content"):
            loads("1 2")

    def test_invalid_escape_sequence(self):
        with pytest.raises(EDNParseError, match=r"invalid escape sequence: \\q"):
            loads(r'"\q"')

    def test_invalid_unicode_escape(self):
        with pytest.raises(EDNParseError, match="invalid unicode escape"):
            loads(r'"\u12"')  # fewer than 4 hex digits
        with pytest.raises(EDNParseError, match="invalid unicode escape"):
            loads(r'"\uZZZZ"')  # non-hex digits

    def test_keyword_without_name(self):
        with pytest.raises(EDNParseError, match="keyword must have a name"):
            loads(": 1")

    def test_unexpected_character(self):
        with pytest.raises(EDNParseError, match="unexpected character: '@'"):
            loads("@")

    def test_error_is_a_value_error(self):
        with pytest.raises(ValueError):
            loads("]")

    def test_error_carries_line_and_column(self):
        with pytest.raises(EDNParseError) as exc_info:
            loads("[1\n  2")
        err = exc_info.value
        assert err.line == 2
        assert err.column == 4
        assert "line 2, column 4" in str(err)

    def test_positions_are_one_based(self):
        with pytest.raises(EDNParseError) as exc_info:
            loads("]")
        assert (exc_info.value.line, exc_info.value.column) == (1, 1)
        with pytest.raises(EDNParseError) as exc_info:
            loads("1 2")
        assert (exc_info.value.line, exc_info.value.column) == (1, 3)

    def test_multiline_error_position(self):
        with pytest.raises(EDNParseError) as exc_info:
            loads("{\n:a 1\n:b}")
        assert (exc_info.value.line, exc_info.value.column) == (3, 4)


class TestDumps:
    def test_scalars(self):
        assert dumps(None) == "nil"
        assert dumps(True) == "true"
        assert dumps(False) == "false"
        assert dumps(42) == "42"
        assert dumps(-3.5) == "-3.5"

    def test_string_escaping(self):
        assert dumps('a"b\\c') == r'"a\"b\\c"'
        assert dumps("a\nb\tc\rd") == r'"a\nb\tc\rd"'

    def test_keyword_and_symbol(self):
        assert dumps(Keyword("foo")) == ":foo"
        assert dumps(Symbol("react")) == "react"

    def test_containers(self):
        assert dumps([1, 2]) == "[1 2]"
        assert dumps((1, 2)) == "[1 2]"  # tuples dump as vectors
        assert dumps(Form([Symbol("+"), 1])) == "(+ 1)"
        assert dumps({Keyword("a"): 1}) == "{:a 1}"

    def test_unsupported_type_rejected(self):
        with pytest.raises(TypeError, match="cannot dump set as EDN"):
            dumps({1, 2})
        with pytest.raises(TypeError, match="cannot dump object as EDN"):
            dumps(object())


class TestRoundtrip:
    """``loads(dumps(x)) == x`` across the supported value domain.

    ``dumps`` escapes only ``\" \\ \n \t \r`` — strings containing other
    control characters (e.g. ``\b``/``\f``) do not roundtrip, so the
    fixtures below avoid them.
    """

    @pytest.mark.parametrize(
        "value",
        [
            None,
            True,
            False,
            42,
            -7,
            3.14,
            0.015,
            "plain",
            'escapes: "quoted" \\ \n\t\r',
            Keyword("foo-bar"),
            Symbol("react"),
            Form([Symbol("+"), 1, 2]),
            [],
            [1, "two", Keyword("three")],
            (1, 2),  # roundtrips to a plain vector
            {},
            {Keyword("a"): 1, Keyword("b"): [True, None]},
            {"str-key": {Keyword("nested"): Form()}},
        ],
    )
    def test_roundtrip(self, value: Any):
        reparsed = loads(dumps(value))
        if isinstance(value, tuple):
            assert reparsed == list(value)
        else:
            assert reparsed == value
        # Types survive the trip for the tag-carrying values.
        assert type(reparsed) is type(value) or isinstance(value, tuple)


class TestDeepNesting:
    def test_deeply_nested_vectors_parse(self):
        depth = 150
        value = loads("[" * depth + "1" + "]" * depth)
        for _ in range(depth):
            assert isinstance(value, list)
            assert len(value) == 1
            value = value[0]
        assert value == 1

    def test_deeply_nested_forms_parse(self):
        depth = 150
        value = loads("(" * depth + ")" * depth)
        assert isinstance(value, Form)
        inner: Any = value
        while inner:
            inner = inner[0]
            assert isinstance(inner, Form)
        assert inner == []
