from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.executor_routing import (
    PERMIT_QUOTA,
    RECEIPT_SCHEMA,
    parse_policy,
    select_executor,
    validate_fallback_receipt,
)

ROOT = Path(__file__).resolve().parents[1]


def _write(text: str) -> Path:
    directory = Path(tempfile.mkdtemp())
    path = directory / "local.toml"
    path.write_text(text.strip() + "\n", encoding="utf-8")
    return path


NATIVE_ONLY = """
[models]
primary = "primary-model"
executor = "native-slug-future"
reviewer = "reviewer-model"

[routing]
selection = "native_only"

[[routing.executors]]
id = "native"
backend = "codex"
capabilities = ["implementation", "tests", "git", "local_write"]
tools = ["workspace-write"]
cost_preference = "unknown"
availability = "configured"
"""

PAID_BOTH = """
[models]
primary = "primary-model"
executor = "native-slug-future"
reviewer = "reviewer-model"

[routing]
selection = "paid_preferred"

[[routing.executors]]
id = "grok_build"
backend = "grok"
capabilities = ["implementation", "tests", "git", "local_write"]
tools = ["workspace-write"]
cost_preference = "paid_included"
availability = "configured"

[[routing.executors]]
id = "native"
backend = "codex"
capabilities = ["implementation", "tests", "git", "local_write"]
tools = ["workspace-write"]
cost_preference = "unknown"
availability = "configured"

[routing.fallback]
permit = ["quota_exhausted"]
target = "native"
"""

PAID_STRICT = PAID_BOTH.replace("paid_preferred", "paid_strict")

LEGACY = """
[models]
primary = "primary-model"
executor = "luna-low"
reviewer = "reviewer-model"
"""


class ExecutorRoutingTests(unittest.TestCase):
    def test_native_only_does_not_require_grok(self) -> None:
        policy = parse_policy(__import__("tomllib").loads(NATIVE_ONLY))
        self.assertFalse(policy.grok_required)
        self.assertFalse(policy.native_is_fallback)
        result = select_executor(
            policy, capabilities=("implementation",), tools=("workspace-write",)
        )
        self.assertEqual(result.status, "selected")
        self.assertEqual(result.backend, "codex")
        self.assertEqual(result.actual_model, "native-slug-future")
        self.assertIn("native_only", result.reason)

    def test_paid_preferred_selects_included_executor(self) -> None:
        policy = parse_policy(__import__("tomllib").loads(PAID_BOTH))
        result = select_executor(
            policy, capabilities=("implementation",), tools=("workspace-write",)
        )
        self.assertEqual(result.selected_id, "grok_build")
        self.assertEqual(result.actual_model, "grok-4.6-build")
        self.assertFalse(result.fallback_authorized)

    def test_capability_mismatch_is_actionable(self) -> None:
        policy = parse_policy(__import__("tomllib").loads(NATIVE_ONLY))
        result = select_executor(policy, capabilities=("vision",), tools=())
        self.assertEqual(result.status, "blocked")
        self.assertIn("capability mismatch", result.blocked or "")

    def test_unknown_backend_rejected_at_parse(self) -> None:
        text = PAID_BOTH.replace('backend = "grok"', 'backend = "anthropic"')
        with self.assertRaises(Exception) as raised:
            parse_policy(__import__("tomllib").loads(text))
        self.assertIn("backend", str(raised.exception))

    def test_unavailable_paid_executor_blocks_strict(self) -> None:
        text = PAID_STRICT.replace(
            'id = "grok_build"\nbackend = "grok"',
            'id = "grok_build"\nbackend = "grok"\n',
        ).replace(
            'cost_preference = "paid_included"\navailability = "configured"',
            'cost_preference = "paid_included"\navailability = "unavailable"',
            1,
        )
        policy = parse_policy(__import__("tomllib").loads(text))
        result = select_executor(
            policy, capabilities=("implementation",), tools=("workspace-write",)
        )
        self.assertEqual(result.status, "blocked")
        self.assertIn("paid_strict", result.blocked or "")

    def test_paid_preferred_can_use_native_when_paid_unavailable(self) -> None:
        text = PAID_BOTH.replace(
            'cost_preference = "paid_included"\navailability = "configured"',
            'cost_preference = "paid_included"\navailability = "unavailable"',
            1,
        )
        policy = parse_policy(__import__("tomllib").loads(text))
        self.assertFalse(policy.grok_required)
        self.assertFalse(policy.native_is_fallback)
        result = select_executor(
            policy, capabilities=("implementation",), tools=("workspace-write",)
        )
        self.assertEqual(result.selected_id, "native")
        self.assertEqual(result.status, "selected")
        self.assertFalse(result.fallback_authorized)
        self.assertEqual(result.invocation["kind"], "codex_agent")
        self.assertEqual(result.invocation["model"], "native-slug-future")

    def test_generic_failure_does_not_authorize_fallback(self) -> None:
        policy = parse_policy(__import__("tomllib").loads(LEGACY))
        for cause in ("network", "auth", "timeout", "bridge", "http_429"):
            result = select_executor(
                policy,
                capabilities=("implementation",),
                tools=("workspace-write",),
                failure_cause=cause,
            )
            self.assertEqual(result.status, "blocked")
            self.assertFalse(result.fallback_authorized)
            self.assertIn("not a quota receipt", result.blocked or "")

    def test_quota_receipt_binding_authorizes_legacy_fallback(self) -> None:
        policy = parse_policy(__import__("tomllib").loads(LEGACY))
        owned = ["/tmp/work/owned"]
        receipt = {
            "schema": RECEIPT_SCHEMA,
            "status": "QUOTA_EXHAUSTED",
            "fallback_reason": "grok_quota_exhausted",
            "task_id": "task-1",
            "working_directory": "/tmp/work",
            "owned_paths": owned,
            "requested_model": "grok-4.6",
            "actual_model": "grok-4.6-build",
        }
        ok = validate_fallback_receipt(
            policy,
            receipt,
            task_id="task-1",
            working_directory="/tmp/work",
            owned_paths=owned,
            capabilities=("implementation",),
            tools=("workspace-write",),
        )
        self.assertEqual(ok.status, "fallback")
        self.assertEqual(ok.selected_id, "native")
        self.assertTrue(ok.fallback_authorized)
        bad = validate_fallback_receipt(
            policy,
            receipt,
            task_id="other",
            working_directory="/tmp/work",
            owned_paths=owned,
        )
        self.assertEqual(bad.status, "blocked")
        self.assertIn("task_id", bad.blocked or "")

    def test_cause_string_does_not_authorize_quota_fallback(self) -> None:
        policy = parse_policy(__import__("tomllib").loads(LEGACY))
        result = select_executor(
            policy,
            capabilities=("implementation",),
            tools=("workspace-write",),
            failure_cause=PERMIT_QUOTA,
        )
        self.assertEqual(result.status, "blocked")
        self.assertFalse(result.fallback_authorized)
        self.assertIn("validate-receipt", result.blocked or "")

    def test_forbidden_fallback_stays_blocked(self) -> None:
        text = PAID_STRICT.replace(
            'permit = ["quota_exhausted"]\ntarget = "native"',
            'permit = []\ntarget = "native"',
        )
        policy = parse_policy(__import__("tomllib").loads(text))
        owned = ["/tmp/work/owned"]
        receipt = {
            "schema": RECEIPT_SCHEMA,
            "status": "QUOTA_EXHAUSTED",
            "fallback_reason": "grok_quota_exhausted",
            "task_id": "task-1",
            "working_directory": "/tmp/work",
            "owned_paths": owned,
            "requested_model": "grok-4.6",
        }
        result = validate_fallback_receipt(
            policy,
            receipt,
            task_id="task-1",
            working_directory="/tmp/work",
            owned_paths=owned,
            capabilities=("implementation",),
            tools=("workspace-write",),
        )
        self.assertEqual(result.status, "blocked")

    def test_future_native_slug_is_accepted(self) -> None:
        policy = parse_policy(__import__("tomllib").loads(NATIVE_ONLY))
        result = select_executor(
            policy, capabilities=("implementation",), tools=("workspace-write",)
        )
        self.assertEqual(result.actual_model, "native-slug-future")

    def test_read_only_does_not_require_executor(self) -> None:
        policy = parse_policy(__import__("tomllib").loads(NATIVE_ONLY))
        result = select_executor(policy, capabilities=(), tools=())
        self.assertEqual(result.status, "not_required")

    def test_example_and_legacy_cli_roundtrip(self) -> None:
        example = ROOT / "package/local.example.toml"
        completed_status = json.loads(
            __import__("subprocess")
            .run(
                [
                    "python",
                    str(ROOT / "scripts/executor_routing.py"),
                    "show-policy",
                    "--local-config",
                    str(example),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            .stdout
        )
        self.assertEqual(completed_status["selection"], "native_only")
        self.assertFalse(completed_status["grok_required"])

    def test_cli_cause_does_not_authorize_legacy_fallback(self) -> None:
        path = _write(LEGACY)
        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/executor_routing.py"),
                "select",
                "--local-config",
                str(path),
                "--capability",
                "implementation",
                "--tool",
                "workspace-write",
                "--cause",
                "quota_exhausted",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["status"], "blocked")
        self.assertFalse(payload["fallback_authorized"])

    def test_cli_malformed_receipt_is_structured(self) -> None:
        path = _write(LEGACY)
        receipt = path.parent / "receipt.json"
        receipt.write_text("{not-json", encoding="utf-8")
        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/executor_routing.py"),
                "validate-receipt",
                "--local-config",
                str(path),
                "--receipt",
                str(receipt),
                "--task-id",
                "task-1",
                "--cwd",
                "/tmp/work",
                "--owned-path",
                "/tmp/work/owned",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 2)
        payload = json.loads(completed.stderr)
        self.assertEqual(payload["status"], "error")
        self.assertIn("malformed receipt JSON", payload["blocked"])

    def test_unsupported_grok_identity_rejected(self) -> None:
        text = PAID_BOTH.replace('backend = "grok"', 'backend = "grok"\nrequested_model = "grok-9"')
        with self.assertRaisesRegex(Exception, "unsupported Grok identity"):
            parse_policy(__import__("tomllib").loads(text))

    def test_receipt_model_mismatch_blocks(self) -> None:
        policy = parse_policy(__import__("tomllib").loads(LEGACY))
        owned = ["/tmp/work/owned"]
        receipt = {
            "schema": RECEIPT_SCHEMA,
            "status": "QUOTA_EXHAUSTED",
            "fallback_reason": "grok_quota_exhausted",
            "task_id": "task-1",
            "working_directory": "/tmp/work",
            "owned_paths": owned,
            "requested_model": "other",
        }
        result = validate_fallback_receipt(
            policy,
            receipt,
            task_id="task-1",
            working_directory="/tmp/work",
            owned_paths=owned,
            capabilities=("implementation",),
            tools=("workspace-write",),
        )
        self.assertEqual(result.status, "blocked")
        self.assertIn("requested_model", result.blocked or "")


if __name__ == "__main__":
    unittest.main()
