"""Arena facade：演武场 run 的触发与查询（docs/design/arena.md）。

薄透传 ``tlon.arena.runner.ArenaRunner``：构造器注入生产 TlonPaths +
runner 工厂（默认以 ``build_runtime`` 装配隔离 runtime 的标准 runner）。
``ArenaRun`` / ``RunStatus`` / ``ArenaError``（方法返回/抛出类型）作为
本模块 API 面的一部分对 server 可见。
"""

from collections.abc import Callable
from typing import Any

from tlon.api.bootstrap import build_runtime
from tlon.arena.runner import ArenaError, ArenaRun, ArenaRunner, RunStatus
from tlon.base.paths import TlonPaths

__all__ = ["ArenaError", "ArenaFacade", "ArenaRun", "RunStatus"]


def _default_runner(tlon_paths: TlonPaths) -> ArenaRunner:
    return ArenaRunner(tlon_paths=tlon_paths, runtime_factory=build_runtime)


class ArenaFacade:
    """演武场域北向 API。构造器注入 TlonPaths + runner 工厂，薄透传。

    Example:
        >>> arena = ArenaFacade(tlon_paths=paths)
        >>> run = await arena.start_run("llm-idea", model="deepseek/deepseek-chat")
        >>> arena.get_run(run.run_id)
    """

    def __init__(
        self,
        *,
        tlon_paths: TlonPaths,
        runner_factory: Callable[[TlonPaths], ArenaRunner] | None = None,
    ):
        """Initialize the facade.

        Args:
            tlon_paths: Production path resolver (staging library + arena homes)
            runner_factory: Builds the runner (tests inject a faked runner);
                None = standard runner over ``build_runtime``
        """
        factory = runner_factory or _default_runner
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
