"""Print a record identifier from a JSON file."""

from __future__ import annotations

import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: app.py PATH", file=sys.stderr)
        return 2
    print(Path(argv[1]).read_text(encoding="utf-8"), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
