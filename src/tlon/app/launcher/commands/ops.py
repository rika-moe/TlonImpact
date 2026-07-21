"""Ops command - Operator TUI (operations console).

Tlon Operator - Professional operations console for engineers.

The Operator is a TypeScript/Ink application living in apps/operator/.
This command locates it and launches it with node, passing connection
parameters through.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from tlon.base.logger import configure_logging, get_logger

if TYPE_CHECKING:
    from argparse import Namespace

logger = get_logger(__name__)

# Repository root: src/tlon/app/launcher/commands/ops.py -> parents[5]
REPO_ROOT = Path(__file__).resolve().parents[5]
OPERATOR_DIR = REPO_ROOT / "apps" / "operator"


def _find_operator_command() -> list[str] | None:
    """Build the command to launch the Operator TUI.

    Prefers the built app (dist/cli.js), falls back to tsx dev mode when
    dependencies are installed but the app has not been built.

    Returns:
        Command argv list, or None if the app is not installed/built.
    """
    dist_cli = OPERATOR_DIR / "dist" / "cli.js"
    if dist_cli.exists():
        return ["node", str(dist_cli)]

    tsx_bin = OPERATOR_DIR / "node_modules" / ".bin" / "tsx"
    src_cli = OPERATOR_DIR / "src" / "cli.tsx"
    if tsx_bin.exists() and src_cli.exists():
        return [str(tsx_bin), str(src_cli)]

    return None


async def run_ops(args: Namespace) -> int:
    """Run the ops command (Operator TUI).

    Operator - Professional operations console for engineers.

    Args:
        args: Parsed arguments

    Returns:
        Exit code
    """
    is_lucid = getattr(args, "debug", False)
    if is_lucid:
        configure_logging(log_level="DEBUG", debug=True)
    else:
        configure_logging(silent=True)

    if shutil.which("node") is None:
        print("\n❌ Operator TUI requires Node.js (>= 22), but 'node' was not found in PATH.")
        print("   Install Node.js: https://nodejs.org/")
        return 1

    command = _find_operator_command()
    if command is None:
        print(f"\n❌ Operator TUI is not installed/built in {OPERATOR_DIR}")
        print("   Run:")
        print("       cd apps/operator && npm install && npm run build")
        return 1

    command += [
        "--host",
        args.host,
        "--port",
        str(args.port),
        "--rest-port",
        str(args.rest_port),
    ]

    logger.info(f"Launching Operator TUI: {' '.join(command)}")

    try:
        result = subprocess.run(command, cwd=OPERATOR_DIR, check=False)
        return result.returncode
    except KeyboardInterrupt:
        print("\n\n运维控制台已关闭...")
        return 130
    except Exception as e:
        logger.error(f"Operator TUI error: {e}")
        print(f"\n❌ Operator TUI error: {e}")
        return 1
