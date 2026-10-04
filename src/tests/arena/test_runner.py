"""ArenaRunner — 一次 run 的完整生命周期（docs/design/arena.md）。

Pins（全部 duck-typed fake + cast 注入，不起真 runtime / docker / LLM）：

- 隔离家园布局：生产 home 下 ``arena/<run-id>/``，staging 原型拷进 trusted 库
- 模型解析三分支：run 参数 / TLON_ARENA_MODEL / 皆无 → ArenaError
- config 收紧落盘：memory_limit 256m / cpu_limit 0.5，network 不动
- 喂信：每封语料一个 MAIL_NEW，收件人是临时成员地址
- 有界等待：fake journal 逐封完成 → done；撞时间盒 → timed_out 且拆除照走
- 拆除顺序：shutdown → 容器 GC → 删隔离家园；docker 不可达 fail-soft
- 归档：corpus.json / sessions/ / run.json 落进 staging 原型目录的 arena/<run-id>/
- 单并发：活跃 run 期间第二个 start_run 拒绝
- instantiate 失败（多给 params）→ failed + 清理发生 + run.json 落盘
"""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from tlon.arena.runner import (
    ArenaError,
    ArenaRun,
    ArenaRunner,
    IsolatedRuntime,
    RunStatus,
    docker_container_gc,
)
from tlon.base.config import AgentConfig, yaml_load_file
from tlon.base.events.event import Event, EventType
from tlon.base.llm_client import LLMResponse
from tlon.base.paths import TlonPaths
from tlon.base.settings import Settings
from tlon.organization.mail.mail import Mail, address_for_member

_RELAY_SPEC = """\
{:version 1
 :rules [{:name "relay"
          :on :mail-delivered
          :actions [(forward {:to ["x@tlon.internal"]})]}]}
"""

_TYPICAL_JSON = json.dumps(
    [{"from": f"user-{i}@example.com", "subject": f"请求 {i}", "body": f"正文 {i}"} for i in range(3)]
)


def _write_staging_proto(paths: TlonPaths, name: str = "relay") -> Path:
    proto_dir = paths.prototypes_staging_dir() / name
    proto_dir.mkdir(parents=True, exist_ok=True)
    (proto_dir / "behavior.tlon").write_text(_RELAY_SPEC, encoding="utf-8")
    (proto_dir / "prototype.edn").write_text(
        f'{{:name "{name}" :display-name "{name}" :description "转发邮件的中继成员"'
        " :trusted false}",
        encoding="utf-8",
    )
    return proto_dir


class _FakeMembers:
    def __init__(self) -> None:
        self.adopted: list[str] = []

    async def adopt(self, agent_id: str) -> str:
        self.adopted.append(agent_id)
        return "registered-lazy"


class _FakeEventBus:
    def __init__(self) -> None:
        self.events: list[Event] = []

    async def publish(self, event: Event) -> None:
        self.events.append(event)


class _FakeRuntime:
    def __init__(self) -> None:
        self.event_bus = _FakeEventBus()
        self.members = _FakeMembers()
        self.shutdown_called = False

    async def shutdown(self) -> None:
        self.shutdown_called = True


class _FakeJournal:
    """每轮 list_completed 多完成 per_poll 个 session（上限 total），并把
    journal 文件写进隔离家园 sessions 目录供归档断言。"""

    def __init__(self, agent_id: str, tlon_paths: TlonPaths, *, per_poll: int, total: int) -> None:
        self._dir = tlon_paths.agent_sessions_dir(agent_id)
        self._per_poll = per_poll
        self._total = total
        self._done = 0

    async def list_completed(self) -> list[str]:
        self._done = min(self._total, self._done + self._per_poll)
        if self._done:
            self._dir.mkdir(parents=True, exist_ok=True)
        for i in range(self._done):
            (self._dir / f"s{i}.jsonl").write_text(
                json.dumps({"type": "session/end", "reason": "completed"}) + "\n",
                encoding="utf-8",
            )
        return [f"s{i}" for i in range(self._done)]


class _FakeClock:
    """每次调用前进 step 秒的假时钟。"""

    def __init__(self, step: float = 0.0) -> None:
        self.now = 0.0
        self.step = step

    def __call__(self) -> float:
        value = self.now
        self.now += self.step
        return value


class _FakeGC:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def __call__(self, agent_id: str) -> list[str]:
        self.calls.append(agent_id)
        return [f"tlon-agent-{agent_id}"]


class _FailingGC:
    async def __call__(self, agent_id: str) -> list[str]:
        raise RuntimeError("docker daemon down")


class _FakeCorpusLLM:
    def __init__(self, content: str = _TYPICAL_JSON) -> None:
        self._content = content

    async def chat(self, system_prompt: str, messages: list[dict[str, Any]]) -> LLMResponse:
        return LLMResponse(content=self._content)


def _settings(arena_model: str = "") -> Settings:
    return cast(Settings, SimpleNamespace(arena_model=arena_model))


class _Harness:
    """一个 runner + 全部 fake 的线头集合。"""

    def __init__(
        self,
        tmp_path: Path,
        *,
        settings: Settings | None = None,
        clock_step: float = 0.0,
        real_clock: bool = False,
        journal_per_poll: int = 3,
        journal_total: int = 8,
        gc: Any = None,
        llm_content: str = _TYPICAL_JSON,
    ) -> None:
        self.paths = TlonPaths(home=tmp_path)
        self.runtime = _FakeRuntime()
        self.runtime_seen_paths: TlonPaths | None = None
        self.config_at_build: AgentConfig | None = None
        self.gc = gc if gc is not None else _FakeGC()
        self.clock = __import__("time").monotonic if real_clock else _FakeClock(step=clock_step)

        async def runtime_factory(arena_paths: TlonPaths) -> IsolatedRuntime:
            self.runtime_seen_paths = arena_paths
            agent_yaml = list(arena_paths.tlon_home().glob("arena-*/config/agent.yaml"))
            if agent_yaml:
                self.config_at_build = yaml_load_file(agent_yaml[0])
            return cast(IsolatedRuntime, self.runtime)

        def journal_factory(agent_id: str, tlon_paths: TlonPaths) -> _FakeJournal:
            return _FakeJournal(
                agent_id, tlon_paths, per_poll=journal_per_poll, total=journal_total
            )

        self.runner = ArenaRunner(
            tlon_paths=self.paths,
            runtime_factory=runtime_factory,
            settings=settings or _settings(),
            journal_factory=journal_factory,
            corpus_llm_factory=lambda model: cast(Any, _FakeCorpusLLM(llm_content)),
            container_gc=self.gc,
            clock=self.clock,
            poll_seconds=0.01,
        )


async def _wait_terminal(run: ArenaRun, timeout: float = 10.0) -> RunStatus:
    for _ in range(int(timeout / 0.01)):
        if run.terminal:
            return run.status
        await asyncio.sleep(0.01)
    raise AssertionError(f"run 未在 {timeout}s 内到达终态（当前 {run.status}）")


def _archive_dir(h: _Harness, run: ArenaRun) -> Path:
    return h.paths.prototypes_staging_dir() / run.prototype_name / "arena" / run.run_id


async def test_happy_path_layout_feed_archive_teardown(tmp_path: Path) -> None:
    h = _Harness(tmp_path, settings=_settings())
    proto_dir = _write_staging_proto(h.paths)

    run = await h.runner.start_run("relay", model="deepseek/test-chat")
    assert run.status is RunStatus.PREPARING
    assert await _wait_terminal(run) is RunStatus.DONE

    agent_id = f"arena-{run.run_id}"
    # 隔离家园布局 + staging 原型拷进 trusted 库（拆除前在 runtime 工厂里见过）
    assert h.runtime_seen_paths is not None
    assert h.runtime_seen_paths.tlon_home() == tmp_path / "arena" / run.run_id
    # 隔离家园已拆；staging 原件完好
    assert not (tmp_path / "arena" / run.run_id).exists()
    assert (proto_dir / "behavior.tlon").read_text(encoding="utf-8") == _RELAY_SPEC

    # 成员接入 + 每封语料一个 MAIL_NEW
    assert h.runtime.members.adopted == [agent_id]
    mail_events = [e for e in h.runtime.event_bus.events if e.type is EventType.MAIL_NEW]
    assert len(mail_events) == 8  # 默认上限 8 = 5 对抗 + 3 典型
    assert run.fed_count == 8
    assert run.completed_count == 8
    for event in mail_events:
        mail = event.payload
        assert isinstance(mail, Mail)
        assert mail.to == [address_for_member(agent_id)]
        assert "@" in mail.from_address

    # config 收紧（runtime 装配时读到的 agent.yaml）
    assert h.config_at_build is not None
    assert h.config_at_build.docker.memory_limit == "256m"
    assert h.config_at_build.docker.cpu_limit == 0.5
    assert h.config_at_build.docker.network_mode == "none"
    assert h.config_at_build.llm is not None
    assert h.config_at_build.llm.model_name == "deepseek/test-chat"

    # 拆除：runtime shutdown + 容器 GC 按成员名调用
    assert h.runtime.shutdown_called
    assert h.gc.calls == [agent_id]

    # 归档：corpus.json / sessions/ / run.json 全在 staging 原型目录内
    archive = _archive_dir(h, run)
    corpus = json.loads((archive / "corpus.json").read_text(encoding="utf-8"))
    assert len(corpus["mails"]) == 8
    assert [m["origin"] for m in corpus["mails"][:5]] == ["adversarial"] * 5
    assert corpus["note"] is None
    assert len(list((archive / "sessions").glob("*.jsonl"))) == 8
    record = json.loads((archive / "run.json").read_text(encoding="utf-8"))
    assert record["status"] == "done"
    assert record["fed_count"] == 8
    assert record["completed_count"] == 8
    assert record["timed_out"] is False
    assert record["model"] == "deepseek/test-chat"
    assert record["error"] is None


async def test_model_resolution_param_wins(tmp_path: Path) -> None:
    h = _Harness(tmp_path, settings=_settings("env/model"))
    _write_staging_proto(h.paths)
    run = await h.runner.start_run("relay", model="param/model")
    await _wait_terminal(run)
    assert run.model == "param/model"


async def test_model_resolution_env_fallback(tmp_path: Path) -> None:
    h = _Harness(tmp_path, settings=_settings("env/model"))
    _write_staging_proto(h.paths)
    run = await h.runner.start_run("relay")
    await _wait_terminal(run)
    assert run.model == "env/model"


async def test_model_resolution_missing_raises(tmp_path: Path) -> None:
    h = _Harness(tmp_path, settings=_settings(""))
    _write_staging_proto(h.paths)
    with pytest.raises(ArenaError, match="TLON_ARENA_MODEL"):
        await h.runner.start_run("relay")


async def test_staging_prototype_missing_raises(tmp_path: Path) -> None:
    h = _Harness(tmp_path)
    with pytest.raises(ArenaError, match="不存在"):
        await h.runner.start_run("ghost", model="m/x")


async def test_corpus_size_caps(tmp_path: Path) -> None:
    h = _Harness(tmp_path)
    _write_staging_proto(h.paths)
    with pytest.raises(ArenaError, match="硬上限"):
        await h.runner.start_run("relay", model="m/x", corpus_size=21)
    with pytest.raises(ArenaError, match="对抗套件"):
        await h.runner.start_run("relay", model="m/x", corpus_size=3)


async def test_time_box_cap(tmp_path: Path) -> None:
    h = _Harness(tmp_path)
    _write_staging_proto(h.paths)
    with pytest.raises(ArenaError, match="时间盒"):
        await h.runner.start_run("relay", model="m/x", time_box_seconds=1801)


async def test_time_box_path_times_out_and_still_tears_down(tmp_path: Path) -> None:
    h = _Harness(tmp_path, clock_step=100.0, journal_per_poll=1, journal_total=0)
    _write_staging_proto(h.paths)

    run = await h.runner.start_run("relay", model="m/x", time_box_seconds=300.0)
    assert await _wait_terminal(run) is RunStatus.TIMED_OUT
    assert run.timed_out
    assert run.fed_count == 8
    assert run.completed_count == 0

    # 拆除仍执行：shutdown + GC + 隔离家园删除
    assert h.runtime.shutdown_called
    assert h.gc.calls == [f"arena-{run.run_id}"]
    assert not (tmp_path / "arena" / run.run_id).exists()

    # 部分证据也归档：corpus.json + run.json（无 sessions 目录——零完成）
    archive = _archive_dir(h, run)
    assert (archive / "corpus.json").exists()
    record = json.loads((archive / "run.json").read_text(encoding="utf-8"))
    assert record["status"] == "timed_out"
    assert record["timed_out"] is True
    assert not (archive / "sessions").exists()


async def test_teardown_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    h = _Harness(tmp_path)
    _write_staging_proto(h.paths)
    order: list[str] = []

    original_shutdown = h.runtime.shutdown

    async def shutdown() -> None:
        order.append("shutdown")
        await original_shutdown()

    h.runtime.shutdown = shutdown  # type: ignore[method-assign]

    gc = h.gc

    async def recording_gc(agent_id: str) -> list[str]:
        order.append("gc")
        return await gc(agent_id)

    h.runner._container_gc = recording_gc

    import tlon.arena.runner as runner_mod

    original_rmtree = runner_mod.shutil.rmtree

    def rmtree(path: Any, *args: Any, **kwargs: Any) -> None:
        order.append("rmtree")
        original_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(runner_mod.shutil, "rmtree", rmtree)

    run = await h.runner.start_run("relay", model="m/x")
    await _wait_terminal(run)
    assert order == ["shutdown", "gc", "rmtree"]


async def test_container_gc_failure_is_fail_soft(tmp_path: Path) -> None:
    h = _Harness(tmp_path, gc=_FailingGC())
    _write_staging_proto(h.paths)
    run = await h.runner.start_run("relay", model="m/x")
    assert await _wait_terminal(run) is RunStatus.DONE
    assert not (tmp_path / "arena" / run.run_id).exists()


async def test_single_concurrency_rejects_second_run(tmp_path: Path) -> None:
    # 真实时钟 + 永不完成的 journal：run 卡在 running 直到 0.3s 时间盒
    h = _Harness(tmp_path, real_clock=True, journal_per_poll=1, journal_total=0)
    _write_staging_proto(h.paths)

    first = await h.runner.start_run("relay", model="m/x", time_box_seconds=0.3)
    with pytest.raises(ArenaError, match="只跑一个"):
        await h.runner.start_run("relay", model="m/x")
    assert await _wait_terminal(first) is RunStatus.TIMED_OUT

    # 第一个到终态后可以继续跑
    second = await h.runner.start_run("relay", model="m/x", time_box_seconds=0.3)
    await _wait_terminal(second)
    assert len(h.runner.list_runs()) == 2


async def test_instantiate_failure_marks_failed_and_cleans_up(tmp_path: Path) -> None:
    h = _Harness(tmp_path)
    _write_staging_proto(h.paths)

    # relay 不声明 :parameters，多给 params → instantiate 拒绝
    run = await h.runner.start_run("relay", model="m/x", params={"unexpected": "x"})
    assert await _wait_terminal(run) is RunStatus.FAILED
    assert run.error is not None and ":parameters" in run.error

    # runtime 从未装配、GC 无需调用；隔离家园已删
    assert h.runtime_seen_paths is None
    assert h.gc.calls == []
    assert not (tmp_path / "arena" / run.run_id).exists()

    # run.json 仍落盘（失败也是证据）；语料未到生成阶段
    archive = _archive_dir(h, run)
    record = json.loads((archive / "run.json").read_text(encoding="utf-8"))
    assert record["status"] == "failed"
    assert record["error"]
    assert not (archive / "corpus.json").exists()


class _FakeContainer:
    def __init__(self, name: str) -> None:
        self.name = name
        self.removed_force: bool | None = None

    def remove(self, force: bool = False) -> None:
        self.removed_force = force


class _FakeContainers:
    def __init__(self, containers: list[_FakeContainer]) -> None:
        self._containers = containers

    def list(self, all: bool = False, filters: dict[str, str] | None = None) -> list[_FakeContainer]:
        return self._containers


class _FakeDockerClient:
    def __init__(self, containers: list[_FakeContainer]) -> None:
        self.containers = _FakeContainers(containers)
        self.closed = False

    def close(self) -> None:
        self.closed = True


async def test_docker_container_gc_removes_only_the_runs_containers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cabinets = _FakeContainer("tlon-agent-arena-r1")
    exec_unit = _FakeContainer("tlon-agent-arena-r1-exec-ab12cd34")
    lookalike = _FakeContainer("tlon-agent-arena-r10")  # 子串撞名，不是本 run 的
    unrelated = _FakeContainer("tlon-agent-other")
    containers = [cabinets, exec_unit, lookalike, unrelated]
    client = _FakeDockerClient(containers)
    monkeypatch.setattr(
        "tlon.arena.runner.create_docker_client", lambda: cast(Any, client)
    )

    removed = await docker_container_gc("arena-r1")
    assert removed == ["tlon-agent-arena-r1", "tlon-agent-arena-r1-exec-ab12cd34"]
    assert cabinets.removed_force is True
    assert exec_unit.removed_force is True
    assert lookalike.removed_force is None
    assert unrelated.removed_force is None
    assert client.closed


async def test_docker_container_gc_daemon_unreachable_fail_soft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom() -> Any:
        raise RuntimeError("no docker socket")

    monkeypatch.setattr("tlon.arena.runner.create_docker_client", boom)
    assert await docker_container_gc("arena-r1") == []
