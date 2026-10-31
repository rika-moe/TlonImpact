"""Small ordinary module packages shared by domain tests."""

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from tlon.agents.member_spec import MemberSpec
from tlon.agents.program_package import validate_program_sources

MINIMAL_BEHAVIOR = "{:version 2 :handlers {:boot work/handle}}"
MINIMAL_MODULES = {
    "work.tlon": "(ns work (:export handle r2)) (defn handle [event] {:result nil}) (defn r2 [event] {:result nil})"
}


def member_boot_idle(home: Path, agent: dict[str, Any]) -> bool:
    """Require persisted boot completion as well as an idle resident snapshot."""
    if not agent["resident"] or agent["state"] != "waiting" or agent["waking"]:
        return False
    database = home / agent["id"] / "program.sqlite3"
    if not database.exists():
        return False
    with closing(sqlite3.connect(database)) as connection:
        initialized = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='runs'"
        ).fetchone()
        if not initialized:
            return False
        runs = connection.execute("SELECT run_id, completed, error FROM runs").fetchall()
    boot_finished = any(
        identity.endswith(":handler:boot") and completed and error is None
        for identity, completed, error in runs
    )
    return boot_finished and all(completed or error is not None for _, completed, error in runs)


def fixture_modules(behavior: str) -> dict[str, str]:
    """The two explicit package shapes used by prototype REST tests."""
    if ":parameters" in behavior:
        return {"work.tlon": "(ns work (:export handle)) (defn handle [event] {:result ${to}})"}
    return dict(MINIMAL_MODULES)


def make_program(body: str = "", *, events: tuple[str, ...] = ("boot",)) -> MemberSpec:
    handlers = " ".join(f":{event} work/handle" for event in events)
    return validate_program_sources(
        "{:version 2 :handlers {" + handlers + "}}",
        {
            "work.tlon": "(ns work (:export handle)) (defn handle [event] "
            + body
            + " {:result nil})"
        },
    )


def write_program(
    path: Path, behavior: str = MINIMAL_BEHAVIOR, modules: dict[str, str] | None = None
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(behavior, encoding="utf-8")
    for name, source in (modules if modules is not None else MINIMAL_MODULES).items():
        target = path.parent / "modules" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")
