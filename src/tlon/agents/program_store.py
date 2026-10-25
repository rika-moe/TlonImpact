"""Durable member program inputs, effects and private structured state."""

import hashlib
import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from tlon.base.execution import BudgetExceededError
from tlon.operations.invocation import Invocation, InvocationOutcome
from tlon.operations.schema import RecoveryMismatchError, edn_value, json_value
from tlosure.edn import dumps, loads


@dataclass(frozen=True)
class ProgramRun:
    run_id: str
    rule_edn: str
    input_edn: str
    completed: bool
    error: str | None = None
    package_edn: str | None = None
    result_edn: str | None = None
    scheduling_edn: str | None = None
    scheduling_applied: bool = False


class ProgramStore:
    """One member's authority; every disk operation closes its connection."""

    def __init__(self, database: Path | None, *, build_id: str | None = None) -> None:
        if build_id is None:
            from tlon.base.runtime_identity import runtime_identity

            build_id = runtime_identity()
        self.build_id = build_id
        self._budgets: dict[str, dict[str, Any]] = {}
        self._allocations: dict[str, dict[str, Any]] = {}
        self._roots: dict[str, str] = {}
        self._model_contexts: dict[tuple[str, str], str] = {}
        self._invocations: dict[str, tuple[Invocation, str | None]] = {}
        self.database = database
        self._lock = threading.RLock()
        self._runs: dict[str, ProgramRun] = {}
        self._history_times: dict[str, tuple[str, str | None]] = {}
        self._state: dict[str, str] = {}

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        assert self.database is not None
        self.database.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database, timeout=30)
        try:
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("BEGIN IMMEDIATE")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            tables = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
            if version not in (0, 2) or (version == 0 and tables):
                raise RecoveryMismatchError(
                    "Unsupported program database format; refusing to alter existing history"
                )
            schema = (
                "CREATE TABLE IF NOT EXISTS runs (run_id TEXT PRIMARY KEY, "
                "rule_edn TEXT NOT NULL, input_edn TEXT NOT NULL, completed INTEGER NOT NULL DEFAULT 0, error TEXT);"
                "CREATE TABLE IF NOT EXISTS roots (root_id TEXT PRIMARY KEY, build_id TEXT NOT NULL);"
                "CREATE TABLE IF NOT EXISTS invocations (call_id TEXT PRIMARY KEY, identity_json TEXT NOT NULL, outcome_json TEXT, root_id TEXT NOT NULL, path_json TEXT NOT NULL, UNIQUE(root_id,path_json));"
                "PRAGMA user_version=2;"
                "CREATE TABLE IF NOT EXISTS model_contexts (root_id TEXT NOT NULL, slot TEXT NOT NULL, value_json TEXT NOT NULL, PRIMARY KEY(root_id,slot));"
                "CREATE TABLE IF NOT EXISTS budgets (root_id TEXT PRIMARY KEY, token_limit INTEGER NOT NULL, measure TEXT NOT NULL, deadline REAL);"
                "CREATE TABLE IF NOT EXISTS model_allocations (call_id TEXT PRIMARY KEY, root_id TEXT NOT NULL, reserved INTEGER NOT NULL, output_limit INTEGER NOT NULL, charged INTEGER);"
                "CREATE TABLE IF NOT EXISTS entry_programs (run_id TEXT PRIMARY KEY, package_edn TEXT NOT NULL, result_edn TEXT, scheduling_edn TEXT, scheduling_applied INTEGER NOT NULL DEFAULT 0);"
                "CREATE TABLE IF NOT EXISTS entry_history_metadata (run_id TEXT PRIMARY KEY, started_at TEXT NOT NULL, ended_at TEXT);"
                "CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value_edn TEXT NOT NULL);"
            )
            with connection:
                for statement in schema.split(";"):
                    if statement.strip():
                        connection.execute(statement)
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _read_run(connection: sqlite3.Connection, run_id: str) -> ProgramRun | None:
        row = connection.execute(
            "SELECT r.run_id,r.rule_edn,r.input_edn,r.completed,r.error,"
            "e.package_edn,e.result_edn,e.scheduling_edn,e.scheduling_applied "
            "FROM runs r LEFT JOIN entry_programs e ON r.run_id=e.run_id WHERE r.run_id=?",
            (run_id,),
        ).fetchone()
        return (
            ProgramRun(
                row[0], row[1], row[2], bool(row[3]), row[4], row[5], row[6], row[7], bool(row[8])
            )
            if row
            else None
        )

    def ensure_entry(
        self, run_id: str, rule_edn: str, input_edn: str, package: dict[str, Any]
    ) -> ProgramRun:
        """Freeze the source package and input atomically; retain existing snapshots."""
        encoded = dumps(edn_value(json_value(package)))
        contexts = {
            slot: json.dumps(json_value(value), sort_keys=True, allow_nan=False)
            for slot, value in package.get("model_contexts", {}).items()
        }
        with self._lock:
            if self.database is None:
                self._root(run_id)
                previous = self._runs.get(run_id)
                if previous is not None:
                    if previous.package_edn is None:
                        raise RecoveryMismatchError("Run is not a module entry")
                    return previous
                run = ProgramRun(run_id, rule_edn, input_edn, False, package_edn=encoded)
                self._runs[run_id] = run
                self._history_times[run_id] = (datetime.now(UTC).isoformat(), None)
                for slot, value in contexts.items():
                    self._model_contexts.setdefault((run_id, slot), value)
                return run
            with self._connection() as connection:
                self._root(run_id, connection)
                previous = self._read_run(connection, run_id)
                if previous is not None:
                    if previous.package_edn is None:
                        raise RecoveryMismatchError("Run is not a module entry")
                    return previous
                connection.execute(
                    "INSERT INTO runs(run_id,rule_edn,input_edn) VALUES (?,?,?)",
                    (run_id, rule_edn, input_edn),
                )
                connection.execute(
                    "INSERT INTO entry_programs(run_id,package_edn) VALUES (?,?)", (run_id, encoded)
                )
                connection.execute(
                    "INSERT INTO entry_history_metadata VALUES (?,?,NULL)",
                    (run_id, datetime.now(UTC).isoformat()),
                )
                for slot, value in contexts.items():
                    connection.execute(
                        "INSERT OR IGNORE INTO model_contexts VALUES (?,?,?)", (run_id, slot, value)
                    )
                stored_run = self._read_run(connection, run_id)
                assert stored_run is not None
                return stored_run

    def pending(self) -> list[ProgramRun]:
        with self._lock:
            if self.database is None:
                return [r for r in self._runs.values() if not r.completed and r.error is None]
            with self._connection() as connection:
                ids = connection.execute(
                    "SELECT run_id FROM runs WHERE completed=0 AND error IS NULL ORDER BY rowid"
                ).fetchall()
                return [r for (key,) in ids if (r := self._read_run(connection, key)) is not None]

    def failed_entries(self) -> list[ProgramRun]:
        """Terminal module failures whose courtesy mail may need reconciliation."""
        with self._lock:
            if self.database is None:
                return [r for r in self._runs.values() if r.package_edn is not None and r.error]
            if not self.database.exists():
                return []
            with self._history_connection() as connection:
                ids = connection.execute(
                    "SELECT r.run_id FROM runs r JOIN entry_programs e ON e.run_id=r.run_id "
                    "WHERE r.error IS NOT NULL ORDER BY r.rowid"
                ).fetchall()
                return [r for (key,) in ids if (r := self._read_run(connection, key)) is not None]

    def get_run(self, run_id: str) -> ProgramRun | None:
        """Inspect durable lifecycle status without authorizing execution."""
        with self._lock:
            if self.database is None:
                return self._runs.get(run_id)
            with self._connection() as connection:
                return self._read_run(connection, run_id)

    def complete_entry(
        self, run_id: str, result: Any, scheduling: dict[str, Any] | None = None
    ) -> ProgramRun:
        """Commit result, completion and scheduling intent atomically, once."""
        result_edn = dumps(edn_value(json_value(result, edn_keys=True)))
        scheduling_edn = (
            dumps(edn_value(json_value(scheduling, edn_keys=True)))
            if scheduling is not None
            else None
        )
        with self._lock:
            if self.database is None:
                run = self._runs[run_id]
                if run.package_edn is None or run.error is not None:
                    raise RecoveryMismatchError("Entry cannot be completed")
                if not run.completed:
                    run = replace(
                        run, completed=True, result_edn=result_edn, scheduling_edn=scheduling_edn
                    )
                    self._runs[run_id] = run
                    self._finish_history(run_id)
                return run
            with self._connection() as connection:
                stored_run = self._read_run(connection, run_id)
                if (
                    stored_run is None
                    or stored_run.package_edn is None
                    or stored_run.error is not None
                ):
                    raise RecoveryMismatchError("Entry cannot be completed")
                if not stored_run.completed:
                    connection.execute("UPDATE runs SET completed=1 WHERE run_id=?", (run_id,))
                    self._finish_history(run_id, connection)
                    connection.execute(
                        "UPDATE entry_programs SET result_edn=?,scheduling_edn=? WHERE run_id=?",
                        (result_edn, scheduling_edn, run_id),
                    )
                    stored_run = replace(
                        stored_run,
                        completed=True,
                        result_edn=result_edn,
                        scheduling_edn=scheduling_edn,
                    )
                return stored_run

    def pending_scheduling(self) -> list[ProgramRun]:
        """Committed entry intents awaiting idempotent scheduler application."""
        with self._lock:
            if self.database is None:
                return [
                    r
                    for r in self._runs.values()
                    if r.completed and r.scheduling_edn is not None and not r.scheduling_applied
                ]
            with self._connection() as connection:
                ids = connection.execute(
                    "SELECT r.run_id FROM runs r JOIN entry_programs e ON r.run_id=e.run_id "
                    "WHERE r.completed=1 AND e.scheduling_edn IS NOT NULL "
                    "AND e.scheduling_applied=0 ORDER BY r.rowid"
                ).fetchall()
                return [r for (key,) in ids if (r := self._read_run(connection, key)) is not None]

    def mark_scheduling_applied(self, run_id: str) -> None:
        with self._lock:
            if self.database is None:
                run = self._runs[run_id]
                if not run.completed or run.scheduling_edn is None:
                    raise RecoveryMismatchError("Scheduling intent is not committed")
                self._runs[run_id] = replace(run, scheduling_applied=True)
            else:
                with self._connection() as connection:
                    stored_run = self._read_run(connection, run_id)
                    if (
                        stored_run is None
                        or not stored_run.completed
                        or stored_run.scheduling_edn is None
                    ):
                        raise RecoveryMismatchError("Scheduling intent is not committed")
                    connection.execute(
                        "UPDATE entry_programs SET scheduling_applied=1 WHERE run_id=?", (run_id,)
                    )

    def state_get(self, key: str) -> Any:
        with self._lock:
            if self.database is None:
                value = self._state.get(key)
            else:
                with self._connection() as connection:
                    row = connection.execute(
                        "SELECT value_edn FROM state WHERE key=?", (key,)
                    ).fetchone()
                    value = row[0] if row else None
            return loads(value) if value is not None else None

    @staticmethod
    def _identity(invocation: Invocation) -> str:
        from dataclasses import asdict

        return json.dumps(asdict(invocation), sort_keys=True, allow_nan=False)

    @staticmethod
    def _outcome(outcome: InvocationOutcome) -> str:
        from dataclasses import asdict

        return json.dumps(asdict(outcome), allow_nan=False)

    @staticmethod
    def _decode(encoded: str | None) -> InvocationOutcome | None:
        return InvocationOutcome(**json.loads(encoded)) if encoded is not None else None

    def _root(self, root_id: str, connection: sqlite3.Connection | None = None) -> None:
        if connection is None:
            previous = self._roots.get(root_id)
            if previous is not None and previous != self.build_id:
                raise RecoveryMismatchError("Runtime build changed for frozen root")
            self._roots[root_id] = self.build_id
        else:
            row = connection.execute(
                "SELECT build_id FROM roots WHERE root_id=?", (root_id,)
            ).fetchone()
            if row is not None and row[0] != self.build_id:
                raise RecoveryMismatchError("Runtime build changed for frozen root")
            connection.execute("INSERT OR IGNORE INTO roots VALUES (?,?)", (root_id, self.build_id))

    def ensure_root(self, root_id: str) -> None:
        with self._lock:
            if self.database is None:
                self._root(root_id)
            else:
                with self._connection() as connection:
                    self._root(root_id, connection)

    def _invocation_row(
        self, invocation: Invocation, connection: sqlite3.Connection | None = None
    ) -> tuple[str, str | None] | None:
        if invocation.build_id != self.build_id:
            raise RecoveryMismatchError("Invocation runtime build changed")
        self._root(invocation.root_id, connection)
        if connection is None:
            item = self._invocations.get(invocation.call_id)
            row = (self._identity(item[0]), item[1]) if item else None
        else:
            row = connection.execute(
                "SELECT identity_json,outcome_json FROM invocations WHERE call_id=?",
                (invocation.call_id,),
            ).fetchone()
        if connection is None:
            collision = any(
                other.call_id != invocation.call_id
                and other.root_id == invocation.root_id
                and other.path == invocation.path
                for other, _ in self._invocations.values()
            )
        else:
            collision = (
                connection.execute(
                    "SELECT 1 FROM invocations WHERE root_id=? AND path_json=? AND call_id<>?",
                    (invocation.root_id, json.dumps(invocation.path), invocation.call_id),
                ).fetchone()
                is not None
            )
        if collision:
            raise RecoveryMismatchError("Invocation root/path belongs to another call")
        if row is not None and row[0] != self._identity(invocation):
            raise RecoveryMismatchError(
                "Invocation identity, path, arguments or definition changed"
            )
        return row

    def read_invocation_fact(
        self, root_id: str, path: tuple[str, ...]
    ) -> tuple[Invocation, InvocationOutcome | None] | None:
        """Read one leaf's identity and outcome, without authorizing execution.

        No row means no recorded intent; a row with no outcome is unresolved.
        Callers must validate the identity before treating a success as evidence.
        """
        with self._lock:
            if self.database is None:
                rows = (
                    (self._identity(invocation), outcome)
                    for invocation, outcome in self._invocations.values()
                    if invocation.root_id == root_id and invocation.path == path
                )
                row = next(rows, None)
            else:
                if not self.database.exists():
                    return None
                with self._history_connection() as connection:
                    row = connection.execute(
                        "SELECT identity_json,outcome_json FROM invocations "
                        "WHERE root_id=? AND path_json=?",
                        (root_id, json.dumps(path)),
                    ).fetchone()
            if row is None:
                return None
            identity = json.loads(row[0])
            identity["path"] = tuple(identity["path"])
            return Invocation(**identity), self._decode(row[1])

    def lookup_invocation(self, invocation: Invocation) -> InvocationOutcome | None:
        with self._lock:
            if self.database is None:
                row = self._invocation_row(invocation)
            else:
                with self._connection() as connection:
                    row = self._invocation_row(invocation, connection)
            return self._decode(row[1]) if row else None

    def begin_invocation(self, invocation: Invocation) -> bool:
        with self._lock:
            if self.database is None:
                if self._invocation_row(invocation) is not None:
                    return False
                self._invocations[invocation.call_id] = (invocation, None)
            else:
                with self._connection() as connection:
                    if self._invocation_row(invocation, connection) is not None:
                        return False
                    connection.execute(
                        "INSERT INTO invocations VALUES (?,?,NULL,?,?)",
                        (
                            invocation.call_id,
                            self._identity(invocation),
                            invocation.root_id,
                            json.dumps(invocation.path),
                        ),
                    )
            return True

    def _commit(
        self,
        invocation: Invocation,
        outcome: InvocationOutcome,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        encoded = self._outcome(outcome)
        row = self._invocation_row(invocation, connection)
        previous = self._decode(row[1]) if row else None
        if (
            previous is not None
            and previous.error_type != "UnknownOutcomeError"
            and row is not None
            and row[1] != encoded
        ):
            raise RecoveryMismatchError("Committed invocation outcome cannot be overwritten")
        if connection is None:
            self._invocations[invocation.call_id] = (invocation, encoded)
        else:
            connection.execute(
                "INSERT INTO invocations VALUES (?,?,?,?,?) ON CONFLICT(call_id) DO UPDATE SET outcome_json=excluded.outcome_json",
                (
                    invocation.call_id,
                    self._identity(invocation),
                    encoded,
                    invocation.root_id,
                    json.dumps(invocation.path),
                ),
            )

    def commit_invocation(self, invocation: Invocation, outcome: InvocationOutcome) -> None:
        with self._lock:
            if self.database is None:
                self._commit(invocation, outcome)
            else:
                with self._connection() as connection:
                    self._commit(invocation, outcome, connection)

    def atomic_state_put(self, invocation: Invocation, key: str, value: Any) -> Any:
        """State and the sole invocation receipt commit in one transaction."""
        result = json_value(value, edn_keys=True)
        encoded = dumps(edn_value(result))
        outcome = InvocationOutcome(True, value=result)
        with self._lock:
            if self.database is None:
                row = self._invocation_row(invocation)
                previous = self._decode(row[1]) if row else None
                if previous is not None and previous.error_type != "UnknownOutcomeError":
                    if not previous.success:
                        raise RecoveryMismatchError("State invocation already failed")
                    return previous.value
                previous_record = self._invocations.get(invocation.call_id)
                try:
                    self._commit(invocation, outcome)
                except BaseException:
                    if previous_record is None:
                        self._invocations.pop(invocation.call_id, None)
                    else:
                        self._invocations[invocation.call_id] = previous_record
                    raise
                self._state[key] = encoded
            else:
                with self._connection() as connection:
                    row = self._invocation_row(invocation, connection)
                    previous = self._decode(row[1]) if row else None
                    if previous is not None and previous.error_type != "UnknownOutcomeError":
                        if not previous.success:
                            raise RecoveryMismatchError("State invocation already failed")
                        return previous.value
                    connection.execute(
                        "INSERT INTO state VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value_edn=excluded.value_edn",
                        (key, encoded),
                    )
                    self._commit(invocation, outcome, connection)
        return json.loads(json.dumps(result, allow_nan=False))

    def ensure_budget(
        self, root_id: str, token_limit: int, measure: str, deadline: float | None = None
    ) -> dict[str, Any]:
        if type(token_limit) is not int or token_limit < 0 or measure not in ("output", "total"):
            raise ValueError("Invalid execution budget")
        with self._lock:
            if self.database is None:
                self._root(root_id)
                previous = self._budgets.get(root_id)
                value = {"token_limit": token_limit, "measure": measure, "deadline": deadline}
                if previous is not None:
                    if deadline is None:
                        value["deadline"] = previous["deadline"]
                    if previous != value:
                        raise RecoveryMismatchError("Frozen execution budget changed")
                self._budgets[root_id] = value
                return dict(value)
            else:
                with self._connection() as connection:
                    self._root(root_id, connection)
                    row = connection.execute(
                        "SELECT token_limit,measure,deadline FROM budgets WHERE root_id=?",
                        (root_id,),
                    ).fetchone()
                    if row is not None and deadline is None:
                        deadline = row[2]
                    if row is not None and row != (token_limit, measure, deadline):
                        raise RecoveryMismatchError("Frozen execution budget changed")
                    connection.execute(
                        "INSERT OR IGNORE INTO budgets VALUES (?,?,?,?)",
                        (root_id, token_limit, measure, deadline),
                    )
                    return {"token_limit": token_limit, "measure": measure, "deadline": deadline}

    def _budget(self, root_id: str, connection: sqlite3.Connection | None = None) -> dict[str, Any]:
        if connection is None:
            value = self._budgets.get(root_id)
            if value is None:
                raise RecoveryMismatchError("Execution budget is not initialized")
            allocations = [v for v in self._allocations.values() if v["root_id"] == root_id]
        else:
            row = connection.execute(
                "SELECT token_limit,measure,deadline FROM budgets WHERE root_id=?", (root_id,)
            ).fetchone()
            if row is None:
                raise RecoveryMismatchError("Execution budget is not initialized")
            value = dict(zip(("token_limit", "measure", "deadline"), row, strict=True))
            allocations = [
                dict(zip(("reserved", "charged"), row, strict=True))
                for row in connection.execute(
                    "SELECT reserved,charged FROM model_allocations WHERE root_id=?", (root_id,)
                )
            ]
        used = sum(v["charged"] if v["charged"] is not None else v["reserved"] for v in allocations)
        return {
            **value,
            "used": used,
            "remaining": max(value["token_limit"] - used, 0),
            "pending_count": sum(v["charged"] is None for v in allocations),
        }

    def budget_usage(self, root_id: str) -> dict[str, Any]:
        with self._lock:
            if self.database is None:
                self._root(root_id)
                return self._budget(root_id)
            with self._connection() as connection:
                self._root(root_id, connection)
                return self._budget(root_id, connection)

    def _model_invocation(
        self, root_id: str, path: tuple[str, ...], request_json: str
    ) -> Invocation:
        digest = hashlib.sha256(json.dumps(path).encode()).hexdigest()
        return Invocation(
            f"model:{root_id}:{digest}",
            root_id,
            ("__model__", *path),
            "_model-request",
            request_json,
            "model-request-v1",
            self.build_id,
        )

    def _allocation(
        self, call_id: str, connection: sqlite3.Connection | None = None
    ) -> dict[str, Any] | None:
        if connection is None:
            return self._allocations.get(call_id)
        row = connection.execute(
            "SELECT root_id,reserved,output_limit,charged FROM model_allocations WHERE call_id=?",
            (call_id,),
        ).fetchone()
        return (
            dict(zip(("root_id", "reserved", "output_limit", "charged"), row, strict=True))
            if row is not None
            else None
        )

    def _peek_model(
        self, invocation: Invocation, connection: sqlite3.Connection | None = None
    ) -> dict[str, Any] | None:
        row = self._invocation_row(invocation, connection)
        if row is None:
            return None
        outcome = self._decode(row[1])
        if outcome is None:
            return {"status": "unknown"}
        if outcome.success:
            return {"status": "replay", "response": outcome.value}
        return {
            "status": "rejected",
            "error_type": outcome.error_type,
            "message": outcome.error_message,
        }

    def peek_model(
        self, root_id: str, path: tuple[str, ...], request_json: str
    ) -> dict[str, Any] | None:
        invocation = self._model_invocation(root_id, path, request_json)
        with self._lock:
            if self.database is None:
                return self._peek_model(invocation)
            with self._connection() as connection:
                return self._peek_model(invocation, connection)

    def freeze_model_context(
        self, root_id: str, slot: str, value: dict[str, Any]
    ) -> dict[str, Any]:
        """Freeze host inputs once, returning isolated JSON data."""
        encoded = json.dumps(json_value(value), sort_keys=True, ensure_ascii=False, allow_nan=False)
        with self._lock:
            if self.database is None:
                self._root(root_id)
                frozen = self._model_contexts.setdefault((root_id, slot), encoded)
            else:
                with self._connection() as connection:
                    self._root(root_id, connection)
                    connection.execute(
                        "INSERT OR IGNORE INTO model_contexts VALUES (?,?,?)",
                        (root_id, slot, encoded),
                    )
                    frozen = connection.execute(
                        "SELECT value_json FROM model_contexts WHERE root_id=? AND slot=?",
                        (root_id, slot),
                    ).fetchone()[0]
            return cast(dict[str, Any], json.loads(frozen))

    def load_model_context(self, root_id: str, slot: str) -> dict[str, Any] | None:
        """Read frozen inputs without exposing mutable storage."""
        with self._lock:
            if self.database is None:
                self._root(root_id)
                encoded = self._model_contexts.get((root_id, slot))
            else:
                with self._connection() as connection:
                    self._root(root_id, connection)
                    row = connection.execute(
                        "SELECT value_json FROM model_contexts WHERE root_id=? AND slot=?",
                        (root_id, slot),
                    ).fetchone()
                    encoded = row[0] if row else None
            return json.loads(encoded) if encoded is not None else None

    def lookup_model_path(self, root_id: str, path: tuple[str, ...]) -> dict[str, Any] | None:
        """Query authoritative model state without dispatching a request."""

        def lookup(connection: sqlite3.Connection | None = None) -> dict[str, Any] | None:
            self._root(root_id, connection)
            probe = self._model_invocation(root_id, path, "")
            if connection is None:
                item = self._invocations.get(probe.call_id)
                encoded = item[0].arguments_json if item else None
            else:
                row = connection.execute(
                    "SELECT identity_json FROM invocations WHERE call_id=?", (probe.call_id,)
                ).fetchone()
                encoded = json.loads(row[0])["arguments_json"] if row else None
            if encoded is None:
                return None
            invocation = self._model_invocation(root_id, path, encoded)
            if self._allocation(invocation.call_id, connection) is None:
                raise RecoveryMismatchError("Model invocation has no allocation")
            state = self._peek_model(invocation, connection)
            assert state is not None
            return {**state, "request_json": encoded, "request": json.loads(encoded)["request"]}

        with self._lock:
            if self.database is None:
                return lookup()
            with self._connection() as connection:
                return lookup(connection)

    def _reserve_model(
        self,
        root_id: str,
        path: tuple[str, ...],
        request_json: str,
        output_limit: int,
        estimated_input: int,
        connection: sqlite3.Connection | None = None,
    ) -> dict[str, Any]:
        invocation = self._model_invocation(root_id, path, request_json)
        previous = self._peek_model(invocation, connection)
        if previous is not None:
            return previous
        budget = self._budget(root_id, connection)
        prompt = estimated_input if budget["measure"] == "total" else 0
        allowed = min(output_limit, budget["remaining"] - prompt)
        if allowed <= 0:
            raise BudgetExceededError("Execution token allowance exhausted")
        allocation = {
            "root_id": root_id,
            "reserved": allowed + prompt,
            "output_limit": allowed,
            "charged": None,
        }
        if connection is None:
            self._invocations[invocation.call_id] = (invocation, None)
            self._allocations[invocation.call_id] = allocation
        else:
            connection.execute(
                "INSERT INTO invocations VALUES (?,?,NULL,?,?)",
                (
                    invocation.call_id,
                    self._identity(invocation),
                    root_id,
                    json.dumps(invocation.path),
                ),
            )
            connection.execute(
                "INSERT INTO model_allocations VALUES (?,?,?,?,NULL)",
                (invocation.call_id, root_id, allocation["reserved"], allowed),
            )
        return {"status": "new", "output_limit": allowed, "reserved": allocation["reserved"]}

    def reserve_model(
        self,
        root_id: str,
        path: tuple[str, ...],
        request_json: str,
        output_limit: int,
        estimated_input: int,
    ) -> dict[str, Any]:
        if (
            type(output_limit) is not int
            or output_limit <= 0
            or type(estimated_input) is not int
            or estimated_input < 0
        ):
            raise ValueError("Invalid model reservation")
        with self._lock:
            if self.database is None:
                return self._reserve_model(
                    root_id, path, request_json, output_limit, estimated_input
                )
            with self._connection() as connection:
                return self._reserve_model(
                    root_id, path, request_json, output_limit, estimated_input, connection
                )

    def _finish_model(
        self,
        root_id: str,
        path: tuple[str, ...],
        response: dict[str, Any] | None,
        actual: int | None,
        error_type: str = "",
        message: str = "",
        connection: sqlite3.Connection | None = None,
    ) -> dict[str, Any]:
        call_id = self._model_invocation(root_id, path, "").call_id
        if connection is None:
            record = self._invocations.get(call_id)
            if record is None:
                raise RecoveryMismatchError("Model reservation is missing")
            invocation = record[0]
        else:
            row = connection.execute(
                "SELECT identity_json FROM invocations WHERE call_id=?", (call_id,)
            ).fetchone()
            if row is None:
                raise RecoveryMismatchError("Model reservation is missing")
            value = json.loads(row[0])
            value["path"] = tuple(value["path"])
            invocation = Invocation(**value)
        allocation = self._allocation(call_id, connection)
        if allocation is None:
            raise RecoveryMismatchError("Model allocation is missing")
        outcome = InvocationOutcome(
            response is not None, value=response, error_type=error_type, error_message=message
        )
        previous = self._invocation_row(invocation, connection)
        committed = self._decode(previous[1]) if previous is not None else None
        if committed is not None:
            if committed != outcome:
                raise RecoveryMismatchError("Model settlement changed")
            return committed.value if committed.success else {}
        charge = allocation["reserved"] if actual is None else actual
        if response is None:
            charge = 0
        if connection is None:
            previous_record = self._invocations[call_id]
            try:
                self._commit(invocation, outcome)
                self._allocations[call_id] = {**allocation, "charged": charge}
            except BaseException:
                self._invocations[call_id] = previous_record
                self._allocations[call_id] = allocation
                raise
        else:
            self._commit(invocation, outcome, connection)
            connection.execute(
                "UPDATE model_allocations SET charged=? WHERE call_id=?", (charge, call_id)
            )
        return response or {}

    def commit_model(
        self, root_id: str, path: tuple[str, ...], response: dict[str, Any], actual: int | None
    ) -> dict[str, Any]:
        response = json_value(response)
        if actual is not None and (type(actual) is not int or actual < 0):
            raise ValueError("Invalid actual model usage")
        with self._lock:
            if self.database is None:
                return self._finish_model(root_id, path, response, actual)
            with self._connection() as connection:
                return self._finish_model(root_id, path, response, actual, connection=connection)

    def reject_model(
        self, root_id: str, path: tuple[str, ...], error_type: str, message: str
    ) -> None:
        with self._lock:
            if self.database is None:
                self._finish_model(root_id, path, None, 0, error_type, message)
            else:
                with self._connection() as connection:
                    self._finish_model(root_id, path, None, 0, error_type, message, connection)

    def fail(self, run_id: str, error: str) -> None:
        """Finish a failed program without claiming success or discarding committed effects."""
        with self._lock:
            if self.database is None:
                run = self._runs[run_id]
                if not run.completed and run.error is None:
                    self._runs[run_id] = replace(run, error=error)
                    self._finish_history(run_id)
            else:
                with self._connection() as connection:
                    connection.execute(
                        "UPDATE runs SET error=? WHERE run_id=? AND completed=0 AND error IS NULL",
                        (error, run_id),
                    )
                    self._finish_history(run_id, connection)

    def _finish_history(self, run_id: str, connection: sqlite3.Connection | None = None) -> None:
        """Commit terminal ordering alongside the authoritative lifecycle fact."""
        ended_at = datetime.now(UTC).isoformat()
        if connection is None:
            previous = self._history_times.get(run_id)
            if previous is not None and previous[1] is None:
                self._history_times[run_id] = (previous[0], ended_at)
        else:
            connection.execute(
                "UPDATE entry_history_metadata SET ended_at=? WHERE run_id=? AND ended_at IS NULL",
                (ended_at, run_id),
            )

    @contextmanager
    def _history_connection(self) -> Iterator[sqlite3.Connection]:
        """A read-only snapshot; opening history never initializes authority tables."""
        assert self.database is not None
        connection = sqlite3.connect(self.database.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            connection.execute("BEGIN")
            if connection.execute("PRAGMA user_version").fetchone()[0] != 2:
                raise RecoveryMismatchError("Unsupported program database format")
            yield connection
        finally:
            connection.close()

    @staticmethod
    def _history_clock_sql(connection: sqlite3.Connection) -> tuple[str, str]:
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='entry_history_metadata'"
        ).fetchone()
        return (
            ("m.started_at,m.ended_at", "LEFT JOIN entry_history_metadata m ON e.run_id=m.run_id")
            if exists
            else ("NULL,NULL", "")
        )

    @staticmethod
    def _entry_reading(run_id: str, package_edn: str, result_edn: str | None) -> dict[str, Any]:
        package = json_value(loads(package_edn), edn_keys=True)
        contract = hashlib.sha256(
            json.dumps(
                {slot: package.get("model_contexts", {}).get(slot) for slot in ("host", "config")},
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ).encode()
        ).hexdigest()
        return {
            "run_id": run_id,
            "entry": package.get("entry"),
            "contract": contract,
            "result": json_value(loads(result_edn), edn_keys=True) if result_edn else None,
        }

    def previous_entry(self, run_id: str) -> dict[str, Any]:
        """Read the latest successful same-entry result preceding this run's start.

        Packages are decoded one at a time; no invocation history is loaded.
        Module source identity is deliberately absent from the model contract.
        """
        with self._lock:
            if self.database is None:
                current = self._runs.get(run_id)
                if current is None or current.package_edn is None:
                    raise ValueError("current run is not a recorded program entry")
                start = self._history_times.get(run_id, (None, None))[0]
                owner = self._entry_reading(run_id, current.package_edn, None)
                candidates = sorted(
                    (
                        (self._history_times.get(key, (None, None))[1], key)
                        for key, run in self._runs.items()
                        if key != run_id
                        and run.completed
                        and run.error is None
                        and run.package_edn is not None
                    ),
                    key=lambda item: (item[0] or "", item[1]),
                    reverse=True,
                )
                previous = None
                for ended, key in candidates:
                    if start is None or ended is None or ended > start:
                        continue
                    run = self._runs[key]
                    assert run.package_edn is not None
                    candidate = self._entry_reading(key, run.package_edn, run.result_edn)
                    if candidate["entry"] == owner["entry"]:
                        previous = candidate
                        break
            else:
                with self._history_connection() as connection:
                    row = connection.execute(
                        "SELECT e.package_edn,m.started_at FROM entry_programs e "
                        "JOIN entry_history_metadata m USING(run_id) WHERE e.run_id=?",
                        (run_id,),
                    ).fetchone()
                    if row is None:
                        raise ValueError("current run is not a recorded program entry")
                    owner = self._entry_reading(run_id, row[0], None)
                    previous = None
                    rows = connection.execute(
                        "SELECT e.run_id,e.package_edn,e.result_edn FROM entry_programs e "
                        "JOIN runs r USING(run_id) JOIN entry_history_metadata m USING(run_id) "
                        "WHERE r.completed=1 AND r.error IS NULL AND e.run_id<>? "
                        "AND m.ended_at IS NOT NULL AND m.ended_at<=? "
                        "ORDER BY m.ended_at DESC,e.run_id DESC",
                        (run_id, row[1]),
                    )
                    for key, package, result in rows:
                        candidate = self._entry_reading(key, package, result)
                        if candidate["entry"] == owner["entry"]:
                            previous = candidate
                            break
            if previous is not None:
                del previous["entry"]
            return {"run_id": run_id, "contract": owner["contract"], "previous": previous}

    def entry_metadata(self) -> list[dict[str, Any]]:
        """Read terminal clocks without reading source packages or invocations."""
        with self._lock:
            if self.database is None:
                return [
                    {
                        "run_id": run.run_id,
                        "completed": run.completed,
                        "error": run.error,
                        "started_at": self._history_times.get(run.run_id, (None, None))[0],
                        "ended_at": self._history_times.get(run.run_id, (None, None))[1],
                    }
                    for run in self._runs.values()
                    if run.package_edn is not None
                ]
            if not self.database.exists():
                return []
            with self._history_connection() as connection:
                clocks, join = self._history_clock_sql(connection)
                return [
                    dict(
                        zip(
                            ("run_id", "completed", "error", "started_at", "ended_at"),
                            row,
                            strict=True,
                        )
                    )
                    for row in connection.execute(
                        f"SELECT e.run_id,r.completed,r.error,{clocks} FROM entry_programs e "
                        f"JOIN runs r ON r.run_id=e.run_id {join} ORDER BY e.rowid"
                    )
                ]

    def entry_history(
        self, run_id: str | None = None, *, include_invocations: bool = True
    ) -> list[dict[str, Any]]:
        """Read a consistent snapshot without build verification or execution.

        Preexisting entries without clock metadata remain explicitly undated.
        """
        with self._lock:
            if self.database is None:
                return [
                    {
                        "run": run,
                        "started_at": self._history_times.get(run.run_id, (None, None))[0],
                        "ended_at": self._history_times.get(run.run_id, (None, None))[1],
                        "invocations": [
                            (
                                json.loads(self._identity(invocation)),
                                json.loads(outcome) if outcome is not None else None,
                            )
                            for invocation, outcome in self._invocations.values()
                            if include_invocations and invocation.root_id == run.run_id
                        ],
                    }
                    for run in self._runs.values()
                    if run.package_edn is not None and (run_id is None or run.run_id == run_id)
                ]
            if not self.database.exists():
                return []
            with self._history_connection() as connection:
                clocks, join = self._history_clock_sql(connection)
                rows = connection.execute(
                    f"SELECT e.run_id,{clocks} FROM entry_programs e {join} "
                    "WHERE (? IS NULL OR e.run_id=?) ORDER BY e.rowid",
                    (run_id, run_id),
                ).fetchall()
                return [
                    {
                        "run": self._read_run(connection, run_id),
                        "started_at": started_at,
                        "ended_at": ended_at,
                        "invocations": [
                            (
                                json.loads(identity),
                                json.loads(outcome) if outcome is not None else None,
                            )
                            for identity, outcome in (
                                connection.execute(
                                    "SELECT identity_json,outcome_json FROM invocations "
                                    "WHERE root_id=? ORDER BY rowid",
                                    (run_id,),
                                )
                                if include_invocations
                                else []
                            )
                        ],
                    }
                    for run_id, started_at, ended_at in rows
                ]

    def close(self) -> None:
        """No persistent SQLite connection is held, including for bare instances."""
