"""Content caching must preserve parser and member isolation semantics."""

from collections.abc import Iterator
from unittest.mock import patch

import pytest

from tlosure import edn
from tlosure.builtins import Builtin
from tlosure.modules import ModuleError, ModulePackage


@pytest.fixture(autouse=True)
def isolated_parse_cache() -> Iterator[None]:
    edn._cached_parse_all.cache_clear()
    yield
    edn._cached_parse_all.cache_clear()


def test_repeated_source_is_parsed_once_and_edits_take_effect() -> None:
    with patch.object(edn, "_parse_all", wraps=edn._parse_all) as parse:
        assert edn.loads_all("[1]") == [[1]]
        assert edn.loads_all("[1]") == [[1]]
        assert edn.loads_all("[2]") == [[2]]
        assert parse.call_count == 2


def test_cached_trees_preserve_types_and_deep_mutation_isolation() -> None:
    source = '(call [{:key [name "text"]}])'
    first = edn.loads_all(source)
    second = edn.loads_all(source)
    assert type(second[0]) is edn.Form
    assert type(second[0][0]) is edn.Symbol
    assert type(second[0][1]) is list
    mapping = second[0][1][0]
    assert type(next(iter(mapping))) is edn.Keyword
    assert type(mapping[edn.Keyword("key")][0]) is edn.Symbol
    assert type(mapping[edn.Keyword("key")][1]) is str
    first[0][1][0][edn.Keyword("key")].append("changed")
    first[0][1].append("changed")
    first[0].append("changed")
    first.append("changed")
    assert second == edn.loads_all(source)
    assert second == [edn.loads(source)]


def test_packages_do_not_share_closure_constants_bodies_or_environments() -> None:
    sources = {
        "main.tlon": """
        (ns main (:export run))
        (def value {:items [1]})
        (defn run [event] value)
        """,
    }
    first = ModulePackage.from_sources(sources, {}).entry("main/run")
    second = ModulePackage.from_sources(sources, {}).entry("main/run")
    first.env["value"][edn.Keyword("items")].append(2)
    first.env["extra"] = True
    first.body.append("changed")
    first.params.append("changed")
    for closure in (second, ModulePackage.from_sources(sources, {}).entry("main/run")):
        assert closure.env["value"] == {edn.Keyword("items"): [1]}
        assert "extra" not in closure.env
        assert closure.body == [edn.Symbol("value")]
        assert closure.params == ["event"]
        assert closure.env["run"] is closure


def test_cached_source_still_validates_each_builtin_vocabulary() -> None:
    sources = {"main.tlon": "(ns main (:export run)) (defn run [x] (host x))"}
    host = Builtin(lambda env, value: value, pure=True)
    ModulePackage.from_sources(sources, {"host": host})
    with pytest.raises(ModuleError, match="unknown function"):
        ModulePackage.from_sources(sources, {})
    with pytest.raises(ModuleError, match="duplicate or reserved binding: run"):
        ModulePackage.from_sources(sources, {"host": host, "run": host})


def test_parse_errors_keep_positions_and_are_not_cached() -> None:
    with patch.object(edn, "_parse_all", wraps=edn._parse_all) as parse:
        for _ in range(2):
            with pytest.raises(edn.EDNParseError) as error:
                edn.loads_all("1\n]")
            assert error.value.line == 2
            assert error.value.column == 1
            assert error.value.message == "unmatched delimiter: ']'"
        assert parse.call_count == 2
    assert edn._cached_parse_all.cache_info().currsize == 0
    assert edn.loads_all("; empty") == []


def test_cache_is_bounded_and_evicts_least_recently_used_source() -> None:
    with patch.object(edn, "_parse_all", wraps=edn._parse_all) as parse:
        for number in range(257):
            assert edn.loads_all(str(number)) == [number]
        assert edn._cached_parse_all.cache_info().currsize == 256
        edn.loads_all("256")
        assert parse.call_count == 257
        edn.loads_all("0")
        assert parse.call_count == 258


def test_large_sources_bypass_cache_without_changing_results() -> None:
    boundary = "1" + " " * (edn._PARSE_CACHE_MAX_SOURCE_LENGTH - 1)
    oversized = boundary + " "
    with patch.object(edn, "_parse_all", wraps=edn._parse_all) as parse:
        assert edn.loads_all(boundary) == [1]
        assert edn.loads_all(boundary) == [1]
        assert parse.call_count == 1
        assert edn.loads_all(oversized) == [1]
        assert edn.loads_all(oversized) == [1]
        assert parse.call_count == 3
    assert edn._cached_parse_all.cache_info().currsize == 1
