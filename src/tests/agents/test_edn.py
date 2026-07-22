"""Tests for the EDN data literal parser (tlon.agents.edn)."""

import pytest

from tlon.agents.edn import EDNParseError, Keyword, Symbol, dumps, loads


class TestLiterals:
    def test_nil_true_false(self):
        assert loads("nil") is None
        assert loads("true") is True
        assert loads("false") is False

    def test_integers(self):
        assert loads("42") == 42
        assert loads("-7") == -7
        assert loads("+3") == 3
        assert isinstance(loads("42"), int)

    def test_floats(self):
        assert loads("3.14") == 3.14
        assert loads("-0.5") == -0.5
        assert loads("1e3") == 1000.0
        assert loads("1.5e-2") == 0.015
        assert isinstance(loads("1.0"), float)

    def test_strings(self):
        assert loads('"hello"') == "hello"
        assert loads('""') == ""

    def test_string_escapes(self):
        assert loads(r'"a\"b"') == 'a"b'
        assert loads(r'"a\\b"') == "a\\b"
        assert loads(r'"a\nb\tc\rd"') == "a\nb\tc\rd"
        assert loads(r'"\u0041"') == "A"

    def test_whitespace_and_commas(self):
        assert loads("  , 42 ,\n") == 42

    def test_comments(self):
        assert loads("; leading comment\n42 ; trailing comment") == 42


class TestKeywordSymbolStringDistinction:
    def test_keyword_type(self):
        value = loads(":foo-bar")
        assert isinstance(value, Keyword)
        assert isinstance(value, str)
        assert value == "foo-bar"

    def test_symbol_type(self):
        value = loads("react")
        assert isinstance(value, Symbol)
        assert isinstance(value, str)
        assert value == "react"

    def test_keyword_symbol_string_are_distinct(self):
        keyword = loads(":react")
        symbol = loads("react")
        string = loads('"react"')
        assert type(keyword) is Keyword
        assert type(symbol) is Symbol
        assert type(string) is str
        # :mode :react and :mode "react" stay distinguishable
        assert isinstance(loads("{:mode :react}")[Keyword("mode")], Keyword)
        assert type(loads('{:mode "react"}')[Keyword("mode")]) is str

    def test_symbol_like_builtins_are_not_symbols(self):
        assert loads("nil") is None
        assert not isinstance(loads("true"), Symbol)


class TestCollections:
    def test_vector(self):
        assert loads("[1 2 3]") == [1, 2, 3]
        assert loads("[]") == []

    def test_map(self):
        result = loads("{:a 1 :b 2}")
        assert result == {Keyword("a"): 1, Keyword("b"): 2}

    def test_nested(self):
        result = loads('{:trigger {:on-mention true} :tags [:a "b" c]}')
        assert result == {
            Keyword("trigger"): {Keyword("on-mention"): True},
            Keyword("tags"): [Keyword("a"), "b", Symbol("c")],
        }

    def test_commas_separate_map_entries(self):
        assert loads("{:a 1, :b 2}") == {Keyword("a"): 1, Keyword("b"): 2}

    def test_duplicate_keys_last_wins(self):
        assert loads("{:a 1 :a 2}") == {Keyword("a"): 2}


class TestParseErrors:
    def test_unterminated_string(self):
        with pytest.raises(EDNParseError) as exc_info:
            loads('"abc')
        assert "unterminated string" in str(exc_info.value)

    def test_unterminated_vector(self):
        with pytest.raises(EDNParseError) as exc_info:
            loads("[1 2")
        assert "unterminated vector" in str(exc_info.value)

    def test_unterminated_map(self):
        with pytest.raises(EDNParseError) as exc_info:
            loads("{:a 1")
        assert "unterminated map" in str(exc_info.value)

    def test_odd_map_forms(self):
        with pytest.raises(EDNParseError) as exc_info:
            loads("{:a 1 :b}")
        assert "even number" in str(exc_info.value)

    def test_unmatched_delimiter(self):
        with pytest.raises(EDNParseError):
            loads("]")

    def test_trailing_content(self):
        with pytest.raises(EDNParseError) as exc_info:
            loads("1 2")
        assert "trailing" in str(exc_info.value)

    def test_invalid_escape(self):
        with pytest.raises(EDNParseError) as exc_info:
            loads(r'"a\qb"')
        assert "invalid escape" in str(exc_info.value)

    def test_lists_not_supported_in_l0(self):
        with pytest.raises(EDNParseError) as exc_info:
            loads("(foo bar)")
        assert "not supported" in str(exc_info.value)

    def test_empty_input(self):
        with pytest.raises(EDNParseError):
            loads("   ")

    def test_error_has_position(self):
        try:
            loads('{:a 1\n  :b "unterminated')
        except EDNParseError as e:
            assert e.line == 2
            assert e.column > 1
            assert f"line {e.line}" in str(e)
            assert f"column {e.column}" in str(e)
        else:
            pytest.fail("expected EDNParseError")


class TestDumps:
    def test_scalars(self):
        assert dumps(None) == "nil"
        assert dumps(True) == "true"
        assert dumps(False) == "false"
        assert dumps(42) == "42"
        assert dumps(3.14) == "3.14"
        assert dumps(Keyword("mode")) == ":mode"
        assert dumps(Symbol("react")) == "react"
        assert dumps("hi") == '"hi"'

    def test_string_escaping(self):
        assert dumps('a"b\nc') == '"a\\"b\\nc"'

    def test_collections(self):
        assert dumps([1, 2]) == "[1 2]"
        assert dumps({Keyword("a"): 1}) == "{:a 1}"

    def test_roundtrip(self):
        source = (
            '{:version 1, :trigger {:on-mention true, :proactive false},'
            ' :loop {:mode :react, :max-iterations 10, :ratio 1.5},'
            ' :names ["x" y], :nothing nil}'
        )
        value = loads(source)
        assert loads(dumps(value)) == value

    def test_roundtrip_preserves_keyword_and_symbol_types(self):
        value = loads("{:mode :react :op spawn}")
        reparsed = loads(dumps(value))
        assert isinstance(reparsed[Keyword("mode")], Keyword)
        assert isinstance(reparsed[Keyword("op")], Symbol)

    def test_unsupported_type(self):
        with pytest.raises(TypeError):
            dumps(object())
