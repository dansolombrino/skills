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
    def test_plugin_version_and_distributed_skills(self) -> None:
        manifest = json.loads((CORE / ".codex-plugin/plugin.json").read_text())
        self.assertEqual(manifest["version"], "1.3.1")
        claude_manifest = json.loads((CORE / ".claude-plugin/plugin.json").read_text())
        catalog = json.loads((ROOT / ".claude-plugin/marketplace.json").read_text())
        entry = next(plugin for plugin in catalog["plugins"] if plugin["name"] == "core")
        self.assertEqual(claude_manifest["version"], manifest["version"])
        self.assertEqual(entry["version"], manifest["version"])
        self.assertEqual(
            {path.parent.name for path in (CORE / "skills").glob("*/SKILL.md")},
            {
                "brainstorm-hold",
                "integrate-reference-code",
                "intent-gate",
                "intent-mirror",
                "project-init",
                "resirch-killalot",
            },
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

    def test_reference_code_integration_requires_informed_delta_approval(self) -> None:
        skill_root = CORE / "skills/integrate-reference-code"
        skill = (skill_root / "SKILL.md").read_text()
        metadata = (skill_root / "agents/openai.yaml").read_text()

        self.assertIn("Keep this phase read-only", skill)
        self.assertIn("Enumerate every concrete", skill)
        self.assertIn("Use all seven columns", skill)
        self.assertIn("mark each initial approval as", skill)
        self.assertIn("Stop and wait for the user to approve", skill)
        self.assertIn("general permission", skill)
        self.assertIn("If a new deviation becomes necessary, stop before applying it", skill)
        self.assertIn("any unapproved deviation remains", skill)
        self.assertIn("$integrate-reference-code", metadata)

    def test_resirch_killalot_keeps_the_owner_gates(self) -> None:
        skill_root = CORE / "skills/resirch-killalot"
        skill = " ".join((skill_root / "SKILL.md").read_text().split())
        metadata = (skill_root / "agents/openai.yaml").read_text()
        self.assertIn("$resirch-killalot", metadata)
        for phrase in (
            "Agents only propose.",
            "Only the owner accepts",
            "Start an item only on the owner's yes",
            "never that the **how** is approved",
            "**Never approve**",
            "approvals are given only from a paired device",
            "check-approval",
            "Evidence is required",
            "never propose it again",
            "Never edit the store by hand",
            "Never accept anything.",
        ):
            self.assertIn(phrase, skill, phrase)
        hooks = json.loads((CORE / "hooks/hooks.json").read_text())
        command = hooks["hooks"]["SessionStart"][0]["hooks"][0]["command"]
        self.assertIn("${CLAUDE_PLUGIN_ROOT}/skills/resirch-killalot/scripts/killalot.py", command)
        self.assertTrue(command.endswith("hook session-start"))

    def test_intent_gate_family_is_congruent(self) -> None:
        skills_root = CORE / "skills"
        contract_path = skills_root / "intent-gate/references/contract.md"
        self.assertTrue(contract_path.is_file())
        contract = " ".join(contract_path.read_text().split())
        bodies = {
            name: " ".join((skills_root / name / "SKILL.md").read_text().split())
            for name in ("intent-mirror", "brainstorm-hold", "intent-gate")
        }

        # The two single-mode skills defer to the combined skill's contract as canon.
        for name in ("intent-mirror", "brainstorm-hold"):
            self.assertIn("../intent-gate/references/contract.md", bodies[name], name)
        self.assertIn("references/contract.md", bodies["intent-gate"])

        # Shared vocabulary appears in the contract and in every body.
        for text_name, text in {"contract": contract, **bodies}.items():
            self.assertIn("Did I sniff that right", text, text_name)
            self.assertIn("greenlight", text, text_name)
            self.assertIn("not doing yet", text, text_name)
            self.assertIn("Engineering-auto may not bypass this gate", text, text_name)

        # The hold forbids the host's planning mode, host-neutrally.
        for text_name in ("contract", "brainstorm-hold", "intent-gate"):
            text = contract if text_name == "contract" else bodies[text_name]
            self.assertIn("planning mode", text, text_name)

        # The combined skill confirms each detected mode separately.
        self.assertIn("once per mode", bodies["intent-gate"].lower())
        self.assertIn("Never bundle", bodies["intent-gate"])

        # Exploration during a hold is negotiated, never assumed.
        for text_name in ("contract", "brainstorm-hold", "intent-gate"):
            text = contract if text_name == "contract" else bodies[text_name]
            self.assertIn("Do you want exploration at this stage?", text, text_name)

        # The mirror re-fires on every later directive once active.
        for text_name in ("contract", "intent-mirror", "intent-gate"):
            text = contract if text_name == "contract" else bodies[text_name]
            self.assertIn("every later directive", text, text_name)


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
        self.assertEqual(self.claude()["permissions"], {"allow": ["Bash(ls)"]})
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


if __name__ == "__main__":
    unittest.main()
