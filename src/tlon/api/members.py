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

from typing import Any

from tlon.agents.agent_registry import AgentRegistry
from tlon.agents.prototypes import instantiate
from tlon.base.config import AgentConfig
from tlon.base.paths import TlonPaths
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


def _write_persona_files(
    tlon_paths: TlonPaths, agent_id: str, persona: dict[str, Any]
) -> None:
    """Write the non-empty persona fields into the member's config dir."""
    config_dir = tlon_paths.agent_config_dir(agent_id)
    config_dir.mkdir(parents=True, exist_ok=True)
    for field, filename in _PERSONA_FILES:
        content = persona.get(field)
        if content:
            (config_dir / filename).write_text(
                str(content).strip() + "\n", encoding="utf-8"
            )


class MembersFacade:
    """成员域北向 API。构造器注入 AgentRegistry + TlonPaths，薄透传。

    Example:
        >>> members = MembersFacade(agent_registry=registry, tlon_paths=paths)
        >>> members.get_all_agent_status()
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

    def get_all_agent_status(self) -> list[dict[str, Any]]:
        """Status snapshot of every registered agent."""
        return self._registry.get_all_agent_status()

    def get_config(self, agent_id: str) -> AgentConfig | None:
        """The member's config, or None when unknown."""
        return self._registry.get_config(agent_id)

    def get_state(self, agent_id: str) -> str:
        """The member's runtime state ("unknown" when not running)."""
        agent = self._registry.get_agent(agent_id)
        return agent.get_state() if agent else "unknown"

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
                members.append(
                    MemberInfo(id=agent_id, name=config.name, type="agent", metadata={})
                )
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
        agent_id: str,
        name: str,
        model: str,
        *,
        params: dict[str, Any] | None = None,
        persona: dict[str, Any] | None = None,
    ) -> AgentConfig:
        """Create a member from a prototype (config + persona on disk).

        Writes the instance's behavior/config (instantiate), then the
        creation-time persona files (ADR 0006). The caller hot-adopts the
        new member separately (``adopt``) so it can report the adopt
        result.

        Args:
            prototype: Prototype directory name (behavior genome)
            agent_id: New member id
            name: Display name
            model: Member-level LLM in ``provider/model`` form (ADR 0016)
            params: Template parameter values for parameterized prototypes
            persona: Optional creation-time persona (identity/profile/behavior)

        Returns:
            The created AgentConfig

        Raises:
            PrototypeError: prototype missing, id taken, bad model/params
        """
        cfg = instantiate(
            prototype, agent_id, name, model=model, params=params,
            tlon_paths=self._paths,
        )
        if persona:
            _write_persona_files(self._paths, agent_id, persona)
        return cfg

    def write_persona(self, agent_id: str, persona: dict[str, Any]) -> None:
        """Write persona fields of an existing member (PATCH semantics:
        non-empty fields only; prompt cache keys on the file mtimes)."""
        _write_persona_files(self._paths, agent_id, persona)
