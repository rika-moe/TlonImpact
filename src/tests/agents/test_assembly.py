"""Tests for _build_llm_config's member-level resolution (ADR 0016).

There is no process-level fallback: an agent.yaml without a declared
``llm.model_name`` is a hard error naming the member. Context
window precedence (ADR 0012 phase 2): explicit agent.yaml value >
litellm model registry (``get_model_info().max_input_tokens``) >
DEFAULT_CONTEXT_WINDOW. The built config always carries a resolved int
and a non-None model_name.

Also covers the execution-device level gate (issue #26, L1 in #40): a member
declaring ``docker.level: L0`` assembles without a DockerRuntime and without
the seven device-dependent shell/filesystem tools; ``L1`` assembles the
runtime and the filesystem five, but not the bash pair.
"""

import asyncio
import threading
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from tlon.agents.assembly import _build_llm_config, assemble_member_services
from tlon.base.config import (
    DEFAULT_CONTEXT_WINDOW,
    AgentConfig,
    DockerRuntimeConfig,
    LLMConfig,
)
from tlon.base.paths import TlonPaths

_MODEL = "moonshot/kimi-k2.5"


def _file(**overrides: object) -> LLMConfig:
    return LLMConfig(model_name=_MODEL, max_output_tokens=8192, **overrides)  # type: ignore[arg-type]


class TestContextWindowResolution:
    def test_yaml_explicit_value_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        get_model_info = MagicMock(return_value={"max_input_tokens": 131072})
        monkeypatch.setattr("litellm.get_model_info", get_model_info)
        file_config = _file(context_window=99999)

        built = _build_llm_config(file_config, "member-1")

        assert built.context_window == 99999
        get_model_info.assert_not_called()

    def test_registry_hit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "litellm.get_model_info", MagicMock(return_value={"max_input_tokens": 131072})
        )

        built = _build_llm_config(_file(), "member-1")

        assert built.context_window == 131072

    def test_registry_miss_falls_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "litellm.get_model_info",
            MagicMock(side_effect=Exception("model not in registry")),
        )

        built = _build_llm_config(_file(), "member-1")

        assert built.context_window == DEFAULT_CONTEXT_WINDOW

    def test_registry_entry_without_max_input_tokens_falls_back(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "litellm.get_model_info", MagicMock(return_value={"max_input_tokens": None})
        )

        built = _build_llm_config(_file(), "member-1")

        assert built.context_window == DEFAULT_CONTEXT_WINDOW


class TestModelNameRequired:
    """ADR 0016: no declared ``llm.model_name`` is a hard assembly error
    naming the member — no process-level fallback exists."""

    def test_missing_llm_section_raises(self) -> None:
        with pytest.raises(ValueError, match="member-1"):
            _build_llm_config(None, "member-1")

    def test_llm_section_without_model_raises(self) -> None:
        with pytest.raises(ValueError, match="llm.model_name"):
            _build_llm_config(LLMConfig(), "member-1")

    def test_declared_model_passes_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        get_model_info = MagicMock(
            return_value={"max_input_tokens": 64000, "max_output_tokens": 8192}
        )
        monkeypatch.setattr("litellm.get_model_info", get_model_info)
        file_config = LLMConfig(model_name="deepseek/deepseek-chat")

        built = _build_llm_config(file_config, "member-1")

        assert built.model_name == "deepseek/deepseek-chat"
        # the context window resolves against the declared model
        assert get_model_info.call_args.args[0] == "deepseek/deepseek-chat"


class TestFileValuesTakeEffect:
    """ADR 0016: the file's per-member knobs are the only source."""

    def test_per_member_knobs_and_api_base_pass_through(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "litellm.get_model_info",
            MagicMock(return_value={"max_input_tokens": 64000, "max_output_tokens": 8192}),
        )

        built = _build_llm_config(
            _file(top_p=0.5, api_base="https://proxy.example/v1"),
            "member-1",
        )

        assert built.max_output_tokens == 8192
        assert "max_output_tokens" not in built.model_dump()
        assert built.top_p == 0.5
        assert built.api_base == "https://proxy.example/v1"


# Shell execution is L2-only; every level has its own workspace file tools.
BASH_TOOLS = frozenset({"shell-run", "shell-check"})
FILE_TOOLS = frozenset(
    {
        "file-read",
        "file-write",
        "file-edit",
        "file-list",
        "file-search",
    }
)
DEVICE_TOOLS = BASH_TOOLS | FILE_TOOLS


class _FakeAgentInfo:
    """AgentInfoProvider stand-in over a fixed config roster."""

    def __init__(self, configs: dict[str, AgentConfig]):
        self._configs = configs

    def get_all_ids(self) -> list[str]:
        return sorted(self._configs)

    def get_config(self, agent_id: str) -> AgentConfig | None:
        return self._configs.get(agent_id)


@pytest.fixture
def tlon_paths(tmp_path: Path) -> TlonPaths:
    return TlonPaths(home=tmp_path)


class TestExecutionLevelAssembly:
    """Workspace files are universal; execution devices remain level-bound."""

    async def _assemble(self, tlon_paths: TlonPaths, level: str) -> Any:
        config = AgentConfig(
            id=f"member-{level.lower()}",
            name=f"member-{level.lower()}",
            llm=LLMConfig(model_name="test/model", max_output_tokens=8192, context_window=64000),
            docker=DockerRuntimeConfig(level=level),
        )
        return await assemble_member_services(
            config,
            registry=None,  # type: ignore[arg-type]
            agent_info=_FakeAgentInfo({config.id: config}),  # type: ignore[arg-type]
            tlon_paths=tlon_paths,
        )

    @pytest.mark.parametrize("cancel", [False, True])
    async def test_slow_docker_probe_keeps_loop_responsive_and_owns_late_client(
        self, tlon_paths: TlonPaths, monkeypatch: pytest.MonkeyPatch, cancel: bool
    ) -> None:
        loop = asyncio.get_running_loop()
        started = asyncio.Event()
        release = threading.Event()
        closed = threading.Event()
        client = MagicMock()
        client.close.side_effect = closed.set

        def construct(*args: Any) -> Any:
            loop.call_soon_threadsafe(started.set)
            assert release.wait(timeout=3), "Docker probe blocked the event loop"
            return client

        monkeypatch.setattr("tlon.agents.assembly.DockerRuntime", construct)
        task = asyncio.create_task(self._assemble(tlon_paths, "L2"))
        try:
            await asyncio.wait_for(started.wait(), timeout=2)
            # This coroutine can run while the synchronous probe is still blocked.
            assert not task.done()
            if cancel:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                client.close.assert_not_called()
                release.set()
                assert await asyncio.to_thread(closed.wait, 2)
            else:
                release.set()
                services = await asyncio.wait_for(task, timeout=2)
                client.close.assert_not_called()
                assert services.docker_runtime is not None
                services.docker_runtime.close()
            client.close.assert_called_once()
        finally:
            release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_l0_has_workspace_files_without_runtime(
        self, tlon_paths: TlonPaths, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runtime_ctor = MagicMock(side_effect=AssertionError("L0 must not construct DockerRuntime"))
        monkeypatch.setattr("tlon.agents.assembly.DockerRuntime", runtime_ctor)

        caps = await self._assemble(tlon_paths, "L0")

        runtime_ctor.assert_not_called()
        assert caps.docker_runtime is None
        assert not hasattr(caps, "memory")
        names = set(caps.tool_executor.list_tools()) | {op.name for op in caps.operations}
        assert names & DEVICE_TOOLS == FILE_TOOLS
        # 其余工具照常装配
        assert {"time-now", "run-read"} <= names
        assert not any(name.startswith("memory-") for name in names)
        # Mail tools bind later in MemberRuntime, with its actual mailbox/PostOffice.
        assert "mail-send" not in names

        if caps.shell_service is not None:
            await caps.shell_service.shutdown()
        if caps.docker_runtime is not None:
            caps.docker_runtime.close()

    async def test_l1_registers_file_tools_without_bash(
        self, tlon_paths: TlonPaths, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("tlon.agents.assembly.DockerRuntime", MagicMock())

        caps = await self._assemble(tlon_paths, "L1")

        # L1 保留 workspace 外文件柜装配；自己的 workspace 不依赖它。
        assert caps.docker_runtime is not None
        names = set(caps.tool_executor.list_tools()) | {op.name for op in caps.operations}
        assert names >= FILE_TOOLS
        assert names.isdisjoint(BASH_TOOLS)

        if caps.shell_service is not None:
            await caps.shell_service.shutdown()
        if caps.docker_runtime is not None:
            caps.docker_runtime.close()

    async def test_l2_registers_device_tools(
        self, tlon_paths: TlonPaths, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("tlon.agents.assembly.DockerRuntime", MagicMock())
        caps = await self._assemble(tlon_paths, "L2")

        names = set(caps.tool_executor.list_tools()) | {op.name for op in caps.operations}
        assert names >= DEVICE_TOOLS

        if caps.shell_service is not None:
            await caps.shell_service.shutdown()
        if caps.docker_runtime is not None:
            caps.docker_runtime.close()


async def test_real_assembly_evolution_writes_use_injected_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercise the assembled writer bindings, not hand-built test partials."""

    from tlon.agents.prototypes.staging import list_staging_prototypes
    from tlon.operations.adapters import OperationTool
    from tlon.operations.core import CallContext, OperationExecutor, OperationRegistry

    paths = TlonPaths(home=tmp_path / "member-home")
    other_paths = TlonPaths(home=tmp_path / "other-home")
    spec = "{:version 2 :handlers {:mail-delivered work.reply/handle} :fuel 1000}"
    modules = {
        "work/reply.tlon": "(ns work.reply (:require [work.helpers :as h]) (:export handle)) (defn handle [event] (h/result))",
        "work/helpers.tlon": '(ns work.helpers (:export result)) (defn result [] {:result "ok"})',
    }
    config = AgentConfig(
        id="author",
        name="author",
        llm=LLMConfig(model_name="test/model", max_output_tokens=8192, context_window=64000),
        docker=DockerRuntimeConfig(level="L0"),
    )
    caps = await assemble_member_services(
        config,
        registry=None,
        agent_info=_FakeAgentInfo({config.id: config}),
        tlon_paths=paths,
    )
    executor = OperationExecutor(
        OperationRegistry(caps.operations), CallContext(config.id, "run", "call")
    )
    written = await OperationTool(executor, "prototype-write").execute_context(
        {"behavior": spec, "modules": modules, "display_name": "direct"}
    )
    assert written.success, written.error
    proposals = list_staging_prototypes(paths)
    assert {proposal.name for proposal in proposals} == {
        written.data["name"],
    }
    assert all(proposal.author == config.id and proposal.trusted is False for proposal in proposals)
    for proposal in proposals:
        for relative, source in modules.items():
            assert (
                paths.prototypes_staging_dir() / proposal.name / "modules" / relative
            ).read_text() == source
    assert list_staging_prototypes(other_paths) == []
    assert not other_paths.prototypes_staging_dir().exists()
    assert not paths.prototypes_dir().exists()


@pytest.mark.parametrize("level", ["L0", "L1", "L2"])
async def test_workspace_operations_survive_unavailable_docker(
    tlon_paths: TlonPaths, monkeypatch: pytest.MonkeyPatch, level: str
) -> None:
    from tlon.operations.core import CallContext, OperationExecutor, OperationRegistry

    constructor = MagicMock(side_effect=RuntimeError("Docker deliberately unavailable"))
    monkeypatch.setattr("tlon.agents.assembly.DockerRuntime", constructor)
    caps = await TestExecutionLevelAssembly()._assemble(tlon_paths, level)
    member_id = f"member-{level.lower()}"
    assert caps.docker_runtime is None
    assert caps.shell_service is None
    assert BASH_TOOLS.isdisjoint(caps.tool_executor.list_tools())
    assert BASH_TOOLS.isdisjoint(op.name for op in caps.operations)
    executor = OperationExecutor(
        OperationRegistry(caps.operations), CallContext(member_id, "workspace", "call")
    )
    await executor.call("file-write", {"file_path": "CONTACTS.md", "content": "original"})
    await executor.call(
        "file-edit", {"file_path": "CONTACTS.md", "old_string": "original", "new_string": "current"}
    )
    result = await executor.call("file-read", {"file_path": "CONTACTS.md"})
    assert result["content"] == "current"
    assert tlon_paths.agent_contacts_file(member_id).read_text() == "current"
    assert (await executor.call("file-list", {}))["entries"][0]["name"] == "CONTACTS.md"
    assert (await executor.call("file-search", {"pattern": "current"}))["count"] == 1
    if level == "L0":
        constructor.assert_not_called()


async def test_born_member_runtime_exposes_and_executes_reproduction_tools(tmp_path: Path) -> None:
    from unittest.mock import AsyncMock

    from tests.program_helpers import make_program
    from tlon.agents.member_runtime import MemberRuntime
    from tlon.agents.vocabulary import make_member_builtins
    from tlon.base.events.event_bus import EventBus
    from tlon.operations.core import CallContext

    paths = TlonPaths(home=tmp_path)
    config = AgentConfig(
        id="born",
        name="Born",
        spawned_by="creator",
        prototype="source",
        spawn_depth=1,
        llm=LLMConfig(model_name="test/model", max_output_tokens=8192, context_window=64000),
        docker=DockerRuntimeConfig(level="L0"),
    )
    registry = MagicMock()
    registry.spawn_member = AsyncMock(
        return_value=AgentConfig(
            id="child",
            name="Child",
            prototype="source",
            spawned_by="born",
            spawn_depth=2,
        )
    )
    services = await assemble_member_services(
        config,
        registry=registry,
        agent_info=_FakeAgentInfo({config.id: config}),
        tlon_paths=paths,
    )
    member = MemberRuntime(
        member_id=config.id,
        name=config.name,
        event_bus=EventBus(),
        spec=make_program(),
        builtins=make_member_builtins(),
        services=services,
    )
    assert {"member-spawn", "prototype-write"} <= set(services.tool_executor.list_tools())
    spawned = await services.tool_executor.execute(
        "member-spawn",
        {"prototype": "source", "name": "Child"},
        CallContext("born", "run", "spawn"),
    )
    assert spawned.success, spawned.error
    registry.spawn_member.assert_awaited_once()
    written = await services.tool_executor.execute(
        "prototype-write",
        {
            "behavior": "{:version 2 :handlers {:boot work/handle}}",
            "modules": {"work.tlon": "(ns work (:export handle)) (defn handle [event] {})"},
            "display_name": "born proposal",
        },
        CallContext("born", "run", "proposal"),
    )
    assert written.success, written.error
    assert written.data["trusted"] is False
    await member.shutdown()
