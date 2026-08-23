"""Regression tests for evaluator accounting and suite provenance."""

from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path

from scripts.evaluate import Scenario, load_scenarios, run_offline


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


if __name__ == "__main__":
    unittest.main()
