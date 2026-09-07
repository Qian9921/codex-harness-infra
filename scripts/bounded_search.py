"""Harness-default bounded ripgrep for one explicit repository, module, or file set.

This is a Harness default, not an OS sandbox. Cross-process searches for the
same user are serialized. Each invocation runs ``rg --threads 1 --no-config``
with a bounded wait, combined output cap, and process-group cleanup. Outcomes
are match, no-match, error, timeout, or incomplete. Timeout and incomplete are
never reported as no-match.

Test-only overrides (not a security boundary): ``V23_BOUNDED_RG``,
``V23_BOUNDED_SEARCH_LOCK``, ``V23_BOUNDED_SEARCH_OUTPUT_CAP``.
"""

from __future__ import annotations

import argparse
import json
import os
import select
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

DEFAULT_TIMEOUT_SECONDS = 15
DEFAULT_OUTPUT_CAP_BYTES = 1_048_576
LOCK_WAIT_SECONDS = 60
DRAIN_TIMEOUT_SECONDS = 1.0
STATUS_MATCH = "match"
STATUS_NO_MATCH = "no-match"
STATUS_ERROR = "error"
STATUS_TIMEOUT = "timeout"
STATUS_INCOMPLETE = "incomplete"
EXIT_BY_STATUS = {
    STATUS_MATCH: 0,
    STATUS_NO_MATCH: 1,
    STATUS_ERROR: 2,
    STATUS_TIMEOUT: 3,
    STATUS_INCOMPLETE: 4,
}
_TERMINATION_SIGNALS = (signal.SIGTERM, signal.SIGHUP, signal.SIGINT)
CHILD_LAUNCHER_FLAG = "--v23-search-child"


class SearchError(RuntimeError):
    """Raised when the invocation is out of scope or cannot start."""


def _lock_path() -> Path:
    override = os.environ.get("V23_BOUNDED_SEARCH_LOCK")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".cache/codex-harness/bounded-search.lock"


def _rg_binary() -> str:
    override = os.environ.get("V23_BOUNDED_RG")
    if override:
        return override
    return "rg"


def _output_cap() -> int:
    raw = os.environ.get("V23_BOUNDED_SEARCH_OUTPUT_CAP")
    if raw:
        try:
            value = int(raw)
        except ValueError:
            value = DEFAULT_OUTPUT_CAP_BYTES
        if value > 0:
            return value
    return DEFAULT_OUTPUT_CAP_BYTES


def _too_broad(root: Path) -> bool:
    resolved = root.resolve()
    blocked = {Path("/"), Path("/tmp"), Path("/var/tmp"), Path("/home"), Path("/Users")}
    try:
        blocked.add(Path.home().resolve())
    except OSError:
        pass
    return resolved in blocked


def _scoped_paths(root: Path, values: Sequence[str]) -> list[str]:
    scoped: list[str] = []
    for value in values:
        raw = Path(value)
        candidate = (root / raw).resolve() if not raw.is_absolute() else raw.resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise SearchError(f"search path escapes root: {value}") from exc
        scoped.append(str(candidate))
    return scoped


def acquire_lock(path: Path, wait_seconds: int) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except BlockingIOError:
            if time.monotonic() >= deadline:
                os.close(fd)
                raise SearchError("bounded-search lock wait timed out")
            time.sleep(0.05)


def release_lock(fd: int) -> None:
    import fcntl

    fcntl.flock(fd, fcntl.LOCK_UN)
    os.close(fd)


def _close_pipes(proc: subprocess.Popen[bytes] | None) -> None:
    if proc is None:
        return
    for stream in (proc.stdout, proc.stderr):
        if stream is None:
            continue
        try:
            stream.close()
        except OSError:
            pass


def _child_reset_inherited_signal_mask() -> None:
    for signum in _TERMINATION_SIGNALS:
        signal.signal(signum, signal.SIG_DFL)
    for name in ("SIGPIPE", "SIGXFSZ"):
        signum = getattr(signal, name, None)
        if isinstance(signum, int):
            signal.signal(signum, signal.SIG_DFL)
    signal.pthread_sigmask(signal.SIG_UNBLOCK, _TERMINATION_SIGNALS)


def _validate_child_launcher_target(command: Sequence[str]) -> list[str]:
    if not command:
        raise SearchError("child launcher command is empty")
    argv = [str(item) for item in command]
    if not argv[0] or argv[0].startswith("-"):
        raise SearchError("child launcher command is invalid")
    target = Path(argv[0])
    if (target.is_absolute() or "/" in argv[0]) and (
        not target.is_file() or not os.access(target, os.X_OK)
    ):
        raise SearchError(f"executable unavailable: {argv[0]}")
    return argv


def _resolve_rg(binary: str) -> str:
    path = Path(binary)
    if path.is_absolute() or "/" in binary:
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
        raise FileNotFoundError(binary)
    found = shutil.which(binary)
    if not found:
        raise FileNotFoundError(binary)
    return found


def _child_launcher_argv(command: Sequence[str]) -> list[str]:
    launcher = Path(__file__).resolve()
    if launcher.name not in {"bounded_search.py", "bounded-search.py"} or not launcher.is_file():
        raise SearchError("child launcher path is invalid")
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
    """Spawn-issued cleanup ownership. Candidate PGID is proc.pid."""

    __slots__ = ("_candidate_pgid", "_done", "_proc")

    def __init__(self, proc: Any) -> None:
        pid = getattr(proc, "pid", None)
        if type(pid) is not int or pid <= 1:
            raise SearchError("dedicated process group validation failed")
        self._proc = proc
        self._candidate_pgid = pid
        self._done = False

    def kill_and_drain(self) -> None:
        if self._done:
            return
        self._done = True
        previous = signal.pthread_sigmask(signal.SIG_BLOCK, _TERMINATION_SIGNALS)
        try:
            candidate = self._candidate_pgid
            if candidate > 1 and candidate not in (os.getpid(), os.getpgrp()):
                try:
                    os.killpg(candidate, signal.SIGKILL)
                except OSError:
                    pass
            try:
                self._proc.kill()
            except (OSError, AttributeError):
                pass
            try:
                self._proc.wait(timeout=DRAIN_TIMEOUT_SECONDS)
            except (subprocess.TimeoutExpired, OSError):
                try:
                    self._proc.kill()
                except (OSError, AttributeError):
                    pass
                try:
                    self._proc.wait(timeout=DRAIN_TIMEOUT_SECONDS)
                except (subprocess.TimeoutExpired, OSError):
                    pass
            _close_pipes(self._proc)
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous)


def terminate_group(proc: subprocess.Popen[bytes] | None) -> None:
    """SIGKILL the dedicated session, including after the leader has exited."""
    if proc is None:
        return
    token = getattr(proc, "_v23_cleanup_token", None)
    if isinstance(token, _SpawnCleanupToken):
        token.kill_and_drain()
        return
    _SpawnCleanupToken(proc).kill_and_drain()


def _spawn_search(command: Sequence[str], cwd: Path) -> subprocess.Popen[bytes]:
    previous = signal.pthread_sigmask(signal.SIG_BLOCK, _TERMINATION_SIGNALS)
    proc: subprocess.Popen[bytes] | None = None
    try:
        proc = subprocess.Popen(
            _child_launcher_argv(command),
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        proc._v23_cleanup_token = _SpawnCleanupToken(proc)
        return proc
    except BaseException:
        terminate_group(proc)
        raise
    finally:
        try:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous)
        except BaseException:
            terminate_group(proc)
            raise


def _read_capped(
    proc: subprocess.Popen[bytes], timeout_seconds: int, cap: int
) -> tuple[bytes, str]:
    """Read combined output until exit, timeout, or cap. Caller owns cleanup."""
    assert proc.stdout is not None
    buf = bytearray()
    deadline = time.monotonic() + timeout_seconds
    fd = proc.stdout.fileno()
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return bytes(buf), STATUS_TIMEOUT
        ready, _, _ = select.select([proc.stdout], [], [], remaining)
        if not ready:
            if proc.poll() is None:
                return bytes(buf), STATUS_TIMEOUT
            chunk = os.read(fd, 65536)
            if not chunk:
                return bytes(buf), ""
            if len(buf) + len(chunk) > cap:
                return bytes(buf[:cap]), STATUS_INCOMPLETE
            buf.extend(chunk)
            continue
        try:
            chunk = os.read(fd, 65536)
        except OSError:
            break
        if not chunk:
            break
        if len(buf) + len(chunk) > cap:
            return bytes(buf[:cap]), STATUS_INCOMPLETE
        buf.extend(chunk)
    return bytes(buf), ""


def _status_from_code(code: int | None, text: str) -> dict[str, object]:
    if code == 0:
        return {"status": STATUS_MATCH, "detail": "matched", "stdout": text, "stderr": ""}
    if code == 1:
        return {"status": STATUS_NO_MATCH, "detail": "no match", "stdout": text, "stderr": ""}
    return {
        "status": STATUS_ERROR,
        "detail": (text or f"rg exit {code}").strip()[:400],
        "stdout": text,
        "stderr": "",
    }


def run_search(
    *,
    root: Path,
    pattern: str,
    paths: Sequence[str],
    glob: str | None = None,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    rg: str | None = None,
    output_cap: int | None = None,
) -> dict[str, object]:
    if not pattern:
        raise SearchError("pattern is required")
    if type(timeout_seconds) is not int or timeout_seconds <= 0:
        raise SearchError("timeout must be a positive integer")
    root = root.expanduser().resolve()
    if not root.is_dir():
        raise SearchError(f"search root is not a directory: {root}")
    if _too_broad(root):
        raise SearchError(f"search root is too broad: {root}")
    targets = _scoped_paths(root, paths) if paths else [str(root)]
    binary = rg or _rg_binary()
    cap = output_cap if output_cap is not None else _output_cap()
    proc: subprocess.Popen[bytes] | None = None
    try:
        resolved = _resolve_rg(binary)
        command = [resolved, "--no-config", "--threads", "1", "--color", "never", "-n"]
        if glob:
            command.extend(("--glob", glob))
        command.extend(("--", pattern, *targets))
        proc = _spawn_search(command, root)
        raw, interrupt = _read_capped(proc, timeout_seconds, cap)
        text = raw.decode("utf-8", "replace")
        if interrupt:
            detail = (
                "search timed out; result is incomplete, not no-match"
                if interrupt == STATUS_TIMEOUT
                else "search output exceeded cap; result is incomplete, not no-match"
            )
            return {
                "status": interrupt,
                "detail": detail,
                "stdout": text,
                "stderr": "",
            }
        code = proc.poll()
        if code is None:
            try:
                code = proc.wait(timeout=DRAIN_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                return {
                    "status": STATUS_TIMEOUT,
                    "detail": "search timed out; result is incomplete, not no-match",
                    "stdout": text,
                    "stderr": "",
                }
        return _status_from_code(code, text)
    except FileNotFoundError:
        return {
            "status": STATUS_ERROR,
            "detail": f"executable unavailable: {binary}",
            "stdout": "",
            "stderr": "",
        }
    except OSError as error:
        return {
            "status": STATUS_ERROR,
            "detail": f"spawn failed: {error}",
            "stdout": "",
            "stderr": "",
        }
    except BaseException:
        raise
    finally:
        terminate_group(proc)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--pattern", required=True)
    parser.add_argument("--path", action="append", default=[])
    parser.add_argument("--glob")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    args = parser.parse_args(argv)
    fd = None
    previous = {sig: signal.getsignal(sig) for sig in _TERMINATION_SIGNALS}
    interrupted: int | None = None
    result: dict[str, object] = {
        "status": STATUS_ERROR,
        "detail": "search did not start",
        "stdout": "",
        "stderr": "",
    }

    def _handle(signum: int, _frame: object) -> None:
        raise KeyboardInterrupt(signum)

    for sig in _TERMINATION_SIGNALS:
        signal.signal(sig, _handle)
    try:
        fd = acquire_lock(_lock_path(), LOCK_WAIT_SECONDS)
        result = run_search(
            root=args.root,
            pattern=args.pattern,
            paths=args.path,
            glob=args.glob,
            timeout_seconds=args.timeout,
        )
    except SearchError as error:
        result = {"status": STATUS_ERROR, "detail": str(error), "stdout": "", "stderr": ""}
    except KeyboardInterrupt as error:
        interrupted = error.args[0] if error.args and type(error.args[0]) is int else signal.SIGINT
        result = {
            "status": STATUS_INCOMPLETE,
            "detail": "search interrupted; result is incomplete, not no-match",
            "stdout": "",
            "stderr": "",
        }
    except BaseException as error:  # noqa: BLE001 - must kill/reap before lock release
        result = {
            "status": STATUS_ERROR,
            "detail": f"search failed: {error}",
            "stdout": "",
            "stderr": "",
        }
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
        if fd is not None:
            release_lock(fd)
    printable = {key: result[key] for key in ("status", "detail") if key in result}
    if result.get("stdout"):
        printable["stdout"] = result["stdout"]
    print(json.dumps(printable, ensure_ascii=False))
    if interrupted is not None:
        signal.signal(interrupted, signal.SIG_DFL)
        os.kill(os.getpid(), interrupted)
    return EXIT_BY_STATUS.get(str(result.get("status")), 2)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == CHILD_LAUNCHER_FLAG:
        _run_child_launcher(sys.argv[2:])
        raise SystemExit(127)
    raise SystemExit(main())
