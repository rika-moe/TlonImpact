"""Rebuildable reading events from committed member program facts.

These events are views, never inputs to run recovery. No model or tool is
called and no inferred outcome is written back to either authority.
"""

import json
from typing import Any

from tlon.agents.program_store import ProgramRun
from tlon.operations.schema import json_value
from tlosure.edn import loads


def program_events(record: dict[str, Any], agent_id: str) -> list[dict[str, Any]]:
    """Project one immutable snapshot into the shared reading vocabulary."""
    run: ProgramRun = record["run"]
    snapshot = json_value(loads(run.input_edn), edn_keys=True)
    payload = snapshot.get("payload", {}) if isinstance(snapshot, dict) else {}
    mail = payload.get("mail", {}) if isinstance(payload, dict) else {}
    if isinstance(mail, dict) and "__tlon_mail__" in mail:
        mail = mail["__tlon_mail__"]
    mail_id = mail.get("id") if isinstance(mail, dict) else None
    trigger = mail.get("content", "") if isinstance(mail, dict) else ""
    if not trigger:
        trigger = json.dumps(payload, ensure_ascii=False)
    events: list[dict[str, Any]] = []

    def emit(event_type: str, **data: Any) -> None:
        events.append({"seq": len(events), "ts": record["started_at"], "type": event_type, **data})

    emit(
        "run/start",
        agent_id=agent_id,
        responsibility=[mail_id] if mail_id else [],
        source_mail_id=mail_id,
        source_kind="program",
        run_id=run.run_id,
        trigger_content=trigger,
        input=snapshot,
        context=[],
    )
    for identity, outcome in record["invocations"]:
        name = identity["name"]
        if outcome is None:
            emit("program/invocation-pending", invocation=identity, outcome="unknown")
            continue
        if name == "_model-request":
            request = json.loads(identity["arguments_json"])["request"]
            emit("context/compact", context=request["messages"])
            if outcome["success"]:
                response = outcome["value"]
                emit("llm/response", response=response, call_id=identity["call_id"])
                emit(
                    "context/append",
                    messages=[
                        {
                            "role": "assistant",
                            "content": response.get("content"),
                            "tool_calls": response.get("tool_calls") or [],
                        }
                    ],
                )
            else:
                emit("program/model-failure", call_id=identity["call_id"], outcome=outcome)
        elif name not in {"model-call", "tool-execute", "model-context"}:
            emit(
                "tool/dispatch",
                tool_calls=[{"id": identity["call_id"], "function": {"name": name}}],
            )
            emit(
                "tool/result",
                tool_call={"id": identity["call_id"], "function": {"name": name}},
                result={
                    "success": outcome["success"],
                    "data": outcome.get("value"),
                    "error": outcome.get("error_message"),
                },
                invocation=identity,
            )
    if run.completed or run.error is not None:
        emit(
            "run/end",
            reason="failed" if run.error else "completed",
            error=run.error,
            result=json_value(loads(run.result_edn), edn_keys=True) if run.result_edn else None,
        )
        events[-1]["ts"] = record["ended_at"]
    return events
