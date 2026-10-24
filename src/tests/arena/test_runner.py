"""ArenaRunner — 一次 run 的完整生命周期（docs/design/arena.md）。

Pins（全部 duck-typed fake + cast 注入，不起真 runtime / docker / LLM）：

- 隔离家园布局：生产 home 下 ``arena/<run-id>/``，staging 原型拷进 trusted 库
- 模型解析三分支：run 参数 / TLON_ARENA_MODEL / 皆无 → ArenaError
- config 收紧落盘：memory_limit 256m / cpu_limit 0.5，network 不动
- 喂信：每封语料一个 MAIL_NEW，收件人是临时成员地址；fake event bus 同步
  模拟投递（MAIL_NEW 即落收件箱）
- 有界等待（ADR 0028 drained）：fake journal 模拟调度循环——把当前未读
  批收编进一个 run（run/start 带 responsibility）完成并标读；
  全部收编 → done；部分收编（剩余未读）→ timed_out 且 drained=False
- 拆除顺序：shutdown → 容器 GC → 删隔离家园；docker 不可达 fail-soft
- 归档：corpus.json / runs/ / run.json 落进 staging 原型目录的 arena/<run-id>/
- 单并发：活跃 run 期间第二个 start_run 拒绝
- instantiate 失败（多给 params）→ failed + 清理发生 + run.json 落盘
"""

import asyncio
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from tests.history_helpers import append_history
from tests.program_helpers import MINIMAL_MODULES, write_program
from tlon.agents.program_package import program_digest
from tlon.agents.run_journal import RunJournal
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
from tlon.organization.mail.mailbox import Mailbox
from tlon.organization.mail.store import StoredPost

_RELAY_SPEC = "{:version 2 :handlers {:mail-delivered work/handle}}"

_TYPICAL_JSON = json.dumps(
    [
        {"from": f"user-{i}@example.com", "subject": f"请求 {i}", "body": f"正文 {i}"}
        for i in range(3)
    ]
)


def _write_staging_proto(paths: TlonPaths, name: str = "relay") -> Path:
    proto_dir = paths.prototypes_staging_dir() / name
    proto_dir.mkdir(parents=True, exist_ok=True)
    write_program(proto_dir / "behavior.tlon", _RELAY_SPEC)
    (proto_dir / "prototype.tlon").write_text(
        f'{{:name "{name}" :display-name "{name}" :description "转发邮件的中继成员"'
        f' :program-digest "{program_digest(_RELAY_SPEC, MINIMAL_MODULES)}" :trusted false}}',
        encoding="utf-8",
    )
    return proto_dir


class _FakeMembers:
    def __init__(self) -> None:
        self.adopted: list[str] = []
        self.states: list[str | None] = ["waiting"]
        self.state_observations = 0

    async def adopt(self, agent_id: str) -> str:
        self.adopted.append(agent_id)
        return "registered-lazy"

    async def get_state(self, agent_id: str) -> str | None:
        self.state_observations += 1
        return self.states.pop(0) if len(self.states) > 1 else self.states[0]


class _FakeEventBus:
    """隔离宇宙投递的同步化：MAIL_NEW 发布即投递进成员收件箱。

    mail_dir 由 runtime 工厂在装配时接线（此刻临时成员已实例化）。
    """

    def __init__(self) -> None:
        self.events: list[Event] = []
        self.mail_dir: Path | None = None

    async def publish(self, event: Event) -> None:
        self.events.append(event)
        if event.type is EventType.MAIL_NEW and self.mail_dir is not None:
            mail = event.payload
            assert isinstance(mail, Mail)
            await Mailbox(
                self.mail_dir.parent.parent / "shared" / "mail.sqlite3", self.mail_dir.parent.name
            ).append_inbox(mail)


class _FakePostOffice:
    def __init__(self, bus: _FakeEventBus) -> None:
        self.bus = bus

    async def post(self, mail: Mail, request_id: str | None = None) -> StoredPost:
        await self.bus.publish(Event(type=EventType.MAIL_NEW, payload=mail, source="arena"))
        return StoredPost(mail, request_id or mail.id, [], [])


class _FakeRuntime:
    def __init__(self) -> None:
        self.event_bus = _FakeEventBus()
        self.members = _FakeMembers()
        self.post_office = _FakePostOffice(self.event_bus)
        self.shutdown_called = False

    async def shutdown(self) -> None:
        self.shutdown_called = True


class _FakeJournal:
    """ADR 0028 调度循环的同步化模拟：每轮把当前未读（至多 batch 封）
    批收编进一个 run——run/start 带触发语模板与 responsibility
    快照，立即完成并把收编邮件标读；runs 额度（max_runs）用完后
    转搁置（剩余未读不再收编，模拟部分收编/看过不标读）。run 事实
    写进隔离家园 SQLite，供归档导出断言。"""

    def __init__(self, agent_id: str, tlon_paths: TlonPaths, *, batch: int, max_runs: int) -> None:
        self._agent_id = agent_id
        self._journal = RunJournal(agent_id, tlon_paths)
        self._mailbox = Mailbox(tlon_paths.mail_database_file(), agent_id)
        self._batch = batch
        self._remaining = max_runs
        self._count = 0

    async def _step(self) -> None:
        if self._remaining <= 0:
            return
        unread = await self._mailbox.unread()
        if not unread:
            return
        take = unread[: min(self._batch, self._remaining)]
        for mail in take:
            self._count += 1
            self._remaining -= 1
            run_id = f"{self._agent_id}-{self._count:012d}"
            await append_history(
                self._journal,
                run_id,
                "run/start",
                agent_id=self._agent_id,
                trigger_content=mail.content,
                mail_id=mail.id,
            )
            await append_history(self._journal, run_id, "run/end", reason="completed")
            await self._mailbox.mark_read(mail.id)

    async def count_completed(self, started_after: datetime | None = None) -> int:
        await self._step()
        return await self._journal.count_completed(started_after=started_after)

    async def list_pending(self) -> list[str]:
        return await self._journal.list_pending()


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
        journal_batch: int = 8,
        journal_max_runs: int = 8,
        gc: Any = None,
        llm_content: str = _TYPICAL_JSON,
        journal_factory: Any = None,
        report_generator: Any = None,
    ) -> None:
        self.paths = TlonPaths(home=tmp_path)
        self.runtime = _FakeRuntime()
        self.runtime_seen_paths: TlonPaths | None = None
        self.config_at_build: AgentConfig | None = None
        self.prototype_metadata_at_build: dict[str, Any] | None = None
        self.gc = gc if gc is not None else _FakeGC()
        self.clock = __import__("time").monotonic if real_clock else _FakeClock(step=clock_step)

        async def runtime_factory(arena_paths: TlonPaths) -> IsolatedRuntime:
            self.runtime_seen_paths = arena_paths
            from tlosure.edn import loads

            self.prototype_metadata_at_build = loads(
                (arena_paths.prototypes_dir() / "relay" / "prototype.tlon").read_text()
            )
            agent_yaml = list(arena_paths.tlon_home().glob("arena-*/config/agent.yaml"))
            if agent_yaml:
                self.config_at_build = yaml_load_file(agent_yaml[0])
                agent_id = agent_yaml[0].parent.parent.name
                # 投递接线：fake event bus 的 MAIL_NEW 落进临时成员收件箱
                self.runtime.event_bus.mail_dir = arena_paths.agent_mail_dir(agent_id)
            return cast(IsolatedRuntime, self.runtime)

        def default_journal_factory(agent_id: str, tlon_paths: TlonPaths) -> _FakeJournal:
            return _FakeJournal(
                agent_id, tlon_paths, batch=journal_batch, max_runs=journal_max_runs
            )

        runner_kwargs: dict[str, Any] = {}
        if report_generator is not None:
            runner_kwargs["report_generator"] = report_generator

        self.runner = ArenaRunner(
            tlon_paths=self.paths,
            runtime_factory=runtime_factory,
            settings=settings or _settings(),
            journal_factory=journal_factory or default_journal_factory,
            corpus_llm_factory=lambda model: cast(Any, _FakeCorpusLLM(llm_content)),
            container_gc=self.gc,
            clock=self.clock,
            poll_seconds=0.01,
            **runner_kwargs,
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
    original_metadata = (proto_dir / "prototype.tlon").read_bytes()

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
    assert (proto_dir / "prototype.tlon").read_bytes() == original_metadata
    assert h.prototype_metadata_at_build is not None
    assert h.prototype_metadata_at_build["trusted"] is True
    assert h.prototype_metadata_at_build["program-digest"] == program_digest(
        _RELAY_SPEC, MINIMAL_MODULES
    )

    # 成员接入 + 每封语料一个 MAIL_NEW
    assert h.runtime.members.adopted == [agent_id]
    mail_events = [e for e in h.runtime.event_bus.events if e.type is EventType.MAIL_NEW]
    assert len(mail_events) == 8  # 默认上限 8 = 5 对抗 + 3 典型
    assert run.fed_count == 8
    # ADR 0028：一个 run 批处理全部 8 封（fake 调度循环一轮收编）
    assert run.completed_count == 8
    assert run.drained is True
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

    # 归档：corpus.json / runs/ / run.json 全在 staging 原型目录内
    archive = _archive_dir(h, run)
    corpus = json.loads((archive / "corpus.json").read_text(encoding="utf-8"))
    assert len(corpus["mails"]) == 8
    assert [m["origin"] for m in corpus["mails"][:5]] == ["adversarial"] * 5
    assert corpus["note"] is None
    exported = list((archive / "runs").glob("*.jsonl"))
    assert len(exported) == 8
    events = [json.loads(line) for line in exported[0].read_text(encoding="utf-8").splitlines()]
    assert [event["seq"] for event in events] == [0, 1]
    assert [event["type"] for event in events] == ["run/start", "run/end"]
    record = json.loads((archive / "run.json").read_text(encoding="utf-8"))
    assert record["status"] == "done"
    assert record["fed_count"] == 8
    assert record["completed_count"] == 8
    assert record["drained"] is True
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
    # journal 零额度（从不收编）：未读永不排空 → 撞时间盒
    h = _Harness(tmp_path, clock_step=100.0, journal_max_runs=0)
    _write_staging_proto(h.paths)

    run = await h.runner.start_run("relay", model="m/x", time_box_seconds=300.0)
    assert await _wait_terminal(run) is RunStatus.TIMED_OUT
    assert run.timed_out
    assert run.fed_count == 8
    assert run.completed_count == 0
    assert run.drained is False

    # 拆除仍执行：shutdown + GC + 隔离家园删除
    assert h.runtime.shutdown_called
    assert h.gc.calls == [f"arena-{run.run_id}"]
    assert not (tmp_path / "arena" / run.run_id).exists()

    # 部分证据也归档：corpus.json + run.json（无 runs 目录——零 run）
    archive = _archive_dir(h, run)
    assert (archive / "corpus.json").exists()
    record = json.loads((archive / "run.json").read_text(encoding="utf-8"))
    assert record["status"] == "timed_out"
    assert record["timed_out"] is True
    assert record["drained"] is False
    assert not (archive / "runs").exists()


async def test_partial_collection_times_out_drained_false(tmp_path: Path) -> None:
    """部分收编（ADR 0028）：一个 run 收编 3 封后额度耗尽，其余 5 封
    搁置未读——慢速 rescan（默认 900s）长于时间盒，run 撞盒收场：
    drained=False，报告里 3 封归 run、5 封进 unmatched。"""
    h = _Harness(tmp_path, clock_step=100.0, journal_batch=3, journal_max_runs=3)
    _write_staging_proto(h.paths)

    run = await h.runner.start_run("relay", model="m/x", time_box_seconds=300.0)
    assert await _wait_terminal(run) is RunStatus.TIMED_OUT
    assert run.fed_count == 8
    assert run.completed_count == 3
    assert run.drained is False

    archive = _archive_dir(h, run)
    record = json.loads((archive / "run.json").read_text(encoding="utf-8"))
    assert record["status"] == "timed_out"
    assert record["drained"] is False
    payload = json.loads((archive / "report.json").read_text(encoding="utf-8"))
    assert payload["summary"]["mails_covered"] == 3
    assert payload["summary"]["mails_uncovered"] == 5
    assert len(payload["runs"]) == 3
    assert all(len(entry["mails"]) == 1 for entry in payload["runs"])
    assert len(payload["unmatched_mails"]) == 5


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
    # 真实时钟 + 零额度 journal（未读永不排空）：run 卡在 running 直到 0.3s 时间盒
    h = _Harness(tmp_path, real_clock=True, journal_max_runs=0)
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

    def list(
        self, all: bool = False, filters: dict[str, str] | None = None
    ) -> list[_FakeContainer]:
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
    monkeypatch.setattr("tlon.arena.runner.create_docker_client", lambda: cast(Any, client))

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


# ── 报告接线（issue #29 切片 2/3）：归档原料补全 + fed 映射 + fail-soft 报告 ──


async def test_run_archives_report_and_fed_mapping(tmp_path: Path) -> None:
    h = _Harness(tmp_path, settings=_settings())
    _write_staging_proto(h.paths)

    run = await h.runner.start_run("relay", model="deepseek/test-chat")
    assert await _wait_terminal(run) is RunStatus.DONE

    archive = _archive_dir(h, run)
    record = json.loads((archive / "run.json").read_text(encoding="utf-8"))

    # fed 映射：每封语料一条 {mail_id, from, subject, origin}，与 MAIL_NEW 一一对应
    fed = record["fed"]
    assert len(fed) == 8
    assert all(set(entry) == {"mail_id", "from", "subject", "origin"} for entry in fed)
    mail_events = [e for e in h.runtime.event_bus.events if e.type is EventType.MAIL_NEW]
    event_mail_ids = [cast(Mail, e.payload).id for e in mail_events]
    assert [entry["mail_id"] for entry in fed] == event_mail_ids
    assert [entry["origin"] for entry in fed[:5]] == ["adversarial"] * 5
    assert record["report_error"] is None

    # 报告自动生成：report.json + ARENA.md 双落点（归档目录 + staging 原型根）
    # 每封触发邮件对应一个 program run，报告按 run 组织
    payload = json.loads((archive / "report.json").read_text(encoding="utf-8"))
    assert payload["run_id"] == run.run_id
    assert payload["summary"]["fed"] == 8
    assert payload["summary"]["mails_covered"] == 8
    assert payload["summary"]["mails_uncovered"] == 0
    assert payload["summary"]["drained"] is True
    assert payload["unmatched_mails"] == []
    assert len(payload["runs"]) == 8
    assert [m["mail_id"] for entry in payload["runs"] for m in entry["mails"]] == event_mail_ids
    assert all(entry["trigger"] == entry["mails"][0]["body"] for entry in payload["runs"])
    assert (archive / "ARENA.md").exists()
    root_md = h.paths.prototypes_staging_dir() / run.prototype_name / "ARENA.md"
    assert root_md.read_text(encoding="utf-8") == (archive / "ARENA.md").read_text(encoding="utf-8")


async def test_archive_includes_ledger_and_mailbox(tmp_path: Path) -> None:
    """journal_factory 是打进隔离家园的注入点：补写 ledger + sent，验证归档
    三样原料；inbox.jsonl 由 fake event bus 的投递产生（8 封语料）。"""

    def journal_factory(agent_id: str, tlon_paths: TlonPaths) -> _FakeJournal:
        ledger = tlon_paths.agent_ledger_file(agent_id)
        ledger.parent.mkdir(parents=True, exist_ok=True)
        ledger.write_text(
            json.dumps({"prompt_tokens": 3, "completion_tokens": 4}) + "\n", encoding="utf-8"
        )
        mail_dir = tlon_paths.agent_mail_dir(agent_id)
        mail_dir.mkdir(parents=True, exist_ok=True)
        (mail_dir / "sent.jsonl").write_text(
            json.dumps({"id": "r1", "to": ["x@example.com"], "content": "回信"}) + "\n",
            encoding="utf-8",
        )
        legacy_dir = tlon_paths.agent_dir(agent_id) / "runs"
        legacy_dir.mkdir()
        (legacy_dir / "legacy.jsonl").write_text("old offline archive\n", encoding="utf-8")
        return _FakeJournal(agent_id, tlon_paths, batch=8, max_runs=8)

    h = _Harness(tmp_path, settings=_settings(), journal_factory=journal_factory)
    _write_staging_proto(h.paths)

    run = await h.runner.start_run("relay", model="m/x")
    assert await _wait_terminal(run) is RunStatus.DONE

    archive = _archive_dir(h, run)
    assert (archive / "ledger.jsonl").exists()
    assert (archive / "sent.jsonl").exists()
    assert not (archive / "runs" / "legacy.jsonl").exists()
    assert len(list((archive / "runs").glob("*.jsonl"))) == 8
    # inbox.jsonl 是投递产物：8 封语料全在
    inbox_lines = (archive / "inbox.jsonl").read_text(encoding="utf-8").splitlines()
    assert len([line for line in inbox_lines if line.strip()]) == 8
    # 报告汇总到了账本 token
    payload = json.loads((archive / "report.json").read_text(encoding="utf-8"))
    assert payload["summary"]["total_tokens"] == 7


async def test_report_failure_is_fail_soft(tmp_path: Path) -> None:
    """报告生成器抛异常：run 仍正常终态 + report_error 落 run.json + 拆除照走。"""

    def boom(archive_dir: Path) -> Any:
        raise RuntimeError("report exploded")

    h = _Harness(tmp_path, settings=_settings(), report_generator=boom)
    _write_staging_proto(h.paths)

    run = await h.runner.start_run("relay", model="m/x")
    assert await _wait_terminal(run) is RunStatus.DONE

    # 拆除照走：shutdown + GC + 隔离家园删除
    assert h.runtime.shutdown_called
    assert h.gc.calls == [f"arena-{run.run_id}"]
    assert not (tmp_path / "arena" / run.run_id).exists()

    # 已归档的证据完好；report_error 落 run.json；无报告产物
    archive = _archive_dir(h, run)
    assert (archive / "corpus.json").exists()
    assert (archive / "runs").exists()
    assert not (archive / "report.json").exists()
    record = json.loads((archive / "run.json").read_text(encoding="utf-8"))
    assert "report exploded" in record["report_error"]
    assert record["status"] == "done"


@pytest.mark.parametrize("state", ["active", None])
async def test_drain_waits_for_maintenance_or_known_state(
    tmp_path: Path, state: str | None
) -> None:
    h = _Harness(tmp_path)
    _write_staging_proto(h.paths)
    h.runtime.members.states = [state, state, "waiting"]
    run = await h.runner.start_run("relay", model="m/x")
    assert await _wait_terminal(run) is RunStatus.DONE
    assert run.drained
    assert h.runtime.members.state_observations == 4
