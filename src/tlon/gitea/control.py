"""Cooperative cancellation and a wall-clock startup budget."""

import threading
import time

from tlon.gitea.storage import GiteaError


class StartupControl:
    def __init__(self, stop: threading.Event | None = None, *, timeout: float = 300) -> None:
        self.stop = stop if stop is not None else threading.Event()
        self.deadline = time.monotonic() + timeout

    def check(self) -> None:
        if self.stop.is_set():
            raise GiteaError("Gitea startup cancelled")
        if time.monotonic() >= self.deadline:
            raise GiteaError("Gitea startup timed out")

    def pause(self, seconds: float) -> None:
        self.stop.wait(seconds)
        self.check()
