"""Regression tests for evaluator accounting and suite provenance."""

from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from scripts.evaluate import Scenario, derive_state_dir, load_scenarios, main, run_live, run_offline


def run_generated_case(name: str, method: object) -> dict[str, object]:
    """Run one temporary unittest case through the production evaluator."""
    module_name = f"v23_evaluator_fixture_{name}"
    module = types.ModuleType(module_name)
    case = type("GeneratedCase", (unittest.TestCase,), {"test_case": method})
    case.__module__ = module_name
    module.GeneratedCase = case
    sys.modules[module_name] = module
    try:
        scenario = Scenario(
            name, "runner", "fixture", "fixture", f"{module_name}.GeneratedCase.test_case"
        )
        return run_offline([scenario])[0]
    finally:
        sys.modules.pop(module_name, None)


class EvaluatorRunnerTests(unittest.TestCase):
    def test_skipped_case_is_unobserved_not_pass(self) -> None:
        def skip_case(case: unittest.TestCase) -> None:
            case.skipTest("fixture skipped")

        result = run_generated_case("skipped", skip_case)
        self.assertEqual(result["status"], "UNOBSERVED")
        self.assertEqual(result["detail"], "fixture skipped")

    def test_expected_failure_is_not_pass(self) -> None:
        @unittest.expectedFailure
        def expected_failure(case: unittest.TestCase) -> None:
            case.fail("fixture failure")

        result = run_generated_case("expected_failure", expected_failure)
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("fixture failure", str(result["detail"]))

    def test_unsupported_suite_version_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            suite = Path(directory) / "suite.toml"
            suite.write_text("version = 2\ncount = 0\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unsupported"):
                load_scenarios(suite)

    def test_empty_suite_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            suite = Path(directory) / "suite.toml"
            suite.write_text("version = 1\ncount = 0\nscenario = []\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "positive"):
                load_scenarios(suite)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(("--suite", str(suite))), 2)

    def test_live_derives_and_passes_explicit_state_dir(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "codex"
            hook = home / "harness/v23/task_bootstrap.py"
            hook.parent.mkdir(parents=True)
            hook.write_text("print('hook')\n", encoding="utf-8")
            local = Path(directory) / "local.toml"
            local.write_text("[tools]\n", encoding="utf-8")
            explicit = Path(directory) / "state"
            self.assertEqual(derive_state_dir(home, None), home / "harness/v23-state")
            self.assertEqual(derive_state_dir(home, explicit), explicit)
            observed: dict[str, tuple[str, ...]] = {}

            def fake_run(command, **_kwargs):
                observed["command"] = tuple(str(part) for part in command)
                payload = {
                    "hookSpecificOutput": {
                        "hookEventName": "UserPromptSubmit",
                        "additionalContext": "CodeGraph=ready Semble=ready RTK=ready",
                    }
                }
                return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

            with (
                mock.patch("scripts.evaluate.subprocess.run", side_effect=fake_run),
                mock.patch("scripts.doctor.doctor", return_value={"ok": True}),
            ):
                result = run_live(home, local, explicit)
            command = observed["command"]
            self.assertEqual(result["status"], "PASS")
            self.assertIn(str(hook), command)
            self.assertIn("--local-config", command)
            self.assertIn(str(local), command)
            self.assertIn("--codex-home", command)
            self.assertIn(str(home), command)
            self.assertIn("--state-dir", command)
            self.assertIn(str(explicit), command)


if __name__ == "__main__":
    unittest.main()
