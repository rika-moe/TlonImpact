"""Portable WebUI setup: verified provider credentials and explicit member models."""

import asyncio
import json
import os
import re
from typing import Any

import litellm
from dotenv import dotenv_values, set_key

from tlon.agents.initial_society import _read_state
from tlon.api.config import ConfigFacade
from tlon.api.members import MembersFacade
from tlon.base.config import LLMConfig
from tlon.base.model_limits import resolve_model_limits
from tlon.base.paths import TlonPaths

PROVIDERS = ("deepseek", "openai", "anthropic", "gemini")


class SetupError(ValueError):
    """A safe, user-visible setup failure containing no provider exception text."""


class PortableSetupFacade:
    """Serialize credential checks and writes for one explicitly bound instance."""

    def __init__(self, paths: TlonPaths, configs: ConfigFacade, members: MembersFacade) -> None:
        self._paths = paths
        self._configs = configs
        self._members = members
        self._lock = asyncio.Lock()
        self._marker = paths.tlon_home() / "model-setup.json"
        self._configured_before = self._marker.exists()
        state = _read_state(paths)
        self._initial_ids = (
            tuple(state.addresses.values()) if state and state.status == "complete" else ()
        )

    def status(self) -> dict[str, Any]:
        """Return only non-secret state; persisted credential presence is checked."""
        complete = False
        # Credential repair is not a new initial setup. Even a malformed
        # completion record permanently withdraws blanket scope authorization.
        self._configured_before = self._configured_before or self._marker.exists()
        if self._marker.exists():
            try:
                marker = json.loads(self._marker.read_text(encoding="utf-8"))
                provider = marker["provider"]
                if provider in PROVIDERS:
                    key = dotenv_values(self._paths.env_file(), interpolate=False).get(
                        f"{provider.upper()}_API_KEY"
                    )
                    complete = bool(key and os.getenv(f"{provider.upper()}_API_KEY") == key)
            except (ValueError, KeyError, TypeError, OSError):
                complete = False
        return {
            "enabled": True,
            "required": not complete,
            "providers": list(PROVIDERS),
            "initial_allowed": not self._configured_before,
            "initial_member_ids": [] if self._configured_before else list(self._initial_ids),
            "members": [
                {"id": cfg.id, "name": cfg.name, "model": cfg.llm.model_name if cfg.llm else None}
                for cfg in self._configs.list_agent_configs()
            ],
        }

    async def configure(self, body: Any) -> dict[str, Any]:
        """Validate first, then persist the key and explicitly selected models.

        Disk/env/member writes are retryable; the completion marker is written
        last. A partial failure is reported honestly and never marks setup done.
        Provider errors are deliberately not included in responses or logs.
        """
        if not isinstance(body, dict) or set(body) != {
            "provider",
            "model",
            "api_key",
            "scope",
            "agent_ids",
        }:
            raise SetupError("请提供服务商、模型、API Key 和应用范围。")
        provider, model, key = body["provider"], body["model"], body["api_key"]
        if not isinstance(provider, str) or provider not in PROVIDERS:
            raise SetupError("请选择支持的模型服务商。")
        if (
            not isinstance(model, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{1,199}", model)
            or not model.startswith(provider + "/")
            or not model.removeprefix(provider + "/")
        ):
            raise SetupError("模型必须使用所选服务商的 provider/model 名称。")
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9._~+/=-]{1,4096}", key):
            raise SetupError("API Key 不能为空，且不能包含空白、换行或控制字符。")
        try:
            resolve_model_limits(LLMConfig(model_name=model))
        except ValueError:
            raise SetupError("无法确定此模型的能力，请选择列表中的模型。") from None
        supplied_ids = body["agent_ids"]
        if not isinstance(supplied_ids, list) or any(not isinstance(i, str) for i in supplied_ids):
            raise SetupError("成员范围格式无效。")
        async with self._lock:
            state = self.status()
            required = state["required"]
            if body["scope"] == "initial":
                if not state["initial_allowed"] or not required or supplied_ids:
                    raise SetupError("首次配置范围不可用于覆盖已有成员，请明确选择成员。")
                ids = list(self._initial_ids)
                if not ids:
                    raise SetupError("没有初始成员，请明确选择要配置的成员。")
            elif body["scope"] == "selected":
                ids = list(dict.fromkeys(supplied_ids))
                if state["initial_allowed"] and required and not ids:
                    raise SetupError("首次配置至少需要选择一位成员。")
            else:
                raise SetupError("请选择初始成员或明确的成员列表。")
            known = {cfg.id for cfg in self._configs.list_agent_configs()}
            if not set(ids) <= known:
                raise SetupError("所选成员已不存在，请刷新页面。")
            try:
                await asyncio.wait_for(
                    litellm.acompletion(
                        model=model,
                        api_key=key,
                        messages=[{"role": "user", "content": "Reply with OK."}],
                        max_tokens=32,
                        timeout=20,
                        num_retries=0,
                    ),
                    timeout=25,
                )
            except Exception:
                raise SetupError(
                    "连接验证失败，请检查服务商、模型、API Key 和网络后重试。"
                ) from None
            pending: list[str] = []
            try:
                # dotenv rewrites atomically and quotes values; the input grammar
                # additionally excludes dotenv interpolation and line injection.
                set_key(str(self._paths.env_file()), f"{provider.upper()}_API_KEY", key)
                os.environ[f"{provider.upper()}_API_KEY"] = key
                for agent_id in ids:
                    if (
                        self._configs.update_llm(
                            agent_id, model_name=model, use_provider_defaults=True
                        )
                        is None
                    ):
                        raise SetupError("成员配置保存不完整，请刷新后重试。")
                    result = await self._members.adopt(agent_id)
                    if result in {"config-not-found", "start-failed"}:
                        raise SetupError("成员配置尚未生效，请重试。")
                    member = await self._members.get_status(agent_id)
                    if not state["initial_allowed"] and member and member.get("resident"):
                        pending.append(agent_id)
                if state["initial_allowed"]:
                    # Boot may already have assembled the seeded members with
                    # their original model. wait serializes with boot under the
                    # registry wake lock and drains the old instance safely.
                    drained = await asyncio.gather(*(self._members.wait(i) for i in ids))
                    if not all(drained):
                        raise SetupError("成员仍在完成当前任务，请稍后重试以完成首次配置。")
                temporary = self._marker.with_suffix(".tmp")
                temporary.write_text(json.dumps({"provider": provider}), encoding="utf-8")
                temporary.replace(self._marker)
            except SetupError:
                raise
            except Exception:
                raise SetupError(
                    "配置未全部完成。已保存的部分会保留，请重试；无需重新启动来更新 API Key。"
                ) from None
            return {**self.status(), "pending_members": pending}
