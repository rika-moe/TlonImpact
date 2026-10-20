"""Member-bound lifecycle decisions and self scheduling requests."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from structlog.contextvars import get_contextvars

from tlon.evolution.ports import PrototypeError
from tlon.lifecycle.ports import (
    AgentLifecycle,
    MemberDirectory,
    SelfStateReader,
    SpawnRejectedError,
)
from tlon.operations.core import CallContext
from tlon.operations.schema import InputError, OperationError, RejectedError

RequestPublisher = Callable[[str, str, str, CallContext], Awaitable[None]]


class LifecycleService:
    """Bind identity and authority outside business input; the registry owns activation."""

    def __init__(
        self,
        member_id: str,
        *,
        registry: AgentLifecycle | None = None,
        directory: MemberDirectory | None = None,
        state_reader: SelfStateReader | None = None,
        publish_request: RequestPublisher | None = None,
        start_all_candidates: Callable[[], list[str]] | None = None,
        apprentice: bool = False,
    ) -> None:
        self.member_id = member_id
        self.registry = registry
        self.directory = directory
        self.state_reader = state_reader
        self.publish_request = publish_request
        self.start_all_candidates = start_all_candidates
        self.apprentice = apprentice

    def check_context(self, context: CallContext) -> None:
        if context.member_id != self.member_id:
            raise InputError("lifecycle service belongs to another member")

    async def spawn(self, args: dict[str, Any]) -> dict[str, Any]:
        if self.apprentice:
            raise RejectedError("probationary members cannot spawn", {"reason": "probationary"})
        if self.registry is None:
            raise OperationError("member lifecycle registry is not bound")
        session_id = get_contextvars().get("session_id")
        try:
            config = await self.registry.spawn_member(
                prototype=args["prototype"],
                name=args["name"],
                spawner_id=self.member_id,
                session_id=str(session_id) if session_id else None,
                params=args.get("params"),
            )
        except SpawnRejectedError as error:
            raise RejectedError(str(error), {"reason": error.reason}) from error
        except PrototypeError as error:
            raise OperationError(str(error)) from error
        return {
            "agent_id": config.id,
            "name": config.name,
            "prototype": config.prototype,
            "spawn_depth": config.spawn_depth,
        }

    async def members(self) -> list[dict[str, str]]:
        if self.directory is None:
            raise OperationError("member directory is not bound")
        members = await asyncio.to_thread(self.directory.list_members)
        return [
            {"id": member["id"], "name": member["name"], "address": member["address"]}
            for member in members
        ]

    async def self_state(self) -> dict[str, Any]:
        if self.state_reader is None:
            raise OperationError("member scheduling state reader is not bound")
        status = await self.state_reader.get_agent_status(self.member_id)
        if status is None:
            raise RejectedError("member does not exist", {"reason": "unknown_agent"})
        return status

    async def request(
        self, action: str, agent_id: str, reason: str, context: CallContext
    ) -> dict[str, Any]:
        if self.member_id != "adam" and agent_id != self.member_id:
            raise RejectedError(
                "members may only manage their own scheduling state", {"reason": "forbidden"}
            )
        if self.publish_request is None:
            raise OperationError("member lifecycle request publisher is not bound")
        await self.publish_request(action, agent_id, reason, context)
        return {"action": action, "agent_id": agent_id, "reason": reason}

    async def request_all(self, reason: str, context: CallContext) -> list[dict[str, Any]]:
        if self.member_id != "adam":
            raise RejectedError("only adam may start all members", {"reason": "forbidden"})
        if self.start_all_candidates is None:
            raise OperationError("member boot candidates are not bound")
        return [
            await self.request("start", member_id, reason, context)
            for member_id in self.start_all_candidates()
        ]


def scheduling_request(action: str, args: dict[str, Any]) -> dict[str, Any]:
    """Declare a self transition; the runtime commits after the turn drains."""
    request = {"action": action, "reason": args.get("reason", "")}
    if action == "sleep":
        try:
            wake_at = datetime.fromisoformat(args["wake_at"])
            if wake_at.tzinfo is None or wake_at.utcoffset() is None:
                raise ValueError("wake_at 必须带时区")
            wake_at = wake_at.astimezone(UTC)
            if wake_at <= datetime.now(UTC):
                raise ValueError("wake_at 必须是未来时间")
        except (ValueError, TypeError, KeyError) as error:
            raise InputError(str(error)) from error
        request["wake_at"] = wake_at.isoformat()
    return {"scheduling_request": request}
