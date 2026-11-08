"""Contracts for the always-on `core` plugin and its project-init tagging script."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import tempfile
import tomllib
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).parents[1]
CORE = ROOT / "plugins/core"
PROJECT_INIT = CORE / "skills/project-init/scripts/project_init.py"


def load_project_init():
    spec = importlib.util.spec_from_file_location("project_init", PROJECT_INIT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CoreContractTests(unittest.TestCase):
    def test_distributed_skills(self) -> None:
        self.assertEqual(
            {path.parent.name for path in (CORE / "skills").glob("*/SKILL.md")},
            {"behemoth-gpu-niceness", "progress-updates", "project-init", "service-hub"},
        )

    def test_core_is_installed_by_default_in_codex(self) -> None:
        catalog = json.loads((ROOT / ".agents/plugins/marketplace.json").read_text())
        entry = next(plugin for plugin in catalog["plugins"] if plugin["name"] == "core")
        self.assertEqual(entry["policy"]["installation"], "INSTALLED_BY_DEFAULT")

    def test_every_other_plugin_is_a_known_category(self) -> None:
        catalog = json.loads((ROOT / ".claude-plugin/marketplace.json").read_text())
        module = load_project_init()
        self.assertEqual(
            module.CATEGORIES,
            {
                plugin["name"]: f"{plugin['name']}@{catalog['name']}"
                for plugin in catalog["plugins"]
                if plugin["name"] != "core"
            },
        )
        skill = (CORE / "skills/project-init/SKILL.md").read_text()
        for name, plugin in module.CATEGORIES.items():
            self.assertIn(f"`{name}` (plugin `{plugin}`)", skill)

    def test_research_scaffold_accepts_and_requires_the_tag(self) -> None:
        init = " ".join(
            (ROOT / "plugins/research/skills/research-project-init/SKILL.md").read_text().split()
        )
        self.assertIn("The project tag written by `project-init` does not count as content", init)
        self.assertIn("Require `.project.toml` to list `research`", init)
        self.assertIn("hand off to `project-init`", init)


class ProjectInitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_project_init()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve() / "project"
        self.root.mkdir()
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        self.codex_home = Path(self.tmp.name) / "codex-home"
        self.codex_home.mkdir()
        env = mock.patch.dict(os.environ, {"CODEX_HOME": str(self.codex_home)})
        env.start()
        self.addCleanup(env.stop)
        # No real `claude` unless a test installs a fake one: check must not depend on this machine.
        which = mock.patch.object(self.module.shutil, "which", return_value=None)
        self.which = which.start()
        self.addCleanup(which.stop)
        self.addCleanup(self.tmp.cleanup)
        self.plugin = self.module.CATEGORIES["research"]

    def run_cli(self, *args: str, path: Path | None = None) -> tuple[int, str]:
        out, err = StringIO(), StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = self.module.main(["--path", str(path or self.root), *args])
        return code, out.getvalue() + err.getvalue()

    def claude(self) -> dict:
        return json.loads((self.root / ".claude/settings.json").read_text())

    def codex(self) -> dict:
        return tomllib.loads((self.root / ".codex/config.toml").read_text())

    def test_apply_tags_an_empty_repo_for_both_hosts(self) -> None:
        code, _ = self.run_cli("apply", "--categories", "research")
        self.assertEqual(code, 0)
        marker = tomllib.loads((self.root / ".project.toml").read_text())
        self.assertEqual(marker["categories"], ["research"])
        self.assertIs(self.claude()["enabledPlugins"][self.plugin], True)
        self.assertIs(self.codex()["plugins"][self.plugin]["enabled"], True)
        self.assertEqual(self.run_cli("check")[0], 0)

    def test_core_only_project_disables_every_category_explicitly(self) -> None:
        code, _ = self.run_cli("apply", "--categories")
        self.assertEqual(code, 0)
        self.assertEqual(tomllib.loads((self.root / ".project.toml").read_text())["categories"], [])
        self.assertIs(self.claude()["enabledPlugins"][self.plugin], False)
        self.assertIs(self.codex()["plugins"][self.plugin]["enabled"], False)

    def test_existing_settings_survive_and_reapply_is_idempotent(self) -> None:
        (self.root / ".claude").mkdir()
        (self.root / ".claude/settings.json").write_text(
            json.dumps({"permissions": {"allow": ["Bash(ls)"]}, "enabledPlugins": {"x@y": True}})
        )
        (self.root / ".codex").mkdir()
        (self.root / ".codex/config.toml").write_text('model = "m"\n\n[plugins."x@y"]\nenabled = true\n')
        self.run_cli("apply", "--categories", "research")
        before = {p: p.read_text() for p in self.root.rglob("*") if p.is_file() and ".git" not in p.parts}
        self.run_cli("apply", "--categories", "research")
        after = {p: p.read_text() for p in self.root.rglob("*") if p.is_file() and ".git" not in p.parts}
        self.assertEqual(before, after)
        self.assertEqual(self.claude()["permissions"]["allow"], ["Bash(ls)"])
        self.assertIs(self.claude()["enabledPlugins"]["x@y"], True)
        codex = self.codex()
        self.assertEqual(codex["model"], "m")
        self.assertIs(codex["plugins"]["x@y"]["enabled"], True)
        self.assertEqual((self.root / ".codex/config.toml").read_text().count(self.module.MANAGED_BEGIN), 1)

    def test_retag_flips_both_hosts(self) -> None:
        self.run_cli("apply", "--categories", "research")
        self.run_cli("apply", "--categories")
        self.assertIs(self.claude()["enabledPlugins"][self.plugin], False)
        self.assertIs(self.codex()["plugins"][self.plugin]["enabled"], False)

    def test_hand_written_managed_table_is_refused_without_writing(self) -> None:
        (self.root / ".codex").mkdir()
        (self.root / ".codex/config.toml").write_text(f'[plugins."{self.plugin}"]\nenabled = false\n')
        code, output = self.run_cli("apply", "--categories", "research")
        self.assertEqual(code, 3)
        self.assertIn("outside the managed block", output)
        self.assertFalse((self.root / ".project.toml").exists())
        self.assertFalse((self.root / ".claude/settings.json").exists())

    def test_unknown_category_is_refused(self) -> None:
        code, output = self.run_cli("apply", "--categories", "astrology")
        self.assertEqual(code, 3)
        self.assertIn("unknown categories: astrology", output)

    def test_check_reports_untagged_and_drift(self) -> None:
        self.assertEqual(self.run_cli("check")[0], 2)
        self.run_cli("apply", "--categories", "research")
        settings = self.claude()
        settings["enabledPlugins"][self.plugin] = False
        (self.root / ".claude/settings.json").write_text(json.dumps(settings))
        code, output = self.run_cli("check")
        self.assertEqual(code, 1)
        self.assertIn(f"drift claude: {self.plugin} is False, marker says True", output)

    def test_check_resolves_the_git_root_from_a_subdirectory(self) -> None:
        self.run_cli("apply", "--categories", "research")
        sub = self.root / "a/b"
        sub.mkdir(parents=True)
        self.assertEqual(self.run_cli("check", path=sub)[0], 0)

    def test_codex_trust_requires_an_exact_project_entry(self) -> None:
        config = self.codex_home / "config.toml"
        self.assertEqual(self.module.codex_trust(self.root), "unknown")
        config.write_text(f'[projects."{self.root.parent}"]\ntrust_level = "trusted"\n')
        self.assertEqual(self.module.codex_trust(self.root), "untrusted")
        config.write_text(f'[projects."{self.root}"]\ntrust_level = "trusted"\n')
        self.assertEqual(self.module.codex_trust(self.root), "trusted")
        self.assertIn("codex trust: trusted", self.run_cli("check")[1])

    def test_codex_reads_are_tolerant_of_a_broken_user_config(self) -> None:
        self.run_cli("apply", "--categories", "research")
        (self.codex_home / "config.toml").write_bytes(b"\xff not = [toml")
        code, output = self.run_cli("check")
        self.assertEqual(code, 0, output)
        self.assertIn("codex trust: unknown", output)
        self.assertIn("codex global: unknown", output)

    def test_check_reports_the_global_codex_state(self) -> None:
        config = self.codex_home / "config.toml"
        self.run_cli("apply", "--categories", "research")
        trust = f'[projects."{self.root}"]\ntrust_level = "trusted"\n'
        config.write_text(f'{trust}[plugins."{self.plugin}"]\nenabled = false\n')
        self.assertIn(f"codex global: {self.plugin} is off", self.run_cli("check")[1])
        config.write_text(f'{trust}[plugins."{self.plugin}"]\nenabled = true\n')
        code, output = self.run_cli("check")
        self.assertEqual(code, 0, "a trusted project's own config decides")
        self.assertIn(f"codex global: {self.plugin} is on", output)
        self.assertIn("in sync", output)
        self.assertIn(f"codex global: {self.plugin} is on", self.run_cli("apply", "--categories", "research")[1])

    def test_untrusted_project_falls_back_to_the_global_codex_state(self) -> None:
        config = self.codex_home / "config.toml"
        self.run_cli("apply", "--categories")
        config.write_text(f'[plugins."{self.plugin}"]\nenabled = true\n')
        code, output = self.run_cli("check")
        self.assertEqual(code, 1)
        self.assertIn(f"mismatch codex: {self.plugin} is on globally", output)
        self.assertNotIn("in sync", output)
        config.write_text(f'[plugins."{self.plugin}"]\nenabled = false\n')
        self.assertEqual(self.run_cli("check")[0], 0)

    def fake_claude(self, listing: str, code: int = 0) -> None:
        script = Path(self.tmp.name) / "claude"
        script.write_text(f"#!/bin/sh\ncat <<'EOF'\n{listing}\nEOF\nexit {code}\n")
        script.chmod(0o755)
        self.which.return_value = str(script)

    def test_check_reports_a_plugin_the_merged_settings_turn_off(self) -> None:
        self.run_cli("apply", "--categories", "research")
        self.fake_claude(json.dumps([{"id": self.plugin, "enabled": False, "projectEnabled": True}]))
        code, output = self.run_cli("check")
        self.assertEqual(code, 1)
        self.assertIn(f"mismatch claude: {self.plugin} is off in merged settings, marker says True", output)
        self.assertNotIn("in sync", output)
        self.assertNotIn("run `project_init.py apply`", output, "apply cannot fix a setting outside the project")

    def test_check_agrees_with_the_host_listing(self) -> None:
        self.run_cli("apply", "--categories", "research")
        self.fake_claude(json.dumps([{"id": self.plugin, "enabled": True}]))
        code, output = self.run_cli("check")
        self.assertEqual(code, 0, output)
        self.assertIn(f"claude effective: {self.plugin} is on", output)
        self.assertIn("in sync", output)

    def test_check_reports_a_tagged_plugin_that_is_not_installed(self) -> None:
        self.fake_claude("[]")
        self.run_cli("apply", "--categories", "research")
        code, output = self.run_cli("check")
        self.assertEqual(code, 1)
        self.assertIn(f"mismatch claude: {self.plugin} is not installed", output)
        self.run_cli("apply", "--categories")
        self.assertEqual(self.run_cli("check")[0], 0, "an off plugin may be absent")

    def test_check_reports_a_load_error(self) -> None:
        self.run_cli("apply", "--categories", "research")
        self.fake_claude(json.dumps([{"id": self.plugin, "enabled": True, "errors": ["bad hook"]}]))
        code, output = self.run_cli("check")
        self.assertEqual(code, 1)
        self.assertIn("failed to load: bad hook", output)

    def test_check_survives_a_broken_host_listing(self) -> None:
        self.run_cli("apply", "--categories", "research")
        for listing, code in (("not json", 0), ("[]", 3), ('{"a": 1}', 0)):
            self.fake_claude(listing, code)
            status, output = self.run_cli("check")
            self.assertEqual(status, 0, output)
            self.assertIn("claude effective: unknown", output)

    def test_research_tag_denies_reading_env_and_keeps_other_keys(self) -> None:
        (self.root / ".claude").mkdir()
        (self.root / ".claude/settings.json").write_text(
            json.dumps({"model": "m", "permissions": {"allow": ["Bash(ls)"], "deny": ["Read(./x)"]}})
        )
        self.run_cli("apply", "--categories", "research")
        settings = self.claude()
        self.assertEqual(settings["model"], "m")
        self.assertEqual(settings["permissions"]["allow"], ["Bash(ls)"])
        self.assertEqual(settings["permissions"]["deny"], ["Read(./x)", "Read(./.env)"])
        self.run_cli("apply", "--categories", "research")
        self.assertEqual(self.claude()["permissions"]["deny"].count("Read(./.env)"), 1)

    def test_core_only_project_gets_no_env_rule_and_retag_keeps_it(self) -> None:
        self.run_cli("apply", "--categories")
        self.assertNotIn("permissions", self.claude())
        self.run_cli("apply", "--categories", "research")
        self.run_cli("apply", "--categories")
        self.assertIn("Read(./.env)", self.claude()["permissions"]["deny"])

    def test_check_reports_a_missing_env_rule_as_drift(self) -> None:
        self.run_cli("apply", "--categories", "research")
        settings = self.claude()
        del settings["permissions"]
        (self.root / ".claude/settings.json").write_text(json.dumps(settings))
        code, output = self.run_cli("check")
        self.assertEqual(code, 1)
        self.assertIn("drift claude: permissions.deny lacks Read(./.env)", output)
        self.run_cli("apply", "--categories", "research")
        self.assertEqual(self.run_cli("check")[0], 0)

    def test_malformed_permissions_are_refused_without_writing(self) -> None:
        (self.root / ".claude").mkdir()
        (self.root / ".claude/settings.json").write_text(json.dumps({"permissions": {"deny": "x"}}))
        code, output = self.run_cli("apply", "--categories", "research")
        self.assertEqual(code, 3)
        self.assertIn("permissions.deny must be a list", output)
        self.assertFalse((self.root / ".project.toml").exists())


if __name__ == "__main__":
    unittest.main()
