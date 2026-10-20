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

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from tlon.agents.assembly import _build_llm_config, assemble_member_capabilities
from tlon.base.config import (
    DEFAULT_CONTEXT_WINDOW,
    AgentConfig,
    DockerRuntimeConfig,
    LLMConfig,
)
from tlon.base.paths import TlonPaths

_MODEL = "moonshot/kimi-k2.5"


def _file(**overrides: object) -> LLMConfig:
    return LLMConfig(model_name=_MODEL, **overrides)  # type: ignore[arg-type]


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
            _build_llm_config(LLMConfig(max_tokens=2048), "member-1")

    def test_declared_model_passes_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        get_model_info = MagicMock(return_value={"max_input_tokens": 64000})
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
            "litellm.get_model_info", MagicMock(return_value={"max_input_tokens": 64000})
        )

        built = _build_llm_config(
            _file(max_tokens=2048, top_p=0.5, api_base="https://proxy.example/v1"),
            "member-1",
        )

        assert built.max_tokens == 2048
        assert built.top_p == 0.5
        assert built.api_base == "https://proxy.example/v1"


# The device-dependent tools: the bash pair is L2-only, the filesystem
# five are also registered at L1 (文件层, issue #40); all seven are
# withheld from members declaring docker.level: L0 (issue #26).
BASH_TOOLS = frozenset({"executeBashCommand", "checkBashTask"})
FILE_TOOLS = frozenset(
    {
        "readFile",
        "writeFile",
        "editFile",
        "listDirectory",
        "searchFiles",
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
    """docker.level at assembly (issue #26, L1 in #40): L0 = 无执行装置 —
    no DockerRuntime is constructed and none of the seven device-dependent
    tools is registered; L1 = 文件层 — runtime exists, the filesystem five
    are registered, the bash pair is not; L2 = the status-quo full path."""

    async def _assemble(self, tlon_paths: TlonPaths, level: str) -> Any:
        config = AgentConfig(
            id=f"member-{level.lower()}",
            name=f"member-{level.lower()}",
            llm=LLMConfig(model_name="test/model"),
            docker=DockerRuntimeConfig(level=level),
        )
        return await assemble_member_capabilities(
            config,
            registry=None,  # type: ignore[arg-type]
            agent_info=_FakeAgentInfo({config.id: config}),  # type: ignore[arg-type]
            tlon_paths=tlon_paths,
        )

    async def test_l0_skips_runtime_and_device_tools(
        self, tlon_paths: TlonPaths, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runtime_ctor = MagicMock(side_effect=AssertionError("L0 must not construct DockerRuntime"))
        monkeypatch.setattr("tlon.agents.assembly.DockerRuntime", runtime_ctor)

        caps = await self._assemble(tlon_paths, "L0")

        runtime_ctor.assert_not_called()
        assert caps.docker_runtime is None
        names = set(caps.tool_executor.list_tools())
        assert names.isdisjoint(DEVICE_TOOLS)
        # 其余工具照常装配
        assert {"getCurrentTime", "rememberFact"} <= names
        # Mail tools bind later in MemberRuntime, with its actual mailbox/PostOffice.
        assert "mail-send" not in names

        await caps.tool_executor.shutdown()

    async def test_l1_registers_file_tools_without_bash(
        self, tlon_paths: TlonPaths, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("tlon.agents.assembly.DockerRuntime", MagicMock())

        caps = await self._assemble(tlon_paths, "L1")

        # 文件 I/O 需要文件柜容器，L1 照常装配 runtime（经审计包装）
        assert caps.docker_runtime is not None
        names = set(caps.tool_executor.list_tools())
        assert names >= FILE_TOOLS
        assert names.isdisjoint(BASH_TOOLS)

        await caps.tool_executor.shutdown()

    async def test_l2_registers_device_tools(self, tlon_paths: TlonPaths) -> None:
        caps = await self._assemble(tlon_paths, "L2")

        names = set(caps.tool_executor.list_tools())
        assert names >= DEVICE_TOOLS

        await caps.tool_executor.shutdown()
