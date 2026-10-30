"""Own test subprocess trees, including descendants surviving their launcher."""

import asyncio
import contextlib
import os
import signal
import subprocess
import sys
from typing import Any
from uuid import uuid4

# Joining the Windows job must precede execution of code that can spawn children.
_GATE = "import sys; sys.stdin.buffer.read(1) == b'1' or sys.exit(1); exec(sys.argv[1])"


class ProcessTree:
    """A POSIX process group or a Windows job owned by the test controller."""

    def __init__(self, proc: asyncio.subprocess.Process, job: Any = None) -> None:
        self.proc = proc
        self._job = job

    @classmethod
    async def start(cls, code: str, *, env: dict[str, str] | None = None) -> "ProcessTree":
        job: Any = None
        options: dict[str, Any] = {}
        proc: asyncio.subprocess.Process | None = None
        try:
            if sys.platform == "win32":
                import win32job

                job = win32job.CreateJobObject(None, f"tlon-test-{uuid4().hex}")
                limits = win32job.QueryInformationJobObject(
                    job, win32job.JobObjectExtendedLimitInformation
                )
                limits["BasicLimitInformation"]["LimitFlags"] = (
                    win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                )
                win32job.SetInformationJobObject(
                    job, win32job.JobObjectExtendedLimitInformation, limits
                )
                options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                options["start_new_session"] = True
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                _GATE,
                code,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=env,
                **options,
            )
            if job is not None:
                import win32api
                import win32con
                import win32job

                handle = win32api.OpenProcess(
                    win32con.PROCESS_SET_QUOTA | win32con.PROCESS_TERMINATE, False, proc.pid
                )
                try:
                    win32job.AssignProcessToJobObject(job, handle)
                finally:
                    handle.Close()
            assert proc.stdin is not None
            proc.stdin.write(b"1")
            await proc.stdin.drain()
            proc.stdin.close()
            return cls(proc, job)
        except BaseException:
            # Cancellation during gate release may already have started descendants.
            if proc is not None:
                cls(proc, job).kill()
            if job is not None:
                job.Close()
            if proc is not None:
                # Assignment itself may have failed, leaving the gated root outside the job.
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                await asyncio.wait_for(proc.communicate(), 5.0)
            raise

    def request_stop(self) -> None:
        """Send the platform's cooperative console signal, even after parent exit."""
        if sys.platform == "win32":
            # Process.send_signal skips exited parents; their console group may live on.
            with contextlib.suppress(OSError):
                os.kill(self.proc.pid, signal.CTRL_BREAK_EVENT)
        else:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(self.proc.pid, signal.SIGTERM)

    def kill(self) -> None:
        """Terminate all members, including orphaned descendants."""
        if self._job is not None:
            import win32job

            win32job.TerminateJobObject(self._job, 1)
        elif sys.platform != "win32":
            with contextlib.suppress(ProcessLookupError):
                os.killpg(self.proc.pid, signal.SIGKILL)

    def close(self) -> None:
        """Release the sole job handle; closing also kills remaining members."""
        if self._job is not None:
            self._job.Close()
            self._job = None
