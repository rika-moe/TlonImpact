"""Update persona tool — member-authored edits to its own IDENTITY/SOUL files (ADR 0024).

Persona files live on the host at ``~/.tlon/{agent_id}/config/`` — outside the
container ``/workspace`` the filesystem tools are locked into — so self-editing
needs this host-side tool. The owning ``agent_id`` is bound at construction
(never a schema parameter): structurally, a member can only rewrite its own
IDENTITY.md / SOUL.md. RULES.md is deliberately absent from the ``file`` enum
— behavioral constraints stay human-edited (docs/design/identity.md).

The read side needs no tool: the member's own IDENTITY/SOUL are injected into
its system prompt by ``prompt_builder.build_identity``, and context assembly
(context_builder.py) rebuilds the cached prompt on persona-file mtime change,
so an edit takes effect at the next assembly.
"""

from pathlib import Path
from typing import Any

from tlon.base.paths import TlonPaths
from tlon.tools.base import Tool, ToolResult

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
                "- file=identity：IDENTITY.md —— 你是谁、你的核心自我认知。\n"
                "- file=soul：SOUL.md —— 你的角色定位、工作风格（队友也会看到这个文件）。\n"
                "使用纪律：\n"
                "- content 是全文替换，不是追加——先想清楚完整新文本再写，去空白后为空会被拒绝。\n"
                "- 只能改你自己的文件；想改 RULES.md（行为约束）不行，那是人类编辑的，"
                "有诉求发邮件找人类。\n"
                "- 修改在下一次组装系统提示词时生效，不中断当前思考。\n"
                "- 每次修改都会被留痕，可问责——写你能为之负责的内容。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "file": {
                        "type": "string",
                        "enum": ["identity", "soul"],
                        "description": (
                            "要重写的人格文件：identity（核心自我认知）或 soul"
                            "（角色定位、工作风格）"
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
        return ToolResult(
            success=True,
            data={"file": file, "path": display_name, "bytes": len(content.encode("utf-8"))},
            message=f"已更新你的 {display_name}（{len(content)} 字符），下次组装生效",
        )
