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
        and not {".git", ".venv", "__pycache__"}.intersection(path.relative_to(ROOT).parts)
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
        self.assertIn("2–3 个高价值、互斥", installed_portable)
        self.assertIn("不要求另一次明确“开始”", installed_portable)
        self.assertIn("request_user_input", installed_portable)
        self.assertNotIn("默认直接推进任务", installed_portable)
        self.assertNotIn("只有会实质改变结果的歧义才问用户", installed_portable)
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
        for text in (agents, portable):
            self.assertIn("简单事实查询", text)
            self.assertIn("request_user_input", text)
            self.assertIn("不要求另一次明确“开始”", text)
            self.assertIn("有界只读调查", text)
        for text in (workflow, skill):
            self.assertIn("fully explicit trivial operations may proceed directly", text)
            self.assertIn("request_user_input", text)
            self.assertIn("proceed without a separate explicit start", text)
            self.assertIn("Bounded read-only investigation is allowed", text)
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


if __name__ == "__main__":
    unittest.main()
