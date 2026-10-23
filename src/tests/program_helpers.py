"""Small ordinary module packages shared by domain tests."""

from pathlib import Path

from tlon.agents.member_spec import MemberSpec
from tlon.agents.program_package import validate_program_sources

MINIMAL_BEHAVIOR = "{:version 2 :handlers {:boot work/handle}}"
MINIMAL_MODULES = {
    "work.tlon": "(ns work (:export handle r2)) (defn handle [event] {:result nil}) (defn r2 [event] {:result nil})"
}


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
