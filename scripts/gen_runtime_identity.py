"""Generate/check the packaged fingerprint of the locked dependency resolution."""

import argparse
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "src/tlon/base/_locked_dependencies.py"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    # Git may check out text as CRLF on Windows. Dependency identity follows
    # lock content, not the host checkout's line-ending convention.
    lock_text = (ROOT / "uv.lock").read_text(encoding="utf-8")
    digest = hashlib.sha256(lock_text.encode("utf-8")).hexdigest()
    expected = (
        '\"\"\"Generated from uv.lock by scripts/gen_runtime_identity.py; do not edit.\"\"\"\n\n'
        f'LOCK_DIGEST = "{digest}"\n'
    )
    if args.check:
        if not TARGET.exists() or TARGET.read_text(encoding="utf-8") != expected:
            raise SystemExit("Runtime dependency fingerprint is stale; run scripts/gen_runtime_identity.py")
        print("Runtime dependency fingerprint is fresh")
    else:
        TARGET.write_text(expected, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
