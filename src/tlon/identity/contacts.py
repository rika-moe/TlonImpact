"""Contact book service — the member's private CONTACTS.md (ADR 0024, docs/design/identity.md).

Every member owns one private contact book at
``~/.tlon/{agent_id}/config/CONTACTS.md`` (entries: 名字 / 地址 / 我对 ta 的
描述) — self-written, private, and (from #101 PR3) injected into its own
prompt in place of the team-wide SOUL roster. Like ``PersonaService`` the
owning ``agent_id`` binds at construction and never appears in the schema:
structurally, a member can only read/write its own book. The filesystem
tools are locked into the container ``/workspace``, so these two tools are
the only write path — the file format is always tool-maintained.

Semantics (free text + loose structure, no content schema — a description
is subjective and must not be frozen by a schema):

- An absent file IS an empty book (``agent_contacts_file`` default); the
  first write creates it with a minimal ``# 联系簿`` header.
- ``contacts-read`` returns the full text; ``contacts-upsert`` is an
  **entry-level upsert** (never a whole-book rewrite — a hallucinated
  rewrite could silently drop entries; the book is a social asset and
  stays append-safe).
- File format: ``## {name}`` sections, each carrying ``- 地址：`` and
  ``- 描述：`` label lines. Other free-text lines inside a section are
  preserved verbatim across upserts; anything before the first ``## ``
  (the member's own title/notes) is never touched. Unrecognized content
  is kept, never an error.
- Delete: ``description=""`` removes the whole section.
"""

from pathlib import Path
from typing import Any

from tlon.base.paths import TlonPaths
from tlon.operations.schema import InputError

#: Minimal header for the first write to a previously absent/empty book.
_BOOK_HEADER = "# 联系簿"

#: Service-maintained label lines inside an entry section (Chinese labels,
#: matching the entry semantics 名字 / 地址 / 我对 ta 的描述).
_ADDRESS_PREFIX = "- 地址："
_DESCRIPTION_PREFIX = "- 描述："


def _parse_book(text: str) -> tuple[list[str], list[tuple[str, list[str]]]]:
    """Split book text into preamble lines and ``(name, body lines)`` sections.

    Loosest possible parsing: a section starts at a ``## `` heading;
    everything before the first heading is the preamble. Content is
    preserved verbatim — nothing here ever raises.
    """
    preamble: list[str] = []
    sections: list[tuple[str, list[str]]] = []
    current_name: str | None = None
    current_body: list[str] = []
    for line in text.splitlines():
        if line.startswith("## "):
            if current_name is not None:
                sections.append((current_name, current_body))
            current_name = line[3:].strip()
            current_body = []
        elif current_name is None:
            preamble.append(line)
        else:
            current_body.append(line)
    if current_name is not None:
        sections.append((current_name, current_body))
    return preamble, sections


def _render_book(preamble: list[str], sections: list[tuple[str, list[str]]]) -> str:
    """Render preamble + sections back to text (blank-line separated blocks)."""
    blocks: list[str] = []
    preamble_text = "\n".join(preamble).strip()
    if preamble_text:
        blocks.append(preamble_text)
    for name, body in sections:
        body_text = "\n".join(body).strip()
        block = f"## {name}"
        if body_text:
            block += "\n" + body_text
        blocks.append(block)
    return "\n\n".join(blocks) + "\n"


def _upsert_entry_body(body: list[str], address: str, description: str) -> list[str]:
    """Replace the 地址/描述 label lines in place (insert at the top when
    absent); every other free-text line keeps its position."""
    address_line = f"{_ADDRESS_PREFIX}{address}"
    description_line = f"{_DESCRIPTION_PREFIX}{description}"
    result: list[str] = []
    saw_address = False
    saw_description = False
    for line in body:
        if line.startswith(_ADDRESS_PREFIX):
            if not saw_address:
                result.append(address_line)
                saw_address = True
            continue  # drop duplicate label lines — the service owns the format
        if line.startswith(_DESCRIPTION_PREFIX):
            if not saw_description:
                result.append(description_line)
                saw_description = True
            continue
        result.append(line)
    missing: list[str] = []
    if not saw_address:
        missing.append(address_line)
    if not saw_description:
        missing.append(description_line)
    return missing + result


#: Success-message suffix: the contact book feeds the system prompt, whose
#: cache only rebuilds at the next assembly — the current session still
#: thinks with the old book.
_STALE_PROMPT_NOTE = "（注意：本次会话注入的联系簿仍是旧版本，改动在下一次组装生效）"


class ContactService:
    """Service to add / update / delete one entry in the owning member's book.

    Entry-level upsert keyed by ``name``: the entry's 地址/描述 label
    lines are replaced in place, free-text lines the member jotted inside
    the section are preserved, and every other entry stays untouched.
    ``description=""`` deletes the entry.
    """

    def __init__(self, tlon_paths: TlonPaths, agent_id: str) -> None:
        """Initialize the member-bound service.

        Args:
            tlon_paths: Path resolver for the host-side CONTACTS.md
            agent_id: Owning agent id — the only member whose contact
                book this service instance can ever write
        """
        self._tlon_paths = tlon_paths
        self._agent_id = agent_id

    async def read(self) -> dict[str, Any]:
        """Return the full text of the owning member's contact book."""
        path = self._tlon_paths.agent_contacts_file(self._agent_id)
        content = path.read_text(encoding="utf-8").strip() if path.exists() else ""
        if not content:
            return {
                "content": "",
                "entries": 0,
                "message": "你的联系簿还是空的——用 contacts-upsert 添加第一个条目",
            }
        _, sections = _parse_book(content)
        return {
            "content": content,
            "entries": len(sections),
            "message": f"联系簿共 {len(sections)} 个条目",
        }

    async def upsert(self, **kwargs: Any) -> dict[str, Any]:
        """Upsert (or, with empty description, delete) one contact entry."""
        name = str(kwargs.get("name") or "").strip()
        if not name or "\n" in name:
            raise InputError("name 为空或含换行")
        raw_description = kwargs.get("description")
        if not isinstance(raw_description, str):
            raise InputError("description 必填")
        # The label lines are single-line — fold multi-line input.
        description = " ".join(raw_description.splitlines()).strip()
        address = str(kwargs.get("address") or "").strip()

        path = self._tlon_paths.agent_contacts_file(self._agent_id)
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        preamble, sections = _parse_book(text)

        if not description:
            return self._delete(path, name, preamble, sections)
        if not address:
            raise InputError(
                "添加/更新条目需要 address（邮件地址）；删除条目请把 description 传空字符串"
            )
        return self._upsert(path, name, address, description, preamble, sections)

    def _upsert(
        self,
        path: Path,
        name: str,
        address: str,
        description: str,
        preamble: list[str],
        sections: list[tuple[str, list[str]]],
    ) -> dict[str, Any]:
        for index, (entry_name, body) in enumerate(sections):
            if entry_name == name:
                sections[index] = (name, _upsert_entry_body(body, address, description))
                self._write(path, preamble, sections)
                return {
                    "name": name,
                    "address": address,
                    "action": "updated",
                    "message": f"已更新条目「{name}」{_STALE_PROMPT_NOTE}",
                }
        sections.append(
            (name, [f"{_ADDRESS_PREFIX}{address}", f"{_DESCRIPTION_PREFIX}{description}"])
        )
        self._write(path, preamble, sections)
        return {
            "name": name,
            "address": address,
            "action": "added",
            "message": f"已添加条目「{name}」{_STALE_PROMPT_NOTE}",
        }

    def _delete(
        self,
        path: Path,
        name: str,
        preamble: list[str],
        sections: list[tuple[str, list[str]]],
    ) -> dict[str, Any]:
        for index, (entry_name, _) in enumerate(sections):
            if entry_name == name:
                del sections[index]
                self._write(path, preamble, sections)
                return {
                    "name": name,
                    "action": "deleted",
                    "message": f"已删除条目「{name}」{_STALE_PROMPT_NOTE}",
                }
        raise InputError(f"联系簿里没有条目「{name}」，无法删除")

    def _write(
        self, path: Path, preamble: list[str], sections: list[tuple[str, list[str]]]
    ) -> None:
        if not any(line.strip() for line in preamble):
            preamble = [_BOOK_HEADER]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_render_book(preamble, sections), encoding="utf-8")
