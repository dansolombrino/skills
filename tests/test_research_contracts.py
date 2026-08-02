from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]


class ResearchContractTests(unittest.TestCase):
    def test_plugin_minor_version_and_new_skill(self) -> None:
        manifest = json.loads(
            (ROOT / "plugins/research/.codex-plugin/plugin.json").read_text()
        )
        self.assertEqual(manifest["version"], "1.1.0")
        self.assertTrue((ROOT / "plugins/research/skills/rig-sync/SKILL.md").is_file())

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
        self.assertIn("When `<rig>` is the current local hub", templates)

    def test_behemoth_guard_precedes_python(self) -> None:
        templates = (
            ROOT / "plugins/research/skills/sweep-dispatch/references/templates.md"
        ).read_text()
        guard = templates.index("BEHEMOTH_AUTHORIZED_GPUS")
        python = templates.index("python code/<NNN_exp>/<script>.py")
        self.assertLess(guard, python)


if __name__ == "__main__":
    unittest.main()
