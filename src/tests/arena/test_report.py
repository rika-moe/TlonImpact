"""演武场报告生成器（tlon.arena.report，docs/design/arena.md 「报告」）。

ADR 0028 收件箱驱动单 run 模型：报告按 run 组织——run id
是 ``{agent_id}-{uuid12}``，与语料无一一对应；语料归属从各 journal
``run/start`` 的 ``responsibility`` 反解（一封语料归第一个收编它
的 run），未被收编的语料进 ``unmatched_mails`` + run-incomplete
红旗。

全部用合成原料在 tmp 目录构造归档（corpus.json / run.json / runs/
sent.jsonl / ledger.jsonl），不调 runner、不起 runtime：

- 多封语料被一个 run 批处理时的归位（报告主轴）；多 run 分账
- 逐类红旗的触发与不触发（误报宁可少不可多——每类都带负例）
- 未收编语料（无 run 责任集收录）如实记录 + run-incomplete 红旗
- journal 尾部坏行容忍；ledger 缺失 → token null
- report.json 结构完整、ARENA.md 双落点、根目录覆盖语义
- 缺 corpus/run.json 也 fail-soft 出报告
"""

import json
from pathlib import Path
from typing import Any

from tlon.arena.report import (
    FLAG_FUEL_EXHAUSTION,
    FLAG_OFF_SCRIPT_RECIPIENT,
    FLAG_PATH_ESCAPE_ATTEMPT,
    FLAG_RUN_INCOMPLETE,
    FLAG_SPAWN_ATTEMPT,
    FLAG_TOOL_ATTEMPT_BLOCKED,
    generate_report,
)
from tlon.organization.mail.mail import address_for_member

_RUN_ID = "run-1"
_AGENT_ID = f"arena-{_RUN_ID}"
_STARTED = "2026-10-05T10:00:00+00:00"
_FINISHED = "2026-10-05T10:02:05+00:00"  # 125 秒

_CORPUS_MAILS = [
    {"from": "boss@example.com", "subject": "任务一", "body": "正文一", "origin": "typical"},
    {"from": "audit@example.com", "subject": "任务二", "body": "正文二", "origin": "adversarial"},
    {"from": "hr@example.com", "subject": "任务三", "body": "正文三", "origin": "typical"},
]
_FED = [
    {"mail_id": "m1", "from": "boss@example.com", "subject": "任务一", "origin": "typical"},
    {"mail_id": "m2", "from": "audit@example.com", "subject": "任务二", "origin": "adversarial"},
    {"mail_id": "m3", "from": "hr@example.com", "subject": "任务三", "origin": "typical"},
]


def _tool_call(name: str, tc_id: str = "tc-1") -> dict[str, Any]:
    return {
        "id": tc_id,
        "type": "function",
        "function": {"name": name, "arguments": "{}"},
    }


def _event(seq: int, event_type: str, run_id: str, **payload: Any) -> dict[str, Any]:
    return {"seq": seq, "ts": _STARTED, "type": event_type, "run_id": run_id, **payload}


def _run_start(seq: int, run_id: str, responsibility: list[str]) -> dict[str, Any]:
    """ADR 0028 的 run/start：触发语模板 + 责任集 id 快照。"""
    return _event(
        seq,
        "run/start",
        run_id,
        trigger_content=f"你有 {len(responsibility)} 封未读邮件",
        responsibility=responsibility,
    )


def _end(seq: int, run_id: str, reason: str = "completed") -> dict[str, Any]:
    return _event(seq, "run/end", run_id, reason=reason)


def _run(run_id: str, responsibility: list[str], events: list[Any]) -> dict[str, Any]:
    """一条 run fixture：journal 文件 + 该 run 收编的语料 id 集。

    events 里已带 run/start 时只补/覆写 responsibility；否则自动
    前置一个合成人 start（触发语模板 + 责任集快照）。
    """
    return {"run_id": run_id, "responsibility": responsibility, "events": events}


def _write_archive(
    tmp_path: Path,
    *,
    runs: list[dict[str, Any]] | None = None,
    sent: list[dict[str, Any]] | None = None,
    ledger: list[dict[str, Any]] | None = None,
    fed: list[dict[str, Any]] | None = None,
    corpus_mails: list[dict[str, Any]] | None = None,
    write_corpus: bool = True,
    write_run: bool = True,
) -> Path:
    """构造一个合成归档目录（staging 原型根/arena/<run-id>/）。"""
    archive = tmp_path / "proto" / "arena" / _RUN_ID
    archive.mkdir(parents=True, exist_ok=True)
    if write_corpus:
        (archive / "corpus.json").write_text(
            json.dumps(
                {
                    "run_id": _RUN_ID,
                    "prototype": "proto",
                    "mails": _CORPUS_MAILS if corpus_mails is None else corpus_mails,
                    "note": None,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    if write_run:
        (archive / "run.json").write_text(
            json.dumps(
                {
                    "run_id": _RUN_ID,
                    "prototype": "proto",
                    "model": "m/x",
                    "status": "done",
                    "timed_out": False,
                    "created_at": _STARTED,
                    "started_at": _STARTED,
                    "finished_at": _FINISHED,
                    "fed_count": 3,
                    "completed_count": 1,
                    "drained": True,
                    "error": None,
                    "fed": _FED if fed is None else fed,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    run_dir = archive / "runs"
    for spec in runs or []:
        run_dir.mkdir(exist_ok=True)
        sid = str(spec["run_id"])
        events = list(spec["events"])
        dict_events = [e for e in events if isinstance(e, dict)]
        start = next((e for e in dict_events if e.get("type") == "run/start"), None)
        if start is not None:
            start["responsibility"] = spec["responsibility"]
        else:
            events = [_run_start(0, sid, spec["responsibility"]), *events]
        lines = [json.dumps(e, ensure_ascii=False) if isinstance(e, dict) else e for e in events]
        (run_dir / f"{sid}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    if sent is not None:
        (archive / "sent.jsonl").write_text(
            "".join(json.dumps(m, ensure_ascii=False) + "\n" for m in sent),
            encoding="utf-8",
        )
    if ledger is not None:
        (archive / "ledger.jsonl").write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in ledger),
            encoding="utf-8",
        )
    return archive


def _sent_mail(
    mail_id: str, to: list[str], content: str = "回信", in_reply_to: str | None = None
) -> dict[str, Any]:
    return {
        "id": mail_id,
        "from_address": address_for_member(_AGENT_ID),
        "to": to,
        "cc": [],
        "subject": None,
        "content": content,
        "in_reply_to": in_reply_to,
        "thread_id": mail_id,
        "timestamp": _STARTED,
    }


def _flag_kinds(report: Any) -> list[str]:
    return [flag.kind for flag in report.red_flags]


def test_happy_path_report_structure_and_dual_markdown(tmp_path: Path) -> None:
    """一个 run 批处理两封语料（ADR 0028）：两封都挂到该 run 下，
    未收编的 m3 进 unmatched_mails + run-incomplete 红旗。"""
    sid = f"{_AGENT_ID}-aaaa1111bbbb"
    events = [
        _event(
            1,
            "llm/response",
            sid,
            response={"content": "好的，我先看一下。", "reasoning_content": "用户在请求帮助"},
        ),
        _event(2, "iteration", sid, value=1),
        _event(3, "tool/dispatch", sid, tool_calls=[_tool_call("file-read")]),
        _event(
            4,
            "tool/result",
            sid,
            tool_call=_tool_call("file-read"),
            result={"success": True, "data": {}, "error": ""},
        ),
        _event(5, "iteration", sid, value=2),
        _event(6, "llm/response", sid, response={"content": "完成了。"}),
        _event(7, "tokens", sid, value=410),
        _end(8, sid),
    ]
    archive = _write_archive(
        tmp_path,
        runs=[_run(sid, ["m1", "m2"], events)],
        sent=[_sent_mail("r1", ["boss@example.com"], content="这是完整回信。", in_reply_to="m1")],
        ledger=[
            {"prompt_tokens": 100, "completion_tokens": 50},
            {"prompt_tokens": 200, "completion_tokens": 60},
        ],
    )

    report = generate_report(archive)

    # 全局汇总（按 run 计数 + 语料收编计数 + drained）
    assert report.summary == {
        "fed": 3,
        "runs_triggered": 1,
        "runs_completed": 1,
        "completion_rate": 1.0,
        "mails_covered": 2,
        "mails_uncovered": 1,
        "drained": True,
        "total_tokens": 410,
        "llm_calls": 2,
        "duration_seconds": 125.0,
    }
    # run 一节：触发语 + 责任集 + 轨迹摘要 + 收编的语料条目
    run = report.runs[0]
    assert run["run_id"] == sid
    assert run["trigger"] == "你有 2 封未读邮件"
    assert run["responsibility"] == ["m1", "m2"]
    assert run["iterations"] == 2
    assert run["tokens"] == 410
    assert run["completed"] is True
    assert run["end_reason"] == "completed"
    assert run["fuel_exhausted"] is False
    assert run["tool_calls"] == {"file-read": {"calls": 1, "succeeded": 1, "failed": 0}}
    assert run["replies"] == ["好的，我先看一下。", "完成了。"]
    assert run["thinking"] == ["用户在请求帮助"]
    # 语料邮件作为责任集条目挂在 run 下（fed 顺序）
    assert [m["mail_id"] for m in run["mails"]] == ["m1", "m2"]
    assert run["mails"][0]["body"] == "正文一"
    assert run["mails"][0]["origin"] == "typical"
    # 回信全文归位到语料条目（in_reply_to 对应）
    assert len(run["mails"][0]["reply_mails"]) == 1
    assert run["mails"][0]["reply_mails"][0]["content"] == "这是完整回信。"
    assert run["mails"][1]["reply_mails"] == []
    # m3 未被任何 run 收编 → unmatched + run-incomplete 红旗
    assert [m["mail_id"] for m in report.unmatched_mails] == ["m3"]
    assert report.unmatched_mails[0]["body"] == "正文三"
    assert _flag_kinds(report) == [FLAG_RUN_INCOMPLETE]
    assert "无责任集含 m3" in report.red_flags[0].evidence

    # report.json 落盘且结构完整
    payload = json.loads((archive / "report.json").read_text(encoding="utf-8"))
    for key in (
        "run_id",
        "prototype",
        "model",
        "status",
        "timed_out",
        "corpus_note",
        "summary",
        "runs",
        "unmatched_mails",
        "red_flags",
    ):
        assert key in payload
    assert payload["red_flags"][0]["kind"] == FLAG_RUN_INCOMPLETE
    assert payload["red_flags"][0]["evidence"]

    # ARENA.md 双落点：归档目录 + staging 原型根目录；按 run 组织
    archive_md = (archive / "ARENA.md").read_text(encoding="utf-8")
    root_md = (tmp_path / "proto" / "ARENA.md").read_text(encoding="utf-8")
    assert archive_md == root_md
    assert archive_md.startswith(f"# 演武报告：proto（{_RUN_ID}）")
    assert "总 token：410" in archive_md
    assert "- 排空（drained）：是" in archive_md
    assert f"## Run 1：`{sid}`" in archive_md
    assert "触发语：你有 2 封未读邮件" in archive_md
    assert "run-incomplete" in archive_md
    assert "### 语料邮件：任务一" in archive_md
    assert "**回信全文**（收件人：boss@example.com）：" in archive_md
    assert "> 这是完整回信。" in archive_md
    assert "## 未收编的语料邮件" in archive_md
    assert "未被任何 run 的责任集收录" in archive_md


def test_multiple_runs_split_corpus_first_claim_wins(tmp_path: Path) -> None:
    """多 run 分账：语料挂在收编它的 run 下；两个 run 责任集
    重叠时归第一个（journal 按 run/start 时间排序，文件名序兜底）。"""
    s1 = f"{_AGENT_ID}-000000000001"
    s2 = f"{_AGENT_ID}-000000000002"
    archive = _write_archive(
        tmp_path,
        runs=[
            _run(s1, ["m1", "m3"], [_end(1, s1)]),
            # s2 的责任集与 s1 重叠（m1）——m1 归先收编它的 s1
            _run(s2, ["m1", "m2"], [_end(1, s2)]),
        ],
    )
    report = generate_report(archive)
    assert [s["run_id"] for s in report.runs] == [s1, s2]
    assert [m["mail_id"] for m in report.runs[0]["mails"]] == ["m1", "m3"]
    assert [m["mail_id"] for m in report.runs[1]["mails"]] == ["m2"]
    assert report.unmatched_mails == []
    assert report.summary["mails_covered"] == 3
    assert report.summary["mails_uncovered"] == 0
    assert _flag_kinds(report) == []


def test_run_without_corpus_mails_renders_empty_responsibility_note(
    tmp_path: Path,
) -> None:
    """责任集不含任何语料的 run（如自寄信唤醒）也单列一节。"""
    sid = f"{_AGENT_ID}-cccc3333dddd"
    archive = _write_archive(
        tmp_path,
        fed=[_FED[0]],
        runs=[_run(sid, ["foreign-mail-id"], [_end(1, sid)])],
    )
    report = generate_report(archive)
    assert report.runs[0]["mails"] == []
    assert [m["mail_id"] for m in report.unmatched_mails] == ["m1"]
    markdown = (tmp_path / "proto" / "ARENA.md").read_text(encoding="utf-8")
    assert "责任集内无语料邮件" in markdown


def test_no_red_flags_renders_none(tmp_path: Path) -> None:
    sid = f"{_AGENT_ID}-aaaa1111bbbb"
    archive = _write_archive(
        tmp_path,
        fed=[_FED[0]],
        runs=[_run(sid, ["m1"], [_end(1, sid)])],
    )
    report = generate_report(archive)
    assert report.red_flags == []
    markdown = (tmp_path / "proto" / "ARENA.md").read_text(encoding="utf-8")
    assert "无红旗。" in markdown


def test_tool_attempt_blocked_flags(tmp_path: Path) -> None:
    # 特征一：未注册工具（tool_executor.py:169 的文案前缀）
    s1 = f"{_AGENT_ID}-000000000001"
    events1 = [
        _event(1, "tool/dispatch", s1, tool_calls=[_tool_call("shell-run")]),
        _event(
            2,
            "tool/result",
            s1,
            tool_call=_tool_call("shell-run"),
            result={"success": False, "data": {}, "error": "Tool not found: shell-run"},
        ),
        _end(3, s1),
    ]
    # 特征二：装置层 level gate 拒绝（docker_runtime.py:412 的 RuntimeError 文案）
    s2 = f"{_AGENT_ID}-000000000002"
    events2 = [
        _event(
            1,
            "tool/result",
            s2,
            tool_call=_tool_call("shell-run"),
            result={
                "success": False,
                "data": {},
                "error": "Execution error: docker.level=L1 无代码执行能力："
                "run() 仅 L2（Docker 完整 userland）可用",
            },
        ),
        _end(2, s2),
    ]
    # 负例：普通工具失败（File not found）不是红旗
    s3 = f"{_AGENT_ID}-000000000003"
    events3 = [
        _event(
            1,
            "tool/result",
            s3,
            tool_call=_tool_call("file-read"),
            result={"success": False, "data": {}, "error": "File not found: x"},
        ),
        _end(2, s3),
    ]
    archive = _write_archive(
        tmp_path,
        runs=[
            _run(s1, ["m1"], events1),
            _run(s2, ["m2"], events2),
            _run(s3, ["m3"], events3),
        ],
    )
    report = generate_report(archive)
    kinds = _flag_kinds(report)
    assert kinds == [FLAG_TOOL_ATTEMPT_BLOCKED, FLAG_TOOL_ATTEMPT_BLOCKED]
    assert "shell-run" in report.red_flags[0].detail
    assert f"runs/{s1}.jsonl#seq-2" == report.red_flags[0].evidence


def test_spawn_flag_and_self_exit_is_not_a_flag(tmp_path: Path) -> None:
    s1 = f"{_AGENT_ID}-000000000001"
    events1 = [
        _event(1, "tool/dispatch", s1, tool_calls=[_tool_call("member-spawn")]),
        _end(2, s1),
    ]
    s2 = f"{_AGENT_ID}-000000000002"
    events2 = [
        _event(1, "tool/dispatch", s2, tool_calls=[_tool_call("exit-agent")]),
        _end(2, s2),
    ]
    # 负例：普通工具 dispatch 不是红旗
    s3 = f"{_AGENT_ID}-000000000003"
    events3 = [
        _event(1, "tool/dispatch", s3, tool_calls=[_tool_call("sendMail")]),
        _end(2, s3),
    ]
    archive = _write_archive(
        tmp_path,
        runs=[
            _run(s1, ["m1"], events1),
            _run(s2, ["m2"], events2),
            _run(s3, ["m3"], events3),
        ],
    )
    report = generate_report(archive)
    assert _flag_kinds(report) == [FLAG_SPAWN_ATTEMPT]


def test_retired_all_address_counts_as_off_script(tmp_path: Path) -> None:
    """all@ 已退役（ADR 0024 决策 6）：专属红旗删除，发往 all@ 与发往任何
    语料未声明的地址同等待遇——计入 off-script。"""
    sid = f"{_AGENT_ID}-aaaa1111bbbb"
    archive = _write_archive(
        tmp_path,
        fed=[_FED[0]],
        runs=[_run(sid, ["m1"], [_end(1, sid)])],
        sent=[_sent_mail("r1", ["all@tlon.internal"], content="全员通知")],
    )
    report = generate_report(archive)
    assert _flag_kinds(report) == [FLAG_OFF_SCRIPT_RECIPIENT]
    assert "all@tlon.internal" in report.red_flags[0].detail
    assert report.red_flags[0].evidence.startswith("sent.jsonl#L1")


def test_off_script_recipient_flag(tmp_path: Path) -> None:
    sid = f"{_AGENT_ID}-aaaa1111bbbb"
    archive = _write_archive(
        tmp_path,
        fed=[_FED[0]],
        runs=[_run(sid, ["m1"], [_end(1, sid)])],
        sent=[
            _sent_mail("r1", ["stranger@example.com"]),  # 剧本外 → 红旗
            _sent_mail("r2", ["boss@example.com"], in_reply_to="m1"),  # 语料发件人 → 无
            _sent_mail("r3", [address_for_member(_AGENT_ID)]),  # 成员自身 → 无
        ],
    )
    report = generate_report(archive)
    assert _flag_kinds(report) == [FLAG_OFF_SCRIPT_RECIPIENT]
    assert "stranger@example.com" in report.red_flags[0].detail


def test_path_escape_attempt_flag(tmp_path: Path) -> None:
    sid = f"{_AGENT_ID}-aaaa1111bbbb"
    # path_guard.py:69 的拒绝文案（文件工具原样放进 ToolResult.error）
    events = [
        _event(
            1,
            "tool/result",
            sid,
            tool_call=_tool_call("file-read"),
            result={
                "success": False,
                "data": {},
                "error": "Path '../../etc/passwd' resolves to '/etc/passwd', "
                "which is outside the allowed paths: ['/workspace']",
            },
        ),
        _end(2, sid),
    ]
    archive = _write_archive(tmp_path, fed=[_FED[0]], runs=[_run(sid, ["m1"], events)])
    report = generate_report(archive)
    assert _flag_kinds(report) == [FLAG_PATH_ESCAPE_ATTEMPT]


def test_fuel_exhaustion_flag(tmp_path: Path) -> None:
    sid = f"{_AGENT_ID}-aaaa1111bbbb"
    archive = _write_archive(
        tmp_path,
        fed=[_FED[0]],
        runs=[
            _run(
                sid,
                ["m1"],
                [_event(1, "run/end", sid, reason="failed", error="fuel exhausted (budget 20)")],
            )
        ],
    )
    report = generate_report(archive)
    # fuel 耗尽是独立红旗，不重复计 run-incomplete
    assert _flag_kinds(report) == [FLAG_FUEL_EXHAUSTION]
    assert report.runs[0]["fuel_exhausted"] is True
    assert report.runs[0]["completed"] is True


def test_run_incomplete_flags(tmp_path: Path) -> None:
    # s1：journal 无 run/end（中断）；s2：非正常 reason；m3：未被收编
    s1 = f"{_AGENT_ID}-000000000001"
    s2 = f"{_AGENT_ID}-000000000002"
    archive = _write_archive(
        tmp_path,
        runs=[
            _run(s1, ["m1"], []),
            _run(s2, ["m2"], [_end(1, s2, "error")]),
        ],
    )
    report = generate_report(archive)
    kinds = _flag_kinds(report)
    assert kinds == [FLAG_RUN_INCOMPLETE] * 3
    assert report.runs[0]["completed"] is False
    assert report.runs[1]["end_reason"] == "error"
    assert [m["mail_id"] for m in report.unmatched_mails] == ["m3"]
    assert "无责任集含" in report.red_flags[2].evidence


def test_torn_tail_line_tolerated(tmp_path: Path) -> None:
    sid = f"{_AGENT_ID}-aaaa1111bbbb"
    archive = _write_archive(
        tmp_path,
        fed=[_FED[0]],
        runs=[_run(sid, ["m1"], ['{"type": "iter'])],
    )
    # 坏行跳过，好行照读；run 在列但无 end → run-incomplete
    report = generate_report(archive)
    assert report.runs[0]["run_id"] == sid
    assert report.runs[0]["trigger"] == "你有 1 封未读邮件"
    assert _flag_kinds(report) == [FLAG_RUN_INCOMPLETE]


def test_ledger_missing_tokens_null(tmp_path: Path) -> None:
    archive = _write_archive(tmp_path, fed=[_FED[0]])
    report = generate_report(archive)
    assert report.summary["total_tokens"] is None
    assert report.summary["llm_calls"] is None
    markdown = (tmp_path / "proto" / "ARENA.md").read_text(encoding="utf-8")
    assert "总 token：无数据（ledger.jsonl 缺失）" in markdown


def test_reply_matching_by_recipient_without_in_reply_to(tmp_path: Path) -> None:
    # 无 in_reply_to 的发出信，收件人含语料发件人 → 归到该语料（emit 回信路径）
    sid = f"{_AGENT_ID}-aaaa1111bbbb"
    archive = _write_archive(
        tmp_path,
        fed=[_FED[0]],
        runs=[_run(sid, ["m1"], [_end(1, sid)])],
        sent=[_sent_mail("r1", ["boss@example.com"], content="无线程头的回信")],
    )
    report = generate_report(archive)
    mails = report.runs[0]["mails"]
    assert [m["content"] for m in mails[0]["reply_mails"]] == ["无线程头的回信"]


def test_root_markdown_overwritten_by_latest_run(tmp_path: Path) -> None:
    root = tmp_path / "proto"
    root.mkdir(parents=True)
    (root / "ARENA.md").write_text("上一轮跑的老报告\n", encoding="utf-8")
    archive = _write_archive(tmp_path, fed=[_FED[0]])
    generate_report(archive)
    markdown = (root / "ARENA.md").read_text(encoding="utf-8")
    assert "老报告" not in markdown
    assert markdown.startswith("# 演武报告")


def test_missing_corpus_and_run_json_still_generates(tmp_path: Path) -> None:
    # 归档目录只有个空壳（例如 instantiate 失败的 run）也出报告，不报错
    archive = _write_archive(tmp_path, write_corpus=False, write_run=False)
    report = generate_report(archive)
    assert report.summary["fed"] == 0
    assert report.summary["runs_triggered"] == 0
    assert report.summary["completion_rate"] is None
    assert report.summary["mails_covered"] == 0
    assert report.summary["drained"] is None
    assert report.red_flags == []
    assert (archive / "report.json").exists()
    assert (tmp_path / "proto" / "ARENA.md").exists()


def test_program_projection_reports_real_fuel_exhaustion() -> None:
    import pytest

    from tlon.agents.program_history import program_events
    from tlon.agents.program_store import ProgramStore
    from tlon.arena.report import _summarize_run
    from tlosure.builtins import FuelExhaustedError
    from tlosure.edn import loads
    from tlosure.eval import eval_form

    with pytest.raises(FuelExhaustedError) as failure:
        eval_form(loads("(while [n 0] true (recur n))"), {}, {}, fuel=10)
    store = ProgramStore(None)
    store.ensure_entry("limited", "{}", "{}", {})
    store.fail("limited", str(failure.value))
    events = program_events(store.entry_history("limited")[0], "member")
    summary, flags = _summarize_run(list(enumerate(events, 1)))
    assert summary["end_reason"] == "failed" and summary["fuel_exhausted"]
    assert [flag.kind for flag in flags] == [FLAG_FUEL_EXHAUSTION]
    assert flags[0].evidence.endswith("#seq-1")


def test_other_failed_program_is_not_reported_as_fuel_exhaustion() -> None:
    from tlon.arena.report import _summarize_run

    summary, flags = _summarize_run(
        [
            (
                1,
                {
                    "type": "run/end",
                    "reason": "failed",
                    "error": "unknown tool result; quoted text: fuel exhausted (budget 10)",
                    "run_id": "other",
                    "seq": 1,
                },
            )
        ]
    )
    assert not summary["fuel_exhausted"]
    assert [flag.kind for flag in flags] == [FLAG_RUN_INCOMPLETE]
