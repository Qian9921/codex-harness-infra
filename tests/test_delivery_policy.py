"""Synthetic tests for explicit delivery preference and publication gates."""

from __future__ import annotations

import io
import os
import stat
import subprocess
import sys
import tempfile
import tomllib
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from scripts.delivery_policy import (
    MODE_LOCAL_ONLY,
    MODE_MERGE_IF_READY,
    MODE_PULL_REQUEST,
    DeliveryError,
    assert_publication_allowed,
    parse_delivery,
    write_effective_config,
)
from scripts.github_delivery import DeliveryFlow, FlowError, GHClient
from scripts.github_delivery import main as delivery_main
from scripts.task_bootstrap import collect_live_runtime_state

ROOT = Path(__file__).resolve().parents[1]


class RecordingRunner:
    def __init__(self) -> None:
        self.commands: list[tuple[str, ...]] = []

    def __call__(self, command, _cwd, _timeout):
        self.commands.append(tuple(command))
        return subprocess.CompletedProcess(command, 0, "{}", "")


class DeliveryPolicyTests(unittest.TestCase):
    def test_omitted_table_is_local_only(self) -> None:
        policy = parse_delivery({})
        self.assertEqual(policy.mode, MODE_LOCAL_ONLY)
        self.assertFalse(policy.github_write)
        self.assertEqual(policy.repositories, frozenset())

    def test_github_modes_require_repositories(self) -> None:
        with self.assertRaises(DeliveryError):
            parse_delivery({"delivery": {"mode": MODE_PULL_REQUEST, "repositories": []}})
        policy = parse_delivery(
            {
                "delivery": {
                    "mode": MODE_MERGE_IF_READY,
                    "repositories": ["owner/demo-repo"],
                }
            }
        )
        self.assertTrue(policy.merge_authorized)
        self.assertTrue(policy.authorizes("owner/demo-repo.git"))

    def test_local_only_rejects_publication(self) -> None:
        policy = parse_delivery({"delivery": {"mode": "local_only"}})
        with self.assertRaises(DeliveryError):
            assert_publication_allowed(policy, "owner/repo", "push")

    def test_pull_request_rejects_merge(self) -> None:
        policy = parse_delivery(
            {"delivery": {"mode": MODE_PULL_REQUEST, "repositories": ["owner/repo"]}}
        )
        assert_publication_allowed(policy, "owner/repo", "push")
        with self.assertRaises(DeliveryError):
            assert_publication_allowed(policy, "owner/repo", "merge")

    def test_flow_blocks_unauthorized_repo(self) -> None:
        policy = parse_delivery(
            {"delivery": {"mode": MODE_PULL_REQUEST, "repositories": ["owner/allowed"]}}
        )
        flow = DeliveryFlow(
            GHClient(Path("/profiles/author")),
            GHClient(Path("/profiles/reviewer")),
            "author",
            "reviewer",
            delivery=policy,
        )
        with self.assertRaises(FlowError):
            flow._require_delivery("owner/other", "ensure-pr")
        flow._require_delivery("owner/allowed", "ensure-pr")

    def test_effective_file_is_delivery_table_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            persistent = Path(directory) / "local.toml"
            original = (
                '[models]\nprimary = "secret-model"\n'
                'path = "/home/user/.config/secret"\n\n'
                "[section]\nvalue = 1\n\n"
                '[delivery]\nmode = "local_only"\nrepositories = []\n'
            )
            persistent.write_text(original, encoding="utf-8")
            effective = Path(directory) / "ephemeral" / "request.toml"
            write_effective_config(persistent, effective, MODE_PULL_REQUEST, ["owner/demo-repo"])
            self.assertEqual(persistent.read_text(encoding="utf-8"), original)
            text = effective.read_text(encoding="utf-8")
            self.assertEqual(
                text, '[delivery]\nmode = "pull_request"\nrepositories = ["owner/demo-repo"]\n'
            )
            self.assertNotIn("secret-model", text)
            self.assertNotIn("/home/user", text)
            self.assertNotIn("[section]", text)
            self.assertNotIn("[models]", text)
            self.assertEqual(stat.S_IMODE(effective.stat().st_mode), 0o600)
            with self.assertRaises(DeliveryError):
                write_effective_config(
                    persistent, persistent, MODE_PULL_REQUEST, ["owner/demo-repo"]
                )
            loaded = tomllib.loads(text)
            policy = parse_delivery(loaded)
            assert_publication_allowed(policy, "owner/demo-repo", "ensure-pr")
            with self.assertRaises(DeliveryError):
                assert_publication_allowed(policy, "owner/other", "ensure-pr")
            with self.assertRaises(DeliveryError):
                assert_publication_allowed(policy, "owner/demo-repo", "merge")

    def test_inline_delivery_table_and_simple_section_parse(self) -> None:
        inline = tomllib.loads(
            '[delivery]\nmode = "pull_request"\nrepositories = ["owner/demo-repo"]\n'
        )
        policy = parse_delivery(inline)
        self.assertEqual(policy.mode, MODE_PULL_REQUEST)
        self.assertTrue(policy.authorizes("owner/demo-repo"))
        simple = tomllib.loads(
            'mode = 1\n\n[delivery]\nmode = "pull_request"\nrepositories = ["owner/demo-repo"]\n'
        )
        self.assertEqual(parse_delivery(simple).mode, MODE_PULL_REQUEST)

    def test_github_cli_allows_named_repo_denies_other_and_blocks_merge(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            persistent = Path(directory) / "local.toml"
            persistent.write_text(
                '[delivery]\nmode = "local_only"\nrepositories = []\n', encoding="utf-8"
            )
            effective = Path(directory) / "request.toml"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts/delivery_policy.py"),
                    "effective",
                    "--local-config",
                    str(persistent),
                    "--mode",
                    "pull_request",
                    "--repository",
                    "owner/demo-repo",
                    "--output",
                    str(effective),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("local_only", persistent.read_text(encoding="utf-8"))
            self.assertNotIn("[models]", effective.read_text(encoding="utf-8"))
            base = [
                "ensure-pr",
                "--author-config",
                str(Path(directory) / "author"),
                "--reviewer-config",
                str(Path(directory) / "reviewer"),
                "--author-login",
                "author",
                "--reviewer-login",
                "reviewer",
                "--branch",
                "topic",
                "--base",
                "main",
                "--title",
                "demo",
                "--local-config",
                str(effective),
            ]
            captured = io.StringIO()
            with redirect_stderr(captured):
                allowed = delivery_main([*base, "--repo", "owner/demo-repo"])
            self.assertEqual(allowed, 2)
            self.assertNotIn("not in [delivery].repositories", captured.getvalue())
            captured = io.StringIO()
            with redirect_stderr(captured):
                denied = delivery_main([*base, "--repo", "owner/other"])
            self.assertEqual(denied, 2)
            self.assertIn("not in [delivery].repositories", captured.getvalue())
            captured = io.StringIO()
            with redirect_stderr(captured):
                merged = delivery_main(
                    [
                        "merge",
                        "--author-config",
                        str(Path(directory) / "author"),
                        "--reviewer-config",
                        str(Path(directory) / "reviewer"),
                        "--author-login",
                        "author",
                        "--reviewer-login",
                        "reviewer",
                        "--repo",
                        "owner/demo-repo",
                        "--pr",
                        "1",
                        "--sha",
                        "a" * 40,
                        "--local-config",
                        str(effective),
                    ]
                )
            self.assertEqual(merged, 2)
            self.assertIn("merge requires delivery.mode=merge_if_ready", captured.getvalue())
            effective.unlink()
            self.assertFalse(effective.exists())


class ConsumerJourneyTests(unittest.TestCase):
    """Install into a fresh CODEX_HOME whose path contains spaces."""

    def test_native_only_install_without_github_opening_or_tools(self) -> None:
        from scripts.install import install, uninstall
        from scripts.task_bootstrap import HOOK_TIMEOUT_SECONDS

        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "codex home with spaces"
            state = Path(directory) / "state dir"
            local = Path(directory) / "local.toml"
            local.write_text(
                """
[models]
primary = "primary-model"
executor = "executor-model"
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

[delivery]
mode = "local_only"
repositories = []
""".strip()
                + "\n",
                encoding="utf-8",
            )
            unrelated = home / "personal notes.txt"
            unrelated.parent.mkdir(parents=True)
            unrelated.write_text("keep me\n", encoding="utf-8")
            install(root, home, local, state)
            self.assertTrue((home / "skills/engineering-delivery/SKILL.md").is_file())
            self.assertTrue((home / "harness/v23/delivery_policy.py").is_file())
            self.assertTrue((home / "bin/delivery-policy.py").is_file())
            self.assertIn(
                "timeout = " + str(HOOK_TIMEOUT_SECONDS), (home / "config.toml").read_text()
            )
            env = {
                key: value
                for key, value in os.environ.items()
                if key not in {"PYTHONPATH", "PYTHONHOME"}
            }
            env["PYTHONPATH"] = ""
            env["PYTHONHOME"] = ""
            completed = subprocess.run(
                [
                    sys.executable,
                    str(home / "harness/v23/task_bootstrap.py"),
                    "--local-config",
                    str(local),
                    "--codex-home",
                    str(home),
                    "--state-dir",
                    str(state),
                    "--cwd",
                    str(Path(directory)),
                    "--prompt",
                    "New task.",
                ],
                text=True,
                capture_output=True,
                check=False,
                env=env,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            context = completed.stdout
            self.assertIn("daemon_probes=skipped", context)
            self.assertNotIn("cli_version=", context)
            executor = (home / "agents/v23-executor.toml").read_text(encoding="utf-8")
            self.assertIn("missing or failed tools fall back to baseline", executor)
            self.assertNotIn("[tools]", local.read_text(encoding="utf-8"))
            self.assertNotIn("codegraph =", local.read_text(encoding="utf-8"))
            self.assertNotIn("probe_tools", context)
            ephemeral = Path(directory) / "request delivery.toml"
            policy_cli = subprocess.run(
                [
                    sys.executable,
                    str(home / "bin/delivery-policy.py"),
                    "effective",
                    "--local-config",
                    str(local),
                    "--mode",
                    "pull_request",
                    "--repository",
                    "owner/demo-repo",
                    "--output",
                    str(ephemeral),
                ],
                text=True,
                capture_output=True,
                check=False,
                env=env,
            )
            self.assertEqual(policy_cli.returncode, 0, policy_cli.stderr)
            self.assertEqual(
                ephemeral.read_text(encoding="utf-8"),
                '[delivery]\nmode = "pull_request"\nrepositories = ["owner/demo-repo"]\n',
            )
            self.assertNotIn("primary-model", ephemeral.read_text(encoding="utf-8"))
            ephemeral.unlink()
            install(root, home, local, state)
            self.assertEqual(unrelated.read_text(encoding="utf-8"), "keep me\n")
            uninstall(home, state)
            self.assertEqual(unrelated.read_text(encoding="utf-8"), "keep me\n")
            self.assertTrue(unrelated.is_file())

    def test_default_hook_does_not_invoke_daemon_probe(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "codex").mkdir()
            runner = RecordingRunner()
            collect_live_runtime_state(
                cwd=root,
                local_config=root / "missing.toml",
                codex_home=root / "codex",
                state_dir=root / "state",
                runner=runner,
                probe_daemons=False,
            )
            self.assertFalse(
                any(
                    command[:4] == ("codex", "app-server", "daemon", "version")
                    for command in runner.commands
                )
            )
            collect_live_runtime_state(
                cwd=root,
                local_config=root / "missing.toml",
                codex_home=root / "codex",
                state_dir=root / "state",
                runner=runner,
                probe_daemons=True,
            )
            self.assertTrue(
                any(
                    command[:4] == ("codex", "app-server", "daemon", "version")
                    for command in runner.commands
                )
            )


if __name__ == "__main__":
    unittest.main()
