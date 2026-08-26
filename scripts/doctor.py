"""Report whether a V23 local installation is usable and run bounded tool probes.

Doctor summarizes the live installation; it does not re-enter the full
UserPromptSubmit ``task_bootstrap`` hook. Live runtime state for new tasks is
collected separately from ``install.json`` and daemon probes. Memory of earlier
tasks is historical only.
"""

from __future__ import annotations

try:
    from scripts.runtime import ensure_supported_python
except ModuleNotFoundError:  # Support the documented direct script entrypoint.
    from runtime import ensure_supported_python

ensure_supported_python(__file__)

import argparse
import json
import os
import subprocess
import tomllib
from collections.abc import Callable, Iterable
from pathlib import Path

try:
    from scripts.install import effective_global_instruction
    from scripts.task_bootstrap import ToolResult, local_installation_checks, probe_tools
except ModuleNotFoundError:  # Support the documented `python scripts/doctor.py` entrypoint.
    from install import effective_global_instruction
    from task_bootstrap import ToolResult, local_installation_checks, probe_tools


def _result(name: str, ok: bool, detail: str) -> dict[str, object]:
    return {"name": name, "ok": ok, "detail": detail}


def _github_login(config_dir: str) -> tuple[bool, str]:
    if not config_dir:
        return False, "not configured"
    if not Path(config_dir).is_dir():
        return False, "configured GH_CONFIG_DIR does not exist"
    completed = subprocess.run(
        ["gh", "api", "user", "--jq", ".login"],
        env={**os.environ, "GH_CONFIG_DIR": config_dir},
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode:
        return False, "GitHub CLI identity check failed"
    return True, completed.stdout.strip()


def _agent_chain(project: Path) -> list[str]:
    """Return the effective project instruction candidates in Codex order."""
    project = project.resolve()
    root = project
    completed = subprocess.run(
        ["git", "-C", str(project), "rev-parse", "--show-toplevel"],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode == 0 and completed.stdout.strip():
        root = Path(completed.stdout.strip()).resolve()
    directories = [root]
    cursor = project
    while cursor != root and root in cursor.parents:
        directories.append(cursor)
        cursor = cursor.parent
    directories = [root, *reversed(directories[1:])]
    candidates: list[str] = []
    for directory in directories:
        for name in ("AGENTS.override.md", "AGENTS.md"):
            target = directory / name
            if target.is_file() and target.read_text().strip():
                candidates.append(str(target))
                break
    return candidates


ToolProbe = Callable[[Path, str, dict[str, object]], list[ToolResult]]


def doctor(
    codex_home: Path,
    local_config: Path,
    project: Path,
    check_github: bool = True,
    tool_probe: ToolProbe = probe_tools,
    probe_required_tools: bool = True,
) -> dict:
    checks: list[dict[str, object]] = []
    codex_home = codex_home.resolve()
    source_bridge = Path(__file__).resolve().parent / "grok_execution.py"
    local_checks = local_installation_checks(codex_home, local_config, source_bridge=source_bridge)
    checks.extend(_result(name, ok, detail) for name, ok, detail in local_checks)
    local: dict = {}
    tools: dict[str, object] = {}
    local_ok = next((item for item in local_checks if item[0] == "local_config"), None)
    if local_ok is not None and local_ok[1]:
        try:
            loaded = tomllib.loads(local_config.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            loaded = {}
        if isinstance(loaded, dict):
            local = loaded
            raw_tools = local.get("tools", {})
            tools = raw_tools if isinstance(raw_tools, dict) else {}
    missing = [name for name in ("codegraph", "semble", "rtk") if not tools.get(name)]
    if local_ok is not None and local_ok[1] and not missing and probe_required_tools:
        try:
            tool_results = tool_probe(project, "V23 Doctor health probe.", tools)
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            tool_results = [
                ToolResult(name.title(), False, f"tool probe failed: {error}")
                for name in ("codegraph", "semble", "rtk")
            ]
        expected_names = {"CodeGraph", "Semble", "RTK"}
        observed_names = {result.name for result in tool_results}
        if observed_names != expected_names:
            checks.append(
                _result("tools_behavior", False, "probe did not return all required tools")
            )
        for result in tool_results:
            checks.append(_result(f"tool_{result.name.casefold()}", result.ok, result.detail))
    if check_github:
        if not local:
            try:
                loaded = tomllib.loads(local_config.read_text(encoding="utf-8"))
            except (OSError, tomllib.TOMLDecodeError):
                loaded = {}
            if isinstance(loaded, dict):
                local = loaded
        if local:
            github = local.get("github", {})
            if not isinstance(github, dict):
                checks.append(_result("github_config", False, "[github] is not a TOML table"))
                github = {}
            for role in ("author", "reviewer"):
                ok, detail = _github_login(github.get(f"{role}_config_dir", ""))
                expected = github.get(f"{role}_login", "")
                identity_ok = ok and bool(expected) and detail.casefold() == expected.casefold()
                checks.append(_result(f"github_{role}", identity_ok, detail))
            author, reviewer = github.get("author_login", ""), github.get("reviewer_login", "")
            checks.append(
                _result(
                    "github_audit_identity_split",
                    bool(author and reviewer and author.casefold() != reviewer.casefold()),
                    "same-machine audit identities must differ",
                )
            )
    return {
        "ok": all(bool(check["ok"]) for check in checks),
        "active_global_instruction": str(effective_global_instruction(codex_home)),
        "project_instruction_candidates": _agent_chain(project),
        "primary_profile_start": "codex --profile v23-primary",
        "live_runtime_authority": (
            "Current runtime state is collected live from "
            "${CODEX_HOME}/harness/v23-state/install.json and daemon probes. "
            "Prior-task memory is historical only and is not an authority."
        ),
        "checks": checks,
    }


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex-home", type=Path, default=Path.home() / ".codex")
    parser.add_argument(
        "--local-config", type=Path, default=Path.home() / ".config/codex-harness/local.toml"
    )
    parser.add_argument("--project", type=Path, default=Path.cwd())
    parser.add_argument("--skip-github", action="store_true")
    args = parser.parse_args(argv)
    result = doctor(args.codex_home, args.local_config, args.project, not args.skip_github)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
