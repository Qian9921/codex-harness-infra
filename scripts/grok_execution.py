"""Run Grok Build's pinned execution model with Codex-safe receipts."""

from __future__ import annotations

import argparse
import concurrent.futures
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
from collections.abc import Sequence
from typing import Any

REQUESTED_MODEL = "grok-4.6"
ACTUAL_MODEL = "grok-4.6-build"
EXECUTION_EFFORT = "low"
SCHEMA = "codex-external-execution.v1"
BATCH_SCHEMA = "codex-external-execution-batch.v1"
DEFAULT_TIMEOUT_SECONDS = None
POLL_INTERVAL_SECONDS = 1.0
DRAIN_TIMEOUT_SECONDS = 1.0
MAX_PARALLEL = 2
_SIGNAL_PGID_SLOTS = 8
_REGISTERED_PGIDS = [0] * _SIGNAL_PGID_SLOTS
_TERMINATION_SIGNALS = (signal.SIGTERM, signal.SIGHUP, signal.SIGINT)
_REGISTRY_LOCK = threading.Lock()
_TERMINATING = False
_termination_handlers_installed = False
_SPAWN_BOUNDARY_HOOK: Any = None
CHILD_LAUNCHER_FLAG = "--v23-exec-child"


class BridgeError(RuntimeError):
    """Raised when Grok cannot produce a trustworthy result."""


class QuotaExhausted(BridgeError):
    """Raised only when Grok reports an explicit account-usage exhaustion."""

    def __init__(self, detail: str, receipt: dict[str, Any]) -> None:
        super().__init__(detail)
        self.receipt = receipt


QUOTA_EXHAUSTION_MARKERS = (
    "quota exhausted",
    "quota exceeded",
    "insufficient_quota",
    "usage limit reached",
    "weekly limit reached",
    "monthly limit reached",
    "out of credits",
    "no credits remaining",
    "credit balance is too low",
)


def _is_quota_exhaustion(detail: str) -> bool:
    normalized = " ".join(detail.casefold().split())
    return any(marker in normalized for marker in QUOTA_EXHAUSTION_MARKERS)


def _grok_binary() -> str:
    candidate = os.environ.get("GROK_BIN") or shutil.which("grok")
    if not candidate:
        raise BridgeError("grok executable was not found")
    path = pathlib.Path(candidate).expanduser()
    if not path.is_file() or not os.access(path, os.X_OK):
        raise BridgeError("grok executable is not an executable file")
    return str(path.resolve())


def _directory(value: str) -> pathlib.Path:
    path = pathlib.Path(value).expanduser().resolve()
    if not path.is_dir():
        raise BridgeError(f"working directory is not a directory: {value}")
    return path


def _required_task_id(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BridgeError("task-id is required and must be nonempty")
    return value


def _prompt(args: argparse.Namespace) -> str:
    if args.prompt is not None:
        value = args.prompt
    elif args.prompt_file is not None:
        value = pathlib.Path(args.prompt_file).expanduser().read_text(encoding="utf-8")
    else:
        value = sys.stdin.read()
    if not value.strip():
        raise BridgeError("execution prompt is empty")
    return value


def _resume_binding(
    args: argparse.Namespace,
    cwd: pathlib.Path,
    owned_paths: Sequence[str],
    task_id: str,
) -> None:
    if args.session is None:
        return
    try:
        receipt = json.loads(pathlib.Path(args.receipt).expanduser().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BridgeError(f"invalid resume receipt: {exc}") from exc
    expected = {
        "schema": SCHEMA,
        "status": "SUCCESS",
        "conversation_id": args.session,
        "working_directory": str(cwd),
        "task_id": task_id,
        "owned_paths": list(owned_paths),
        "requested_model": REQUESTED_MODEL,
        "actual_model": ACTUAL_MODEL,
    }
    mismatched = [key for key, value in expected.items() if receipt.get(key) != value]
    if mismatched:
        raise BridgeError("resume receipt binding mismatch: " + ", ".join(mismatched))


def _extract_result(stdout: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    candidates: list[dict[str, Any]] = []
    for offset, character in enumerate(stdout):
        if character != "{":
            continue
        try:
            value, _end = decoder.raw_decode(stdout[offset:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and "sessionId" in value and "stopReason" in value:
            candidates.append(value)
    if not candidates:
        raise BridgeError("grok did not return a structured session result")
    return candidates[-1]


def _owned_paths(cwd: pathlib.Path, values: Sequence[str]) -> list[str]:
    owned: list[str] = []
    for value in values:
        raw = pathlib.Path(value)
        candidate = (cwd / raw).resolve() if not raw.is_absolute() else raw.resolve()
        try:
            relative = candidate.relative_to(cwd)
        except ValueError as exc:
            raise BridgeError(f"owned path escapes working directory: {value}") from exc
        normalized = relative.as_posix()
        if normalized in {"", "."}:
            raise BridgeError("owned path must be narrower than the working directory")
        owned.append(normalized)
    if not owned:
        raise BridgeError("at least one owned-path is required")
    if len(owned) != len(set(owned)):
        raise BridgeError("owned paths must be unique")
    return sorted(owned)


def _bound_prompt(
    prompt: str,
    cwd: pathlib.Path,
    *,
    task_id: str,
    owned_paths: Sequence[str],
) -> str:
    return (
        "You are grok_execution, the preferred external execution lead managed by Codex.\n"
        f"Authoritative working directory: {cwd}\n"
        f"Task ID: {task_id}\n"
        "Exclusive writable paths: " + ", ".join(owned_paths) + "\n"
        "Work only in that directory. Preserve unrelated changes, do not commit unless "
        "explicitly authorized, run decision-changing checks, and report exact changed "
        "paths, checks, limitations, requested model, actual model, and session ID.\n\n"
        "TASK\n"
        f"{prompt}"
    )


def _quota_receipt(
    *,
    cwd: pathlib.Path,
    task_id: str,
    owned_paths: Sequence[str],
    actual_model: str | None = None,
) -> dict[str, Any]:
    receipt: dict[str, Any] = {
        "schema": SCHEMA,
        "status": "QUOTA_EXHAUSTED",
        "task_id": task_id,
        "working_directory": str(cwd),
        "owned_paths": list(owned_paths),
        "requested_model": REQUESTED_MODEL,
        "fallback_reason": "grok_quota_exhausted",
    }
    if actual_model:
        receipt["actual_model"] = actual_model
    return receipt


def _command(
    *,
    binary: str,
    cwd: pathlib.Path,
    prompt_file: pathlib.Path,
    session_id: str | None,
    effort: str,
    mode: str,
) -> list[str]:
    if effort != EXECUTION_EFFORT:
        raise BridgeError(f"execution effort must be {EXECUTION_EFFORT}")
    command = [
        binary,
        "--cwd",
        str(cwd),
        "--model",
        REQUESTED_MODEL,
        "--reasoning-effort",
        effort,
        "--permission-mode",
        "plan" if mode == "plan" else "bypassPermissions",
        "--output-format",
        "json",
        "--no-subagents",
        "--prompt-file",
        str(prompt_file),
    ]
    if session_id:
        command.extend(("--resume", session_id))
    return command


def _positive_timeout(value: Any, *, label: str = "timeout") -> int | None:
    if value is None:
        return None
    if type(value) is not int or value <= 0:
        raise BridgeError(f"{label} must be a positive integer when set")
    return value


def _proc_state(pid: int) -> str | None:
    try:
        stat = pathlib.Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return None
    close = stat.rfind(")")
    if close == -1:
        return None
    fields = stat[close + 2 :].split()
    if not fields:
        return None
    return fields[0]


def default_is_alive(proc: subprocess.Popen[str]) -> bool:
    if proc.poll() is not None:
        return False
    return _proc_state(proc.pid) != "Z"


def default_is_zombie(proc: subprocess.Popen[str]) -> bool:
    if proc.poll() is not None:
        return False
    return _proc_state(proc.pid) == "Z"


def _record_dedicated_pgid(proc: Any) -> None:
    pid = getattr(proc, "pid", None)
    pgid: int | None = None
    if type(pid) is int and pid > 1:
        try:
            pgid = os.getpgid(pid)
        except OSError:
            pgid = None
        if pgid != pid or pgid in (os.getpid(), os.getpgrp()):
            pgid = None
    proc._v23_pgid = pgid


def _child_reset_inherited_signal_mask() -> None:
    for signum in _TERMINATION_SIGNALS:
        signal.signal(signum, signal.SIG_DFL)
    for name in ("SIGPIPE", "SIGXFSZ"):
        signum = getattr(signal, name, None)
        if isinstance(signum, int):
            signal.signal(signum, signal.SIG_DFL)
    signal.pthread_sigmask(signal.SIG_UNBLOCK, _TERMINATION_SIGNALS)


def _module_path() -> pathlib.Path:
    return pathlib.Path(__file__).resolve()


def _validate_child_launcher_target(command: Sequence[str]) -> list[str]:
    if not command:
        raise BridgeError("child launcher command is empty")
    argv = [str(item) for item in command]
    if not argv[0] or argv[0].startswith("-"):
        raise BridgeError("child launcher command is invalid")
    target = pathlib.Path(argv[0])
    if (target.is_absolute() or "/" in argv[0]) and (
        not target.is_file() or not os.access(target, os.X_OK)
    ):
        raise BridgeError("child launcher target is not an executable file")
    return argv


def _child_launcher_argv(command: Sequence[str]) -> list[str]:
    launcher = _module_path()
    if launcher.name not in {"grok_execution.py", "grok-execution.py"} or not launcher.is_file():
        raise BridgeError("child launcher path is invalid")
    return [
        sys.executable,
        str(launcher),
        CHILD_LAUNCHER_FLAG,
        "--",
        *_validate_child_launcher_target(command),
    ]


def _run_child_launcher(argv: Sequence[str]) -> None:
    if not argv or argv[0] != "--" or len(argv) < 2:
        raise SystemExit("invalid child launcher arguments")
    target = [str(item) for item in argv[1:]]
    _child_reset_inherited_signal_mask()
    os.execvpe(target[0], target, os.environ)


class _SpawnCleanupToken:
    """Internal spawn-issued cleanup ownership.

    Invariant: `_spawn_grok` always uses `start_new_session=True`, so the
    dedicated candidate PGID equals `proc.pid` at Popen success and does not
    depend on a later `os.getpgid` read. This type is constructed only from
    the live child; callers cannot supply an untrusted PGID. Public registry
    and signal cleanup still require `os.getpgid(proc.pid) == proc.pid`.
    """

    __slots__ = ("_candidate_pgid", "_proc")

    def __init__(self, proc: Any) -> None:
        pid = getattr(proc, "pid", None)
        if type(pid) is not int or pid <= 1:
            raise BridgeError("dedicated process group validation failed")
        self._proc = proc
        self._candidate_pgid = pid

    def kill_candidate_group(self) -> None:
        candidate = self._candidate_pgid
        if (
            type(candidate) is int
            and candidate > 1
            and candidate not in (os.getpid(), os.getpgrp())
        ):
            try:
                os.killpg(candidate, signal.SIGKILL)
            except OSError:
                pass
        try:
            self._proc.kill()
        except (OSError, AttributeError):
            pass


def _spawn_grok(command: Sequence[str], cwd: pathlib.Path) -> subprocess.Popen[str]:
    proc = subprocess.Popen(
        _child_launcher_argv(command),
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    proc._v23_cleanup_token = _SpawnCleanupToken(proc)
    _record_dedicated_pgid(proc)
    return proc


def _snapshot_registered_pgids() -> list[int]:
    snapshot: list[int] = []
    for index in range(_SIGNAL_PGID_SLOTS):
        pgid = _REGISTERED_PGIDS[index]
        if type(pgid) is int and pgid > 1:
            snapshot.append(pgid)
    return snapshot


def _kill_registered_dedicated_groups(pgids: Sequence[int] | None = None) -> None:
    for pgid in list(pgids) if pgids is not None else _snapshot_registered_pgids():
        if type(pgid) is not int or pgid <= 1:
            continue
        try:
            os.killpg(pgid, signal.SIGKILL)
        except OSError:
            pass


def _termination_coordinator() -> None:
    global _TERMINATING
    signum = signal.sigwait(set(_TERMINATION_SIGNALS))
    with _REGISTRY_LOCK:
        _TERMINATING = True
        snapshot = _snapshot_registered_pgids()
    _kill_registered_dedicated_groups(snapshot)
    signal.pthread_sigmask(signal.SIG_UNBLOCK, {signum})
    os.kill(os.getpid(), signum)
    os._exit(128 + int(signum))


def install_termination_handlers() -> None:
    global _termination_handlers_installed
    if _termination_handlers_installed:
        return
    signal.pthread_sigmask(signal.SIG_BLOCK, _TERMINATION_SIGNALS)
    threading.Thread(
        target=_termination_coordinator,
        name="v23-grok-sigwait",
        daemon=True,
    ).start()
    _termination_handlers_installed = True


def _register_dedicated_pgid_unlocked(pgid: int) -> None:
    if type(pgid) is not int or pgid <= 1:
        raise BridgeError("dedicated process group validation failed")
    for index in range(_SIGNAL_PGID_SLOTS):
        if _REGISTERED_PGIDS[index] == 0:
            _REGISTERED_PGIDS[index] = pgid
            return
    raise BridgeError("dedicated process group registry is full")


def _register_dedicated_pgid(pgid: int) -> None:
    with _REGISTRY_LOCK:
        if _TERMINATING:
            raise BridgeError("process is terminating")
        _register_dedicated_pgid_unlocked(pgid)


def _unregister_dedicated_pgid(pgid: int) -> None:
    if type(pgid) is not int or pgid <= 1:
        return
    with _REGISTRY_LOCK:
        for index in range(_SIGNAL_PGID_SLOTS):
            if _REGISTERED_PGIDS[index] == pgid:
                _REGISTERED_PGIDS[index] = 0
                return


def _owns_dedicated_group(proc: Any) -> bool:
    expected = getattr(proc, "_v23_pgid", None)
    pid = getattr(proc, "pid", None)
    if type(expected) is not int or expected <= 1:
        return False
    if type(pid) is not int or expected != pid:
        return False
    if expected in (os.getpid(), os.getpgrp()):
        return False
    try:
        current = os.getpgid(pid)
    except OSError:
        current = expected
    return current == expected


def _terminate_dedicated_group(proc: Any) -> None:
    if _owns_dedicated_group(proc):
        try:
            os.killpg(proc._v23_pgid, signal.SIGKILL)
        except OSError:
            pass
    try:
        proc.kill()
    except (OSError, AttributeError):
        pass


def _kill_spawned_group(proc: Any) -> None:
    if _owns_dedicated_group(proc):
        _terminate_dedicated_group(proc)
        return
    token = getattr(proc, "_v23_cleanup_token", None)
    if isinstance(token, _SpawnCleanupToken):
        token.kill_candidate_group()
        return
    _terminate_dedicated_group(proc)


def _bounded_reap(proc: Any, timeout: float = DRAIN_TIMEOUT_SECONDS) -> tuple[str, str]:
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_spawned_group(proc)
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except (OSError, ValueError, subprocess.TimeoutExpired, AttributeError):
            stdout, stderr = "", ""
    except (OSError, ValueError, AttributeError):
        stdout, stderr = "", ""
    return stdout or "", stderr or ""


def _stop_group_and_reap(proc: Any) -> tuple[str, str]:
    _kill_spawned_group(proc)
    return _bounded_reap(proc)


def _supervised_run(
    command: Sequence[str],
    cwd: pathlib.Path,
    *,
    timeout: int | None,
    spawn: Any = _spawn_grok,
    is_alive: Any = default_is_alive,
    is_zombie: Any = default_is_zombie,
    sleep: Any = time.sleep,
    clock: Any = time.monotonic,
    poll_interval: float = POLL_INTERVAL_SECONDS,
) -> subprocess.CompletedProcess[str]:
    proc: Any = None
    pgid: int | None = None
    registered = False
    completed: subprocess.CompletedProcess[str] | None = None
    try:
        with _REGISTRY_LOCK:
            if _TERMINATING:
                raise BridgeError("process is terminating")
            proc = spawn(command, cwd)
            if not hasattr(proc, "_v23_pgid") or getattr(proc, "_v23_pgid", None) is None:
                _record_dedicated_pgid(proc)
            if not _owns_dedicated_group(proc):
                raise BridgeError("dedicated process group validation failed")
            hook = _SPAWN_BOUNDARY_HOOK
            if hook is not None:
                hook()
            pgid = proc._v23_pgid
            _register_dedicated_pgid_unlocked(pgid)
            registered = True
        started = clock()
        while True:
            remaining: float | None = None
            if timeout is not None:
                remaining = timeout - (clock() - started)
                if remaining <= 0:
                    raise BridgeError(f"grok exceeded the {timeout}s bridge timeout")
            slice_timeout = poll_interval if timeout is None else min(poll_interval, remaining)
            try:
                stdout, stderr = proc.communicate(timeout=slice_timeout)
            except subprocess.TimeoutExpired:
                code = proc.poll()
                if code is not None:
                    stdout, stderr = _stop_group_and_reap(proc)
                    completed = subprocess.CompletedProcess(list(command), code, stdout, stderr)
                    return completed
                if is_zombie(proc):
                    raise BridgeError("grok child process became a zombie") from None
                if not is_alive(proc):
                    raise BridgeError("grok child process died before returning a result") from None
                sleep(0)
                continue
            code = proc.returncode
            if code is None:
                code = 0
            completed = subprocess.CompletedProcess(list(command), code, stdout or "", stderr or "")
            return completed
    finally:
        if proc is not None:
            try:
                if completed is None:
                    _stop_group_and_reap(proc)
                else:
                    _terminate_dedicated_group(proc)
                    _bounded_reap(proc)
            finally:
                if registered and pgid is not None:
                    _unregister_dedicated_pgid(pgid)


def _write_prompt_file(content: str) -> pathlib.Path:
    fd, name = tempfile.mkstemp(prefix="v23-grok-prompt-", suffix=".txt", text=True)
    path = pathlib.Path(name)
    handle = None
    try:
        os.fchmod(fd, 0o600)
        handle = os.fdopen(fd, "w", encoding="utf-8")
        handle.write(content)
        handle.close()
        return path
    except Exception:
        if handle is not None:
            handle.close()
        else:
            try:
                os.close(fd)
            except OSError:
                pass
        path.unlink(missing_ok=True)
        raise


def _run(args: argparse.Namespace) -> dict[str, Any]:
    cwd = _directory(args.cwd)
    task_id = _required_task_id(getattr(args, "task_id", None))
    owned_paths = _owned_paths(cwd, getattr(args, "owned_path", ()))
    _resume_binding(args, cwd, owned_paths, task_id)
    bound = _bound_prompt(
        _prompt(args),
        cwd,
        task_id=task_id,
        owned_paths=owned_paths,
    )
    prompt_path: pathlib.Path | None = None
    started = time.monotonic()
    try:
        prompt_path = _write_prompt_file(bound)
        command = _command(
            binary=_grok_binary(),
            cwd=cwd,
            prompt_file=prompt_path,
            session_id=args.session,
            effort=args.effort,
            mode=args.mode,
        )
        completed = _supervised_run(
            command,
            cwd,
            timeout=_positive_timeout(getattr(args, "timeout", None)),
        )
    finally:
        if prompt_path is not None:
            prompt_path.unlink(missing_ok=True)
    wall_seconds = time.monotonic() - started
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        if _is_quota_exhaustion(detail):
            raise QuotaExhausted(
                "Grok account quota is exhausted",
                _quota_receipt(cwd=cwd, task_id=task_id, owned_paths=owned_paths),
            )
        raise BridgeError(f"grok failed with exit code {completed.returncode}: {detail}")
    result = _extract_result(completed.stdout)
    if result.get("stopReason") != "end_turn":
        raise BridgeError(f"grok returned non-success stop reason: {result.get('stopReason')!r}")
    model_usage = result.get("modelUsage")
    actual_usage = model_usage.get(ACTUAL_MODEL) if isinstance(model_usage, dict) else None
    if (
        not isinstance(model_usage, dict)
        or set(model_usage) != {ACTUAL_MODEL}
        or not isinstance(actual_usage, dict)
        or type(actual_usage.get("modelCalls")) is not int
        or actual_usage["modelCalls"] <= 0
    ):
        raise BridgeError("grok result does not prove the pinned runtime model")
    session_id = result.get("sessionId")
    if not isinstance(session_id, str) or not session_id:
        raise BridgeError("grok result lacks a valid session ID")
    if args.session is not None and session_id != args.session:
        raise BridgeError("grok resume returned a different session ID")
    return {
        "schema": SCHEMA,
        "status": "SUCCESS",
        "provider": "grok-build-cli",
        "requested_model": REQUESTED_MODEL,
        "actual_model": ACTUAL_MODEL,
        "working_directory": str(cwd),
        "task_id": task_id,
        "owned_paths": owned_paths,
        "conversation_id": session_id,
        "continued": args.session is not None,
        "wall_seconds": round(wall_seconds, 6),
        "usage": result.get("usage"),
        "cost_usd": result.get("total_cost_usd"),
        "response": result.get("text", ""),
    }


def _batch_task(value: Any) -> argparse.Namespace:
    if not isinstance(value, dict):
        raise BridgeError("each batch task must be an object")
    required = {"id", "cwd", "prompt", "owned_paths"}
    allowed = required | {"effort", "timeout"}
    if set(value) - allowed or not required.issubset(value):
        raise BridgeError("batch task fields must be id, cwd, prompt, owned_paths, effort, timeout")
    task_id = value["id"]
    if not isinstance(task_id, str) or not task_id.strip():
        raise BridgeError("batch task id must be a nonempty string")
    if not isinstance(value["prompt"], str) or not value["prompt"].strip():
        raise BridgeError(f"batch task {task_id!r} has an empty prompt")
    if (
        not isinstance(value["owned_paths"], list)
        or not value["owned_paths"]
        or not all(isinstance(item, str) and item for item in value["owned_paths"])
    ):
        raise BridgeError(f"batch task {task_id!r} requires nonempty owned_paths")
    effort = value.get("effort", "low")
    timeout = value.get("timeout", DEFAULT_TIMEOUT_SECONDS)
    if effort != EXECUTION_EFFORT:
        raise BridgeError(f"batch task {task_id!r} has invalid effort or timeout")
    try:
        timeout = _positive_timeout(timeout, label=f"batch task {task_id!r} timeout")
    except BridgeError as exc:
        raise BridgeError(f"batch task {task_id!r} has invalid effort or timeout") from exc
    return argparse.Namespace(
        cwd=value["cwd"],
        prompt=value["prompt"],
        prompt_file=None,
        session=None,
        effort=effort,
        mode="accept-edits",
        timeout=timeout,
        task_id=task_id,
        owned_path=value["owned_paths"],
    )


def _assert_exclusive(tasks: Sequence[argparse.Namespace]) -> None:
    claims: list[tuple[pathlib.Path, pathlib.Path, str]] = []
    for task in tasks:
        cwd = _directory(task.cwd)
        for relative in _owned_paths(cwd, task.owned_path):
            claim = (cwd / relative).resolve()
            for _other_cwd, other_claim, other_id in claims:
                overlaps = (
                    claim == other_claim
                    or claim in other_claim.parents
                    or other_claim in claim.parents
                )
                if overlaps:
                    raise BridgeError(
                        f"overlapping owned paths for tasks {other_id!r} and {task.task_id!r}"
                    )
            claims.append((cwd, claim, task.task_id))


def _batch(args: argparse.Namespace) -> dict[str, Any]:
    try:
        value = json.loads(pathlib.Path(args.manifest).expanduser().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BridgeError(f"invalid batch manifest: {exc}") from exc
    if (
        not isinstance(value, dict)
        or set(value) != {"tasks"}
        or not isinstance(value["tasks"], list)
    ):
        raise BridgeError("batch manifest must contain exactly one tasks array")
    tasks = [_batch_task(item) for item in value["tasks"]]
    if not tasks:
        raise BridgeError("batch manifest has no tasks")
    ids = [task.task_id for task in tasks]
    if len(ids) != len(set(ids)):
        raise BridgeError("batch task ids must be unique")
    _assert_exclusive(tasks)

    def execute(task: argparse.Namespace) -> dict[str, Any]:
        try:
            return _run(task)
        except QuotaExhausted as exc:
            return dict(exc.receipt)
        except (BridgeError, OSError, UnicodeError, json.JSONDecodeError) as exc:
            return {
                "schema": SCHEMA,
                "status": "ERROR",
                "task_id": task.task_id,
                "working_directory": str(_directory(task.cwd)),
                "owned_paths": _owned_paths(_directory(task.cwd), task.owned_path),
                "error": str(exc),
            }

    workers = min(args.max_parallel, len(tasks))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        receipts = list(executor.map(execute, tasks))
    success = all(receipt["status"] == "SUCCESS" for receipt in receipts)
    return {
        "schema": BATCH_SCHEMA,
        "status": "SUCCESS" if success else "ERROR",
        "max_parallel": workers,
        "receipts": receipts,
    }


def _doctor(_args: argparse.Namespace) -> dict[str, Any]:
    binary = _grok_binary()
    version = subprocess.run(
        [binary, "--version"], capture_output=True, text=True, timeout=30, check=False
    )
    models = subprocess.run(
        [binary, "models"], capture_output=True, text=True, timeout=60, check=False
    )
    if version.returncode != 0 or models.returncode != 0:
        raise BridgeError("grok version/model discovery failed")
    if f"{REQUESTED_MODEL} (default)" not in models.stdout:
        raise BridgeError(f"required model is unavailable: {REQUESTED_MODEL}")
    return {
        "schema": SCHEMA,
        "status": "DISCOVERED",
        "provider": "grok-build-cli",
        "cli_binary": binary,
        "cli_version": version.stdout.strip(),
        "requested_model": REQUESTED_MODEL,
        "actual_model": ACTUAL_MODEL,
        "execution_probe_required": True,
    }


def _add_execution_arguments(parser: argparse.ArgumentParser, *, resume: bool) -> None:
    parser.add_argument("--cwd", default=os.getcwd())
    parser.add_argument("--effort", choices=(EXECUTION_EFFORT,), default=EXECUTION_EFFORT)
    parser.add_argument("--mode", choices=("accept-edits",), default="accept-edits")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    prompt = parser.add_mutually_exclusive_group()
    prompt.add_argument("--prompt")
    prompt.add_argument("--prompt-file")
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--owned-path", action="append", required=True)
    if resume:
        parser.add_argument("--session", required=True)
        parser.add_argument("--receipt", required=True)
    else:
        parser.set_defaults(session=None)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run")
    _add_execution_arguments(run_parser, resume=False)
    resume_parser = subparsers.add_parser("resume")
    _add_execution_arguments(resume_parser, resume=True)
    batch_parser = subparsers.add_parser("batch")
    batch_parser.add_argument("--manifest", required=True)
    batch_parser.add_argument(
        "--max-parallel",
        type=int,
        choices=range(1, MAX_PARALLEL + 1),
        default=2,
    )
    subparsers.add_parser("doctor")
    args = parser.parse_args(argv)
    if hasattr(args, "timeout") and args.timeout is not None and args.timeout <= 0:
        parser.error("--timeout must be a positive integer when set")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        if args.command in {"run", "resume", "batch"}:
            install_termination_handlers()
        if args.command == "doctor":
            result = _doctor(args)
        elif args.command == "batch":
            result = _batch(args)
        else:
            result = _run(args)
    except QuotaExhausted as exc:
        print(json.dumps(exc.receipt, sort_keys=True))
        return 2
    except (BridgeError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(json.dumps({"schema": SCHEMA, "status": "ERROR", "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("status") in {"SUCCESS", "DISCOVERED"} else 1


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == CHILD_LAUNCHER_FLAG:
        _run_child_launcher(sys.argv[2:])
        raise SystemExit(127)
    raise SystemExit(main())
