"""Durable member program inputs, effects and private structured state."""

import hashlib
import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tlon.base.execution import BudgetExceededError
from tlon.operations.invocation import Invocation, InvocationOutcome
from tlon.operations.schema import RecoveryMismatchError, ReplayConflictError, edn_value, json_value
from tlosure.edn import dumps, loads


@dataclass(frozen=True)
class ProgramRun:
    run_id: str
    rule_edn: str
    input_edn: str
    completed: bool
    error: str | None = None


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
        self._invocations: dict[str, tuple[Invocation, str | None]] = {}
        self.database = database
        self._lock = threading.RLock()
        self._runs: dict[str, ProgramRun] = {}
        self._effects: dict[tuple[str, int], tuple[str, str, str]] = {}
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
                "CREATE TABLE IF NOT EXISTS effects (run_id TEXT NOT NULL, ordinal INTEGER NOT NULL, "
                "name TEXT NOT NULL, args_edn TEXT NOT NULL, result_edn TEXT NOT NULL, "
                "PRIMARY KEY(run_id,ordinal));"
                "CREATE TABLE IF NOT EXISTS roots (root_id TEXT PRIMARY KEY, build_id TEXT NOT NULL);"
                "CREATE TABLE IF NOT EXISTS invocations (call_id TEXT PRIMARY KEY, identity_json TEXT NOT NULL, outcome_json TEXT, root_id TEXT NOT NULL, path_json TEXT NOT NULL, UNIQUE(root_id,path_json));"
                "PRAGMA user_version=2;"
                "CREATE TABLE IF NOT EXISTS budgets (root_id TEXT PRIMARY KEY, token_limit INTEGER NOT NULL, measure TEXT NOT NULL, deadline REAL);"
                "CREATE TABLE IF NOT EXISTS model_allocations (call_id TEXT PRIMARY KEY, root_id TEXT NOT NULL, reserved INTEGER NOT NULL, output_limit INTEGER NOT NULL, charged INTEGER);"
                "CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value_edn TEXT NOT NULL);"
            )
            with connection:
                for statement in schema.split(";"):
                    if statement.strip():
                        connection.execute(statement)
                yield connection
        finally:
            connection.close()

    def ensure(self, run_id: str, rule_edn: str, input_edn: str) -> ProgramRun:
        """Create once; an existing invocation always retains its original source/input."""
        return self.ensure_many([(run_id, rule_edn, input_edn)])[0]

    def ensure_many(self, items: list[tuple[str, str, str]]) -> list[ProgramRun]:
        """Register an event's rules atomically in execution order, retaining existing records."""
        if not items:
            return []
        with self._lock:
            if self.database is None:
                for run_id, _, _ in items:
                    previous = self._roots.get(run_id)
                    frozen = self._runs.get(run_id)
                    if frozen is not None and frozen.error is not None:
                        continue
                    if previous is not None and previous != self.build_id:
                        raise RecoveryMismatchError("Runtime build changed for frozen root")
                for run_id, rule_edn, input_edn in items:
                    frozen = self._runs.get(run_id)
                    if frozen is not None and frozen.error is not None:
                        continue
                    self._root(run_id)
                    if run_id not in self._runs:
                        self._runs[run_id] = ProgramRun(run_id, rule_edn, input_edn, False)
                return [self._runs[run_id] for run_id, _, _ in items]
            with self._connection() as connection:
                records: list[ProgramRun] = []
                for run_id, rule_edn, input_edn in items:
                    failed = connection.execute(
                        "SELECT run_id,rule_edn,input_edn,completed,error FROM runs WHERE run_id=? AND error IS NOT NULL",
                        (run_id,),
                    ).fetchone()
                    if failed is not None:
                        records.append(
                            ProgramRun(
                                str(failed[0]),
                                str(failed[1]),
                                str(failed[2]),
                                bool(failed[3]),
                                failed[4],
                            )
                        )
                        continue
                    self._root(run_id, connection)
                    connection.execute(
                        "INSERT OR IGNORE INTO runs(run_id,rule_edn,input_edn) VALUES (?,?,?)",
                        (run_id, rule_edn, input_edn),
                    )
                    row = connection.execute(
                        "SELECT run_id,rule_edn,input_edn,completed,error FROM runs WHERE run_id=?",
                        (run_id,),
                    ).fetchone()
                    assert row is not None
                    records.append(
                        ProgramRun(str(row[0]), str(row[1]), str(row[2]), bool(row[3]), row[4])
                    )
                return records

    def pending(self) -> list[ProgramRun]:
        with self._lock:
            if self.database is None:
                records = [
                    run for run in self._runs.values() if not run.completed and run.error is None
                ]
                return records
            with self._connection() as connection:
                records = [
                    ProgramRun(str(row[0]), str(row[1]), str(row[2]), False)
                    for row in connection.execute(
                        "SELECT run_id,rule_edn,input_edn FROM runs WHERE completed=0 AND error IS NULL ORDER BY rowid"
                    )
                ]
                return records

    def get_run(self, run_id: str) -> ProgramRun | None:
        """Inspect durable lifecycle status without authorizing execution."""
        with self._lock:
            if self.database is None:
                return self._runs.get(run_id)
            with self._connection() as connection:
                row = connection.execute(
                    "SELECT run_id,rule_edn,input_edn,completed,error FROM runs WHERE run_id=?",
                    (run_id,),
                ).fetchone()
                return (
                    ProgramRun(str(row[0]), str(row[1]), str(row[2]), bool(row[3]), row[4])
                    if row
                    else None
                )

    @staticmethod
    def _check(
        row: tuple[str, str, str] | None, name: str, args_edn: str
    ) -> tuple[bool, str | None]:
        if row is None:
            return False, None
        if row[:2] != (name, args_edn):
            raise ReplayConflictError("Program replay effect name/arguments changed")
        return True, row[2]

    def get_effect(
        self, run_id: str, index: int, name: str, args_edn: str
    ) -> tuple[bool, str | None]:
        """Return (found, result EDN), distinguishing a recorded nil from absence."""
        with self._lock:
            if self.database is None:
                return self._check(self._effects.get((run_id, index)), name, args_edn)
            with self._connection() as connection:
                row = connection.execute(
                    "SELECT name,args_edn,result_edn FROM effects WHERE run_id=? AND ordinal=?",
                    (run_id, index),
                ).fetchone()
                return self._check(row, name, args_edn)

    @staticmethod
    def _record(
        connection: sqlite3.Connection,
        run_id: str,
        index: int,
        name: str,
        args_edn: str,
        result_edn: str,
    ) -> None:
        existing = connection.execute(
            "SELECT name,args_edn,result_edn FROM effects WHERE run_id=? AND ordinal=?",
            (run_id, index),
        ).fetchone()
        if existing is not None:
            if existing != (name, args_edn, result_edn):
                raise ReplayConflictError("Program effect result conflicts with committed record")
            return
        connection.execute(
            "INSERT INTO effects VALUES (?,?,?,?,?)", (run_id, index, name, args_edn, result_edn)
        )

    def record_effect(
        self, run_id: str, index: int, name: str, args_edn: str, result_edn: str
    ) -> None:
        with self._lock:
            if self.database is None:
                key = (run_id, index)
                value = (name, args_edn, result_edn)
                if key in self._effects and self._effects[key] != value:
                    raise ReplayConflictError(
                        "Program effect result conflicts with committed record"
                    )
                self._effects[key] = value
                return
            with self._connection() as connection:
                self._record(connection, run_id, index, name, args_edn, result_edn)

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
        budget = self._budget(root_id, connection)
        charge = (
            allocation["reserved"] if actual is None or budget["measure"] == "output" else actual
        )
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

    def complete(self, run_id: str) -> None:
        with self._lock:
            if self.database is None:
                run = self._runs[run_id]
                if run.error is None:
                    self._runs[run_id] = ProgramRun(run.run_id, run.rule_edn, run.input_edn, True)
            else:
                with self._connection() as connection:
                    connection.execute(
                        "UPDATE runs SET completed=1 WHERE run_id=? AND error IS NULL", (run_id,)
                    )

    def fail(self, run_id: str, error: str) -> None:
        """Finish a failed program without claiming success or discarding committed effects."""
        with self._lock:
            if self.database is None:
                run = self._runs[run_id]
                if not run.completed and run.error is None:
                    self._runs[run_id] = ProgramRun(
                        run.run_id, run.rule_edn, run.input_edn, False, error
                    )
            else:
                with self._connection() as connection:
                    connection.execute(
                        "UPDATE runs SET error=? WHERE run_id=? AND completed=0 AND error IS NULL",
                        (error, run_id),
                    )

    def close(self) -> None:
        """No persistent SQLite connection is held, including for bare instances."""
