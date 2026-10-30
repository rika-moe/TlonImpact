"""Windowed frozen application's only entry point."""

import sys

sys.dont_write_bytecode = True


def run() -> int:
    from tlon_launcher.portable import main

    return main()


if __name__ == "__main__":
    raise SystemExit(run())
