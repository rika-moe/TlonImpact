"""Durable member program inputs, effects and private structured state."""

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
            if version not in (0, 1) or (version == 0 and tables):
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
                "PRAGMA user_version=1;"
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
                    if previous is not None and previous != self.build_id:
                        raise RecoveryMismatchError("Runtime build changed for frozen root")
                for run_id, rule_edn, input_edn in items:
                    self._root(run_id)
                    if run_id not in self._runs:
                        self._runs[run_id] = ProgramRun(run_id, rule_edn, input_edn, False)
                return [self._runs[run_id] for run_id, _, _ in items]
            with self._connection() as connection:
                records: list[ProgramRun] = []
                for run_id, rule_edn, input_edn in items:
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
                for run in records:
                    self._root(run.run_id)
                return records
            with self._connection() as connection:
                records = [
                    ProgramRun(str(row[0]), str(row[1]), str(row[2]), False)
                    for row in connection.execute(
                        "SELECT run_id,rule_edn,input_edn FROM runs WHERE completed=0 AND error IS NULL ORDER BY rowid"
                    )
                ]
                for run in records:
                    self._root(run.run_id, connection)
                return records

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
