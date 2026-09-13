"""Synthetic tests for explicit delivery preference and publication gates."""

from __future__ import annotations

import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from scripts.delivery_policy import (
    MODE_LOCAL_ONLY,
    MODE_MERGE_IF_READY,
    MODE_PULL_REQUEST,
    DeliveryError,
    assert_publication_allowed,
    parse_delivery,
)
from scripts.github_delivery import DeliveryFlow, FlowError, GHClient


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
                    "repositories": ["Qian9921/codex-governance-infra"],
                }
            }
        )
        self.assertTrue(policy.merge_authorized)
        self.assertTrue(policy.authorizes("Qian9921/codex-governance-infra.git"))

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


class ConsumerJourneyTests(unittest.TestCase):
    """Install into a fresh CODEX_HOME whose path contains spaces."""

    def test_native_only_install_without_github_opening_or_tools(self) -> None:
        from scripts.install import install, uninstall
        from scripts.task_bootstrap import HOOK_TIMEOUT_SECONDS, run_hook

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
            self.assertIn(
                "timeout = " + str(HOOK_TIMEOUT_SECONDS), (home / "config.toml").read_text()
            )
            payload = run_hook(
                Path(directory),
                "New task.",
                local,
                codex_home=home,
                state_dir=state,
            )
            context = payload["hookSpecificOutput"]["additionalContext"]
            self.assertIn("daemon_probes=skipped", context)
            self.assertNotIn("cli_version=", context)
            install(root, home, local, state)
            self.assertEqual(unrelated.read_text(encoding="utf-8"), "keep me\n")
            uninstall(home, state)
            self.assertEqual(unrelated.read_text(encoding="utf-8"), "keep me\n")
            self.assertTrue(unrelated.is_file())

    def test_default_hook_skips_daemon_cost_relative_to_explicit_probe(self) -> None:
        from scripts.task_bootstrap import collect_live_runtime_state

        class SlowDaemon:
            def __call__(self, command, _cwd, _timeout):
                if command[:4] == ("codex", "app-server", "daemon", "version"):
                    time.sleep(0.05)
                    return subprocess.CompletedProcess(command, 0, "{}", "")
                return subprocess.CompletedProcess(command, 0, "", "")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "codex").mkdir()
            times_default: list[float] = []
            times_probe: list[float] = []
            for _ in range(3):
                start = time.perf_counter()
                collect_live_runtime_state(
                    cwd=root,
                    local_config=root / "missing.toml",
                    codex_home=root / "codex",
                    state_dir=root / "state",
                    runner=SlowDaemon(),
                    probe_daemons=False,
                )
                times_default.append(time.perf_counter() - start)
                start = time.perf_counter()
                collect_live_runtime_state(
                    cwd=root,
                    local_config=root / "missing.toml",
                    codex_home=root / "codex",
                    state_dir=root / "state",
                    runner=SlowDaemon(),
                    probe_daemons=True,
                )
                times_probe.append(time.perf_counter() - start)
            self.assertLess(min(times_default), min(times_probe))
            self.assertGreaterEqual(min(times_probe), 0.04)


if __name__ == "__main__":
    unittest.main()
