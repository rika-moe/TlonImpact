"""Model setup uses real member configs and never exposes provider credentials."""

import asyncio
import json
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from dotenv import dotenv_values
from structlog.testing import capture_logs

from tlon.agents.bootstrap import bootstrap_society
from tlon.api.config import ConfigFacade
from tlon.api.members import MembersFacade
from tlon.api.model_setup import ModelSetupFacade, SetupError
from tlon.base.config import ConfigService, LLMConfig, yaml_save
from tlon.base.events.event_bus import EventBus
from tlon.base.llm_client import LLMClient
from tlon.base.paths import TlonPaths
from tlon.base.settings import load_process_env
from tlon_server.api.rest import RESTAPIHandler
from tlon_server.core.local_identity import LocalIdentity

KEY = "test-private-key-not-in-responses"
BODY: dict[str, Any] = {
    "provider": "openai",
    "model": "openai/gpt-4o-mini",
    "api_key": KEY,
    "scope": "initial",
    "agent_ids": [],
}


@pytest.fixture
async def setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[ModelSetupFacade]:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    paths = TlonPaths(tmp_path)
    bootstrap_society(paths)
    bus = EventBus()
    await bus.initialize()
    service = ConfigService(bus, tmp_path)
    await service.initialize()
    members = MagicMock(spec=MembersFacade)

    async def adopt(agent_id: str) -> str:
        assert await service.reload_agent(agent_id)
        return "already-known"

    members.adopt = AsyncMock(side_effect=adopt)
    members.get_status = AsyncMock(return_value={"resident": False})
    members.wait = AsyncMock(return_value=True)
    facade = ModelSetupFacade(
        paths, ConfigFacade(config_service=service, tlon_paths=paths), members
    )
    yield facade
    await service.shutdown()
    await bus.shutdown()


async def test_initial_thirty_members_persist_and_key_becomes_live(
    setup: ModelSetupFacade, monkeypatch: pytest.MonkeyPatch
) -> None:
    call = AsyncMock()
    monkeypatch.setattr("litellm.acompletion", call)
    assert setup.status()["required"]
    assert len(setup.status()["initial_member_ids"]) == 5
    setup._members.get_status = AsyncMock(return_value={"resident": True})
    response = await setup.configure(BODY)
    assert setup._members.wait.await_count == 5
    assert response["pending_members"] == []
    assert response["required"] is False
    assert len(response["members"]) == 5
    assert all(member["model"] == BODY["model"] for member in response["members"])
    assert KEY not in json.dumps(response)
    assert os.environ["OPENAI_API_KEY"] == KEY
    assert dotenv_values(setup._paths.env_file())["OPENAI_API_KEY"] == KEY
    assert call.call_args.kwargs["api_key"] == KEY
    for member in response["members"]:
        cfg = setup._configs.load_agent(member["id"])
        assert cfg is not None and cfg.llm is not None
        assert cfg.llm.model_name == BODY["model"]
        assert cfg.llm.context_window is None and cfg.llm.api_base is None
    again = ModelSetupFacade(setup._paths, setup._configs, setup._members)
    assert again.status()["required"] is False
    setup._paths.env_file().unlink()
    assert again.status()["required"] is False  # Process environment remains effective.
    monkeypatch.delenv("OPENAI_API_KEY")
    assert again.status()["required"] is True


async def test_initial_scope_preserves_extra_members_and_resets_selected_model_overrides(
    setup: ModelSetupFacade, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("litellm.acompletion", AsyncMock())
    initial = setup._configs.load_agent(setup._initial_ids[0])
    assert initial is not None
    initial.llm = LLMConfig(
        model_name="deepseek/deepseek-chat", api_base="https://old.example", context_window=12345
    )
    yaml_save(initial, setup._paths.tlon_home())
    extra = initial.model_copy(update={"id": "m-" + "a" * 32, "name": "Existing extra"})
    yaml_save(extra, setup._paths.tlon_home())
    await setup._members.adopt(extra.id)
    response = await setup.configure(BODY)
    assert len(response["members"]) == 6
    saved = setup._configs.load_agent(initial.id)
    untouched = setup._configs.load_agent(extra.id)
    assert saved is not None and saved.llm is not None
    assert saved.llm.model_name == BODY["model"]
    assert saved.llm.api_base is None and saved.llm.context_window is None
    assert untouched is not None and untouched.llm == extra.llm


async def test_resident_model_changes_are_reported_as_pending(
    setup: ModelSetupFacade, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("litellm.acompletion", AsyncMock())
    await setup.configure(BODY)
    setup._members.get_status = AsyncMock(return_value={"resident": True})
    setup._members.wait.reset_mock()
    selected = setup._initial_ids[0]
    result = await setup.configure({**BODY, "scope": "selected", "agent_ids": [selected]})
    assert result["pending_members"] == [selected]
    setup._members.wait.assert_not_awaited()


@pytest.mark.parametrize(
    "patch",
    [
        {"api_key": ""},
        {"api_key": "key\nOTHER=bad"},
        {"api_key": "key\rINJECT"},
        {"api_key": "${SECRET}"},
        {"provider": "openai\nEVIL"},
        {"provider": "unknown"},
        {"model": "deepseek/deepseek-chat"},
        {"model": "openai/not-a-known-model-934343"},
        {"scope": "all"},
        {"scope": "selected", "agent_ids": ["missing"]},
        {"scope": "selected", "agent_ids": []},
    ],
)
async def test_invalid_values_do_not_call_provider_or_write(
    setup: ModelSetupFacade, monkeypatch: pytest.MonkeyPatch, patch: dict[str, Any]
) -> None:
    call = AsyncMock()
    monkeypatch.setattr("litellm.acompletion", call)
    with pytest.raises(SetupError):
        await setup.configure({**BODY, **patch})
    call.assert_not_awaited()
    assert not setup._paths.env_file().exists()
    assert setup.status()["required"]


async def test_validation_failure_does_not_save_or_leak_secret(
    setup: ModelSetupFacade,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    failure = RuntimeError(KEY)
    failure.__cause__ = ModuleNotFoundError(KEY)
    monkeypatch.setattr("litellm.acompletion", AsyncMock(side_effect=failure))
    with capture_logs() as events, pytest.raises(SetupError) as error:
        await setup.configure(BODY)
    assert events == [
        {
            "event": "model_setup_connection_failed",
            "log_level": "warning",
            "provider": "openai",
            "exception_type": "RuntimeError",
            "cause_type": "ModuleNotFoundError",
        }
    ]
    assert KEY not in json.dumps(events)
    assert KEY not in str(error.value)
    output = capsys.readouterr()
    assert KEY not in caplog.text + output.out + output.err
    assert not setup._paths.env_file().exists()
    assert os.environ["OPENAI_API_KEY"] == ""


async def test_explicit_scope_does_not_overwrite_other_models(
    setup: ModelSetupFacade, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("litellm.acompletion", AsyncMock())
    await setup.configure(BODY)
    with pytest.raises(SetupError):
        await setup.configure(BODY)  # Initial scope is never a later blanket reset.
    member_id = setup.status()["members"][0]["id"]
    result = await setup.configure(
        {**BODY, "model": "openai/gpt-4o", "scope": "selected", "agent_ids": [member_id]}
    )
    assert sum(member["model"] == "openai/gpt-4o" for member in result["members"]) == 1
    result = await setup.configure({**BODY, "api_key": "replacement-key", "scope": "selected"})
    assert sum(member["model"] == "openai/gpt-4o" for member in result["members"]) == 1
    assert os.environ["OPENAI_API_KEY"] == "replacement-key"


@pytest.mark.parametrize("scope", ["initial", "selected"])
async def test_partial_apply_is_not_marked_complete_and_retry_finishes(
    setup: ModelSetupFacade, monkeypatch: pytest.MonkeyPatch, scope: str
) -> None:
    monkeypatch.setattr("litellm.acompletion", AsyncMock())
    body = {
        **BODY,
        "scope": scope,
        "agent_ids": [setup._initial_ids[0]] if scope == "selected" else [],
    }
    adopt = setup._members.adopt
    setup._members.adopt = AsyncMock(side_effect=RuntimeError("adoption failed"))
    with pytest.raises(SetupError, match="未全部完成"):
        await setup.configure(body)
    assert setup.status()["required"]
    assert not setup._marker.exists()
    assert setup._pending.exists()
    setup._members.adopt = adopt
    reopened = ModelSetupFacade(setup._paths, setup._configs, setup._members)
    assert reopened.status()["required"] and reopened.status()["initial_allowed"]
    assert not (await reopened.configure(body))["required"]
    assert not reopened._pending.exists()


async def test_key_updates_are_serialized_and_validation_does_not_change_live_key(
    setup: ModelSetupFacade, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered, release = asyncio.Event(), asyncio.Event()
    calls: list[str] = []

    async def verify(**kwargs: Any) -> None:
        calls.append(kwargs["api_key"])
        if len(calls) == 1:
            entered.set()
            await release.wait()

    monkeypatch.setattr("litellm.acompletion", verify)
    first = asyncio.create_task(setup.configure(BODY))
    await entered.wait()
    second = asyncio.create_task(
        setup.configure({**BODY, "api_key": "second-key", "scope": "selected"})
    )
    await asyncio.sleep(0)
    assert calls == [KEY] and os.environ["OPENAI_API_KEY"] == ""
    release.set()
    await asyncio.gather(first, second)
    assert calls == [KEY, "second-key"]
    assert os.environ["OPENAI_API_KEY"] == "second-key"
    assert dotenv_values(setup._paths.env_file())["OPENAI_API_KEY"] == "second-key"


async def test_http_route_requires_service_and_is_origin_protected(
    setup: ModelSetupFacade, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("litellm.acompletion", AsyncMock())
    identity = LocalIdentity(setup._paths.webui_identity_file())
    identity.initialize("alice")
    for service in (None, setup):
        handler = RESTAPIHandler(None, identity=identity, model_setup=service)
        async with TestClient(TestServer(handler.create_app())) as client:
            response = await client.post("/admin/llm/setup", json=BODY)
            assert response.status == 403
            response = await client.post(
                "/admin/llm/setup",
                json=BODY,
                headers={"X-Tlon-WebUI": "1", "Origin": "https://evil.example"},
            )
            assert response.status == 403
            response = await client.get("/admin/llm/setup", headers={"X-Tlon-WebUI": "1"})
            assert (await response.json())["enabled"] is (service is not None)
            response = await client.post(
                "/admin/llm/setup", json=BODY, headers={"X-Tlon-WebUI": "1"}
            )
            assert response.status == (404 if service is None else 200)
            assert KEY not in await response.text()


@pytest.mark.parametrize("accepted", [True, False])
async def test_real_litellm_connection_and_provider_error_redaction(
    setup: ModelSetupFacade,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
    accepted: bool,
) -> None:
    requests: list[str] = []
    existing_client = LLMClient(LLMConfig(model_name=BODY["model"]))

    async def completion(request: web.Request) -> web.Response:
        authorization = request.headers["Authorization"]
        requests.append(authorization)
        assert authorization in {f"Bearer {KEY}", "Bearer rotated-key"}
        if not accepted:
            return web.json_response(
                {"error": {"message": f"invalid {KEY}", "type": "invalid_api_key"}}, status=401
            )
        return web.json_response(
            {
                "id": "test",
                "object": "chat.completion",
                "created": 1,
                "model": "gpt-4o-mini",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "OK"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
        )

    app = web.Application()
    app.router.add_post("/v1/chat/completions", completion)
    async with TestServer(app) as server:
        monkeypatch.setenv("OPENAI_API_BASE", str(server.make_url("/v1")))
        if accepted:
            assert not (await setup.configure(BODY))["required"]
            await existing_client.chat("", [{"role": "user", "content": "hello"}])
            await setup.configure({**BODY, "api_key": "rotated-key", "scope": "selected"})
            await existing_client.chat("", [{"role": "user", "content": "again"}])
        else:
            with pytest.raises(SetupError) as error:
                await setup.configure(BODY)
            assert KEY not in str(error.value)
        assert requests == (
            [f"Bearer {KEY}"] * 2 + ["Bearer rotated-key"] * 2 if accepted else [f"Bearer {KEY}"]
        )
    output = capsys.readouterr()
    assert KEY not in caplog.text + output.out + output.err


@pytest.mark.parametrize("damage", ["missing-key", "empty-key", "malformed-marker"])
async def test_credential_repair_never_reauthorizes_initial_scope(
    setup: ModelSetupFacade, monkeypatch: pytest.MonkeyPatch, damage: str
) -> None:
    monkeypatch.setattr("litellm.acompletion", AsyncMock())
    await setup.configure(BODY)
    if damage == "missing-key":
        monkeypatch.delenv("OPENAI_API_KEY")
    elif damage == "empty-key":
        monkeypatch.setenv("OPENAI_API_KEY", "")
    else:
        setup._marker.write_text("invalid", encoding="utf-8")
    # Reopening the facade proves the restriction comes from persisted history.
    reopened = ModelSetupFacade(setup._paths, setup._configs, setup._members)
    state = reopened.status()
    assert state["required"] is True
    assert state["initial_allowed"] is False
    assert state["initial_member_ids"] == []
    before = state["members"]
    with pytest.raises(SetupError, match="首次配置范围不可"):
        await reopened.configure(BODY)
    restored = await reopened.configure({**BODY, "scope": "selected", "api_key": "repaired-key"})
    assert restored["required"] is False
    assert restored["initial_allowed"] is False
    assert restored["members"] == before
    assert os.environ["OPENAI_API_KEY"] == "repaired-key"


async def test_first_setup_waits_for_old_resident_and_busy_drain_remains_retryable(
    setup: ModelSetupFacade, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("litellm.acompletion", AsyncMock())
    setup._members.get_status = AsyncMock(return_value={"resident": True})
    setup._members.wait = AsyncMock(return_value=False)
    with pytest.raises(SetupError, match="成员仍在完成"):
        await setup.configure(BODY)
    assert setup._members.wait.await_count == 5
    assert not setup._marker.exists()
    assert setup.status()["required"] and setup.status()["initial_allowed"]
    setup._members.wait = AsyncMock(return_value=True)
    result = await setup.configure(BODY)
    assert not result["required"] and result["pending_members"] == []
    assert setup._members.wait.await_count == 5


@pytest.mark.parametrize("source", ["environment", "dotenv", "override"])
async def test_existing_credentials_skip_setup_without_writes_or_verification(
    setup: ModelSetupFacade, monkeypatch: pytest.MonkeyPatch, source: str
) -> None:
    call = AsyncMock()
    monkeypatch.setattr("litellm.acompletion", call)
    monkeypatch.delenv("DEEPSEEK_API_KEY")
    if source != "environment":
        setup._paths.env_file().write_text("DEEPSEEK_API_KEY=file-key\n", encoding="utf-8")
    if source != "dotenv":
        monkeypatch.setenv("DEEPSEEK_API_KEY", "environment-key")
    load_process_env(setup._paths)
    before = {p: p.read_bytes() for p in setup._paths.tlon_home().rglob("*") if p.is_file()}
    reopened = ModelSetupFacade(setup._paths, setup._configs, setup._members)
    state = reopened.status()
    assert not state["required"] and not state["initial_allowed"]
    assert state["initial_member_ids"] == []
    assert "key" not in json.dumps(state)
    assert os.environ["DEEPSEEK_API_KEY"] == (
        "file-key" if source == "dotenv" else "environment-key"
    )
    assert before == {p: p.read_bytes() for p in setup._paths.tlon_home().rglob("*") if p.is_file()}
    call.assert_not_awaited()
    with pytest.raises(SetupError, match="首次配置范围不可"):
        await reopened.configure(BODY)
    result = await reopened.configure({**BODY, "scope": "selected"})
    assert result["members"] == state["members"]
    assert not result["required"]
    setup._members.adopt.assert_not_awaited()


async def test_existing_provider_uses_runtime_key_naming(
    setup: ModelSetupFacade, monkeypatch: pytest.MonkeyPatch
) -> None:
    member = setup._configs.load_agent(setup._initial_ids[0])
    assert member is not None
    member.llm = LLMConfig(model_name="custom-provider/example", context_window=8192)
    yaml_save(member, setup._paths.tlon_home())
    await setup._members.adopt(member.id)
    monkeypatch.setenv("CUSTOM_PROVIDER_API_KEY", "custom-key")
    reopened = ModelSetupFacade(setup._paths, setup._configs, setup._members)
    assert not reopened.status()["required"]
    assert not reopened.status()["initial_allowed"]
    monkeypatch.delenv("CUSTOM_PROVIDER_API_KEY")
    assert reopened.status()["required"]
    assert not reopened.status()["initial_allowed"]


async def test_unrelated_credentials_do_not_skip_new_setup(
    setup: ModelSetupFacade, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "unrelated-key")
    reopened = ModelSetupFacade(setup._paths, setup._configs, setup._members)
    assert reopened.status()["required"] and reopened.status()["initial_allowed"]


async def test_completed_setup_uses_effective_environment_override(
    setup: ModelSetupFacade, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("litellm.acompletion", AsyncMock())
    await setup.configure(BODY)
    monkeypatch.setenv("OPENAI_API_KEY", "external-override")
    load_process_env(setup._paths)
    reopened = ModelSetupFacade(setup._paths, setup._configs, setup._members)
    assert not reopened.status()["required"]
    assert not reopened.status()["initial_allowed"]
    assert os.environ["OPENAI_API_KEY"] == "external-override"


async def test_empty_instance_allows_entry_and_key_only_configuration(
    setup: ModelSetupFacade, monkeypatch: pytest.MonkeyPatch
) -> None:
    configs = MagicMock(spec=ConfigFacade)
    configs.list_agent_configs.return_value = []
    reopened = ModelSetupFacade(setup._paths, configs, setup._members)
    assert not reopened.status()["required"]
    assert not reopened.status()["initial_allowed"]
    monkeypatch.setattr("litellm.acompletion", AsyncMock())
    result = await reopened.configure({**BODY, "scope": "selected"})
    assert not result["required"] and result["members"] == []
    configs.update_llm.assert_not_called()


async def test_removing_all_members_does_not_trap_existing_session(
    setup: ModelSetupFacade,
) -> None:
    configs = MagicMock(spec=ConfigFacade)
    configs.list_agent_configs.return_value = setup._configs.list_agent_configs()
    reopened = ModelSetupFacade(setup._paths, configs, setup._members)
    assert reopened.status()["required"]
    configs.list_agent_configs.return_value = []
    assert not reopened.status()["required"]
    assert not reopened.status()["initial_allowed"]


async def test_removing_members_after_partial_setup_allows_entry_and_retry(
    setup: ModelSetupFacade, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("litellm.acompletion", AsyncMock())
    setup._members.adopt = AsyncMock(side_effect=RuntimeError("adoption failed"))
    with pytest.raises(SetupError, match="未全部完成"):
        await setup.configure(BODY)
    assert setup._pending.exists()

    configs = MagicMock(spec=ConfigFacade)
    configs.list_agent_configs.return_value = []
    reopened = ModelSetupFacade(setup._paths, configs, setup._members)
    assert not reopened.status()["required"]
    assert not reopened.status()["initial_allowed"]
    assert reopened._pending.exists()

    result = await reopened.configure({**BODY, "scope": "selected"})
    assert not result["required"] and result["members"] == []
    assert not reopened._pending.exists()
    configs.update_llm.assert_not_called()
