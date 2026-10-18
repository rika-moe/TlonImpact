"""Durable member program inputs, effects and private structured state."""

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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

    def __init__(self, database: Path | None) -> None:
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
            connection.executescript(
                "CREATE TABLE IF NOT EXISTS runs (run_id TEXT PRIMARY KEY, "
                "rule_edn TEXT NOT NULL, input_edn TEXT NOT NULL, completed INTEGER NOT NULL DEFAULT 0, error TEXT);"
                "CREATE TABLE IF NOT EXISTS effects (run_id TEXT NOT NULL, ordinal INTEGER NOT NULL, "
                "name TEXT NOT NULL, args_edn TEXT NOT NULL, result_edn TEXT NOT NULL, "
                "PRIMARY KEY(run_id,ordinal));"
                "CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value_edn TEXT NOT NULL);"
            )
            connection.execute("BEGIN IMMEDIATE")
            with connection:
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
                for run_id, rule_edn, input_edn in items:
                    if run_id not in self._runs:
                        self._runs[run_id] = ProgramRun(run_id, rule_edn, input_edn, False)
                return [self._runs[run_id] for run_id, _, _ in items]
            with self._connection() as connection:
                records: list[ProgramRun] = []
                for run_id, rule_edn, input_edn in items:
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
                return [
                    run for run in self._runs.values() if not run.completed and run.error is None
                ]
            with self._connection() as connection:
                return [
                    ProgramRun(str(row[0]), str(row[1]), str(row[2]), False)
                    for row in connection.execute(
                        "SELECT run_id,rule_edn,input_edn FROM runs WHERE completed=0 AND error IS NULL ORDER BY rowid"
                    )
                ]

    @staticmethod
    def _check(
        row: tuple[str, str, str] | None, name: str, args_edn: str
    ) -> tuple[bool, str | None]:
        if row is None:
            return False, None
        if row[:2] != (name, args_edn):
            raise ValueError("Program replay effect name/arguments changed")
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
                raise ValueError("Program effect result conflicts with committed record")
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
                    raise ValueError("Program effect result conflicts with committed record")
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

    def atomic_state_put_record(
        self, run_id: str, index: int, name: str, args_edn: str, key: str, value: Any
    ) -> Any:
        """Commit state and its replay result together; replay never reapplies state."""
        encoded = dumps(value)
        with self._lock:
            if self.database is None:
                found, previous = self.get_effect(run_id, index, name, args_edn)
                if found:
                    assert previous is not None
                    return loads(previous)
                self._effects[(run_id, index)] = (name, args_edn, encoded)
                self._state[key] = encoded
            else:
                with self._connection() as connection:
                    row = connection.execute(
                        "SELECT name,args_edn,result_edn FROM effects WHERE run_id=? AND ordinal=?",
                        (run_id, index),
                    ).fetchone()
                    found, previous = self._check(row, name, args_edn)
                    if found:
                        assert previous is not None
                        return loads(previous)
                    connection.execute(
                        "INSERT INTO state VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value_edn=excluded.value_edn",
                        (key, encoded),
                    )
                    self._record(connection, run_id, index, name, args_edn, encoded)
        return loads(encoded)

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
