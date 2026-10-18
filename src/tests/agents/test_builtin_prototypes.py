"""Built-in prototype library smoke test — 播种 → 实例化 → 加载 spec.

Every package-carried prototype must produce a bootable member: seeding
copies the files verbatim, instantiation writes ``behavior.tlon`` into
the new agent's home, and that file must parse as EDN and pass the single
validator — otherwise a spawned member refuses to boot. This is the
regression guard for the known issue where the three session
prototypes shipped invalid EDN (an unbalanced ``}`` in the first rule),
masked by the listing/instantiation tolerance until spawn
(docs/design/prototypes.md).
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

import tlon
from tests.agents.test_completion_gate import _completing, _drive, _make_driver
from tests.prototype_helpers import builtin_id
from tests.recipe_helpers import recipe_for
from tlon.agents.behavior_template import analyze, render_template
from tlon.agents.member_runtime import MemberRuntime
from tlon.agents.member_spec import MemberSpec, SessionRecipe, validate_member_spec
from tlon.agents.prototypes.catalog import get_prototype_info, seed_prototypes
from tlon.agents.prototypes.instantiate import instantiate
from tlon.agents.session import AgentSession, SessionState
from tlon.agents.stages.plan import plan_stage
from tlon.agents.vocabulary import (
    LOOP_BUILTINS,
    STAGE_COND_BUILTINS,
    make_member_builtins,
    stage_cond_env,
)
from tlon.base.events.event_bus import EventBus
from tlon.base.llm_client import LLMResponse
from tlon.base.paths import TlonPaths
from tlon.organization.mail.mail import Mail
from tlosure.edn import dumps, loads
from tlosure.eval import ActionRequest, eval_form

# Fixed package IDs are discovered by behavioral labels, never by historical roles.
BUILTIN_PARAMS: dict[str, dict] = {
    builtin_id(label): (
        {"sender": "sender@tlon.internal", "pattern": "URGENT", "to": ["a@tlon.internal"]}
        if label == "条件路由"
        else {"to": ["a@tlon.internal"]}
        if label == "定址转发"
        else {}
    )
    for label in (
        "计划后执行",
        "短回合交流",
        "标准工具迭代",
        "标准文件迭代",
        "定址转发",
        "标准无执行迭代",
        "证据核验",
        "条件路由",
    )
}
EXPECTED_LEVELS: dict[str, str] = {
    name: "L2"
    if name in {builtin_id("计划后执行"), builtin_id("标准工具迭代"), builtin_id("证据核验")}
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

        # Every built-in declares a boot rule.
        assert any(rule.on is None for rule in spec.rules)

        # The :execution declaration (genotype) is projected once into
        # agent.yaml's docker.level by instantiate — the runtime authority.
        assert spec.execution_level == EXPECTED_LEVELS[name]
        assert cfg.docker.level == EXPECTED_LEVELS[name]


#: The default session recipe (docs/design/runtime.md): a lightweight
#: pre-submission review. critique fires only when the round's pending response would
#: end the session (``completing?``) — one review per completion attempt,
#: independent of the round count; a FAIL blocks act and revise feeds the
#: feedback back for the next think.
DEFAULT_REVIEW_STAGES = [
    "(think)",
    "(critique {:if (sending-mail?)})",
    "(act {:if (or (not (sending-mail?)) (not (critique-failed?)))})",
    "(revise {:if (critique-failed?)})",
]

#: Minimal-context and forwarding programs deliberately omit the default loop.
GATED_PROTOTYPES = [
    name
    for name in BUILTIN_PARAMS
    if name
    not in {
        builtin_id(label)
        for label in ("短回合交流", "定址转发", "条件路由", "计划后执行", "证据核验")
    }
]


class TestDefaultStageVector:
    @pytest.mark.parametrize("name", GATED_PROTOTYPES)
    def test_session_recipes_carry_the_lightweight_critique(self, name: str) -> None:
        loop = _prototype_loop(name)
        assert [dumps(form) for form in loop.stages] == DEFAULT_REVIEW_STAGES

    def test_fast_responder_stays_bare(self) -> None:
        loop = _prototype_loop(builtin_id("短回合交流"))
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


def _prototype_loop(name: str) -> SessionRecipe:
    """The validated session recipe of a package-carried prototype."""
    loop = recipe_for(_prototype_spec(name).rules[0])
    assert loop is not None
    return loop


class TestBuiltinLibraryIdentity:
    def test_eight_fixed_generated_ids_and_distinct_behavior(self) -> None:
        root = Path(tlon.__file__).parent / "prototypes"
        directories = [entry for entry in root.iterdir() if (entry / "prototype.tlon").is_file()]
        assert len(directories) == 8
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
        assert seed_prototypes(paths) == 16
        name = builtin_id("标准工具迭代")
        metadata = paths.prototypes_dir() / name / "prototype.tlon"
        metadata.write_text("user-owned metadata")
        assert seed_prototypes(paths) == 0
        assert metadata.read_text() == "user-owned metadata"
        assert {entry.name for entry in paths.prototypes_dir().iterdir()} == set(BUILTIN_PARAMS)


class TestDistinctBehaviorPrototypes:
    async def test_planning_runs_once_in_a_multi_round_session(self) -> None:
        loop = _prototype_loop(builtin_id("计划后执行"))
        assert (loop.max_iterations, loop.max_tokens) == (30, 1000000)
        form = loop.stages[0]
        assert str(form[0]) == "plan"
        opts = {str(key): value for key, value in form[1].items()}
        svc = MagicMock()
        svc.think = AsyncMock(return_value=LLMResponse(content="1. 检查输入\n2. 执行并验证"))
        session = AgentSession(session_id="plan-once", trigger_content="处理任务")
        for iteration in range(3):
            env = stage_cond_env(
                iteration=iteration,
                error_count=0,
                token_usage=0,
                scratch=session.loop_scratch,
                completing=False,
            )
            if eval_form(opts["if"], dict(env), STAGE_COND_BUILTINS):
                outcome = await plan_stage(svc, session, None, opts)
                session.loop_scratch.update(outcome.scratch_updates)
                session.context.extend(outcome.context_appends)
        svc.think.assert_awaited_once()
        assert "不超过 5 步" in svc.think.call_args.args[0][-1]["content"]
        assert session.loop_scratch["plan"].startswith("1. 检查输入")
        assert [dumps(stage) for stage in loop.stages[1:]] == DEFAULT_REVIEW_STAGES

    def test_short_conversation_bounds_and_task_context(self) -> None:
        loop = _prototype_loop(builtin_id("短回合交流"))
        assert (loop.max_iterations, loop.max_tokens) == (3, 32000)
        assert [dumps(section) for section in loop.context_sections] == [
            "(persona)",
            "(protocol)",
            "(current-task)",
            "(mail-history 5)",
            "(thread-recap)",
        ]
        for iteration, tokens, exhausted in [(2, 31999, False), (3, 0, True), (0, 32000, True)]:
            assert (
                eval_form(
                    loop.until,
                    {
                        "state": {"iteration": iteration, "error_count": 0, "token_usage": tokens},
                        "limits": {
                            "max_iterations": loop.max_iterations,
                            "max_consecutive_errors": loop.max_consecutive_errors,
                            "max_tokens": loop.max_tokens,
                        },
                    },
                    LOOP_BUILTINS,
                )
                is exhausted
            )

    @pytest.mark.parametrize("passed", [True, False, None])
    def test_evidence_completion_requires_an_explicit_pass(self, passed: bool | None) -> None:
        loop = _prototype_loop(builtin_id("证据核验"))
        options = [
            {str(key): value for key, value in stage[1].items()} for stage in loop.stages[1:]
        ]
        assert "具体来源" in options[0]["instructions"]
        scratch = {} if passed is None else {"critique": {"passed": passed}}
        env = dict(
            stage_cond_env(
                iteration=0,
                error_count=0,
                token_usage=0,
                scratch=scratch,
                completing=False,
                sending_mail=True,
            )
        )
        assert eval_form(options[0]["if"], env, STAGE_COND_BUILTINS) is True
        assert eval_form(options[1]["if"], env, STAGE_COND_BUILTINS) is (passed is True)
        assert eval_form(options[2]["if"], env, STAGE_COND_BUILTINS) is (passed is not True)

    @pytest.mark.parametrize(
        ("sender", "content", "recipient", "matches"),
        [
            ("sender@tlon.internal", "URGENT: 请处理", "router", True),
            ("sender@tlon.internal", "URGENT: 请处理", "other", False),
            ("sender@tlon.internal", "URGENT: 请处理", None, False),
            ("other@tlon.internal", "URGENT: 请处理", "router", False),
            ("sender@tlon.internal", "日常问候", "router", False),
        ],
    )
    def test_conditional_router_evaluates_sender_and_content(
        self, sender: str, content: str, recipient: str | None, matches: bool
    ) -> None:
        name = builtin_id("条件路由")
        path = Path(tlon.__file__).parent / "prototypes" / name / "behavior.tlon"
        text = render_template(path.read_text(), BUILTIN_PARAMS[name])
        spec = validate_member_spec(MemberSpec.from_edn(loads(text)))
        member = MemberRuntime(
            member_id="router",
            name="router",
            event_bus=EventBus(),
            spec=spec,
            builtins=make_member_builtins(),
        )
        mail = Mail.create(from_address=sender, content=content, to=["router@tlon.internal"])
        actions = member._evaluate_rule(
            spec.rules[0], member._env({"mail": mail, "recipient": recipient})
        )
        assert actions == (
            [
                ActionRequest(
                    kind="forward", data={"to": ["a@tlon.internal"], "cc": [], "subject": None}
                ),
                ActionRequest(kind="mark_read", data={}),
            ]
            if matches
            else []
        )


class TestEvidencePrototypeExecution:
    async def test_fail_revises_then_pass_delivers_only_revised_reply(self, tmp_path: Path) -> None:
        name = builtin_id("证据核验")
        text = (Path(tlon.__file__).parent / "prototypes" / name / "behavior.tlon").read_text()
        with patch("tests.agents.test_completion_gate.GATED_EDN", text):
            driver, journal, executor, services = _make_driver(
                tmp_path,
                [
                    _completing("未经核实的结论"),
                    LLMResponse(content="FAIL\n缺少实际计算依据"),
                    _completing("修订：计算结果为 4"),
                    LLMResponse(content="PASS"),
                ],
            )
        session = AgentSession(session_id="evidence-revision", trigger_content="核验计算结果")

        await driver.step_session(session)

        assert session.state == SessionState.ACTIVE
        executor.send_mail.assert_not_awaited()
        assert any("缺少实际计算依据" in str(message.get("content")) for message in session.context)

        await _drive(driver, session)

        assert session.is_completed
        executor.send_mail.assert_awaited_once()
        assert executor.send_mail.await_args.args[0].content == "修订：计算结果为 4"
        critiques = [
            call
            for call in services.think.await_args_list
            if call.kwargs.get("span") == "agent.critique"
        ]
        assert len(critiques) == 2
        assert all("具体来源" in call.args[0][-1]["content"] for call in critiques)
        assert await journal.derive_context(session.id) == session.context

    async def test_unknown_review_cannot_deliver_and_remains_bounded(self, tmp_path: Path) -> None:
        name = builtin_id("证据核验")
        text = (Path(tlon.__file__).parent / "prototypes" / name / "behavior.tlon").read_text()
        with patch("tests.agents.test_completion_gate.GATED_EDN", text):
            driver, _, executor, services = _make_driver(
                tmp_path,
                [_completing("未经核实的结论"), LLMResponse(content="大概没问题")]
                * _prototype_loop(name).max_iterations,
            )
        session = AgentSession(session_id="evidence-unknown", trigger_content="核验依据")

        await _drive(driver, session)

        assert session.is_completed
        assert session.iteration == _prototype_loop(name).max_iterations
        executor.send_mail.assert_not_awaited()
        assert len(services.think.await_args_list) == 2 * _prototype_loop(name).max_iterations
