"""Windows-only launch gate used to assign a command to a Job Object first."""

from __future__ import annotations

import subprocess
import sys


def main() -> int:
    """Wait for the parent assignment signal, then run the original argv."""

    if len(sys.argv) < 2 or sys.stdin.buffer.read(1) != b"\0":
        return 125
    return subprocess.call(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
