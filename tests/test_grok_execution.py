from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import pathlib
import subprocess
import tempfile
import unittest
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
        "timeout": 30,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


class GrokExecutionTests(unittest.TestCase):
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
            owned = ["owned.txt"]
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
                    mock.patch.object(grok_execution.subprocess, "run", return_value=quota),
                    self.assertRaises(grok_execution.QuotaExhausted) as raised_quota,
                ):
                    grok_execution._run(args)
                bound = raised_quota.exception.receipt
                self.assertEqual(bound["task_id"], "quota-test")
                self.assertEqual(bound["working_directory"], str(pathlib.Path(directory).resolve()))
                self.assertEqual(bound["owned_paths"], ["owned.txt"])
                self.assertEqual(bound["fallback_reason"], "grok_quota_exhausted")
                with (
                    mock.patch.object(grok_execution.subprocess, "run", return_value=transient),
                    self.assertRaises(grok_execution.BridgeError) as raised,
                ):
                    grok_execution._run(args)
                self.assertNotIsInstance(raised.exception, grok_execution.QuotaExhausted)

    def test_prompt_stays_off_argv_and_file_is_mode_0600_during_subprocess(self) -> None:
        observed: dict[str, object] = {}

        def fake_run(command, **_kwargs):
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
                mock.patch.object(grok_execution.subprocess, "run", side_effect=fake_run),
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

    def test_prompt_file_is_deleted_after_subprocess_failure(self) -> None:
        observed: dict[str, pathlib.Path] = {}

        def fake_run(command, **_kwargs):
            observed["path"] = pathlib.Path(command[command.index("--prompt-file") + 1])
            raise OSError("cli crashed")

        with tempfile.TemporaryDirectory() as directory:
            with (
                mock.patch.object(grok_execution, "_grok_binary", return_value="/bin/true"),
                mock.patch.object(grok_execution.subprocess, "run", side_effect=fake_run),
                self.assertRaises(OSError),
            ):
                grok_execution._run(_run_args(directory))
            self.assertFalse(observed["path"].exists())

    def test_batch_quota_receipt_uses_same_bindings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cwd = pathlib.Path(directory).resolve()
            owned = ["owned.txt"]
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


def stat_mode(path: pathlib.Path) -> int:
    return os.stat(path).st_mode & 0o777


if __name__ == "__main__":
    unittest.main()
