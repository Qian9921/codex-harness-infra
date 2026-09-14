"""Precondition and helper-call checks for disposable forward packs."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKS = ROOT / "evals/reuse-evidence"


def _load(module_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(app: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(app), *args],
        check=False,
        capture_output=True,
        text=True,
        cwd=str(app.parent),
    )


class ReuseEvidenceFixtureTests(unittest.TestCase):
    def test_case01_helper_matches_elapsed_ms_while_app_prints_raw(self) -> None:
        clock = _load("case01_clock", PACKS / "case01/clock.py")
        self.assertEqual(clock.format_duration_ms(65000), "01:05.000")
        self.assertEqual(clock.format_duration_ms(125000), "02:05.000")
        completed = _run(PACKS / "case01/app.py", "65000")
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "65000\n")

    def test_case02_named_helper_is_iso_wall_clock_not_elapsed(self) -> None:
        formatters = _load("case02_formatters", PACKS / "case02/formatters.py")
        rendered = formatters.format_duration(65000)
        self.assertIn("T", rendered)
        self.assertTrue(rendered.endswith("Z"))
        self.assertNotEqual(rendered, "01:05.000")
        completed = _run(PACKS / "case02/app.py", "65000")
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout.strip(), rendered)

    def test_case03_owner_loader_returns_object_while_app_dumps_file(self) -> None:
        records = _load("case03_records", PACKS / "case03/records.py")
        payload = records.load_object(PACKS / "case03/sample.json")
        self.assertEqual(payload["id"], "rec-1")
        completed = _run(PACKS / "case03/app.py", "sample.json")
        self.assertEqual(completed.returncode, 0)
        self.assertIn("{", completed.stdout)
        self.assertNotEqual(completed.stdout.strip(), "rec-1")

    def test_case04_log_records_timeout_and_notes_are_unchecked_guess(self) -> None:
        log = (PACKS / "case04/run.log").read_text(encoding="utf-8")
        notes = (PACKS / "case04/notes.md").read_text(encoding="utf-8")
        self.assertIn("timeout", log)
        self.assertIn("incomplete", log)
        self.assertIn("not imported", notes)
        self.assertFalse((PACKS / "case04/app.py").exists())


if __name__ == "__main__":
    unittest.main()
