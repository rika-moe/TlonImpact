"""Persona service — member-authored edits to its own IDENTITY/SOUL files (ADR 0024).

Persona files live on the host at ``~/.tlon/{agent_id}/config/`` — outside the
container ``/workspace`` the filesystem tools are locked into — so self-editing
needs this host-side service. The owning ``agent_id`` is bound at construction
(never a schema parameter): structurally, a member can only rewrite its own
IDENTITY.md / SOUL.md. RULES.md is deliberately absent from the ``file`` enum
— behavioral constraints stay human-edited (docs/design/identity.md).

Every successful rewrite appends a ``persona_update`` record to the member's
``ledger.jsonl`` (agent_id / file / sha256 / bytes) — the trust gate is
accountability, not approval (ADR 0024 决策 2). An append failure is logged
and surfaced in the operation result message (audit failures never pass silently);
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
from tlon.operations.schema import InputError

logger = get_logger(__name__)

#: Logical file names exposed to the LLM — no path semantics leak into the
#: schema; the host path is resolved internally via TlonPaths.
PERSONA_FILES = ("identity", "soul")


class PersonaService:
    """Service to rewrite the owning member's own IDENTITY.md or SOUL.md.

    Full-content replacement (the member composes the new text wholesale).
    The target agent is fixed at construction; the schema carries no
    ``agent_id`` parameter, so touching another member's persona files is
    structurally impossible.
    """

    def __init__(self, tlon_paths: TlonPaths, agent_id: str) -> None:
        """Initialize the member-bound service.

        Args:
            tlon_paths: Path resolver for the host-side persona files
            agent_id: Owning agent id — the only member whose persona
                files this service instance can ever write
        """
        self._tlon_paths = tlon_paths
        self._agent_id = agent_id

    def _resolve(self, file: str) -> tuple[Path, str]:
        """Map the logical file name to its host path and display name."""
        if file == "identity":
            return self._tlon_paths.agent_identity_file(self._agent_id), "IDENTITY.md"
        return self._tlon_paths.agent_soul_file(self._agent_id), "SOUL.md"

    async def update(self, **kwargs: Any) -> dict[str, Any]:
        """Rewrite one of the owning member's persona files (full replacement)."""
        file = str(kwargs.get("file") or "").strip()
        if file not in PERSONA_FILES:
            raise InputError(
                f"file 必须是 {' / '.join(PERSONA_FILES)} 之一；RULES.md 不可自改（找人类）"
            )
        content = kwargs.get("content")
        if not isinstance(content, str) or not content.strip():
            raise InputError("content 为空：全文替换要求给出完整的新内容（去空白后不能为空）")

        path, display_name = self._resolve(file)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        content_bytes = content.encode("utf-8")
        ledger_note = await self._record_audit(file, content_bytes)
        return {
            "file": file,
            "path": display_name,
            "bytes": len(content_bytes),
            "message": f"已更新你的 {display_name}（{len(content)} 字符）。注意：本次会话的系统提示仍是旧版本——你此刻仍带着旧人格思考，改动在下一次组装生效{ledger_note}",
        }

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
