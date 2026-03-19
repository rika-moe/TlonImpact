"""Wizard CLI command.

Entry point for the Minions configuration wizard.
"""

import sys

from tlon.base.logger import get_logger

logger = get_logger(__name__)


def run_wizard() -> int:
    """Run the configuration wizard.

    Returns:
        Exit code (0 for success)
    """
    try:
        from ..wizard.core import Wizard

        wizard = Wizard()
        wizard.run()
        return 0

    except ImportError:
        print("\n" + "=" * 50)
        print("  Wizard 依赖未安装")
        print("=" * 50)
        print("\n请安装以下依赖:")
        print("  pip install rich questionary")
        print("\n或者安装完整依赖:")
        print("  pip install -e '.[dev]'")
        print("=" * 50 + "\n")
        return 1

    except Exception as e:
        logger.error(f"Wizard error: {e}", exc_info=True)
        print(f"\n错误: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(run_wizard())
