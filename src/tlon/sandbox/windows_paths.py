"""Validate logical components before interpreting them as Windows paths."""

_RESERVED = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"} | {
    f"{prefix}{number}" for prefix in ("COM", "LPT") for number in "123456789¹²³"
}


def validate_windows_component(name: str) -> None:
    """Reject separators, stream/device aliases and lossy Win32 normalization."""
    if (
        not name
        or name in {".", ".."}
        or name.endswith((".", " "))
        or any(ord(char) < 32 or char in '\\/:<>"|?*' for char in name)
        or name.split(".", 1)[0].rstrip(" ").upper() in _RESERVED
    ):
        raise ValueError(f"Unsafe Windows path component: {name!r}")
