"""Locate fixed built-in prototype IDs by their behavior display names."""

from functools import cache
from importlib import resources

from tlon.agents.prototypes.info import PROTOTYPE_PACKAGE
from tlosure.edn import loads


@cache
def builtin_id(display_name: str) -> str:
    """Return a package-carried prototype's stable ID from its metadata."""
    for entry in resources.files(PROTOTYPE_PACKAGE).iterdir():
        metadata = entry.joinpath("prototype.tlon")
        if not entry.is_dir() or not metadata.is_file():
            continue
        values = {
            str(key).lstrip(":"): value
            for key, value in loads(metadata.read_text(encoding="utf-8")).items()
        }
        if values.get("display-name") == display_name:
            return str(values["name"]).lstrip(":")
    raise ValueError(f"No built-in prototype with display name {display_name!r}")
