"""Guard the invariants that let one skill tree serve both Codex and Claude Code."""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
SKILLS = ROOT / "plugins/research/skills"
SKILL_SIGIL_RE = re.compile(r"\$([a-z0-9]+(?:-[a-z0-9]+)+)\b")

# Claude truncates the skill listing well above this; the lower house limit keeps descriptions
# scannable in both hosts' pickers.
MAX_DESCRIPTION = 1024


def read_json(relative: str) -> dict:
    return json.loads((ROOT / relative).read_text())


class DualHostManifestTests(unittest.TestCase):
    def test_both_catalogs_list_the_same_plugins(self) -> None:
        codex = read_json(".agents/plugins/marketplace.json")
        claude = read_json(".claude-plugin/marketplace.json")

        self.assertEqual(codex["name"], claude["name"])
        self.assertTrue(claude["owner"]["name"])
        self.assertEqual(
            {entry["name"] for entry in codex["plugins"]},
            {entry["name"] for entry in claude["plugins"]},
        )
        for entry in claude["plugins"]:
            self.assertEqual(entry["source"], f"./plugins/{entry['name']}")

    def test_plugin_manifests_do_not_drift(self) -> None:
        codex = read_json("plugins/research/.codex-plugin/plugin.json")
        claude = read_json("plugins/research/.claude-plugin/plugin.json")
        catalog = read_json(".claude-plugin/marketplace.json")["plugins"][0]

        for field in ("name", "version", "description"):
            self.assertEqual(codex[field], claude[field], f"{field} drifted between hosts")
        self.assertEqual(catalog["version"], codex["version"])

    def test_claude_manifest_omits_codex_only_and_redundant_fields(self) -> None:
        claude = read_json("plugins/research/.claude-plugin/plugin.json")

        # `interface` is Codex-only and warns under `claude plugin validate`.
        self.assertNotIn("interface", claude)
        # Claude auto-discovers ./skills/; declaring it again is redundant.
        self.assertNotIn("skills", claude)


class SharedSkillTreeTests(unittest.TestCase):
    def skill_dirs(self) -> list[Path]:
        return sorted(path.parent for path in SKILLS.glob("*/SKILL.md"))

    def test_every_skill_is_discoverable_by_both_hosts(self) -> None:
        skill_dirs = self.skill_dirs()
        self.assertTrue(skill_dirs)
        for skill_dir in skill_dirs:
            # Codex needs its metadata file; Claude needs the SKILL.md at this exact depth.
            self.assertTrue((skill_dir / "agents/openai.yaml").is_file(), skill_dir)
            self.assertTrue((skill_dir / "SKILL.md").is_file(), skill_dir)

    def test_frontmatter_is_portable(self) -> None:
        for skill_dir in self.skill_dirs():
            text = (skill_dir / "SKILL.md").read_text()
            block = re.match(r"\A---\n(.*?)\n---(?:\n|\Z)", text, re.DOTALL)
            self.assertIsNotNone(block, skill_dir)
            keys = {
                line.split(":", 1)[0].strip()
                for line in block.group(1).splitlines()
                if line.strip()
            }
            self.assertEqual(keys, {"name", "description"}, skill_dir)

            description = re.search(r"^description:\s*(.+)$", block.group(1), re.MULTILINE)
            self.assertIsNotNone(description, skill_dir)
            self.assertLessEqual(
                len(description.group(1).strip().strip('"')), MAX_DESCRIPTION, skill_dir
            )

    def test_skill_bodies_carry_no_host_specific_invocation_sigil(self) -> None:
        offenders = [
            f"{markdown.relative_to(ROOT)}: {match.group(0)}"
            for markdown in SKILLS.rglob("*.md")
            for match in SKILL_SIGIL_RE.finditer(markdown.read_text())
        ]
        self.assertEqual(offenders, [], "distributed skills must not use the Codex '$' sigil")

    def test_codex_metadata_keeps_its_own_sigil(self) -> None:
        # The '$' form stays valid in Codex-only metadata, which Claude never reads.
        for skill_dir in self.skill_dirs():
            metadata = (skill_dir / "agents/openai.yaml").read_text()
            self.assertIn(f"${skill_dir.name}", metadata, skill_dir)


class MaintenanceSkillMirrorTests(unittest.TestCase):
    NAMES = ("marketplace-skill-creator", "marketplace-skill-reviewer")

    def test_claude_sees_the_same_maintenance_skills(self) -> None:
        self.assertEqual(
            {path.name for path in (ROOT / ".claude/skills").iterdir() if path.is_dir()},
            set(self.NAMES),
        )

    def test_mirrors_point_at_the_canonical_body_instead_of_copying_it(self) -> None:
        for name in self.NAMES:
            pointer = ROOT / ".claude/skills" / name / "SKILL.md"
            canonical = ROOT / ".agents/skills" / name / "SKILL.md"
            self.assertTrue(canonical.is_file(), name)
            self.assertIn(f".agents/skills/{name}/SKILL.md", pointer.read_text(), name)
            # A pointer, not a fork: it must stay far shorter than the body it defers to.
            self.assertLess(
                len(pointer.read_text()), len(canonical.read_text()), f"{name} looks forked"
            )


class ScaffoldTests(unittest.TestCase):
    def test_project_init_scaffolds_one_canonical_doc_for_both_hosts(self) -> None:
        init_root = SKILLS / "research-project-init"
        skill = (init_root / "SKILL.md").read_text()
        templates = (init_root / "references/templates.md").read_text()
        conventions = (init_root / "references/conventions.md").read_text()

        self.assertIn("`AGENTS.md`, `CLAUDE.md`", skill)
        self.assertIn("Never duplicate the notes across the two", skill)
        self.assertIn("## CLAUDE.md", templates)
        self.assertIn("Project notes live in `AGENTS.md`", templates)
        self.assertIn("├── CLAUDE.md", conventions)


if __name__ == "__main__":
    unittest.main()
