"""LLMConfig yaml round trip — the member-level llm section (ADR 0016),
including the embedding model knob (ADR 0017 stage 2)."""

from pathlib import Path

from tlon.base.config import AgentConfig, LLMConfig, yaml_load_file, yaml_save
from tlon.base.paths import TlonPaths


def _config_file(tmp_path: Path, agent_id: str) -> Path:
    return TlonPaths(home=tmp_path).agent_config_file(agent_id)


def test_embedding_model_name_round_trips(tmp_path: Path) -> None:
    config = AgentConfig(
        id="a1",
        name="A1",
        llm=LLMConfig(
            model_name="deepseek/deepseek-chat",
            embedding_model_name="openai/text-embedding-3-small",
        ),
    )
    yaml_save(config, tmp_path)

    loaded = yaml_load_file(_config_file(tmp_path, "a1"))

    assert loaded is not None
    assert loaded.llm is not None
    assert loaded.llm.embedding_model_name == "openai/text-embedding-3-small"


def test_embedding_model_name_defaults_to_none_and_stays_out_of_yaml(
    tmp_path: Path,
) -> None:
    """Unset means no vector retrieval — and the default never pollutes
    the member's yaml (exclude_none on save)."""
    config = AgentConfig(id="a2", name="A2", llm=LLMConfig(model_name="deepseek/deepseek-chat"))
    yaml_save(config, tmp_path)

    assert "embedding_model_name" not in _config_file(tmp_path, "a2").read_text(encoding="utf-8")
    loaded = yaml_load_file(_config_file(tmp_path, "a2"))
    assert loaded is not None and loaded.llm is not None
    assert loaded.llm.embedding_model_name is None
