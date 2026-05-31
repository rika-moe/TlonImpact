"""Agent data storage operations.

Thin wrapper around base/config.py YAML CRUD functions.
"""

from pathlib import Path

from pydantic import ValidationError

from tlon.base.config import (
    AgentConfig,
    _yaml_delete,
    _yaml_ensure_dir,
    _yaml_exists,
    _yaml_load_all,
    _yaml_load_file,
    _yaml_save,
)
from tlon.base.logger import get_logger
from tlon.base.settings import get_default_config_dir, get_default_data_dir

logger = get_logger(__name__)


class AgentStore:
    """Store for managing Agent configurations.

    Delegates to base/config.py standalone YAML CRUD functions.
    """

    def __init__(self, agents_dir: str | None = None, data_dir: str | None = None):
        cfg = get_default_config_dir()
        resolved_agents_dir = agents_dir if agents_dir is not None else str(cfg / "agents")
        resolved_data_dir = data_dir if data_dir is not None else str(get_default_data_dir())

        self.agents_dir = Path(resolved_agents_dir)
        self.data_dir = Path(resolved_data_dir)
        _yaml_ensure_dir(self.agents_dir)

    def load_all_agents(self) -> dict[str, AgentConfig]:
        return _yaml_load_all(self.agents_dir)

    def get_agent(self, agent_id: str) -> AgentConfig | None:
        filepath = self.agents_dir / f"{agent_id}.yaml"
        if not filepath.exists():
            return None
        return _yaml_load_file(filepath)

    def get_agent_by_name(self, name: str) -> AgentConfig | None:
        for agent in self.load_all_agents().values():
            if agent.name == name:
                return agent
        return None

    def save_agent(self, agent: AgentConfig) -> tuple[bool, str]:
        try:
            _yaml_save(agent, self.agents_dir, self.data_dir)
            return True, "Agent saved"
        except Exception as e:
            return False, f"Failed to save agent: {e}"

    def delete_agent(self, agent_id: str, delete_data: bool = True) -> tuple[bool, str]:
        try:
            _yaml_delete(agent_id, self.agents_dir, self.data_dir if delete_data else None)
            return True, f"Agent '{agent_id}' deleted"
        except ValueError as e:
            return False, str(e)
        except Exception as e:
            return False, f"Failed to delete agent: {e}"

    def agent_exists(self, agent_id: str) -> bool:
        return _yaml_exists(agent_id, self.agents_dir)

    def validate_agent_data(self, data: dict) -> tuple[bool, list[str]]:
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


__all__ = ["AgentStore"]
