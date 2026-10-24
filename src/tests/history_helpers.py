"""Construct real ProgramStore execution facts for read-only history tests."""

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any
from uuid import uuid4

from tlon.agents.program_store import ProgramStore
from tlon.agents.run_journal import RunJournal
from tlon.operations.invocation import Invocation, InvocationOutcome
from tlon.operations.schema import edn_value
from tlosure.edn import dumps


async def append_history(journal: RunJournal, run_id: str, event_type: str, **payload: Any) -> None:
    """Record run inputs and model/tool receipts through the execution authority."""
    if journal._program_store is None:
        journal._program_store = ProgramStore(journal._program_database)
    store = journal._program_store
    if event_type == "run/start":
        responsibility = payload.get("responsibility") or []
        source_mail_id = (
            payload.get("source_mail_id")
            or payload.get("mail_id")
            or (responsibility[0] if responsibility else None)
        )
        snapshot = {
            "payload": {
                "mail": {
                    "content": payload.get("trigger_content", ""),
                    "id": source_mail_id,
                }
            }
        }
        store.ensure_entry(run_id, "{}", dumps(edn_value(snapshot)), {})
        return
    if event_type == "run/end":
        if payload.get("reason") == "failed":
            store.fail(run_id, payload.get("error", "failed"))
        else:
            store.complete_entry(run_id, payload.get("result"))
        return
    if event_type == "tool/dispatch":
        return  # Dispatch is derived from each committed tool receipt.
    if event_type == "llm/response":
        name = "_model-request"
        arguments = {"request": {"messages": []}}
        outcome = InvocationOutcome(True, value=payload["response"])
    elif event_type == "tool/result":
        call = payload["tool_call"]
        name = call.get("function", {}).get("name", call.get("function_name", "tool"))
        arguments = {}
        result = payload["result"]
        outcome = InvocationOutcome(
            bool(result.get("success")), value=result.get("data"), error_message=result.get("error")
        )
    else:
        raise ValueError(f"unsupported history fixture fact: {event_type}")
    call_id = uuid4().hex
    invocation = Invocation(
        call_id, run_id, (call_id,), name, json.dumps(arguments), "test", store.build_id
    )
    store.commit_invocation(invocation, outcome)


def insert_legacy_run(
    database: Path, run_id: str, source: str = "{}", input_edn: str = "{}"
) -> None:
    """Insert retired evidence into an already initialized program database."""
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            "INSERT INTO runs(run_id,rule_edn,input_edn) VALUES (?,?,?)",
            (run_id, source, input_edn),
        )
        connection.commit()
