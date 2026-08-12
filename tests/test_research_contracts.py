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
    def test_plugin_major_version_and_distributed_capabilities(self) -> None:
        manifest = json.loads(
            (ROOT / "plugins/research/.codex-plugin/plugin.json").read_text()
        )
        self.assertEqual(manifest["version"], "3.3.0")
        self.assertTrue((ROOT / "plugins/research/skills/rig-sync/SKILL.md").is_file())
        self.assertTrue(
            (ROOT / "plugins/research/skills/integrate-reference-code/SKILL.md").is_file()
        )
        self.assertTrue(
            (ROOT / "plugins/research/skills/environment-sync/SKILL.md").is_file()
        )
        skill_names = {
            path.parent.name
            for path in (ROOT / "plugins/research/skills").glob("*/SKILL.md")
        }
        self.assertEqual(len(skill_names), 21)
        self.assertTrue(
            {
                "scientific-orchestrator",
                "science-literature-plan",
                "assumption-breaker-plan",
                "critical-scientific-audit",
                "research-housekeeping",
                "flywheel",
                "flywheel-auto",
                "flywheel-index",
                "flywheel-log",
                "flywheel-lookahead",
                "flywheel-reproduce",
                "flywheel-to-graph",
            }.issubset(skill_names)
        )

    def test_visualizations_use_direct_rig_4090_execution(self) -> None:
        skill = (
            ROOT / "plugins/research/skills/visualizations/SKILL.md"
        ).read_text()
        conventions = (
            ROOT
            / "plugins/research/skills/research-project-init/references/conventions.md"
        ).read_text()

        for contract in (skill, conventions):
            self.assertIn("direct, non-orchestrated fast path", contract)
            self.assertIn("foreground", contract)
            self.assertIn("`rig-4090`", contract)
            self.assertIn("Do not invoke `scientific-orchestrator`", contract)
            self.assertIn("`sweep-dispatch`", contract)
            self.assertIn("mint a wave id", contract)
            self.assertIn("start tmux", contract)
            self.assertIn("`EXPERIMENTS.md` rows", contract)

    def test_single_producer_plots_preserve_full_experiment_hierarchy(self) -> None:
        skills = ROOT / "plugins/research/skills"
        visualization = (skills / "visualizations/SKILL.md").read_text()
        metadata = (skills / "visualizations/agents/openai.yaml").read_text()
        design = (skills / "experiment-design/SKILL.md").read_text()
        housekeeping = (skills / "research-housekeeping/SKILL.md").read_text()
        conventions = (
            skills / "research-project-init/references/conventions.md"
        ).read_text()

        for contract in (visualization, conventions):
            self.assertIn(
                "visualizations/<experiment_path>/<script_stem>.py", contract
            )
            self.assertIn(
                "plots/<experiment_path>/<script_stem>/<partial_run_id path>/",
                contract,
            )
            self.assertIn("multiple producer experiment paths", contract)

        nested = (
            "plots/001_compression/002_weight_error/plot_layers/"
            "model=vit/seed=0/layer_error.pdf"
        )
        top_level = (
            "plots/000_grokking/plot_loss/"
            "model=mlp/lr=1e-3/seed=0/loss_curve.pdf"
        )
        self.assertIn(top_level, visualization)
        self.assertIn(top_level, conventions)
        self.assertIn(nested, visualization)
        self.assertIn(nested, conventions)
        self.assertNotIn("plots/NNN_exp/<script_stem>/", visualization)
        self.assertIn("never migrate it silently", visualization)
        self.assertIn("preserve it unchanged", design)
        self.assertIn("complete numbered `<experiment_path>`", housekeeping)
        self.assertIn("full numbered producer hierarchy", metadata)

    def test_scientific_orchestrator_has_independent_explicit_modes(self) -> None:
        skill_root = ROOT / "plugins/research/skills/scientific-orchestrator"
        skill = (skill_root / "SKILL.md").read_text()
        contract = (skill_root / "references/control-contract.md").read_text()

        self.assertIn("Require explicit `scientific_mode` and `engineering_mode`", skill)
        self.assertIn("Never infer either mode", skill)
        self.assertIn("| manual | manual |", contract)
        self.assertIn("| manual | auto |", contract)
        self.assertIn("| auto | manual |", contract)
        self.assertIn("| auto | auto |", contract)
        self.assertIn("System or sandbox approvals remain independent", contract)
        self.assertIn("every source-to-target deviation", contract)

    def test_scientific_and_engineering_handoffs_preserve_ownership(self) -> None:
        skill_root = ROOT / "plugins/research/skills/scientific-orchestrator"
        skill = (skill_root / "SKILL.md").read_text()
        contract = (skill_root / "references/control-contract.md").read_text()

        self.assertIn("experiment-design", skill)
        self.assertIn("environment-sync", skill)
        self.assertIn("rig-sync", skill)
        self.assertIn("sweep-dispatch", skill)
        self.assertIn("The scientific layer defines what evidence", contract)
        self.assertIn("The engineering layer chooses how", contract)
        self.assertIn("never rewrites its factual execution state", contract)

    def test_program_experiments_journal_and_flywheel_are_noncompeting(self) -> None:
        housekeeping = (
            ROOT / "plugins/research/skills/research-housekeeping/SKILL.md"
        ).read_text()
        orchestrator = (
            ROOT / "plugins/research/skills/scientific-orchestrator/SKILL.md"
        ).read_text()

        self.assertIn("program.md` as the one-screen current index", orchestrator)
        self.assertIn("`EXPERIMENTS.md` as the only run-state authority", orchestrator)
        self.assertIn("`JOURNAL.md` as the chronological narrative", orchestrator)
        self.assertIn("Flywheel as curated scientific lineage", orchestrator)
        self.assertIn("Never introduce a generic `outputs/` tree", housekeeping)
        self.assertIn("Never edit it from this skill", housekeeping)

    def test_project_init_is_fresh_only_and_scaffolds_research_2_records(self) -> None:
        init_root = ROOT / "plugins/research/skills/research-project-init"
        skill = (init_root / "SKILL.md").read_text()
        templates = (init_root / "references/templates.md").read_text()

        self.assertIn("do not use to upgrade, migrate, or retrofit", skill)
        self.assertIn("stop as unsupported", skill)
        self.assertRegex(skill, r"Never\s+infer a default")
        self.assertIn("## program/00-execution-agreement.md (initial)", templates)
        self.assertIn("scientific_mode: <manual|auto", templates)
        self.assertIn("engineering_mode: <manual|auto", templates)
        self.assertIn("orchestration/", templates)
        self.assertIn("FLYWHEEL_ROOT_NODE_ID=", templates)
        for key in (
            "HF_TOKEN",
            "HUGGING_FACE_HUB_TOKEN",
            "HF_HOME",
            "HF_DATASETS_CACHE",
            "HF_HUB_CACHE",
            "OPENCLIP_CACHE_DIR",
            "CACHE_DIR",
            "TORCH_NUM_WORKERS",
        ):
            self.assertGreaterEqual(templates.count(f"{key}="), 2, key)
        self.assertIn("HF_HOME=/mnt/KS_2TB/cache/huggingface", templates)
        self.assertIn(
            "HF_DATASETS_CACHE=/mnt/KS_2TB/cache/huggingface/datasets", templates
        )
        self.assertIn(
            "HF_HUB_CACHE=/mnt/KS_2TB/cache/huggingface/models", templates
        )
        self.assertIn(
            "OPENCLIP_CACHE_DIR=/mnt/KS_2TB/PARA/Projects/quantization/qat-transfer/"
            "storage/openclip",
            templates,
        )
        self.assertIn(
            "CACHE_DIR=/mnt/KS_2TB/PARA/Projects/quantization/qat-transfer/"
            "storage/cache",
            templates,
        )
        self.assertIn("TORCH_NUM_WORKERS=16", templates)
        self.assertIn("never copy or commit a token", templates)
        self.assertIn("Do not create this file with placeholders", templates)
        self.assertIn("`.gitkeep` in every directory", skill)
        self.assertIn("Ask which single directory name", skill)
        self.assertIn('name = "{{ENVIRONMENT_NAME}}"', templates)
        self.assertIn("{{ENVIRONMENT_NAME}}/", templates)

    def test_flywheel_family_preserves_logging_and_index_authority(self) -> None:
        log = (ROOT / "plugins/research/skills/flywheel-log/SKILL.md").read_text()
        index = (ROOT / "plugins/research/skills/flywheel-index/SKILL.md").read_text()
        auto = (ROOT / "plugins/research/skills/flywheel-auto/SKILL.md").read_text()
        gate = (
            ROOT
            / "plugins/research/skills/flywheel/references/research-root-gate.md"
        ).read_text()

        self.assertIn("sole source of truth", log)
        self.assertIn("./.flywheel.json", gate)
        self.assertIn("Never create a", log)
        self.assertIn("generic `outputs/`", log)
        self.assertIn("`index.md` is a local logging side effect", log)
        self.assertIn("index.md` is a *mirror*", index)
        self.assertIn("authoritative", index)
        self.assertIn("explicit budget", auto)

    def test_flywheel_logging_contract_is_deterministic_and_fail_closed(self) -> None:
        log = (ROOT / "plugins/research/skills/flywheel-log/SKILL.md").read_text()
        index = (ROOT / "plugins/research/skills/flywheel-index/SKILL.md").read_text()
        gate = (
            ROOT
            / "plugins/research/skills/flywheel/references/research-root-gate.md"
        ).read_text()

        self.assertLess(gate.index("./.flywheel.json"), gate.index("FLYWHEEL_ROOT_NODE_ID"))
        self.assertLess(gate.index("FLYWHEEL_ROOT_NODE_ID"), gate.index("VS Code setting"))
        for artifact in (
            "at least one real plot/image",
            "`summary.md`",
            "machine-readable metrics",
            "`reproducibility.md`",
            "`commit.txt`",
        ):
            self.assertIn(artifact, log)
        self.assertIn("stop and route its", log)
        self.assertIn("generation to the owning research skill", log)
        self.assertIn("explicit approval before deletion", log)
        self.assertIn("approval before deletion", log)
        self.assertIn("incremental", index)
        self.assertIn("--rebuild", index)
        self.assertIn("Never invent content", index)

    def test_flywheel_writes_use_current_node_and_stage_contracts(self) -> None:
        log = (ROOT / "plugins/research/skills/flywheel-log/SKILL.md").read_text()

        self.assertNotIn("flywheel_stage_node_create", log)
        self.assertIn("flywheel_commit_new_node", log)
        self.assertIn("flywheel_branch_node", log)
        self.assertIn("flywheel_acquire_stage_lease", log)
        self.assertIn("stage_session_id", log)
        self.assertIn("base_committed_revision", log)
        self.assertIn("full `staged_payload`", log)
        self.assertIn("Never send removed fields", log)
        self.assertIn("For a failed or canceled run", log)
        self.assertIn("For an insight node, require only self-contained `content`", log)
        self.assertIn("A coherent\ngraph synthesis may be published with no artifacts", log)

    def test_flywheel_mutators_require_canonical_root_ancestry(self) -> None:
        gate = (
            ROOT
            / "plugins/research/skills/flywheel/references/research-root-gate.md"
        ).read_text()
        self.assertIn("flywheel_get_node_ancestry", gate)
        self.assertIn("never overrides the workspace root", gate)
        self.assertIn("Every new Research 2.0 node must be created with", gate)

        for name in (
            "flywheel-auto",
            "flywheel-lookahead",
            "flywheel-reproduce",
            "flywheel-to-graph",
        ):
            skill = (ROOT / f"plugins/research/skills/{name}/SKILL.md").read_text()
            self.assertIn("research-root-gate.md", skill, name)
            self.assertRegex(skill, r"(?i)ancestry", name)
            self.assertIn("explicitly approved new root", skill, name)

    def test_flywheel_shared_contract_references_are_centralized(self) -> None:
        common = (
            "ARTIFACTS.md",
            "INTERFACES.md",
            "flywheel-cli-tool-map.md",
            "flywheel-mcp-tool-map.md",
        )
        for skill_name in (
            "flywheel-auto",
            "flywheel-lookahead",
            "flywheel-reproduce",
            "flywheel-to-graph",
        ):
            skill = (ROOT / f"plugins/research/skills/{skill_name}/SKILL.md").read_text()
            references = ROOT / f"plugins/research/skills/{skill_name}/references"
            for filename in common:
                self.assertFalse((references / filename).exists())
                self.assertIn(f"../flywheel/references/{filename}", skill)

        for skill_name in ("flywheel-auto", "flywheel-reproduce"):
            duplicate = (
                ROOT
                / f"plugins/research/skills/{skill_name}/references/experiment-design-protocol.md"
            )
            self.assertFalse(duplicate.exists())

    def test_operational_retry_preserves_run_identity(self) -> None:
        fast_path = (
            ROOT
            / "plugins/research/skills/scientific-orchestrator/references/operational-fast-path.md"
        ).read_text()
        audit = (
            ROOT / "plugins/research/skills/critical-scientific-audit/SKILL.md"
        ).read_text()

        self.assertIn("same elected run ID", fast_path)
        self.assertIn("new wave or attempt identifier", fast_path)
        self.assertIn("evolve the run ID", fast_path)
        self.assertIn("../scientific-orchestrator/references/operational-fast-path.md", audit)
        self.assertFalse(
            (
                ROOT
                / "plugins/research/skills/critical-scientific-audit/references/operational-fast-path.md"
            ).exists()
        )

    def test_all_execution_skills_reject_legacy_project_migration(self) -> None:
        tracking = (
            ROOT / "plugins/research/skills/experiments-tracking/SKILL.md"
        ).read_text()
        dispatch = (ROOT / "plugins/research/skills/sweep-dispatch/SKILL.md").read_text()

        self.assertIn("do not offer migration", tracking)
        self.assertIn("do not offer migration", dispatch)
        self.assertIn("Unsupported layouts", dispatch)
        self.assertNotIn("offer to migrate", tracking)

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
        self.assertIn("rig-sync", skill)
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
        self.assertIn("environment-sync", skill)
        self.assertIn("EXPECTED_ENVIRONMENT_FINGERPRINT", templates)
        self.assertIn("ENVIRONMENT_FINGERPRINT", templates)
        self.assertIn(
            '"environment_fingerprint": os.environ.get("ENVIRONMENT_FINGERPRINT")',
            status_template,
        )
        environment_guard = templates.index("EXPECTED_ENVIRONMENT_FINGERPRINT")
        python = templates.index('"$ENVIRONMENT_DIR/bin/python" code/<NNN_exp>/<script>.py')
        self.assertLess(environment_guard, python)
        self.assertIn('"$rc" -eq 87', templates)

    def test_uv_environment_name_is_user_chosen_and_consumed_everywhere(self) -> None:
        environment_sync = (
            ROOT / "plugins/research/skills/environment-sync/SKILL.md"
        ).read_text()
        project_init = (
            ROOT / "plugins/research/skills/research-project-init/SKILL.md"
        ).read_text()
        dispatch = (
            ROOT / "plugins/research/skills/sweep-dispatch/references/templates.md"
        ).read_text()

        self.assertIn("ask the user", environment_sync.lower())
        self.assertIn("virtual environment should have", environment_sync.lower())
        self.assertIn("Ask which single directory name", project_init)
        for skill in (environment_sync, project_init):
            self.assertRegex(skill, r"(?i)never infer|never .*default")
        self.assertIn('ENVIRONMENT_DIR="<environment.name from sync.toml>"', dispatch)
        self.assertNotIn(".venv/bin/python", dispatch)

    def test_behemoth_guard_precedes_python(self) -> None:
        templates = (
            ROOT / "plugins/research/skills/sweep-dispatch/references/templates.md"
        ).read_text()
        guard = templates.index("BEHEMOTH_AUTHORIZED_GPUS")
        python = templates.index('"$ENVIRONMENT_DIR/bin/python" code/<NNN_exp>/<script>.py')
        self.assertLess(guard, python)

    def test_git_revision_guard_precedes_python_and_records_provenance(self) -> None:
        templates = (
            ROOT / "plugins/research/skills/sweep-dispatch/references/templates.md"
        ).read_text()
        status_template = (
            ROOT / "plugins/research/skills/research-project-init/references/templates.md"
        ).read_text()
        source_guard = templates.index("SOURCE_REVISION=$(git rev-parse")
        python = templates.index('"$ENVIRONMENT_DIR/bin/python" code/<NNN_exp>/<script>.py')
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

    def test_directives_are_closed_against_other_projects_on_disk(self) -> None:
        def flat(path: Path) -> str:
            # Rules are hard-wrapped in the sources; compare on normalized whitespace.
            return " ".join(path.read_text().split())

        init_root = ROOT / "plugins/research/skills/research-project-init"
        conventions = flat(init_root / "references/conventions.md")
        init_skill = flat(init_root / "SKILL.md")
        templates = flat(init_root / "references/templates.md")
        reference_code = flat(
            ROOT / "plugins/research/skills/integrate-reference-code/SKILL.md"
        )

        # The canon states the rule, and names the permitted reads so it cannot be over-read.
        self.assertIn("## Directives are closed", conventions)
        self.assertIn(
            "Never list, search, read, or copy from another project, repository, or directory"
            " on disk",
            conventions,
        )
        self.assertIn("stop and ask the user", conventions)
        self.assertIn("Reading outside the target project is legitimate", conventions)
        self.assertIn("the installed directory of a `research` skill", conventions)
        self.assertIn("References are always user-supplied", reference_code)
        self.assertIn("never authorizes surveying the filesystem", reference_code)

        # The scaffolder restates it at both ends, and the scaffolded project inherits it.
        self.assertIn("These directives are the complete specification.", init_skill)
        self.assertIn(
            "Never take structure, file contents, or defaults from another project on disk.",
            init_skill,
        )
        self.assertIn("never treat a similar-looking project as a template", templates)

        # Every skill that creates or edits project files carries the rule or the canon link.
        for skill_name in (
            "experiment-design",
            "visualizations",
            "environment-sync",
            "rig-sync",
            "research-journal",
            "research-housekeeping",
        ):
            skill = flat(ROOT / "plugins/research/skills" / skill_name / "SKILL.md")
            self.assertIn("Directives are closed", skill, skill_name)
            self.assertIn("another project on disk", skill, skill_name)

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

    def test_status_writer_template_runs_structured_progress_only(self) -> None:
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

                with self.assertRaises(TypeError):
                    writer.heartbeat(progress="legacy 2/4")
                time.sleep(0.12)
                live = json.loads(writer.path.read_text())
                self.assertGreaterEqual(live["elapsed_s"], 0.1)

            ended = json.loads(writer.path.read_text())
            self.assertEqual(ended["state"], "done")
            self.assertIsNotNone(ended["ended"])
            self.assertFalse(writer.path.with_suffix(".json.tmp").exists())


if __name__ == "__main__":
    unittest.main()
