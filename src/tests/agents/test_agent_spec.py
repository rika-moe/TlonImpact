"""Tests for AgentSpec (tlon.agents.spec) and behavior.tlon loading."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tlon.agents.agent import Agent
from tlon.agents.edn import EDNParseError, Keyword, dumps, loads
from tlon.agents.spec import (
    DEFAULT_SECTIONS,
    REACT_STAGES,
    REACT_UNTIL,
    AgentSpec,
    SpecValidationError,
    standard_behavior_edn,
)
from tlon.agents.tool_executor import ToolExecutor
from tlon.base.config import AgentConfig
from tlon.base.events.event import EventType
from tlon.base.llm_client import LLMResponse, ToolCall
from tlon.base.paths import TlonPaths
from tlon.organization.mail.mail import Mail
from tlon.tools.base import ToolResult


def _section(standard: str, fragment: str) -> dict[Any, Any]:
    """Merge ``fragment`` map entries into a standard section map."""
    merged: dict[Any, Any] = dict(loads(standard))
    if fragment:
        merged.update(loads(f"{{{fragment}}}"))
    return merged


def _full_data(
    *,
    trigger: str = "",
    context: str = "",
    loop: str = "",
    on_complete: str = "",
) -> dict[Any, Any]:
    """A complete parsed spec map (standard ReAct program).

    The spec is total: every section and every required field must be
    present. Each keyword argument carries extra EDN map entries merged
    into that section, so a test can exercise one field while the rest
    of the spec stays valid.
    """
    return {
        Keyword("version"): 1,
        Keyword("trigger"): _section("{:on-mention true}", trigger),
        Keyword("context"): _section(
            "{:sections [(persona) (protocol) (knowledge) (mail-history 20)]}", context
        ),
        Keyword("loop"): _section(
            "{:stages [(think) (act)]"
            " :until (or (iteration-limit-reached?) (error-limit-reached?))"
            " :max-iterations 10 :max-consecutive-errors 3}",
            loop,
        ),
        Keyword("on-complete"): _section(
            "{:save-interaction true :reflect {:enabled true :interval-minutes 15}}",
            on_complete,
        ),
    }


def _spec(**sections: str) -> AgentSpec:
    """Parse a complete spec with per-section overrides (see ``_full_data``)."""
    return AgentSpec.from_edn(_full_data(**sections))


class TestAgentSpecSemantics:
    def test_standard_behavior_edn_is_the_react_program(self):
        spec = AgentSpec.from_edn(loads(standard_behavior_edn()))
        assert spec.trigger.on_mention is True
        assert spec.trigger.when is None
        assert spec.context.sections == DEFAULT_SECTIONS
        assert spec.loop.stages == REACT_STAGES
        assert spec.loop.until == REACT_UNTIL
        assert spec.loop.max_iterations == 10
        assert spec.loop.max_consecutive_errors == 3
        assert spec.on_complete.save_interaction is True
        assert spec.on_complete.reflect.enabled is True
        assert spec.on_complete.reflect.interval_minutes == 15

    def test_to_dict_from_dict_roundtrip(self):
        spec = _spec(loop=":max-iterations 7")
        assert AgentSpec.from_dict(spec.to_dict()) == spec

    def test_version_required(self):
        with pytest.raises(SpecValidationError, match=":version"):
            AgentSpec.from_edn(loads("{:trigger {:on-mention true}}"))

    def test_version_unsupported(self):
        with pytest.raises(SpecValidationError, match="version"):
            AgentSpec.from_edn(loads("{:version 2}"))

    def test_top_level_must_be_map(self):
        with pytest.raises(SpecValidationError, match="top-level map"):
            AgentSpec.from_edn(loads("[1 2 3]"))

    def test_type_error_reports_field(self):
        with pytest.raises(SpecValidationError, match="max-iterations"):
            _spec(loop=':max-iterations "ten"')

    def test_bool_field_rejects_int(self):
        with pytest.raises(SpecValidationError, match=":on-mention"):
            _spec(trigger=":on-mention 1")

    def test_unknown_keys_ignored(self):
        data = _full_data(trigger=":on-mention false :bogus 1")
        data[Keyword("future-feature")] = [1, 2]
        spec = AgentSpec.from_edn(data)
        assert spec.trigger.on_mention is False

    def test_retired_proactive_key_silently_ignored(self):
        # :proactive died with R1b: still a known key, but never read.
        spec = _spec(trigger=":on-mention false :proactive true")
        assert not hasattr(spec.trigger, "proactive")
        assert spec.trigger.on_mention is False

    def test_legacy_context_keys_warn_as_unknown(self):
        # Legacy :persona/:protocol/:knowledge/:mail-history keys are retired
        # with the total-spec migration: they warn as unknown keys and are
        # never read — :sections is the only context input.
        with patch("tlon.agents.spec.logger") as mock_logger:
            spec = _spec(context=":knowledge false :mail-history 5")
        assert spec.context.sections == DEFAULT_SECTIONS  # untouched
        warning_events = [c.args[0] for c in mock_logger.warning.call_args_list]
        assert "agent_spec_unknown_key" in warning_events


class TestTotalValidation:
    """Total spec semantics: every section and required field must be present."""

    @pytest.mark.parametrize("missing", ["trigger", "context", "loop", "on-complete"])
    def test_missing_top_level_section_rejected(self, missing):
        data = _full_data()
        del data[Keyword(missing)]
        with pytest.raises(
            SpecValidationError, match=f"missing required :{missing} section"
        ):
            AgentSpec.from_edn(data)

    @pytest.mark.parametrize(
        "section, source, match",
        [
            ("trigger", "{}", ":trigger/:on-mention"),
            ("context", "{}", ":context/:sections"),
            (
                "loop",
                "{:stages [(think)] :until (error-limit-reached?) :max-consecutive-errors 3}",
                ":loop/:max-iterations",
            ),
            (
                "loop",
                "{:stages [(think)] :until (error-limit-reached?) :max-iterations 10}",
                ":loop/:max-consecutive-errors",
            ),
            (
                "loop",
                "{:until (error-limit-reached?) :max-iterations 10 :max-consecutive-errors 3}",
                ":loop/:stages",
            ),
            (
                "loop",
                "{:stages [(think)] :max-iterations 10 :max-consecutive-errors 3}",
                ":loop/:until",
            ),
            (
                "on-complete",
                "{:reflect {:enabled true :interval-minutes 15}}",
                ":on-complete/:save-interaction",
            ),
            ("on-complete", "{:save-interaction true}", ":on-complete/:reflect"),
            (
                "on-complete",
                "{:save-interaction true :reflect {:interval-minutes 15}}",
                ":reflect/:enabled",
            ),
            (
                "on-complete",
                "{:save-interaction true :reflect {:enabled true}}",
                ":reflect/:interval-minutes",
            ),
        ],
    )
    def test_missing_required_field_rejected(self, section, source, match):
        data = _full_data()
        data[Keyword(section)] = loads(source)
        with pytest.raises(SpecValidationError, match=match):
            AgentSpec.from_edn(data)


# ── behavior.tlon file loading (Agent integration) ──


def _make_agent(**kwargs) -> Agent:
    """Build an Agent with all deps as mocks."""
    config = kwargs.pop("config", None) or AgentConfig(id="a-001", name="TestAgent")

    event_bus = MagicMock()
    event_bus.publish = AsyncMock()
    event_bus.subscribe = MagicMock(return_value="sub-id")
    event_bus.unsubscribe = MagicMock()

    defaults = {
        "config": config,
        "event_bus": event_bus,
        "llm_client": MagicMock(),
        "memory": AsyncMock(),
        "task_manager": MagicMock(),
        "tool_executor": ToolExecutor(),
    }
    defaults.update(kwargs)
    return Agent(**defaults)


def _use_tmp_paths(monkeypatch, tmp_path) -> TlonPaths:
    """Redirect the paths singleton used by spec loading to a tmp dir."""
    test_paths = TlonPaths(home=tmp_path)
    monkeypatch.setattr("tlon.agents.spec.paths", test_paths)
    return test_paths


def _write_behavior(test_paths: TlonPaths, agent_id: str, content: str) -> None:
    behavior_file = test_paths.agent_config_dir(agent_id) / "behavior.tlon"
    behavior_file.write_text(content, encoding="utf-8")


class TestBehaviorFileLoading:
    """A missing or invalid behavior file refuses the agent's boot (no fallback)."""

    def test_missing_file_refuses_boot(self, tmp_path, monkeypatch):
        _use_tmp_paths(monkeypatch, tmp_path)
        with pytest.raises(
            SpecValidationError, match="behavior file missing, agent refused to boot"
        ):
            _make_agent()

    def test_complete_behavior_file_loads(self, tmp_path, monkeypatch):
        test_paths = _use_tmp_paths(monkeypatch, tmp_path)
        _write_behavior(test_paths, "a-001", standard_behavior_edn())
        agent = _make_agent()
        assert agent.spec.loop.max_iterations == 10
        assert agent.spec.source_hash  # stamped from the file bytes

    def test_behavior_file_drives_spec(self, tmp_path, monkeypatch):
        test_paths = _use_tmp_paths(monkeypatch, tmp_path)
        _write_behavior(test_paths, "a-001", dumps(_full_data(loop=":max-iterations 1")))
        agent = _make_agent()
        assert agent.spec.loop.max_iterations == 1
        assert agent.spec.on_complete.save_interaction is True

    def test_malformed_file_refuses_boot(self, tmp_path, monkeypatch):
        test_paths = _use_tmp_paths(monkeypatch, tmp_path)
        _write_behavior(test_paths, "a-001", "{:version 1 :loop {:max-iterations")
        with pytest.raises(EDNParseError):
            _make_agent()

    def test_invalid_spec_refuses_boot(self, tmp_path, monkeypatch):
        test_paths = _use_tmp_paths(monkeypatch, tmp_path)
        _write_behavior(
            test_paths, "a-001", dumps(_full_data(loop=':max-iterations "ten"'))
        )
        with pytest.raises(SpecValidationError, match="max-iterations"):
            _make_agent()


class TestSpecDrivenBehavior:
    async def test_max_iterations_one_gives_up_early(self, tmp_path, monkeypatch):
        """behavior.tlon {:loop {:max-iterations 1}} shortens the ReAct loop."""
        test_paths = _use_tmp_paths(monkeypatch, tmp_path)
        _write_behavior(test_paths, "a-001", dumps(_full_data(loop=":max-iterations 1")))

        llm = MagicMock()
        llm.chat = AsyncMock(
            return_value=LLMResponse(
                content=None,
                tool_calls=[ToolCall(id="tc-1", function_name="testTool", arguments={})],
            )
        )
        agent = _make_agent(llm_client=llm)

        mock_tool = MagicMock()
        mock_tool.name = "testTool"
        mock_tool.execute = AsyncMock(return_value=ToolResult(success=True, data={}))
        agent.tool_executor.register_tool(mock_tool)

        msg = Mail.create(
            from_address="user-001@tlon.internal",
            to=["a-001@tlon.internal"],
            content="hi",
        )
        await agent._process_message(msg)

        # Gave up after 1 iteration with the max-iterations message
        message_events = [
            c
            for c in agent.event_bus.publish.await_args_list
            if c.args[0].type == EventType.MAIL_NEW
        ]
        assert len(message_events) == 1
        assert "简化" in message_events[0].args[0].payload.content
        # LLM was called exactly once (second step hit the iteration limit)
        assert llm.chat.await_count == 1

    async def test_trigger_on_mention_gate(self, tmp_path, monkeypatch):
        """behavior.tlon {:trigger {:on-mention false}} mutes the agent."""
        test_paths = _use_tmp_paths(monkeypatch, tmp_path)
        _write_behavior(
            test_paths, "a-001", dumps(_full_data(trigger=":on-mention false"))
        )
        agent = _make_agent(config=AgentConfig(id="a-001", name="TestAgent"))

        addressed = Mail.create(
            from_address="user-001@tlon.internal",
            to=["a-001@tlon.internal"],
            content="hello",
        )
        assert agent._should_respond(addressed) is False

        cc_only = Mail.create(
            from_address="user-001@tlon.internal",
            to=["other@tlon.internal"],
            content="hello",
        )
        assert agent._should_respond(cc_only) is False

    async def test_mail_history_limit_from_spec(self, tmp_path, monkeypatch):
        test_paths = _use_tmp_paths(monkeypatch, tmp_path)
        _write_behavior(
            test_paths,
            "a-001",
            dumps(_full_data(context=":sections [(mail-history 5)]")),
        )

        mailbox = AsyncMock()
        mailbox.recent = AsyncMock(return_value=[])
        agent = _make_agent(mailbox=mailbox)

        msg = Mail.create(
            from_address="user-001@tlon.internal",
            to=["a-001@tlon.internal"],
            content="hi",
        )
        await agent._build_context(msg)
        mailbox.recent.assert_awaited_once_with(limit=5)

    def test_reflect_interval_from_spec(self, tmp_path, monkeypatch):
        test_paths = _use_tmp_paths(monkeypatch, tmp_path)
        _write_behavior(
            test_paths,
            "a-001",
            dumps(
                _full_data(
                    on_complete=":reflect {:enabled false :interval-minutes 7}"
                )
            ),
        )
        agent = _make_agent()
        assert agent._reflection.enabled is False
        assert agent._reflection._interval == 7 * 60


class TestLoopStagesAndUntil:
    """LoopSpec stage vector + until expression (phase 7a)."""

    def test_standard_program_is_react(self):
        spec = _spec()
        assert spec.loop.stages == REACT_STAGES
        assert spec.loop.until == REACT_UNTIL
        assert dumps(spec.loop.until) == "(or (iteration-limit-reached?) (error-limit-reached?))"

    def test_custom_stages_and_until(self):
        spec = _spec(loop=":stages [(think)] :until (error-limit-reached?)")
        assert spec.loop.stages == (loads("(think)"),)
        assert spec.loop.until == loads("(error-limit-reached?)")

    def test_unknown_stage_rejected(self):
        with pytest.raises(SpecValidationError, match="unknown stage"):
            _spec(loop=":stages [(dance)]")

    def test_stage_unknown_opts_key_rejected(self):
        with pytest.raises(SpecValidationError, match="unknown opts key"):
            _spec(loop=":stages [(act {:parallel true})]")

    def test_stage_opts_keys_must_be_keywords(self):
        with pytest.raises(SpecValidationError, match="keywords"):
            _spec(loop=':stages [(act {"parallel" true})]')

    def test_stage_item_must_be_form(self):
        with pytest.raises(SpecValidationError, match="stage forms"):
            _spec(loop=":stages [think]")

    def test_empty_stages_rejected(self):
        with pytest.raises(SpecValidationError, match="must not be empty"):
            _spec(loop=":stages []")

    def test_stages_must_be_vector(self):
        with pytest.raises(SpecValidationError, match="must be a vector"):
            _spec(loop=":stages (think)")

    def test_invalid_until_expression_rejected(self):
        with pytest.raises(SpecValidationError, match="invalid :loop/:until"):
            _spec(loop=":until (while true)")

    def test_until_must_be_expression(self):
        with pytest.raises(SpecValidationError, match=":loop/:until must be an expression"):
            _spec(loop=":until 42")

    def test_until_undefined_symbol_rejected(self):
        with pytest.raises(SpecValidationError, match="undefined symbol"):
            _spec(loop=":until (iteration-limit-reached? foo)")

    def test_dict_roundtrip_preserves_stages_and_until(self):
        spec = _spec(loop=":stages [(think)] :until (not (error-limit-reached?))")
        assert AgentSpec.from_dict(spec.to_dict()) == spec

    def test_standard_program_dict_roundtrip(self):
        spec = _spec()
        assert AgentSpec.from_dict(spec.to_dict()) == spec

    def test_unknown_loop_key_still_warns(self):
        with patch("tlon.agents.spec.logger") as mock_logger:
            spec = _spec(loop=":bogus 1")
        assert spec.loop.stages == REACT_STAGES
        warning_events = [c.args[0] for c in mock_logger.warning.call_args_list]
        assert "agent_spec_unknown_key" in warning_events


class TestStageOpts:
    """Stage opts and ``:if`` conditions (phase 7c)."""

    def test_new_stage_names_accepted(self):
        spec = _spec(loop=":stages [(plan) (think) (act) (critique) (revise)]")
        assert [dumps(form) for form in spec.loop.stages] == [
            "(plan)",
            "(think)",
            "(act)",
            "(critique)",
            "(revise)",
        ]

    def test_if_condition_accepted_on_all_stages(self):
        spec = _spec(
            loop=":stages [(think {:if (first-iteration?)}) "
            "(act {:if (plan-exists?)}) "
            "(plan {:if (not (plan-exists?))}) "
            "(critique {:if (= 1 1)}) "
            "(revise {:if (critique-failed?)})]"
        )
        assert len(spec.loop.stages) == 5

    def test_if_invalid_expression_rejected(self):
        with pytest.raises(SpecValidationError, match=":if"):
            _spec(loop=":stages [(revise {:if (unknown-predicate?)})]")

    def test_if_undefined_symbol_rejected(self):
        with pytest.raises(SpecValidationError, match="undefined symbol"):
            _spec(loop=":stages [(plan {:if (plan-exists? foo)})]")

    def test_if_on_loop_only_predicate_rejected(self):
        # until predicates are not stage conditions
        with pytest.raises(SpecValidationError, match=":if"):
            _spec(loop=":stages [(think {:if (iteration-limit-reached?)})]")

    def test_plan_max_steps_accepted(self):
        spec = _spec(loop=":stages [(plan {:max-steps 3})]")
        assert dumps(spec.loop.stages[0]) == "(plan {:max-steps 3})"

    def test_plan_max_steps_must_be_positive_int(self):
        for bad in ("0", "-1", "true", '"3"'):
            with pytest.raises(SpecValidationError, match=":max-steps"):
                _spec(loop=f":stages [(plan {{:max-steps {bad}}})]")

    def test_max_steps_rejected_on_other_stages(self):
        with pytest.raises(SpecValidationError, match="unknown opts key"):
            _spec(loop=":stages [(think {:max-steps 3})]")

    def test_unknown_opts_key_rejected(self):
        with pytest.raises(SpecValidationError, match="unknown opts key"):
            _spec(loop=':stages [(critique {:llm "self"})]')

    def test_dict_roundtrip_preserves_stage_opts(self):
        spec = _spec(
            loop=":stages [(plan {:max-steps 3 :if (first-iteration?)}) "
            "(think) (act) (critique) (revise {:if (critique-failed?)})]"
        )
        assert AgentSpec.from_dict(spec.to_dict()) == spec


class TestContextSections:
    """ContextSpec section vector (phase 7b)."""

    def test_standard_program_has_all_sections(self):
        spec = _spec()
        assert spec.context.sections == DEFAULT_SECTIONS
        assert [dumps(form) for form in spec.context.sections] == [
            "(persona)",
            "(protocol)",
            "(knowledge)",
            "(mail-history 20)",
        ]

    def test_custom_sections_preserve_order(self):
        spec = _spec(context=":sections [(mail-history 5) (knowledge)]")
        assert spec.context.sections == (loads("(mail-history 5)"), loads("(knowledge)"))

    def test_unknown_section_rejected(self):
        with pytest.raises(SpecValidationError, match="unknown section"):
            _spec(context=":sections [(memory)]")

    def test_duplicate_section_rejected(self):
        with pytest.raises(SpecValidationError, match="duplicate section"):
            _spec(context=":sections [(knowledge) (knowledge)]")

    def test_section_item_must_be_form(self):
        with pytest.raises(SpecValidationError, match="section forms"):
            _spec(context=":sections [persona]")

    def test_section_head_must_be_symbol(self):
        with pytest.raises(SpecValidationError, match="must be a symbol"):
            _spec(context=":sections [(:persona)]")

    def test_sections_must_be_vector(self):
        with pytest.raises(SpecValidationError, match="must be a vector"):
            _spec(context=":sections (persona)")

    @pytest.mark.parametrize(
        "fragment",
        [
            ":sections [(persona 1)]",
            ":sections [(protocol :full)]",
            ":sections [(knowledge 5)]",
        ],
    )
    def test_flag_sections_take_no_arguments(self, fragment):
        with pytest.raises(SpecValidationError, match="takes no arguments"):
            _spec(context=fragment)

    @pytest.mark.parametrize(
        "fragment",
        [
            ":sections [(mail-history)]",
            ":sections [(mail-history 5 10)]",
            ":sections [(mail-history -1)]",
            ":sections [(mail-history true)]",
            ':sections [(mail-history "5")]',
        ],
    )
    def test_mail_history_argument_shape(self, fragment):
        with pytest.raises(SpecValidationError, match="mail-history"):
            _spec(context=fragment)

    def test_dict_roundtrip_preserves_sections(self):
        spec = _spec(context=":sections [(mail-history 5) (knowledge)]")
        assert AgentSpec.from_dict(spec.to_dict()) == spec


class TestContextBuilderSections:
    """ContextBuilder interprets the section vector in order (phase 7b)."""

    def _memory_with_knowledge(self, content: str = "记忆内容") -> AsyncMock:
        memory = AsyncMock()
        block = MagicMock()
        block.to_openai_message.return_value = {"content": content}
        memory.build_context_blocks = AsyncMock(return_value=[block])
        return memory

    def _mail(self, from_address: str, content: str) -> Mail:
        return Mail.create(
            from_address=from_address, to=["a-001@tlon.internal"], content=content
        )

    async def test_default_program_matches_legacy_assembly(self, tmp_path, monkeypatch):
        """Standard sections reproduce the pre-7b context byte for byte."""
        test_paths = _use_tmp_paths(monkeypatch, tmp_path)
        _write_behavior(test_paths, "a-001", standard_behavior_edn())
        own = Mail.create(
            from_address="a-001@tlon.internal",
            to=["user-001@tlon.internal"],
            content="我发的",
        )
        other = self._mail("user-001@tlon.internal", "他发的")
        mailbox = AsyncMock()
        mailbox.recent = AsyncMock(return_value=[own, other])
        agent = _make_agent(mailbox=mailbox, memory=self._memory_with_knowledge())

        current = self._mail("user-001@tlon.internal", "当前")
        context = await agent._build_context(current)

        mailbox.recent.assert_awaited_once_with(limit=20)
        assert context == [
            {"role": "user", "content": "[已知信息]\n\n记忆内容"},
            {
                "role": "assistant",
                "content": "From: a-001@tlon.internal\nTo: user-001@tlon.internal\n\n我发的",
            },
            {
                "role": "user",
                "content": "From: user-001@tlon.internal\nTo: a-001@tlon.internal\n\n他发的",
            },
            {
                "role": "user",
                "content": "From: user-001@tlon.internal\nTo: a-001@tlon.internal\n\n当前",
            },
        ]

    async def test_sections_interpreted_in_vector_order(self, tmp_path, monkeypatch):
        """(mail-history) before (knowledge) puts history ahead of knowledge."""
        test_paths = _use_tmp_paths(monkeypatch, tmp_path)
        _write_behavior(
            test_paths,
            "a-001",
            dumps(_full_data(context=":sections [(mail-history 10) (knowledge)]")),
        )
        mailbox = AsyncMock()
        mailbox.recent = AsyncMock(return_value=[self._mail("user-001@tlon.internal", "历史")])
        agent = _make_agent(mailbox=mailbox, memory=self._memory_with_knowledge())

        current = self._mail("user-001@tlon.internal", "当前")
        context = await agent._build_context(current)

        mailbox.recent.assert_awaited_once_with(limit=10)
        assert [m["role"] for m in context] == ["user", "user", "user"]
        assert "历史" in context[0]["content"]
        assert context[1]["content"] == "[已知信息]\n\n记忆内容"
        assert "当前" in context[2]["content"]  # current mail always last

    async def test_empty_sections_yield_current_mail_only(self, tmp_path, monkeypatch):
        test_paths = _use_tmp_paths(monkeypatch, tmp_path)
        _write_behavior(
            test_paths, "a-001", dumps(_full_data(context=":sections []"))
        )
        mailbox = AsyncMock()
        agent = _make_agent(mailbox=mailbox, memory=self._memory_with_knowledge())

        current = self._mail("user-001@tlon.internal", "当前")
        context = await agent._build_context(current)

        mailbox.recent.assert_not_awaited()
        assert len(context) == 1
        assert "当前" in context[0]["content"]

    async def _system_prompt_flags(self, agent: Agent) -> dict[str, bool]:
        with (
            patch.object(agent, "_build_prompt_context", new_callable=AsyncMock) as mock_ctx,
            patch("tlon.agents.context_builder.SystemPromptBuilder") as mock_builder_cls,
        ):
            mock_ctx.return_value = MagicMock()
            mock_builder_cls.return_value.build.return_value = "prompt"
            await agent._get_system_prompt()
        return mock_builder_cls.call_args.kwargs

    async def test_default_program_enables_persona_and_protocol(
        self, tmp_path, monkeypatch
    ):
        test_paths = _use_tmp_paths(monkeypatch, tmp_path)
        _write_behavior(test_paths, "a-001", standard_behavior_edn())
        agent = _make_agent()
        flags = await self._system_prompt_flags(agent)
        assert flags == {"include_persona": True, "include_protocol": True}

    async def test_sections_gate_system_prompt_flags(self, tmp_path, monkeypatch):
        test_paths = _use_tmp_paths(monkeypatch, tmp_path)
        _write_behavior(
            test_paths,
            "a-001",
            dumps(_full_data(context=":sections [(knowledge)]")),
        )
        agent = _make_agent()
        flags = await self._system_prompt_flags(agent)
        assert flags == {"include_persona": False, "include_protocol": False}
