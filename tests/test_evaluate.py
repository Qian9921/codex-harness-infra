"""Controlled, end-to-end-in-helper scenarios for the V23 evaluation suite."""

from __future__ import annotations

import json
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path
from typing import ClassVar

from scripts.bounded_search import STATUS_NO_MATCH, STATUS_TIMEOUT, SearchError, run_search
from scripts.executor_routing import parse_policy, select_executor
from scripts.github_delivery import DeliveryFlow, FlowError, GHClient, ReviewVerdict
from scripts.install import install, uninstall
from scripts.task_bootstrap import RTK_VERIFIED_ROUTES, _semble_health_scope, probe_tools, run_hook

ROOT = Path(__file__).resolve().parents[1]


class ToolRunner:
    """Controlled tool environment that records each actual Harness invocation."""

    FRESH_STATUS: ClassVar[dict] = {
        "initialized": True,
        "fileCount": 3,
        "pendingChanges": {"added": 0, "modified": 0, "removed": 0},
        "index": {"state": "complete", "reindexRecommended": False},
        "lastIndexed": "2026-09-16T00:00:00.000Z",
    }

    def __init__(
        self,
        root: Path,
        *,
        timeout_codegraph: bool = False,
        codegraph_status: dict | None = None,
        rtk_fail_routes: tuple[str, ...] = (),
        rtk_probe_fail: bool = False,
    ) -> None:
        self.root = root
        self.timeout_codegraph = timeout_codegraph
        self.codegraph_status = dict(codegraph_status or self.FRESH_STATUS)
        self.rtk_fail_routes = set(rtk_fail_routes)
        self.rtk_probe_fail = rtk_probe_fail
        self.calls: list[tuple[str, ...]] = []

    def __call__(
        self, command: tuple[str, ...], _cwd: Path | None, timeout: int
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append(command)
        codegraph = str(self.root / "codegraph")
        rtk = str(self.root / "rtk")
        if command[:4] == ("git", "-C", str(self.root), "rev-parse"):
            output = f"{self.root}\n" if command[-1] == "--show-toplevel" else ".git/info/exclude\n"
            return subprocess.CompletedProcess(command, 0, output, "")
        if command[0] == codegraph:
            if self.timeout_codegraph:
                raise subprocess.TimeoutExpired(command, timeout)
            if command[1] == "status" and "--json" in command:
                return subprocess.CompletedProcess(
                    command, 0, json.dumps(self.codegraph_status) + "\n", ""
                )
            if command[1] == "status":
                state = "ok\n" if self.codegraph_status.get("initialized") else "Not initialized\n"
                return subprocess.CompletedProcess(command, 0, state, "")
            if command[1] in ("init", "sync"):
                self.codegraph_status["initialized"] = True
                self.codegraph_status["pendingChanges"] = {
                    "added": 0,
                    "modified": 0,
                    "removed": 0,
                }
                if isinstance(self.codegraph_status.get("index"), dict):
                    self.codegraph_status["index"]["reindexRecommended"] = False
                return subprocess.CompletedProcess(command, 0, "ok\n", "")
        if command[0] == rtk:
            if "--help" in command and command[1] in self.rtk_fail_routes:
                return subprocess.CompletedProcess(command, 1, "", f"unknown command {command[1]}")
            if self.rtk_probe_fail and "--help" not in command:
                return subprocess.CompletedProcess(command, 1, "", "routed probe exploded")
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
        self.assertIn("Omitted `[delivery]` is `local_only`", workflow)
        self.assertIn("request-delivery-only ephemeral file", workflow)
        self.assertIn("understand → implement → verify → commit → push → Pull Request", workflow)

    def test_local_only_is_an_explicit_opt_out(self) -> None:
        policy = (ROOT / "package/global-portable.md").read_text(encoding="utf-8")
        self.assertIn("local_only", policy)
        example = (ROOT / "package/local.example.toml").read_text(encoding="utf-8")
        self.assertIn('mode = "local_only"', example)

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
            codegraph_commands = [c for c in commands if c[0] == tools["codegraph"]]
            self.assertEqual(
                [c[1] for c in codegraph_commands], ["status", "sync", "status", "query"]
            )
            self.assertNotIn("files", [part for command in commands for part in command])
            self.assertTrue(any(command[:2] == (tools["semble"], "search") for command in commands))
            self.assertTrue(any(command[:2] == (tools["rtk"], "git") for command in commands))
            self.assertTrue(
                any(command[:3] == (tools["rtk"], "pytest", "--version") for command in commands)
            )

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

    def test_index_check_precedes_structural_query(self) -> None:
        directory, root, tools, runner = self.tool_environment()
        with directory:
            results = probe_tools(
                root,
                "Who calls assemble?",
                tools,
                runner=runner,
                codegraph_symbol="assemble",
            )
            self.assertTrue(results[0].ok)
            codegraph_calls = [c for c in runner.calls if c[0] == tools["codegraph"]]
            self.assertEqual(
                [c[1] for c in codegraph_calls], ["status", "sync", "status", "callers"]
            )
            self.assertNotIn("files", [part for command in runner.calls for part in command])

    def test_stale_index_refreshes_or_states_read_only_limit(self) -> None:
        directory, root, tools, runner = self.tool_environment()
        with directory:
            runner.codegraph_status["pendingChanges"] = {"added": 0, "modified": 1, "removed": 0}
            results = probe_tools(root, "Change assemble.", tools, runner=runner)
            self.assertTrue(results[0].ok)
            self.assertIn("sync", [c[1] for c in runner.calls if c[0] == tools["codegraph"]])
        directory, root, tools, runner = self.tool_environment()
        with directory:
            # CodeGraph has reported zero pending while sync then found changes,
            # so a zero pending count cannot skip the writable refresh.
            results = probe_tools(root, "Change assemble.", tools, runner=runner)
            self.assertTrue(results[0].ok)
            self.assertIn("sync", [c[1] for c in runner.calls if c[0] == tools["codegraph"]])
        directory, root, tools, runner = self.tool_environment()
        with directory:
            runner.codegraph_status["initialized"] = False
            results = probe_tools(
                root,
                "Read-only investigation.",
                tools,
                runner=runner,
                initialize_codegraph=False,
            )
            self.assertFalse(results[0].ok)
            self.assertIn("read-only", results[0].detail)
            self.assertIn("bounded-search", results[0].detail)
            self.assertNotIn("init", [c[1] for c in runner.calls if c[0] == tools["codegraph"]])
            self.assertNotIn("sync", [c[1] for c in runner.calls if c[0] == tools["codegraph"]])

    def test_rtk_routes_finite_verified_set_and_preserves_failure(self) -> None:
        directory, root, tools, runner = self.tool_environment()
        with directory:
            results = probe_tools(root, "Compact summary.", tools, runner=runner)
            self.assertTrue(results[2].ok)
            rtk_calls = [c for c in runner.calls if c[0] == tools["rtk"]]
            self.assertTrue(all(c[1] in RTK_VERIFIED_ROUTES for c in rtk_calls))
            self.assertFalse(any("--short" in c or "--json" in c for c in rtk_calls))
            self.assertTrue(any(c[:3] == (tools["rtk"], "pytest", "--version") for c in rtk_calls))
        failing_directory, failing_root, failing_tools, _failing_runner = self.tool_environment()
        with failing_directory:
            failing = ToolRunner(failing_root, rtk_probe_fail=True)
            failed = probe_tools(failing_root, "Compact summary.", failing_tools, runner=failing)[2]
            self.assertFalse(failed.ok)
            self.assertIn("routed probe exploded", failed.detail)
            self.assertIn("raw", failed.detail)

    def test_hook_does_not_block_unrelated_work(self) -> None:
        directory, root, tools, runner = self.tool_environment()
        with directory:
            local = root / "local.toml"
            local.write_text("[tools]\n", encoding="utf-8")
            payload = run_hook(
                root,
                "Continue an unrelated task.",
                local,
                codex_home=root / "codex",
                state_dir=root / "state",
                runner=runner,
            )
            context = payload["hookSpecificOutput"]["additionalContext"]
            self.assertIn("must not block", context)
            self.assertNotIn("Repair the failed required tool", context)
            self.assertFalse(
                any(command and command[0] == tools["codegraph"] for command in runner.calls)
            )

    def test_bounded_search_requires_explicit_scope(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "module").mkdir()
            (root / "module/app.py").write_text("unique_eval_token = True\n", encoding="utf-8")
            matched = run_search(root=root, pattern="unique_eval_token", paths=["module"])
            self.assertEqual(matched["status"], "match")
            missing = run_search(root=root, pattern="no-such-eval-token", paths=["module"])
            self.assertEqual(missing["status"], STATUS_NO_MATCH)
            self.assertNotEqual(missing["status"], STATUS_TIMEOUT)
            with self.assertRaises(SearchError):
                run_search(root=Path.home(), pattern="x", paths=[])

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

    def test_pi_only_routing_blocks_without_native_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            local = Path(directory) / "local.toml"
            local.write_text(
                """
[models]
primary = "primary-model"
executor = "executor-model"
reviewer = "reviewer-model"

[routing]
selection = "paid_strict"

[[routing.executors]]
id = "pi_one"
backend = "pi"
provider = "provider"
requested_model = "model"
actual_model = "model"
effort = "high"
capabilities = ["implementation", "tests", "git", "local_write"]
tools = ["workspace-write"]
cost_preference = "paid_included"
availability = "configured"
""".lstrip(),
                encoding="utf-8",
            )
            policy = parse_policy(tomllib.loads(local.read_text(encoding="utf-8")))
            selected = select_executor(
                policy, capabilities=["implementation"], tools=["workspace-write"]
            )
            self.assertEqual(selected.status, "selected")
            self.assertEqual(selected.backend, "pi")
            self.assertFalse(selected.fallback_authorized)
            self.assertEqual(policy.fallback_permit, ())
            local.write_text(
                local.read_text(encoding="utf-8").replace(
                    'availability = "configured"', 'availability = "unavailable"'
                ),
                encoding="utf-8",
            )
            blocked = select_executor(
                parse_policy(tomllib.loads(local.read_text(encoding="utf-8"))),
                capabilities=["implementation"],
                tools=["workspace-write"],
            )
            self.assertEqual(blocked.status, "blocked")
            self.assertIsNone(blocked.selected_id)
            self.assertIn("paid_strict", blocked.blocked)

    def test_pi_direct_review_uses_fresh_pi_session(self) -> None:
        workflow = (ROOT / "WORKFLOW.md").read_text(encoding="utf-8")
        skill = (ROOT / ".agents/skills/grok-execution/SKILL.md").read_text(encoding="utf-8")
        for text in (workflow, skill):
            self.assertIn("fresh read-only Pi session", text)
            self.assertIn("native reviewer is not required", text)
            self.assertNotIn("MUST be supervised", text)
        self.assertIn("no native fallback is authorized", skill)

    def test_claim_appropriate_evidence_guidance_is_precise(self) -> None:
        standards = (ROOT / "docs/engineering-standards.md").read_text(encoding="utf-8")
        portable = (ROOT / "package/global-portable.md").read_text(encoding="utf-8")
        self.assertIn("official documentation is not local runtime evidence", standards)
        self.assertIn("a single run is not general performance evidence", standards)
        self.assertIn("aggregate over runs, not an individual-request latency", standards)
        self.assertIn("whether one core was saturated", standards)
        self.assertIn("official documentation is not local runtime evidence", portable)


if __name__ == "__main__":
    unittest.main()
