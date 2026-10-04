"""ArenaFacade — 薄透传校验（runner 工厂注入 + start/get/list 转发）。"""

from pathlib import Path
from typing import Any, cast

from tlon.api.arena import ArenaError, ArenaFacade, ArenaRun, RunStatus
from tlon.arena.runner import ArenaRunner
from tlon.base.paths import TlonPaths


class _StubRunner:
    """记录调用的 duck-typed runner。"""

    def __init__(self) -> None:
        self.started: list[dict[str, Any]] = []

    async def start_run(self, prototype_name: str, **kwargs: Any) -> ArenaRun:
        self.started.append({"prototype_name": prototype_name, **kwargs})
        return ArenaRun(
            run_id="r1",
            prototype_name=prototype_name,
            model=kwargs.get("model") or "m/x",
            params=kwargs.get("params"),
            corpus_limit=8,
            time_box_seconds=600.0,
            status=RunStatus.PREPARING,
        )

    def get_run(self, run_id: str) -> ArenaRun | None:
        return None

    def list_runs(self) -> list[ArenaRun]:
        return []


def test_facade_default_surfaces() -> None:
    assert ArenaError is not None and RunStatus is not None and ArenaRun is not None


async def test_facade_delegates_to_runner(tmp_path: Path) -> None:
    stub = _StubRunner()
    facade = ArenaFacade(
        tlon_paths=TlonPaths(home=tmp_path),
        runner_factory=lambda paths: cast(ArenaRunner, stub),
    )
    run = await facade.start_run(
        "relay", model="m/x", params={"a": 1}, corpus_size=10, time_box_seconds=30.0
    )
    assert run.run_id == "r1"
    assert stub.started == [
        {
            "prototype_name": "relay",
            "model": "m/x",
            "params": {"a": 1},
            "corpus_size": 10,
            "time_box_seconds": 30.0,
        }
    ]
    assert facade.get_run("r1") is None
    assert facade.list_runs() == []


def test_facade_default_runner_factory(tmp_path: Path) -> None:
    facade = ArenaFacade(tlon_paths=TlonPaths(home=tmp_path))
    assert isinstance(facade._runner, ArenaRunner)
