"""Prototype lineage: walk the ``:parent`` chain up to the root, deriving
per-generation diffs.
"""

import difflib
from pathlib import Path
from typing import Any

from tlon.agents.prototypes.info import (
    BEHAVIOR_FILE,
    _find_prototype_dir,
    _read_metadata,
    _validate_name,
)
from tlon.base.paths import TlonPaths
from tlon.tools.prototypes.ports import PrototypeError

_MAX_LINEAGE_DEPTH = 32


def get_prototype_lineage(
    name: str, tlon_paths: TlonPaths
) -> list[dict[str, Any]]:
    """Walk the ``:parent`` chain up to the root (issue ).

    The lineage is **derived**, never stored separately: each generation is
    read from its own ``prototype.edn`` (trusted or staging area, whichever
    holds it), and the diff between adjacent generations is a plain
    ``difflib`` unified diff of the two ``behavior.tlon`` texts.

    A parent that was deleted or renamed breaks the chain: the walk stops
    there and the oldest reachable entry is marked ``broken``. Cycles and
    runaway chains are cut at ``_MAX_LINEAGE_DEPTH`` / a visited set, also
    marked ``broken``.

    Args:
        name: prototype directory name (either area)
        tlon_paths: path resolver for the target layout

    Returns:
        Root-first list of generations, each
        ``{name, generation, author, proposal_note, trusted, parent,
        diff_from_parent, broken}``; ``diff_from_parent`` is None for the
        root entry.

    Raises:
        PrototypeError: invalid name or no prototype under that name in
            either area
    """
    p = tlon_paths
    _validate_name(name)
    proto_dir = _find_prototype_dir(name, p)
    if proto_dir is None:
        raise PrototypeError(f"no prototype named '{name}'")

    chain: list[dict[str, Any]] = []  # youngest first while walking up
    visited: set[str] = set()
    current: Path | None = proto_dir
    while current is not None:
        info = _read_metadata(current)
        entry: dict[str, Any] = {
            "name": info.name,
            "generation": info.generation,
            "author": info.author,
            "proposal_note": info.proposal_note,
            "trusted": info.trusted,
            "parent": info.parent,
            "broken": False,
            "_behavior": (current / BEHAVIOR_FILE).read_text(encoding="utf-8"),
        }
        chain.append(entry)
        visited.add(info.name)
        if info.parent is None:
            break
        if info.parent in visited or len(chain) >= _MAX_LINEAGE_DEPTH:
            entry["broken"] = True
            break
        current = _find_prototype_dir(info.parent, p)
        if current is None:
            entry["broken"] = True
    chain.reverse()

    for i, entry in enumerate(chain):
        if i == 0:
            entry["diff_from_parent"] = None
        else:
            entry["diff_from_parent"] = "".join(
                difflib.unified_diff(
                    chain[i - 1]["_behavior"].splitlines(keepends=True),
                    entry["_behavior"].splitlines(keepends=True),
                    fromfile=f"{chain[i - 1]['name']}/{BEHAVIOR_FILE}",
                    tofile=f"{entry['name']}/{BEHAVIOR_FILE}",
                )
            )
    for entry in chain:
        del entry["_behavior"]
    return chain
