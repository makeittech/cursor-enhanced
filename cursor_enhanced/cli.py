"""CLI entrypoint for cursor-enhanced."""

import sys
from typing import Optional

from main import main as _main


def main(argv: Optional[list[str]] = None) -> None:
    if argv is None:
        _main()
        return
    original_argv = sys.argv
    try:
        sys.argv = [original_argv[0]] + list(argv)
        _main()
    finally:
        sys.argv = original_argv
