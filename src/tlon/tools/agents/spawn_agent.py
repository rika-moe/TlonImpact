"""Spawn a member from a behavior prototype."""

from typing import Any

from structlog.contextvars import get_contextvars

from tlon.base.logger import get_logger
from tlon.tools.agents.ports import AgentLifecycle, SpawnRejectedError
from tlon.tools.base import Tool, ToolResult
from tlon.tools.prototypes.ports import PrototypeError

logger = get_logger(__name__)


class SpawnAgentTool(Tool):
    """Tool to spawn a new member from a prototype.

    Unified member model (ADR 0006): the spawned member is a member like
    any other — it is never discarded (stopping puts it to sleep, its
    home directory is archived), and whether it boots/lazes follows from
    the prototype's spec. Spawning is bounded by the registry's budget
    (global activation cap, spawn depth, per-session quota) — rejections
    come back as ``budget_exceeded: <reason>``.

    Permissions cannot escalate: the prototype library is the only source
    (Tlon-prebuilt prototypes are the trusted source; LLM-authored specs
    stay behind the manual-approval gate of stage 4), and the behavior
    file is copied verbatim — a spec is total, so nothing can be
    overridden at spawn time.
    """

    def __init__(self, registry: AgentLifecycle, spawner_id: str) -> None:
        super().__init__(
            name="spawnAgent",
            description=(
                "从 behavior prototype 派生一个新 Agent 并立即启动。"
                "新 Agent 是普通社会成员（不会自动回收/删除，空闲时睡眠，"
                "是否开机启动由原型 spec 决定）。"
                "受 spawn 预算约束：全局运行数上限、spawn 深度、每会话配额。"
                "behavior 文件原样拷贝（spec 是 total 的，无 overrides），无法授予新工具。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "prototype": {
                        "type": "string",
                        "description": "prototype 名称（先用 listPrototypes 查看可用项）",
                    },
                    "name": {
                        "type": "string",
                        "description": "新 Agent 的显示名（他人 @ 此名即可与之对话）",
                    },
                    "params": {
                        "type": "object",
                        "description": (
                            "模板参数（仅当 prototype 声明了 :parameters 时需要）。"
                            "key 为参数名，value 为参数值——:string 参数传字符串，"
                            ":string-list 参数传字符串数组"
                        ),
                    },
                },
                "required": ["prototype", "name"],
            },
        )
        self._registry = registry
        self._spawner_id = spawner_id

    async def execute(self, **kwargs: Any) -> ToolResult:
        prototype: str = kwargs["prototype"]
        name: str = kwargs["name"]
        params: dict[str, Any] | None = kwargs.get("params")

        # Session context is bound by the agent's session span via structlog
        # contextvars; direct calls (tests) fall back to a single bucket.
        session_id = get_contextvars().get("session_id")
        try:
            config = await self._registry.spawn_member(
                prototype=prototype,
                name=name,
                spawner_id=self._spawner_id,
                session_id=str(session_id) if session_id else None,
                params=params,
            )
        except SpawnRejectedError as e:
            if e.reason == "untrusted_prototype":
                error = f"untrusted_prototype: {prototype}（需人类在 mirror 中批准）"
            else:
                error = f"budget_exceeded: {e.reason}"
            return ToolResult(
                success=False,
                data={"reason": e.reason},
                error=error,
                message=str(e),
            )
        except PrototypeError as e:
            return ToolResult(success=False, data={}, error=str(e))

        return ToolResult(
            success=True,
            data={
                "agent_id": config.id,
                "name": config.name,
                "prototype": config.prototype,
                "spawn_depth": config.spawn_depth,
            },
            message=(
                f"新成员 {config.name}（{config.id}）已创建并启动；@ {config.name} 即可分配任务"
            ),
        )
