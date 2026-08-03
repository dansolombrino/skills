from __future__ import annotations

import json
import re
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).parents[1]


class ResearchContractTests(unittest.TestCase):
    def test_plugin_minor_version_and_distributed_capabilities(self) -> None:
        manifest = json.loads(
            (ROOT / "plugins/research/.codex-plugin/plugin.json").read_text()
        )
        self.assertEqual(manifest["version"], "1.5.0")
        self.assertTrue((ROOT / "plugins/research/skills/rig-sync/SKILL.md").is_file())
        self.assertTrue(
            (ROOT / "plugins/research/skills/integrate-reference-code/SKILL.md").is_file()
        )
        self.assertTrue(
            (ROOT / "plugins/research/skills/environment-sync/SKILL.md").is_file()
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

    def test_dispatch_requires_environment_parity_and_provenance(self) -> None:
        skill = (ROOT / "plugins/research/skills/sweep-dispatch/SKILL.md").read_text()
        templates = (
            ROOT / "plugins/research/skills/sweep-dispatch/references/templates.md"
        ).read_text()
        status_template = (
            ROOT / "plugins/research/skills/research-project-init/references/templates.md"
        ).read_text()
        self.assertIn("$environment-sync", skill)
        self.assertIn("EXPECTED_ENVIRONMENT_FINGERPRINT", templates)
        self.assertIn("ENVIRONMENT_FINGERPRINT", templates)
        self.assertIn(
            '"environment_fingerprint": os.environ.get("ENVIRONMENT_FINGERPRINT")',
            status_template,
        )
        environment_guard = templates.index("EXPECTED_ENVIRONMENT_FINGERPRINT")
        python = templates.index(".venv/bin/python code/<NNN_exp>/<script>.py")
        self.assertLess(environment_guard, python)
        self.assertIn('"$rc" -eq 87', templates)

    def test_behemoth_guard_precedes_python(self) -> None:
        templates = (
            ROOT / "plugins/research/skills/sweep-dispatch/references/templates.md"
        ).read_text()
        guard = templates.index("BEHEMOTH_AUTHORIZED_GPUS")
        python = templates.index(".venv/bin/python code/<NNN_exp>/<script>.py")
        self.assertLess(guard, python)

    def test_git_revision_guard_precedes_python_and_records_provenance(self) -> None:
        templates = (
            ROOT / "plugins/research/skills/sweep-dispatch/references/templates.md"
        ).read_text()
        status_template = (
            ROOT / "plugins/research/skills/research-project-init/references/templates.md"
        ).read_text()
        source_guard = templates.index("SOURCE_REVISION=$(git rev-parse")
        python = templates.index(".venv/bin/python code/<NNN_exp>/<script>.py")
        self.assertLess(source_guard, python)
        self.assertIn(
            '"source_revision": os.environ.get("SOURCE_REVISION")', status_template
        )
        self.assertIn('"source_tag": os.environ.get("SOURCE_TAG")', status_template)
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

    def test_dispatch_guarantees_fixed_timestamped_status_updates(self) -> None:
        skill_root = ROOT / "plugins/research/skills/sweep-dispatch"
        skill = (skill_root / "SKILL.md").read_text()
        metadata = (skill_root / "agents/openai.yaml").read_text()

        self.assertIn("exact 600-second increments", skill)
        self.assertIn("even if nothing changed", skill)
        self.assertIn("Status written <timestamp> —", skill)
        self.assertIn("does not reset `next_update`", skill)
        self.assertIn("must not send its final response", skill)
        self.assertIn("recurring wait/monitor primitive", skill)
        self.assertIn("ten-minute status and ETA updates", metadata)

    def test_tracking_defines_run_lane_and_wave_eta_contracts(self) -> None:
        tracking = (
            ROOT / "plugins/research/skills/experiments-tracking/SKILL.md"
        ).read_text()

        self.assertIn(
            "remaining_s = elapsed_s * (progress_total - progress_completed) / progress_completed",
            tracking,
        )
        self.assertIn("exact-run history", tracking)
        self.assertIn("experiment median", tracking)
        self.assertIn("For a lane, add its active run's remaining estimate", tracking)
        self.assertIn("latest available lane completion only", tracking)
        self.assertIn("heartbeat older than three minutes", tracking)

    def test_status_writer_template_is_canonical_and_machine_readable(self) -> None:
        project_templates = (
            ROOT / "plugins/research/skills/research-project-init/references/templates.md"
        ).read_text()
        dispatch_templates = (
            ROOT / "plugins/research/skills/sweep-dispatch/references/templates.md"
        ).read_text()

        self.assertEqual(dispatch_templates.count("class StatusWriter"), 0)
        self.assertIn("../../research-project-init/references/templates.md", dispatch_templates)
        self.assertIn('"schema_version": 2', project_templates)
        self.assertIn('"progress_completed": None', project_templates)
        self.assertIn("datetime.datetime.now().astimezone()", project_templates)
        self.assertIn("threading.Thread", project_templates)

    def test_status_writer_template_runs_structured_and_legacy_progress(self) -> None:
        templates = (
            ROOT / "plugins/research/skills/research-project-init/references/templates.md"
        ).read_text()
        match = re.search(
            r"## code/common/status\.py\n\n```python\n(.*?)\n```",
            templates,
            re.DOTALL,
        )
        self.assertIsNotNone(match)
        namespace: dict[str, object] = {}
        exec(compile(match.group(1), "status.py", "exec"), namespace)
        status_writer = namespace["StatusWriter"]

        with tempfile.TemporaryDirectory() as tmp:
            with status_writer(tmp, heartbeat_interval_s=0.02) as writer:
                started = json.loads(writer.path.read_text())
                self.assertEqual(started["schema_version"], 2)
                self.assertIsNotNone(datetime.fromisoformat(started["started"]).tzinfo)

                writer.heartbeat(completed=1, total=4, unit="epoch")
                structured = json.loads(writer.path.read_text())
                self.assertEqual(structured["progress"], "epoch 1/4")
                self.assertEqual(structured["progress_completed"], 1)
                self.assertEqual(structured["progress_total"], 4)
                with self.assertRaises(ValueError):
                    writer.heartbeat(completed=5, total=4, unit="epoch")

                writer.heartbeat(progress="legacy 2/4")
                legacy = json.loads(writer.path.read_text())
                self.assertEqual(legacy["progress"], "legacy 2/4")
                self.assertIsNone(legacy["progress_completed"])
                time.sleep(0.12)
                live = json.loads(writer.path.read_text())
                self.assertGreaterEqual(live["elapsed_s"], 0.1)

            ended = json.loads(writer.path.read_text())
            self.assertEqual(ended["state"], "done")
            self.assertIsNotNone(ended["ended"])
            self.assertFalse(writer.path.with_suffix(".json.tmp").exists())


if __name__ == "__main__":
    unittest.main()
