from __future__ import annotations

import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from scripts.bounded_search import (
    _TERMINATION_SIGNALS,
    DEFAULT_TIMEOUT_SECONDS,
    STATUS_ERROR,
    STATUS_INCOMPLETE,
    STATUS_MATCH,
    STATUS_NO_MATCH,
    STATUS_TIMEOUT,
    SearchError,
    _child_reset_inherited_signal_mask,
    _spawn_search,
    _SpawnCleanupToken,
    main,
    run_search,
    terminate_group,
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

    def test_direct_exit_descendant_with_redirected_pipes_is_killed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "file.txt").write_text("needle\n", encoding="utf-8")
            pidfile = Path(directory) / "descendant.pid"
            fake = _executable(
                Path(directory) / "rg",
                f"#!/bin/sh\nsleep 30 >/dev/null 2>&1 &\necho $! > '{pidfile}'\nexit 0\n",
            )
            result = run_search(
                root=root,
                pattern="needle",
                paths=["file.txt"],
                timeout_seconds=5,
                rg=str(fake),
            )
            self.assertEqual(result["status"], STATUS_MATCH)
            descendant = int(pidfile.read_text(encoding="utf-8").strip())
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                try:
                    os.kill(descendant, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.05)
            else:
                self.fail(f"descendant {descendant} still alive after match")

    def test_spawn_blocks_signals_until_cleanup_ownership(self) -> None:
        blocked_at_publish: list[bool] = []
        real_init = _SpawnCleanupToken.__init__

        def wrapped_init(self: _SpawnCleanupToken, proc: object) -> None:
            blocked = signal.pthread_sigmask(signal.SIG_BLOCK, [])
            blocked_at_publish.append(set(_TERMINATION_SIGNALS) <= set(blocked))
            real_init(self, proc)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "file.txt").write_text("needle\n", encoding="utf-8")
            fake = _executable(Path(directory) / "rg", "#!/bin/sh\nexit 0\n")
            with mock.patch.object(_SpawnCleanupToken, "__init__", wrapped_init):
                result = run_search(
                    root=root,
                    pattern="needle",
                    paths=["file.txt"],
                    rg=str(fake),
                )
        self.assertEqual(result["status"], STATUS_MATCH)
        self.assertEqual(blocked_at_publish, [True])

    def test_cleanup_blocks_signals_before_done_flag(self) -> None:
        class FakeProc:
            pid = 999_999
            stdout = None
            stderr = None

            def __init__(self) -> None:
                self.killed = 0

            def kill(self) -> None:
                self.killed += 1

            def wait(self, timeout: float | None = None) -> int:
                return 0

        proc = FakeProc()
        token = _SpawnCleanupToken(proc)
        real_mask = signal.pthread_sigmask
        blocked_before_done: list[bool] = []
        interrupt_first_block = True

        def wrapped_mask(how: int, mask: object) -> set[int]:
            nonlocal interrupt_first_block
            if how == signal.SIG_BLOCK:
                blocked_before_done.append(token._done)
                if interrupt_first_block:
                    interrupt_first_block = False
                    raise KeyboardInterrupt(signal.SIGTERM)
            return real_mask(how, mask)

        with mock.patch("scripts.bounded_search.signal.pthread_sigmask", wrapped_mask):
            with self.assertRaises(KeyboardInterrupt):
                token.kill_and_drain()
            self.assertFalse(token._done)
            self.assertEqual(proc.killed, 0)
            token.kill_and_drain()
        self.assertTrue(token._done)
        self.assertEqual(proc.killed, 1)
        self.assertEqual(blocked_before_done, [False, False])

    def test_child_unblocks_termination_signals_and_restores_sigpipe(self) -> None:
        script = (
            "import signal, sys\n"
            "blocked = signal.pthread_sigmask(signal.SIG_BLOCK, [])\n"
            "sys.stdout.write(' '.join(str(item) for item in sorted(blocked)))\n"
        )
        previous = signal.pthread_sigmask(signal.SIG_BLOCK, _TERMINATION_SIGNALS)
        try:
            proc = _spawn_search([sys.executable, "-c", script], Path("."))
            try:
                stdout, _stderr = proc.communicate(timeout=5)
            finally:
                terminate_group(proc)
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous)
        blocked = {int(item) for item in stdout.decode("utf-8", "replace").split() if item}
        self.assertTrue(set(_TERMINATION_SIGNALS).isdisjoint(blocked), msg=stdout)

        names = [
            name for name in ("SIGPIPE", "SIGXFSZ") if isinstance(getattr(signal, name, None), int)
        ]
        self.assertIn("SIGPIPE", names)
        sleep_bin = shutil.which("sleep")
        self.assertIsNotNone(sleep_bin)
        assert sleep_bin is not None
        ignored_handlers = {name: signal.getsignal(getattr(signal, name)) for name in names}
        try:
            for name in names:
                signal.signal(getattr(signal, name), signal.SIG_IGN)
            proc = _spawn_search([sleep_bin, "5"], Path("."))
            try:
                deadline = time.monotonic() + 5
                status_text = ""
                while time.monotonic() < deadline:
                    path = Path(f"/proc/{proc.pid}/status")
                    try:
                        status_text = path.read_text(encoding="utf-8")
                    except OSError:
                        time.sleep(0.01)
                        continue
                    fields = {}
                    for line in status_text.splitlines():
                        if ":" in line:
                            key, value = line.split(":", 1)
                            fields[key] = value.strip()
                    if fields.get("Name") == "sleep" and "SigIgn" in fields:
                        ignored = int(fields["SigIgn"], 16)
                        for name in names:
                            signum = getattr(signal, name)
                            self.assertEqual(ignored & (1 << (signum - 1)), 0, msg=name)
                        break
                else:
                    self.fail(f"did not observe exec of sleep: {status_text}")
            finally:
                terminate_group(proc)
        finally:
            for name, handler in ignored_handlers.items():
                signal.signal(getattr(signal, name), handler)

        seen: list[int] = []

        class MissingXfsz:
            SIGTERM = signal.SIGTERM
            SIGHUP = signal.SIGHUP
            SIGINT = signal.SIGINT
            SIGPIPE = signal.SIGPIPE
            SIG_DFL = signal.SIG_DFL
            SIG_UNBLOCK = signal.SIG_UNBLOCK

            @staticmethod
            def signal(signum: int, handler: object) -> None:
                seen.append(signum)

            @staticmethod
            def pthread_sigmask(how: int, mask: object) -> set[int]:
                return set()

        with mock.patch("scripts.bounded_search.signal", MissingXfsz):
            _child_reset_inherited_signal_mask()
        self.assertIn(signal.SIGPIPE, seen)
        if hasattr(signal, "SIGXFSZ"):
            self.assertNotIn(signal.SIGXFSZ, seen)

    def test_cli_match_and_too_broad_root(self) -> None:
        self.assertEqual(DEFAULT_TIMEOUT_SECONDS, 15)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "AGENTS.md").write_text("policy\n", encoding="utf-8")
            self.assertEqual(main(["--root", str(root), "--pattern", "policy"]), 0)
        self.assertEqual(main(["--root", "/tmp", "--pattern", "x"]), 2)


if __name__ == "__main__":
    unittest.main()
