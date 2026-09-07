from __future__ import annotations

import json
import os
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from scripts.bounded_search import (
    DEFAULT_TIMEOUT_SECONDS,
    STATUS_ERROR,
    STATUS_INCOMPLETE,
    STATUS_MATCH,
    STATUS_NO_MATCH,
    STATUS_TIMEOUT,
    SearchError,
    main,
    run_search,
)

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/bounded_search.py"


def _executable(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


class BoundedSearchTests(unittest.TestCase):
    def test_match_and_no_match_in_explicit_temp_module(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "module").mkdir()
            (root / "module/alpha.py").write_text("bounded_search_token = 1\n", encoding="utf-8")
            (root / "module/beta.py").write_text("other = 2\n", encoding="utf-8")
            matched = run_search(root=root, pattern="bounded_search_token", paths=["module"])
            missing = run_search(root=root, pattern="definitely-not-present-xyz", paths=["module"])
            self.assertEqual(matched["status"], STATUS_MATCH)
            self.assertIn("alpha.py", str(matched["stdout"]))
            self.assertEqual(missing["status"], STATUS_NO_MATCH)
            self.assertNotEqual(missing["status"], STATUS_TIMEOUT)
            self.assertNotEqual(missing["status"], STATUS_INCOMPLETE)

    def test_containment_rejects_escaped_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "ok.py").write_text("ok\n", encoding="utf-8")
            with self.assertRaises(SearchError):
                run_search(root=root, pattern="x", paths=["../escape"])
            with self.assertRaises(SearchError):
                run_search(root=Path.home(), pattern="x", paths=[])

    def test_timeout_is_not_no_match_and_kills_child(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "file.txt").write_text("needle\n", encoding="utf-8")
            fake = _executable(Path(directory) / "rg", "#!/bin/sh\nsleep 30\nexit 1\n")
            started = time.monotonic()
            result = run_search(
                root=root,
                pattern="needle",
                paths=["file.txt"],
                timeout_seconds=1,
                rg=str(fake),
            )
            self.assertLess(time.monotonic() - started, 5)
            self.assertEqual(result["status"], STATUS_TIMEOUT)
            self.assertNotEqual(result["status"], STATUS_NO_MATCH)
            self.assertIn("incomplete", str(result["detail"]))

    def test_cross_process_max_concurrency_one_with_slow_fake_rg(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "file.txt").write_text("needle\n", encoding="utf-8")
            marker = Path(directory) / "marker.log"
            fake = _executable(
                Path(directory) / "rg",
                f'#!/bin/sh\necho start >> "{marker}"\nsleep 0.4\necho end >> "{marker}"\nexit 0\n',
            )
            lock = Path(directory) / "lock"
            env = os.environ.copy()
            env["V23_BOUNDED_SEARCH_LOCK"] = str(lock)
            env["V23_BOUNDED_RG"] = str(fake)
            command = [
                sys.executable,
                str(SCRIPT),
                "--root",
                str(root),
                "--pattern",
                "needle",
                "--path",
                "file.txt",
            ]
            first = subprocess.Popen(command, env=env, stdout=subprocess.PIPE, text=True)
            second = subprocess.Popen(command, env=env, stdout=subprocess.PIPE, text=True)
            out1, _ = first.communicate(timeout=15)
            out2, _ = second.communicate(timeout=15)
            self.assertEqual(first.returncode, 0)
            self.assertEqual(second.returncode, 0)
            self.assertEqual(json.loads(out1)["status"], STATUS_MATCH)
            self.assertEqual(json.loads(out2)["status"], STATUS_MATCH)
            self.assertEqual(
                marker.read_text(encoding="utf-8").split(),
                ["start", "end", "start", "end"],
            )

    def test_signal_kills_child_before_lock_release(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "file.txt").write_text("needle\n", encoding="utf-8")
            pidfile = Path(directory) / "child.pid"
            fake = _executable(
                Path(directory) / "rg",
                f"#!/bin/sh\necho $$ > '{pidfile}'\ntrap '' TERM HUP INT\nsleep 30\n",
            )
            lock = Path(directory) / "lock"
            env = os.environ.copy()
            env["V23_BOUNDED_SEARCH_LOCK"] = str(lock)
            env["V23_BOUNDED_RG"] = str(fake)
            proc = subprocess.Popen(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--root",
                    str(root),
                    "--pattern",
                    "needle",
                    "--path",
                    "file.txt",
                    "--timeout",
                    "20",
                ],
                env=env,
                stdout=subprocess.PIPE,
                text=True,
            )
            for _ in range(50):
                if pidfile.exists() and pidfile.read_text(encoding="utf-8").strip():
                    break
                time.sleep(0.05)
            child_pid = int(pidfile.read_text(encoding="utf-8").strip())
            os.kill(proc.pid, signal.SIGTERM)
            proc.communicate(timeout=5)
            self.assertNotEqual(proc.returncode, 0)
            time.sleep(0.2)
            self.assertRaises(ProcessLookupError, os.kill, child_pid, 0)

    def test_output_cap_is_incomplete_not_no_match(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "file.txt").write_text("needle\n", encoding="utf-8")
            fake = _executable(
                Path(directory) / "rg",
                "#!/bin/sh\npython3 -c 'print(\"n\"*20000)'\n",
            )
            result = run_search(
                root=root,
                pattern="needle",
                paths=["file.txt"],
                timeout_seconds=5,
                rg=str(fake),
                output_cap=64,
            )
            self.assertEqual(result["status"], STATUS_INCOMPLETE)
            self.assertNotEqual(result["status"], STATUS_NO_MATCH)

    def test_no_config_isolates_ripgrep_config_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "keep.py").write_text("config_isolation_token = 1\n", encoding="utf-8")
            config = Path(directory) / "rgrc"
            config.write_text("--glob !*.py\n", encoding="utf-8")
            env = os.environ.copy()
            env["RIPGREP_CONFIG_PATH"] = str(config)
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--root",
                    str(root),
                    "--pattern",
                    "config_isolation_token",
                    "--path",
                    "keep.py",
                ],
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(json.loads(completed.stdout)["status"], STATUS_MATCH)

    def test_missing_executable_is_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "file.txt").write_text("needle\n", encoding="utf-8")
            result = run_search(
                root=root,
                pattern="needle",
                paths=["file.txt"],
                rg=str(Path(directory) / "missing-rg"),
            )
            self.assertEqual(result["status"], STATUS_ERROR)
            self.assertIn("unavailable", str(result["detail"]))

    def test_cli_match_and_too_broad_root(self) -> None:
        self.assertEqual(DEFAULT_TIMEOUT_SECONDS, 15)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "AGENTS.md").write_text("policy\n", encoding="utf-8")
            self.assertEqual(main(["--root", str(root), "--pattern", "policy"]), 0)
        self.assertEqual(main(["--root", "/tmp", "--pattern", "x"]), 2)


if __name__ == "__main__":
    unittest.main()
