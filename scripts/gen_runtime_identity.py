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
    digest = hashlib.sha256((ROOT / "uv.lock").read_bytes()).hexdigest()
    expected = (
        '\"\"\"Generated from uv.lock by scripts/gen_runtime_identity.py; do not edit.\"\"\"\n\n'
        f'LOCK_DIGEST = "{digest}"\n'
    )
    if args.check:
        if not TARGET.exists() or TARGET.read_text() != expected:
            raise SystemExit("Runtime dependency fingerprint is stale; run scripts/gen_runtime_identity.py")
        print("✅ runtime dependency fingerprint is fresh")
    else:
        TARGET.write_text(expected)


if __name__ == "__main__":
    main()
