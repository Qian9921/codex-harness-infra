"""Real-Pi smoke tests for the owned V23 tool-routing extension.

These tests launch the installed ``pi`` binary with the candidate extension and
an offline mock provider, so the real extension loader, ``tool_call`` path, and
built-in ``bash`` tool execute the fixture commands. No provider credentials or
network access are used. Tests skip when ``pi`` or ``node`` is unavailable.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import grok_execution
from scripts.install import install

ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "package/pi/v23-enforce-tools.ts"
MOCK_PROVIDER = ROOT / "tests/fixtures/pi_mock_provider.ts"
STUB_SOURCE = """#!{python}
import json
import os
import sys

with open(os.environ["V23_RTK_STUB_LOG"], "a", encoding="utf-8") as handle:
    handle.write(json.dumps(sys.argv[1:]) + "\\n")
raise SystemExit(int(os.environ.get("V23_RTK_STUB_EXIT", "0")))
"""


def _executable(value: str | None) -> str | None:
    if not value:
        return None
    path = Path(value).expanduser()
    if path.is_file() and os.access(path, os.X_OK):
        return str(path.resolve())
    return shutil.which(value)


def _read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    records: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(json.loads(line))
    return records


class PiExtensionSmokeTests(unittest.TestCase):
    pi: str

    @classmethod
    def setUpClass(cls) -> None:
        pi = _executable(os.environ.get("PI_BIN") or "pi")
        if pi is None:
            raise unittest.SkipTest("pi executable is unavailable")
        node = _executable("node")
        if node is None:
            raise unittest.SkipTest("node executable is unavailable for the pi launcher")
        for required in (EXTENSION, MOCK_PROVIDER):
            if not required.is_file():
                raise unittest.SkipTest(f"missing smoke fixture: {required}")
        cls.pi = pi
        cls.node = node

    def _run_pi(
        self,
        *,
        commands: list[str],
        stub_exit: int = 0,
        rtk_absent: bool = False,
        extension: Path = EXTENSION,
    ) -> tuple[subprocess.CompletedProcess[str], list[list[str]], list[dict]]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            work = root / "work"
            work.mkdir()
            home = root / "home"
            home.mkdir()
            stub = work / "rtk-stub"
            stub.write_text(STUB_SOURCE.format(python=sys.executable), encoding="utf-8")
            stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            stub_log = root / "rtk-argv.jsonl"
            rtk_log = root / "rtk-log.jsonl"
            prompt = work / "prompt.txt"
            prompt.write_text("please run the v23-smoke tool calls\n", encoding="utf-8")
            env = {
                **os.environ,
                "HOME": str(home),
                "PI_OFFLINE": "1",
                "V23_MOCK_COMMANDS": json.dumps(commands),
                "V23_MOCK_TRIGGER": "v23-smoke",
                "V23_RTK_STUB_LOG": str(stub_log),
                "V23_RTK_STUB_EXIT": str(stub_exit),
            }
            if rtk_absent:
                bin_dir = root / "bin"
                bin_dir.mkdir()
                (bin_dir / "node").symlink_to(self.node)
                env["PATH"] = str(bin_dir)
                env.pop("V23_RTK_BIN", None)
            else:
                env["V23_RTK_BIN"] = str(stub)
            command = [
                self.pi,
                "--provider",
                "v23-mock",
                "--model",
                "v23-mock-1",
                "--thinking",
                "off",
                "--mode",
                "json",
                "-p",
                "--offline",
                "--no-context-files",
                "--no-extensions",
                "--no-skills",
                "--no-prompt-templates",
                "--no-approve",
                "-e",
                str(MOCK_PROVIDER),
                "-e",
                str(extension),
                "--v23-rtk-log",
                str(rtk_log),
                "--tools",
                "bash",
                f"@{prompt}",
            ]
            completed = subprocess.run(
                command,
                cwd=work,
                env=env,
                text=True,
                capture_output=True,
                check=False,
                timeout=180,
            )
            stub_calls = [
                json.loads(line)
                for line in (
                    stub_log.read_text(encoding="utf-8").splitlines() if stub_log.exists() else []
                )
                if line.strip()
            ]
            records = _read_jsonl(rtk_log)
        return completed, stub_calls, records

    def test_installed_extension_is_the_one_pi_loads(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            local = root / "local.toml"
            local.write_text(
                '[models]\nprimary = "primary-model"\nexecutor = "executor-model"\n'
                'reviewer = "reviewer-model"\n',
                encoding="utf-8",
            )
            codex_home = root / "codex"
            install(ROOT, codex_home, local, root / "state")
            installed = codex_home / "harness/v23/pi/v23-enforce-tools.ts"
            self.assertTrue(installed.is_file())
            with mock.patch.dict(
                os.environ, {"CODEX_HOME": str(codex_home), "V23_PI_EXTENSION": ""}, clear=False
            ):
                self.assertEqual(grok_execution._enforcement_extension(), installed.resolve())
            completed, stub_calls, records = self._run_pi(
                commands=["pytest -q installed.py"], extension=installed
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(stub_calls, [["pytest", "-q", "installed.py"]])
        routes = [record for record in records if record.get("event") == "rtk-route"]
        self.assertEqual(len(routes), 1, records)
        self.assertIn("installed.py", str(routes[0]["command"]))

    def test_real_pi_routes_pytest_and_keeps_raw_commands_raw(self) -> None:
        completed, stub_calls, records = self._run_pi(
            commands=[
                'pytest -q "test file.py"',
                "git diff --stat",
                "git status --porcelain",
                "pytest --json-report -q",
                "pytest -q && echo compound",
            ],
            stub_exit=3,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(stub_calls, [["pytest", "-q", "test file.py"]])
        routes = [record for record in records if record.get("event") == "rtk-route"]
        self.assertEqual(len(routes), 1, records)
        self.assertEqual(routes[0]["route"], "pytest")
        self.assertIn("rtk-stub", str(routes[0]["command"]))
        raw_exceptions = [
            record for record in records if record.get("event") == "rtk-raw-exception"
        ]
        self.assertEqual(len(raw_exceptions), 1, records)
        results = [
            record
            for record in records
            if record.get("event") == "rtk-result" and record.get("kind") == "routed"
        ]
        self.assertEqual(len(results), 1, records)
        self.assertTrue(results[0]["isError"], results)
        self.assertEqual(results[0]["kind"], "routed")

    def test_real_pi_states_explicit_fallback_when_rtk_is_absent(self) -> None:
        completed, stub_calls, records = self._run_pi(
            commands=["pytest -q"],
            rtk_absent=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(stub_calls, [])
        fallbacks = [record for record in records if record.get("event") == "rtk-fallback"]
        self.assertEqual(len(fallbacks), 1, records)
        self.assertIn("rtk-not-found", fallbacks[0]["reason"])
        self.assertNotIn("rtk-route", [record.get("event") for record in records])
        self.assertIn("rtk unavailable", completed.stdout)


if __name__ == "__main__":
    unittest.main()
