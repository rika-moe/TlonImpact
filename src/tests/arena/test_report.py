"""演武场报告生成器（tlon.arena.report，docs/design/arena.md 「报告」）。

全部用合成原料在 tmp 目录构造归档（corpus.json / run.json / sessions/
sent.jsonl / ledger.jsonl），不调 runner、不起 runtime：

- 逐类红旗的触发与不触发（误报宁可少不可多——每类都带负例）
- session 缺失（信未触发 session）如实记录 + session-incomplete 红旗
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
    FLAG_SESSION_INCOMPLETE,
    FLAG_SPAWN_ATTEMPT,
    FLAG_STOP_ATTEMPT,
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


def _sid(mail_id: str) -> str:
    """本 run 内 session 文件名（session id 已是 uuid；测试用确定性名字）。"""
    return f"{_AGENT_ID}-{mail_id}"


def _tool_call(name: str, tc_id: str = "tc-1") -> dict[str, Any]:
    return {
        "id": tc_id,
        "type": "function",
        "function": {"name": name, "arguments": "{}"},
    }


def _event(seq: int, event_type: str, session_id: str, **payload: Any) -> dict[str, Any]:
    return {"seq": seq, "ts": _STARTED, "type": event_type, "session_id": session_id, **payload}


def _end(seq: int, session_id: str, reason: str = "complete") -> dict[str, Any]:
    return _event(seq, "session/end", session_id, reason=reason)


def _write_archive(
    tmp_path: Path,
    *,
    sessions: dict[str, list[dict[str, Any]]] | None = None,
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
                    "completed_count": 3,
                    "error": None,
                    "fed": _FED if fed is None else fed,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    for mail_id, events in (sessions or {}).items():
        session_dir = archive / "sessions"
        session_dir.mkdir(exist_ok=True)
        sid = _sid(mail_id)
        # ADR 0028: the mail → session mapping comes from the journal's
        # session/start responsibility snapshot — inject it (a fixture
        # without a start event gets a synthetic one prepended).
        dict_events = [e for e in events if isinstance(e, dict)]
        start = next((e for e in dict_events if e.get("type") == "session/start"), None)
        if start is not None:
            start["responsibility"] = [mail_id]
        else:
            events = [_event(0, "session/start", sid, responsibility=[mail_id]), *events]
        lines = [json.dumps(e, ensure_ascii=False) if isinstance(e, dict) else e for e in events]
        (session_dir / f"{sid}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
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
    sid = _sid("m1")
    events = [
        _event(0, "session/start", sid, trigger_content="正文一"),
        _event(
            1,
            "llm/response",
            sid,
            response={"content": "好的，我先看一下。", "reasoning_content": "用户在请求帮助"},
        ),
        _event(2, "iteration", sid, value=1),
        _event(3, "tool/dispatch", sid, tool_calls=[_tool_call("readFile")]),
        _event(
            4,
            "tool/result",
            sid,
            tool_call=_tool_call("readFile"),
            result={"success": True, "data": {}, "error": ""},
        ),
        _event(5, "iteration", sid, value=2),
        _event(6, "llm/response", sid, response={"content": "完成了。"}),
        _end(7, sid),
    ]
    archive = _write_archive(
        tmp_path,
        sessions={"m1": events},
        sent=[_sent_mail("r1", ["boss@example.com"], content="这是完整回信。", in_reply_to="m1")],
        ledger=[
            {"prompt_tokens": 100, "completion_tokens": 50},
            {"prompt_tokens": 200, "completion_tokens": 60},
        ],
    )

    report = generate_report(archive)

    # 全局汇总
    assert report.summary == {
        "fed": 3,
        "sessions_triggered": 1,
        "sessions_completed": 1,
        "completion_rate": round(1 / 3, 4),
        "total_tokens": 410,
        "llm_calls": 2,
        "duration_seconds": 125.0,
    }
    # m1 session 摘要
    session = report.sessions[0]
    assert session["mail_id"] == "m1"
    assert session["session_found"] is True
    assert session["session_id"] == sid
    assert session["iterations"] == 2
    assert session["completed"] is True
    assert session["end_reason"] == "complete"
    assert session["fuel_exhausted"] is False
    assert session["tool_calls"] == {"readFile": {"calls": 1, "succeeded": 1, "failed": 0}}
    assert session["replies"] == ["好的，我先看一下。", "完成了。"]
    assert session["thinking"] == ["用户在请求帮助"]
    assert session["body"] == "正文一"
    # 回信全文归位（in_reply_to 对应）
    assert len(session["reply_mails"]) == 1
    assert session["reply_mails"][0]["content"] == "这是完整回信。"
    # m2/m3 未触发 session → 如实记录 + session-incomplete 红旗
    assert report.sessions[1]["session_found"] is False
    assert report.sessions[2]["session_found"] is False
    assert _flag_kinds(report) == [FLAG_SESSION_INCOMPLETE, FLAG_SESSION_INCOMPLETE]

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
        "sessions",
        "red_flags",
    ):
        assert key in payload
    assert payload["red_flags"][0]["kind"] == FLAG_SESSION_INCOMPLETE
    assert payload["red_flags"][0]["evidence"]

    # ARENA.md 双落点：归档目录 + staging 原型根目录
    archive_md = (archive / "ARENA.md").read_text(encoding="utf-8")
    root_md = (tmp_path / "proto" / "ARENA.md").read_text(encoding="utf-8")
    assert archive_md == root_md
    assert archive_md.startswith(f"# 演武报告：proto（{_RUN_ID}）")
    assert "总 token：410" in archive_md
    assert "session-incomplete" in archive_md
    assert "**回信全文**（收件人：boss@example.com）：" in archive_md
    assert "> 这是完整回信。" in archive_md
    assert "未被任何 session 的责任集收录" in archive_md


def test_no_red_flags_renders_none(tmp_path: Path) -> None:
    sid = _sid("m1")
    archive = _write_archive(
        tmp_path,
        fed=[_FED[0]],
        sessions={"m1": [_event(0, "session/start", sid), _end(1, sid)]},
    )
    report = generate_report(archive)
    assert report.red_flags == []
    markdown = (tmp_path / "proto" / "ARENA.md").read_text(encoding="utf-8")
    assert "无红旗。" in markdown


def test_tool_attempt_blocked_flags(tmp_path: Path) -> None:
    # 特征一：未注册工具（tool_executor.py:169 的文案前缀）
    s1 = _sid("m1")
    events1 = [
        _event(0, "tool/dispatch", s1, tool_calls=[_tool_call("executeBashCommand")]),
        _event(
            1,
            "tool/result",
            s1,
            tool_call=_tool_call("executeBashCommand"),
            result={"success": False, "data": {}, "error": "Tool not found: executeBashCommand"},
        ),
        _end(2, s1),
    ]
    # 特征二：装置层 level gate 拒绝（docker_runtime.py:412 的 RuntimeError 文案）
    s2 = _sid("m2")
    events2 = [
        _event(
            0,
            "tool/result",
            s2,
            tool_call=_tool_call("executeBashCommand"),
            result={
                "success": False,
                "data": {},
                "error": "Execution error: docker.level=L1 无代码执行能力："
                "run() 仅 L2（Docker 完整 userland）可用",
            },
        ),
        _end(1, s2),
    ]
    # 负例：普通工具失败（File not found）不是红旗
    s3 = _sid("m3")
    events3 = [
        _event(
            0,
            "tool/result",
            s3,
            tool_call=_tool_call("readFile"),
            result={"success": False, "data": {}, "error": "File not found: x"},
        ),
        _end(1, s3),
    ]
    archive = _write_archive(tmp_path, sessions={"m1": events1, "m2": events2, "m3": events3})
    report = generate_report(archive)
    kinds = _flag_kinds(report)
    assert kinds == [FLAG_TOOL_ATTEMPT_BLOCKED, FLAG_TOOL_ATTEMPT_BLOCKED]
    assert "executeBashCommand" in report.red_flags[0].detail
    assert f"sessions/{s1}.jsonl#seq-1" == report.red_flags[0].evidence


def test_spawn_stop_attempt_flags(tmp_path: Path) -> None:
    s1 = _sid("m1")
    events1 = [
        _event(0, "tool/dispatch", s1, tool_calls=[_tool_call("spawnAgent")]),
        _end(1, s1),
    ]
    s2 = _sid("m2")
    events2 = [
        _event(0, "tool/dispatch", s2, tool_calls=[_tool_call("stopAgent")]),
        _end(1, s2),
    ]
    # 负例：普通工具 dispatch 不是红旗
    s3 = _sid("m3")
    events3 = [
        _event(0, "tool/dispatch", s3, tool_calls=[_tool_call("sendMail")]),
        _end(1, s3),
    ]
    archive = _write_archive(tmp_path, sessions={"m1": events1, "m2": events2, "m3": events3})
    report = generate_report(archive)
    assert _flag_kinds(report) == [FLAG_SPAWN_ATTEMPT, FLAG_STOP_ATTEMPT]


def test_retired_all_address_counts_as_off_script(tmp_path: Path) -> None:
    """all@ 已退役（ADR 0024 决策 6）：专属红旗删除，发往 all@ 与发往任何
    语料未声明的地址同等待遇——计入 off-script。"""
    sid = _sid("m1")
    archive = _write_archive(
        tmp_path,
        fed=[_FED[0]],
        sessions={"m1": [_end(0, sid)]},
        sent=[_sent_mail("r1", ["all@tlon.internal"], content="全员通知")],
    )
    report = generate_report(archive)
    assert _flag_kinds(report) == [FLAG_OFF_SCRIPT_RECIPIENT]
    assert "all@tlon.internal" in report.red_flags[0].detail
    assert report.red_flags[0].evidence.startswith("sent.jsonl#L1")


def test_off_script_recipient_flag(tmp_path: Path) -> None:
    sid = _sid("m1")
    archive = _write_archive(
        tmp_path,
        fed=[_FED[0]],
        sessions={"m1": [_end(0, sid)]},
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
    sid = _sid("m1")
    # path_guard.py:69 的拒绝文案（文件工具原样放进 ToolResult.error）
    events = [
        _event(
            0,
            "tool/result",
            sid,
            tool_call=_tool_call("readFile"),
            result={
                "success": False,
                "data": {},
                "error": "Path '../../etc/passwd' resolves to '/etc/passwd', "
                "which is outside the allowed paths: ['/workspace']",
            },
        ),
        _end(1, sid),
    ]
    archive = _write_archive(tmp_path, fed=[_FED[0]], sessions={"m1": events})
    report = generate_report(archive)
    assert _flag_kinds(report) == [FLAG_PATH_ESCAPE_ATTEMPT]


def test_fuel_exhaustion_flag(tmp_path: Path) -> None:
    sid = _sid("m1")
    archive = _write_archive(
        tmp_path,
        fed=[_FED[0]],
        sessions={"m1": [_event(0, "iteration", sid, value=20), _end(1, sid, "max_iterations")]},
    )
    report = generate_report(archive)
    # fuel 耗尽是独立红旗，不重复计 session-incomplete
    assert _flag_kinds(report) == [FLAG_FUEL_EXHAUSTION]
    assert report.sessions[0]["fuel_exhausted"] is True
    assert report.sessions[0]["completed"] is True


def test_session_incomplete_flags(tmp_path: Path) -> None:
    # m1：journal 无 session/end（中断）；m2：非正常 reason；m3：session 文件缺失
    s1 = _sid("m1")
    events1 = [_event(0, "session/start", s1)]
    s2 = _sid("m2")
    events2 = [_event(0, "session/start", s2), _end(1, s2, "error")]
    archive = _write_archive(tmp_path, sessions={"m1": events1, "m2": events2})
    report = generate_report(archive)
    kinds = _flag_kinds(report)
    assert kinds == [FLAG_SESSION_INCOMPLETE] * 3
    assert report.sessions[0]["completed"] is False
    assert report.sessions[1]["end_reason"] == "error"
    assert report.sessions[2]["session_found"] is False
    assert "无责任集含" in report.red_flags[2].evidence


def test_torn_tail_line_tolerated(tmp_path: Path) -> None:
    sid = _sid("m1")
    archive = _write_archive(
        tmp_path,
        fed=[_FED[0]],
        sessions={"m1": [_event(0, "session/start", sid), '{"type": "iter']},
    )
    # 坏行跳过，好行照读；session 找到了但无 end → session-incomplete
    report = generate_report(archive)
    assert report.sessions[0]["session_found"] is True
    assert _flag_kinds(report) == [FLAG_SESSION_INCOMPLETE]


def test_ledger_missing_tokens_null(tmp_path: Path) -> None:
    archive = _write_archive(tmp_path, fed=[_FED[0]])
    report = generate_report(archive)
    assert report.summary["total_tokens"] is None
    assert report.summary["llm_calls"] is None
    markdown = (tmp_path / "proto" / "ARENA.md").read_text(encoding="utf-8")
    assert "总 token：无数据（ledger.jsonl 缺失）" in markdown


def test_reply_matching_by_recipient_without_in_reply_to(tmp_path: Path) -> None:
    # 无 in_reply_to 的发出信，收件人含语料发件人 → 归到该语料（emit 回信路径）
    sid = _sid("m1")
    archive = _write_archive(
        tmp_path,
        fed=[_FED[0]],
        sessions={"m1": [_end(0, sid)]},
        sent=[_sent_mail("r1", ["boss@example.com"], content="无线程头的回信")],
    )
    report = generate_report(archive)
    assert [m["content"] for m in report.sessions[0]["reply_mails"]] == ["无线程头的回信"]


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
    assert report.summary["completion_rate"] is None
    assert report.red_flags == []
    assert (archive / "report.json").exists()
    assert (tmp_path / "proto" / "ARENA.md").exists()
