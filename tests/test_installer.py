from __future__ import annotations

import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

from scripts.install import (
    MARKER,
    PORTABLE_KIND,
    InstallError,
    block_body,
    install,
    replace_managed_block,
    uninstall,
)

ROOT = Path(__file__).resolve().parents[1]


class InstallerTests(unittest.TestCase):
    def make_repo(self, root: Path) -> tuple[Path, Path]:
        repo = root / "repo"
        (repo / "package/agents").mkdir(parents=True)
        (repo / "scripts").mkdir()
        (repo / "package/v23-primary.config.toml.in").write_text(
            'model = "{{primary_model}}"\nmodel_reasoning_effort = "{{primary_effort}}"\n'
            'review_model = "{{reviewer_model}}"\n',
            encoding="utf-8",
        )
        (repo / "package/agents/v23-executor.toml.in").write_text(
            'name = "v23_executor"\ndescription = "Test executor."\n'
            'model = "{{executor_model}}"\nmodel_reasoning_effort = "{{executor_effort}}"\n'
            'developer_instructions = "Test executor instructions."\n',
            encoding="utf-8",
        )
        (repo / "package/agents/v23-reviewer.toml.in").write_text(
            'name = "v23_reviewer"\ndescription = "Test reviewer."\n'
            'model = "{{reviewer_model}}"\nmodel_reasoning_effort = "high"\n'
            'developer_instructions = "Test reviewer instructions."\n',
            encoding="utf-8",
        )
        (repo / "package/global-portable.md").write_text(
            "Work identity: Principal Engineer / Research Scientist.\n", encoding="utf-8"
        )
        (repo / "scripts/task_bootstrap.py").write_text("print('bootstrap')\n", encoding="utf-8")
        (repo / "scripts/grok_execution.py").write_text("print('grok bridge')\n", encoding="utf-8")
        skill = repo / ".agents/skills/engineering-delivery"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            "---\nname: engineering-delivery\ndescription: Test.\n---\n", encoding="utf-8"
        )
        grok_skill = repo / ".agents/skills/grok-execution"
        grok_skill.mkdir(parents=True)
        (grok_skill / "SKILL.md").write_text(
            "---\nname: grok-execution\ndescription: Test.\n---\n", encoding="utf-8"
        )
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
""".strip()
            + "\n",
            encoding="utf-8",
        )
        return repo, local

    def test_replace_managed_block_preserves_unmanaged_content(self) -> None:
        before = (
            "Personal rule.\n\n"
            f"<!-- BEGIN {MARKER} {PORTABLE_KIND} -->\nold\n"
            f"<!-- END {MARKER} {PORTABLE_KIND} -->\n\nTail.\n"
        )
        after = replace_managed_block(before, PORTABLE_KIND, "new")
        self.assertIn("Personal rule.", after)
        self.assertIn("Tail.", after)
        self.assertIn("new", after)
        self.assertNotIn("old", after)

    def test_replace_managed_block_rejects_partial_marker(self) -> None:
        begin = f"<!-- BEGIN {MARKER} {PORTABLE_KIND} -->"
        end = f"<!-- END {MARKER} {PORTABLE_KIND} -->"
        with self.assertRaises(InstallError):
            replace_managed_block(f"before\n{begin}\nbody\n", PORTABLE_KIND, "new")
        with self.assertRaises(InstallError):
            replace_managed_block(f"before\n{end}\n", PORTABLE_KIND, "new")

    def test_install_and_uninstall_preserve_unmanaged_agents_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo, local = self.make_repo(root)
            codex_home = root / "codex"
            state_dir = root / "state"
            agents = codex_home / "AGENTS.md"
            codex_home.mkdir()
            agents.write_text("Personal rule.\n", encoding="utf-8")
            (codex_home / "config.toml").write_text("user_setting = true\n", encoding="utf-8")

            install(repo, codex_home, local, state_dir)
            installed = agents.read_text(encoding="utf-8")
            self.assertIn("Personal rule.", installed)
            self.assertIn("Principal Engineer / Research Scientist", installed)
            self.assertIn("Local-only opening.", installed)
            config_text = (codex_home / "config.toml").read_text()
            self.assertIn("user_setting = true", config_text)
            self.assertIn("[[hooks.UserPromptSubmit]]", config_text)
            self.assertTrue((codex_home / "harness/v23/task_bootstrap.py").is_file())
            self.assertTrue((codex_home / "bin/grok-execution.py").is_file())
            self.assertTrue((codex_home / "skills/grok-execution/SKILL.md").is_file())

            uninstall(codex_home, state_dir)
            self.assertEqual(agents.read_text(encoding="utf-8"), "Personal rule.\n")
            self.assertEqual((codex_home / "config.toml").read_text(), "user_setting = true\n")
            self.assertFalse((state_dir / "install.json").exists())

    def test_install_refuses_unowned_collision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo, local = self.make_repo(root)
            codex_home = root / "codex"
            state_dir = root / "state"
            collision = codex_home / "agents" / "v23-executor.toml"
            collision.parent.mkdir(parents=True)
            collision.write_text("user-owned = true\n", encoding="utf-8")

            with self.assertRaises(InstallError):
                install(repo, codex_home, local, state_dir)
            self.assertEqual(collision.read_text(encoding="utf-8"), "user-owned = true\n")

    def test_uninstall_keeps_user_modified_installed_assets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo, local = self.make_repo(root)
            codex_home = root / "codex"
            state_dir = root / "state"
            install(repo, codex_home, local, state_dir)

            agent = codex_home / "agents" / "v23-executor.toml"
            skill = codex_home / "skills/engineering-delivery/SKILL.md"
            agent.write_text(agent.read_text(encoding="utf-8") + "user edit\n", encoding="utf-8")
            skill.write_text(skill.read_text(encoding="utf-8") + "user edit\n", encoding="utf-8")

            report = uninstall(codex_home, state_dir)
            self.assertTrue(agent.exists())
            self.assertTrue(skill.exists())
            self.assertIn("user edit", agent.read_text(encoding="utf-8"))
            self.assertIn("user edit", skill.read_text(encoding="utf-8"))
            self.assertTrue(any("preserved entire" in line for line in report))

    def test_uninstall_preserves_agent_assets_when_config_block_was_edited(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo, local = self.make_repo(root)
            codex_home, state_dir = root / "codex", root / "state"
            install(repo, codex_home, local, state_dir)
            config = codex_home / "config.toml"
            config.write_text(
                config.read_text(encoding="utf-8").replace(
                    "V23 quota-exhaustion-only", "Edited V23"
                ),
                encoding="utf-8",
            )

            report = uninstall(codex_home, state_dir)
            self.assertTrue((codex_home / "agents/v23-executor.toml").exists())
            self.assertTrue((codex_home / "skills/engineering-delivery/SKILL.md").exists())
            self.assertTrue(any("preserved entire" in line for line in report))

    def test_install_refuses_partial_global_marker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo, local = self.make_repo(root)
            codex_home = root / "codex"
            state_dir = root / "state"
            codex_home.mkdir()
            begin = f"<!-- BEGIN {MARKER} {PORTABLE_KIND} -->"
            (codex_home / "AGENTS.md").write_text(f"{begin}\nleftover\n", encoding="utf-8")

            with self.assertRaises(InstallError):
                install(repo, codex_home, local, state_dir)

    def test_install_refuses_unmarked_duplicate_agent_registration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo, local = self.make_repo(root)
            codex_home = root / "codex"
            codex_home.mkdir()
            config = codex_home / "config.toml"
            original = (
                '[agents.v23_executor]\ndescription = "personal"\nconfig_file = "personal.toml"\n'
            )
            config.write_text(original, encoding="utf-8")

            with self.assertRaises(InstallError):
                install(repo, codex_home, local, root / "state")
            self.assertEqual(config.read_text(encoding="utf-8"), original)

    def test_install_refuses_active_global_path_transition(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo, local = self.make_repo(root)
            codex_home, state_dir = root / "codex", root / "state"
            install(repo, codex_home, local, state_dir)
            (codex_home / "AGENTS.override.md").write_text("Personal override.\n", encoding="utf-8")

            with self.assertRaisesRegex(InstallError, "path changed"):
                install(repo, codex_home, local, state_dir)
            self.assertIn(MARKER, (codex_home / "AGENTS.md").read_text(encoding="utf-8"))
            self.assertNotIn(
                MARKER, (codex_home / "AGENTS.override.md").read_text(encoding="utf-8")
            )

    def test_uninstall_allows_reinstall_after_global_override_is_created(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo, local = self.make_repo(root)
            codex_home, state_dir = root / "codex", root / "state"
            install(repo, codex_home, local, state_dir)
            uninstall(codex_home, state_dir)
            (codex_home / "AGENTS.override.md").write_text("Personal override.\n", encoding="utf-8")

            install(repo, codex_home, local, state_dir)
            self.assertIn(MARKER, (codex_home / "AGENTS.override.md").read_text(encoding="utf-8"))

    def test_install_refuses_symlink_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo, local = self.make_repo(root)
            codex_home = root / "codex"
            state_dir = root / "state"
            target = root / "personal.toml"
            target.write_text("personal = true\n", encoding="utf-8")
            collision = codex_home / "agents" / "v23-executor.toml"
            collision.parent.mkdir(parents=True)
            collision.symlink_to(target)

            with self.assertRaises(InstallError):
                install(repo, codex_home, local, state_dir)
            self.assertEqual(target.read_text(encoding="utf-8"), "personal = true\n")

    def test_install_checks_state_directory_before_mutating_codex_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo, local = self.make_repo(root)
            codex_home, state_dir = root / "codex", root / "state"
            codex_home.mkdir()
            (codex_home / "AGENTS.md").write_text("Personal rule.\n", encoding="utf-8")
            state_dir.write_text("not a directory\n", encoding="utf-8")

            with self.assertRaises(InstallError):
                install(repo, codex_home, local, state_dir)
            self.assertEqual(
                (codex_home / "AGENTS.md").read_text(encoding="utf-8"), "Personal rule.\n"
            )
            self.assertFalse((codex_home / "config.toml").exists())

    def test_install_rejects_state_symlink_before_mutating_codex_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo, local = self.make_repo(root)
            codex_home, target = root / "codex", root / "personal-state"
            target.mkdir()
            state_link = root / "state-link"
            state_link.symlink_to(target)

            with self.assertRaises(InstallError):
                install(repo, codex_home, local, state_link)
            self.assertFalse((codex_home / "config.toml").exists())
            self.assertFalse((target / "install.json").exists())

    def test_uninstall_rejects_state_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo, local = self.make_repo(root)
            codex_home, state_dir = root / "codex", root / "state"
            install(repo, codex_home, local, state_dir)
            state_link = root / "state-link"
            state_link.symlink_to(state_dir)

            with self.assertRaises(InstallError):
                uninstall(codex_home, state_link)
            self.assertTrue((codex_home / "v23-primary.config.toml").exists())

    def test_install_rejects_invalid_rendered_agent_toml(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo, local = self.make_repo(root)
            text = local.read_text(encoding="utf-8")
            local.write_text(
                text.replace('primary = "primary-model"', "primary = 'bad\\q'"),
                encoding="utf-8",
            )
            codex_home = root / "codex"

            with self.assertRaises(InstallError):
                install(repo, codex_home, local, root / "state")
            self.assertFalse((codex_home / "config.toml").exists())

    def test_install_rejects_unsupported_explicit_python_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo, local = self.make_repo(root)
            local.write_text(
                local.read_text(encoding="utf-8")
                + '\n[runtime]\npython = "/not-a-python-runtime"\n',
                encoding="utf-8",
            )

            with self.assertRaisesRegex(InstallError, "runtime"):
                install(repo, root / "codex", local, root / "state")

    def test_real_repository_blank_home_smoke(self) -> None:
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
            codex_home, state_dir = root / "codex", root / "state"

            install(ROOT, codex_home, local, state_dir)
            self.assertTrue((codex_home / "v23-primary.config.toml").is_file())
            self.assertTrue((codex_home / "agents/v23-executor.toml").is_file())
            self.assertTrue((codex_home / "agents/v23-reviewer.toml").is_file())
            self.assertTrue((codex_home / "skills/engineering-delivery/SKILL.md").is_file())
            self.assertTrue((codex_home / "skills/grok-execution/SKILL.md").is_file())
            self.assertTrue((codex_home / "harness/v23/task_bootstrap.py").is_file())
            self.assertTrue((codex_home / "bin/grok-execution.py").is_file())
            installed_help = subprocess.run(
                [sys.executable, str(codex_home / "bin/grok-execution.py"), "--help"],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(installed_help.returncode, 0, installed_help.stderr)
            self.assertIn("run", installed_help.stdout)
            self.assertIn('[agents."v23_executor"]', (codex_home / "config.toml").read_text())
            self.assertIn("[[hooks.UserPromptSubmit]]", (codex_home / "config.toml").read_text())
            tomllib.loads((codex_home / "config.toml").read_text())
            primary = tomllib.loads((codex_home / "v23-primary.config.toml").read_text())
            self.assertEqual(primary["model"], "primary-model")
            self.assertEqual(primary["model_reasoning_effort"], "medium")
            self.assertEqual(primary["review_model"], "reviewer-model")
            for path, expected_name in (
                (codex_home / "agents/v23-executor.toml", "v23_executor"),
                (codex_home / "agents/v23-reviewer.toml", "v23_reviewer"),
            ):
                agent = tomllib.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(agent["name"], expected_name)
                self.assertTrue(agent["description"])
                self.assertTrue(agent["developer_instructions"])

            uninstall(codex_home, state_dir)
            self.assertFalse((codex_home / "v23-primary.config.toml").exists())
            self.assertFalse((codex_home / "agents/v23-executor.toml").exists())
            self.assertFalse((codex_home / "skills/engineering-delivery").exists())
            self.assertFalse((codex_home / "skills/grok-execution").exists())
            self.assertFalse((codex_home / "harness/v23/task_bootstrap.py").exists())
            self.assertFalse((codex_home / "bin/grok-execution.py").exists())

    def test_install_preserves_unowned_hook_trust_sections(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo, local = self.make_repo(root)
            codex_home = root / "codex"
            codex_home.mkdir()
            old_hook = codex_home.resolve() / "hooks.json"
            config = codex_home / "config.toml"
            config.write_text(
                (
                    "[hooks.state]\n"
                    f'[hooks.state."{old_hook}:stop:0:0"]\n'
                    'trusted_hash = "old"\n'
                    '[hooks.state."/user-owned/hooks.json:stop:0:0"]\n'
                    'trusted_hash = "keep"\n'
                ),
                encoding="utf-8",
            )

            install(repo, codex_home, local, root / "state")

            rendered = config.read_text(encoding="utf-8")
            self.assertIn(f"{old_hook}:stop:0:0", rendered)
            self.assertIn("/user-owned/hooks.json:stop:0:0", rendered)
            self.assertIsNotNone(block_body(rendered, "CONFIG"))
            self.assertIn("UserPromptSubmit", tomllib.loads(rendered)["hooks"])


if __name__ == "__main__":
    unittest.main()
