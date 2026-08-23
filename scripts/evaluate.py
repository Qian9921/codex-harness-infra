"""Run the small, deterministic V23 Harness scenario suite.

This is an evaluation command, not a second agent runtime.  It runs the
versioned, offline scenarios against the production helpers with controlled
temporary environments. ``--live`` adds a non-remote local smoke of the
installed Hook plus the checkout Doctor; it never writes GitHub state.
"""

from __future__ import annotations

try:
    from scripts.runtime import ensure_supported_python
except ModuleNotFoundError:  # Support the documented direct script entrypoint.
    from runtime import ensure_supported_python

ensure_supported_python(__file__)

import argparse
import hashlib
import json
import subprocess
import sys
import time
import tomllib
import unittest
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUPPORTED_SUITE_VERSION = 1
REQUIRED_FIELDS = frozenset({"id", "dimension", "prompt", "expected", "test"})
UNOBSERVED_METRICS = (
    "fresh-context Codex prompt-following pass@k",
    "token and inference-cost distribution",
    "disposable-repository GitHub PR to review to merge",
)


@dataclass(frozen=True)
class Scenario:
    """One versioned deterministic Harness scenario."""

    identifier: str
    dimension: str
    prompt: str
    expected: str
    test: str


@dataclass(frozen=True)
class ScenarioSuite:
    """A versioned suite with enough provenance to identify its source."""

    version: int
    path: Path
    content_sha256: str
    scenarios: list[Scenario]


def load_scenarios(path: Path) -> ScenarioSuite:
    """Load and validate the small, complete scenario denominator."""
    try:
        resolved = path.resolve(strict=True)
        content = resolved.read_bytes()
        data = tomllib.loads(content.decode("utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ValueError(f"scenario suite is unavailable: {error}") from error
    version = data.get("version")
    if version != SUPPORTED_SUITE_VERSION:
        raise ValueError(f"unsupported scenario suite version: {version!r}")
    entries = data.get("scenario")
    declared_count = data.get("count")
    if not isinstance(entries, list) or not isinstance(declared_count, int):
        raise TypeError("scenario suite requires integer count and [[scenario]] entries")
    if len(entries) != declared_count:
        raise ValueError(
            f"scenario count mismatch: declared {declared_count}, found {len(entries)}"
        )
    scenarios: list[Scenario] = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != REQUIRED_FIELDS:
            raise ValueError("every scenario must have exactly the required fields")
        if not all(
            isinstance(entry[field], str) and entry[field].strip() for field in REQUIRED_FIELDS
        ):
            raise ValueError("scenario fields must be non-empty strings")
        scenarios.append(
            Scenario(
                entry["id"], entry["dimension"], entry["prompt"], entry["expected"], entry["test"]
            )
        )
    identifiers = [scenario.identifier for scenario in scenarios]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("scenario identifiers must be unique")
    return ScenarioSuite(
        version=version,
        path=resolved,
        content_sha256=hashlib.sha256(content).hexdigest(),
        scenarios=scenarios,
    )


def _source_revision() -> dict[str, object]:
    """Identify the evaluated source without mislabeling an uncommitted tree."""
    completed = subprocess.run(
        ("git", "-C", str(ROOT), "rev-parse", "HEAD"),
        text=True,
        capture_output=True,
        check=False,
    )
    status = subprocess.run(
        ("git", "-C", str(ROOT), "status", "--porcelain"),
        text=True,
        capture_output=True,
        check=False,
    )
    return {
        "head": completed.stdout.strip() if completed.returncode == 0 else "unavailable",
        "worktree_clean": status.returncode == 0 and not status.stdout.strip(),
    }


def _test_problems(result: unittest.TestResult) -> tuple[str, str | None]:
    """Classify every unittest terminal state without treating skips as passes."""
    if result.skipped:
        return "UNOBSERVED", result.skipped[0][1]
    if result.expectedFailures:
        return "FAIL", result.expectedFailures[0][1]
    if result.unexpectedSuccesses:
        return "FAIL", "test unexpectedly succeeded"
    problems = [detail for _test, detail in [*result.failures, *result.errors]]
    return ("FAIL", problems[0]) if problems else ("PASS", None)


def run_offline(scenarios: list[Scenario]) -> list[dict[str, object]]:
    """Run each scenario separately so a report preserves its full denominator."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    loader = unittest.defaultTestLoader
    results: list[dict[str, object]] = []
    for scenario in scenarios:
        suite = loader.loadTestsFromName(scenario.test)
        if suite.countTestCases() != 1:
            results.append(
                {
                    "id": scenario.identifier,
                    "dimension": scenario.dimension,
                    "status": "FAIL",
                    "evidence": scenario.test,
                    "detail": "scenario test must resolve to exactly one test case",
                }
            )
            continue
        result = unittest.TestResult()
        started = time.perf_counter()
        suite.run(result)
        elapsed_ms = round((time.perf_counter() - started) * 1_000, 1)
        status, detail = _test_problems(result)
        results.append(
            {
                "id": scenario.identifier,
                "dimension": scenario.dimension,
                "status": status,
                "evidence": scenario.test,
                "duration_ms": elapsed_ms,
                **({"detail": detail} if detail else {}),
            }
        )
    return results


def _file_identity(path: Path) -> dict[str, str]:
    """Identify a separately installed artifact used by a live evaluation."""
    content = path.read_bytes()
    return {"path": str(path.resolve()), "sha256": hashlib.sha256(content).hexdigest()}


def run_live(codex_home: Path, local_config: Path) -> dict[str, object]:
    """Exercise the installed Hook and Doctor locally without GitHub writes."""
    from scripts.doctor import doctor

    installed_hook = codex_home / "harness/v23/task_bootstrap.py"
    try:
        hook_identity = _file_identity(installed_hook)
        hook = subprocess.run(
            (sys.executable, str(installed_hook), "--local-config", str(local_config)),
            input=json.dumps({"cwd": str(ROOT), "prompt": "V23 live evaluation."}),
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError as error:
        hook_identity = {"path": str(installed_hook), "sha256": "unavailable"}
        hook = subprocess.CompletedProcess((), 1, "", str(error))
    try:
        hook_payload = json.loads(hook.stdout)
        context = hook_payload["hookSpecificOutput"]["additionalContext"]
    except (KeyError, TypeError, json.JSONDecodeError):
        context = "invalid Hook response"
    doctor_report = doctor(codex_home, local_config, ROOT, check_github=False)
    ready = all(f"{tool}=ready" in context for tool in ("CodeGraph", "Semble", "RTK"))
    return {
        "name": "installed_hook_and_doctor",
        "status": "PASS" if hook.returncode == 0 and ready and doctor_report["ok"] else "FAIL",
        "hook_context": context,
        "doctor_ok": doctor_report["ok"],
        "installed_hook": hook_identity,
        "doctor_source": str(ROOT / "scripts/doctor.py"),
    }


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, default=ROOT / "evals/v23-scenarios.toml")
    parser.add_argument(
        "--live", action="store_true", help="also probe the installed local Hook and Doctor"
    )
    parser.add_argument("--codex-home", type=Path, default=Path.home() / ".codex")
    parser.add_argument(
        "--local-config", type=Path, default=Path.home() / ".config/codex-harness/local.toml"
    )
    args = parser.parse_args(argv)
    try:
        suite = load_scenarios(args.suite)
    except (TypeError, ValueError) as error:
        print(json.dumps({"ok": False, "error": str(error)}))
        return 2
    results = run_offline(suite.scenarios)
    passed = sum(result["status"] == "PASS" for result in results)
    failed = sum(result["status"] == "FAIL" for result in results)
    unobserved = len(results) - passed - failed
    report: dict[str, object] = {
        "ok": failed == 0 and unobserved == 0,
        "source_revision": _source_revision(),
        "suite": {
            "version": suite.version,
            "path": str(suite.path),
            "content_sha256": suite.content_sha256,
        },
        "offline": {
            "total": len(results),
            "passed": passed,
            "failed": failed,
            "unobserved": unobserved,
            "results": results,
        },
        "unobserved_claims": {"total": len(UNOBSERVED_METRICS), "claims": list(UNOBSERVED_METRICS)},
    }
    if args.live:
        live = run_live(args.codex_home, args.local_config)
        report["live"] = live
        report["ok"] = bool(report["ok"] and live["status"] == "PASS")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
