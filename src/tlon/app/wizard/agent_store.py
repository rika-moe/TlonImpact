"""Agent data storage operations.

Thin wrapper around base/config.py YAML CRUD functions.
"""

from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from tlon.base.config import (
    AgentConfig,
    yaml_delete,
    yaml_exists,
    yaml_load_all,
    yaml_load_file,
    yaml_save,
)
from tlon.base.logger import get_logger

if TYPE_CHECKING:
    from tlon.base.paths import TlonPaths as _TlonPaths

logger = get_logger(__name__)


def startup_mode_from_behavior(behavior_edn: str) -> str:
    """Derive the startup mode label from a behavior spec's EDN text.

    A ``:on :boot`` rule (parsed as ``rule.on is None``) makes the member
    resident (常驻); anything else — or an unparseable spec — is lazy (延迟).
    """
    from tlon.agents.edn import loads
    from tlon.agents.member_spec import MemberSpec

    try:
        spec = MemberSpec.from_edn(loads(behavior_edn))
    except Exception:
        return "延迟"
    return "常驻" if any(rule.on is None for rule in spec.rules) else "延迟"


class AgentStore:
    """Store for managing Agent configurations.

    Delegates to base/config.py standalone YAML CRUD functions.
    """

    def __init__(self, agents_dir: str | None = None, data_dir: str | None = None, paths: "_TlonPaths | None" = None):
        from tlon.base.paths import paths as _paths

        self._paths = paths or _paths

    def load_all_agents(self) -> dict[str, AgentConfig]:
        return yaml_load_all(self._paths.tlon_home())

    def get_agent(self, agent_id: str) -> AgentConfig | None:
        filepath = self._paths.agent_config_file(agent_id)
        if not filepath.exists():
            return None
        return yaml_load_file(filepath)

    def get_agent_by_name(self, name: str) -> AgentConfig | None:
        for agent in self.load_all_agents().values():
            if agent.name == name:
                return agent
        return None

    def save_agent(self, agent: AgentConfig) -> tuple[bool, str]:
        try:
            yaml_save(agent, self._paths.tlon_home())
            return True, "Agent saved"
        except Exception as e:
            return False, f"Failed to save agent: {e}"

    def write_behavior_file(self, agent_id: str) -> None:
        """Write the canonical complete behavior.tlon for a newly created agent.

        The behavior spec is total — a missing file refuses the agent's
        boot — so every wizard-created agent gets the standard ReAct
        program spelled out in full alongside its agent.yaml.
        """
        from tlon.agents.behavior_templates import standard_behavior_edn

        filepath = self._paths.agent_behavior_file(agent_id)
        filepath.write_text(standard_behavior_edn(agent_id), encoding="utf-8")

    def startup_mode(self, agent_id: str) -> str:
        """Startup mode label (常驻/延迟) derived from the on-disk behavior spec.

        Falls back to 延迟 when the behavior file is missing or unreadable.
        """
        filepath = self._paths.agent_behavior_file(agent_id)
        try:
            text = filepath.read_text(encoding="utf-8")
        except OSError:
            return "延迟"
        return startup_mode_from_behavior(text)

    def delete_agent(self, agent_id: str) -> tuple[bool, str]:
        """Delete an agent: removes the entire ~/.tlon/{agent_id}/ directory."""
        try:
            yaml_delete(agent_id, self._paths.tlon_home())
            return True, f"Agent '{agent_id}' deleted"
        except ValueError as e:
            return False, str(e)
        except Exception as e:
            return False, f"Failed to delete agent: {e}"

    def agent_exists(self, agent_id: str) -> bool:
        return yaml_exists(agent_id, self._paths.tlon_home())

    def validate_agent_data(self, data: dict[str, Any]) -> tuple[bool, list[str]]:
        """Validate agent data against the AgentConfig model.

        Args:
            data: Agent data dictionary

        Returns:
            Tuple of (is_valid, list_of_errors)
        """
        try:
            AgentConfig.model_validate(data)
            return True, []
        except ValidationError as e:
            errors = [f"{err['loc']}: {err['msg']}" for err in e.errors()]
            return False, errors

    def generate_agent_id(self, prefix: str, existing_ids: list[str] | None = None) -> str:
        if existing_ids is None:
            existing_ids = list(self.load_all_agents().keys())
        prefix = prefix.replace("_", "-")
        counter = 1
        while True:
            agent_id = f"{prefix}-{counter:03d}"
            if agent_id not in existing_ids:
                return agent_id
            counter += 1

    # ===== Persona .md file operations =====

    def read_persona_file(self, agent_id: str, filename: str) -> str:
        """Read a persona markdown file (IDENTITY.md, SOUL.md, RULES.md)."""
        filepath = self._paths.agent_config_dir(agent_id) / filename
        if filepath.exists():
            return filepath.read_text(encoding="utf-8").strip()
        return ""

    def write_persona_file(self, agent_id: str, filename: str, content: str) -> None:
        """Write a persona markdown file (IDENTITY.md, SOUL.md, RULES.md)."""
        filepath = self._paths.agent_config_dir(agent_id) / filename
        filepath.write_text(content.strip() + "\n", encoding="utf-8")


__all__ = ["AgentStore"]
