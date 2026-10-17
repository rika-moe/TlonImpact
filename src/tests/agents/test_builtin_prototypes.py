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
from uuid import UUID

import pytest

import tlon
from tests.prototype_helpers import builtin_id
from tlon.agents.behavior_template import analyze
from tlon.agents.member_spec import MemberLoop, MemberSpec, validate_member_spec
from tlon.agents.prototypes.catalog import get_prototype_info, seed_prototypes
from tlon.agents.prototypes.instantiate import instantiate
from tlon.base.paths import TlonPaths
from tlosure.edn import dumps, loads

# Fixed package IDs are discovered by behavioral labels, never by historical roles.
BUILTIN_PARAMS: dict[str, dict] = {
    builtin_id(label): ({"to": ["a@tlon.internal"]} if label == "定址转发" else {})
    for label in (
        "频繁反思迭代",
        "长预算工具迭代",
        "超长预算迭代",
        "精简上下文响应",
        "标准工具迭代",
        "标准文件迭代",
        "定址转发",
        "低频反思迭代",
        "标准无执行迭代",
    )
}
EXPECTED_LEVELS: dict[str, str] = {
    name: "L2"
    if name in {builtin_id("长预算工具迭代"), builtin_id("标准工具迭代")}
    else "L1"
    if name == builtin_id("标准文件迭代")
    else "L0"
    for name in BUILTIN_PARAMS
}


def _paths(tmp_path) -> TlonPaths:
    p = TlonPaths(home=tmp_path)
    seed_prototypes(p)
    return p


class TestBuiltinPrototypes:
    @pytest.mark.parametrize(("name", "params"), BUILTIN_PARAMS.items(), ids=BUILTIN_PARAMS.keys())
    def test_seed_instantiate_load_spec(self, tmp_path, name: str, params: dict) -> None:
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

        # The :execution declaration (genotype) is projected once into
        # agent.yaml's docker.level by instantiate — the runtime authority.
        assert spec.execution_level == EXPECTED_LEVELS[name]
        assert cfg.docker.level == EXPECTED_LEVELS[name]


#: The default cognitive loop (docs/design/runtime.md): a lightweight
#: pre-submission review. critique fires only when the round's pending response would
#: end the session (``completing?``) — one review per completion attempt,
#: independent of the round count; a FAIL blocks act and revise feeds the
#: feedback back for the next think.
DEFAULT_COGNITIVE_STAGES = [
    "(think)",
    "(critique {:if (completing?)})",
    "(act {:if (or (not (completing?)) (not (critique-failed?)))})",
    "(revise {:if (critique-failed?)})",
]

#: Minimal-context and deterministic behaviors deliberately omit the default loop.
GATED_PROTOTYPES = [
    name
    for name in BUILTIN_PARAMS
    if name not in {builtin_id("精简上下文响应"), builtin_id("定址转发")}
]


class TestDefaultStageVector:
    @pytest.mark.parametrize("name", GATED_PROTOTYPES)
    def test_cognitive_prototypes_carry_the_lightweight_critique(self, name: str) -> None:
        loop = _prototype_loop(name)
        assert [dumps(form) for form in loop.stages] == DEFAULT_COGNITIVE_STAGES

    def test_fast_responder_stays_bare(self) -> None:
        loop = _prototype_loop(builtin_id("精简上下文响应"))
        assert [dumps(form) for form in loop.stages] == ["(think)", "(act)"]


class TestExecutionLevels:
    def test_level_table_covers_every_builtin(self) -> None:
        assert set(EXPECTED_LEVELS) == set(BUILTIN_PARAMS)

    @pytest.mark.parametrize(("name", "level"), EXPECTED_LEVELS.items(), ids=EXPECTED_LEVELS.keys())
    def test_builtin_prototype_declares_its_level(self, name: str, level: str) -> None:
        spec = _prototype_spec(name)
        assert spec.execution_level == level


def _prototype_spec(name: str) -> MemberSpec:
    """The validated spec of a package-carried prototype.

    Template prototypes (relay) carry ``${var}`` placeholders that are not
    parseable EDN; the stand-in text substitutes type-appropriate
    placeholders, exactly as instantiation's structural validation does.
    """
    path = Path(tlon.__file__).parent / "prototypes" / name / "behavior.tlon"
    text = analyze(path.read_text(encoding="utf-8")).standin_text
    return validate_member_spec(MemberSpec.from_edn(loads(text)))


def _prototype_loop(name: str) -> MemberLoop:
    """The validated cognitive loop of a package-carried prototype."""
    loop = _prototype_spec(name).rules[0].loop
    assert loop is not None
    return loop


class TestBuiltinLibraryIdentity:
    def test_nine_fixed_generated_ids_and_distinct_behavior(self) -> None:
        root = Path(tlon.__file__).parent / "prototypes"
        directories = [entry for entry in root.iterdir() if (entry / "prototype.tlon").is_file()]
        assert len(directories) == 9
        assert {entry.name for entry in directories} == set(BUILTIN_PARAMS)
        specs = []
        for entry in directories:
            assert entry.name.startswith("p-")
            assert UUID(hex=entry.name[2:]).version == 4
            metadata = {
                str(key).lstrip(":"): value
                for key, value in loads((entry / "prototype.tlon").read_text()).items()
            }
            assert str(metadata["name"]).lstrip(":") == entry.name
            # Preserve parameter declarations while comparing parsed behavior.
            spec = loads(analyze((entry / "behavior.tlon").read_text()).standin_text)
            assert spec not in specs
            specs.append(spec)

    def test_seed_is_idempotent_and_preserves_existing_files(self, tmp_path: Path) -> None:
        paths = TlonPaths(home=tmp_path)
        assert seed_prototypes(paths) == 18
        name = builtin_id("标准工具迭代")
        metadata = paths.prototypes_dir() / name / "prototype.tlon"
        metadata.write_text("user-owned metadata")
        assert seed_prototypes(paths) == 0
        assert metadata.read_text() == "user-owned metadata"
        assert {entry.name for entry in paths.prototypes_dir().iterdir()} == set(BUILTIN_PARAMS)
