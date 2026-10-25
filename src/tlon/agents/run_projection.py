"""Read-only projections of original program execution records."""

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class RunProjection:
    """One completed run's reading view.

    Rebuilt from the program run reading view:
    the trigger content plus, per round, the LLM response content and a
    one-line digest of each tool result, and the ``run/end`` reason.
    """

    run_id: str
    trigger_content: str
    llm_responses: list[str] = field(default_factory=list)
    tool_results: list[str] = field(default_factory=list)
    end_reason: str = ""
    program_result: str = ""
    errors: list[str] = field(default_factory=list)


def build_run_projection(events: list[dict[str, Any]], run_id: str = "") -> RunProjection:
    """Rebuild one run's reading view from its journal events.

    The projection is: the real trigger content (from
    ``run/start``), every round's LLM response content, a one-line
    digest per tool result, and the ``run/end`` reason. The thread recap
    uses this projection without resuming or modifying execution.
    """
    trigger_content = ""
    llm_responses: list[str] = []
    tool_results: list[str] = []
    end_reason = ""
    program_result = ""
    errors: list[str] = []
    for event in events:
        event_type = event.get("type")
        if event_type == "run/start":
            trigger_content = str(event.get("trigger_content") or "")
        elif event_type == "llm/response":
            content = (event.get("response") or {}).get("content") or ""
            if content:
                llm_responses.append(str(content))
        elif event_type == "tool/result":
            tool_call = event.get("tool_call") or {}
            name = (tool_call.get("function") or {}).get("name", "unknown")
            result = event.get("result") or {}
            text = (
                result.get("message")
                or result.get("error")
                or json.dumps(result.get("data") or {}, ensure_ascii=False)
            )
            tool_results.append(f"{name}: {text}")
        elif event_type == "program/invocation-pending":
            identity = event.get("invocation") or {}
            errors.append(
                f"未决调用：{identity.get('name', 'unknown')} {identity.get('call_id', '')}"
            )
        elif event_type == "program/model-failure":
            errors.append(str((event.get("outcome") or {}).get("error_message") or "模型请求失败"))
        elif event_type == "run/end":
            if event.get("error"):
                errors.append(str(event["error"]))
            end_reason = str(event.get("reason") or "")
            if event.get("result") is not None:
                program_result = json.dumps(event["result"], ensure_ascii=False)
    return RunProjection(
        run_id=run_id,
        trigger_content=trigger_content,
        llm_responses=llm_responses,
        tool_results=tool_results,
        end_reason=end_reason,
        program_result=program_result,
        errors=errors,
    )
