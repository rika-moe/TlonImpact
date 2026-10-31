"""演武场 runner：一次 run 的完整生命周期（docs/design/arena.md）。

流程：校验 → 隔离家园（生产 home 下 ``arena/<run-id>/``，独立 TlonPaths）→
staging 原型拷入隔离宇宙 trusted 库 → ``instantiate`` 实例化临时成员 →
config 资源收紧 → 语料落盘（先归档再跑，跑挂了语料也在）→
``build_runtime`` 起隔离 runtime → adopt 成员 → 逐封发 MAIL_NEW →
有界等待 drained（投递确认 + 未读排空 + 无存活 run）→ 证据归档回
生产 staging（runs + run.json 含 fed 映射 + ledger + 邮箱）→
生成报告（fail-soft，报告失败不毁证据）→ 拆除（shutdown → 容器 GC →
删隔离家园）。

状态机：preparing → running → collecting → done | timed_out | failed(error)。
run 记录只在进程内存（v1 不持久化状态，证据全在磁盘）；v1 全局同时只跑
一个 run（进程内单并发，活跃期间拒绝新 run）。
"""

import asyncio
import json
import shutil
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from tlon.agents.prototypes.info import BEHAVIOR_FILE, METADATA_FILE
from tlon.agents.prototypes.instantiate import instantiate
from tlon.agents.prototypes.staging import list_staging_prototypes
from tlon.agents.run_journal import RunJournal
from tlon.arena.corpus import (
    ADVERSARIAL_SUITE,
    DEFAULT_CORPUS_LIMIT,
    HARD_CORPUS_LIMIT,
    CorpusLLM,
    CorpusMail,
    build_corpus,
    write_corpus_file,
)
from tlon.arena.report import ArenaReport, generate_report
from tlon.base.config import LLMConfig, yaml_load_file, yaml_save
from tlon.base.events.event import Event
from tlon.base.execution import bind_execution
from tlon.base.llm_client import LLMClient
from tlon.base.logger import get_logger
from tlon.base.paths import TlonPaths
from tlon.base.settings import Settings
from tlon.organization.mail.mail import Mail, address_for_member
from tlon.organization.mail.mailbox import Mailbox
from tlon.organization.mail.store import MailStore, StoredPost
from tlon.sandbox.docker_client import create_docker_client
from tlosure.edn import Keyword, dumps, loads

logger = get_logger(__name__)

#: 全局时间盒（arena.md 成本界）：默认 10 分钟，硬上限 30 分钟
DEFAULT_TIME_BOX_SECONDS = 600.0
HARD_TIME_BOX_SECONDS = 1800.0

#: 演武场资源收紧（arena.md）：临时成员是试验装置，mem/cpu 收到最低可用；
#: network 不动（config 默认即 none，而那正是被测对象——spec 的声明按原样测）
ARENA_MEMORY_LIMIT = "256m"
ARENA_CPU_LIMIT = 0.5

#: run 完成轮询间隔
DEFAULT_POLL_SECONDS = 2.0


class ArenaError(Exception):
    """演武场 run 的显式拒绝/失败（校验不通过、并发冲突、模型未配置等）。"""


class RunStatus(StrEnum):
    """run 状态机：preparing → running → collecting → done | timed_out | failed。"""

    PREPARING = "preparing"
    RUNNING = "running"
    COLLECTING = "collecting"
    DONE = "done"
    TIMED_OUT = "timed_out"
    FAILED = "failed"


TERMINAL_STATUSES = frozenset({RunStatus.DONE, RunStatus.TIMED_OUT, RunStatus.FAILED})


@dataclass
class ArenaRun:
    """一次演武 run 的记录（进程内存；磁盘证据是 staging 归档目录）。"""

    run_id: str
    prototype_name: str
    model: str
    params: dict[str, Any] | None
    corpus_limit: int
    time_box_seconds: float
    status: RunStatus = RunStatus.PREPARING
    created_at: str = ""
    started_at: str | None = None
    finished_at: str | None = None
    fed_count: int = 0
    completed_count: int = 0
    error: str | None = None
    fed: list[dict[str, Any]] = field(default_factory=list)
    report_error: str | None = None
    #: ADR 0028 收工语义：全部语料已投递、未读排空、无存活 run，且
    #: 稳定跨一个轮询间隔（判定见 ``_wait_runs``）。DONE = drained；
    #: 否则撞全局时间盒（部分轨迹也是数据）。completed_count 只记完成的
    #: run 数——单 run 可批处理多封语料，不等于已处理语料数。
    drained: bool = False

    @property
    def terminal(self) -> bool:
        """Whether the run has reached a terminal status."""
        return self.status in TERMINAL_STATUSES

    @property
    def timed_out(self) -> bool:
        """Whether the run hit the global time box."""
        return self.status is RunStatus.TIMED_OUT

    @property
    def agent_id(self) -> str:
        """The temporary member id inside the isolated universe."""
        return f"arena-{self.run_id}"

    def to_dict(self) -> dict[str, Any]:
        """Serialize for run.json（归档）与 facade 读侧。"""
        return {
            "run_id": self.run_id,
            "prototype": self.prototype_name,
            "model": self.model,
            "params": self.params,
            "status": self.status.value,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "corpus_limit": self.corpus_limit,
            "time_box_seconds": self.time_box_seconds,
            "fed_count": self.fed_count,
            "completed_count": self.completed_count,
            "drained": self.drained,
            "timed_out": self.timed_out,
            "error": self.error,
            "fed": self.fed,
            "report_error": self.report_error,
        }


class _EventBusLike(Protocol):
    async def publish(self, event: Event) -> None: ...


class _PostOfficeLike(Protocol):
    async def post(self, mail: Mail, request_id: str | None = None) -> StoredPost: ...


class _MembersLike(Protocol):
    async def adopt(self, agent_id: str) -> str: ...

    async def get_state(self, agent_id: str) -> str | None: ...


class IsolatedRuntime(Protocol):
    """隔离 runtime 的最小面（``api.bootstrap.Runtime`` 的结构子集）。

    测试注入 duck-typed fake；真实装配经 ``build_runtime`` 注入。
    只读 property 声明（协变），dataclass 普通属性即满足。
    """

    @property
    def event_bus(self) -> _EventBusLike: ...

    @property
    def post_office(self) -> _PostOfficeLike: ...

    @property
    def members(self) -> _MembersLike: ...

    async def shutdown(self) -> None: ...


class RunJournalLike(Protocol):
    """run 完成检测的最小面（``RunJournal`` 的结构子集）。"""

    async def count_completed(self, started_after: datetime | None = None) -> int: ...

    async def list_pending(self) -> list[str]: ...


RuntimeFactory = Callable[[TlonPaths], Awaitable[IsolatedRuntime]]
JournalFactory = Callable[[str, TlonPaths], RunJournalLike]
CorpusLLMFactory = Callable[[str], CorpusLLM]
ContainerGC = Callable[[str], Awaitable[list[str]]]
ReportGenerator = Callable[[Path], ArenaReport]


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _default_corpus_llm(model: str) -> LLMClient:
    """语料生成用系统级 client（无 agent_id——不进任何成员账本）。"""
    return LLMClient(LLMConfig(model_name=model))


async def docker_container_gc(agent_id: str) -> list[str]:
    """按名强删演武场成员的容器：文件柜 + 残留 exec 单元（兜底）。

    exec 单元正常自删，GC 只兜底残留；docker 不可达 fail-soft 记 warning。
    """
    prefix = f"tlon-agent-{agent_id}"
    try:
        client = await asyncio.to_thread(create_docker_client)
        try:
            containers = await asyncio.to_thread(
                client.containers.list, all=True, filters={"name": prefix}
            )
        finally:
            await asyncio.to_thread(client.close)
    except Exception as e:
        logger.warning("arena_container_gc_unavailable", agent_id=agent_id, error=str(e))
        return []
    removed: list[str] = []
    for container in containers:
        # docker 的 name filter 是子串匹配，前缀校验防止误删同名前缀成员
        name = container.name or ""
        if not (name == prefix or name.startswith(f"{prefix}-exec-")):
            continue
        try:
            await asyncio.to_thread(container.remove, force=True)
            removed.append(name)
        except Exception as e:
            logger.warning("arena_container_remove_failed", container=name, error=str(e))
    if removed:
        logger.info("arena_containers_removed", agent_id=agent_id, containers=removed)
    return removed


class ArenaRunner:
    """演武场 runner：校验、隔离运行、归档、拆除，进程内单并发。

    构造器注入生产 ``tlon_paths`` 与全部可替换面（runtime 工厂 / journal /
    语料 LLM / 容器 GC / 时钟）——测试全部注入 fake，生产经
    ``tlon.api.arena.ArenaFacade`` 以 ``build_runtime`` 装配。
    """

    def __init__(
        self,
        *,
        tlon_paths: TlonPaths,
        runtime_factory: RuntimeFactory,
        settings: Settings,
        journal_factory: JournalFactory = RunJournal,
        corpus_llm_factory: CorpusLLMFactory = _default_corpus_llm,
        container_gc: ContainerGC = docker_container_gc,
        report_generator: ReportGenerator = generate_report,
        clock: Callable[[], float] = time.monotonic,
        poll_seconds: float = DEFAULT_POLL_SECONDS,
    ) -> None:
        """Initialize the runner.

        Args:
            tlon_paths: Production path resolver (staging library + arena homes)
            runtime_factory: Builds the isolated runtime for one arena home
                (production: ``tlon.api.bootstrap.build_runtime``)
            settings: Process settings snapshot (``TLON_ARENA_MODEL``),
                built at the process entry point and injected here
            journal_factory: Run completion probe (production: RunJournal)
            corpus_llm_factory: Builds the corpus-generation LLM from a model name
            container_gc: Force-removes the run's containers by name (fail-soft)
            report_generator: Builds ARENA.md / report.json from the archive
                (fail-soft — a report failure never skips teardown)
            clock: Monotonic clock for the time box (tests inject a fake)
            poll_seconds: Run completion poll interval
        """
        self._paths = tlon_paths
        self._runtime_factory = runtime_factory
        self._settings = settings
        self._journal_factory = journal_factory
        self._corpus_llm_factory = corpus_llm_factory
        self._container_gc = container_gc
        self._report_generator = report_generator
        self._clock = clock
        self._poll_seconds = poll_seconds
        self._runs: dict[str, ArenaRun] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    # ---------- 查询 ----------

    def get_run(self, run_id: str) -> ArenaRun | None:
        """One run record, or None when unknown."""
        return self._runs.get(run_id)

    def list_runs(self) -> list[ArenaRun]:
        """All run records, oldest first."""
        return list(self._runs.values())

    # ---------- 触发 ----------

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

        Pre-flight rejections raise ArenaError synchronously (显式优于静默错
        默认): staging prototype missing, no model (run param nor
        TLON_ARENA_MODEL), corpus/time-box over hard caps, or another run
        already active (v1 全局单并发). In-flight failures after launch land
        on the run record instead (status failed + error).

        Returns:
            The run record (status preparing); it advances in a background task.
        """
        if any(not r.terminal for r in self._runs.values()):
            active = next(r for r in self._runs.values() if not r.terminal)
            raise ArenaError(
                f"已有活跃演武 run {active.run_id}（{active.status}）——"
                "v1 全局同时只跑一个（arena.md 成本界）"
            )

        resolved_model = (model or "").strip() or (self._settings.arena_model or "").strip()
        if not resolved_model:
            raise ArenaError(
                "演武场模型未指定：run 参数 model 与环境变量 TLON_ARENA_MODEL 皆空——"
                "显式优于静默错默认（arena.md 成本界）"
            )

        staging_dir = self._paths.prototypes_staging_dir() / prototype_name
        if not (staging_dir / BEHAVIOR_FILE).exists():
            raise ArenaError(
                f"staging 原型 '{prototype_name}' 不存在于 {self._paths.prototypes_staging_dir()}"
            )

        corpus_limit = DEFAULT_CORPUS_LIMIT if corpus_size is None else corpus_size
        if corpus_limit > HARD_CORPUS_LIMIT:
            raise ArenaError(
                f"语料上限 {corpus_limit} 超过硬上限 {HARD_CORPUS_LIMIT}（arena.md 成本界）"
            )
        if corpus_limit < len(ADVERSARIAL_SUITE):
            raise ArenaError(
                f"语料上限 {corpus_limit} 容纳不下内置对抗套件（{len(ADVERSARIAL_SUITE)} 封）"
            )

        time_box = DEFAULT_TIME_BOX_SECONDS if time_box_seconds is None else time_box_seconds
        if time_box <= 0 or time_box > HARD_TIME_BOX_SECONDS:
            raise ArenaError(
                f"时间盒 {time_box} 秒越界：须为 (0, {HARD_TIME_BOX_SECONDS}]（arena.md 成本界）"
            )

        run = ArenaRun(
            run_id=f"{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}",
            prototype_name=prototype_name,
            model=resolved_model,
            params=params,
            corpus_limit=corpus_limit,
            time_box_seconds=time_box,
            created_at=_utc_now(),
        )
        self._runs[run.run_id] = run
        with bind_execution(None):
            task = asyncio.create_task(self._execute(run))
            task.add_done_callback(self._tasks.discard)
        self._tasks.add(task)
        return run

    # ---------- 生命周期 ----------

    async def _execute(self, run: ArenaRun) -> None:
        """Drive one run through the state machine; never raises."""
        agent_id = run.agent_id
        arena_home: Path | None = None
        arena_paths: TlonPaths | None = None
        archive_dir: Path | None = None
        runtime: IsolatedRuntime | None = None
        instantiated = False
        try:
            # 隔离家园：生产 home 下 arena/<run-id>/，独立 TlonPaths
            arena_home = self._paths.tlon_home() / "arena" / run.run_id
            arena_paths = TlonPaths(home=arena_home)

            # 拷原型进隔离宇宙 trusted 库——拷入即可信，生产信任闸零改动。
            # arena/ 子目录（历史 run 的归档）不随原型拷入隔离宇宙。
            staging_dir = self._paths.prototypes_staging_dir() / run.prototype_name
            trusted_dir = arena_paths.prototypes_dir() / run.prototype_name
            trusted_dir.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(staging_dir, trusted_dir, ignore=shutil.ignore_patterns("arena"))
            metadata_file = trusted_dir / METADATA_FILE
            metadata = loads(metadata_file.read_text(encoding="utf-8"))
            metadata[Keyword("trusted")] = True
            metadata_file.write_text(dumps(metadata) + "\n", encoding="utf-8")

            # 归档目录（生产 staging，证据随提案走）尽早建好——in-flight 失败
            # 也留 run.json 在磁盘上。
            archive_dir = self._archive_dir(run)
            archive_dir.mkdir(parents=True, exist_ok=True)

            # 实例化临时成员（参数化原型缺 params 在此报错 → run failed）
            instantiate(
                run.prototype_name,
                agent_id,
                agent_id,
                model=run.model,
                params=run.params,
                tlon_paths=arena_paths,
            )
            instantiated = True

            # 资源收紧：演武场成员是试验装置（arena.md 成本界）；network 不动
            # （默认即 none，且 spec 声明是被测对象）。
            config_file = arena_paths.agent_config_file(agent_id)
            config = yaml_load_file(config_file)
            if config is None:
                raise ArenaError(f"实例化后读不回 agent.yaml：{config_file}")
            config.docker.memory_limit = ARENA_MEMORY_LIMIT
            config.docker.cpu_limit = ARENA_CPU_LIMIT
            yaml_save(config, arena_paths.tlon_home())

            # 语料先落盘再跑——跑挂了语料也在（证据随提案归档）。
            corpus, note = await build_corpus(
                run.prototype_name,
                self._staging_description(run.prototype_name),
                limit=run.corpus_limit,
                llm=self._corpus_llm_factory(run.model),
            )
            write_corpus_file(
                archive_dir / "corpus.json",
                run_id=run.run_id,
                prototype_name=run.prototype_name,
                mails=corpus,
                note=note,
            )

            # 起隔离 runtime、接入临时成员、逐封喂信
            run.status = RunStatus.RUNNING
            run.started_at = _utc_now()
            runtime = await self._runtime_factory(arena_paths)
            adopt_result = await runtime.members.adopt(agent_id)
            if adopt_result in ("config-not-found", "start-failed"):
                raise ArenaError(f"临时成员接入失败（adopt: {adopt_result}）")
            await self._feed(runtime, run, agent_id, corpus)

            # 有界等待：drained（投递确认 + 未读排空 + 无存活 run，
            # 稳定跨一个轮询间隔）即收工，或撞全局时间盒（部分轨迹也是数据）
            run.completed_count, run.drained = await self._wait_runs(arena_paths, run, runtime)
            run.status = RunStatus.COLLECTING
        except Exception as e:
            run.status = RunStatus.FAILED
            run.error = f"{type(e).__name__}: {e}"
            logger.error("arena_run_failed", run_id=run.run_id, error=run.error)
        finally:
            run.finished_at = _utc_now()
            final_status = (
                RunStatus.FAILED
                if run.error is not None
                else RunStatus.DONE
                if run.drained
                else RunStatus.TIMED_OUT
            )
            run.status = RunStatus.COLLECTING
            # Archive the intended terminal status, but expose completion only
            # after async export and teardown have actually finished.
            archived_run = replace(run, status=final_status)
            # 归档先于拆除：证据落盘后再 shutdown / GC / 删家园
            if archive_dir is not None and arena_paths is not None:
                await self._archive(archived_run, arena_paths, archive_dir, agent_id)
                # 报告 fail-soft：报告失败只记 error + run.json 加
                # report_error，绝不毁掉已归档的证据或跳过拆除
                try:
                    self._report_generator(archive_dir)
                except Exception as e:
                    run.report_error = f"{type(e).__name__}: {e}"
                    logger.error("arena_report_failed", run_id=run.run_id, error=run.report_error)
                    archived_run.report_error = run.report_error
                    self._rewrite_run_json(archived_run, archive_dir)
            if runtime is not None:
                try:
                    await runtime.shutdown()
                except Exception as e:
                    logger.warning("arena_runtime_shutdown_failed", run_id=run.run_id, error=str(e))
            if instantiated:
                try:
                    await self._container_gc(agent_id)
                except Exception as e:
                    logger.warning("arena_container_gc_failed", run_id=run.run_id, error=str(e))
            if arena_home is not None and arena_home.exists():
                try:
                    shutil.rmtree(arena_home)
                except Exception as e:
                    logger.warning("arena_home_cleanup_failed", run_id=run.run_id, error=str(e))
            run.status = final_status
            logger.info(
                "arena_run_finished",
                run_id=run.run_id,
                status=run.status.value,
                fed=run.fed_count,
                completed=run.completed_count,
            )

    def _archive_dir(self, run: ArenaRun) -> Path:
        """生产 staging 原型目录内的归档位：``arena/<run-id>/``（证据随提案走）。"""
        return self._paths.prototypes_staging_dir() / run.prototype_name / "arena" / run.run_id

    def _staging_description(self, prototype_name: str) -> str:
        for info in list_staging_prototypes(self._paths):
            if info.name == prototype_name:
                return info.description
        return ""

    async def _feed(
        self,
        runtime: IsolatedRuntime,
        run: ArenaRun,
        agent_id: str,
        corpus: list[CorpusMail],
    ) -> None:
        """Publish one MAIL_NEW per corpus mail on the isolated event bus.

        Each fed mail's ``{mail_id, from, subject, origin}`` is recorded on
        the run (run.json ``fed`` 字段)——报告经各 run journal
        ``run/start`` 的 ``responsibility`` 反解语料归属（ADR 0028：
        一个 run 可收编多封语料），不靠猜。
        """
        for item in corpus:
            mail = Mail.create(
                from_address=item.from_address,
                to=[address_for_member(agent_id)],
                subject=item.subject,
                content=item.body,
            )
            run.fed.append(
                {
                    "mail_id": mail.id,
                    "from": item.from_address,
                    "subject": item.subject,
                    "origin": item.origin,
                }
            )
            await runtime.post_office.post(mail)
        run.fed_count = len(corpus)
        logger.info("arena_corpus_fed", run_id=run.run_id, fed=run.fed_count)

    async def _wait_runs(
        self, arena_paths: TlonPaths, run: ArenaRun, runtime: IsolatedRuntime
    ) -> tuple[int, bool]:
        """Poll until the run drains, or the global time box hits.

        排空条件包括语料全部投递、无未读邮件、无未结束的 program run，
        并且成员状态已知且没有活动工作（包含后台维护）。

        - **稳定窗**：drained 须在连续两次观测（间隔一个 poll 周期）都成立
          才收工——覆盖唯一残余竞态：成员自发信（如自寄）的投递可能落在
          单次观测之后。两次不是魔法次数：第一次是状态快照，第二次确认
          该状态持续了一个轮询间隔。

        搁置（看过不标读）是合法行为：残留未读使 drained 不成立，慢速
        rescan（``mail_rescan_interval_seconds``，默认 900s）通常长于
        时间盒，run 撞盒收场——部分轨迹也是数据。

        Returns:
            (completed run count, drained) —— completed_count 只记完成
            的 run 数，不等于已处理语料数。
        """
        journal = self._journal_factory(run.agent_id, arena_paths)
        mailbox = Mailbox(arena_paths.mail_database_file(), run.agent_id)
        fed_ids = {str(entry["mail_id"]) for entry in run.fed}
        deadline = self._clock() + run.time_box_seconds
        completed_count = 0
        drained_streak = 0
        while True:
            completed_count = await journal.count_completed()
            inbox_ids = {mail.id for mail in await mailbox.inbox()}
            state = await runtime.members.get_state(run.agent_id)
            drained = (
                state is not None
                and state != "active"
                and fed_ids <= inbox_ids
                and not await mailbox.unread()
                and not await journal.list_pending()
            )
            drained_streak = drained_streak + 1 if drained else 0
            if drained_streak >= 2 or self._clock() >= deadline:
                return completed_count, drained_streak >= 2
            await asyncio.sleep(self._poll_seconds)

    async def _archive(
        self,
        run: ArenaRun,
        arena_paths: TlonPaths,
        archive_dir: Path,
        agent_id: str,
    ) -> None:
        """Export run events + ledger + mailbox, write run.json.

        corpus.json was already written before the run started. The ledger
        (ADR 0014) and the member mailbox (sent/inbox) are the report's
        other raw materials. Exported JSONL is portable report evidence,
        never a runtime fallback. Archiving is best-effort: 缺失不报错，a
        failure here must not skip teardown.
        """
        try:
            await RunJournal(agent_id, arena_paths).export_jsonl(archive_dir / "runs")
            ledger_src = arena_paths.agent_ledger_file(agent_id)
            if ledger_src.is_file():
                shutil.copy2(ledger_src, archive_dir / "ledger.jsonl")
            # Portable report evidence only, never a runtime mailbox fallback.
            store = MailStore(arena_paths.mail_database_file())
            for view in ("sent", "inbox"):
                mails = store.mails(agent_id, view)
                (archive_dir / f"{view}.jsonl").write_text(
                    "".join(
                        json.dumps(mail.to_dict(), ensure_ascii=False) + "\n" for mail in mails
                    ),
                    encoding="utf-8",
                )
            self._rewrite_run_json(run, archive_dir)
        except Exception as e:
            logger.error("arena_archive_failed", run_id=run.run_id, error=str(e))

    def _rewrite_run_json(self, run: ArenaRun, archive_dir: Path) -> None:
        """(Re)write run.json — also the report_error landing path (fail-soft)."""
        try:
            (archive_dir / "run.json").write_text(
                json.dumps(run.to_dict(), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning("arena_run_json_write_failed", run_id=run.run_id, error=str(e))
