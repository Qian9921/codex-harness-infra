from __future__ import annotations

import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.doctor import doctor
from scripts.install import install
from scripts.task_bootstrap import (
    CODEGRAPH_BEGIN,
    LIVE_STATE_CONTEXT_CAP,
    LiveField,
    ToolResult,
    _is_current_codex_executable,
    _semble_health_scope,
    canonical_control_socket,
    collect_bound_daemon,
    collect_control_socket,
    collect_doctor_summary,
    collect_install_state,
    collect_instruction_state,
    collect_live_runtime_state,
    doctor_subset,
    local_installation_checks,
    parse_proc_net_unix,
    probe_tools,
    render_live_state,
    run_hook,
    sanitize_cmdline,
)

ROOT = Path(__file__).resolve().parents[1]
DAEMON_VERSION_JSON = json.dumps(
    {"status": "running", "cliVersion": "0.149.1", "appServerVersion": "0.149.1"}
)


class FakeRunner:
    """Return deterministic successful tool output while preserving every call."""

    def __init__(
        self,
        root: Path,
        *,
        timeout_codegraph: bool = False,
        timeout_daemon_version: bool = False,
        daemon_version_fail: bool = False,
    ) -> None:
        self.root = root
        self.timeout_codegraph = timeout_codegraph
        self.timeout_daemon_version = timeout_daemon_version
        self.daemon_version_fail = daemon_version_fail
        self.calls: list[tuple[tuple[str, ...], Path | None]] = []
        self.timeouts: list[int] = []

    def __call__(
        self, command: tuple[str, ...], cwd: Path | None, _timeout: int
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append((command, cwd))
        self.timeouts.append(_timeout)
        if command[:4] == ("git", "-C", str(self.root), "rev-parse"):
            if command[-1] == "--show-toplevel":
                return subprocess.CompletedProcess(command, 0, f"{self.root}\n", "")
            return subprocess.CompletedProcess(command, 0, ".git/info/exclude\n", "")
        if self.timeout_codegraph and command[0] == str(self.root / "codegraph"):
            raise subprocess.TimeoutExpired(command, _timeout)
        if command[:4] == ("codex", "app-server", "daemon", "version"):
            if self.timeout_daemon_version:
                raise subprocess.TimeoutExpired(command, _timeout)
            if self.daemon_version_fail:
                return subprocess.CompletedProcess(command, 1, "", "failed")
            return subprocess.CompletedProcess(command, 0, DAEMON_VERSION_JSON + "\n", "")
        if len(command) > 1 and command[1] == "status":
            return subprocess.CompletedProcess(command, 0, "Not initialized\n", "")
        return subprocess.CompletedProcess(command, 0, "ok\n", "")


class TaskBootstrapTests(unittest.TestCase):
    def test_bootstrap_uses_all_tools_and_initializes_only_git_local_cache(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / ".git/info").mkdir(parents=True)
            tools = {}
            for name in ("codegraph", "semble", "rtk"):
                executable = root / name
                executable.write_text("", encoding="utf-8")
                tools[name] = str(executable)
            runner = FakeRunner(root)

            results = probe_tools(root, "Inspect the delivery adapter.", tools, runner=runner)

            self.assertEqual([result.name for result in results], ["CodeGraph", "Semble", "RTK"])
            self.assertTrue(all(result.ok for result in results))
            commands = [command for command, _ in runner.calls]
            self.assertTrue(
                any(
                    command[0] == tools["codegraph"] and command[1] == "init"
                    for command in commands
                )
            )
            self.assertTrue(
                any(
                    command[0] == tools["codegraph"] and command[1] == "files"
                    for command in commands
                )
            )
            self.assertTrue(
                any(
                    command[0] == tools["semble"] and command[1] == "search" for command in commands
                )
            )
            semble_command = next(command for command in commands if command[0] == tools["semble"])
            self.assertEqual(semble_command[3], "code")
            self.assertEqual(semble_command[7], "0")
            self.assertEqual(semble_command[-1], str(_semble_health_scope()))
            self.assertTrue(
                any(command[0] == tools["rtk"] and command[1] == "git" for command in commands)
            )
            exclude = (root / ".git/info/exclude").read_text(encoding="utf-8")
            self.assertIn(CODEGRAPH_BEGIN, exclude)
            self.assertIn(".codegraph/", exclude)

    def test_probe_reports_missing_tool_without_skipping_other_required_tools(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / ".git/info").mkdir(parents=True)
            executable = root / "rtk"
            executable.write_text("", encoding="utf-8")
            runner = FakeRunner(root)

            results = probe_tools(root, "Check tooling.", {"rtk": str(executable)}, runner=runner)

            self.assertEqual(
                results,
                [
                    ToolResult("CodeGraph", False, "not configured or unavailable"),
                    ToolResult("Semble", False, "not configured or unavailable"),
                    ToolResult("RTK", True, "ok"),
                ],
            )

    def test_probe_rejects_a_modified_v23_codegraph_exclude_block(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            exclude = root / ".git/info/exclude"
            exclude.parent.mkdir(parents=True)
            exclude.write_text(
                f"{CODEGRAPH_BEGIN}\nuser-owned-pattern/\n# END CODEX-HARNESS-INFRA V23 CODEGRAPH\n",
                encoding="utf-8",
            )
            executable = root / "codegraph"
            executable.write_text("", encoding="utf-8")
            runner = FakeRunner(root)

            results = probe_tools(
                root, "Check CodeGraph.", {"codegraph": str(executable)}, runner=runner
            )

            self.assertFalse(results[0].ok)
            self.assertIn("modified", results[0].detail)
            self.assertFalse(any(call[0][0] == str(executable) for call in runner.calls))

    def test_codegraph_timeout_does_not_skip_semble_or_rtk(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / ".git/info").mkdir(parents=True)
            tools = {}
            for name in ("codegraph", "semble", "rtk"):
                executable = root / name
                executable.write_text("", encoding="utf-8")
                tools[name] = str(executable)
            runner = FakeRunner(root, timeout_codegraph=True)

            results = probe_tools(root, "Check bounded tool timeouts.", tools, runner=runner)

            self.assertFalse(results[0].ok)
            self.assertTrue(results[1].ok)
            self.assertTrue(results[2].ok)
            commands = [command for command, _ in runner.calls]
            self.assertTrue(any(command[0] == tools["semble"] for command in commands))
            self.assertTrue(any(command[0] == tools["rtk"] for command in commands))
            self.assertLessEqual(max(runner.timeouts), 14)

    def test_live_state_uses_current_install_json_not_memory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            state = root / "state"
            state.mkdir()
            (state / "install.json").write_text(
                json.dumps(
                    {
                        "version": "23.2.0",
                        "portable_digest": "a" * 64,
                        "source_commit": "b" * 40,
                    }
                ),
                encoding="utf-8",
            )
            stale = {"version": "1.0.0", "portable_digest": "stale", "source_commit": "c" * 40}
            fields = collect_live_runtime_state(
                cwd=root,
                local_config=root / "missing.toml",
                codex_home=root / "codex",
                state_dir=state,
                runner=FakeRunner(root),
                memory_state=stale,
            )
            rendered = {field.name: field for field in fields}
            self.assertEqual(rendered["infra_version"].detail, "23.2.0")
            self.assertNotIn("1.0.0", rendered["infra_version"].render())
            self.assertNotIn("stale", render_live_state(fields))
            self.assertEqual(rendered["source_commit"].status, "ok")

    def test_missing_and_corrupt_install_state_fail_open(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            missing = collect_install_state(root / "absent")
            self.assertTrue(
                all(field.status == "missing" or field.status == "unavailable" for field in missing)
            )
            corrupt_dir = root / "corrupt"
            corrupt_dir.mkdir()
            (corrupt_dir / "install.json").write_text("{not-json", encoding="utf-8")
            corrupt = collect_install_state(corrupt_dir)
            self.assertTrue(all(field.status == "corrupt" for field in corrupt))

    def test_daemon_timeout_and_failure_are_categorized(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            timeout_fields = collect_live_runtime_state(
                cwd=root,
                local_config=root / "local.toml",
                codex_home=root / "codex",
                state_dir=root / "state",
                runner=FakeRunner(root, timeout_daemon_version=True),
            )
            error_fields = collect_live_runtime_state(
                cwd=root,
                local_config=root / "local.toml",
                codex_home=root / "codex",
                state_dir=root / "state",
                runner=FakeRunner(root, daemon_version_fail=True),
            )
            timeout_map = {field.name: field for field in timeout_fields}
            error_map = {field.name: field for field in error_fields}
            self.assertEqual(timeout_map["cli_version"].status, "timeout")
            self.assertEqual(timeout_map["app_server"].status, "timeout")
            self.assertEqual(error_map["app_server"].status, "error")
            self.assertEqual(error_map["cli_version"].status, "error")

    def test_override_absent_and_present(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "codex"
            home.mkdir()
            (home / "AGENTS.md").write_text("canonical\n", encoding="utf-8")
            absent = collect_live_runtime_state(
                cwd=home,
                local_config=home / "local.toml",
                codex_home=home,
                state_dir=home / "state",
                runner=FakeRunner(home),
            )
            self.assertEqual(
                {field.name: field.detail for field in absent}["agents_override"], "absent"
            )
            (home / "AGENTS.override.md").write_text("override-body\n", encoding="utf-8")
            present = collect_live_runtime_state(
                cwd=home,
                local_config=home / "local.toml",
                codex_home=home,
                state_dir=home / "state",
                runner=FakeRunner(home),
            )
            self.assertEqual(
                {field.name: field.detail for field in present}["agents_override"], "present"
            )

    def test_socket_parsing_and_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "codex"
            sock_path = canonical_control_socket(home)
            sock_path.parent.mkdir(parents=True)
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                listener.bind(str(sock_path))
                os.chmod(sock_path, 0o600)
                inode = sock_path.lstat().st_ino
                kernel_inode = "424242"
                table = (
                    "Num       RefCount Protocol Flags    Type St Inode Path\n"
                    f"00000000: 00000002 00000000 00010000 0001 01 {kernel_inode} {sock_path}\n"
                )
                parsed = parse_proc_net_unix(table)
                self.assertEqual(parsed[0][3], str(sock_path))
                self.assertEqual(parsed[0][2], kernel_inode)
                self.assertNotEqual(str(inode), kernel_inode)
                field = collect_control_socket(home, table)
                self.assertEqual(field.status, "ok")
                self.assertIn(f"path={sock_path}", field.detail)
                self.assertIn(f"inode={kernel_inode}", field.detail)
                self.assertNotIn(f"inode={inode}", field.detail)
                self.assertIn("mode=0o600", field.detail)
                self.assertIn(f"uid={os.getuid()}", field.detail)
                self.assertTrue(stat.S_ISSOCK(sock_path.lstat().st_mode))
            finally:
                listener.close()

    def test_output_cap_and_privacy(self) -> None:
        secret_fields = [
            LiveField("cli_version", "ok", "token=super-secret-value " + ("x" * 4000)),
        ]
        rendered = render_live_state(secret_fields, cap=200)
        self.assertNotIn("super-secret-value", rendered)
        self.assertIn("[redacted]", rendered)
        self.assertLessEqual(len(rendered), 200)
        self.assertIn("truncated", rendered)
        self.assertLessEqual(LIVE_STATE_CONTEXT_CAP, 1600)

    def test_hook_json_shape_includes_live_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / ".git/info").mkdir(parents=True)
            local = root / "local.toml"
            local.write_text("[tools]\n", encoding="utf-8")
            payload = run_hook(
                root,
                "Inspect runtime.",
                local,
                codex_home=root / "codex",
                state_dir=root / "state",
                runner=FakeRunner(root),
                memory_state={"version": "ignored"},
            )
            output = payload["hookSpecificOutput"]
            self.assertEqual(set(output), {"hookEventName", "additionalContext"})
            self.assertEqual(output["hookEventName"], "UserPromptSubmit")
            context = output["additionalContext"]
            self.assertIn("V23 required tool bootstrap", context)
            self.assertIn("V23 live runtime state", context)
            self.assertIn("memory of prior tasks is historical only", context)
            self.assertNotIn("ignored", context)
            json.dumps(payload)

    def test_socket_rejects_backup_stale_and_regular_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "codex"
            canonical = canonical_control_socket(home)
            canonical.parent.mkdir(parents=True)
            backup = home / "app-server-control.bak.old" / "app-server-control.sock"
            backup.parent.mkdir(parents=True)
            backup.write_text("not-a-socket", encoding="utf-8")
            missing = collect_control_socket(
                home,
                "Num Flags Type St Inode Path\n00000000: 00000002 00000000 00010000 0001 01 1 "
                f"{backup}\n",
            )
            self.assertEqual(missing.status, "unavailable")
            self.assertIn(str(canonical), missing.detail)
            canonical.write_text("regular-file", encoding="utf-8")
            regular = collect_control_socket(home, "")
            self.assertEqual(regular.status, "error")
            self.assertEqual(f"path={canonical} not-a-socket", regular.detail)
            canonical.unlink()
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                listener.bind(str(canonical))
                listener.listen(1)
                fs_inode = canonical.lstat().st_ino
                kernel_inode = fs_inode + 99
                healthy = collect_control_socket(
                    home,
                    "Num Flags Type St Inode Path\n"
                    f"00000000: 00000002 00000000 00010000 0001 01 {kernel_inode} {canonical}\n",
                )
                self.assertEqual(healthy.status, "ok")
                self.assertIn(f"path={canonical}", healthy.detail)
                self.assertIn(f"inode={kernel_inode}", healthy.detail)
            finally:
                listener.close()

    def test_cmdline_allowlist_omits_hostile_paths_and_opaque_credentials(self) -> None:
        canonical = Path("/home/user/.codex/app-server-control/app-server-control.sock")
        sanitized = sanitize_cmdline(
            [
                "/usr/bin/codex",
                "app-server",
                "-c",
                "features.code_mode_host=true",
                "--listen",
                f"unix://{canonical}",
                "--token",
                "super-token",
                "--api-key=equals-key",
                "--config",
                "/etc/shadow",
                "/home/user/.codex/secret.toml",
                "opaque-credential-value",
            ],
            canonical_socket=canonical,
        )
        self.assertEqual(
            sanitized, "codex app-server -c features.code_mode_host=true --listen unix://"
        )
        self.assertNotIn("super-token", sanitized)
        self.assertNotIn("equals-key", sanitized)
        self.assertNotIn("/etc/shadow", sanitized)
        self.assertNotIn("secret.toml", sanitized)
        self.assertNotIn("opaque-credential-value", sanitized)
        self.assertNotIn(str(canonical), sanitized)

    def test_doctor_subset_corrupt_toml_non_v23_and_broken_role(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            local = root / "local.toml"
            local.write_text("{not toml", encoding="utf-8")
            home = root / "codex"
            home.mkdir()
            corrupt = {name: (ok, detail) for name, ok, detail in doctor_subset(home, local)}
            self.assertFalse(corrupt["local_config"][0])
            self.assertIn("corrupt", corrupt["local_config"][1])
            self.assertEqual(collect_doctor_summary(home, local).status, "error")
            self.assertIn("local_config", collect_doctor_summary(home, local).detail)
            local.write_text(
                """
[models]
primary = "primary-model"
primary_effort = "medium"
executor = "executor-model"
executor_effort = "medium"
reviewer = "reviewer-model"

[opening]
instruction = "Local-only opening."

[tools]
codegraph = "codegraph"
semble = "semble"
rtk = "rtk"
""".lstrip(),
                encoding="utf-8",
            )
            (home / "AGENTS.md").write_text("unmanaged personal rules\n", encoding="utf-8")
            non_v23 = {name: ok for name, ok, _detail in doctor_subset(home, local)}
            self.assertFalse(non_v23["global_portable"])
            self.assertFalse(non_v23["global_local"])
            install(ROOT, home, local, root / "state")
            agent = home / "agents/v23-executor.toml"
            text = agent.read_text(encoding="utf-8")
            agent.write_text(text.replace("executor-model", "wrong-model"), encoding="utf-8")
            broken = {name: (ok, detail) for name, ok, detail in doctor_subset(home, local)}
            self.assertFalse(broken["agent_v23_executor"][0])
            self.assertIn(str(agent), broken["agent_v23_executor"][1])
            (home / "bin/grok-execution.py").unlink()
            missing_bridge = {
                name: ok for name, ok, _detail in local_installation_checks(home, local)
            }
            self.assertFalse(missing_bridge["grok_execution_route"])
            local_names = [name for name, _ok, _detail in local_installation_checks(home, local)]
            report = doctor(
                home,
                local,
                ROOT / "tests",
                check_github=False,
                probe_required_tools=False,
            )
            doctor_names = [check["name"] for check in report["checks"]]
            self.assertEqual(local_names, doctor_names)

    def test_instruction_state_permission_error_keeps_all_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "codex"
            home.mkdir()
            (home / "AGENTS.md").write_text("canonical\n", encoding="utf-8")
            (home / "AGENTS.override.md").write_text("override\n", encoding="utf-8")

            def deny(path: Path) -> str:
                if path.name == "AGENTS.override.md":
                    raise PermissionError("mocked permission error")
                return path.read_text(encoding="utf-8")

            fields = {
                field.name: field for field in collect_instruction_state(home, read_text=deny)
            }
            self.assertEqual(
                set(fields),
                {"active_global_instruction", "canonical_agents", "agents_override"},
            )
            self.assertEqual(fields["canonical_agents"].status, "ok")
            self.assertEqual(fields["agents_override"].status, "error")
            self.assertIn("mocked permission error", fields["agents_override"].detail)
            self.assertEqual(fields["active_global_instruction"].status, "error")

    def test_linux_listener_uses_kernel_inode_not_filesystem_ino(self) -> None:
        if not Path("/proc/net/unix").is_file():
            self.skipTest("/proc/net/unix is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "codex"
            sock_path = canonical_control_socket(home)
            sock_path.parent.mkdir(parents=True)
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                listener.bind(str(sock_path))
                listener.listen(1)
                fs_inode = str(sock_path.lstat().st_ino)
                table = Path("/proc/net/unix").read_text(encoding="utf-8", errors="replace")
                rows = [
                    row
                    for row in parse_proc_net_unix(table)
                    if row[3] == str(sock_path) and row[0] == "00010000" and row[1] == "01"
                ]
                self.assertTrue(rows, "expected a LISTEN row for the bound socket")
                kernel_inode = rows[0][2]
                if kernel_inode == fs_inode:
                    self.skipTest("kernel and filesystem inode unexpectedly matched")
                field = collect_control_socket(home, table)
                self.assertEqual(field.status, "ok")
                self.assertIn(f"path={sock_path}", field.detail)
                self.assertIn(f"inode={kernel_inode}", field.detail)
                self.assertNotIn(f"inode={fs_inode}", field.detail)
                proc_root = Path(directory) / "proc"
                pid_dir = proc_root / "4242"
                (pid_dir / "fd").mkdir(parents=True)
                fake_cli = Path(directory) / "bin" / "codex"
                fake_cli.parent.mkdir()
                fake_cli.write_text("", encoding="utf-8")
                fake_cli.chmod(0o755)
                (pid_dir / "cmdline").write_bytes(
                    b"codex\0app-server\0--listen\0unix://" + str(sock_path).encode() + b"\0"
                )
                (pid_dir / "exe").symlink_to(fake_cli)
                os.symlink(f"socket:[{kernel_inode}]", pid_dir / "fd" / "3")
                daemon = collect_bound_daemon(
                    field,
                    proc_root,
                    expected_uid=os.getuid(),
                    expected_exe=fake_cli,
                    canonical_socket=sock_path,
                )
                self.assertEqual(daemon.status, "ok")
                self.assertIn("pid=4242", daemon.detail)
                proxy = collect_bound_daemon(
                    field,
                    proc_root,
                    expected_uid=os.getuid(),
                    expected_exe=Path("/usr/bin/python3"),
                    canonical_socket=sock_path,
                )
                self.assertEqual(proxy.status, "unavailable")
            finally:
                listener.close()

    def test_same_basename_foreign_codex_is_not_current_executable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            expected = Path(directory) / "expected" / "codex"
            foreign = Path(directory) / "foreign" / "codex"
            expected.parent.mkdir()
            foreign.parent.mkdir()
            expected.write_text("", encoding="utf-8")
            foreign.write_text("", encoding="utf-8")
            expected.chmod(0o755)
            foreign.chmod(0o755)
            self.assertTrue(_is_current_codex_executable(str(expected), expected))
            self.assertFalse(_is_current_codex_executable(str(foreign), expected))
            self.assertFalse(_is_current_codex_executable(str(foreign), None))
            socket_field = LiveField("control_socket", "ok", "inode=99")
            proc_root = Path(directory) / "proc"
            pid_dir = proc_root / "77"
            (pid_dir / "fd").mkdir(parents=True)
            (pid_dir / "cmdline").write_bytes(b"codex\0app-server\0--listen\0unix://sock\0")
            (pid_dir / "exe").symlink_to(foreign)
            os.symlink("socket:[99]", pid_dir / "fd" / "3")
            daemon = collect_bound_daemon(
                socket_field,
                proc_root,
                expected_uid=os.getuid(),
                expected_exe=expected,
                canonical_socket=Path(directory) / "app-server-control" / "app-server-control.sock",
            )
            self.assertEqual(daemon.status, "unavailable")
            self.assertIn("not current Codex CLI", daemon.detail)

    def test_installed_hook_copy_is_self_contained(self) -> None:
        source = Path(__file__).resolve().parents[1] / "scripts/task_bootstrap.py"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dest = root / "harness/v23/task_bootstrap.py"
            dest.parent.mkdir(parents=True)
            shutil.copy2(source, dest)
            local = root / "local.toml"
            local.write_text("[tools]\n", encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(dest),
                    "--local-config",
                    str(local),
                    "--codex-home",
                    str(root / "codex"),
                    "--state-dir",
                    str(root / "state"),
                    "--cwd",
                    str(root),
                    "--prompt",
                    "Inspect runtime.",
                ],
                text=True,
                capture_output=True,
                check=False,
                env={**os.environ, "PYTHONPATH": ""},
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            context = payload["hookSpecificOutput"]["additionalContext"]
            self.assertIn("UserPromptSubmit", payload["hookSpecificOutput"]["hookEventName"])
            self.assertIn("V23 live runtime state", context)


if __name__ == "__main__":
    unittest.main()
