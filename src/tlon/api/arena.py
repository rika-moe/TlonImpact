"""Arena facade：演武场 run 的触发与查询（docs/design/arena.md）。

薄透传 ``tlon.arena.runner.ArenaRunner``：构造器注入生产 TlonPaths +
Settings + runner 工厂（默认以 ``build_runtime`` 装配隔离 runtime 的标准 runner）。
``ArenaRun`` / ``RunStatus`` / ``ArenaError``（方法返回/抛出类型）作为
本模块 API 面的一部分对 server 可见。另有两个读侧归档方法
（``get_report`` / ``latest_report``）直读 staging 归档目录——报告是
磁盘证据，读它不需要 runner。
"""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from tlon.arena.runner import ArenaError, ArenaRun, ArenaRunner, RunStatus
from tlon.base.logger import get_logger
from tlon.base.paths import TlonPaths
from tlon.base.settings import Settings

__all__ = ["ArenaError", "ArenaFacade", "ArenaRun", "RunStatus"]

logger = get_logger(__name__)


def _default_runner(tlon_paths: TlonPaths, settings: Settings) -> ArenaRunner:
    # 延迟 import 打破环：api.bootstrap 装配 ArenaFacade（顶层 import 本模块）。
    from tlon.api.bootstrap import build_runtime

    return ArenaRunner(
        tlon_paths=tlon_paths,
        runtime_factory=lambda arena_paths: build_runtime(arena_paths, settings),
        settings=settings,
    )


class ArenaFacade:
    """演武场域北向 API。构造器注入 TlonPaths + Settings + runner 工厂，薄透传。

    Example:
        >>> arena = ArenaFacade(tlon_paths=paths, settings=settings)
        >>> run = await arena.start_run("llm-idea", model="deepseek/deepseek-chat")
        >>> arena.get_run(run.run_id)
    """

    def __init__(
        self,
        *,
        tlon_paths: TlonPaths,
        settings: Settings,
        runner_factory: Callable[[TlonPaths], ArenaRunner] | None = None,
    ):
        """Initialize the facade.

        Args:
            tlon_paths: Production path resolver (staging library + arena homes)
            settings: Process settings snapshot (injected into the default
                runner; the runner never reads the environment itself)
            runner_factory: Builds the runner (tests inject a faked runner);
                None = standard runner over ``build_runtime``
        """
        factory = runner_factory or (
            lambda paths: _default_runner(paths, settings)
        )
        self._paths = tlon_paths
        self._runner = factory(tlon_paths)

    async def start_run(
        self,
        prototype_name: str,
        *,
        model: str | None = None,
        params: dict[str, Any] | None = None,
        corpus_size: int | None = None,
        time_box_seconds: float | None = None,
    ) -> ArenaRun:
        """Validate and launch one arena run in the background.

        Returns:
            The run record (status preparing); it advances in a background task.

        Raises:
            ArenaError: staging prototype missing, no model configured,
                corpus/time-box over hard caps, or another run already active
        """
        return await self._runner.start_run(
            prototype_name,
            model=model,
            params=params,
            corpus_size=corpus_size,
            time_box_seconds=time_box_seconds,
        )

    def get_run(self, run_id: str) -> ArenaRun | None:
        """One run record, or None when unknown."""
        return self._runner.get_run(run_id)

    def list_runs(self) -> list[ArenaRun]:
        """All run records, oldest first."""
        return self._runner.list_runs()

    # ---------- 归档读侧（直读磁盘证据，不过 runner） ----------

    def _archive_root(self, prototype_name: str) -> Path:
        """staging 原型目录内的演武归档位：``<staging>/<name>/arena/``。"""
        return self._paths.prototypes_staging_dir() / prototype_name / "arena"

    def get_report(self, prototype_name: str, run_id: str) -> dict[str, Any] | None:
        """Read one run's report.json + ARENA.md from its archive dir.

        Returns:
            ``{"report": <report.json 对象>, "arena_md": <ARENA.md 全文>}``；
            report.json 缺失或损坏（含 run_id 越出归档位）时 None
        """
        archive_dir = self._archive_root(prototype_name) / run_id
        report_file = archive_dir / "report.json"
        if not report_file.is_file():
            return None
        try:
            report = json.loads(report_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("arena_report_read_failed", file_path=str(report_file), error=str(e))
            return None
        if not isinstance(report, dict):
            return None
        md_file = archive_dir / "ARENA.md"
        arena_md = md_file.read_text(encoding="utf-8") if md_file.is_file() else ""
        return {"report": report, "arena_md": arena_md}

    def latest_report(self, prototype_name: str) -> dict[str, Any] | None:
        """Latest archived run's summary for the staging detail payload.

        run_id 以 ``YYYYMMDD-HHMMSS-`` 时间戳开头，字典序即时间序。只看
        最新一个 run 目录：它有 report.json 才返回
        ``{"latest_run": <run.json 对象或 None>, "red_flags": <红旗列表>}``，
        否则 None（进行中的 run 与报告生成失败的 run 都不冒名旧报告）。
        """
        arena_root = self._archive_root(prototype_name)
        if not arena_root.is_dir():
            return None
        run_dirs = sorted(d.name for d in arena_root.iterdir() if d.is_dir())
        if not run_dirs:
            return None
        run_dir = arena_root / run_dirs[-1]
        report_file = run_dir / "report.json"
        if not report_file.is_file():
            return None
        try:
            report = json.loads(report_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("arena_report_read_failed", file_path=str(report_file), error=str(e))
            return None
        if not isinstance(report, dict):
            return None
        latest_run: dict[str, Any] | None = None
        run_file = run_dir / "run.json"
        if run_file.is_file():
            try:
                parsed = json.loads(run_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as e:
                logger.warning("arena_run_json_read_failed", file_path=str(run_file), error=str(e))
            else:
                if isinstance(parsed, dict):
                    latest_run = parsed
        red_flags = report.get("red_flags")
        return {
            "latest_run": latest_run,
            "red_flags": red_flags if isinstance(red_flags, list) else [],
        }
