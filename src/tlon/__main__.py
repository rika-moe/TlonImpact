"""Tlon package entry point.

This module serves as the entry point when running the package directly:
    $ python -m tlon

It delegates to the CLI module for command handling.
"""

from tlon.cli.main import main

if __name__ == "__main__":
    main()
