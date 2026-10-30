"""Own the real server and its control listener in one joinable worker thread."""

from __future__ import annotations

import asyncio
import hmac
import threading
import traceback
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tlon_server.serverapp import ServerApp

from tlon_launcher.portable_instance import InstanceLease, signature
from tlon_launcher.portable_paths import PortableLayout


class PortableRuntime:
    def __init__(
        self,
        layout: PortableLayout,
        lease: InstanceLease,
        notify: Callable[[str, str], None],
        *,
        smoke_seconds: float | None = None,
    ) -> None:
        self.layout, self.lease, self.notify = layout, lease, notify
        self.smoke_seconds = smoke_seconds
        self.url = ""
        self.status = "starting"
        self.error: str | None = None
        self.stopped = threading.Event()
        self._stop = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task[None] | None = None
        self.thread = threading.Thread(target=self._run, name="tlon-server", daemon=False)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        if self._stop.is_set():
            return
        self._stop.set()
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._cancel)

    def _cancel(self) -> None:
        if self.status == "stopping":
            return
        self.status = "stopping"
        self.notify("stopping", "Tlon 正在保存并退出…")
        if self._task is not None:
            self._task.cancel()

    def join(self) -> None:
        self.thread.join()

    def _run(self) -> None:
        try:
            asyncio.run(self._serve())
        except BaseException:
            self.error = "Tlon 未能正常运行或退出。请打开日志目录查看 portable.log。"
            traceback.print_exc()
            self.notify("error", self.error)
        finally:
            self._loop = None
            self.stopped.set()
            self.notify("stopped", "")

    async def _serve(self) -> None:
        from aiohttp import ClientSession, web

        self._loop = asyncio.get_running_loop()
        self._task = asyncio.current_task()
        app: ServerApp | None = None
        start_task: asyncio.Task[None] | None = None
        ready_task: asyncio.Task[bool] | None = None
        control = web.Application(client_max_size=4096)

        async def command(request: web.Request) -> web.Response:
            try:
                message = await request.json()
                payload = message["payload"]
                if not hmac.compare_digest(message["mac"], signature(self.lease.secret, payload)):
                    raise ValueError("invalid authentication")
                if payload["action"] not in {"status", "open", "quit"}:
                    raise ValueError("unknown action")
                result = {"nonce": payload["nonce"], "status": self.status, "url": self.url}
            except (ValueError, KeyError, TypeError):
                raise web.HTTPForbidden() from None
            response = web.json_response(
                {"payload": result, "mac": signature(self.lease.secret, result)}
            )
            if payload["action"] == "quit":
                # Finish the authenticated response before cancellation owns shutdown.
                await response.prepare(request)
                await response.write_eof()
                self.stop()
            return response

        control.router.add_post("/control", command)
        runner = web.AppRunner(control, access_log=None)
        try:
            await runner.setup()
            await web.TCPSite(runner, "127.0.0.1", 0).start()
            self.lease.publish(runner.addresses[0][1])
            # Heavy imports happen only after portable cache locations have been set.

            from tlon.base.logger import configure_logging
            from tlon.base.paths import TlonPaths
            from tlon.base.settings import Settings
            from tlon_server.api.rest import WEBUI_DIST_DIR
            from tlon_server.serverapp import ServerApp

            paths = TlonPaths(home=self.layout.data)
            settings = Settings.from_env()
            configure_logging(log_level=settings.log_level, log_dir=paths.logs_dir())
            if not (WEBUI_DIST_DIR / "index.html").is_file():
                raise RuntimeError("发行包缺少 WebUI，请重新下载完整 Tlon ZIP。")
            app = ServerApp(
                host="127.0.0.1",
                rest_port=0,
                tlon_paths=paths,
                settings=settings,
            )
            if self._stop.is_set():
                return
            await app.initialize()
            start_task = asyncio.create_task(app.start())
            ready_task = asyncio.create_task(app.ready.wait())
            done, _ = await asyncio.wait(
                (start_task, ready_task), return_when=asyncio.FIRST_COMPLETED
            )
            if start_task in done:
                await start_task
                raise RuntimeError("服务在就绪前退出。")
            self.url = f"http://127.0.0.1:{app.rest_port}/ui/"
            async with ClientSession() as client, client.get(self.url) as response:
                if response.status != 200:
                    raise RuntimeError("浏览器界面未就绪。")
                await response.read()
            self.status = "ready"
            self.notify("ready", self.url)
            if self.smoke_seconds is not None:
                asyncio.get_running_loop().call_later(self.smoke_seconds, self.stop)
            await start_task
            if not self._stop.is_set():
                raise RuntimeError("服务意外停止。")
        except asyncio.CancelledError:
            if not self._stop.is_set():
                raise
        finally:
            self.status = "stopping"
            # Stop requests during teardown must not cancel the cleanup coroutine again.
            for pending in (ready_task, start_task):
                if pending is not None:
                    pending.cancel()
            await asyncio.gather(
                *(t for t in (ready_task, start_task) if t), return_exceptions=True
            )
            try:
                if app is not None:
                    try:
                        await app.shutdown()
                    finally:
                        # Even an earlier component's shutdown failure cannot
                        # strand the listener while the UI reports that error.
                        if app.rest is not None:
                            await app.rest.stop()
            finally:
                await runner.cleanup()
