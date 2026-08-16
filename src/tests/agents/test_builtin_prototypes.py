"""Built-in prototype library smoke test — 播种 → 实例化 → 加载 spec.

Every package-carried prototype must produce a bootable member: seeding
copies the files verbatim, instantiation writes ``behavior.tlon`` into
the new agent's home, and that file must parse as EDN and pass the single
validator — otherwise a spawned member refuses to boot. This is the
regression guard for the known issue where the three cognitive
prototypes shipped invalid EDN (an unbalanced ``}`` in the first rule),
masked by the listing/instantiation tolerance until spawn
(docs/design/prototypes.md).
"""

import pytest

from tlon.agents.edn import loads
from tlon.agents.member_spec import MemberSpec, validate_member_spec
from tlon.agents.prototypes import (
    get_prototype_info,
    instantiate,
    seed_prototypes,
)
from tlon.base.paths import TlonPaths

# Every built-in prototype, with the template parameters instantiation
# requires (relay declares a ``${to}`` placeholder; the cognitive three
# are concrete specs and take none).
BUILTIN_PARAMS: dict[str, dict] = {
    "archivist": {},
    "deep-thinker": {},
    "fast-responder": {},
    "relay": {"to": ["a@tlon.internal"]},
}


def _paths(tmp_path) -> TlonPaths:
    p = TlonPaths(home=tmp_path)
    seed_prototypes(p)
    return p


class TestBuiltinPrototypes:
    @pytest.mark.parametrize(
        ("name", "params"), BUILTIN_PARAMS.items(), ids=BUILTIN_PARAMS.keys()
    )
    def test_seed_instantiate_load_spec(
        self, tmp_path, name: str, params: dict
    ) -> None:
        p = _paths(tmp_path)

        info = get_prototype_info(name, tlon_paths=p)
        assert info is not None
        assert info.trusted

        cfg = instantiate(
            name,
            f"t-{name}",
            name,
            params=params or None,
            tlon_paths=p,
        )
        assert cfg.id == f"t-{name}"

        # The instance's behavior file must parse and pass the single
        # validator (host-less registry) — the boot gate for a spawned
        # member. A broken (e.g. unbalanced-brace) prototype fails here.
        text = p.agent_behavior_file(cfg.id).read_text(encoding="utf-8")
        spec = validate_member_spec(MemberSpec.from_edn(loads(text)))

        # Every built-in declares a boot rule; at most one cognitive rule
        # (the unified spec is total — the derived runtime comes from here).
        assert any(rule.on is None for rule in spec.rules)
        assert sum(rule.loop is not None for rule in spec.rules) <= 1
