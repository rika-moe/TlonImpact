"""Update persona tool — member-authored edits to its own IDENTITY/SOUL files (ADR 0024).

Persona files live on the host at ``~/.tlon/{agent_id}/config/`` — outside the
container ``/workspace`` the filesystem tools are locked into — so self-editing
needs this host-side tool. The owning ``agent_id`` is bound at construction
(never a schema parameter): structurally, a member can only rewrite its own
IDENTITY.md / SOUL.md. RULES.md is deliberately absent from the ``file`` enum
— behavioral constraints stay human-edited (docs/design/identity.md).

Every successful rewrite appends a ``persona_update`` record to the member's
``ledger.jsonl`` (agent_id / file / sha256 / bytes) — the trust gate is
accountability, not approval (ADR 0024 决策 2). An append failure is logged
and surfaced in the tool result message (audit failures never pass silently);
the file write itself already succeeded, so the result stays a success.

The read side needs no tool: the member's own IDENTITY/SOUL are injected into
its system prompt by ``prompt_builder.build_identity``, and context assembly
(context_builder.py) rebuilds the cached prompt on persona-file mtime change,
so an edit takes effect at the next assembly.
"""

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tlon.base.ledger import LedgerWriter
from tlon.base.logger import get_logger
from tlon.base.paths import TlonPaths
from tlon.tools.base import Tool, ToolResult

logger = get_logger(__name__)

#: Logical file names exposed to the LLM — no path semantics leak into the
#: schema; the host path is resolved internally via TlonPaths.
PERSONA_FILES = ("identity", "soul")


class UpdatePersonaTool(Tool):
    """Tool to rewrite the owning member's own IDENTITY.md or SOUL.md.

    Full-content replacement (the member composes the new text wholesale).
    The target agent is fixed at construction; the schema carries no
    ``agent_id`` parameter, so touching another member's persona files is
    structurally impossible.
    """

    def __init__(self, tlon_paths: TlonPaths, agent_id: str) -> None:
        """Initialize the tool.

        Args:
            tlon_paths: Path resolver for the host-side persona files
            agent_id: Owning agent id — the only member whose persona
                files this tool instance can ever write
        """
        super().__init__(
            name="updatePersona",
            description=(
                "全文替换你自己的人格文件（IDENTITY.md / SOUL.md），用于自主更新自我认知。\n"
                "- file=identity：IDENTITY.md —— 我如何定义自己：名字、自称、身份叙述、当前自认的角色。\n"
                "- file=soul：SOUL.md —— 我重视什么、如何表达和判断：价值观、性格、语气、判断倾向（写给自己的锚，只有你自己看得到）。\n"
                "使用纪律：\n"
                "- content 是全文替换，不是追加——先想清楚完整新文本再写，去空白后为空会被拒绝。\n"
                "- 只能改你自己的文件；想改 RULES.md（工作规则、流程与明确约束）不行，那是人类编辑的，"
                "有诉求发邮件找人类。\n"
                "- 修改在下一次组装系统提示词时生效，不中断当前思考。\n"
                "- 每次修改都会在你的账本留痕（内容哈希），可问责——写你能为之负责的内容。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "file": {
                        "type": "string",
                        "enum": ["identity", "soul"],
                        "description": (
                            "要重写的人格文件：identity（自我定义与身份叙述）或 soul"
                            "（价值观、性格、语气、判断倾向）"
                        ),
                    },
                    "content": {
                        "type": "string",
                        "description": "新文件的完整内容（全文替换，去空白后不能为空）",
                    },
                },
                "required": ["file", "content"],
            },
        )
        self._tlon_paths = tlon_paths
        self._agent_id = agent_id

    def _resolve(self, file: str) -> tuple[Path, str]:
        """Map the logical file name to its host path and display name."""
        if file == "identity":
            return self._tlon_paths.agent_identity_file(self._agent_id), "IDENTITY.md"
        return self._tlon_paths.agent_soul_file(self._agent_id), "SOUL.md"

    async def execute(self, **kwargs: Any) -> ToolResult:
        """Rewrite one of the owning member's persona files (full replacement)."""
        file = str(kwargs.get("file") or "").strip()
        if file not in PERSONA_FILES:
            return ToolResult(
                success=False,
                data=None,
                error=f"file 必须是 {' / '.join(PERSONA_FILES)} 之一；RULES.md 不可自改（找人类）",
            )
        content = kwargs.get("content")
        if not isinstance(content, str) or not content.strip():
            return ToolResult(
                success=False,
                data=None,
                error="content 为空：全文替换要求给出完整的新内容（去空白后不能为空）",
            )

        path, display_name = self._resolve(file)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        content_bytes = content.encode("utf-8")
        ledger_note = await self._record_audit(file, content_bytes)
        return ToolResult(
            success=True,
            data={"file": file, "path": display_name, "bytes": len(content_bytes)},
            message=(
                f"已更新你的 {display_name}（{len(content)} 字符）。"
                f"注意：本次会话的系统提示仍是旧版本——你此刻仍带着旧人格思考，"
                f"改动在下一次组装生效{ledger_note}"
            ),
        )

    async def _record_audit(self, file: str, content_bytes: bytes) -> str:
        """Append the persona_update audit record to the member's ledger.

        Returns the message suffix: empty on success, an explicit
        「留痕失败」 note on failure — the file write already succeeded and
        the result stays a success, but an audit failure never passes
        silently.
        """
        record = {
            "ts": datetime.now(UTC).isoformat(),
            "event": "persona_update",
            "agent_id": self._agent_id,
            "file": file,
            "sha256": hashlib.sha256(content_bytes).hexdigest(),
            "bytes": len(content_bytes),
        }
        try:
            await LedgerWriter(self._tlon_paths.agent_ledger_file(self._agent_id)).append(record)
        except Exception as e:
            logger.error(
                "persona_ledger_append_failed", agent_id=self._agent_id, file=file, error=str(e)
            )
            return "（警告：留痕失败，本次修改未记账）"
        return ""
