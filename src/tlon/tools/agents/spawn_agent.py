"""Spawn a contractor agent from a behavior prototype."""

from typing import TYPE_CHECKING, Any

from structlog.contextvars import get_contextvars

from tlon.agents.edn import Keyword
from tlon.agents.prototypes import PrototypeError
from tlon.agents.spawn_errors import SpawnRejectedError
from tlon.base.logger import get_logger
from tlon.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from tlon.agents.agent_registry import AgentRegistry

logger = get_logger(__name__)


def _ednify_keys(data: Any) -> Any:
    """Recursively convert plain string map keys to EDN keywords.

    Tool arguments arrive as JSON, so override keys are plain strings; the
    prototype merge and the behavior file expect EDN keyword keys.
    """
    if isinstance(data, dict):
        return {Keyword(str(k)): _ednify_keys(v) for k, v in data.items()}
    if isinstance(data, list):
        return [_ednify_keys(item) for item in data]
    return data


class SpawnAgentTool(Tool):
    """Tool to spawn a disposable contractor agent from a prototype.

    The contractor starts immediately and is reclaimed by its own idle
    timeout when done. Spawning is bounded by the registry's budget
    (global activation cap, spawn depth, per-session quota) — rejections
    come back as ``budget_exceeded: <reason>``.

    Permissions cannot escalate: the prototype library is the only source
    (Tlon-prebuilt prototypes are the trusted source; LLM-authored specs
    stay behind the manual-approval gate of stage 4), and ``overrides``
    covers behavior fields only — tools are not part of the spec schema.
    """

    def __init__(self, registry: "AgentRegistry", spawner_id: str) -> None:
        super().__init__(
            name="spawnAgent",
            description=(
                "从 behavior prototype 派生一个临时工 Agent（contractor）并立即启动。"
                "临时工完成委托任务后会被自动回收（空闲超时即弃）。"
                "受 spawn 预算约束：全局运行数上限、spawn 深度、每会话配额。"
                "overrides 仅覆盖行为字段（如 loop.max-iterations），无法授予新工具。"
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
                    "overrides": {
                        "type": "object",
                        "description": (
                            "可选的行为覆盖（EDN 结构的 JSON 表示），"
                            '如 {"loop": {"max-iterations": 5}}'
                        ),
                    },
                },
                "required": ["prototype", "name"],
            },
        )
        self._registry = registry
        self._spawner_id = spawner_id

    async def execute(
        self,
        prototype: str,
        name: str,
        overrides: dict[str, Any] | None = None,
    ) -> ToolResult:
        # Session context is bound by the agent's session span via structlog
        # contextvars; direct calls (tests) fall back to a single bucket.
        session_id = get_contextvars().get("session_id")
        try:
            config = await self._registry.spawn_contractor(
                prototype=prototype,
                name=name,
                spawner_id=self._spawner_id,
                session_id=str(session_id) if session_id else None,
                overrides=_ednify_keys(overrides) if overrides else None,
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
