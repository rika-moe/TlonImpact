"""Finite parsing budgets apply to all public EDN entry points."""

import sys
from collections.abc import Callable
from typing import Any

import pytest

from tlosure.edn import EDNParseError, loads, loads_all


@pytest.mark.parametrize("parse", [loads, loads_all])
def test_source_length_boundary(parse: Callable[[str], Any]) -> None:
    source = '"' + "a" * (1_048_576 - 2) + '"'
    parse(source)
    with pytest.raises(EDNParseError, match="source length limit") as error:
        parse(source + " ")
    assert (error.value.line, error.value.column) == (1, 1)


@pytest.mark.parametrize("parse", [loads, loads_all])
@pytest.mark.parametrize(("opening", "closing"), [("[", "]"), ("(", ")"), ("{:a ", "}")])
def test_container_depth_boundary(parse: Callable[[str], Any], opening: str, closing: str) -> None:
    source = opening * 100 + "nil" + closing * 100
    parse(source)
    parse(source)  # Exercise the cached loads_all path, including deepcopy.
    with pytest.raises(EDNParseError, match="container depth limit"):
        parse(opening + source + closing)


@pytest.mark.parametrize("parse", [loads, loads_all])
def test_node_boundary(parse: Callable[[str], Any]) -> None:
    parse("[" + "0 " * 99_999 + "]")
    with pytest.raises(EDNParseError, match="node limit"):
        parse("[" + "0 " * 100_000 + "]")


def test_all_forms_share_node_budget() -> None:
    assert len(loads_all("0 " * 100_000)) == 100_000
    with pytest.raises(EDNParseError, match="node limit"):
        loads_all("0 " * 100_001)


@pytest.mark.parametrize("parse", [loads, loads_all])
def test_map_pair_boundary_counts_duplicate_keys(parse: Callable[[str], Any]) -> None:
    parse("{" + ":a 0 " * 256 + "}")
    # Reject before attempting to parse the extra key (which is invalid syntax).
    with pytest.raises(EDNParseError, match="map pair limit"):
        parse("{" + ":a 0 " * 256 + "@}")


@pytest.mark.parametrize("count", [16, 32, 64, 256])
def test_colliding_integer_keys_keep_their_values(count: int) -> None:
    expected = {i * sys.hash_info.modulus: i for i in range(count)}
    assert len({hash(key) for key in expected}) == 1
    source = "{" + " ".join(f"{key} {value}" for key, value in expected.items()) + "}"
    assert loads(source) == expected
    assert loads_all(source) == [expected]


def test_many_maps_share_node_budget() -> None:
    # Three nodes per map: the map itself, its key and its value.
    assert len(loads_all("{:a 0} " * 33_333 + "nil")) == 33_334
    with pytest.raises(EDNParseError, match="node limit"):
        loads_all("{:a 0} " * 33_334)
