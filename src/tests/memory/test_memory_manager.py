"""MemoryManager.load config semantics plus ``forget_fact`` resolution.

``memory/config.json`` is the single per-agent tuning surface: when it
exists it is authoritative (the constructor config is defaults in
production), and its episodic limits must reach the store — which
snapshots them at construction — before it loads. Guards the wiring in
agents/agent_registry.py + memory/manager.py.

``forget_fact`` (issue ) is the reflection engine's deletion path:
exact ID first, then unique-prefix (the reflection prompt shows IDs
truncated); unknown or ambiguous IDs delete nothing and log a warning.
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
    with patch("tlon.memory.manager.paths") as mock_paths:
        mock_paths.agent_memory_dir.return_value = memory_dir
        mock_paths.memory_config_file.return_value = memory_dir / "config.json"
        return MemoryManager("test-agent", MemoryConfig())


async def test_load_disk_config_is_authoritative_and_reaches_stores(
    tmp_path: Path,
) -> None:
    manager = _manager(
        tmp_path,
        {
            "summary": {"window": 4},
            "episodic": {"max_summaries": 7, "max_facts": 9},
            "context": {
                "max_summaries_in_context": 1,
                "max_facts_in_context": 2,
            },
        },
    )

    await manager.load()

    assert manager.config.agent_id == "test-agent"
    assert manager.config.summary_window == 4
    assert manager.config.episodic_max_summaries == 7
    assert manager.config.episodic_max_facts == 9
    assert manager.config.context_max_summaries == 1
    assert manager.config.context_max_facts == 2
    # Store limits are snapshotted at construction — load() must re-apply
    # the merged values before the store reads its files.
    assert manager.episodic.max_summaries == 7
    assert manager.episodic.max_facts == 9


async def test_load_creates_default_config_when_missing(tmp_path: Path) -> None:
    manager = _manager(tmp_path, None)

    await manager.load()

    assert manager.config.agent_id == "test-agent"
    assert (tmp_path / "memory" / "config.json").exists()
    # Constructor defaults survive the first boot (no disk file to merge).
    assert manager.config.summary_window == 3
    assert manager.episodic.max_summaries == 20
    assert manager.episodic.max_facts == 50


async def test_load_tolerates_retired_working_sections(tmp_path: Path) -> None:
    """A pre-ADR-0009 config.json (with working/triggers sections) loads as
    the current defaults — the retired sections are simply not read."""
    manager = _manager(
        tmp_path,
        {
            "working": {"max_items": 3, "summary_window": 4},
            "triggers": {"idle_timeout_minutes": 5},
            "episodic": {"max_summaries": 7, "max_facts": 9},
        },
    )

    await manager.load()

    assert manager.config.summary_window == 3  # default; old keys ignored
    assert manager.config.episodic_max_summaries == 7


# ── forget_fact: ID resolution for the reflection engine (issue ) ──


async def test_forget_fact_exact_id(tmp_path: Path) -> None:
    manager = _manager(tmp_path, None)
    victim_id = await manager.remember_fact("用户偏好深色主题")
    keeper_id = await manager.remember_fact("项目使用 Python")

    assert await manager.forget_fact(victim_id) is True

    assert await manager.episodic.get_fact(victim_id) is None
    assert await manager.episodic.get_fact(keeper_id) is not None


async def test_forget_fact_unique_prefix(tmp_path: Path) -> None:
    manager = _manager(tmp_path, None)
    victim_id = await manager.remember_fact("过时的部署地址")

    assert await manager.forget_fact(victim_id[:8]) is True
    assert await manager.episodic.get_fact(victim_id) is None


async def test_forget_fact_unknown_id_warns_and_deletes_nothing(tmp_path: Path) -> None:
    manager = _manager(tmp_path, None)
    keeper_id = await manager.remember_fact("仍然有效的事实")

    with patch("tlon.memory.manager.logger") as mock_logger:
        assert await manager.forget_fact("no-such-fact-id") is False

    mock_logger.warning.assert_called_once()
    assert mock_logger.warning.call_args.args[0] == "fact_delete_not_found"
    assert await manager.episodic.get_fact(keeper_id) is not None


async def test_forget_fact_ambiguous_prefix_warns_and_deletes_nothing(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path, None)
    await manager.load()
    for fact_id in ("shared-prefix-1", "shared-prefix-2"):
        await manager.episodic.add_fact({"id": fact_id, "description": "x"})

    with patch("tlon.memory.manager.logger") as mock_logger:
        assert await manager.forget_fact("shared-prefix") is False

    mock_logger.warning.assert_called_once()
    assert mock_logger.warning.call_args.args[0] == "fact_delete_ambiguous"
    assert len(await manager.episodic.get_all_facts(include_superseded=True)) == 2
