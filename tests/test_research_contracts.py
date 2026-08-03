from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]


class ResearchContractTests(unittest.TestCase):
    def test_plugin_minor_version_and_distributed_capabilities(self) -> None:
        manifest = json.loads(
            (ROOT / "plugins/research/.codex-plugin/plugin.json").read_text()
        )
        self.assertEqual(manifest["version"], "1.3.0")
        self.assertTrue((ROOT / "plugins/research/skills/rig-sync/SKILL.md").is_file())
        self.assertTrue(
            (ROOT / "plugins/research/skills/integrate-reference-code/SKILL.md").is_file()
        )

    def test_reference_code_integration_requires_informed_delta_approval(self) -> None:
        skill_root = ROOT / "plugins/research/skills/integrate-reference-code"
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

    def test_rig_sync_documents_canonical_3090_ti_hostname(self) -> None:
        configuration = (
            ROOT / "plugins/research/skills/rig-sync/references/configuration.md"
        ).read_text()
        self.assertIn('hostname = "rig-3090-ti"', configuration)
        self.assertNotIn('hostname = "rig-3090"', configuration)

    def test_dispatch_supports_local_hub_and_rig_sync(self) -> None:
        skill = (ROOT / "plugins/research/skills/sweep-dispatch/SKILL.md").read_text()
        templates = (
            ROOT / "plugins/research/skills/sweep-dispatch/references/templates.md"
        ).read_text()
        self.assertIn("$rig-sync", skill)
        self.assertIn("current local hub", skill)
        self.assertIn("deploy-revision", skill)
        self.assertIn("verify-revision", skill)
        self.assertNotIn("push-source` command to stage", templates)
        self.assertIn("When `<rig>` is the current local hub", templates)

    def test_behemoth_guard_precedes_python(self) -> None:
        templates = (
            ROOT / "plugins/research/skills/sweep-dispatch/references/templates.md"
        ).read_text()
        guard = templates.index("BEHEMOTH_AUTHORIZED_GPUS")
        python = templates.index("python code/<NNN_exp>/<script>.py")
        self.assertLess(guard, python)

    def test_git_revision_guard_precedes_python_and_records_provenance(self) -> None:
        templates = (
            ROOT / "plugins/research/skills/sweep-dispatch/references/templates.md"
        ).read_text()
        source_guard = templates.index("SOURCE_REVISION=$(git rev-parse")
        python = templates.index("python code/<NNN_exp>/<script>.py")
        self.assertLess(source_guard, python)
        self.assertIn('"source_revision": os.environ.get("SOURCE_REVISION")', templates)
        self.assertIn('"source_tag": os.environ.get("SOURCE_TAG")', templates)
        self.assertIn('"$rc" -eq 86', templates)

    def test_project_scaffold_and_design_require_git_and_smoke_contracts(self) -> None:
        init_skill = (
            ROOT / "plugins/research/skills/research-project-init/SKILL.md"
        ).read_text()
        design = (ROOT / "plugins/research/skills/experiment-design/SKILL.md").read_text()
        configuration = (
            ROOT / "plugins/research/skills/rig-sync/references/configuration.md"
        ).read_text()
        self.assertIn("GitHub remote", init_skill)
        self.assertIn("Pre-dispatch smoke test", design)
        self.assertIn("[git]", configuration)
        self.assertIn("source_revision", design)


if __name__ == "__main__":
    unittest.main()
