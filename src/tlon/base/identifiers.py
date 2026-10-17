"""Opaque, system-assigned identifiers for prototypes and members."""

from uuid import uuid4


def new_prototype_id() -> str:
    """Allocate a prototype identifier without social or behavioral meaning."""
    return f"p-{uuid4().hex}"


def new_member_id() -> str:
    """Allocate a member identifier independent of its prototype or persona."""
    return f"m-{uuid4().hex}"
