"""Presence checks for disposable reuse-evidence packs. No answer labels."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKS = (
    "exact-function",
    "similar-name",
    "neighbor-dep",
    "contradict-hypothesis",
)


class ReuseEvidenceFixtureTests(unittest.TestCase):
    def test_four_artifact_packs_exist(self) -> None:
        base = ROOT / "evals/reuse-evidence"
        self.assertTrue((base / "README.md").is_file())
        for name in PACKS:
            pack = base / name
            self.assertTrue((pack / "task.md").is_file(), name)
            artifacts = [path for path in pack.iterdir() if path.name != "task.md"]
            self.assertGreaterEqual(len(artifacts), 1, name)


if __name__ == "__main__":
    unittest.main()
