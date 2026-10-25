"""Config facade：成员配置的读与 agent.yaml 的受控写。

收编 REST 层对 ``ConfigService.agents`` 内部 dict 的直读和对
``agent.yaml`` 的直接读写（yaml_load_file / yaml_save / LLMConfig）。
"""

from tlon.base.config import (
    AgentConfig,
    ConfigService,
    LLMConfig,
    yaml_load_file,
    yaml_save,
)
from tlon.base.paths import TlonPaths


class ConfigFacade:
    """配置域北向 API。构造器注入 ConfigService + TlonPaths，薄透传。

    Example:
        >>> configs = ConfigFacade(config_service=cs, tlon_paths=paths)
        >>> configs.list_agent_configs()
        >>> configs.update_llm("dev-1", model_name="openai/gpt-5")
    """

    def __init__(self, *, config_service: ConfigService, tlon_paths: TlonPaths):
        """Initialize the facade.

        Args:
            config_service: The runtime config service (live view)
            tlon_paths: Path resolver (agent.yaml locations)
        """
        self._config_service = config_service
        self._paths = tlon_paths

    def list_agent_configs(self) -> list[AgentConfig]:
        """Current agent configurations (live view of the config service)."""
        return list(self._config_service.agents.values())

    def load_agent(self, agent_id: str) -> AgentConfig | None:
        """Load a member's agent.yaml from disk, or None when absent."""
        return yaml_load_file(self._paths.agent_config_file(agent_id))

    def update_llm(
        self,
        agent_id: str,
        *,
        model_name: str | None = None,
        embedding_model_name: str | None = None,
    ) -> AgentConfig | None:
        """Rewrite a member's agent.yaml with updated LLM knobs.

        model_copy preserves the untouched knobs (top_p / an
        explicit context_window override); a fresh LLMConfig would
        silently reset them to defaults. Fields left None keep their
        current value. The caller hot-adopts the member afterwards so the
        running registry picks the change up.

        Returns:
            The saved AgentConfig, or None when the member has no
            agent.yaml on disk
        """
        cfg = self.load_agent(agent_id)
        if cfg is None:
            return None
        base = cfg.llm if cfg.llm is not None else LLMConfig()
        update: dict[str, str] = {}
        if model_name:
            update["model_name"] = model_name
        if embedding_model_name:
            update["embedding_model_name"] = embedding_model_name
        cfg.llm = base.model_copy(update=update)
        yaml_save(cfg, self._paths.tlon_home())
        return cfg
