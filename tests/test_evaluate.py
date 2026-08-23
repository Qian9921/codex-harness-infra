"""Controlled, end-to-end-in-helper scenarios for the V23 evaluation suite."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.github_delivery import DeliveryFlow, FlowError, GHClient, ReviewVerdict
from scripts.install import install, uninstall
from scripts.task_bootstrap import _semble_health_scope, probe_tools

ROOT = Path(__file__).resolve().parents[1]


class ToolRunner:
    """Controlled tool environment that records each actual Harness invocation."""

    def __init__(self, root: Path, *, timeout_codegraph: bool = False) -> None:
        self.root = root
        self.timeout_codegraph = timeout_codegraph
        self.calls: list[tuple[str, ...]] = []

    def __call__(
        self, command: tuple[str, ...], _cwd: Path | None, timeout: int
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append(command)
        if command[:4] == ("git", "-C", str(self.root), "rev-parse"):
            output = f"{self.root}\n" if command[-1] == "--show-toplevel" else ".git/info/exclude\n"
            return subprocess.CompletedProcess(command, 0, output, "")
        if command[0] == str(self.root / "codegraph"):
            if self.timeout_codegraph:
                raise subprocess.TimeoutExpired(command, timeout)
            if command[1] == "status":
                return subprocess.CompletedProcess(command, 0, "Not initialized\n", "")
        return subprocess.CompletedProcess(command, 0, "ok\n", "")


class GithubRunner:
    """Controlled GitHub CLI transcript for adapter-level scenario grading."""

    def __init__(self, responses: list[tuple[int, str, str]]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, ...]] = []
        self.config_dirs: list[str] = []

    def __call__(
        self, command: tuple[str, ...], environment: dict[str, str]
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append(command)
        self.config_dirs.append(environment.get("GH_CONFIG_DIR", ""))
        if not self.responses:
            raise AssertionError(f"unexpected command: {command}")
        code, stdout, stderr = self.responses.pop(0)
        return subprocess.CompletedProcess(command, code, stdout, stderr)


def user(login: str) -> tuple[int, str, str]:
    return 0, json.dumps({"login": login}), ""


def head(sha: str) -> tuple[int, str, str]:
    return 0, json.dumps({"head": {"sha": sha}}), ""


def no_threads() -> tuple[int, str, str]:
    return (
        0,
        json.dumps(
            {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "reviewThreads": {"nodes": [], "pageInfo": {"hasNextPage": False}}
                        }
                    }
                }
            }
        ),
        "",
    )


class HarnessScenarioEvals(unittest.TestCase):
    def tool_environment(
        self,
        *,
        timeout_codegraph: bool = False,
        configured: tuple[str, ...] = ("codegraph", "semble", "rtk"),
    ) -> tuple[tempfile.TemporaryDirectory[str], Path, dict[str, str], ToolRunner]:
        directory = tempfile.TemporaryDirectory()
        root = Path(directory.name).resolve()
        (root / ".git/info").mkdir(parents=True)
        tools: dict[str, str] = {}
        for name in configured:
            executable = root / name
            executable.write_text("", encoding="utf-8")
            tools[name] = str(executable)
        return directory, root, tools, ToolRunner(root, timeout_codegraph=timeout_codegraph)

    def flow(self, runner: GithubRunner) -> DeliveryFlow:
        return DeliveryFlow(
            GHClient(Path("/profiles/author"), runner),
            GHClient(Path("/profiles/reviewer"), runner),
            "author-one",
            "reviewer-two",
        )

    def test_discuss_is_read_only(self) -> None:
        workflow = (ROOT / "WORKFLOW.md").read_text(encoding="utf-8")
        self.assertIn("`discuss`", workflow)
        self.assertIn(
            "Do not create a branch, commit, Pull Request, review, comment, or merge.", workflow
        )

    def test_normal_change_defaults_to_delivery(self) -> None:
        workflow = (ROOT / "WORKFLOW.md").read_text(encoding="utf-8")
        self.assertIn("`repo_change + github_write` is the default", workflow)
        self.assertIn("understand → implement → verify → commit → push → Pull Request", workflow)

    def test_local_only_is_an_explicit_opt_out(self) -> None:
        policy = (ROOT / "package/global-portable.md").read_text(encoding="utf-8")
        self.assertIn("只有用户明确要求“仅本地”时才不外送", policy)

    def test_consequential_actions_stay_separate(self) -> None:
        workflow = (ROOT / "WORKFLOW.md").read_text(encoding="utf-8")
        self.assertIn(
            "production, accounts, data, releases, or another irreversible external system",
            workflow,
        )
        self.assertIn("Neither authorizes unrelated external actions.", workflow)

    def test_bootstrap_operates_all_three_tools(self) -> None:
        directory, root, tools, runner = self.tool_environment()
        with directory:
            results = probe_tools(root, "Inspect delivery adapter.", tools, runner=runner)
            self.assertTrue(all(result.ok for result in results))
            commands = runner.calls
            self.assertTrue(
                any(command[:2] == (tools["codegraph"], "files") for command in commands)
            )
            self.assertTrue(any(command[:2] == (tools["semble"], "search") for command in commands))
            self.assertTrue(any(command[:2] == (tools["rtk"], "git") for command in commands))

    def test_missing_tool_does_not_skip_remaining_tools(self) -> None:
        directory, root, tools, runner = self.tool_environment(configured=("rtk",))
        with directory:
            results = probe_tools(root, "Check tooling.", tools, runner=runner)
            self.assertEqual([result.ok for result in results], [False, False, True])
            self.assertTrue(any(command[0] == tools["rtk"] for command in runner.calls))

    def test_codegraph_timeout_does_not_skip_other_tools(self) -> None:
        directory, root, tools, runner = self.tool_environment(timeout_codegraph=True)
        with directory:
            results = probe_tools(root, "Check bounded tool timeouts.", tools, runner=runner)
            self.assertEqual([result.ok for result in results], [False, True, True])
            self.assertTrue(any(command[0] == tools["semble"] for command in runner.calls))
            self.assertTrue(any(command[0] == tools["rtk"] for command in runner.calls))

    def test_semble_health_search_uses_owned_scope(self) -> None:
        directory, root, tools, runner = self.tool_environment()
        with directory:
            probe_tools(root, "Search source.", tools, runner=runner)
            command = next(command for command in runner.calls if command[0] == tools["semble"])
            self.assertEqual(command[-1], str(_semble_health_scope()))
            self.assertEqual(command[3], "code")

    def test_distinct_github_identities_pass_preflight(self) -> None:
        runner = GithubRunner([user("author-one"), user("reviewer-two")])
        self.assertEqual(self.flow(runner).preflight(), ("author-one", "reviewer-two"))
        self.assertEqual(runner.config_dirs, ["/profiles/author", "/profiles/reviewer"])

    def test_same_github_identity_is_rejected(self) -> None:
        runner = GithubRunner([user("same"), user("SAME")])
        with self.assertRaisesRegex(FlowError, "different"):
            self.flow(runner).preflight()

    def test_stale_review_is_rejected(self) -> None:
        runner = GithubRunner([head("new-head")])
        with self.assertRaisesRegex(FlowError, "stale"):
            GHClient(Path("/profiles/reviewer"), runner).submit_review(
                "owner/repo", 23, ReviewVerdict("old-head", "APPROVE", "Reviewed.")
            )

    def test_old_approval_blocks_merge(self) -> None:
        reviews = json.dumps(
            [{"state": "APPROVED", "commit_id": "old", "user": {"login": "reviewer-two"}}]
        )
        runner = GithubRunner(
            [
                user("author-one"),
                user("reviewer-two"),
                head("current"),
                no_threads(),
                (0, "[]", ""),
                (0, reviews, ""),
            ]
        )
        with self.assertRaisesRegex(FlowError, "latest reviewer"):
            self.flow(runner).merge_if_ready("owner/repo", 23, "current")

    def test_failed_ci_blocks_merge(self) -> None:
        runner = GithubRunner(
            [
                user("author-one"),
                user("reviewer-two"),
                head("current"),
                no_threads(),
                (0, '[{"state":"FAILURE"}]', ""),
            ]
        )
        with self.assertRaisesRegex(FlowError, "required checks"):
            self.flow(runner).merge_if_ready("owner/repo", 23, "current")

    def test_unresolved_thread_blocks_merge(self) -> None:
        unresolved = json.dumps(
            {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "reviewThreads": {
                                "nodes": [{"isResolved": False, "path": "x", "line": 1}],
                                "pageInfo": {"hasNextPage": False},
                            }
                        }
                    }
                }
            }
        )
        runner = GithubRunner(
            [user("author-one"), user("reviewer-two"), head("current"), (0, unresolved, "")]
        )
        with self.assertRaisesRegex(FlowError, "unresolved"):
            self.flow(runner).merge_if_ready("owner/repo", 23, "current")

    def test_head_change_blocks_merge(self) -> None:
        runner = GithubRunner([user("author-one"), user("reviewer-two"), head("new-head")])
        with self.assertRaisesRegex(FlowError, "head changed"):
            self.flow(runner).merge_if_ready("owner/repo", 23, "old-head")

    def test_install_uninstall_preserves_local_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            local = root / "local.toml"
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
""".lstrip(),
                encoding="utf-8",
            )
            codex_home, state = root / "codex", root / "state"
            codex_home.mkdir()
            agents, config = codex_home / "AGENTS.md", codex_home / "config.toml"
            agents.write_text("Personal rule.\n", encoding="utf-8")
            config.write_text("user_setting = true\n", encoding="utf-8")
            install(ROOT, codex_home, local, state)
            rendered = config.read_text(encoding="utf-8")
            self.assertEqual(rendered.count("[[hooks.UserPromptSubmit]]"), 1)
            self.assertNotIn("[[hooks.Stop]]", rendered)
            uninstall(codex_home, state)
            self.assertEqual(agents.read_text(encoding="utf-8"), "Personal rule.\n")
            self.assertEqual(config.read_text(encoding="utf-8"), "user_setting = true\n")


if __name__ == "__main__":
    unittest.main()
