"""Tool output spill — oversized results land on disk, the model sees a locator.

Shell and filesystem tool outputs are a common context-explosion source.
When a ``ToolResult.data`` payload exceeds
``SPILL_THRESHOLD_CHARS`` serialized, the full payload is written to the
member's workspace (``workspace/spills/{session_id}/{tool_call_id}.json``,
pretty-printed so line-based pagination works) and the model receives a
small locator instead: path, size, a short preview, and a hint to page
through the file with ``readFile`` (the workspace is bind-mounted into the
agent's container at the working dir, so the workspace-relative path is
directly readable).

Spilling happens in the engine (``LoopDriver.execute_one_tool``) *before*
the result is journaled, so the session log and the model context hold the
same locator — replay reproduces exactly what the model saw.
"""

import json
from typing import Any

import aiofiles

from tlon.base.llm_client import ToolCall
from tlon.base.logger import get_logger
from tlon.base.paths import TlonPaths
from tlon.tools.base import ToolResult

logger = get_logger(__name__)

#: Serialized ``ToolResult.data`` size (chars) above which the payload is
#: spilled to disk (~4-8k tokens; keeps single results well under context).
SPILL_THRESHOLD_CHARS = 16_000

#: Leading chars of the serialized payload kept in the locator so the model
#: can decide whether paging the spill file is worth it.
PREVIEW_CHARS = 500


class ToolSpill:
    """Spills oversized tool results into the member workspace.

    Tool-agnostic: the trigger is payload size alone, regardless of which
    tool produced it or whether it succeeded (a huge stderr is spilled too).
    """

    def __init__(self, tlon_paths: TlonPaths, agent_id: str):
        """Initialize the spill writer for one agent.

        Args:
            tlon_paths: Path resolver (tests inject an isolated home)
            agent_id: Owning agent id (spills live under its workspace)
        """
        self._paths = tlon_paths
        self._agent_id = agent_id

    def _spill_dir(self, session_id: str) -> str:
        """Workspace-relative spill dir for a session (container-readable)."""
        return f"spills/{session_id}"

    async def maybe_spill(
        self, session_id: str, tool_call: ToolCall, result: ToolResult
    ) -> ToolResult:
        """Spill the result's data to disk when it exceeds the threshold.

        Returns the original result untouched when small enough; otherwise
        a copy whose ``data`` is the locator dict. A failed spill write
        falls back to the original result (a big context beats a lost one)
        and logs an error.
        """
        serialized = json.dumps(result.data, ensure_ascii=False)
        if len(serialized) <= SPILL_THRESHOLD_CHARS:
            return result

        rel_path = f"{self._spill_dir(session_id)}/{tool_call.id}.json"
        abs_path = self._paths.agent_workspace_dir(self._agent_id) / rel_path
        pretty = json.dumps(result.data, ensure_ascii=False, indent=2)
        try:
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            async with aiofiles.open(abs_path, "w", encoding="utf-8") as f:
                await f.write(pretty)
        except Exception as e:
            logger.error(
                "tool_spill_failed",
                session_id=session_id,
                tool_call_id=tool_call.id,
                error=str(e),
            )
            return result

        logger.info(
            "tool_output_spilled",
            session_id=session_id,
            tool_call_id=tool_call.id,
            tool_name=tool_call.function_name,
            size_chars=len(serialized),
            spill_path=rel_path,
        )
        locator: dict[str, Any] = {
            "_spilled": True,
            "spill_path": rel_path,
            "size_chars": len(serialized),
            "preview": serialized[:PREVIEW_CHARS],
            "hint": "工具输出过大，已落盘到工作区文件。用 readFile 工具按 offset/limit 分页读取 spill_path 查看完整内容。",
        }
        return ToolResult(
            success=result.success,
            data=locator,
            error=result.error,
            message=result.message,
        )
