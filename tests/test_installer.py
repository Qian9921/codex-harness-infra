from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

import scripts.install as install_mod
from scripts.doctor import doctor
from scripts.install import (
    MARKER,
    PORTABLE_KIND,
    InstallError,
    block_body,
    install,
    replace_managed_block,
    sha256_bytes,
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
        (repo / "scripts/bounded_search.py").write_text("print('search')\n", encoding="utf-8")
        (repo / "scripts/executor_routing.py").write_text("print('routing')\n", encoding="utf-8")
        (repo / "scripts/runtime.py").write_text("print('runtime')\n", encoding="utf-8")
        (repo / "scripts/delivery_policy.py").write_text(
            (ROOT / "scripts/delivery_policy.py").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
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
        (grok_skill / "references").mkdir()
        (grok_skill / "references/grok-process-lifecycle.md").write_text(
            "# lifecycle\n", encoding="utf-8"
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
            self.assertIn("--codex-home", config_text)
            self.assertIn("--state-dir", config_text)
            self.assertIn(str(state_dir), config_text)
            from scripts.task_bootstrap import HOOK_TIMEOUT_SECONDS

            self.assertIn(f"timeout = {HOOK_TIMEOUT_SECONDS}", config_text)
            self.assertTrue((codex_home / "harness/v23/task_bootstrap.py").is_file())
            self.assertTrue((codex_home / "bin/grok-execution.py").is_file())
            self.assertTrue((codex_home / "bin/bounded-search.py").is_file())
            self.assertTrue((codex_home / "skills/grok-execution/SKILL.md").is_file())
            self.assertTrue(
                (
                    codex_home / "skills/grok-execution/references/grok-process-lifecycle.md"
                ).is_file()
            )
            self.assertFalse((codex_home / "AGENTS.override.md").exists())
            manifest = json.loads((state_dir / "install.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["agents_path"], str(agents))

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

    def test_install_refuses_unowned_nonempty_global_override(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo, local = self.make_repo(root)
            codex_home, state_dir = root / "codex", root / "state"
            codex_home.mkdir()
            agents = codex_home / "AGENTS.md"
            agents.write_text("Personal rule.\n", encoding="utf-8")
            override = codex_home / "AGENTS.override.md"
            override.write_text("Arbitrary override.\n", encoding="utf-8")

            with self.assertRaisesRegex(InstallError, "unowned nonempty AGENTS.override.md"):
                install(repo, codex_home, local, state_dir)
            self.assertEqual(override.read_text(encoding="utf-8"), "Arbitrary override.\n")
            self.assertEqual(agents.read_text(encoding="utf-8"), "Personal rule.\n")
            self.assertFalse((state_dir / "install.json").exists())
            self.assertFalse((codex_home / "agents/v23-executor.toml").exists())

    def test_install_refuses_symlink_override_without_touching_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo, local = self.make_repo(root)
            codex_home, state_dir = root / "codex", root / "state"
            target = root / "personal-override.md"
            target.write_text("Keep this target.\n", encoding="utf-8")
            codex_home.mkdir()
            override = codex_home / "AGENTS.override.md"
            override.symlink_to(target)

            with self.assertRaisesRegex(InstallError, "unowned nonempty AGENTS.override.md"):
                install(repo, codex_home, local, state_dir)
            self.assertTrue(override.is_symlink())
            self.assertEqual(target.read_text(encoding="utf-8"), "Keep this target.\n")
            self.assertFalse((codex_home / "AGENTS.md").exists())

    def test_install_refuses_directory_override_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo, local = self.make_repo(root)
            codex_home, state_dir = root / "codex", root / "state"
            codex_home.mkdir()
            agents = codex_home / "AGENTS.md"
            agents.write_text("Personal rule.\n", encoding="utf-8")
            override = codex_home / "AGENTS.override.md"
            override.mkdir()
            (override / "nested.txt").write_text("keep\n", encoding="utf-8")

            with self.assertRaisesRegex(InstallError, "unsafe global override"):
                install(repo, codex_home, local, state_dir)
            self.assertTrue(override.is_dir())
            self.assertEqual((override / "nested.txt").read_text(encoding="utf-8"), "keep\n")
            self.assertEqual(agents.read_text(encoding="utf-8"), "Personal rule.\n")
            self.assertFalse((codex_home / "config.toml").exists())
            self.assertFalse((state_dir / "install.json").exists())

    def test_install_migrates_existing_override_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo, local = self.make_repo(root)
            codex_home, state_dir = root / "codex", root / "state"
            install(repo, codex_home, local, state_dir)
            agents = codex_home / "AGENTS.md"
            override = codex_home / "AGENTS.override.md"
            override.write_text(agents.read_text(encoding="utf-8"), encoding="utf-8")
            agents.write_text("Personal rule.\n", encoding="utf-8")
            manifest_path = state_dir / "install.json"
            record = json.loads(manifest_path.read_text(encoding="utf-8"))
            record["agents_path"] = str(override)
            manifest_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(InstallError, "unowned nonempty AGENTS.override.md"):
                install(repo, codex_home, local, state_dir)
            self.assertTrue(override.exists())
            self.assertEqual(agents.read_text(encoding="utf-8"), "Personal rule.\n")
            self.assertEqual(
                json.loads(manifest_path.read_text(encoding="utf-8"))["agents_path"], str(override)
            )

    KNOWN_V21_FIXTURE = (
        "# Codex Governance Infra V21 personal kernel\n"
        "\n"
        "This is the V21 policy installed at `CODEX_HOME/AGENTS.md`.\n"
        "\n"
        "Obsolete route tokens: grok-4.5-flash-native, v21_executor, CODEX_HOME/AGENTS.md kernel.\n"
        "Keep none of this after V23 migration.\n"
    )

    def _with_known_v21_fixture(self) -> None:
        blob = self.KNOWN_V21_FIXTURE.encode()
        install_mod.KNOWN_V21_KERNEL_SIZE = len(blob)
        install_mod.KNOWN_V21_KERNEL_SHA256 = sha256_bytes(blob)

    def _restore_known_v21_constants(self) -> None:
        install_mod.KNOWN_V21_KERNEL_SIZE = 10192
        install_mod.KNOWN_V21_KERNEL_SHA256 = (
            "49045df930cac1d0148575ad3f94b193383e4eee8abdb54e3472ccbef6a73bf7"
        )

    def test_install_replaces_known_v21_kernel_and_uninstall_does_not_restore_it(self) -> None:
        self._with_known_v21_fixture()
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                repo, local = self.make_repo(root)
                codex_home, state_dir = root / "codex", root / "state"
                codex_home.mkdir()
                agents = codex_home / "AGENTS.md"
                agents.write_text(self.KNOWN_V21_FIXTURE, encoding="utf-8")

                install(repo, codex_home, local, state_dir)
                installed = agents.read_text(encoding="utf-8")
                self.assertNotIn("# Codex Governance Infra V21 personal kernel", installed)
                self.assertNotIn(
                    "This is the V21 policy installed at `CODEX_HOME/AGENTS.md`.", installed
                )
                self.assertNotIn("grok-4.5-flash-native", installed)
                self.assertNotIn("v21_executor", installed)
                self.assertIn(MARKER, installed)

                uninstall(codex_home, state_dir)
                remaining = agents.read_text(encoding="utf-8") if agents.exists() else ""
                self.assertNotIn("# Codex Governance Infra V21 personal kernel", remaining)
                self.assertNotIn(
                    "This is the V21 policy installed at `CODEX_HOME/AGENTS.md`.", remaining
                )
                self.assertNotIn("grok-4.5-flash-native", remaining)
                self.assertNotIn("v21_executor", remaining)
        finally:
            self._restore_known_v21_constants()

    def test_install_refuses_v21_plus_appended_personal_content_before_mutation(self) -> None:
        self._with_known_v21_fixture()
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                repo, local = self.make_repo(root)
                codex_home, state_dir = root / "codex", root / "state"
                codex_home.mkdir()
                agents = codex_home / "AGENTS.md"
                original = self.KNOWN_V21_FIXTURE + "Keep my personal appendix.\n"
                agents.write_bytes(original.encode())
                before = agents.read_bytes()
                with self.assertRaises(InstallError) as raised:
                    install(repo, codex_home, local, state_dir)
                self.assertIn("unrecognized V21-like AGENTS.md", str(raised.exception))
                self.assertEqual(agents.read_bytes(), before)
                self.assertEqual(agents.read_bytes().decode(), original)
                self.assertFalse((state_dir / "install.json").exists())
                self.assertFalse((codex_home / "agents/v23-executor.toml").exists())
        finally:
            self._restore_known_v21_constants()

    def test_install_refuses_signature_like_unknown_v21_variant(self) -> None:
        self._with_known_v21_fixture()
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                repo, local = self.make_repo(root)
                codex_home, state_dir = root / "codex", root / "state"
                codex_home.mkdir()
                agents = codex_home / "AGENTS.md"
                variant = (
                    "# Codex Governance Infra V21 personal kernel\n"
                    "\n"
                    "This is the V21 policy installed at `CODEX_HOME/AGENTS.md`.\n"
                    "\n"
                    "Unknown local variant, not the authorized digest.\n"
                )
                agents.write_text(variant, encoding="utf-8")
                before = agents.read_bytes()
                with self.assertRaises(InstallError) as raised:
                    install(repo, codex_home, local, state_dir)
                self.assertIn("unrecognized V21-like AGENTS.md", str(raised.exception))
                self.assertEqual(agents.read_bytes(), before)
                self.assertFalse((state_dir / "install.json").exists())
        finally:
            self._restore_known_v21_constants()

    def test_install_does_not_treat_ordinary_agents_as_v21_kernel(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo, local = self.make_repo(root)
            codex_home, state_dir = root / "codex", root / "state"
            codex_home.mkdir()
            agents = codex_home / "AGENTS.md"
            original = "Personal rule mentioning V21 casually.\nThis is not the installed kernel.\n"
            agents.write_text(original, encoding="utf-8")
            install(repo, codex_home, local, state_dir)
            installed = agents.read_text(encoding="utf-8")
            self.assertIn("Personal rule mentioning V21 casually.", installed)
            self.assertIn(original.strip(), installed)

    def test_manifest_write_failure_keeps_override_and_retry_succeeds_fresh(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo, local = self.make_repo(root)
            codex_home, state_dir = root / "codex", root / "state"
            codex_home.mkdir()
            agents = codex_home / "AGENTS.md"
            agents.write_text("Personal rule.\n", encoding="utf-8")
            override = codex_home / "AGENTS.override.md"
            override.write_text("Arbitrary override.\n", encoding="utf-8")
            manifest_path = state_dir / "install.json"
            with self.assertRaisesRegex(InstallError, "unowned nonempty AGENTS.override.md"):
                install(repo, codex_home, local, state_dir)
            self.assertTrue(override.is_file())
            self.assertEqual(override.read_text(encoding="utf-8"), "Arbitrary override.\n")
            self.assertEqual(agents.read_text(encoding="utf-8"), "Personal rule.\n")
            self.assertFalse(manifest_path.exists())
            self.assertFalse((codex_home / "agents/v23-executor.toml").exists())
            override.write_text("", encoding="utf-8")
            install(repo, codex_home, local, state_dir)
            self.assertTrue(override.is_file())
            self.assertEqual(override.read_text(encoding="utf-8"), "")
            self.assertIn(MARKER, agents.read_text(encoding="utf-8"))
            self.assertTrue(manifest_path.is_file())

    def test_manifest_write_failure_keeps_override_and_retry_succeeds_migration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo, local = self.make_repo(root)
            codex_home, state_dir = root / "codex", root / "state"
            install(repo, codex_home, local, state_dir)
            agents = codex_home / "AGENTS.md"
            override = codex_home / "AGENTS.override.md"
            previous_agents = agents.read_text(encoding="utf-8")
            previous_executor = (codex_home / "agents/v23-executor.toml").read_text(
                encoding="utf-8"
            )
            override.write_text(previous_agents, encoding="utf-8")
            agents.write_text("Personal rule.\n", encoding="utf-8")
            manifest_path = state_dir / "install.json"
            previous_manifest = manifest_path.read_text(encoding="utf-8")
            record = json.loads(previous_manifest)
            record["agents_path"] = str(override)
            manifest_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
            restored_manifest = manifest_path.read_text(encoding="utf-8")

            with self.assertRaisesRegex(InstallError, "unowned nonempty AGENTS.override.md"):
                install(repo, codex_home, local, state_dir)
            self.assertTrue(override.is_file())
            self.assertEqual(override.read_text(encoding="utf-8"), previous_agents)
            self.assertEqual(agents.read_text(encoding="utf-8"), "Personal rule.\n")
            self.assertEqual(manifest_path.read_text(encoding="utf-8"), restored_manifest)
            self.assertEqual(
                (codex_home / "agents/v23-executor.toml").read_text(encoding="utf-8"),
                previous_executor,
            )

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
            self.assertTrue((codex_home / "bin/bounded-search.py").is_file())
            self.assertTrue((codex_home / "bin/executor-routing.py").is_file())
            self.assertTrue((codex_home / "bin/runtime.py").is_file())
            self.assertTrue((codex_home / "harness/v23/executor_routing.py").is_file())
            self.assertTrue((codex_home / "harness/v23/runtime.py").is_file())
            self.assertTrue(
                (
                    codex_home / "skills/grok-execution/references/grok-process-lifecycle.md"
                ).is_file()
            )
            installed_help = subprocess.run(
                [sys.executable, str(codex_home / "bin/grok-execution.py"), "--help"],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(installed_help.returncode, 0, installed_help.stderr)
            self.assertIn("run", installed_help.stdout)
            self.assertFalse((codex_home / "AGENTS.override.md").exists())
            self.assertIn(MARKER, (codex_home / "AGENTS.md").read_text(encoding="utf-8"))
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
            self.assertFalse((codex_home / "bin/bounded-search.py").exists())
            self.assertFalse((codex_home / "bin/executor-routing.py").exists())
            self.assertFalse((codex_home / "bin/runtime.py").exists())
            self.assertFalse((codex_home / "harness/v23/runtime.py").exists())
            self.assertFalse(
                (codex_home / "skills/grok-execution/references/grok-process-lifecycle.md").exists()
            )

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

    def test_native_only_install_upgrade_uninstall_preserves_user_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            local = root / "local.toml"
            local.write_text(
                """
[models]
primary = "primary-model"
executor = "future-native-slug"
executor_effort = "low"
reviewer = "reviewer-model"

[opening]
instruction = "Local-only opening."

[routing]
selection = "native_only"

[[routing.executors]]
id = "native"
backend = "codex"
capabilities = ["implementation", "tests"]
tools = ["workspace-write"]
cost_preference = "unknown"
availability = "configured"
""".lstrip(),
                encoding="utf-8",
            )
            codex_home, state_dir = root / "codex", root / "state"
            agents = codex_home / "AGENTS.md"
            codex_home.mkdir()
            agents.write_text("Personal rule.\n", encoding="utf-8")
            install(ROOT, codex_home, local, state_dir)
            executor = (codex_home / "agents/v23-executor.toml").read_text(encoding="utf-8")
            self.assertIn("native_only", executor)
            self.assertIn("Grok is not required", executor)
            self.assertIn("future-native-slug", executor)
            self.assertIn("Personal rule.", agents.read_text(encoding="utf-8"))
            self.assertTrue((codex_home / "bin/executor-routing.py").is_file())
            config = (codex_home / "config.toml").read_text(encoding="utf-8")
            self.assertIn("V23 native implementation executor", config)
            install(ROOT, codex_home, local, state_dir)
            self.assertIn("Personal rule.", agents.read_text(encoding="utf-8"))
            uninstall(codex_home, state_dir)
            self.assertTrue(agents.exists())
            self.assertIn("Personal rule.", agents.read_text(encoding="utf-8"))
            self.assertFalse((codex_home / "bin/executor-routing.py").exists())

    def test_blank_opening_without_github_installs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            local = root / "local.toml"
            local.write_text(
                """
[models]
primary = "primary-model"
executor = "native-slug"
reviewer = "reviewer-model"

[opening]
instruction = ""

[routing]
selection = "native_only"

[[routing.executors]]
id = "native"
backend = "codex"
capabilities = ["implementation"]
tools = ["workspace-write"]
cost_preference = "unknown"
availability = "configured"
""".lstrip(),
                encoding="utf-8",
            )
            codex_home, state_dir = root / "codex", root / "state"
            install(ROOT, codex_home, local, state_dir)
            agents = (codex_home / "AGENTS.md").read_text(encoding="utf-8")
            self.assertIn(MARKER, agents)
            self.assertNotIn("Local-only opening", agents)
            executor = tomllib.loads((codex_home / "agents/v23-executor.toml").read_text())
            self.assertEqual(executor["model"], "native-slug")
            self.assertIn("native_only", executor["developer_instructions"])
            report = doctor(
                codex_home, local, ROOT / "tests", check_github=True, probe_required_tools=False
            )
            self.assertTrue(report["ok"], report["checks"])
            install(ROOT, codex_home, local, state_dir)
            uninstall_report = uninstall(codex_home, state_dir)
            self.assertTrue(any("removed V23 installation" in line for line in uninstall_report))
            self.assertFalse((codex_home / "agents/v23-executor.toml").exists())

    def test_candidate_retirement_and_user_edit_preservation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            local = root / "local.toml"
            two = """
[models]
primary = "primary-model"
executor = "default-native"
reviewer = "reviewer-model"

[opening]
instruction = ""

[routing]
selection = "paid_preferred"

[[routing.executors]]
id = "fast"
backend = "codex"
actual_model = "different-model"
capabilities = ["implementation"]
tools = ["workspace-write"]
cost_preference = "paid_included"
availability = "configured"

[[routing.executors]]
id = "native"
backend = "codex"
capabilities = ["implementation"]
tools = ["workspace-write"]
cost_preference = "unknown"
availability = "configured"
"""
            local.write_text(two.lstrip() + "\n", encoding="utf-8")
            codex_home, state_dir = root / "codex", root / "state"
            install(ROOT, codex_home, local, state_dir)
            extra = codex_home / "agents/v23-executor-native.toml"
            self.assertTrue(extra.is_file())
            self.assertEqual(
                tomllib.loads((codex_home / "agents/v23-executor.toml").read_text())["model"],
                "different-model",
            )
            one = two.replace(
                """
[[routing.executors]]
id = "fast"
backend = "codex"
actual_model = "different-model"
capabilities = ["implementation"]
tools = ["workspace-write"]
cost_preference = "paid_included"
availability = "configured"
""",
                "",
            )
            local.write_text(one.lstrip() + "\n", encoding="utf-8")
            install(ROOT, codex_home, local, state_dir)
            self.assertFalse(extra.exists())
            local.write_text(two.lstrip() + "\n", encoding="utf-8")
            install(ROOT, codex_home, local, state_dir)
            extra.write_text(extra.read_text(encoding="utf-8") + "user edit\n", encoding="utf-8")
            local.write_text(one.lstrip() + "\n", encoding="utf-8")
            with self.assertRaisesRegex(InstallError, "modified obsolete"):
                install(ROOT, codex_home, local, state_dir)
            self.assertIn("user edit", extra.read_text(encoding="utf-8"))
            extra.write_text(
                extra.read_text(encoding="utf-8").replace("user edit\n", ""), encoding="utf-8"
            )
            install(ROOT, codex_home, local, state_dir)
            uninstall(codex_home, state_dir)
            self.assertFalse((codex_home / "agents/v23-executor.toml").exists())
            self.assertFalse(extra.exists())

    def test_cross_home_state_reuse_does_not_delete_foreign_assets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo, local = self.make_repo(root)
            state = root / "state"
            home_a = root / "homeA"
            home_b = root / "homeB"
            install(repo, home_a, local, state)
            owned = home_a / "agents/v23-executor.toml"
            self.assertTrue(owned.is_file())
            previous = owned.read_text(encoding="utf-8")
            with self.assertRaisesRegex(InstallError, "does not belong to this Codex home"):
                install(repo, home_b, local, state)
            self.assertEqual(owned.read_text(encoding="utf-8"), previous)
            self.assertFalse((home_b / "agents/v23-executor.toml").exists())
            self.assertTrue((state / "install.json").is_file())

    def test_symlink_parent_is_not_used_for_obsolete_retirement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo, local = self.make_repo(root)
            home = root / "home"
            outside = root / "outside-agents"
            outside.mkdir()
            planted = outside / "keep.txt"
            planted.write_text("keep\n", encoding="utf-8")
            home.mkdir(parents=True)
            (home / "agents").symlink_to(outside)
            with self.assertRaisesRegex(InstallError, "unsafe V23 asset parent|target escapes"):
                install(repo, home, local, root / "state")
            self.assertEqual(planted.read_text(encoding="utf-8"), "keep\n")
            self.assertTrue((home / "agents").is_symlink())

    def test_obsolete_retirement_refuses_symlink_without_deleting_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            local = root / "local.toml"
            two = """
[models]
primary = "primary-model"
executor = "default-native"
reviewer = "reviewer-model"

[opening]
instruction = ""

[routing]
selection = "paid_preferred"

[[routing.executors]]
id = "fast"
backend = "codex"
actual_model = "different-model"
capabilities = ["implementation"]
tools = ["workspace-write"]
cost_preference = "paid_included"
availability = "configured"

[[routing.executors]]
id = "native"
backend = "codex"
capabilities = ["implementation"]
tools = ["workspace-write"]
cost_preference = "unknown"
availability = "configured"
"""
            local.write_text(two.lstrip() + "\n", encoding="utf-8")
            codex_home, state_dir = root / "codex", root / "state"
            install(ROOT, codex_home, local, state_dir)
            extra = codex_home / "agents/v23-executor-native.toml"
            self.assertTrue(extra.is_file())
            personal = codex_home / "personal.toml"
            personal.write_text(extra.read_text(encoding="utf-8"), encoding="utf-8")
            extra.unlink()
            extra.symlink_to(personal)
            one = two.replace(
                """
[[routing.executors]]
id = "native"
backend = "codex"
capabilities = ["implementation"]
tools = ["workspace-write"]
cost_preference = "unknown"
availability = "configured"
""",
                "",
            )
            local.write_text(one.lstrip() + "\n", encoding="utf-8")
            default_role = (codex_home / "agents/v23-executor.toml").read_text(encoding="utf-8")
            with self.assertRaisesRegex(InstallError, "refusing to retire symlink"):
                install(ROOT, codex_home, local, state_dir)
            self.assertTrue(extra.is_symlink())
            self.assertEqual(
                personal.read_text(encoding="utf-8"), extra.read_text(encoding="utf-8")
            )
            self.assertTrue(personal.is_file())
            self.assertFalse(personal.is_symlink())
            self.assertEqual(
                (codex_home / "agents/v23-executor.toml").read_text(encoding="utf-8"),
                default_role,
            )
            self.assertTrue((state_dir / "install.json").is_file())

    def test_mode_only_upgrade_to_native_only_with_dormant_grok(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            local = root / "local.toml"
            paid = """
[models]
primary = "primary-model"
executor = "native-slug"
reviewer = "reviewer-model"

[opening]
instruction = ""

[routing]
selection = "paid_preferred"

[[routing.executors]]
id = "grok_build"
backend = "grok"
capabilities = ["implementation"]
tools = ["workspace-write"]
cost_preference = "paid_included"
availability = "configured"

[[routing.executors]]
id = "native"
backend = "codex"
capabilities = ["implementation"]
tools = ["workspace-write"]
cost_preference = "unknown"
availability = "configured"

[routing.fallback]
permit = ["quota_exhausted"]
target = "native"
"""
            local.write_text(paid.lstrip() + "\n", encoding="utf-8")
            codex_home, state_dir = root / "codex", root / "state"
            install(ROOT, codex_home, local, state_dir)
            local.write_text(paid.replace("paid_preferred", "native_only").lstrip() + "\n")
            install(ROOT, codex_home, local, state_dir)
            role = (codex_home / "agents/v23-executor.toml").read_text(encoding="utf-8")
            self.assertNotIn("GROK_FALLBACK_NOT_AUTHORIZED", role)
            self.assertIn("native_only route is complete", role)
            report = doctor(
                codex_home, local, ROOT / "tests", check_github=False, probe_required_tools=False
            )
            checks = {check["name"]: check for check in report["checks"]}
            self.assertTrue(checks["executor_routing"]["ok"])
            (codex_home / "bin/grok-execution.py").unlink()
            report = doctor(
                codex_home, local, ROOT / "tests", check_github=False, probe_required_tools=False
            )
            checks = {check["name"]: check for check in report["checks"]}
            self.assertTrue(checks["grok_execution_route"]["ok"])
            install(ROOT, codex_home, local, state_dir)
            uninstall_report = uninstall(codex_home, state_dir)
            self.assertTrue(any("removed V23 installation" in line for line in uninstall_report))

    def test_selected_candidate_model_is_installed_on_role(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            local = root / "local.toml"
            local.write_text(
                """
[models]
primary = "primary-model"
executor = "default-native"
reviewer = "reviewer-model"

[opening]
instruction = ""

[routing]
selection = "paid_preferred"

[[routing.executors]]
id = "fast"
backend = "codex"
actual_model = "different-model"
capabilities = ["implementation"]
tools = ["workspace-write"]
cost_preference = "paid_included"
availability = "configured"

[[routing.executors]]
id = "native"
backend = "codex"
capabilities = ["implementation"]
tools = ["workspace-write"]
cost_preference = "unknown"
availability = "configured"
""".lstrip(),
                encoding="utf-8",
            )
            codex_home = root / "codex"
            install(ROOT, codex_home, local, root / "state")
            from scripts.executor_routing import parse_policy, select_executor

            policy = parse_policy(tomllib.loads(local.read_text(encoding="utf-8")))
            result = select_executor(
                policy, capabilities=("implementation",), tools=("workspace-write",)
            )
            self.assertEqual(result.actual_model, "different-model")
            role_path = codex_home / result.invocation["config_file"]
            agent = tomllib.loads(role_path.read_text(encoding="utf-8"))
            self.assertEqual(agent["model"], "different-model")
            self.assertEqual(agent["name"], result.invocation["agent"])
            registered = tomllib.loads((codex_home / "config.toml").read_text())["agents"]
            self.assertEqual(
                registered[result.invocation["agent"]]["config_file"],
                result.invocation["config_file"],
            )

    def _clean_python_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        env.pop("PYTHONHOME", None)
        return env

    def test_installed_cli_selects_native_outside_source_tree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "cwd"
            outside.mkdir()
            local = root / "local.toml"
            local.write_text(
                """
[models]
primary = "primary-model"
executor = "native-slug-future"
reviewer = "reviewer-model"

[opening]
instruction = ""

[routing]
selection = "native_only"

[[routing.executors]]
id = "native"
backend = "codex"
capabilities = ["implementation"]
tools = ["workspace-write"]
cost_preference = "unknown"
availability = "configured"
""".lstrip(),
                encoding="utf-8",
            )
            codex_home, state_dir = root / "codex", root / "state"
            install(ROOT, codex_home, local, state_dir)
            (codex_home / "bin/grok-execution.py").unlink()
            cli = codex_home / "bin/executor-routing.py"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(cli),
                    "select",
                    "--local-config",
                    str(local),
                    "--capability",
                    "implementation",
                    "--tool",
                    "workspace-write",
                ],
                cwd=outside,
                env=self._clean_python_env(),
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["selected_id"], "native")
            self.assertEqual(payload["actual_model"], "native-slug-future")
            self.assertEqual(payload["invocation"]["agent"], "v23_executor")
            agent = tomllib.loads((codex_home / payload["invocation"]["config_file"]).read_text())
            self.assertEqual(agent["model"], "native-slug-future")
            hook = subprocess.run(
                [sys.executable, str(codex_home / "harness/v23/task_bootstrap.py"), "--help"],
                cwd=outside,
                env=self._clean_python_env(),
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(hook.returncode, 0, hook.stderr)
            report = doctor(
                codex_home, local, ROOT / "tests", check_github=True, probe_required_tools=False
            )
            checks = {check["name"]: check for check in report["checks"]}
            self.assertTrue(checks["executor_routing_entrypoint"]["ok"])
            self.assertTrue(report["ok"], report["checks"])
            install(ROOT, codex_home, local, state_dir)
            self.assertTrue((codex_home / "bin/runtime.py").is_file())
            uninstall(codex_home, state_dir)
            self.assertFalse((codex_home / "bin/runtime.py").exists())
            self.assertFalse((codex_home / "harness/v23/runtime.py").exists())

    def test_installed_cli_legacy_still_selects_grok(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "cwd"
            outside.mkdir()
            local = root / "local.toml"
            local.write_text(
                """
[models]
primary = "primary-model"
executor = "luna-low"
reviewer = "reviewer-model"

[opening]
instruction = "Local-only opening."
""".lstrip(),
                encoding="utf-8",
            )
            codex_home = root / "codex"
            install(ROOT, codex_home, local, root / "state")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(codex_home / "bin/executor-routing.py"),
                    "select",
                    "--local-config",
                    str(local),
                    "--capability",
                    "implementation",
                    "--tool",
                    "workspace-write",
                ],
                cwd=outside,
                env=self._clean_python_env(),
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["backend"], "grok")
            self.assertEqual(payload["actual_model"], "grok-4.6-build")

    def test_missing_installed_runtime_is_doctor_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            local = root / "local.toml"
            local.write_text(
                """
[models]
primary = "primary-model"
executor = "native-slug"
reviewer = "reviewer-model"

[opening]
instruction = ""

[routing]
selection = "native_only"

[[routing.executors]]
id = "native"
backend = "codex"
capabilities = ["implementation"]
tools = ["workspace-write"]
cost_preference = "unknown"
availability = "configured"
""".lstrip(),
                encoding="utf-8",
            )
            codex_home = root / "codex"
            install(ROOT, codex_home, local, root / "state")
            (codex_home / "bin/runtime.py").unlink()
            (codex_home / "harness/v23/runtime.py").unlink()
            outside = root / "cwd"
            outside.mkdir()
            hook = subprocess.run(
                [sys.executable, str(codex_home / "harness/v23/task_bootstrap.py"), "--help"],
                cwd=outside,
                env=self._clean_python_env(),
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(hook.returncode, 0, hook.stdout)
            self.assertIn("runtime", (hook.stderr + hook.stdout).casefold())
            report = doctor(
                codex_home, local, ROOT / "tests", check_github=False, probe_required_tools=False
            )
            checks = {check["name"]: check for check in report["checks"]}
            self.assertFalse(checks["executor_routing_entrypoint"]["ok"])
            self.assertFalse(report["ok"])
            self.assertEqual(checks["executor_routing"]["detail"], "native_only")

    def test_install_refuses_unowned_runtime_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo, local = self.make_repo(root)
            codex_home = root / "codex"
            sidecar = codex_home / "bin/runtime.py"
            sidecar.parent.mkdir(parents=True)
            sidecar.write_text("personal runtime\n", encoding="utf-8")
            with self.assertRaisesRegex(InstallError, "unowned asset"):
                install(repo, codex_home, local, root / "state")
            self.assertEqual(sidecar.read_text(encoding="utf-8"), "personal runtime\n")

    def test_installed_native_roles_and_bound_prompt_carry_reuse_clause(self) -> None:
        marker = "Name or similarity is not fitness"
        configs = {
            "native_only": """
[models]
primary = "p"
executor = "native-slug"
reviewer = "r"

[routing]
selection = "native_only"

[[routing.executors]]
id = "native"
backend = "codex"
capabilities = ["implementation"]
tools = ["workspace-write"]
cost_preference = "unknown"
availability = "configured"
""",
            "paid_preferred": """
[models]
primary = "p"
executor = "native-slug"
reviewer = "r"

[routing]
selection = "paid_preferred"

[[routing.executors]]
id = "grok_build"
backend = "grok"
capabilities = ["implementation"]
tools = ["workspace-write"]
cost_preference = "paid_included"
availability = "configured"

[[routing.executors]]
id = "native"
backend = "codex"
capabilities = ["implementation"]
tools = ["workspace-write"]
cost_preference = "unknown"
availability = "configured"

[routing.fallback]
permit = ["quota_exhausted"]
target = "native"
""",
            "paid_strict_native": """
[models]
primary = "p"
executor = "native-slug"
reviewer = "r"

[routing]
selection = "paid_strict"

[[routing.executors]]
id = "native"
backend = "codex"
capabilities = ["implementation"]
tools = ["workspace-write"]
cost_preference = "paid_included"
availability = "configured"
""",
            "quota_fallback": """
[models]
primary = "p"
executor = "native-slug"
reviewer = "r"

[routing]
selection = "paid_strict"

[[routing.executors]]
id = "grok_build"
backend = "grok"
capabilities = ["implementation"]
tools = ["workspace-write"]
cost_preference = "paid_included"
availability = "configured"

[[routing.executors]]
id = "native"
backend = "codex"
capabilities = ["implementation"]
tools = ["workspace-write"]
cost_preference = "unknown"
availability = "configured"

[routing.fallback]
permit = ["quota_exhausted"]
target = "native"
""",
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, text in configs.items():
                local = root / f"{name}.toml"
                local.write_text(text.lstrip(), encoding="utf-8")
                codex_home, state_dir = root / name / "codex", root / name / "state"
                install(ROOT, codex_home, local, state_dir)
                installed = (codex_home / "agents/v23-executor.toml").read_text(encoding="utf-8")
                self.assertIn(marker, installed)
                template = (ROOT / "package/agents/v23-executor.toml.in").read_text(
                    encoding="utf-8"
                )
                if name == "native_only":
                    self.assertIn("native_only route is complete", installed)
                    self.assertNotIn("native_only route is complete", template)
                grok_bridge = (codex_home / "bin/grok-execution.py").read_text(encoding="utf-8")
                self.assertIn(marker, grok_bridge)
                self.assertIn("Perform only the work the TASK actually authorizes", grok_bridge)
                self.assertIn("claim-appropriate evidence", grok_bridge)
                self.assertNotIn("_write_work_authorized", grok_bridge)
                portable = (codex_home / "AGENTS.md").read_text(encoding="utf-8")
                self.assertIn(marker, portable)
                tool_marker = "Optional tools are selected only for a concrete need"
                self.assertIn(tool_marker, installed)
                self.assertIn("TOOL_SELECTION_GUIDANCE", grok_bridge)
                self.assertIn("not installed by this Harness", grok_bridge)
                self.assertIn('f"{TOOL_SELECTION_GUIDANCE}', grok_bridge)
                self.assertIn("tgrep 为实验性", portable)
                self.assertIn("本 Harness 不安装它", portable)
                self.assertIn("focused CodeGraph", portable)
                self.assertIn("可选工具仅在有具体需要时调用", portable)
                routing_text = (
                    codex_home / "skills/engineering-delivery/references/tool-routing.md"
                ).read_text(encoding="utf-8")
                self.assertIn("codegraph callers", routing_text)
                self.assertIn("status --json", routing_text)
                self.assertIn("rtk pytest", routing_text)
                self.assertNotIn("status -p", routing_text)
                self.assertFalse((codex_home / "skills/codegraph/SKILL.md").exists())


if __name__ == "__main__":
    unittest.main()
