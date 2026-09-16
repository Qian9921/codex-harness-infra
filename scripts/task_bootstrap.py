"""Inject V23 installed instructions and live runtime checks for one prompt.

The script is installed outside repositories and invoked by the sole V23
``UserPromptSubmit`` hook. It injects a bounded live runtime-state block.
CodeGraph, Semble, and RTK remain explicit Doctor/``probe_tools`` calls, not a
per-prompt batch; code investigation or change tasks owe the CodeGraph index
check described in the installed routing reference. Explicit maintenance can
call ``probe_tool_versions`` for bounded versions and update availability.
Install manifest and live daemon probes are authoritative; prior-task memory
is historical only. It deliberately has no task database, Stop hook, or
background loop.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

try:
    from scripts.delivery_policy import DeliveryError, parse_delivery
    from scripts.executor_routing import (
        BACKEND_CODEX,
        RoutingError,
        default_native_spec,
        executor_agent_file,
        executor_agent_name,
        grok_checks_required,
        parse_policy,
    )
except ModuleNotFoundError:  # Installed copy lives beside this hook script.
    try:
        from delivery_policy import DeliveryError, parse_delivery
        from executor_routing import (
            BACKEND_CODEX,
            RoutingError,
            default_native_spec,
            executor_agent_file,
            executor_agent_name,
            grok_checks_required,
            parse_policy,
        )
    except ModuleNotFoundError:
        if (Path(__file__).resolve().parent / "executor_routing.py").is_file():
            raise
        # pragma: no cover - installer always ships the helper.

        class RoutingError(RuntimeError):
            """Placeholder when the routing helper is absent."""

        def parse_policy(config: dict) -> object:
            return None

        def parse_delivery(config: dict) -> object:
            return None

        class DeliveryError(RuntimeError):
            """Placeholder when the delivery helper is absent."""

        def grok_checks_required(policy: object) -> bool:
            return True

        BACKEND_CODEX = "codex"

        def default_native_spec(policy: object) -> object:
            return None

        def executor_agent_name(spec: object, policy: object) -> str:
            return "v23_executor"

        def executor_agent_file(spec: object, policy: object) -> str:
            return "agents/v23-executor.toml"


CODEGRAPH_BEGIN = "# BEGIN CODEX-HARNESS-INFRA V23 CODEGRAPH"
CODEGRAPH_END = "# END CODEX-HARNESS-INFRA V23 CODEGRAPH"
REQUIRED_TOOLS = ("codegraph", "semble", "rtk")
# Default hook reads installed files only. Daemon JSON (~9s floor) stays
# explicit via probe_daemons. Semble (~29s floor) stays on Doctor/probe_tools.
GIT_DISCOVERY_TIMEOUT_SECONDS = 4
CODEGRAPH_TIMEOUT_SECONDS = 14
SEMBLE_TIMEOUT_SECONDS = 36
RTK_TIMEOUT_SECONDS = 8
UPDATE_CHECK_TIMEOUT_SECONDS = 20
LIVE_PROBE_TIMEOUT_SECONDS = 12
# Finite RTK routes, each verified through its own --help before a routed
# call. Anything outside this set stays raw: exact JSON, porcelain, unified
# diffs, and necessary raw diagnostics are explicit raw exceptions.
RTK_VERIFIED_ROUTES = ("git", "ls", "pytest")
HOOK_TIMEOUT_OVERHEAD_SECONDS = 8
HOOK_TIMEOUT_SECONDS = HOOK_TIMEOUT_OVERHEAD_SECONDS
DEFAULT_PRIMARY_EFFORT = "high"
DEFAULT_EXECUTOR_EFFORT = "low"
LIVE_STATE_CONTEXT_CAP = 1600
HOOK_CONTEXT_CAP = 2500
CONTROL_SOCKET_RELATIVE = "app-server-control/app-server-control.sock"
V23_MARKER = "CODEX-HARNESS-INFRA V23"
SAFE_CODE_MODE_HOST = "features.code_mode_host=true"
LISTEN_FLAG = "00010000"
LISTEN_STATE = "01"
SECRET_FRAGMENT = re.compile(
    r"(?i)\b(?:token|secret|password|passwd|api[_-]?key|authorization|bearer)\s*[:=]\s*\S+"
)


@dataclass(frozen=True)
class ToolResult:
    """The concise result of one required tool operation."""

    name: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class LiveField:
    """One fail-open live runtime-state field."""

    name: str
    status: str
    detail: str

    def render(self) -> str:
        return f"{self.name}={self.status}:{self.detail}"


CommandRunner = Callable[[tuple[str, ...], Path | None, int], subprocess.CompletedProcess[str]]


def _system_runner(
    command: tuple[str, ...], cwd: Path | None, timeout_seconds: int
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout_seconds,
    )


def _compact(value: str, root: Path | None = None) -> str:
    """Return bounded hook context without leaking a full command transcript."""
    text = " ".join(value.strip().split())
    if root:
        text = text.replace(str(root), ".")
    return text[:220] if text else "completed"


def _run(
    command: tuple[str, ...],
    cwd: Path | None,
    runner: CommandRunner,
    timeout_seconds: int,
    root: Path | None = None,
) -> tuple[bool, str]:
    """Run one bounded command and retain only a short diagnostic."""
    try:
        completed = runner(command, cwd, timeout_seconds)
    except FileNotFoundError:
        return False, f"executable unavailable: {command[0]}"
    except subprocess.TimeoutExpired:
        return False, "timed out"
    except OSError as error:
        return False, str(error)
    output = completed.stdout if completed.returncode == 0 else completed.stderr or completed.stdout
    if completed.returncode:
        return False, _compact(output, root) or f"exit {completed.returncode}"
    return True, _compact(output, root)


def _run_json(
    command: tuple[str, ...],
    runner: CommandRunner,
    timeout_seconds: int,
) -> tuple[bool, str]:
    """Run one bounded command and keep enough stdout to parse one JSON object."""
    try:
        completed = runner(command, None, timeout_seconds)
    except FileNotFoundError:
        return False, f"executable unavailable: {command[0]}"
    except subprocess.TimeoutExpired:
        return False, "timed out"
    except OSError as error:
        return False, str(error)
    output = completed.stdout if completed.returncode == 0 else completed.stderr or completed.stdout
    text = (output or "").strip()
    if completed.returncode:
        return False, _compact(text) or f"exit {completed.returncode}"
    return True, text[:4000]


def _resolve_executable(value: object) -> str | None:
    """Resolve a local configuration command without invoking a shell."""
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = value.strip()
    path = Path(candidate).expanduser()
    if path.is_file():
        return str(path.resolve())
    return shutil.which(candidate)


def _git_root(cwd: Path, runner: CommandRunner) -> Path | None:
    ok, output = _run(
        ("git", "-C", str(cwd), "rev-parse", "--show-toplevel"),
        None,
        runner,
        GIT_DISCOVERY_TIMEOUT_SECONDS,
    )
    if not ok or not output:
        return None
    root = Path(output)
    return root.resolve() if root.is_dir() else None


def _atomic_write(path: Path, content: str) -> None:
    """Replace one local support file without partially writing it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _ensure_codegraph_exclude(root: Path, runner: CommandRunner) -> tuple[bool, str]:
    """Ignore the V23-created CodeGraph cache using only a marked Git-local block."""
    ok, output = _run(
        ("git", "-C", str(root), "rev-parse", "--git-path", "info/exclude"),
        None,
        runner,
        GIT_DISCOVERY_TIMEOUT_SECONDS,
        root,
    )
    if not ok:
        return False, f"cannot locate Git exclude file: {output}"
    exclude = Path(output)
    if not exclude.is_absolute():
        exclude = root / exclude
    if exclude.is_symlink():
        return False, "Git exclude file is a symlink"
    existing = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    begins, ends = existing.count(CODEGRAPH_BEGIN), existing.count(CODEGRAPH_END)
    if begins != ends or begins > 1:
        return False, "V23 CodeGraph exclude marker is partial or duplicated"
    if begins == 1:
        start = existing.index(CODEGRAPH_BEGIN) + len(CODEGRAPH_BEGIN)
        finish = existing.index(CODEGRAPH_END)
        if existing[start:finish].strip() != ".codegraph/":
            return False, "V23 CodeGraph exclude block was modified"
        return True, "Git-local cache exclusion already present"
    rendered = existing.rstrip("\n")
    if rendered:
        rendered += "\n\n"
    rendered += f"{CODEGRAPH_BEGIN}\n.codegraph/\n{CODEGRAPH_END}\n"
    _atomic_write(exclude, rendered)
    return True, "added Git-local cache exclusion"


def _codegraph_state_text(payload: dict[str, object]) -> str:
    """Render the owner index's declared current-tree freshness."""
    pending = payload.get("pendingChanges")
    pending_total = 0
    if isinstance(pending, dict):
        pending_total = sum(
            int(value) for value in pending.values() if isinstance(value, (int, float))
        )
    index = payload.get("index")
    state = index.get("state") if isinstance(index, dict) else None
    return (
        f"initialized={bool(payload.get('initialized'))} files={payload.get('fileCount', '?')} "
        f"pending={pending_total} state={state or 'reported'} "
        f"indexed={payload.get('lastIndexed', 'unknown')}"
    )


def _codegraph_stale(payload: dict[str, object]) -> bool:
    """True when the current tree differs from the indexed tree or was never complete."""
    pending = payload.get("pendingChanges")
    if isinstance(pending, dict) and any(
        isinstance(value, (int, float)) and value > 0 for value in pending.values()
    ):
        return True
    if payload.get("worktreeMismatch"):
        return True
    index = payload.get("index")
    if isinstance(index, dict):
        if index.get("reindexRecommended"):
            return True
        state = index.get("state")
        if isinstance(state, str) and state != "complete":
            return True
    return False


def _codegraph_status(
    executable: str, root: Path, runner: CommandRunner
) -> tuple[bool, str, dict[str, object]]:
    """Read the owner index state from JSON, then declared text for old versions."""
    ok, detail = _run_json(
        (executable, "status", "--json", str(root)), runner, CODEGRAPH_TIMEOUT_SECONDS
    )
    if ok:
        start = detail.find("{")
        try:
            loaded = json.loads(detail if start < 0 else detail[start:])
        except json.JSONDecodeError:
            loaded = None
        if isinstance(loaded, dict):
            return True, _codegraph_state_text(loaded), loaded
    plain_ok, plain = _run(
        (executable, "status", str(root)), root, runner, CODEGRAPH_TIMEOUT_SECONDS, root
    )
    if plain_ok:
        # CodeGraph reports an uninitialized project in stdout with exit 0.
        initialized = "not initialized" not in plain.casefold()
        return True, plain, {"initialized": initialized}
    return False, detail, {}


def _codegraph_result(
    executable: str | None,
    root: Path | None,
    runner: CommandRunner,
    initialize: bool,
    *,
    query: str = "",
    symbol: str | None = None,
) -> ToolResult:
    """Check the owner index before any query; refresh only with write authority."""
    if not executable:
        return ToolResult(
            "CodeGraph", False, "not configured; baseline bounded-search/direct reads"
        )
    if root is None:
        ok, detail = _run((executable, "--version"), None, runner, CODEGRAPH_TIMEOUT_SECONDS)
        suffix = (
            "non-Git directory; version probe"
            if ok
            else f"{detail}; baseline bounded-search/direct reads"
        )
        return ToolResult("CodeGraph", ok, suffix)
    if initialize:
        excluded, detail = _ensure_codegraph_exclude(root, runner)
        if not excluded:
            return ToolResult("CodeGraph", False, detail)
    status_ok, status, payload = _codegraph_status(executable, root, runner)
    initialized = status_ok and bool(payload.get("initialized"))
    stale = initialized and _codegraph_stale(payload)
    if not initialized or stale:
        if initialized:
            state = "stale index"
        elif status_ok:
            state = "index missing"
        else:
            state = "index status unavailable"
        if not initialize:
            return ToolResult(
                "CodeGraph",
                False,
                f"{state}; read-only fallback to bounded-search/direct reads, state the limit",
            )
        if not initialized:
            initialized_ok, init_detail = _run(
                (executable, "init", str(root)), root, runner, CODEGRAPH_TIMEOUT_SECONDS, root
            )
            if not initialized_ok:
                return ToolResult(
                    "CodeGraph",
                    False,
                    f"init failed: {init_detail}; baseline bounded-search/direct reads",
                )
        synced, sync_detail = _run(
            (executable, "sync", str(root)), root, runner, CODEGRAPH_TIMEOUT_SECONDS, root
        )
        if not synced:
            return ToolResult(
                "CodeGraph",
                False,
                f"sync failed: {sync_detail}; baseline bounded-search/direct reads",
            )
        status_ok, status, payload = _codegraph_status(executable, root, runner)
        if not status_ok or not payload.get("initialized"):
            return ToolResult(
                "CodeGraph",
                False,
                f"refresh failed: {status}; baseline bounded-search/direct reads",
            )
        if _codegraph_stale(payload):
            return ToolResult(
                "CodeGraph",
                False,
                f"index still stale after sync: {status}; cross-check current source",
            )
    if symbol:
        queried, detail = _run(
            (executable, "callers", "-p", str(root), "--json", symbol),
            root,
            runner,
            CODEGRAPH_TIMEOUT_SECONDS,
            root,
        )
        return ToolResult(
            "CodeGraph",
            queried,
            detail if queried else f"structural query failed: {detail}; cross-check current source",
        )
    if query:
        queried, detail = _run(
            (executable, "query", "-p", str(root), "--json", query),
            root,
            runner,
            CODEGRAPH_TIMEOUT_SECONDS,
            root,
        )
        return ToolResult(
            "CodeGraph",
            queried,
            detail if queried else f"focused query failed: {detail}; cross-check current source",
        )
    return ToolResult("CodeGraph", True, f"index check: {status}")


def _prompt_query(prompt: str) -> str:
    """Build a small source-search query from the user's words."""
    words = re.findall(r"[\w.-]{3,}", prompt, flags=re.UNICODE)
    return " ".join(words[:12]) or "task"


def _semble_health_scope() -> Path:
    """Return the small V23-owned source directory for mandatory Semble use."""
    return Path(__file__).resolve().parent


def _semble_result(executable: str | None, prompt: str, runner: CommandRunner) -> ToolResult:
    """Run an optional, bounded semantic search without indexing an umbrella workspace."""
    if not executable:
        return ToolResult("Semble", False, "not configured; semantic discovery skipped")
    target = _semble_health_scope()
    command = (
        executable,
        "search",
        "--content",
        "code",
        "--top-k",
        "1",
        "--max-snippet-lines",
        "0",
        _prompt_query(prompt),
        str(target),
    )
    ok, detail = _run(command, target, runner, SEMBLE_TIMEOUT_SECONDS, target)
    return ToolResult("Semble", ok, detail)


def _rtk_result(
    executable: str | None, root: Path | None, cwd: Path, runner: CommandRunner
) -> ToolResult:
    """Route only the finite verified RTK set; raw exceptions stay raw."""
    if not executable:
        return ToolResult("RTK", False, "not configured; keep commands raw")
    unsupported: list[str] = []
    for route in RTK_VERIFIED_ROUTES:
        ok, _detail = _run((executable, route, "--help"), cwd, runner, RTK_TIMEOUT_SECONDS, root)
        if not ok:
            unsupported.append(route)
    probe_route = "git" if root is not None else "ls"
    if probe_route in unsupported:
        return ToolResult(
            "RTK",
            False,
            f"rtk {probe_route} route unavailable (unsupported: {','.join(unsupported)}); "
            "keep commands raw",
        )
    command = (
        (executable, "git", "-C", str(root), "status")
        if root is not None
        else (executable, "ls", str(cwd))
    )
    ok, detail = _run(command, cwd, runner, RTK_TIMEOUT_SECONDS, root)
    if not ok:
        return ToolResult("RTK", False, f"routed probe failed: {detail}; keep commands raw")
    note = (
        "" if not unsupported else f"; raw fallback for unsupported routes: {','.join(unsupported)}"
    )
    return ToolResult("RTK", True, detail + note)


def probe_tools(
    cwd: Path,
    prompt: str,
    tools: dict[str, object],
    *,
    runner: CommandRunner | None = None,
    initialize_codegraph: bool = True,
    codegraph_symbol: str | None = None,
) -> list[ToolResult]:
    """Explicit Doctor/task-relevant probe; the index check precedes any query."""
    runner = runner or _system_runner
    cwd = cwd.resolve()
    root = _git_root(cwd, runner)
    codegraph = _codegraph_result(
        _resolve_executable(tools.get("codegraph")),
        root,
        runner,
        initialize_codegraph,
        query=_prompt_query(prompt),
        symbol=codegraph_symbol,
    )
    semble = _semble_result(_resolve_executable(tools.get("semble")), prompt, runner)
    rtk = _rtk_result(_resolve_executable(tools.get("rtk")), root, cwd, runner)
    return [codegraph, semble, rtk]


def probe_tool_versions(
    tools: dict[str, object], *, runner: CommandRunner | None = None
) -> list[ToolResult]:
    """Explicit install/migration maintenance: bounded versions and update check.

    Never upgrades, starts no daemon, and reports an offline or unsupported
    update check honestly instead of treating it as a tool failure.
    """
    runner = runner or _system_runner
    results: list[ToolResult] = []
    for key, label in (("codegraph", "CodeGraph"), ("rtk", "RTK")):
        executable = _resolve_executable(tools.get(key))
        if not executable:
            results.append(ToolResult(f"{label} version", False, "not configured"))
            continue
        ok, detail = _run((executable, "--version"), None, runner, RTK_TIMEOUT_SECONDS)
        results.append(
            ToolResult(
                f"{label} version",
                ok,
                detail if ok else f"version probe failed: {detail}",
            )
        )
    semble = _resolve_executable(tools.get("semble"))
    results.append(
        ToolResult(
            "Semble version",
            True,
            "installed; CLI does not expose --version" if semble else "not configured; optional",
        )
    )
    codegraph = _resolve_executable(tools.get("codegraph"))
    if codegraph:
        checked, detail = _run(
            (codegraph, "upgrade", "--check"), None, runner, UPDATE_CHECK_TIMEOUT_SECONDS
        )
        results.append(
            ToolResult(
                "CodeGraph update",
                True,
                detail
                if checked
                else f"update check unavailable (offline or unsupported): {detail}; no auto-update",
            )
        )
    return results


def _privacy_text(value: str) -> str:
    """Drop secret-shaped fragments and cap one field."""
    text = SECRET_FRAGMENT.sub("[redacted]", " ".join(value.strip().split()))
    return text[:220] if text else "unavailable"


def _field_ok(name: str, detail: str) -> LiveField:
    return LiveField(name, "ok", _privacy_text(detail))


def _field_status(name: str, status: str, detail: str) -> LiveField:
    return LiveField(name, status, _privacy_text(detail))


def collect_install_state(state_dir: Path) -> list[LiveField]:
    """Read the durable V23 install manifest; missing or corrupt is fail-open."""
    manifest_path = state_dir / "install.json"
    if not manifest_path.is_file():
        return [
            _field_status("infra_version", "missing", "install.json absent"),
            _field_status("install_digest", "missing", "install.json absent"),
            _field_status("source_commit", "unavailable", "install.json absent"),
        ]
    try:
        record = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return [
            _field_status("infra_version", "corrupt", str(error)),
            _field_status("install_digest", "corrupt", str(error)),
            _field_status("source_commit", "corrupt", str(error)),
        ]
    if not isinstance(record, dict):
        return [
            _field_status("infra_version", "corrupt", "install.json is not an object"),
            _field_status("install_digest", "corrupt", "install.json is not an object"),
            _field_status("source_commit", "corrupt", "install.json is not an object"),
        ]
    version = record.get("version")
    version_field = (
        _field_ok("infra_version", str(version))
        if isinstance(version, str) and version.strip()
        else _field_status("infra_version", "unavailable", "version missing")
    )
    digest = record.get("portable_digest") or record.get("config_digest")
    digest_field = (
        _field_ok("install_digest", str(digest)[:64])
        if isinstance(digest, str) and digest.strip()
        else _field_status("install_digest", "unavailable", "digest missing")
    )
    commit = record.get("source_commit")
    commit_field = (
        _field_ok("source_commit", str(commit))
        if isinstance(commit, str) and re.fullmatch(r"[0-9a-f]{40}", commit)
        else _field_status("source_commit", "unavailable", "source commit not recorded")
    )
    return [version_field, digest_field, commit_field]


def _json_string(record: dict[str, object], *keys: str) -> str | None:
    for key in keys:
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def collect_version_json(runner: CommandRunner) -> tuple[list[LiveField], Path | None]:
    """Parse bounded `codex app-server daemon version` JSON for CLI/app-server status."""
    ok, detail = _run_json(
        ("codex", "app-server", "daemon", "version"),
        runner,
        LIVE_PROBE_TIMEOUT_SECONDS,
    )
    if not ok:
        status = "timeout" if detail == "timed out" else "unavailable"
        if "unavailable" not in detail and detail != "timed out":
            status = "error"
        reason = detail or "daemon version unavailable"
        return (
            [
                _field_status("cli_version", status, reason),
                _field_status("app_server", status, reason),
            ],
            None,
        )
    try:
        start = detail.find("{")
        payload = json.loads(detail if start < 0 else detail[start:])
    except json.JSONDecodeError as error:
        return (
            [
                _field_status("cli_version", "error", str(error)),
                _field_status("app_server", "error", str(error)),
            ],
            None,
        )
    if not isinstance(payload, dict):
        return (
            [
                _field_status("cli_version", "error", "daemon version is not an object"),
                _field_status("app_server", "error", "daemon version is not an object"),
            ],
            None,
        )
    nested = payload.get("appServer") if isinstance(payload.get("appServer"), dict) else {}
    record = {**payload, **(nested if isinstance(nested, dict) else {})}
    cli = _json_string(record, "cliVersion", "cli_version", "cli", "localCliVersion")
    app = _json_string(
        record, "appServerVersion", "app_server_version", "appServer", "runningAppServerVersion"
    )
    status_text = _json_string(record, "status", "state") or "reported"
    managed = _json_string(record, "managedCodexPath", "managed_codex_path", "codexPath")
    fields = []
    fields.append(
        _field_ok("cli_version", cli)
        if cli
        else _field_status("cli_version", "unavailable", status_text)
    )
    app_detail = f"status={status_text} version={app or 'unavailable'}"
    if managed:
        app_detail += f" managedCodexPath={Path(managed).name}"
    fields.append(
        _field_ok("app_server", app_detail)
        if app or status_text
        else _field_status("app_server", "unavailable", "version missing")
    )
    managed_path = Path(managed).expanduser() if managed else None
    return fields, managed_path


def parse_proc_net_unix(text: str) -> list[tuple[str, str, str, str]]:
    """Return (flags, state, inode, path) rows for UNIX sockets with a path."""
    rows: list[tuple[str, str, str, str]] = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 8 or parts[0].startswith("Num"):
            continue
        path = next((part for part in reversed(parts) if part.startswith("/")), "")
        if path.endswith(".sock"):
            rows.append((parts[3], parts[5], parts[6], path))
    return rows


def canonical_control_socket(codex_home: Path) -> Path:
    return codex_home.resolve() / CONTROL_SOCKET_RELATIVE


def collect_control_socket(
    codex_home: Path,
    proc_net_unix: str | None = None,
    *,
    expected_uid: int | None = None,
) -> LiveField:
    """Health-check only the canonical current-user control socket LISTEN entry."""
    path = canonical_control_socket(codex_home)
    uid = os.getuid() if expected_uid is None else expected_uid
    if not path.exists():
        return _field_status("control_socket", "unavailable", f"path={path} missing")
    try:
        stat_result = path.lstat()
    except OSError as error:
        return _field_status("control_socket", "error", f"path={path} {error}")
    if not stat.S_ISSOCK(stat_result.st_mode):
        return _field_status("control_socket", "error", f"path={path} not-a-socket")
    if stat_result.st_uid != uid:
        return _field_status(
            "control_socket", "error", f"path={path} uid={stat_result.st_uid} expected={uid}"
        )
    text = proc_net_unix
    if text is None:
        unix_table = Path("/proc/net/unix")
        if not unix_table.is_file():
            return _field_status(
                "control_socket", "unavailable", f"path={path} /proc/net/unix absent"
            )
        try:
            text = unix_table.read_text(encoding="utf-8", errors="replace")
        except OSError as error:
            return _field_status("control_socket", "error", f"path={path} {error}")
    listen = [
        row
        for row in parse_proc_net_unix(text)
        if row[3] == str(path) and row[0] == LISTEN_FLAG and row[1] == LISTEN_STATE
    ]
    if not listen:
        return _field_status("control_socket", "unavailable", f"path={path} no matching LISTEN")
    inode = listen[0][2]
    try:
        import pwd

        owner = pwd.getpwuid(stat_result.st_uid).pw_name
    except (ImportError, KeyError, OSError):
        owner = str(stat_result.st_uid)
    mode = oct(stat_result.st_mode & 0o777)
    return _field_ok(
        "control_socket",
        f"path={path} uid={stat_result.st_uid} owner={owner} mode={mode} inode={inode}",
    )


def sanitize_cmdline(parts: Iterable[str], *, canonical_socket: Path | None = None) -> str:
    """Emit only executable identity and known-safe app-server listener tokens."""
    tokens = [str(part) for part in parts]
    rendered: list[str] = []
    index = 0
    canonical = str(canonical_socket) if canonical_socket is not None else ""
    while index < len(tokens):
        token = tokens[index]
        name = Path(token).name
        if index == 0 and name:
            rendered.append(name)
            index += 1
            continue
        if token == "app-server":
            rendered.append(token)
            index += 1
            continue
        if token == "-c" and index + 1 < len(tokens) and tokens[index + 1] == SAFE_CODE_MODE_HOST:
            rendered.append(f"-c {SAFE_CODE_MODE_HOST}")
            index += 2
            continue
        if token == f"-c{SAFE_CODE_MODE_HOST}" or token == f"-c={SAFE_CODE_MODE_HOST}":
            rendered.append(f"-c {SAFE_CODE_MODE_HOST}")
            index += 1
            continue
        if token == "--listen" and index + 1 < len(tokens):
            value = tokens[index + 1]
            if _safe_listen_value(value, canonical):
                rendered.append("--listen unix://")
            index += 2
            continue
        if token.startswith("--listen="):
            value = token.split("=", 1)[1]
            if _safe_listen_value(value, canonical):
                rendered.append("--listen unix://")
            index += 1
            continue
        if (
            token.startswith("-")
            and index + 1 < len(tokens)
            and not tokens[index + 1].startswith("-")
        ):
            index += 2
            continue
        index += 1
    return " ".join(rendered)[:220]


def _safe_listen_value(value: str, canonical: str) -> bool:
    if not value.startswith("unix://"):
        return False
    target = value.removeprefix("unix://")
    if canonical and target == canonical:
        return True
    return target.endswith(("/" + CONTROL_SOCKET_RELATIVE, "app-server-control.sock"))


def _read_proc_cmdline(
    proc_root: Path, pid: int, *, canonical_socket: Path | None = None
) -> tuple[str | None, tuple[str, ...] | None, str | None]:
    base = proc_root / str(pid)
    exe_path = None
    try:
        exe_path = str((base / "exe").resolve())
    except OSError:
        exe_path = None
    try:
        raw = (base / "cmdline").read_bytes()
    except OSError:
        return exe_path, None, None
    parts = tuple(part.decode("utf-8", "replace") for part in raw.split(b"\0") if part)
    return exe_path, parts, sanitize_cmdline(parts, canonical_socket=canonical_socket)


def _pid_bound_to_inode(proc_root: Path, inode: str, uid: int) -> int | None:
    target = f"socket:[{inode}]"
    try:
        entries = list(proc_root.iterdir())
    except OSError:
        return None
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            if entry.stat().st_uid != uid:
                continue
        except OSError:
            continue
        fd_dir = entry / "fd"
        try:
            fds = list(fd_dir.iterdir())
        except OSError:
            continue
        for fd in fds[:64]:
            try:
                if os.readlink(fd) == target:
                    return int(entry.name)
            except OSError:
                continue
    return None


def _expected_codex_executable(managed: Path | None) -> Path | None:
    if managed is not None:
        return managed.expanduser()
    found = shutil.which("codex")
    return Path(found).resolve() if found else None


def _is_current_codex_executable(exe: str, expected: Path | None) -> bool:
    path = Path(exe)
    if path.name not in {"codex", "codex-app-server"}:
        return False
    if expected is None:
        return False
    try:
        return path.resolve() == expected.resolve()
    except OSError:
        return False


def _is_app_server_unix_listener(parts: tuple[str, ...], canonical: Path) -> bool:
    lowered = [part.casefold() for part in parts]
    if "proxy" in lowered:
        return False
    if "app-server" not in lowered:
        return False
    joined = " ".join(parts)
    listen = canonical.as_posix()
    return "unix://" in joined or listen in joined or "--listen" in lowered


def collect_bound_daemon(
    socket_field: LiveField,
    proc_root: Path = Path("/proc"),
    *,
    expected_uid: int | None = None,
    expected_exe: Path | None = None,
    canonical_socket: Path | None = None,
) -> LiveField:
    """Expose PID/exe/cmdline only for the current-user Codex unix listener."""
    if socket_field.status != "ok":
        return _field_status("daemon", "unavailable", "control socket not healthy")
    match = re.search(r"inode=(\d+)", socket_field.detail)
    if not match:
        return _field_status("daemon", "unavailable", "socket inode missing")
    uid = os.getuid() if expected_uid is None else expected_uid
    pid = _pid_bound_to_inode(proc_root, match.group(1), uid)
    if pid is None:
        return _field_status("daemon", "unavailable", "no process bound to control socket")
    exe, parts, cmdline = _read_proc_cmdline(proc_root, pid, canonical_socket=canonical_socket)
    if not exe or not parts or cmdline is None:
        return _field_status("daemon", "unavailable", f"pid={pid} exe/cmdline unsafe")
    if not _is_current_codex_executable(exe, expected_exe):
        return _field_status(
            "daemon", "unavailable", f"pid={pid} executable is not current Codex CLI"
        )
    if canonical_socket is None or not _is_app_server_unix_listener(parts, canonical_socket):
        return _field_status(
            "daemon", "unavailable", f"pid={pid} cmdline is not a Codex unix listener"
        )
    return _field_ok("daemon", f"pid={pid} exe={Path(exe).name} cmdline={cmdline}")


def collect_instruction_state(
    codex_home: Path,
    *,
    read_text: Callable[[Path], str] | None = None,
) -> list[LiveField]:
    """Return the three instruction fields independently, even when a path errors."""
    reader = read_text or (lambda path: path.read_text(encoding="utf-8"))
    agents = codex_home / "AGENTS.md"
    override = codex_home / "AGENTS.override.md"
    if agents.is_file() and not agents.is_symlink():
        canonical = _field_ok("canonical_agents", str(agents))
    elif not agents.exists():
        canonical = _field_status("canonical_agents", "missing", str(agents))
    else:
        canonical = _field_status("canonical_agents", "error", str(agents))
    override_present: bool | None = False
    if not override.exists():
        override_field = _field_ok("agents_override", "absent")
    elif not override.is_file() or override.is_symlink():
        override_field = _field_status("agents_override", "error", str(override))
        override_present = None
    else:
        try:
            override_present = bool(reader(override).strip())
            override_field = _field_ok(
                "agents_override", "present" if override_present else "absent"
            )
        except OSError as error:
            override_present = None
            override_field = _field_status("agents_override", "error", str(error))
    if override_present is True:
        active = _field_ok("active_global_instruction", str(override))
    elif override_present is None:
        active = _field_status("active_global_instruction", "error", "override unreadable")
    elif canonical.status == "ok":
        active = _field_ok("active_global_instruction", str(agents))
    else:
        active = _field_status("active_global_instruction", canonical.status, str(agents))
    return [active, canonical, override_field]


def collect_local_config_path(local_config: Path) -> LiveField:
    if local_config.is_file() and not local_config.is_symlink():
        return _field_ok("local_config", str(local_config.resolve()))
    if not local_config.exists():
        return _field_status("local_config", "missing", str(local_config))
    return _field_status("local_config", "error", "unsafe or unreadable")


def _marker_pair(kind: str) -> tuple[str, str]:
    if kind == "CONFIG":
        return f"# BEGIN {V23_MARKER} CONFIG", f"# END {V23_MARKER} CONFIG"
    return f"<!-- BEGIN {V23_MARKER} {kind} -->", f"<!-- END {V23_MARKER} {kind} -->"


def _toml_object(path: Path) -> dict[str, object] | None:
    try:
        loaded = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return None
    return loaded if isinstance(loaded, dict) else None


def _managed_block(text: str, kind: str) -> tuple[bool, str]:
    begin, end = _marker_pair(kind)
    begins, ends = text.count(begin), text.count(end)
    if begins == ends == 0:
        return False, "absent"
    if begins != 1 or ends != 1 or text.index(end) < text.index(begin) + len(begin):
        return False, f"partial or duplicate {kind.lower()} marker in managed file"
    return True, "present"


def _bridge_file(path: Path) -> tuple[bool, str]:
    if not path.is_file() or path.is_symlink():
        return False, f"missing or unsafe: {path}"
    return True, str(path)


def _role_file_check(
    path: Path, expected_name: str, expected_model: str, expected_effort: str
) -> tuple[bool, str]:
    if not path.is_file() or path.is_symlink():
        return False, f"missing or unsafe: {path}"
    agent = _toml_object(path)
    if agent is None:
        return False, f"invalid TOML: {path}"
    for field in ("name", "description", "developer_instructions"):
        if not isinstance(agent.get(field), str) or not str(agent[field]).strip():
            return False, f"missing {field}: {path}"
    if agent.get("name") != expected_name:
        return False, f"name does not match registration: {path}"
    if (
        agent.get("model") != expected_model
        or agent.get("model_reasoning_effort") != expected_effort
    ):
        return False, f"model mapping does not match local configuration: {path}"
    return True, str(path)


def local_installation_checks(
    codex_home: Path,
    local_config: Path,
    *,
    source_bridge: Path | None = None,
) -> list[tuple[str, bool, str]]:
    """Authoritative bounded local Doctor checks: no GitHub, no required-tool probes."""
    checks: list[tuple[str, bool, str]] = []
    agents = codex_home / "AGENTS.md"
    override = codex_home / "AGENTS.override.md"
    checks.append(
        ("global_agents_canonical", agents.is_file() and not agents.is_symlink(), str(agents))
    )
    override_bad = override.is_symlink() or (override.exists() and not override.is_file())
    override_present = False
    if override.is_file() and not override.is_symlink():
        try:
            override_present = bool(override.read_text(encoding="utf-8").strip())
        except OSError:
            override_present = True
    checks.append(
        ("global_override_absent", not override_bad and not override_present, str(override))
    )
    global_text = ""
    if agents.exists():
        try:
            global_text = agents.read_text(encoding="utf-8")
        except OSError as error:
            global_text = ""
            checks.append(("global_portable", False, str(error)))
            checks.append(("global_local", False, str(error)))
    if not any(name == "global_portable" for name, _ok, _detail in checks):
        for kind in ("PORTABLE", "LOCAL"):
            ok, detail = _managed_block(global_text, kind)
            if kind == "LOCAL" and detail == "absent":
                ok, detail = True, "optional local greeting"
            checks.append((f"global_{kind.lower()}", ok, str(agents) if ok else detail))
    config_path = codex_home / "config.toml"
    config_text = ""
    runtime_config: dict[str, object] = {}
    try:
        config_text = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
        runtime_config = tomllib.loads(config_text) if config_text.strip() else {}
        checks.append(("codex_config_syntax", True, str(config_path)))
    except (OSError, tomllib.TOMLDecodeError) as error:
        checks.append(("codex_config_syntax", False, str(error)))
        runtime_config = {}
    ok, detail = _managed_block(config_text, "CONFIG")
    checks.append(("agent_registration", ok, str(config_path) if ok else detail))
    bootstrap = codex_home / "harness/v23/task_bootstrap.py"
    hooks = runtime_config.get("hooks", {}) if isinstance(runtime_config, dict) else {}
    entries = hooks.get("UserPromptSubmit", []) if isinstance(hooks, dict) else []
    hook_ok = False
    if isinstance(entries, list):
        for group in entries:
            handlers = group.get("hooks", []) if isinstance(group, dict) else []
            if not isinstance(handlers, list):
                continue
            for handler in handlers:
                command = handler.get("command") if isinstance(handler, dict) else None
                if isinstance(command, str) and str(bootstrap) in command:
                    hook_ok = True
    safe_bootstrap = bootstrap.is_file() and not bootstrap.is_symlink()
    checks.append(
        (
            "task_bootstrap_hook",
            hook_ok and safe_bootstrap,
            str(bootstrap)
            if hook_ok and safe_bootstrap
            else "missing or unsafe: " + str(bootstrap),
        )
    )
    skill = codex_home / "skills/engineering-delivery/SKILL.md"
    checks.append(
        ("engineering_delivery_skill", skill.is_file() and not skill.is_symlink(), str(skill))
    )
    grok_skill = codex_home / "skills/grok-execution/SKILL.md"
    lifecycle = codex_home / "skills/grok-execution/references/grok-process-lifecycle.md"
    grok_bridge = codex_home / "bin/grok-execution.py"
    source = (
        source_bridge
        if source_bridge is not None
        else Path(__file__).resolve().parent / "grok_execution.py"
    )
    installed_ok, installed_detail = _bridge_file(grok_bridge)
    source_exists = source.is_file()
    source_ok, source_detail = (
        _bridge_file(source) if source_exists or source_bridge is not None else (True, "unrequired")
    )
    if source_bridge is None and not source_exists:
        source_ok, source_detail = True, "unrequired"
    checks.append(
        (
            "grok_execution_route",
            grok_skill.is_file() and not grok_skill.is_symlink() and installed_ok and source_ok,
            f"{grok_skill}; {installed_detail}; {source_detail}",
        )
    )
    bounded = codex_home / "bin/bounded-search.py"
    bounded_ok, bounded_detail = _bridge_file(bounded)
    checks.append(("bounded_search", bounded_ok, bounded_detail))
    life_ok, life_detail = _bridge_file(lifecycle)
    checks.append(("grok_process_lifecycle", life_ok, life_detail))
    try:
        local = tomllib.loads(local_config.read_text(encoding="utf-8"))
    except FileNotFoundError:
        checks.append(("local_config", False, "missing"))
        return checks
    except (OSError, tomllib.TOMLDecodeError) as error:
        checks.append(("local_config", False, f"corrupt:{error}"))
        return checks
    if not isinstance(local, dict):
        checks.append(("local_config", False, "local configuration is not a table"))
        return checks
    models = local.get("models", {})
    configured = isinstance(models, dict) and all(
        models.get(key) for key in ("primary", "executor", "reviewer")
    )
    routing_ok = True
    routing_detail = "legacy grok-preferred"
    require_grok = True
    policy = None
    try:
        policy = parse_policy(local)
        require_grok = grok_checks_required(policy)
        routing_detail = getattr(policy, "selection", "legacy")
        if getattr(policy, "legacy", False):
            routing_detail = "legacy grok-preferred paid_strict"
    except RoutingError as error:
        routing_ok = False
        routing_detail = str(error)
        require_grok = True
    checks.append(("executor_routing", routing_ok, routing_detail))
    routing_pairs = (
        (
            codex_home / "bin/executor-routing.py",
            codex_home / "bin/runtime.py",
        ),
        (
            codex_home / "harness/v23/executor_routing.py",
            codex_home / "harness/v23/runtime.py",
        ),
    )
    entry_ok = True
    entry_details: list[str] = []
    for helper, runtime in routing_pairs:
        helper_ok, helper_detail = _bridge_file(helper)
        runtime_ok, runtime_detail = _bridge_file(runtime)
        if not helper_ok or not runtime_ok:
            entry_ok = False
        entry_details.append(f"{helper_detail}; {runtime_detail}")
    checks.append(("executor_routing_entrypoint", entry_ok, "; ".join(entry_details)))
    if not require_grok:
        rewritten: list[tuple[str, bool, str]] = []
        for name, ok, detail in checks:
            if name in {"grok_execution_route", "grok_process_lifecycle"}:
                rewritten.append(
                    (name, True, "unrequired for current routing" if not ok else detail)
                )
            else:
                rewritten.append((name, ok, detail))
        checks[:] = rewritten
    checks.append(("local_config", configured, str(local_config)))
    try:
        delivery = parse_delivery(local)
        delivery_ok = True
        delivery_detail = f"{getattr(delivery, 'mode', 'local_only')}"
        if getattr(delivery, "repositories", None):
            delivery_detail += f" repos={len(delivery.repositories)}"
    except DeliveryError as error:
        delivery_ok = False
        delivery_detail = str(error)
    checks.append(("delivery_policy", delivery_ok, delivery_detail))
    opening = local.get("opening", {})
    if opening is None:
        opening = {}
    instruction = opening.get("instruction", "") if isinstance(opening, dict) else None
    opening_ok = isinstance(opening, dict) and (instruction is None or isinstance(instruction, str))
    checks.append(
        (
            "local_opening",
            opening_ok,
            "optional local greeting"
            if opening_ok and not (isinstance(instruction, str) and instruction.strip())
            else "configured outside the repository",
        )
    )
    if configured and isinstance(models, dict):
        profile_path = codex_home / "v23-primary.config.toml"
        profile = _toml_object(profile_path)
        profile_ok = bool(
            profile
            and profile.get("model") == models["primary"]
            and profile.get("model_reasoning_effort")
            == models.get("primary_effort", DEFAULT_PRIMARY_EFFORT)
            and profile.get("review_model") == models["reviewer"]
        )
        checks.append(("primary_profile", profile_ok, str(profile_path)))
        registered = runtime_config.get("agents", {}) if isinstance(runtime_config, dict) else {}
        native = default_native_spec(policy) if policy is not None else None
        executor_model = native.actual_model if native is not None else str(models.get("executor"))
        executor_effort = models.get("executor_effort", DEFAULT_EXECUTOR_EFFORT)
        for filename, name, expected_model, effort in (
            (
                "v23-executor.toml",
                "v23_executor",
                executor_model,
                executor_effort,
            ),
            ("v23-reviewer.toml", "v23_reviewer", models["reviewer"], "high"),
        ):
            agent_path = codex_home / "agents" / filename
            ok, detail = _role_file_check(agent_path, name, str(expected_model), str(effort))
            configured_agent = registered.get(name, {}) if isinstance(registered, dict) else {}
            registration_ok = (
                isinstance(configured_agent, dict)
                and configured_agent.get("config_file") == f"agents/{filename}"
            )
            checks.append((f"agent_{name}", ok and registration_ok, detail))
        if policy is not None:
            for spec in policy.executors:
                if spec.backend != BACKEND_CODEX:
                    continue
                name = executor_agent_name(spec, policy)
                if name == "v23_executor":
                    continue
                rel = executor_agent_file(spec, policy)
                agent_path = codex_home / rel
                ok, detail = _role_file_check(
                    agent_path, name, spec.actual_model, str(executor_effort)
                )
                configured_agent = registered.get(name, {}) if isinstance(registered, dict) else {}
                registration_ok = (
                    isinstance(configured_agent, dict)
                    and configured_agent.get("config_file") == rel
                )
                checks.append((f"agent_{name}", ok and registration_ok, detail))
    tools = local.get("tools", {})
    if tools is None:
        checks.append(("tools_config", True, "optional tools not configured"))
    elif not isinstance(tools, dict):
        checks.append(("tools_config", False, "[tools] is not a TOML table"))
    else:
        missing = [name for name in REQUIRED_TOOLS if not tools.get(name)]
        checks.append(
            (
                "tools_config",
                True,
                "all optional tools configured"
                if not missing
                else f"optional tools absent: {', '.join(missing)}",
            )
        )
    return checks


def doctor_subset(
    codex_home: Path, local_config: Path, *, source_bridge: Path | None = None
) -> list[tuple[str, bool, str]]:
    """Compatibility alias for the shared bounded local Doctor checks."""
    return local_installation_checks(codex_home, local_config, source_bridge=source_bridge)


def collect_doctor_summary(codex_home: Path, local_config: Path) -> LiveField:
    """Summarize the bounded Doctor subset; never re-enters the full hook."""
    checks = doctor_subset(codex_home, local_config)
    failed = [name for name, ok, _detail in checks if not ok]
    detail = f"ok={not failed} checks={len(checks)} failed={','.join(failed) or 'none'}"
    return _field_ok("doctor", detail) if not failed else _field_status("doctor", "error", detail)


def collect_live_runtime_state(
    *,
    cwd: Path,
    local_config: Path,
    codex_home: Path,
    state_dir: Path,
    runner: CommandRunner | None = None,
    memory_state: object = None,
    proc_net_unix: str | None = None,
    proc_root: Path = Path("/proc"),
    expected_uid: int | None = None,
    probe_daemons: bool = False,
) -> list[LiveField]:
    """Collect bounded live fields. ``memory_state`` is ignored on purpose.

    Default path is installed policy/config integrity. Daemon/CLI probes run
    only when ``probe_daemons`` is true (Doctor or an explicit relevant path).
    """
    del cwd
    del memory_state
    runner = runner or _system_runner
    fields: list[LiveField] = []
    fields.extend(collect_install_state(state_dir))
    fields.extend(collect_instruction_state(codex_home))
    fields.append(collect_local_config_path(local_config))
    fields.append(collect_doctor_summary(codex_home, local_config))
    if probe_daemons:
        version_fields, managed = collect_version_json(runner)
        fields.extend(version_fields)
        canonical = canonical_control_socket(codex_home)
        socket_field = collect_control_socket(codex_home, proc_net_unix, expected_uid=expected_uid)
        fields.append(socket_field)
        fields.append(
            collect_bound_daemon(
                socket_field,
                proc_root,
                expected_uid=expected_uid,
                expected_exe=_expected_codex_executable(managed),
                canonical_socket=canonical,
            )
        )
    else:
        fields.append(
            _field_status(
                "daemon_probes",
                "skipped",
                "explicit Doctor/--probe-daemons or relevant diagnosis path",
            )
        )
    return fields


def render_live_state(fields: list[LiveField], *, cap: int = LIVE_STATE_CONTEXT_CAP) -> str:
    header = (
        "V23 live runtime state (install manifest and instruction/config checks; "
        "daemon probes are explicit; memory of prior tasks is historical only): "
    )
    body = "; ".join(field.render() for field in fields)
    text = SECRET_FRAGMENT.sub("[redacted]", header + body)
    if len(text) > cap:
        return text[: cap - 15] + "...[truncated]"
    return text


def _hook_context(live_state: str = "") -> str:
    header = (
        "V23 prompt hook injected installed instructions and local integrity checks. "
        "CodeGraph, Semble, RTK, and daemon probes are explicit or task-relevant; "
        "code investigation or change checks the owner CodeGraph index before "
        "exploration and cross-file callers/dependencies/impact uses a focused "
        "structural query; a missing/failed tool or read-only missing index states "
        "the short bounded-search/raw fallback. Tool failure must not block "
        "unrelated work. Doctor diagnosis remains available."
    )
    combined = f"{header} {live_state}".strip()
    if len(combined) > HOOK_CONTEXT_CAP:
        return combined[: HOOK_CONTEXT_CAP - 15] + "...[truncated]"
    return combined


def run_hook(
    cwd: Path,
    prompt: str,
    local_config: Path,
    *,
    codex_home: Path | None = None,
    state_dir: Path | None = None,
    runner: CommandRunner | None = None,
    memory_state: object = None,
    proc_net_unix: str | None = None,
) -> dict[str, object]:
    """Return the documented UserPromptSubmit hook response."""
    del prompt
    home = (codex_home or Path.home() / ".codex").expanduser()
    state = (state_dir or home / "harness/v23-state").expanduser()
    try:
        live = collect_live_runtime_state(
            cwd=cwd,
            local_config=local_config,
            codex_home=home,
            state_dir=state,
            runner=runner,
            memory_state=memory_state,
            proc_net_unix=proc_net_unix,
            probe_daemons=False,
        )
        live_text = render_live_state(live)
    except (OSError, ValueError, TypeError) as error:
        live_text = f"V23 live runtime state error:{_privacy_text(str(error))}"
    return {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": _hook_context(live_text),
        }
    }


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--local-config", type=Path, default=Path.home() / ".config/codex-harness/local.toml"
    )
    parser.add_argument("--codex-home", type=Path, default=Path.home() / ".codex")
    parser.add_argument("--state-dir", type=Path, default=Path.home() / ".codex/harness/v23-state")
    parser.add_argument("--cwd", type=Path)
    parser.add_argument("--prompt")
    args = parser.parse_args(argv)
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        payload = {}
    cwd = args.cwd or Path(str(payload.get("cwd") or Path.cwd()))
    prompt = args.prompt if args.prompt is not None else str(payload.get("prompt") or "")
    print(
        json.dumps(
            run_hook(
                cwd,
                prompt,
                args.local_config,
                codex_home=args.codex_home,
                state_dir=args.state_dir,
            ),
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
