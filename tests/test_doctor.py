from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.doctor import doctor
from scripts.install import install
from scripts.task_bootstrap import ToolResult

ROOT = Path(__file__).resolve().parents[1]


class DoctorTests(unittest.TestCase):
    def test_direct_script_entrypoint(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(ROOT / "scripts/doctor.py"), "--help"],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_blank_home_reports_effective_profiles_and_project_rules(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            local = root / "local.toml"
            local.write_text(
                """
[models]
primary = "primary-model"
primary_effort = "medium"
executor = "executor-model"
executor_effort = "medium"
reviewer = "reviewer-model"

[opening]
instruction = "Local-only opening."

[tools]
codegraph = "codegraph"
semble = "semble"
rtk = "rtk"
""".lstrip(),
                encoding="utf-8",
            )
            codex_home = root / "codex"
            install(ROOT, codex_home, local, root / "state")

            report = doctor(
                codex_home,
                local,
                ROOT / "tests",
                check_github=False,
                tool_probe=lambda _cwd, _prompt, _tools: [
                    ToolResult("CodeGraph", True, "queried"),
                    ToolResult("Semble", True, "searched"),
                    ToolResult("RTK", True, "inspected"),
                ],
            )
            checks = {check["name"]: check for check in report["checks"]}
            self.assertTrue(report["ok"])
            self.assertEqual(report["active_global_instruction"], str(codex_home / "AGENTS.md"))
            self.assertTrue(checks["global_agents_canonical"]["ok"])
            self.assertTrue(checks["global_override_absent"]["ok"])
            self.assertTrue(checks["codex_config_syntax"]["ok"])
            self.assertTrue(checks["primary_profile"]["ok"])
            self.assertTrue(checks["agent_v23_executor"]["ok"])
            self.assertTrue(checks["agent_v23_reviewer"]["ok"])
            self.assertTrue(checks["task_bootstrap_hook"]["ok"])
            self.assertTrue(checks["grok_execution_route"]["ok"])
            detail = str(checks["grok_execution_route"]["detail"])
            self.assertIn(str(codex_home / "bin/grok-execution.py"), detail)
            self.assertIn(str(ROOT / "scripts/grok_execution.py"), detail)
            self.assertTrue(checks["tool_codegraph"]["ok"])
            self.assertTrue(checks["tool_semble"]["ok"])
            self.assertTrue(checks["tool_rtk"]["ok"])
            self.assertEqual(report["project_instruction_candidates"], [str(ROOT / "AGENTS.md")])
            self.assertIn("install.json", report["live_runtime_authority"])
            self.assertIn("historical only", report["live_runtime_authority"])

            skipped = doctor(
                codex_home,
                local,
                ROOT / "tests",
                check_github=False,
                probe_required_tools=False,
                tool_probe=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    AssertionError("Doctor must not re-enter full tool bootstrap")
                ),
            )
            skipped_names = {check["name"] for check in skipped["checks"]}
            self.assertNotIn("tool_codegraph", skipped_names)
            self.assertEqual(report["primary_profile_start"], "codex --profile v23-primary")

            (codex_home / "AGENTS.override.md").write_text("leftover override\n", encoding="utf-8")
            blocked = doctor(
                codex_home,
                local,
                ROOT / "tests",
                check_github=False,
                tool_probe=lambda _cwd, _prompt, _tools: [
                    ToolResult("CodeGraph", True, "queried"),
                    ToolResult("Semble", True, "searched"),
                    ToolResult("RTK", True, "inspected"),
                ],
            )
            blocked_checks = {check["name"]: check for check in blocked["checks"]}
            self.assertFalse(blocked["ok"])
            self.assertFalse(blocked_checks["global_override_absent"]["ok"])
            self.assertEqual(
                blocked["active_global_instruction"], str(codex_home / "AGENTS.override.md")
            )
            self.assertTrue(blocked_checks["global_portable"]["ok"])
            self.assertTrue(blocked_checks["global_local"]["ok"])

            (codex_home / "AGENTS.override.md").write_text("   \n", encoding="utf-8")
            skipped = doctor(
                codex_home,
                local,
                ROOT / "tests",
                check_github=False,
                tool_probe=lambda _cwd, _prompt, _tools: [
                    ToolResult("CodeGraph", True, "queried"),
                    ToolResult("Semble", True, "searched"),
                    ToolResult("RTK", True, "inspected"),
                ],
            )
            skipped_checks = {check["name"]: check for check in skipped["checks"]}
            self.assertTrue(skipped["ok"])
            self.assertTrue(skipped_checks["global_override_absent"]["ok"])
            self.assertEqual(skipped["active_global_instruction"], str(codex_home / "AGENTS.md"))

            (codex_home / "AGENTS.override.md").unlink()
            recovered = doctor(
                codex_home,
                local,
                ROOT / "tests",
                check_github=False,
                tool_probe=lambda _cwd, _prompt, _tools: [
                    ToolResult("CodeGraph", True, "queried"),
                    ToolResult("Semble", True, "searched"),
                    ToolResult("RTK", True, "inspected"),
                ],
            )
            recovered_checks = {check["name"]: check for check in recovered["checks"]}
            self.assertTrue(recovered["ok"])
            self.assertTrue(recovered_checks["global_override_absent"]["ok"])


if __name__ == "__main__":
    unittest.main()
