"""Small static checks for the portable repository surface."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_FILES = {
    "AGENTS.md",
    "README.md",
    "WORKFLOW.md",
    "package/global-portable.md",
    "package/local.example.toml",
    "package/v23-primary.config.toml.in",
    "package/agents/v23-executor.toml.in",
    "package/agents/v23-reviewer.toml.in",
    ".agents/skills/engineering-delivery/SKILL.md",
    ".agents/skills/grok-execution/SKILL.md",
    "scripts/grok_execution.py",
    "scripts/install.py",
    "scripts/doctor.py",
    "scripts/github_delivery.py",
    "scripts/runtime.py",
    "scripts/task_bootstrap.py",
}


def repository_files() -> list[Path]:
    return [
        path
        for path in ROOT.rglob("*")
        if path.is_file()
        and not {".git", ".venv", "__pycache__", "artifacts"}.intersection(
            path.relative_to(ROOT).parts
        )
        and path.suffix != ".pyc"
    ]


class RepositoryContractTests(unittest.TestCase):
    def test_required_portable_surface_exists(self) -> None:
        missing = sorted(path for path in REQUIRED_FILES if not (ROOT / path).is_file())
        self.assertEqual(missing, [], f"missing portable repository files: {missing}")

    def test_portable_identity_excludes_local_machine_content(self) -> None:
        agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        installed = (ROOT / "package/global-portable.md").read_text(encoding="utf-8")
        self.assertIn("Principal Engineer / Research Scientist", agents)
        self.assertIn("不靠仪式感制造正确", agents)
        self.assertIn("不靠仪式感制造正确", installed)
        self.assertNotRegex(agents, re.compile(r"/(?:Users|home)/"))
        self.assertNotRegex(agents, re.compile(r"(?i)(?:api[_-]?key|access[_-]?token|secret)\s*="))

    def test_portable_surface_excludes_local_opening(self) -> None:
        local_opening = "\u4f1f\u5927\u7684\u4eae\u4eae"
        roots = (
            ROOT / "AGENTS.md",
            ROOT / "README.md",
            ROOT / "README.zh-CN.md",
            ROOT / "WORKFLOW.md",
            ROOT / "package",
            ROOT / ".agents",
            ROOT / "scripts",
            ROOT / "docs",
            ROOT / "evals",
            ROOT / "tests",
        )
        violations = [
            str(path.relative_to(ROOT))
            for root in roots
            for path in ([root] if root.is_file() else root.rglob("*"))
            if path.is_file()
            and path.suffix != ".pyc"
            and "__pycache__" not in path.parts
            and local_opening in path.read_text(encoding="utf-8")
        ]
        self.assertEqual(violations, [], f"local opening leaked into portable files: {violations}")

    def test_retired_directory_shapes_are_absent(self) -> None:
        """Use generic shapes so the test does not preserve old release names."""

        blocked_names = {"contracts", "hooks", "rules"}
        blocked_version_directory = re.compile(r"^v\d+$", re.IGNORECASE)
        violations: list[str] = []
        for path in repository_files():
            relative = path.relative_to(ROOT)
            if any(part in blocked_names for part in relative.parts):
                violations.append(str(relative))
            if any(blocked_version_directory.fullmatch(part) for part in relative.parts):
                violations.append(str(relative))
        self.assertEqual(violations, [], f"retired layout paths found: {sorted(violations)}")

    def test_portable_files_do_not_embed_local_bindings(self) -> None:
        roots = [
            ROOT / "AGENTS.md",
            ROOT / "README.md",
            ROOT / "WORKFLOW.md",
            ROOT / "package",
            ROOT / ".agents",
            ROOT / "scripts",
            ROOT / "docs",
        ]
        files = [
            path
            for root in roots
            for path in ([root] if root.is_file() else root.rglob("*"))
            if path.is_file() and path.suffix != ".pyc" and "__pycache__" not in path.parts
        ]
        violations: list[str] = []
        for path in files:
            text = path.read_text(encoding="utf-8")
            if "/Users/" in text or "/home/" in text:
                violations.append(str(path.relative_to(ROOT)))
            if re.search(r"(?im)^\s*(?:push|review|github_)?(?:account|identity|user)\s*=", text):
                violations.append(str(path.relative_to(ROOT)))
        self.assertEqual(violations, [], f"local bindings leaked into portable files: {violations}")

    def test_skill_is_progressively_loaded(self) -> None:
        skill = (ROOT / ".agents/skills/engineering-delivery/SKILL.md").read_text(encoding="utf-8")
        self.assertLessEqual(len(skill), 12_000)
        for reference in (
            "references/github-flow.md",
            "references/code-review.md",
            "references/cpp.md",
            "references/python.md",
            "references/research.md",
            "references/source-index.md",
            "references/tool-routing.md",
        ):
            self.assertIn(reference, skill)

    def test_required_tool_bootstrap_is_portable_and_stop_hook_free(self) -> None:
        bootstrap = (ROOT / "scripts/task_bootstrap.py").read_text(encoding="utf-8")
        installer = (ROOT / "scripts/install.py").read_text(encoding="utf-8")
        portable = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        installed_portable = (ROOT / "package/global-portable.md").read_text(encoding="utf-8")
        self.assertIn("UserPromptSubmit", installer)
        self.assertIn("CodeGraph", portable)
        self.assertIn("Semble", portable)
        self.assertIn("RTK", portable)
        self.assertIn("不是可选路由", installed_portable)
        self.assertNotIn("相关性判断", installed_portable)
        self.assertIn("默认自动进入 GitHub 交付", installed_portable)
        self.assertIn("意图审查", installed_portable)
        self.assertIn("不要求另一次明确“开始”", installed_portable)
        self.assertIn("request_user_input", installed_portable)
        self.assertNotIn("默认直接推进任务", installed_portable)
        self.assertIn("无法安全发现且会实质改变结果", installed_portable)
        self.assertIn("$grok-execution", installed_portable)
        self.assertIn("reasoning effort 固定为 `low`", installed_portable)
        self.assertIn("QUOTA_EXHAUSTED", installed_portable)
        self.assertIn("GROK_EXECUTION_BLOCKED", installed_portable)
        self.assertNotIn("[[hooks.Stop]]", installer)
        self.assertNotIn("/Users/", bootstrap)
        self.assertNotIn("/home/", bootstrap)

    def test_portable_participatory_questioning_contract(self) -> None:
        agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        workflow = (ROOT / "WORKFLOW.md").read_text(encoding="utf-8")
        skill = (ROOT / ".agents/skills/engineering-delivery/SKILL.md").read_text(encoding="utf-8")
        portable = (ROOT / "package/global-portable.md").read_text(encoding="utf-8")
        retired = (
            "默认立即开始有效工作",
            "只有会改变实现结果的实质性歧义才询问",
            "Ask only when a material ambiguity",
            "Ask before implementation only when a material ambiguity",
            "默认直接推进任务",
            "只有会实质改变结果的歧义才问用户",
        )
        for text in (agents, workflow, skill, portable):
            for phrase in retired:
                self.assertNotIn(phrase, text)
            self.assertIn("counterevidence" if text in (workflow, skill) else "反证", text)
            self.assertIn("materially changes" if text in (workflow, skill) else "实质改变", text)
            self.assertIn("Disagree explicitly" if text in (workflow, skill) else "明确反对", text)
        for text in (agents, portable):
            self.assertIn("简单事实查询", text)
            self.assertIn("request_user_input", text)
            self.assertIn("不要求另一次明确“开始”", text)
            self.assertIn("有界只读调查", text)
        for text in (workflow, skill):
            self.assertIn("fully explicit trivial operations may proceed directly", text)
            self.assertIn("request_user_input", text)
            self.assertIn("proceed without a separate explicit start", text)
            self.assertIn("bounded read-only investigation is allowed", text.lower())
        discuss_heading = workflow.index("## DISCUSS")
        repo_change_heading = workflow.index("## REPO_CHANGE")
        shared_marker = workflow.index("This contract applies to both `discuss` and `repo_change`.")
        self.assertLess(shared_marker, discuss_heading)
        self.assertLess(discuss_heading, repo_change_heading)
        discuss_section = workflow[discuss_heading:repo_change_heading]
        self.assertNotIn(
            "inspect only what is needed and answer directly",
            discuss_section,
        )
        self.assertIn("Only the simple/direct exceptions", discuss_section)
        self.assertIn("stay read-only", discuss_section)
        for text in (agents, workflow, skill, portable):
            self.assertIn("retire" if text in (workflow, skill) else "退休", text)

    def test_empty_structured_answers_pause_without_mutation_and_re_present(self) -> None:
        agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        workflow = (ROOT / "WORKFLOW.md").read_text(encoding="utf-8")
        portable = (ROOT / "package/global-portable.md").read_text(encoding="utf-8")
        architecture = (ROOT / "docs/architecture.md").read_text(encoding="utf-8")
        skill = (ROOT / ".agents/skills/grok-execution/SKILL.md").read_text(encoding="utf-8")
        executor = (ROOT / "package/agents/v23-executor.toml.in").read_text(encoding="utf-8")
        bridge = (ROOT / "scripts/grok_execution.py").read_text(encoding="utf-8")
        for text in (agents, portable):
            self.assertIn("空的结构化", text)
            self.assertIn("request_user_input", text)
            self.assertIn("暂停", text)
            self.assertIn("原问重现", text)
            self.assertIn("不得写入或推断默认值", text)
        self.assertIn("Empty structured `request_user_input` answers", workflow)
        self.assertIn("no mutation", workflow)
        self.assertIn("re-presented on resume", workflow)
        self.assertIn(
            "Empty structured `request_user_input` answers stay unanswered and paused", architecture
        )
        self.assertIn("separately spawned generic Luna-low native subagent", skill)
        self.assertIn("separately spawned generic Luna-low native subagent", workflow)
        self.assertIn("MUST be supervised", skill)
        self.assertIn("MUST be supervised", workflow)
        self.assertIn("MUST be supervised", architecture)
        self.assertIn("supervisor completion event", skill)
        self.assertIn("supervisor completion event", workflow)
        self.assertIn("does not directly narrate or poll Grok", skill)
        self.assertIn("does not directly narrate or poll Grok", workflow)
        self.assertNotIn("may supervise", skill)
        self.assertNotIn("may supervise", workflow)
        self.assertNotIn("may supervise", architecture)
        self.assertNotIn("may supervise", agents)
        self.assertNotIn("may supervise", portable)
        self.assertIn("quota-exhaustion-only fallback", skill)
        self.assertIn("`v23_executor` remains the quota-exhaustion-only fallback", workflow)
        self.assertIn("is not the supervisor", workflow)
        self.assertIn("start_new_session=True", bridge)
        self.assertNotIn("preexec_fn", bridge)
        self.assertIn("os.execvpe", bridge)
        self.assertIn("CHILD_LAUNCHER_FLAG", bridge)
        self.assertIn("killpg", bridge)
        self.assertIn("_SpawnCleanupToken", bridge)
        self.assertIn("kill_candidate_group", bridge)
        self.assertIn("_bounded_reap", bridge)
        self.assertIn("if completed is None:", bridge)
        self.assertIn("install_termination_handlers", bridge)
        self.assertIn("_register_dedicated_pgid", bridge)
        self.assertIn("_unregister_dedicated_pgid", bridge)
        self.assertIn("dedicated process group validation failed", bridge)
        self.assertIn("SIGTERM", skill)
        self.assertIn("SIGTERM", workflow)
        self.assertIn("SIGTERM", architecture)
        self.assertIn("every normal return and BaseException path", skill)
        self.assertIn("every normal return and BaseException path", workflow)
        self.assertIn("every normal return and BaseException path", architecture)
        self.assertIn("You are the native fallback executor for one scoped change", executor)
        self.assertNotIn("Two mutually exclusive modes apply", executor)
        self.assertNotIn("def structured_answers_unanswered", bridge)
        self.assertNotIn("def apply_request_user_input", bridge)
        self.assertNotIn("def is_user_visible_update", bridge)


if __name__ == "__main__":
    unittest.main()
