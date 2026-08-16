"""MemoryManager.load config semantics.

``memory/config.json`` is the single per-agent tuning surface: when it
exists it is authoritative (the constructor config is defaults in
production), and its working/episodic limits must reach the stores —
which snapshot them at construction — before they load. Guards the
wiring fix in agents/agent_registry.py + memory/manager.py.
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
            "working": {"max_items": 3, "summary_window": 4},
            "episodic": {"max_summaries": 7, "max_facts": 9},
            "context": {
                "max_summaries_in_context": 1,
                "max_facts_in_context": 2,
                "max_working_in_context": 1,
            },
        },
    )

    await manager.load()

    assert manager.config.agent_id == "test-agent"
    assert manager.config.working_max_items == 3
    assert manager.config.working_summary_window == 4
    assert manager.config.episodic_max_summaries == 7
    assert manager.config.episodic_max_facts == 9
    # Store limits are snapshotted at construction — load() must re-apply
    # the merged values before the stores read their files.
    assert manager.working.max_items == 3
    assert manager.episodic.max_summaries == 7
    assert manager.episodic.max_facts == 9


async def test_load_creates_default_config_when_missing(tmp_path: Path) -> None:
    manager = _manager(tmp_path, None)

    await manager.load()

    assert manager.config.agent_id == "test-agent"
    assert (tmp_path / "memory" / "config.json").exists()
    # Constructor defaults survive the first boot (no disk file to merge).
    assert manager.working.max_items == 10
    assert manager.episodic.max_summaries == 20
    assert manager.episodic.max_facts == 50
