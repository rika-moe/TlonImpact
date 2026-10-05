"""The vocabulary bridge: trusted capability library → member registries
(docs/design/capabilities.md 「词汇桥」, issue #45 slice 2/3).

At load time every artifact in the trusted library
(``~/.tlon/capabilities/``) is parsed and run through the load gate
(:func:`validate_capability` — structure, purity admission, namespace
rejection), then registered as a **closure-backed** :class:`Builtin`:
the entry carries a :class:`Closure` whose captured env is **empty**
(the runtime face of the env contract "sees args only, zero injection")
and whose body resolves words through the *caller's* registry — the
admission gate already guaranteed the impl only references capability
vocabulary. Application counts into the caller's fuel/depth budget,
unlike a host builtin's unmetered internals.

Blast-radius decision: one bad trusted artifact (unparseable, failing
validation, unreadable) is logged and **excluded** — it never fails the
whole registry. A spec referencing the excluded word fails its own load
validation with "unknown function", so the blast radius is exactly the
word's users, never the society.

Deprecated words register like any other (the word stays usable; the
``:deprecated`` marker lives in the metadata layer — ``info.py`` /
``catalog.py`` — which the bridge does not read).

The bridge is invoked on every member-spec load (``AgentRegistry``), not
cached: the trusted library is small (a directory scan plus a few small
artifacts), and recomputing aligns with the trust ladder's
wake-reassembly semantics — a newly approved word takes effect at the
member's next wake, no server restart, no cache invalidation.
"""

from collections.abc import Iterable

from tlon.agents.capabilities.info import CAPABILITY_FILE, _is_capability_dir
from tlon.agents.capabilities.spec import (
    CapabilityError,
    CapabilitySpec,
    validate_capability,
)
from tlon.agents.vocabulary import make_member_builtins
from tlon.base.logger import get_logger
from tlon.base.paths import TlonPaths
from tlosure.builtins import Builtin, Closure
from tlosure.validate import _params

logger = get_logger(__name__)


def load_capability_builtins(
    tlon_paths: TlonPaths, *, reserved: Iterable[str] = ()
) -> dict[str, Builtin]:
    """Load the trusted capability library as closure-backed registry entries.

    Namespace: a word colliding with a special form, a builtin of the
    capability registries, a name in ``reserved`` (the caller's host
    vocabulary — closure-injected host words like ``member-roster`` are
    absent from the host-less registry and must be passed explicitly),
    or another trusted capability's word is rejected (shadowing is
    rejected, never prefixed). The host-less ``make_member_builtins()``
    word set is always reserved.

    Returns:
        The accepted words keyed by name, each a closure-backed
        :class:`Builtin` with the artifact's declared purity marker.
    """
    root = tlon_paths.capabilities_dir()
    if not root.exists():
        return {}
    taken = set(make_member_builtins()) | set(reserved)
    words: dict[str, Builtin] = {}
    for cap_dir in sorted(root.iterdir()):
        if not cap_dir.is_dir() or not _is_capability_dir(cap_dir):
            continue
        try:
            spec = CapabilitySpec.from_edn(
                (cap_dir / CAPABILITY_FILE).read_text(encoding="utf-8")
            )
            validate_capability(spec, taken_names=taken)
        except (CapabilityError, OSError) as e:
            logger.error("capability_word_rejected", directory=cap_dir.name, error=str(e))
            continue
        words[spec.name] = _closure_backed(spec)
        taken.add(spec.name)
    return words


def _closure_backed(spec: CapabilitySpec) -> Builtin:
    """Build the registry entry for one validated artifact.

    The ``:impl`` fn literal becomes a :class:`Closure` with an empty
    captured env (zero injection) named after the word, so arity errors
    read ``thread-digest expects 1 arguments, got 2``.
    """
    params, _ = _params(spec.impl[1], "fn")  # fixed arity, validated at admission
    closure = Closure(params=params, rest=None, body=list(spec.impl[2:]), env={}, name=spec.name)
    return Builtin.from_closure(closure, pure=spec.pure)
