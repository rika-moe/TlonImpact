"""Spawn a contractor agent from a behavior prototype."""

from typing import TYPE_CHECKING, Any

from structlog.contextvars import get_contextvars

from tlon.agents.prototypes import PrototypeError
from tlon.agents.spawn_errors import SpawnRejectedError
from tlon.base.logger import get_logger
from tlon.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from tlon.agents.agent_registry import AgentRegistry

logger = get_logger(__name__)


class SpawnAgentTool(Tool):
    """Tool to spawn a disposable contractor agent from a prototype.

    The contractor starts immediately and is reclaimed by its own idle
    timeout when done. Spawning is bounded by the registry's budget
    (global activation cap, spawn depth, per-session quota) — rejections
    come back as ``budget_exceeded: <reason>``.

    Permissions cannot escalate: the prototype library is the only source
    (Tlon-prebuilt prototypes are the trusted source; LLM-authored specs
    stay behind the manual-approval gate of stage 4), and the behavior
    file is copied verbatim — a spec is total, so nothing can be
    overridden at spawn time.
    """

    def __init__(self, registry: "AgentRegistry", spawner_id: str) -> None:
        super().__init__(
            name="spawnAgent",
            description=(
                "从 behavior prototype 派生一个临时工 Agent（contractor）并立即启动。"
                "临时工完成委托任务后会被自动回收（空闲超时即弃）。"
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
                },
                "required": ["prototype", "name"],
            },
        )
        self._registry = registry
        self._spawner_id = spawner_id

    async def execute(self, **kwargs: Any) -> ToolResult:
        prototype: str = kwargs["prototype"]
        name: str = kwargs["name"]

        # Session context is bound by the agent's session span via structlog
        # contextvars; direct calls (tests) fall back to a single bucket.
        session_id = get_contextvars().get("session_id")
        try:
            config = await self._registry.spawn_contractor(
                prototype=prototype,
                name=name,
                spawner_id=self._spawner_id,
                session_id=str(session_id) if session_id else None,
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
                "role": config.role,
                "prototype": config.prototype,
                "spawn_depth": config.spawn_depth,
            },
            message=(
                f"临时工 {config.name}（{config.id}）已创建并启动；"
                f"@ {config.name} 即可分配任务，完工后会被自动回收"
            ),
        )
