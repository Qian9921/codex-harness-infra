from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import os
import pathlib
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from typing import Any
from unittest import mock

from scripts import grok_execution

SECRET_PROMPT = "SECRET-TASK-PROMPT-DO-NOT-LEAK"


def _run_args(directory: str, **overrides: object) -> argparse.Namespace:
    values = {
        "cwd": directory,
        "owned_path": ["owned.txt"],
        "session": None,
        "prompt": SECRET_PROMPT,
        "prompt_file": None,
        "effort": "low",
        "mode": "accept-edits",
        "task_id": "quota-test",
        "timeout": None,
        "receipt": None,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


class GrokExecutionTests(unittest.TestCase):
    def test_run_and_resume_default_to_no_timeout(self) -> None:
        run_args = grok_execution.parse_args(
            ["run", "--prompt", "bounded task", "--task-id", "one", "--owned-path", "file.txt"]
        )
        resume_args = grok_execution.parse_args(
            [
                "resume",
                "--prompt",
                "bounded task",
                "--task-id",
                "one",
                "--owned-path",
                "file.txt",
                "--session",
                "sess",
                "--receipt",
                "r.json",
            ]
        )
        self.assertIsNone(run_args.timeout)
        self.assertIsNone(resume_args.timeout)
        self.assertIsNone(grok_execution.DEFAULT_TIMEOUT_SECONDS)
        batch = grok_execution._batch_task(
            {"id": "one", "cwd": ".", "prompt": "task", "owned_paths": ["file.txt"]}
        )
        self.assertIsNone(batch.timeout)
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            grok_execution.parse_args(
                [
                    "run",
                    "--prompt",
                    "bounded task",
                    "--task-id",
                    "one",
                    "--owned-path",
                    "file.txt",
                    "--timeout",
                    "0",
                ]
            )
        explicit = grok_execution.parse_args(
            [
                "run",
                "--prompt",
                "bounded task",
                "--task-id",
                "one",
                "--owned-path",
                "file.txt",
                "--timeout",
                "12",
            ]
        )
        self.assertEqual(explicit.timeout, 12)
        self.assertEqual(grok_execution.POLL_INTERVAL_SECONDS, 1.0)

    def test_default_is_alive_treats_missing_proc_stat_as_alive(self) -> None:
        class FakeProc:
            def __init__(self, pid: int, returncode: int | None) -> None:
                self.pid = pid
                self.returncode = returncode

            def poll(self) -> int | None:
                return self.returncode

        exited = FakeProc(7, 0)
        self.assertFalse(grok_execution.default_is_alive(exited))

        running = FakeProc(11, None)
        with mock.patch.object(grok_execution, "_proc_state", return_value="R") as proc_state:
            self.assertTrue(grok_execution.default_is_alive(running))
            proc_state.assert_called_once_with(11)

        unreadable = FakeProc(13, None)
        with mock.patch.object(grok_execution, "_proc_state", return_value=None):
            self.assertTrue(grok_execution.default_is_alive(unreadable))

        zombie = FakeProc(17, None)
        with mock.patch.object(grok_execution, "_proc_state", return_value="Z"):
            self.assertFalse(grok_execution.default_is_alive(zombie))
            self.assertTrue(grok_execution.default_is_zombie(zombie))

        missing_stat = pathlib.Path("/proc/1/stat")
        original_read = pathlib.Path.read_text

        def fake_read(self: pathlib.Path, *args: object, **kwargs: object) -> str:
            if self == missing_stat:
                raise FileNotFoundError("stat vanished")
            return original_read(self, *args, **kwargs)

        with mock.patch.object(pathlib.Path, "read_text", fake_read):
            self.assertIsNone(grok_execution._proc_state(1))
            self.assertTrue(grok_execution.default_is_alive(FakeProc(1, None)))

    def test_supervised_wait_handles_liveness_death_and_zombie_without_hang(self) -> None:
        class FakeProc:
            def __init__(self, mode: str) -> None:
                self.mode = mode
                self.pid = 424242
                self._v23_pgid = 424242
                self.killed = False
                self.kill_calls = 0
                self.returncode: int | None = None

            def poll(self) -> int | None:
                return self.returncode

            def kill(self) -> None:
                self.killed = True
                self.kill_calls += 1

            def communicate(self, timeout: float | None = None) -> tuple[str, str]:
                if self.mode == "ok":
                    self.returncode = 0
                    return "out", "err"
                if self.mode == "zombie" and self.killed:
                    self.returncode = 7
                    return "zombie-out", "zombie-err"
                if self.killed:
                    self.returncode = -9
                    return "", ""
                raise subprocess.TimeoutExpired(["grok"], timeout)

        completed = grok_execution._supervised_run(
            ["grok"],
            pathlib.Path("."),
            timeout=None,
            spawn=lambda _command, _cwd: FakeProc("ok"),
            is_alive=lambda _proc: True,
            is_zombie=lambda _proc: False,
            sleep=lambda _seconds: None,
            clock=lambda: 0.0,
        )
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "out")

        zombie = FakeProc("zombie")
        completed = grok_execution._supervised_run(
            ["grok"],
            pathlib.Path("."),
            timeout=None,
            spawn=lambda _command, _cwd: zombie,
            is_alive=lambda _proc: True,
            is_zombie=lambda _proc: True,
            sleep=lambda _seconds: None,
            clock=lambda: 0.0,
        )
        self.assertTrue(zombie.killed)
        self.assertEqual(zombie.kill_calls, 1)
        self.assertEqual(completed.returncode, 7)
        self.assertEqual(completed.stdout, "zombie-out")
        self.assertEqual(completed.stderr, "zombie-err")
        self.assertNotIn(zombie.pid, grok_execution._REGISTERED_PGIDS)

        with self.assertRaisesRegex(grok_execution.BridgeError, "died"):
            grok_execution._supervised_run(
                ["grok"],
                pathlib.Path("."),
                timeout=None,
                spawn=lambda _command, _cwd: FakeProc("dead"),
                is_alive=lambda _proc: False,
                is_zombie=lambda _proc: False,
                sleep=lambda _seconds: None,
                clock=lambda: 0.0,
            )

        ticks = {"n": 0}

        def clock() -> float:
            ticks["n"] += 1
            return float(ticks["n"])

        with self.assertRaisesRegex(grok_execution.BridgeError, "timeout"):
            grok_execution._supervised_run(
                ["grok"],
                pathlib.Path("."),
                timeout=1,
                spawn=lambda _command, _cwd: FakeProc("late"),
                is_alive=lambda _proc: True,
                is_zombie=lambda _proc: False,
                sleep=lambda _seconds: None,
                clock=clock,
            )

    def test_supervised_run_drains_large_pipe_output_without_deadlock(self) -> None:
        script = (
            "import sys;"
            "sys.stdout.write('x' * 2_000_000);"
            "sys.stdout.flush();"
            "sys.stderr.write('y' * 64_000)"
        )
        completed = grok_execution._supervised_run(
            [sys.executable, "-c", script],
            pathlib.Path("."),
            timeout=15,
        )
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(len(completed.stdout), 2_000_000)
        self.assertEqual(len(completed.stderr), 64_000)

    def test_spawn_uses_dedicated_session_process_group(self) -> None:
        work = pathlib.Path(tempfile.mkdtemp())
        marker = work / "pgid.txt"
        script = work / "session.py"
        script.write_text(
            "import os, pathlib, time\n"
            f"pathlib.Path({str(marker)!r}).write_text(str(os.getpgrp()))\n"
            "time.sleep(2)\n",
            encoding="utf-8",
        )
        proc = grok_execution._spawn_grok([sys.executable, str(script)], pathlib.Path("."))
        try:
            self.assertEqual(proc._v23_pgid, proc.pid)
            self.assertEqual(os.getpgid(proc.pid), proc.pid)
            self.assertNotEqual(proc.pid, os.getpgrp())
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not marker.exists():
                time.sleep(0.05)
            self.assertEqual(marker.read_text(encoding="utf-8"), str(proc.pid))
        finally:
            grok_execution._stop_group_and_reap(proc)

    def test_spawn_does_not_pass_preexec_fn(self) -> None:
        captured: dict[str, object] = {}
        real_popen = grok_execution.subprocess.Popen

        def fake_popen(*args: object, **kwargs: object) -> object:
            captured["args"] = args
            captured["kwargs"] = kwargs
            return real_popen(*args, **kwargs)

        with mock.patch.object(grok_execution.subprocess, "Popen", fake_popen):
            proc = grok_execution._spawn_grok(
                [sys.executable, "-c", "import sys; sys.exit(0)"],
                pathlib.Path("."),
            )
        try:
            kwargs = captured["kwargs"]
            assert isinstance(kwargs, dict)
            self.assertNotIn("preexec_fn", kwargs)
            raw_args = captured["args"]
            assert isinstance(raw_args, tuple)
            argv = list(raw_args[0] if raw_args else kwargs["args"])
            self.assertEqual(argv[0], sys.executable)
            self.assertEqual(argv[1], str(grok_execution._module_path()))
            self.assertEqual(argv[2], grok_execution.CHILD_LAUNCHER_FLAG)
            self.assertEqual(argv[3], "--")
            self.assertEqual(argv[4], sys.executable)
            stdout, stderr = proc.communicate(timeout=5)
        finally:
            if proc.poll() is None:
                grok_execution._stop_group_and_reap(proc)
        self.assertEqual(proc.returncode, 0, msg=stderr)
        self.assertEqual(stdout, "")

    def test_installed_hyphen_launcher_spawns_and_preserves_cwd(self) -> None:
        work = pathlib.Path(tempfile.mkdtemp()).resolve()
        installed = work / "grok-execution.py"
        installed.write_text(
            pathlib.Path(grok_execution.__file__).read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        spec = importlib.util.spec_from_file_location("installed_grok_execution", installed)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        argv = module._child_launcher_argv(
            [sys.executable, "-c", "import os, sys; sys.stdout.write(os.getcwd())"]
        )
        self.assertEqual(pathlib.Path(argv[1]).name, "grok-execution.py")
        previous = signal.pthread_sigmask(signal.SIG_BLOCK, grok_execution._TERMINATION_SIGNALS)
        try:
            proc = module._spawn_grok(
                [sys.executable, "-c", "import os, sys; sys.stdout.write(os.getcwd())"],
                work,
            )
            try:
                stdout, stderr = proc.communicate(timeout=5)
            finally:
                if proc.poll() is None:
                    module._stop_group_and_reap(proc)
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous)
        self.assertEqual(proc.returncode, 0, msg=stderr)
        self.assertEqual(stdout, str(work))

    def test_orphan_descendant_inheriting_pipes_is_killed_promptly(self) -> None:
        work = pathlib.Path(tempfile.mkdtemp())
        descendant_path = work / "descendant.pid"
        script = work / "orphan.py"
        script.write_text(
            "import os, pathlib, time\n"
            f"path = pathlib.Path({str(descendant_path)!r})\n"
            "child = os.fork()\n"
            "if child == 0:\n"
            "    time.sleep(30)\n"
            "    os._exit(0)\n"
            "path.write_text(str(child))\n"
            "os._exit(0)\n",
            encoding="utf-8",
        )
        started = time.monotonic()
        completed = grok_execution._supervised_run(
            [sys.executable, str(script)],
            pathlib.Path("."),
            timeout=None,
            poll_interval=0.1,
        )
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 5)
        self.assertEqual(completed.returncode, 0)
        descendant = int(descendant_path.read_text(encoding="utf-8"))
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and pathlib.Path(f"/proc/{descendant}").exists():
            time.sleep(0.05)
        self.assertFalse(pathlib.Path(f"/proc/{descendant}").exists())

    def test_explicit_timeout_kills_full_dedicated_group(self) -> None:
        work = pathlib.Path(tempfile.mkdtemp())
        descendant_path = work / "descendant.pid"
        script = work / "timeout_group.py"
        script.write_text(
            "import os, pathlib, time\n"
            f"path = pathlib.Path({str(descendant_path)!r})\n"
            "child = os.fork()\n"
            "if child == 0:\n"
            "    time.sleep(30)\n"
            "    os._exit(0)\n"
            "path.write_text(str(child))\n"
            "time.sleep(30)\n",
            encoding="utf-8",
        )
        started = time.monotonic()
        with self.assertRaisesRegex(grok_execution.BridgeError, "timeout"):
            grok_execution._supervised_run(
                [sys.executable, str(script)],
                pathlib.Path("."),
                timeout=4,
                poll_interval=0.1,
            )
        self.assertLess(time.monotonic() - started, 8)
        descendant = int(descendant_path.read_text(encoding="utf-8"))
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and pathlib.Path(f"/proc/{descendant}").exists():
            time.sleep(0.05)
        self.assertFalse(pathlib.Path(f"/proc/{descendant}").exists())

    def test_direct_child_exit_after_descendant_closes_stdio_kills_descendant(self) -> None:
        work = pathlib.Path(tempfile.mkdtemp())
        descendant_path = work / "descendant.pid"
        script = work / "closed_stdio.py"
        script.write_text(
            "import os, pathlib, sys, time\n"
            f"path = pathlib.Path({str(descendant_path)!r})\n"
            "child = os.fork()\n"
            "if child == 0:\n"
            "    os.close(1)\n"
            "    os.close(2)\n"
            "    time.sleep(30)\n"
            "    os._exit(0)\n"
            "path.write_text(str(child))\n"
            "sys.stdout.write('parent-out')\n"
            "sys.stderr.write('parent-err')\n"
            "sys.stdout.flush()\n"
            "sys.stderr.flush()\n",
            encoding="utf-8",
        )
        started = time.monotonic()
        completed = grok_execution._supervised_run(
            [sys.executable, str(script)],
            pathlib.Path("."),
            timeout=None,
            poll_interval=0.1,
        )
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 5)
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "parent-out")
        self.assertEqual(completed.stderr, "parent-err")
        descendant = int(descendant_path.read_text(encoding="utf-8"))
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and pathlib.Path(f"/proc/{descendant}").exists():
            time.sleep(0.05)
        self.assertFalse(pathlib.Path(f"/proc/{descendant}").exists())

    def test_injected_interruption_kills_live_group_and_propagates(self) -> None:
        work = pathlib.Path(tempfile.mkdtemp())
        descendant_path = work / "descendant.pid"
        script = work / "interrupt_group.py"
        script.write_text(
            "import os, pathlib, time\n"
            f"path = pathlib.Path({str(descendant_path)!r})\n"
            "child = os.fork()\n"
            "if child == 0:\n"
            "    time.sleep(30)\n"
            "    os._exit(0)\n"
            "path.write_text(str(child))\n"
            "time.sleep(30)\n",
            encoding="utf-8",
        )

        def boom(_seconds: float) -> None:
            wait_until = time.monotonic() + 5
            while time.monotonic() < wait_until:
                if descendant_path.exists() and descendant_path.read_text(encoding="utf-8").strip():
                    break
                time.sleep(0.05)
            raise KeyboardInterrupt()

        with self.assertRaises(KeyboardInterrupt):
            grok_execution._supervised_run(
                [sys.executable, str(script)],
                pathlib.Path("."),
                timeout=None,
                poll_interval=0.1,
                sleep=boom,
            )
        deadline = time.monotonic() + 5
        descendant = None
        while time.monotonic() < deadline:
            if descendant_path.exists():
                raw = descendant_path.read_text(encoding="utf-8").strip()
                if raw:
                    descendant = int(raw)
                    break
            time.sleep(0.05)
        self.assertIsNotNone(descendant)
        gone_deadline = time.monotonic() + 2
        while time.monotonic() < gone_deadline and pathlib.Path(f"/proc/{descendant}").exists():
            time.sleep(0.05)
        self.assertFalse(pathlib.Path(f"/proc/{descendant}").exists())

    def test_direct_child_exit_with_stuck_pipes_does_not_unbounded_communicate(self) -> None:
        class StickyProc:
            def __init__(self) -> None:
                self.pid = 4242
                self._v23_pgid = 4242
                self.returncode = 0
                self.killed = False

            def poll(self) -> int:
                return 0

            def kill(self) -> None:
                self.killed = True

            def communicate(self, timeout: float | None = None) -> tuple[str, str]:
                if self.killed:
                    return "drained", ""
                raise subprocess.TimeoutExpired(["grok"], timeout)

        completed = grok_execution._supervised_run(
            ["grok"],
            pathlib.Path("."),
            timeout=None,
            spawn=lambda _command, _cwd: StickyProc(),
            is_alive=lambda _proc: False,
            is_zombie=lambda _proc: False,
            sleep=lambda _seconds: None,
            clock=lambda: 0.0,
        )
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "drained")

    def test_low_is_the_default_effort(self) -> None:
        args = grok_execution.parse_args(
            ["run", "--prompt", "bounded task", "--task-id", "one", "--owned-path", "file.txt"]
        )
        self.assertEqual(args.effort, "low")

        batch = grok_execution._batch_task(
            {"id": "one", "cwd": ".", "prompt": "task", "owned_paths": ["file.txt"]}
        )
        self.assertEqual(batch.effort, "low")
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            grok_execution.parse_args(
                [
                    "run",
                    "--prompt",
                    "bounded task",
                    "--task-id",
                    "one",
                    "--owned-path",
                    "file.txt",
                    "--effort",
                    "medium",
                ]
            )
        with self.assertRaises(grok_execution.BridgeError):
            grok_execution._batch_task(
                {
                    "id": "high",
                    "cwd": ".",
                    "prompt": "task",
                    "owned_paths": ["file.txt"],
                    "effort": "high",
                }
            )

    def test_run_and_resume_require_task_id_and_owned_path(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                grok_execution.parse_args(["run", "--prompt", "task"])
            with self.assertRaises(SystemExit):
                grok_execution.parse_args(["run", "--prompt", "task", "--task-id", "one"])
            with self.assertRaises(SystemExit):
                grok_execution.parse_args(["run", "--prompt", "task", "--owned-path", "a.txt"])
            with self.assertRaises(SystemExit):
                grok_execution.parse_args(
                    ["resume", "--prompt", "task", "--session", "s", "--receipt", "r.json"]
                )
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(grok_execution.BridgeError, "task-id"):
                grok_execution._run(_run_args(directory, task_id=""))
            with self.assertRaisesRegex(grok_execution.BridgeError, "owned-path"):
                grok_execution._run(_run_args(directory, owned_path=[]))

    def test_only_explicit_usage_exhaustion_authorizes_fallback(self) -> None:
        for detail in (
            "quota exhausted",
            "insufficient_quota",
            "weekly limit reached",
            "out of credits",
        ):
            self.assertTrue(grok_execution._is_quota_exhaustion(detail), detail)
        for detail in (
            "HTTP 429 transient rate limit",
            "authentication failed",
            "network timeout",
            "model unavailable",
        ):
            self.assertFalse(grok_execution._is_quota_exhaustion(detail), detail)

    def test_quota_receipt_is_bound_and_distinct_from_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cwd = pathlib.Path(directory).resolve()
            owned = [str(cwd / "owned.txt")]
            receipt = grok_execution._quota_receipt(
                cwd=cwd, task_id="quota-test", owned_paths=owned
            )
            output = io.StringIO()
            with (
                mock.patch.object(
                    grok_execution,
                    "_run",
                    side_effect=grok_execution.QuotaExhausted("quota", receipt),
                ),
                contextlib.redirect_stdout(output),
            ):
                status = grok_execution.main(
                    [
                        "run",
                        "--prompt",
                        "task",
                        "--task-id",
                        "quota-test",
                        "--owned-path",
                        "owned.txt",
                    ]
                )
            printed = json.loads(output.getvalue())
            self.assertEqual(status, 2)
            self.assertEqual(printed["status"], "QUOTA_EXHAUSTED")
            self.assertEqual(printed["fallback_reason"], "grok_quota_exhausted")
            self.assertEqual(printed["requested_model"], "grok-4.6")
            self.assertEqual(printed["task_id"], "quota-test")
            self.assertEqual(printed["working_directory"], str(cwd))
            self.assertEqual(printed["owned_paths"], owned)
            self.assertNotIn("actual_model", printed)

    def test_run_classifies_only_explicit_quota_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            args = _run_args(directory)
            quota = subprocess.CompletedProcess([], 1, "", "insufficient_quota")
            transient = subprocess.CompletedProcess([], 1, "", "HTTP 429 transient rate limit")
            with mock.patch.object(grok_execution, "_grok_binary", return_value="/bin/true"):
                with (
                    mock.patch.object(grok_execution, "_supervised_run", return_value=quota),
                    self.assertRaises(grok_execution.QuotaExhausted) as raised_quota,
                ):
                    grok_execution._run(args)
                bound = raised_quota.exception.receipt
                self.assertEqual(bound["task_id"], "quota-test")
                self.assertEqual(bound["working_directory"], str(pathlib.Path(directory).resolve()))
                self.assertEqual(
                    bound["owned_paths"],
                    [str(pathlib.Path(directory).resolve() / "owned.txt")],
                )
                self.assertEqual(bound["fallback_reason"], "grok_quota_exhausted")
                with (
                    mock.patch.object(grok_execution, "_supervised_run", return_value=transient),
                    self.assertRaises(grok_execution.BridgeError) as raised,
                ):
                    grok_execution._run(args)
                self.assertNotIsInstance(raised.exception, grok_execution.QuotaExhausted)

    def test_prompt_stays_off_argv_and_file_is_mode_0600_during_subprocess(self) -> None:
        observed: dict[str, object] = {}

        def fake_run(command, _cwd=None, **_kwargs):
            prompt_file = pathlib.Path(command[command.index("--prompt-file") + 1])
            observed["argv"] = list(command)
            observed["mode"] = stat_mode(prompt_file)
            observed["content"] = prompt_file.read_text(encoding="utf-8")
            observed["path"] = prompt_file
            return subprocess.CompletedProcess(
                command,
                0,
                json.dumps(
                    {
                        "sessionId": "sess-1",
                        "stopReason": "end_turn",
                        "modelUsage": {"grok-4.6-build": {"modelCalls": 1}},
                        "text": "ok",
                    }
                ),
                "",
            )

        with tempfile.TemporaryDirectory() as directory:
            with (
                mock.patch.object(grok_execution, "_grok_binary", return_value="/bin/true"),
                mock.patch.object(grok_execution, "_supervised_run", side_effect=fake_run),
            ):
                receipt = grok_execution._run(_run_args(directory))
            argv = observed["argv"]
            assert isinstance(argv, list)
            joined = " ".join(str(part) for part in argv)
            self.assertNotIn(SECRET_PROMPT, joined)
            self.assertNotIn("--single", argv)
            self.assertIn("--prompt-file", argv)
            self.assertEqual(observed["mode"], 0o600)
            content = observed["content"]
            assert isinstance(content, str)
            self.assertIn(SECRET_PROMPT, content)
            self.assertIn("Task ID: quota-test", content)
            path = observed["path"]
            assert isinstance(path, pathlib.Path)
            self.assertFalse(path.exists())
            self.assertEqual(receipt["conversation_id"], "sess-1")
            self.assertEqual(
                receipt["owned_paths"],
                [str(pathlib.Path(directory).resolve() / "owned.txt")],
            )

    def test_resume_receipt_uses_exact_absolute_owned_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cwd = pathlib.Path(directory).resolve()
            owned = [str(cwd / "owned.txt")]
            receipt_path = cwd / "receipt.json"
            receipt_path.write_text(
                json.dumps(
                    {
                        "schema": grok_execution.SCHEMA,
                        "status": "SUCCESS",
                        "conversation_id": "sess-1",
                        "working_directory": str(cwd),
                        "task_id": "quota-test",
                        "owned_paths": owned,
                        "requested_model": grok_execution.REQUESTED_MODEL,
                        "actual_model": grok_execution.ACTUAL_MODEL,
                    }
                ),
                encoding="utf-8",
            )

            def fake_run(command, _cwd=None, **_kwargs):
                return subprocess.CompletedProcess(
                    command,
                    0,
                    json.dumps(
                        {
                            "sessionId": "sess-1",
                            "stopReason": "end_turn",
                            "modelUsage": {"grok-4.6-build": {"modelCalls": 1}},
                            "text": "ok",
                        }
                    ),
                    "",
                )

            with (
                mock.patch.object(grok_execution, "_grok_binary", return_value="/bin/true"),
                mock.patch.object(grok_execution, "_supervised_run", side_effect=fake_run),
            ):
                receipt = grok_execution._run(
                    _run_args(directory, session="sess-1", receipt=str(receipt_path))
                )
            self.assertEqual(receipt["owned_paths"], owned)
            self.assertTrue(
                all(pathlib.Path(path).is_absolute() for path in receipt["owned_paths"])
            )
            self.assertTrue(receipt["continued"])

    def test_prompt_file_is_deleted_after_subprocess_failure(self) -> None:
        observed: dict[str, pathlib.Path] = {}

        def fake_run(command, _cwd=None, **_kwargs):
            observed["path"] = pathlib.Path(command[command.index("--prompt-file") + 1])
            raise OSError("cli crashed")

        with tempfile.TemporaryDirectory() as directory:
            with (
                mock.patch.object(grok_execution, "_grok_binary", return_value="/bin/true"),
                mock.patch.object(grok_execution, "_supervised_run", side_effect=fake_run),
                self.assertRaises(OSError),
            ):
                grok_execution._run(_run_args(directory))
            self.assertFalse(observed["path"].exists())

    def test_batch_quota_receipt_uses_same_bindings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cwd = pathlib.Path(directory).resolve()
            owned = [str(cwd / "owned.txt")]
            receipt = grok_execution._quota_receipt(cwd=cwd, task_id="batch-one", owned_paths=owned)
            task = grok_execution._batch_task(
                {
                    "id": "batch-one",
                    "cwd": str(cwd),
                    "prompt": "task",
                    "owned_paths": owned,
                }
            )
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
                json.dump({"tasks": []}, handle)
                manifest = handle.name
            try:
                with (
                    mock.patch.object(
                        grok_execution,
                        "_batch_task",
                        return_value=task,
                    ),
                    mock.patch.object(
                        grok_execution,
                        "_run",
                        side_effect=grok_execution.QuotaExhausted("quota", receipt),
                    ),
                    mock.patch.object(grok_execution, "_assert_exclusive"),
                ):
                    pathlib.Path(manifest).write_text(
                        json.dumps(
                            {
                                "tasks": [
                                    {
                                        "id": "batch-one",
                                        "cwd": str(cwd),
                                        "prompt": "task",
                                        "owned_paths": owned,
                                    }
                                ]
                            }
                        ),
                        encoding="utf-8",
                    )
                    result = grok_execution._batch(
                        argparse.Namespace(manifest=manifest, max_parallel=1)
                    )
            finally:
                pathlib.Path(manifest).unlink(missing_ok=True)
            bound = result["receipts"][0]
            self.assertEqual(bound["status"], "QUOTA_EXHAUSTED")
            self.assertEqual(bound["task_id"], "batch-one")
            self.assertEqual(bound["working_directory"], str(cwd))
            self.assertEqual(bound["owned_paths"], owned)
            self.assertEqual(bound["fallback_reason"], "grok_quota_exhausted")
            self.assertEqual(bound["requested_model"], "grok-4.6")

    def test_supervised_run_fails_closed_without_validated_pgid(self) -> None:
        class InvalidProc:
            def __init__(self) -> None:
                self.pid = 1
                self._v23_pgid = None
                self.killed = False
                self.returncode = None

            def poll(self) -> None:
                return None

            def kill(self) -> None:
                self.killed = True

            def communicate(self, timeout: float | None = None) -> tuple[str, str]:
                return "", ""

        with self.assertRaisesRegex(grok_execution.BridgeError, "process group validation"):
            grok_execution._supervised_run(
                ["grok"],
                pathlib.Path("."),
                timeout=None,
                spawn=lambda _command, _cwd: InvalidProc(),
            )

    def test_registry_full_after_spawn_kills_group_and_does_not_unregister_dummy_slots(
        self,
    ) -> None:
        work = pathlib.Path(tempfile.mkdtemp())
        descendant = work / "descendants.txt"
        script = work / "registry-full.py"
        script.write_text(_sleep_group_script(descendant), encoding="utf-8")
        captured: dict[str, Any] = {}
        previous = list(grok_execution._REGISTERED_PGIDS)
        grok_execution._REGISTERED_PGIDS[:] = [
            20_000 + index for index in range(grok_execution._SIGNAL_PGID_SLOTS)
        ]
        grok_execution._TERMINATING = False
        grok_execution._SPAWN_BOUNDARY_HOOK = None

        def spawn(command: list[str], cwd: pathlib.Path) -> subprocess.Popen[str]:
            proc = grok_execution._spawn_grok(command, cwd)
            captured["proc"] = proc
            return proc

        try:
            with self.assertRaisesRegex(grok_execution.BridgeError, "registry is full"):
                grok_execution._supervised_run(
                    [sys.executable, str(script)],
                    pathlib.Path("."),
                    timeout=None,
                    spawn=spawn,
                    poll_interval=0.05,
                )
            proc = captured["proc"]
            self._assert_spawned_group_gone(proc, descendant)
            self.assertEqual(
                list(grok_execution._REGISTERED_PGIDS),
                [20_000 + index for index in range(grok_execution._SIGNAL_PGID_SLOTS)],
            )
            spawned_pgid = getattr(proc, "_v23_pgid", None)
            self.assertNotIn(spawned_pgid, grok_execution._REGISTERED_PGIDS)
        finally:
            grok_execution._REGISTERED_PGIDS[:] = previous
            grok_execution._SPAWN_BOUNDARY_HOOK = None
            grok_execution._TERMINATING = False

    def test_boundary_hook_throw_after_spawn_kills_group_without_registering(self) -> None:
        work = pathlib.Path(tempfile.mkdtemp())
        descendant = work / "descendants.txt"
        script = work / "boundary-throw.py"
        script.write_text(_sleep_group_script(descendant), encoding="utf-8")
        captured: dict[str, Any] = {}
        previous = list(grok_execution._REGISTERED_PGIDS)
        grok_execution._REGISTERED_PGIDS[:] = [0] * grok_execution._SIGNAL_PGID_SLOTS
        grok_execution._TERMINATING = False

        def spawn(command: list[str], cwd: pathlib.Path) -> subprocess.Popen[str]:
            proc = grok_execution._spawn_grok(command, cwd)
            captured["proc"] = proc
            return proc

        def boom() -> None:
            raise RuntimeError("boundary-hook-throw")

        grok_execution._SPAWN_BOUNDARY_HOOK = boom
        try:
            with self.assertRaisesRegex(RuntimeError, "boundary-hook-throw"):
                grok_execution._supervised_run(
                    [sys.executable, str(script)],
                    pathlib.Path("."),
                    timeout=None,
                    spawn=spawn,
                    poll_interval=0.05,
                )
            proc = captured["proc"]
            self._assert_spawned_group_gone(proc, descendant)
            self.assertEqual(
                list(grok_execution._REGISTERED_PGIDS),
                [0] * grok_execution._SIGNAL_PGID_SLOTS,
            )
        finally:
            grok_execution._SPAWN_BOUNDARY_HOOK = None
            grok_execution._REGISTERED_PGIDS[:] = previous
            grok_execution._TERMINATING = False

    def test_validation_failure_after_descendant_fork_kills_spawn_token_group(self) -> None:
        work = pathlib.Path(tempfile.mkdtemp())
        descendant = work / "descendants.txt"
        script = work / "validation-fail.py"
        script.write_text(_sleep_group_script(descendant), encoding="utf-8")
        captured: dict[str, Any] = {}
        previous = list(grok_execution._REGISTERED_PGIDS)
        grok_execution._REGISTERED_PGIDS[:] = [0] * grok_execution._SIGNAL_PGID_SLOTS
        grok_execution._TERMINATING = False
        grok_execution._SPAWN_BOUNDARY_HOOK = None
        fail_getpgid = False
        real_getpgid = grok_execution.os.getpgid

        def getpgid(pid: int) -> int:
            if fail_getpgid:
                raise OSError("unavailable")
            return real_getpgid(pid)

        def parse_two_positive_pids(raw: str) -> list[int]:
            parsed: list[int] = []
            for item in raw.split():
                try:
                    value = int(item)
                except ValueError:
                    continue
                if value > 0:
                    parsed.append(value)
            if len(parsed) == 2:
                return parsed
            return []

        def spawn(command: list[str], cwd: pathlib.Path) -> subprocess.Popen[str]:
            nonlocal fail_getpgid
            proc = grok_execution._spawn_grok(command, cwd)
            captured["proc"] = proc
            deadline = time.monotonic() + 5
            group_pids: list[int] = []
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    break
                try:
                    raw = descendant.read_text(encoding="utf-8")
                except OSError:
                    time.sleep(0.05)
                    continue
                group_pids = parse_two_positive_pids(raw)
                if group_pids:
                    break
                time.sleep(0.05)
            self.assertEqual(
                len(group_pids), 2, "marker never contained two parseable positive PIDs"
            )
            captured["group_pids"] = list(group_pids)
            token = getattr(proc, "_v23_cleanup_token", None)
            self.assertIsInstance(token, grok_execution._SpawnCleanupToken)
            self.assertEqual(token._candidate_pgid, proc.pid)
            fail_getpgid = True
            proc._v23_pgid = None
            return proc

        try:
            with (
                mock.patch.object(grok_execution.os, "getpgid", getpgid),
                self.assertRaisesRegex(grok_execution.BridgeError, "process group validation"),
            ):
                grok_execution._supervised_run(
                    [sys.executable, str(script)],
                    pathlib.Path("."),
                    timeout=None,
                    spawn=spawn,
                    poll_interval=0.05,
                )
            proc = captured["proc"]
            group_pids = captured["group_pids"]
            self.assertEqual(len(group_pids), 2)
            self._assert_spawned_group_gone(proc, descendant)
            for candidate in group_pids:
                self.assertFalse(pathlib.Path(f"/proc/{candidate}").exists())
            with self.assertRaises(OSError):
                os.killpg(proc.pid, 0)
            self.assertEqual(
                list(grok_execution._REGISTERED_PGIDS),
                [0] * grok_execution._SIGNAL_PGID_SLOTS,
            )
            self.assertNotIn(proc.pid, grok_execution._REGISTERED_PGIDS)
        finally:
            grok_execution._SPAWN_BOUNDARY_HOOK = None
            grok_execution._REGISTERED_PGIDS[:] = previous
            grok_execution._TERMINATING = False
            leftover = captured.get("proc")
            if leftover is not None and leftover.poll() is None:
                grok_execution._kill_spawned_group(leftover)
                grok_execution._bounded_reap(leftover)

    def _assert_spawned_group_gone(self, proc: Any, descendant: pathlib.Path) -> None:
        pid = getattr(proc, "pid", None)
        pgid = getattr(proc, "_v23_pgid", None)
        extra: list[int] = []
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            extra = []
            if descendant.exists():
                extra = [
                    int(item) for item in descendant.read_text(encoding="utf-8").split() if item
                ]
            live = False
            if proc.poll() is None:
                live = True
            for candidate in [pid, pgid, *extra]:
                if (
                    type(candidate) is int
                    and candidate > 1
                    and pathlib.Path(f"/proc/{candidate}").exists()
                ):
                    live = True
                    break
            if type(pgid) is int and pgid > 1:
                try:
                    os.killpg(pgid, 0)
                    live = True
                except OSError:
                    pass
            if not live:
                break
            time.sleep(0.05)
        self.assertIsNotNone(proc.poll())
        if type(pid) is int:
            self.assertFalse(pathlib.Path(f"/proc/{pid}").exists())
        if type(pgid) is int and pgid > 1:
            with self.assertRaises(OSError):
                os.killpg(pgid, 0)
        for candidate in extra:
            self.assertFalse(pathlib.Path(f"/proc/{candidate}").exists())

    def test_record_dedicated_pgid_fails_closed_when_getpgid_unavailable(self) -> None:
        class Proc:
            pid = 12345

        proc = Proc()
        with mock.patch.object(grok_execution.os, "getpgid", side_effect=OSError("unavailable")):
            grok_execution._record_dedicated_pgid(proc)
        self.assertIsNone(proc._v23_pgid)
        self.assertFalse(grok_execution._owns_dedicated_group(proc))

    def test_main_installs_termination_handlers_before_run(self) -> None:
        blocked: list[object] = []

        def fake_mask(how: int, mask: object) -> set[int]:
            blocked.append((how, set(mask)))
            return set()

        with (
            mock.patch.object(grok_execution.signal, "pthread_sigmask", fake_mask),
            mock.patch.object(grok_execution.threading, "Thread") as thread_cls,
            mock.patch.object(grok_execution, "_run", return_value={"status": "SUCCESS"}),
        ):
            grok_execution._termination_handlers_installed = False
            with contextlib.redirect_stdout(io.StringIO()):
                grok_execution.main(
                    ["run", "--prompt", "x", "--task-id", "one", "--owned-path", "file.txt"]
                )
        self.assertEqual(blocked[0][0], signal.SIG_BLOCK)
        self.assertEqual(blocked[0][1], set(grok_execution._TERMINATION_SIGNALS))
        thread_cls.assert_called_once()
        grok_execution._termination_handlers_installed = False

    def test_main_installs_termination_handlers_before_batch(self) -> None:
        blocked: list[object] = []

        def fake_mask(how: int, mask: object) -> set[int]:
            blocked.append((how, set(mask)))
            return set()

        with (
            mock.patch.object(grok_execution.signal, "pthread_sigmask", fake_mask),
            mock.patch.object(grok_execution.threading, "Thread") as thread_cls,
            mock.patch.object(grok_execution, "_batch", return_value={"status": "SUCCESS"}),
        ):
            grok_execution._termination_handlers_installed = False
            with contextlib.redirect_stdout(io.StringIO()):
                grok_execution.main(["batch", "--manifest", "tasks.json"])
        self.assertEqual(blocked[0][0], signal.SIG_BLOCK)
        self.assertEqual(blocked[0][1], set(grok_execution._TERMINATION_SIGNALS))
        thread_cls.assert_called_once()
        grok_execution._termination_handlers_installed = False

    def test_sigterm_kills_supervised_child_group(self) -> None:
        self._assert_signal_helper_kills_children("supervised", expected_children=2)

    def test_sigterm_kills_batch_registered_groups(self) -> None:
        self._assert_signal_helper_kills_children("batch", expected_children=4)

    def test_sigterm_at_spawn_registration_boundary_kills_child(self) -> None:
        self._assert_signal_helper_kills_children("boundary", expected_children=2)

    def test_concurrent_registration_does_not_overwrite_slots(self) -> None:
        grok_execution._REGISTERED_PGIDS[:] = [0] * grok_execution._SIGNAL_PGID_SLOTS
        grok_execution._TERMINATING = False
        errors: list[BaseException] = []
        barrier = threading.Barrier(grok_execution._SIGNAL_PGID_SLOTS)

        def worker(pgid: int) -> None:
            try:
                barrier.wait(timeout=2)
                grok_execution._register_dedicated_pgid(pgid)
            except grok_execution.BridgeError as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=worker, args=(10_000 + index,))
            for index in range(grok_execution._SIGNAL_PGID_SLOTS)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
        try:
            self.assertEqual(errors, [])
            registered = [pgid for pgid in grok_execution._REGISTERED_PGIDS if pgid]
            self.assertEqual(sorted(registered), list(range(10_000, 10_008)))
        finally:
            grok_execution._REGISTERED_PGIDS[:] = [0] * grok_execution._SIGNAL_PGID_SLOTS

    def test_child_launcher_rejects_empty_or_flag_targets(self) -> None:
        with self.assertRaisesRegex(grok_execution.BridgeError, "empty"):
            grok_execution._validate_child_launcher_target([])
        with self.assertRaisesRegex(grok_execution.BridgeError, "invalid"):
            grok_execution._validate_child_launcher_target(["-c"])
        with self.assertRaisesRegex(grok_execution.BridgeError, "not an executable"):
            grok_execution._validate_child_launcher_target(["/no/such/grok-bin"])

    def test_child_unblocks_inherited_termination_mask(self) -> None:
        script = (
            "import signal, sys\n"
            "blocked = signal.pthread_sigmask(signal.SIG_BLOCK, [])\n"
            "sys.stdout.write(' '.join(str(item) for item in sorted(blocked)))\n"
        )
        previous = signal.pthread_sigmask(signal.SIG_BLOCK, grok_execution._TERMINATION_SIGNALS)
        try:
            proc = grok_execution._spawn_grok([sys.executable, "-c", script], pathlib.Path("."))
            try:
                stdout, stderr = proc.communicate(timeout=5)
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=5)
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous)
        self.assertEqual(proc.returncode, 0, msg=stderr)
        blocked = {int(item) for item in stdout.split() if item}
        self.assertTrue(
            set(grok_execution._TERMINATION_SIGNALS).isdisjoint(blocked),
            msg=stdout,
        )

    def test_child_exec_restores_sigpipe_and_sigxfsz_to_dfl(self) -> None:
        names = [
            name for name in ("SIGPIPE", "SIGXFSZ") if isinstance(getattr(signal, name, None), int)
        ]
        self.assertIn("SIGPIPE", names)
        previous = {name: signal.getsignal(getattr(signal, name)) for name in names}
        sleep_bin = shutil.which("sleep")
        self.assertIsNotNone(sleep_bin)
        assert sleep_bin is not None
        try:
            for name in names:
                signal.signal(getattr(signal, name), signal.SIG_IGN)
            proc = grok_execution._spawn_grok([sleep_bin, "5"], pathlib.Path("."))
            try:
                status = self._wait_exec_status(proc, "sleep")
                ignored = int(status["SigIgn"], 16)
                for name in names:
                    signum = getattr(signal, name)
                    self.assertEqual(
                        ignored & (1 << (signum - 1)),
                        0,
                        msg=f"{name} still ignored after exec: {status['SigIgn']}",
                    )
            finally:
                grok_execution._stop_group_and_reap(proc)
        finally:
            for name, handler in previous.items():
                signal.signal(getattr(signal, name), handler)

    def _wait_exec_status(self, proc: Any, comm: str) -> dict[str, str]:
        deadline = time.monotonic() + 5
        last = ""
        while time.monotonic() < deadline:
            path = pathlib.Path(f"/proc/{proc.pid}/status")
            try:
                last = path.read_text(encoding="utf-8")
            except OSError:
                time.sleep(0.01)
                continue
            fields = {}
            for line in last.splitlines():
                if ":" in line:
                    key, value = line.split(":", 1)
                    fields[key] = value.strip()
            if fields.get("Name") == comm and "SigIgn" in fields:
                return fields
            time.sleep(0.01)
        self.fail(f"did not observe exec of {comm}: {last}")

    def test_child_reset_uses_getattr_for_optional_restore_signals(self) -> None:
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

        with mock.patch.object(grok_execution, "signal", MissingXfsz):
            grok_execution._child_reset_inherited_signal_mask()
        self.assertIn(signal.SIGPIPE, seen)
        self.assertNotIn(signal.SIGXFSZ, seen)

    def _assert_signal_helper_kills_children(self, mode: str, expected_children: int) -> None:
        work = pathlib.Path(tempfile.mkdtemp())
        ready = work / "ready.txt"
        descendant = work / "descendants.txt"
        env = os.environ.copy()
        env["V23_GROK_SIGNAL_HELPER"] = mode
        env["V23_GROK_SIGNAL_READY"] = str(ready)
        env["V23_GROK_SIGNAL_DESCENDANT"] = str(descendant)
        env["PYTHONPATH"] = os.pathsep.join(
            [str(pathlib.Path(__file__).resolve().parents[1]), env.get("PYTHONPATH", "")]
        )
        stderr_path = work / "helper.stderr"
        with stderr_path.open("w", encoding="utf-8") as stderr_handle:
            helper = subprocess.Popen(
                [sys.executable, str(pathlib.Path(__file__).resolve())],
                cwd=str(pathlib.Path(__file__).resolve().parents[1]),
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=stderr_handle,
            )
        try:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and not descendant.exists():
                if helper.poll() is not None:
                    break
                time.sleep(0.05)
            self.assertTrue(
                descendant.exists(),
                msg=stderr_path.read_text(encoding="utf-8") if stderr_path.exists() else "",
            )
            children = [
                int(item) for item in descendant.read_text(encoding="utf-8").split() if item
            ]
            self.assertEqual(len(children), expected_children)
            os.kill(helper.pid, signal.SIGTERM)
            helper.wait(timeout=8)
            self.assertIn(helper.returncode, {-signal.SIGTERM, 128 + signal.SIGTERM})
            gone_deadline = time.monotonic() + 2
            while time.monotonic() < gone_deadline and any(
                pathlib.Path(f"/proc/{pid}").exists() for pid in children
            ):
                time.sleep(0.05)
            for pid in children:
                self.assertFalse(pathlib.Path(f"/proc/{pid}").exists())
        finally:
            if helper.poll() is None:
                helper.kill()
                helper.wait(timeout=5)


def _sleep_group_script(descendant_path: pathlib.Path) -> str:
    return (
        "import os, pathlib, time\n"
        f"path = pathlib.Path({str(descendant_path)!r})\n"
        "child = os.fork()\n"
        "if child == 0:\n"
        "    time.sleep(30)\n"
        "    os._exit(0)\n"
        "path.write_text(str(os.getpid()) + ' ' + str(child))\n"
        "time.sleep(30)\n"
    )


def _run_signal_helper() -> None:
    mode = os.environ["V23_GROK_SIGNAL_HELPER"]
    ready = pathlib.Path(os.environ["V23_GROK_SIGNAL_READY"])
    descendant = pathlib.Path(os.environ["V23_GROK_SIGNAL_DESCENDANT"])
    grok_execution.install_termination_handlers()
    work = descendant.parent
    if mode == "boundary":
        script = work / "boundary.py"
        script.write_text(_sleep_group_script(descendant), encoding="utf-8")

        def hold() -> None:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not descendant.exists():
                time.sleep(0.01)
            ready.write_text(str(os.getpid()), encoding="utf-8")
            time.sleep(2)

        grok_execution._SPAWN_BOUNDARY_HOOK = hold
        grok_execution._supervised_run(
            [sys.executable, str(script)],
            pathlib.Path("."),
            timeout=None,
            poll_interval=0.1,
        )
        return
    if mode == "batch":
        scripts = []
        markers = []
        for index in range(2):
            marker = work / f"group-{index}.txt"
            script = work / f"batch-{index}.py"
            script.write_text(_sleep_group_script(marker), encoding="utf-8")
            scripts.append(script)
            markers.append(marker)

        def run_one(script: pathlib.Path) -> None:
            grok_execution._supervised_run(
                [sys.executable, str(script)],
                pathlib.Path("."),
                timeout=None,
                poll_interval=0.1,
            )

        threads = [
            threading.Thread(target=run_one, args=(script,), daemon=True) for script in scripts
        ]
        for thread in threads:
            thread.start()
        deadline = time.monotonic() + 5
        pids: list[str] = []
        while time.monotonic() < deadline and len(pids) < 4:
            pids = []
            for marker in markers:
                if marker.exists():
                    pids.extend(marker.read_text(encoding="utf-8").split())
            time.sleep(0.05)
        descendant.write_text(" ".join(pids), encoding="utf-8")
        ready.write_text(str(os.getpid()), encoding="utf-8")
        time.sleep(30)
        return
    script = work / "supervised.py"
    script.write_text(_sleep_group_script(descendant), encoding="utf-8")
    ready.write_text(str(os.getpid()), encoding="utf-8")
    grok_execution._supervised_run(
        [sys.executable, str(script)],
        pathlib.Path("."),
        timeout=None,
        poll_interval=0.1,
    )


def stat_mode(path: pathlib.Path) -> int:
    return os.stat(path).st_mode & 0o777


if __name__ == "__main__":
    if os.environ.get("V23_GROK_SIGNAL_HELPER"):
        _run_signal_helper()
        raise SystemExit(0)
    unittest.main()
