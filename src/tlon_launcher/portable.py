"""Executable entry for ADR 0053; bootstrap paths before heavyweight imports."""

from __future__ import annotations

import argparse
import contextlib
import sys
import traceback
from pathlib import Path

from tlon_launcher.portable_paths import configure_environment, prepare_layout, seed_tokenizer_cache


def main(argv: list[str] | None = None, *, executable: Path | None = None) -> int:
    parser = argparse.ArgumentParser(prog="Tlon")
    parser.add_argument(
        "--headless", action="store_true", help="Run without tray/browser for package verification"
    )
    parser.add_argument(
        "--quit", action="store_true", help="Ask this folder's existing instance to exit"
    )
    parser.add_argument("--smoke-seconds", type=float, default=None)
    args = parser.parse_args(argv)
    if args.smoke_seconds is not None and (not args.headless or args.smoke_seconds <= 0):
        parser.error("--smoke-seconds requires --headless and a positive duration")
    logs: Path | None = None
    try:
        layout = prepare_layout(executable or Path(sys.executable))
        logs = layout.logs
        from dotenv import load_dotenv

        load_dotenv(layout.data / ".env", override=True, interpolate=False)
        configure_environment(layout)
        from filelock import Timeout

        from tlon_launcher.portable_instance import InstanceLease, request_existing

        lease = InstanceLease(layout.control)
        if args.quit:
            request_existing(layout.data, "quit")
            return 0
        try:
            lease.acquire()
        except Timeout:
            result = request_existing(layout.data, "open", timeout=120)
            if result["status"] != "ready":
                raise RuntimeError("Tlon 正在退出，请稍后重试。") from None
            if not args.headless and sys.platform == "win32":
                from tlon_launcher.portable_tray import open_browser

                open_browser(result["url"])
            return 0
        try:
            # PyInstaller windowed mode has no stdout/stderr. Capture dependency
            # diagnostics too, inside the portable data directory.
            with (
                (logs / "portable.log").open("a", encoding="utf-8", buffering=1) as stream,
                contextlib.redirect_stdout(stream),
                contextlib.redirect_stderr(stream),
            ):
                seed_tokenizer_cache(layout)
                from tlon_launcher.portable_runtime import PortableRuntime

                if args.headless:
                    runtime = PortableRuntime(
                        layout,
                        lease,
                        lambda event, value: None,
                        smoke_seconds=args.smoke_seconds,
                    )
                    runtime.start()
                    try:
                        runtime.join()
                    except KeyboardInterrupt:
                        runtime.stop()
                        runtime.join()
                elif sys.platform == "win32":
                    from tlon_launcher.portable_tray import Tray

                    tray = Tray(layout)
                    runtime = PortableRuntime(layout, lease, tray.notify)
                    tray.run(runtime)
                else:
                    raise RuntimeError("Windows 托盘只能在 Windows 上运行。")
                return 1 if runtime.error else 0
        finally:
            lease.close()
    except Exception as exc:
        if logs:
            with (
                contextlib.suppress(OSError),
                (logs / "portable.log").open("a", encoding="utf-8") as stream,
            ):
                traceback.print_exc(file=stream)
        if not args.headless and sys.platform == "win32":
            from tlon_launcher.portable_tray import show_error

            show_error(f"Tlon 无法启动：{exc}", str(logs) if logs else None)
        elif sys.stderr is not None:
            print(f"Tlon 无法启动：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
