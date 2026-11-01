"""Injected member credentials; the execution layer never reads host secrets."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field


@dataclass(frozen=True)
class GiteaExecutionAccess:
    url: str
    username: str
    token: str = field(repr=False)
    network: str


GiteaAccessProvider = Callable[[str], Awaitable[GiteaExecutionAccess]]
