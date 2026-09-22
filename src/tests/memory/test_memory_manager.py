"""MemoryManager.load config semantics plus ``delete_claim`` resolution.

``memory/config.json`` is the single per-agent tuning surface: when it
exists it is authoritative (the constructor config is defaults in
production). Guards the wiring in agents/agent_registry.py +
memory/manager.py.

``delete_claim`` (issue , renamed under ADR 0017) is the reflection
engine's deletion path: exact ID first, then unique-prefix (the
reflection prompt shows IDs truncated); unknown or ambiguous IDs delete
nothing and log a warning.
"""

import json
from pathlib import Path
from unittest.mock import patch

from tlon.memory.manager import MemoryManager
from tlon.memory.models import MemoryConfig


def _manager(tmp_path: Path, disk_config: dict | None) -> MemoryManager:
    """Build a MemoryManager whose paths resolve under tmp_path."""
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    if disk_config is not None:
        (memory_dir / "config.json").write_text(
            json.dumps(disk_config), encoding="utf-8"
        )
    return MemoryManager("test-agent", MemoryConfig(), data_dir=tmp_path)


async def test_load_disk_config_is_authoritative(tmp_path: Path) -> None:
    manager = _manager(
        tmp_path,
        {
            "distillation": {"window": 4},
            "context": {"max_claims_in_context": 7},
            "reflection": {"min_interactions": 9},
        },
    )

    await manager.load()

    assert manager.config.agent_id == "test-agent"
    assert manager.config.distillation_window == 4
    assert manager.config.context_max_claims == 7
    assert manager.config.reflection_min_interactions == 9


async def test_load_creates_default_config_when_missing(tmp_path: Path) -> None:
    manager = _manager(tmp_path, None)

    await manager.load()

    assert manager.config.agent_id == "test-agent"
    assert (tmp_path / "memory" / "config.json").exists()
    # Constructor defaults survive the first boot (no disk file to merge).
    assert manager.config.distillation_window == 3
    assert manager.config.context_max_claims == 10


async def test_load_tolerates_retired_episodic_sections(tmp_path: Path) -> None:
    """A pre-ADR-0017 config.json (with summary/episodic/old context
    sections) loads as the current defaults — the retired sections are
    simply not read."""
    manager = _manager(
        tmp_path,
        {
            "summary": {"window": 4},
            "episodic": {"max_summaries": 7, "max_facts": 9},
            "context": {"max_summaries_in_context": 1, "max_facts_in_context": 2},
            "working": {"max_items": 3},
            "triggers": {"idle_timeout_minutes": 5},
        },
    )

    await manager.load()

    assert manager.config.distillation_window == 3  # default; old keys ignored
    assert manager.config.context_max_claims == 10


# ── delete_claim: ID resolution for the reflection engine (issue ) ──


async def test_delete_claim_exact_id(tmp_path: Path) -> None:
    manager = _manager(tmp_path, None)
    victim_id = await manager.remember_fact("用户偏好深色主题")
    keeper_id = await manager.remember_fact("项目使用 Python")

    assert await manager.delete_claim(victim_id) is True

    assert await manager.claims.get_claim(victim_id) is None
    assert await manager.claims.get_claim(keeper_id) is not None


async def test_delete_claim_unique_prefix(tmp_path: Path) -> None:
    manager = _manager(tmp_path, None)
    victim_id = await manager.remember_fact("过时的部署地址")

    assert await manager.delete_claim(victim_id[:8]) is True
    assert await manager.claims.get_claim(victim_id) is None


async def test_delete_claim_unknown_id_warns_and_deletes_nothing(tmp_path: Path) -> None:
    manager = _manager(tmp_path, None)
    keeper_id = await manager.remember_fact("仍然有效的记忆")

    with patch("tlon.memory.manager.logger") as mock_logger:
        assert await manager.delete_claim("no-such-claim-id") is False

    mock_logger.warning.assert_called_once()
    assert mock_logger.warning.call_args.args[0] == "claim_delete_not_found"
    assert await manager.claims.get_claim(keeper_id) is not None


async def test_delete_claim_ambiguous_prefix_warns_and_deletes_nothing(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path, None)
    await manager.load()
    for claim_id in ("shared-prefix-1", "shared-prefix-2"):
        await manager.claims.add_claim({"id": claim_id, "text": "x"})

    with patch("tlon.memory.manager.logger") as mock_logger:
        assert await manager.delete_claim("shared-prefix") is False

    mock_logger.warning.assert_called_once()
    assert mock_logger.warning.call_args.args[0] == "claim_delete_ambiguous"
    assert len(await manager.claims.get_all_claims(include_superseded=True)) == 2
