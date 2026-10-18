"""Members facade：成员（agent）查询与生命周期管理。

收编 REST 层对 ``AgentRegistry`` 与成员文件（persona .md / 原型实例化）
的直接触点：

- 状态查询：``get_all_agent_status`` / ``get_config`` / ``get_state``
- 成员目录读模型：``list_agent_members`` / ``get_agent_member``（返回
  ``MemberInfo``——该 DTO 是 facade 方法的返回类型，故作为本模块
  API 面的一部分对 server 可见）
- 生命周期：``adopt``（热收编）/ ``remove``（管理面移除）
- 创建：``create``（原型实例化 + persona 文件写入 + 热收编前的全部
  写盘步骤）

跨界异常（``StopRejectedError`` / ``PrototypeError``）不建翻译层：
server 直接从本模块取。
"""

from collections.abc import Collection
from typing import Any

from tlon.agents.agent_registry import AgentRegistry
from tlon.agents.prototypes.instantiate import instantiate
from tlon.base.config import AgentConfig
from tlon.base.identifiers import new_member_id
from tlon.base.paths import TlonPaths
from tlon.organization.mail.mail import HUMAN_ID_PREFIX
from tlon.organization.mail.store import MailStore
from tlon.organization.messaging.member_info import MemberInfo
from tlon.tools.agents.ports import StopRejectedError
from tlon.tools.prototypes.ports import PrototypeError

__all__ = [
    "MembersFacade",
    "MemberInfo",
    "PrototypeError",
    "StopRejectedError",
]

# Persona 字段 → 成员配置目录下的文件名（统一成员模型，ADR 0006）
_PERSONA_FILES = (
    ("identity", "IDENTITY.md"),
    ("profile", "SOUL.md"),
    ("behavior", "RULES.md"),
)


def _write_persona_files(tlon_paths: TlonPaths, agent_id: str, persona: dict[str, str]) -> None:
    """Write supplied persona fields verbatim; empty strings clear files."""
    config_dir = tlon_paths.agent_config_dir(agent_id)
    config_dir.mkdir(parents=True, exist_ok=True)
    for field, filename in _PERSONA_FILES:
        if field in persona:
            (config_dir / filename).write_text(persona[field], encoding="utf-8")


class MembersFacade:
    """成员域北向 API。构造器注入 AgentRegistry + TlonPaths，薄透传。

    Example:
        >>> members = MembersFacade(agent_registry=registry, tlon_paths=paths)
        >>> await members.get_all_agent_status()
        >>> await members.adopt("dev-1")
    """

    def __init__(self, *, agent_registry: AgentRegistry, tlon_paths: TlonPaths):
        """Initialize the facade.

        Args:
            agent_registry: The runtime agent registry
            tlon_paths: Path resolver (member config dirs, prototype library)
        """
        self._registry = agent_registry
        self._paths = tlon_paths

    # ---------- 状态查询 ----------

    async def get_all_agent_status(self) -> list[dict[str, Any]]:
        """Status snapshot of every registered agent."""
        return await self._registry.get_all_agent_status()

    def get_config(self, agent_id: str) -> AgentConfig | None:
        """The member's config, or None when unknown."""
        return self._registry.get_config(agent_id)

    async def get_status(self, agent_id: str) -> dict[str, Any] | None:
        """Read the authoritative scheduling state and runtime diagnostics."""
        return await self._registry.get_agent_status(agent_id)

    async def get_state(self, agent_id: str) -> str | None:
        """The member's scheduling state, or None for an unknown member."""
        status = await self.get_status(agent_id)
        return status["state"] if status is not None else None

    def list_ids(self) -> list[str]:
        """Ids of all registered agents."""
        return self._registry.get_all_ids()

    # ---------- 成员目录读模型 ----------

    def list_agent_members(self) -> list[MemberInfo]:
        """All agent members as read-model DTOs (address book)."""
        members: list[MemberInfo] = []
        for agent_id in self._registry.get_all_ids():
            config = self._registry.get_config(agent_id)
            if config:
                members.append(MemberInfo(id=agent_id, name=config.name, type="agent", metadata={}))
        return members

    def get_agent_member(self, agent_id: str) -> MemberInfo | None:
        """One agent member as a read-model DTO, or None when unknown."""
        config = self._registry.get_config(agent_id)
        if config is None:
            return None
        return MemberInfo(id=agent_id, name=config.name, type="agent", metadata={})

    # ---------- 生命周期 ----------

    async def adopt(self, agent_id: str) -> str:
        """Hot-adopt a member whose config appeared on disk.

        Returns:
            One of "already-known", "config-not-found", "registered-lazy",
            "started", "start-failed" (AgentRegistry.adopt_agent)
        """
        return await self._registry.adopt_agent(agent_id)

    async def terminate(self, agent_id: str, stopped_by: str) -> bool:
        """Permanently terminate after draining work, preserving all history.

        Return True when dead, False when termination is pending.
        """
        return await self._registry.stop_member(agent_id, stopped_by=stopped_by, when_idle=True)

    async def wake(self, agent_id: str) -> bool:
        """Wake a sleeping member early; actual mail determines activity."""
        return await self._registry.wake_member(agent_id)

    async def wait(self, agent_id: str) -> bool:
        """Drain current work into waiting; False means draining is pending."""
        return await self._registry.wait_member(agent_id)

    async def sleep(self, agent_id: str, wake_at: str) -> bool:
        """Sleep until a timezone-aware future deadline, after draining work."""
        return await self._registry.sleep_member(agent_id, wake_at)

    async def remove(self, agent_id: str, removed_by: str) -> bool:
        """Administratively remove a member (stop, unregister, delete home).

        Raises:
            StopRejectedError: removal rejected (e.g. live sessions)
        """
        return await self._registry.remove_member(agent_id, removed_by=removed_by)

    # ---------- 创建 ----------

    def create(
        self,
        prototype: str,
        name: str,
        model: str,
        *,
        params: dict[str, Any] | None = None,
        persona: dict[str, str] | None = None,
        reserved_ids: Collection[str] = (),
    ) -> AgentConfig:
        """Create a member from a prototype (config + persona on disk).

        Writes the instance's behavior/config (instantiate), then the
        self files (creation-time seeding, ADR 0006). IDENTITY and SOUL
        are member-owned and self-editable via ``updatePersona`` (ADR
        0024); RULES is maintained by humans. The caller hot-adopts the
        new member separately (``adopt``) so it can report the adopt
        result.

        Args:
            prototype: Prototype directory name (behavior genome)
            name: Display name
            model: Member-level LLM in ``provider/model`` form (ADR 0016)
            params: Template parameter values for parameterized prototypes
            persona: Optional creation-time persona (identity/profile/behavior)
            reserved_ids: Internal addresses reserved by the serving layer

        Returns:
            The created AgentConfig

        Raises:
            PrototypeError: prototype missing, id taken, bad model/params
        """
        occupied = {value.casefold() for value in reserved_ids}
        occupied.update(
            value.removeprefix(HUMAN_ID_PREFIX).casefold()
            for value in MailStore(self._paths.mail_database_file()).known_human_ids()
        )
        occupied.update(value.casefold() for value in self._registry.get_all_ids())
        while True:
            agent_id = new_member_id()
            if (
                agent_id.casefold() not in occupied
                and not self._paths.agent_config_dir(agent_id).exists()
            ):
                break
        cfg = instantiate(
            prototype,
            agent_id,
            name,
            model=model,
            params=params,
            tlon_paths=self._paths,
        )
        if persona:
            _write_persona_files(self._paths, cfg.id, persona)
        return cfg

    def read_persona(self, agent_id: str) -> dict[str, str]:
        """Read all three self files verbatim, returning empty strings for absent files."""
        config_dir = self._paths.agent_config_dir(agent_id)
        persona: dict[str, str] = {}
        for field, filename in _PERSONA_FILES:
            try:
                persona[field] = (config_dir / filename).read_text(encoding="utf-8")
            except FileNotFoundError:
                persona[field] = ""
        return persona

    def write_persona(self, agent_id: str, persona: dict[str, str]) -> None:
        """Write supplied fields; omitted fields stay untouched and empty values clear."""
        _write_persona_files(self._paths, agent_id, persona)
