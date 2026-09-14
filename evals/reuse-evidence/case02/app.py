"""Job elapsed-time display."""

from __future__ import annotations

import sys

from formatters import format_duration


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: app.py MILLISECONDS", file=sys.stderr)
        return 2
    print(format_duration(int(argv[1])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
