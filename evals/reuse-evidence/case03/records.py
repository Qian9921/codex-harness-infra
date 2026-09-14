"""Local record loading."""

import json
from pathlib import Path


def load_object(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError("record file must contain a JSON object")
    return payload
