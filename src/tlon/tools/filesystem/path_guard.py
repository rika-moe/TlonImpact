"""Path sandbox guard for filesystem tools.

All paths here are *container* paths — normalization is pure POSIX string
processing (posixpath), never host filesystem resolution.
"""

import posixpath

# Default sandbox root inside the container (matches DockerRuntimeConfig.working_dir)
DEFAULT_ALLOWED_PATHS: list[str] = ["/workspace"]


def normalize_container_path(path: str) -> str:
    """Normalize a container path using POSIX semantics.

    Collapses ``.``, ``..``, duplicate separators and trailing slashes.
    The result is always absolute. ``..`` segments that would escape the
    root are clamped at ``/`` (same as POSIX normpath behavior).
    """
    normalized = posixpath.normpath(path)
    if not normalized.startswith("/"):
        normalized = "/" + normalized
    # posixpath.normpath preserves exactly two leading slashes ("//a") —
    # collapse them so prefix checks cannot be confused.
    while normalized.startswith("//"):
        normalized = normalized[1:]
    return normalized


def resolve_container_path(
    file_path: str,
    working_dir: str,
    allowed_paths: list[str],
) -> str:
    """Resolve ``file_path`` to an absolute container path within the sandbox.

    Relative paths are joined onto ``working_dir`` first, then normalized.
    The resolved path must be one of ``allowed_paths`` or live beneath one
    of them; anything else (absolute paths outside the whitelist, ``..``
    escapes) is rejected.

    Args:
        file_path: Path as provided by the agent (relative or absolute).
        working_dir: Container working directory for relative paths.
        allowed_paths: Whitelisted absolute container paths.

    Returns:
        The normalized absolute container path.

    Raises:
        ValueError: If the resolved path is outside every allowed root.
    """
    if not file_path:
        raise ValueError("file_path must not be empty")

    if file_path.startswith("/"):
        candidate = file_path
    else:
        candidate = posixpath.join(normalize_container_path(working_dir), file_path)
    resolved = normalize_container_path(candidate)

    for allowed in allowed_paths:
        root = normalize_container_path(allowed)
        prefix = root.rstrip("/") + "/"  # "/" becomes "/", others get one trailing slash
        if resolved == root or resolved.startswith(prefix):
            return resolved

    raise ValueError(
        f"Path '{file_path}' resolves to '{resolved}', which is outside the "
        f"allowed paths: {allowed_paths}"
    )
