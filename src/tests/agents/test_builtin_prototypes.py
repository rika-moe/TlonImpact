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

from pathlib import Path

import pytest

import tlon
from tlon.agents.edn import dumps, loads
from tlon.agents.member_spec import MemberLoop, MemberSpec, validate_member_spec
from tlon.agents.prototypes import (
    get_prototype_info,
    instantiate,
    seed_prototypes,
)
from tlon.base.paths import TlonPaths

# Every built-in prototype, with the template parameters instantiation
# requires (relay declares a ``${to}`` placeholder; the others are
# concrete specs and take none). Includes the seven role prototypes
# (developer/ops/pm/support/product/data-analyst/scribe) that the
# standard team is born from (ADR 0006).
BUILTIN_PARAMS: dict[str, dict] = {
    "archivist": {},
    "deep-thinker": {},
    "fast-responder": {},
    "relay": {"to": ["a@tlon.internal"]},
    "developer": {},
    "ops": {},
    "pm": {},
    "support": {},
    "product": {},
    "data-analyst": {},
    "scribe": {},
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
            model="openai/gpt-5",
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


#: The default cognitive loop (issue ): a lightweight pre-submission
#: review. critique fires only when the round's pending response would
#: end the session (``completing?``) — one review per completion attempt,
#: independent of the round count; a FAIL blocks act and revise feeds the
#: feedback back for the next think.
DEFAULT_COGNITIVE_STAGES = [
    "(think)",
    "(critique {:if (completing?)})",
    "(act {:if (or (not (completing?)) (not (critique-failed?)))})",
    "(revise {:if (critique-failed?)})",
]

#: Cognitive prototypes carrying the default loop. fast-responder is
#: deliberately bare (minimal design); relay is deterministic (no loop).
GATED_PROTOTYPES = [
    "archivist",
    "deep-thinker",
    "developer",
    "ops",
    "pm",
    "support",
    "product",
    "data-analyst",
    "scribe",
]


class TestDefaultStageVector:
    @pytest.mark.parametrize("name", GATED_PROTOTYPES)
    def test_cognitive_prototypes_carry_the_lightweight_critique(self, name: str) -> None:
        loop = _prototype_loop(name)
        assert [dumps(form) for form in loop.stages] == DEFAULT_COGNITIVE_STAGES

    def test_fast_responder_stays_bare(self) -> None:
        loop = _prototype_loop("fast-responder")
        assert [dumps(form) for form in loop.stages] == ["(think)", "(act)"]


def _prototype_loop(name: str) -> MemberLoop:
    """The validated cognitive loop of a package-carried prototype."""
    path = Path(tlon.__file__).parent / "prototypes" / name / "behavior.tlon"
    spec = validate_member_spec(MemberSpec.from_edn(loads(path.read_text(encoding="utf-8"))))
    loop = spec.rules[0].loop
    assert loop is not None
    return loop
