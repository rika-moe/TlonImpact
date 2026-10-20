"""Conservative runtime identity for refusing incompatible recovery."""

import hashlib
import json
import sys
from collections.abc import Iterable
from functools import lru_cache
from importlib import metadata
from pathlib import Path

import tlosure
from tlon.base._locked_dependencies import LOCK_DIGEST


def build_identity(
    code_roots: Iterable[Path], lock_digest: str, distributions: Iterable[tuple[str, str]]
) -> str:
    """Hash runtime code and dependency identities, excluding documents and caches."""
    digest = hashlib.sha256()
    for root in sorted(code_roots, key=lambda path: path.name):
        for source in sorted(root.rglob("*.py")):
            digest.update(root.name.encode() + b"/" + source.relative_to(root).as_posix().encode())
            digest.update(b"\0" + source.read_bytes() + b"\0")
    environment = {
        "lock": lock_digest,
        "python": list(sys.version_info[:3]),
        "implementation": sys.implementation.name,
        "dependencies": sorted(set(distributions)),
    }
    digest.update(json.dumps(environment, sort_keys=True, separators=(",", ":")).encode())
    return digest.hexdigest()


@lru_cache(maxsize=1)
def runtime_identity() -> str:
    """One immutable process build; a different environment cannot replay old effects."""
    roots = (Path(__file__).resolve().parents[1], Path(tlosure.__file__).resolve().parent)
    installed = (
        (distribution.metadata["Name"].lower().replace("_", "-"), distribution.version)
        for distribution in metadata.distributions()
    )
    return build_identity(roots, LOCK_DIGEST, installed)
