from __future__ import annotations

import importlib.util
import json
import re
import sys
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).parents[1]
RIGSYNC = ROOT / "plugins/research/skills/rig-sync/scripts/rigsync.py"
PROJECT_TEMPLATES = (
    ROOT / "plugins/research/skills/research-project-init/references/templates.md"
)


def template_block(heading: str) -> str:
    """The python body templates.md publishes under one `## <heading>` section."""
    match = re.search(
        rf"## {re.escape(heading)}\n\n```python\n(.*?)\n```",
        PROJECT_TEMPLATES.read_text(),
        re.DOTALL,
    )
    assert match is not None, heading
    return match.group(1)


def run_id_template() -> str:
    return template_block("code/common/run_id.py")


def markdown_template_block(heading: str) -> str:
    """The Markdown body templates.md publishes under one named section."""
    match = re.search(
        rf"## {re.escape(heading)}\n\n```markdown\n(.*?)\n```",
        PROJECT_TEMPLATES.read_text(),
        re.DOTALL,
    )
    assert match is not None, heading
    return match.group(1)


def status_provenance_fields() -> frozenset[str]:
    """Wave provenance as the canonical StatusWriter records it -- the one authority."""
    block = template_block("code/common/status.py")
    section = block.split('"progress_unit": None,')[1].split("}")[0]
    return frozenset(re.findall(r'"([a-z_]+)": os\.environ\.get\(', section))


def machine_env_vars() -> frozenset[str]:
    """MACHINE_ENV_VARS as rigsync.py defines it -- the one authority for that list."""
    spec = importlib.util.spec_from_file_location("_rigsync_for_contracts", RIGSYNC)
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolves field types through sys.modules, so the module has to
    # be registered before it executes.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.MACHINE_ENV_VARS


class ResearchContractTests(unittest.TestCase):
    def test_plugin_major_version_and_distributed_capabilities(self) -> None:
        manifest = json.loads(
            (ROOT / "plugins/research/.codex-plugin/plugin.json").read_text()
        )
        self.assertEqual(manifest["version"], "3.11.1")
        claude_manifest = json.loads(
            (ROOT / "plugins/research/.claude-plugin/plugin.json").read_text()
        )
        claude_marketplace = json.loads(
            (ROOT / ".claude-plugin/marketplace.json").read_text()
        )
        research_entry = next(
            plugin
            for plugin in claude_marketplace["plugins"]
            if plugin["name"] == "research"
        )
        self.assertEqual(claude_manifest["version"], manifest["version"])
        self.assertEqual(research_entry["version"], manifest["version"])
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

    def test_plot_export_paths_are_always_user_owned_and_fully_resolved(self) -> None:
        skills = ROOT / "plugins/research/skills"
        contracts = {
            "visualizations": skills / "visualizations/SKILL.md",
            "conventions": skills / "research-project-init/references/conventions.md",
            "execution agreement": (
                skills / "research-project-init/references/templates.md"
            ),
            "orchestrator": (
                skills / "scientific-orchestrator/references/control-contract.md"
            ),
            "flywheel auto": skills / "flywheel-auto/SKILL.md",
            "autonomous protocol": (
                skills
                / "flywheel-auto/references/experiment-design-protocol-autonomous.md"
            ),
            "experiment protocol": (
                skills / "flywheel/references/experiment-design-protocol.md"
            ),
            "scientific audit": skills / "critical-scientific-audit/SKILL.md",
            "flywheel logging": skills / "flywheel-log/SKILL.md",
            "flywheel reproduction": skills / "flywheel-reproduce/SKILL.md",
        }

        for name, path in contracts.items():
            contract = " ".join(path.read_text().lower().split())
            with self.subTest(contract=name):
                self.assertIn("project-relative", contract)
                self.assertIn("`plots/`", contract)
                self.assertIn("leaf filename", contract)
                self.assertIn("placeholder", contract)
                self.assertIn("resolved", contract)
                self.assertIn("before rendering", contract)
                self.assertRegex(
                    contract,
                    r"fully resolved concrete export path.{0,100}"
                    r"(?:wait for|received) explicit user approval",
                )
                self.assertIn(
                    "approved template does not approve any concrete destination",
                    contract,
                )
                self.assertIn(
                    "new or changed resolved path always reopens approval",
                    contract,
                )
                self.assertIn(
                    "even when it conforms to the approved template",
                    contract,
                )
                self.assertRegex(
                    contract,
                    r"do not (?:accept a )?render before that approval",
                )
                self.assertIn("byte-for-byte identical", contract)
                self.assertIn("previously explicitly approved concrete path", contract)
                self.assertNotRegex(
                    contract,
                    r"reopen approval (?:only )?if .{0,60}(?:does not|doesn't) conform",
                )

        pre_edit_evidence = {
            "visualizations": (
                "propose the complete path template",
                "explicit acceptance before editing plotting code",
            ),
            "conventions": (
                "propose the complete path template",
                "explicit user approval before the code edit",
            ),
            "execution agreement": (
                "user must explicitly approve before the plotting-code edit",
                "leaf-filename template and every identified placeholder",
            ),
            "orchestrator": (
                "require user approval of the complete plot communication specification before every plotting-code creation or modification",
                "complete path template",
            ),
            "flywheel auto": (
                "explicit user acceptance before creating or modifying plot-producing code",
                "approved export path or path template with all placeholders identified",
            ),
            "autonomous protocol": (
                "before creating or modifying plot-producing code, propose",
                "path including its leaf filename, then wait for explicit user acceptance",
                "complete path template",
            ),
            "experiment protocol": (
                "specification before creating or modifying plot-producing code",
                "complete path template",
                "explicit acceptance before editing the plot-producing code",
            ),
            "scientific audit": (
                "require evidence that before the plotting-code edit the user explicitly approved either",
                "leaf-filename template and every identified placeholder",
            ),
            "flywheel logging": (
                "require evidence that before the plotting-code edit the user explicitly approved either",
                "leaf-filename template and every identified placeholder",
            ),
            "flywheel reproduction": (
                "gate before creating or modifying plot-producing code",
                "wait for explicit user acceptance",
                "replace `none` only after explicit user acceptance",
                "when runtime values require a path template, identify every placeholder before the code edit",
            ),
        }
        ownership_evidence = {
            "visualizations": (
                "neither scientific-auto nor engineering-auto may bypass this gate",
            ),
            "conventions": (
                "only the user may accept them",
                "scientific-auto and engineering-auto cannot approve it",
            ),
            "execution agreement": (
                "only the user may approve these protected path choices",
                "scientific-auto and engineering-auto cannot bypass them",
            ),
            "orchestrator": ("auto modes cannot approve it",),
            "flywheel auto": ("this gate overrides autonomy",),
            "autonomous protocol": (
                "the agent cannot approve its own proposal or derive acceptance from autonomous mode",
            ),
            "experiment protocol": (
                "the agent proposes only",
                "even in an automatic workflow",
            ),
            "scientific audit": (
                "only explicit user approval satisfies this gate",
                "scientific-auto and engineering-auto cannot bypass it",
            ),
            "flywheel logging": (
                "only explicit user approval satisfies this gate",
                "scientific-auto and engineering-auto cannot bypass it",
            ),
            "flywheel reproduction": (
                "neither scientific-auto nor engineering-auto may approve it",
            ),
        }

        for name, path in contracts.items():
            contract = " ".join(path.read_text().lower().split())
            with self.subTest(contract=name, rule="pre-edit approval"):
                for evidence in pre_edit_evidence[name]:
                    self.assertIn(evidence, contract)
            with self.subTest(contract=name, rule="user ownership"):
                for evidence in ownership_evidence[name]:
                    self.assertIn(evidence, contract)

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
        self.assertIn("stop; do not move or rewrite artifacts", visualization)
        self.assertIn("new numbered sub-experiment", visualization)
        self.assertIn("preserve it unchanged", design)
        self.assertIn("complete numbered `<experiment_path>`", housekeeping)
        self.assertIn("full numbered producer hierarchy", metadata)

    def test_run_id_path_layout_renderings_are_compatible_and_lossless(self) -> None:
        namespace: dict[str, object] = {}
        exec(compile(run_id_template(), "run_id.py", "exec"), namespace)
        run_id_path = namespace["run_id_path"]
        run_id_flat = namespace["run_id_flat"]

        cfg = {
            "seed value": 7,
            "model/type": "wide, residual/net",
            "structured": {"z": "x/y", "a": [1, 2]},
        }
        params = ["seed value", "model/type", "structured"]

        nested = run_id_path(cfg, params)
        explicit_nested = run_id_path(cfg, params, layout="nested")
        flat = run_id_flat(cfg, params)
        collapsed = run_id_path(cfg, params, layout="collapsed-v1")

        # Omitting layout retains the long-standing nested behavior exactly.
        self.assertEqual(nested, explicit_nested)
        self.assertEqual(nested.parts, tuple(flat.split(",")))

        # collapsed-v1 is one component containing exactly run_id_flat: same
        # elected order, same delimiters, and the same canonical encoding.
        self.assertEqual(collapsed.parts, (flat,))
        self.assertEqual(str(collapsed), flat)
        self.assertTrue(flat.startswith("seed%20value=7,model%2Ftype="))
        self.assertIn("wide%2C%20residual%2Fnet", flat)
        self.assertIn(
            "structured=%7B%22a%22%3A%5B1%2C2%5D%2C%22z%22%3A%22x%2Fy%22%7D",
            flat,
        )
        self.assertNotIn("wide, residual/net", flat)

        with self.assertRaisesRegex(ValueError, "invalid run_id path layout"):
            run_id_path(cfg, params, layout="collapsed")

    def test_run_path_layout_is_one_choice_across_artifact_surfaces(self) -> None:
        skills = ROOT / "plugins/research/skills"
        design = (skills / "experiment-design/SKILL.md").read_text()
        conventions = (
            skills / "research-project-init/references/conventions.md"
        ).read_text()
        dispatch = (skills / "sweep-dispatch/SKILL.md").read_text()
        dispatch_templates = (
            skills / "sweep-dispatch/references/templates.md"
        ).read_text()

        for name, contract in {
            "experiment design": design,
            "project conventions": conventions,
            "dispatch": dispatch,
        }.items():
            with self.subTest(contract=name):
                self.assertIn("RUN_ID_PATH_LAYOUT", contract)
                self.assertIn("checkpoints", contract)
                self.assertIn("evaluations", contract)
                self.assertIn("plots", contract)
                self.assertIn("run_id_path", contract)
                self.assertIn("run_id_flat", contract)

        compact_design = " ".join(design.split())
        self.assertIn("one authoritative experiment-wide", compact_design)
        self.assertIn("Never mix layouts", compact_design)
        self.assertIn(
            "Do not construct or show an alternative when fewer than two params are eligible",
            compact_design,
        )
        compact_conventions = " ".join(conventions.split())
        self.assertIn("one experiment-wide layout", compact_conventions)
        self.assertIn("must all call `run_id_path", compact_conventions)
        self.assertIn("show exactly one separately labeled", compact_conventions)
        self.assertIn("show none when fewer than two are eligible", compact_conventions)
        self.assertIn("must use the same selected layout", dispatch)
        for field in (
            "CHECKPOINT_DIR",
            "EVAL_DIR",
            "STATUS_PATH",
            "ARTIFACT",
        ):
            self.assertIn(field, dispatch_templates)
        self.assertIn("materialize the resulting exact", dispatch_templates)
        self.assertIn("from the same selected `RUN_ID_PATH_LAYOUT`", dispatch_templates)

    def test_path_layout_never_changes_flat_run_identity(self) -> None:
        skills = ROOT / "plugins/research/skills"
        design = " ".join((skills / "experiment-design/SKILL.md").read_text().split())
        conventions = " ".join(
            (skills / "research-project-init/references/conventions.md")
            .read_text()
            .split()
        )
        dispatch = " ".join((skills / "sweep-dispatch/SKILL.md").read_text().split())
        tracking = " ".join(
            (skills / "experiments-tracking/SKILL.md").read_text().split()
        )

        self.assertIn(
            "scripts/logs/WandB and EXPERIMENTS.md row identity keep using `run_id_flat` unchanged",
            design,
        )
        self.assertIn(
            "`scripts/` and `logs/` folders, WandB run names, log-line identity, and EXPERIMENTS.md row identity continue to use `run_id_flat`",
            conventions,
        )
        self.assertIn(
            "never changes scripts, logs, or wandb naming: those remain flat",
            dispatch.lower(),
        )
        self.assertIn("run identity is semantic", tracking.lower())
        self.assertIn("independent of whether", tracking.lower())

    def test_identifying_param_schema_evolution_before_outputs_respects_the_pin(self) -> None:
        namespace: dict[str, object] = {}
        exec(compile(run_id_template(), "run_id.py", "exec"), namespace)
        run_id_path = namespace["run_id_path"]
        run_id_flat = namespace["run_id_flat"]

        old_cfg = {"model": "vit/base", "seed": 3}
        backfilled_cfg = {"model": "vit/base", "precision": "bf16", "seed": 3}
        old_params = ["model", "seed"]
        new_params = ["model", "precision", "seed"]
        old_plot_params = ["model"]
        new_plot_params = ["model", "precision"]

        expected = {
            "nested": {
                "old": Path("model=vit%2Fbase/seed=3"),
                "new": Path("model=vit%2Fbase/precision=bf16/seed=3"),
                "old_plot": Path("model=vit%2Fbase"),
                "new_plot": Path("model=vit%2Fbase/precision=bf16"),
            },
            "collapsed-v1": {
                "old": Path("model=vit%2Fbase,seed=3"),
                "new": Path("model=vit%2Fbase,precision=bf16,seed=3"),
                "old_plot": Path("model=vit%2Fbase"),
                "new_plot": Path("model=vit%2Fbase,precision=bf16"),
            },
        }

        prefixes = {
            "checkpoint": Path("checkpoints/009_schema"),
            "evaluation": Path("evaluations/009_schema"),
        }
        for layout in ("nested", "collapsed-v1"):
            with self.subTest(layout=layout):
                old_suffix = run_id_path(old_cfg, old_params, layout=layout)
                new_suffix = run_id_path(backfilled_cfg, new_params, layout=layout)
                old_plot_suffix = run_id_path(
                    old_cfg, old_plot_params, layout=layout
                )
                new_plot_suffix = run_id_path(
                    backfilled_cfg, new_plot_params, layout=layout
                )

                self.assertEqual(old_suffix, expected[layout]["old"])
                self.assertEqual(new_suffix, expected[layout]["new"])
                self.assertEqual(old_plot_suffix, expected[layout]["old_plot"])
                self.assertEqual(new_plot_suffix, expected[layout]["new_plot"])
                for prefix in prefixes.values():
                    self.assertEqual(prefix / old_suffix, prefix / expected[layout]["old"])
                    self.assertEqual(prefix / new_suffix, prefix / expected[layout]["new"])
                plot_root = Path("plots/009_schema/plot_precision")
                self.assertEqual(
                    plot_root / new_plot_suffix,
                    plot_root / expected[layout]["new_plot"],
                )

                if layout == "nested":
                    self.assertEqual(
                        new_suffix.parts,
                        ("model=vit%2Fbase", "precision=bf16", "seed=3"),
                    )
                    self.assertEqual(len(new_suffix.parts), len(old_suffix.parts) + 1)
                else:
                    self.assertEqual(len(old_suffix.parts), 1)
                    self.assertEqual(len(new_suffix.parts), 1)
                    self.assertEqual(
                        new_suffix.parts,
                        ("model=vit%2Fbase,precision=bf16,seed=3",),
                    )

        old_flat = run_id_flat(old_cfg, old_params)
        new_flat = run_id_flat(backfilled_cfg, new_params)
        self.assertEqual(old_flat, "model=vit%2Fbase,seed=3")
        self.assertEqual(new_flat, "model=vit%2Fbase,precision=bf16,seed=3")
        self.assertEqual(
            Path("scripts/009_schema") / old_flat,
            Path("scripts/009_schema/model=vit%2Fbase,seed=3"),
        )
        self.assertEqual(Path("scripts/009_schema") / new_flat, Path(
            "scripts/009_schema/model=vit%2Fbase,precision=bf16,seed=3"
        ))
        self.assertEqual(
            Path("logs/009_schema") / old_flat,
            Path("logs/009_schema/model=vit%2Fbase,seed=3"),
        )
        self.assertEqual(Path("logs/009_schema") / new_flat, Path(
            "logs/009_schema/model=vit%2Fbase,precision=bf16,seed=3"
        ))

    def test_run_id_schema_can_change_only_before_outputs(self) -> None:
        skills = ROOT / "plugins/research/skills"
        design = " ".join((skills / "experiment-design/SKILL.md").read_text().split())
        conventions = " ".join(
            (skills / "research-project-init/references/conventions.md")
            .read_text()
            .split()
        )
        dispatch = " ".join((skills / "sweep-dispatch/SKILL.md").read_text().split())

        design_schema = design.split("## 2b. run_id evolution", 1)[1].split("## 2c.", 1)[0]
        self.assertIn("Inspect checkpoints, evaluations, plots, and wave records", design_schema)
        self.assertIn("If none exists, update `RUN_ID_PARAMS`", design_schema)
        self.assertIn("normally before the first launch", design_schema)
        self.assertIn("immutable under both `nested` and `collapsed-v1`", design_schema)
        self.assertIn("Never change the identity schema in place", design_schema)
        self.assertRegex(design_schema, r"(?i)create a new numbered sub-experiment")

        self.assertIn("Before any checkpoint, evaluation, or plot output or wave README/script exists", conventions)
        self.assertIn("After any one of those surfaces exists", conventions)
        self.assertIn("immutable under both `nested` and `collapsed-v1`", conventions)
        self.assertIn("new numbered sub-experiment", conventions)
        self.assertIn("any identity-schema change under `nested` or `collapsed-v1` requires a new numbered sub-experiment", dispatch)

        def schema_decision(layout: str, surfaces: set[str]) -> str:
            if layout not in {"nested", "collapsed-v1"}:
                raise ValueError("unsupported layout")
            return (
                "update-before-first-launch"
                if not surfaces
                else "new-numbered-sub-experiment"
            )

        for layout in ("nested", "collapsed-v1"):
            with self.subTest(layout=layout, surfaces="none"):
                self.assertEqual(schema_decision(layout, set()), "update-before-first-launch")
            for surface in ("checkpoint", "evaluation", "plot", "wave"):
                with self.subTest(layout=layout, surfaces=surface):
                    self.assertEqual(
                        schema_decision(layout, {surface}),
                        "new-numbered-sub-experiment",
                    )

    def test_experiments_format_durably_records_both_run_id_decisions(self) -> None:
        tracking = (
            ROOT / "plugins/research/skills/experiments-tracking/SKILL.md"
        ).read_text()
        format_section = tracking.split("## Format", 1)[1].split(
            "## ETA calculation", 1
        )[0]
        example_match = re.search(
            r"```markdown\n(?P<example>.*?)\n```", format_section, re.DOTALL
        )
        self.assertIsNotNone(example_match)
        example = example_match.group("example")

        params_match = re.search(
            r"^run_id params:\s*(?P<params>[^\n(]+?)\s+"
            r"\(mirrors RUN_ID_PARAMS in (?P<source>[^)]+)\)$",
            example,
            re.MULTILINE,
        )
        layout_match = re.search(
            r"^run_id path layout:\s*(?P<layout>nested|collapsed-v1)\s+"
            r"\(mirrors RUN_ID_PATH_LAYOUT in (?P<source>[^)]+)\)$",
            example,
            re.MULTILINE,
        )
        self.assertIsNotNone(params_match)
        self.assertIsNotNone(layout_match)
        self.assertEqual(params_match.group("source"), layout_match.group("source"))

        ordered_params = [
            value.strip() for value in params_match.group("params").split(",")
        ]
        table_header = next(line for line in example.splitlines() if line.startswith("|"))
        table_fields = [field.strip() for field in table_header.strip("|").split("|")]
        self.assertEqual(table_fields[: len(ordered_params)], ordered_params)

        compact_format = " ".join(format_section.split())
        self.assertIn("restates both authoritative source decisions", compact_format)
        self.assertIn("layout line is durable experiment metadata", compact_format)
        self.assertIn("not a run-row identity field", compact_format)

        immutable_section = tracking.split(
            "`RUN_ID_PARAMS` may change only while", 1
        )[1].split("## Who writes what", 1)[0]
        compact_immutable = " ".join(immutable_section.split())
        self.assertIn("recorded `RUN_ID_PATH_LAYOUT` is immutable", compact_immutable)
        self.assertIn("exact wave paths remain authoritative", compact_immutable)
        self.assertIn("new numbered sub-experiment", compact_immutable)
        self.assertIn("fresh EXPERIMENTS section", compact_immutable)

    def test_scaffold_guidance_names_both_authoritative_run_id_constants(self) -> None:
        readme = markdown_template_block("README.md")
        agents = markdown_template_block("AGENTS.md")
        experiments = markdown_template_block("EXPERIMENTS.md (initial)")

        for name, scaffold in {
            "README": readme,
            "AGENTS": agents,
            "EXPERIMENTS": experiments,
        }.items():
            with self.subTest(scaffold=name):
                self.assertIn("RUN_ID_PARAMS", scaffold)
                self.assertIn("RUN_ID_PATH_LAYOUT", scaffold)
                self.assertLess(
                    scaffold.index("RUN_ID_PARAMS"),
                    scaffold.index("RUN_ID_PATH_LAYOUT"),
                )

        self.assertIn("experiment's `.py` is authoritative for both", readme)
        self.assertIn("authoritative ordered run identity", agents)
        self.assertIn("authoritative literal", agents)
        self.assertIn("header mirrors the experiment .py's ordered RUN_ID_PARAMS", experiments)
        self.assertIn("exact literal pin", experiments)
        self.assertIn("`RUN_ID_PATH_LAYOUT: nested`", experiments)
        self.assertIn("`RUN_ID_PATH_LAYOUT: collapsed-v1`", experiments)

    def test_existing_output_layout_is_immutable(self) -> None:
        skills = ROOT / "plugins/research/skills"
        contracts = {
            "experiment design": skills / "experiment-design/SKILL.md",
            "project conventions": skills / "research-project-init/references/conventions.md",
            "dispatch": skills / "sweep-dispatch/SKILL.md",
            "tracking": skills / "experiments-tracking/SKILL.md",
            "housekeeping": skills / "research-housekeeping/SKILL.md",
        }
        for name, path in contracts.items():
            contract = " ".join(path.read_text().lower().split())
            with self.subTest(contract=name):
                self.assertIn("new numbered sub-experiment", contract)
        self.assertIn("preserve the checked-in renderer and `run_id_path_layout` forever", " ".join(contracts["experiment design"].read_text().lower().split()))
        for name in ("project conventions", "dispatch", "tracking", "housekeeping"):
            contract = " ".join(contracts[name].read_text().lower().split())
            self.assertIn("immutable", contract)
        for name in ("dispatch", "tracking", "housekeeping"):
            contract = " ".join(contracts[name].read_text().lower().split())
            self.assertRegex(contract, r"(?:mismatch|disagreement|mixed)")
            self.assertRegex(contract, r"never (?:move|rename|rewrite)")

        agreement = " ".join(PROJECT_TEMPLATES.read_text().lower().split())
        self.assertIn("offer this choice only before any checkpoint, evaluation, plot output, or wave readme/script exists", agreement)
        self.assertIn("preserve the checked-in renderer and literal pin forever", agreement)
        self.assertIn("never propose or perform an in-place layout change", agreement)
        self.assertIn("another layout requires a new numbered sub-experiment", agreement)

    def test_layout_election_closes_at_first_artifact_or_wave(self) -> None:
        def elect_layout(
            recorded: str,
            proposed: str,
            existing_surfaces: dict[str, tuple[str, ...]],
        ) -> str:
            if proposed not in {"nested", "collapsed-v1"}:
                raise ValueError("unsupported layout")
            if not any(existing_surfaces.values()):
                return proposed
            if proposed != recorded:
                raise RuntimeError("create a new numbered sub-experiment")
            return recorded

        empty = {name: () for name in ("checkpoints", "evaluations", "plots", "waves")}
        self.assertEqual(elect_layout("nested", "collapsed-v1", empty), "collapsed-v1")

        exact_paths = {
            "checkpoint": "checkpoints/009_schema/model=vit/seed=3",
            "evaluation": "evaluations/009_schema/model=vit/seed=3",
            "plot": "plots/009_schema/plot_loss/model=vit/seed=3/loss.pdf",
        }
        for surface in empty:
            with self.subTest(first_existing_surface=surface):
                existing = dict(empty)
                existing[surface] = (
                    exact_paths.get(surface[:-1], "scripts/009_schema/model=vit,seed=3/wave_20260816-120000/README.md"),
                )
                self.assertEqual(elect_layout("nested", "nested", existing), "nested")
                with self.assertRaisesRegex(RuntimeError, "new numbered sub-experiment"):
                    elect_layout("nested", "collapsed-v1", existing)

        self.assertEqual(
            exact_paths,
            {
                "checkpoint": "checkpoints/009_schema/model=vit/seed=3",
                "evaluation": "evaluations/009_schema/model=vit/seed=3",
                "plot": "plots/009_schema/plot_loss/model=vit/seed=3/loss.pdf",
            },
        )

    def test_immutable_layout_and_schema_boundary_aligns_across_consumers(self) -> None:
        skills = ROOT / "plugins/research/skills"
        contracts = {
            "experiment design": skills / "experiment-design/SKILL.md",
            "conventions": skills / "research-project-init/references/conventions.md",
            "execution agreement": skills / "research-project-init/references/templates.md",
            "tracking": skills / "experiments-tracking/SKILL.md",
            "dispatch": skills / "sweep-dispatch/SKILL.md",
            "dispatch templates": skills / "sweep-dispatch/references/templates.md",
            "housekeeping": skills / "research-housekeeping/SKILL.md",
        }
        compact = {
            name: " ".join(path.read_text().lower().split())
            for name, path in contracts.items()
        }
        for name, contract in compact.items():
            with self.subTest(contract=name):
                self.assertIn("run_id_path_layout", contract)
                self.assertRegex(contract, r"(?:artifact|output).{0,100}(?:wave|wave record)")
                self.assertIn("new numbered sub-experiment", contract)

        self.assertIn("preserve the checked-in renderer and `run_id_path_layout` forever", compact["experiment design"])
        self.assertIn("preserve the checked-in renderer and literal pin forever", compact["execution agreement"])
        for name in ("conventions", "tracking", "dispatch", "dispatch templates", "housekeeping"):
            self.assertIn("immutable", compact[name])
        for name in ("tracking", "dispatch", "dispatch templates", "housekeeping"):
            self.assertRegex(compact[name], r"(?:mismatch|disagreement|mixed)")

        joined = "\n".join(compact.values())
        for forbidden in (
            "path-" + "migration",
            "abandoned-" + "frozen",
            "cutover_" + "state",
        ):
            self.assertNotIn(forbidden, joined)

        design = compact["experiment design"]
        self.assertIn("only while the experiment has no checkpoint, evaluation, or plot", compact["conventions"])
        self.assertIn("once any output or wave record exists", design)
        self.assertIn("never propose or perform an in-place layout change", design)
        self.assertIn("collapsed-v1", design)
        self.assertNotRegex(design, r"nested.{0,120}backfill|backfill.{0,120}nested")
        self.assertIn("create a new numbered sub-experiment", design)

    def test_same_wave_recovery_reuses_unchanged_identity_and_paths(self) -> None:
        original = {
            "wave_id": "20260816-120000",
            "source_tag": "wave--20260816-120000",
            "source_revision": "a" * 40,
            "layout": "collapsed-v1",
            "checkpoint": "checkpoints/009_schema/model=vit,seed=3",
            "status": "evaluations/009_schema/model=vit,seed=3/.status.json",
            "artifact": "evaluations/009_schema/model=vit,seed=3/result.json",
        }

        def recover(record: dict[str, str], observed: dict[str, str]) -> dict[str, str]:
            if observed != record:
                raise RuntimeError("recovery identity or path mismatch")
            return dict(record)

        self.assertEqual(recover(original, dict(original)), original)
        for field in ("source_tag", "source_revision", "layout", "status", "artifact"):
            with self.subTest(mismatch=field):
                changed = dict(original)
                changed[field] += "-changed"
                with self.assertRaisesRegex(RuntimeError, "mismatch"):
                    recover(original, changed)

        dispatch = " ".join(
            (ROOT / "plugins/research/skills/sweep-dispatch/SKILL.md")
            .read_text()
            .lower()
            .split()
        )
        templates = " ".join(
            (ROOT / "plugins/research/skills/sweep-dispatch/references/templates.md")
            .read_text()
            .lower()
            .split()
        )
        self.assertIn("with the same wave id", dispatch)
        self.assertIn("same paths, session names, tag, and commit", dispatch)
        self.assertIn("unchanged wave readme/script, pin, tag, and revision", dispatch)
        self.assertIn("same wave id", templates)
        self.assertIn("paths and session names are unchanged", templates)

    def test_tracking_status_vocabulary_remains_closed(self) -> None:
        tracking = (
            ROOT / "plugins/research/skills/experiments-tracking/SKILL.md"
        ).read_text()
        match = re.search(
            r"Statuses:\s*`(?P<todo>todo)`\s*→\s*`(?P<inpr>inpr)`\s*→\s*"
            r"`(?P<done>done)`\s*\|\s*`(?P<failed>failed)`",
            tracking,
        )
        self.assertIsNotNone(match)
        self.assertEqual(set(match.groupdict().values()), {"todo", "inpr", "done", "failed"})
        format_section = tracking.split("## Format", 1)[1].split("## ETA calculation", 1)[0]
        status_cells = set(
            re.findall(r"\|\s*(todo|inpr|done|failed)\s*\|", format_section)
        )
        self.assertLessEqual(status_cells, {"todo", "inpr", "done", "failed"})

    def test_producer_layout_drives_evaluation_input_and_plot_output(self) -> None:
        namespace: dict[str, object] = {}
        exec(compile(run_id_template(), "run_id.py", "exec"), namespace)
        run_id_path = namespace["run_id_path"]

        cfg = {"model": "vit/base", "lr": 0.001, "seed": 3}
        producer_params = ["model", "lr", "seed"]
        fixed_plot_params = ["model", "seed"]

        def artifact_paths(layout: str) -> tuple[Path, Path]:
            evaluation = Path("evaluations/007_probe") / run_id_path(
                cfg, producer_params, layout=layout
            )
            plot = Path("plots/007_probe/plot_loss") / run_id_path(
                cfg, fixed_plot_params, layout=layout
            )
            return evaluation, plot

        nested_eval, nested_plot = artifact_paths("nested")
        self.assertEqual(
            nested_eval,
            Path("evaluations/007_probe/model=vit%2Fbase/lr=0.001/seed=3"),
        )
        self.assertEqual(
            nested_plot,
            Path("plots/007_probe/plot_loss/model=vit%2Fbase/seed=3"),
        )

        collapsed_eval, collapsed_plot = artifact_paths("collapsed-v1")
        self.assertEqual(
            collapsed_eval,
            Path("evaluations/007_probe/model=vit%2Fbase,lr=0.001,seed=3"),
        )
        self.assertEqual(
            collapsed_plot,
            Path("plots/007_probe/plot_loss/model=vit%2Fbase,seed=3"),
        )
        self.assertEqual(len(collapsed_eval.relative_to("evaluations/007_probe").parts), 1)
        self.assertEqual(len(collapsed_plot.relative_to("plots/007_probe/plot_loss").parts), 1)

        visualization = " ".join(
            (
                ROOT / "plugins/research/skills/visualizations/SKILL.md"
            ).read_text().split()
        )

        self.assertNotIn("producer's ordinary nested run-id path", visualization)
        self.assertIn("Read the producer's recorded experiment-wide `RUN_ID_PATH_LAYOUT`", visualization)
        self.assertIn("`run_id_path(..., layout=RUN_ID_PATH_LAYOUT)`", visualization)
        self.assertIn("same recorded `RUN_ID_PATH_LAYOUT` as the producer", visualization)
        self.assertIn("after aggregated params are elided", visualization)
        self.assertIn("replaces all fixed-param path segments", visualization)
        for protected in (
            "`plots/`",
            "complete `<experiment_path>`",
            "`<script_stem>`",
            "leaf filename",
        ):
            self.assertIn(protected, visualization)
        self.assertIn("Elide every aggregated param in both layouts", visualization)

    def test_collapsed_pin_degenerate_plot_paths_are_directly_approvable(self) -> None:
        namespace: dict[str, object] = {}
        exec(compile(run_id_template(), "run_id.py", "exec"), namespace)
        run_id_path = namespace["run_id_path"]

        cfg = {"model": "vit", "seed": 3}
        plot_root = Path("plots/007_probe/plot_loss")

        def proposal(params: list[str], pinned: str) -> dict[str, object]:
            nested = plot_root / run_id_path(cfg, params, layout="nested")
            collapsed = plot_root / run_id_path(cfg, params, layout="collapsed-v1")
            if nested == collapsed:
                return {
                    "paths": (nested,),
                    "separate_alternatives": (),
                    "labels": ("nested", "collapsed-v1"),
                    "pinned": pinned,
                    "approvable": True,
                    "boundary_crossed": False,
                }
            return {
                "paths": (nested, collapsed),
                "separate_alternatives": (collapsed,),
                "labels": ("nested", "collapsed-v1"),
                "pinned": pinned,
                "approvable": pinned in {"nested", "collapsed-v1"},
                "boundary_crossed": False,
            }

        for params, expected in (
            ([], plot_root),
            (["seed"], plot_root / "seed=3"),
        ):
            with self.subTest(fixed_params=params):
                nested = plot_root / run_id_path(cfg, params, layout="nested")
                collapsed = plot_root / run_id_path(
                    cfg, params, layout="collapsed-v1"
                )
                self.assertEqual(nested, expected)
                self.assertEqual(collapsed, expected)
                self.assertEqual(
                    str(nested).encode("utf-8"), str(collapsed).encode("utf-8")
                )

                presented = proposal(params, pinned="collapsed-v1")
                self.assertEqual(presented["paths"], (expected,))
                self.assertEqual(presented["separate_alternatives"], ())
                self.assertEqual(presented["labels"], ("nested", "collapsed-v1"))
                self.assertEqual(presented["pinned"], "collapsed-v1")
                self.assertIs(presented["approvable"], True)
                self.assertIs(presented["boundary_crossed"], False)

        distinct = proposal(["model", "seed"], pinned="collapsed-v1")
        self.assertEqual(len(distinct["separate_alternatives"]), 1)
        self.assertNotEqual(distinct["paths"][0], distinct["paths"][1])

    def test_every_layout_proposal_handles_degenerate_and_distinct_paths(self) -> None:
        skills = ROOT / "plugins/research/skills"
        contracts = {
            "experiment design": (
                skills / "experiment-design/SKILL.md",
                "## 2. run_id election",
            ),
            "visualizations": (
                skills / "visualizations/SKILL.md",
                "for every export proposal",
            ),
            "project conventions": (
                skills / "research-project-init/references/conventions.md",
                "### run-output path layout approval",
            ),
            "execution agreement": (
                skills / "research-project-init/references/templates.md",
                "- plot communication:",
            ),
            "orchestrator": (
                skills / "scientific-orchestrator/references/control-contract.md",
                "identify the experiment-wide pinned",
            ),
            "flywheel auto": (
                skills / "flywheel-auto/SKILL.md",
                "identify the experiment-wide pinned",
            ),
            "autonomous protocol": (
                skills
                / "flywheel-auto/references/experiment-design-protocol-autonomous.md",
                "the experiment-wide pinned",
            ),
            "experiment protocol": (
                skills / "flywheel/references/experiment-design-protocol.md",
                "identify the experiment-wide pinned",
            ),
            "scientific audit": (
                skills / "critical-scientific-audit/SKILL.md",
                "verify that the proposal identified",
            ),
            "flywheel logging": (
                skills / "flywheel-log/SKILL.md",
                "verify that the proposal identified",
            ),
            "flywheel reproduction": (
                skills / "flywheel-reproduce/SKILL.md",
                "identify the experiment-wide pinned",
            ),
        }

        for name, (path, marker) in contracts.items():
            text = " ".join(path.read_text().lower().split())
            self.assertIn(marker, text, name)
            section = text[text.index(marker) :]
            if name == "execution agreement":
                section = section.split("- additional project-specific choices:", 1)[0]
            with self.subTest(contract=name):
                self.assertRegex(section, r"(?:show|require|display) exactly one")
                self.assertIn("collapsed-v1", section)
                self.assertRegex(section, r"zero or one (?:fixed param|item|applicable fixed param)")
                self.assertRegex(section, r"(?:show|require) (?:the path|that path|its byte-identical .+ path|one path).{0,40}(?:once|and no separate alternative)")
                self.assertRegex(
                    section,
                    r"(?:no|not as a) separate alternative|show the path once",
                )
                self.assertIn("byte-identical", section)
                self.assertRegex(section, r"pinned literal|selected or existing pinned literal")
                self.assertRegex(section, r"(?:approvable|accept that path)")
                self.assertRegex(
                    section,
                    r"(?:two or more|at least two).{0,160}exactly one|"
                    r"exactly-one.{0,80}two or more",
                )

    def test_execution_agreement_plot_contract_matches_every_propagated_surface(self) -> None:
        skills = ROOT / "plugins/research/skills"
        template = " ".join(PROJECT_TEMPLATES.read_text().lower().split())
        agreement = template.split("- plot communication:", 1)[1].split(
            "- additional project-specific choices:", 1
        )[0]
        propagated = {
            "visualizations": skills / "visualizations/SKILL.md",
            "orchestrator": skills / "scientific-orchestrator/references/control-contract.md",
            "flywheel auto": skills / "flywheel-auto/SKILL.md",
            "autonomous protocol": skills / "flywheel-auto/references/experiment-design-protocol-autonomous.md",
            "experiment protocol": skills / "flywheel/references/experiment-design-protocol.md",
            "scientific audit": skills / "critical-scientific-audit/SKILL.md",
            "flywheel logging": skills / "flywheel-log/SKILL.md",
            "flywheel reproduction": skills / "flywheel-reproduce/SKILL.md",
        }

        def assert_plot_contract(name: str, section: str) -> None:
            with self.subTest(contract=name):
                nested = re.search(
                    r"(?:complete )?(?:canonical )?`nested`.{0,80}(?:first|without optimizing)|"
                    r"first (?:display|showed) the complete (?:canonical )?`nested`",
                    section,
                )
                collapsed = re.search(r"exactly one.{0,100}`collapsed-v1`", section)
                self.assertIsNotNone(nested)
                self.assertIsNotNone(collapsed)
                self.assertLess(nested.start(), collapsed.start())
                self.assertRegex(section, r"(?:two or more|at least two)")
                self.assertRegex(section, r"zero or one.{0,180}(?:one path|show the path once)")
                self.assertIn("byte-identical", section)
                self.assertRegex(section, r"pinned literal|which literal is pinned")
                self.assertRegex(
                    section,
                    r"only (?:its |the |that )?matching form is approvable|"
                    r"only that form is an approvable plot path|"
                    r"only the form matching that pin is approvable|"
                    r"only (?:that form|the form matching the pinned literal) is approvable|"
                    r"accepted only (?:its matching form|the form matching the pinned literal)|"
                    r"accept only its matching form",
                )
                self.assertIn("new numbered sub-experiment", section)
                self.assertNotRegex(section, r"show only.{0,80}pinned|only show.{0,80}pinned")

        assert_plot_contract("execution agreement", agreement)
        for name, path in propagated.items():
            assert_plot_contract(name, " ".join(path.read_text().lower().split()))

    def test_plot_workflows_approve_only_the_pinned_layout(self) -> None:
        skills = ROOT / "plugins/research/skills"
        contracts = {
            "visualizations": skills / "visualizations/SKILL.md",
            "orchestrator": skills / "scientific-orchestrator/references/control-contract.md",
            "flywheel auto": skills / "flywheel-auto/SKILL.md",
            "autonomous protocol": (
                skills
                / "flywheel-auto/references/experiment-design-protocol-autonomous.md"
            ),
            "experiment protocol": skills / "flywheel/references/experiment-design-protocol.md",
            "scientific audit": skills / "critical-scientific-audit/SKILL.md",
            "flywheel logging": skills / "flywheel-log/SKILL.md",
            "flywheel reproduction": skills / "flywheel-reproduce/SKILL.md",
        }

        def disposition(pinned: str, proposed: str) -> str:
            return "approvable" if proposed == pinned else "new-numbered-sub-experiment"

        for pinned in ("nested", "collapsed-v1"):
            self.assertEqual(disposition(pinned, pinned), "approvable")
            other = "collapsed-v1" if pinned == "nested" else "nested"
            self.assertEqual(disposition(pinned, other), "new-numbered-sub-experiment")

        for name, path in contracts.items():
            contract = " ".join(path.read_text().lower().split())
            with self.subTest(contract=name):
                self.assertIn("pinned", contract)
                self.assertIn("new numbered sub-experiment", contract)
                self.assertRegex(
                    contract,
                    r"only (?:its |the |that )?matching form is approvable|"
                    r"only that form is an approvable plot path|"
                    r"only (?:that form|the form matching the pinned literal) is approvable|"
                    r"accepted only (?:its matching form|the form matching the pinned literal)|"
                    r"accept only its matching form",
                )
                self.assertRegex(
                    contract,
                    r"(?:other(?: form)?|layout revision).{0,160}"
                    r"new numbered sub-experiment",
                )
                self.assertRegex(
                    contract,
                    r"never (?:permit )?per-plot approval|"
                    r"not per-plot approval|never approve or select a layout per plot",
                )

    def test_every_launch_and_recovery_loop_stops_on_reserved_exits(self) -> None:
        templates = (
            ROOT / "plugins/research/skills/sweep-dispatch/references/templates.md"
        ).read_text()
        lane_loops = [line for line in templates.splitlines() if "for s in scripts/" in line]

        self.assertEqual(len(lane_loops), 3)
        for index, loop in enumerate(lane_loops):
            with self.subTest(loop=index):
                handled = {int(code) for code in re.findall(r"-eq\\?\s+(8[678])", loop)}
                self.assertEqual(handled, {86, 87, 88})
                self.assertIn("exit", loop)

    def test_tests_tree_mirrors_source_root_then_experiment_hierarchy(self) -> None:
        skills = ROOT / "plugins/research/skills"
        visualization = (skills / "visualizations/SKILL.md").read_text()
        design = (skills / "experiment-design/SKILL.md").read_text()
        init = (skills / "research-project-init/SKILL.md").read_text()
        conventions = (
            skills / "research-project-init/references/conventions.md"
        ).read_text()
        templates = PROJECT_TEMPLATES.read_text()

        code_form = "tests/code/<experiment_path>/<script_stem>/test_*.py"
        viz_form = "tests/visualizations/<experiment_path>/<script_stem>/test_*.py"
        self.assertIn(code_form, conventions)
        self.assertIn(viz_form, conventions)
        self.assertIn(code_form, design)
        self.assertIn(viz_form, visualization)

        # The source root is the first level and is never dropped: without it
        # code/common/ needs a special case and stems collide across roots.
        self.assertNotIn("tests/<experiment_path>/", conventions)
        self.assertIn("tests/code/common/run_id/test_collision_guard.py", conventions)
        self.assertIn(
            "tests/code/001_compression/002_weight_error/train/"
            "test_quantizer_roundtrip.py",
            conventions,
        )

        # Optional and independent of the pre-dispatch smoke gate.
        self.assertIn("optional and create-on-demand", conventions)
        self.assertIn("unrelated to the pre-dispatch smoke test", conventions)
        self.assertIn("never substitutes for it", design)

        # Scaffolded as a tracked empty dir, with a runnable pytest from day one.
        self.assertIn("`tests/`, `visualizations/`", init)
        self.assertIn('dev = ["pytest>=8"]', templates)
        self.assertIn(".pytest_cache/", templates)

        # Test edits deliberately do NOT trip the journal guard.
        self.assertIn(
            "'^(code|config|scripts|evaluations|visualizations)/'", templates
        )

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

    def test_orchestrator_is_a_compaction_safe_control_plane(self) -> None:
        skill_root = ROOT / "plugins/research/skills/scientific-orchestrator"
        orchestrator = (skill_root / "SKILL.md").read_text()
        roles = (skill_root / "references/subagent-roles.md").read_text()
        housekeeping = (
            ROOT / "plugins/research/skills/research-housekeeping/SKILL.md"
        ).read_text()
        orchestrator_words = " ".join(orchestrator.split())
        roles_words = " ".join(roles.split())
        housekeeping_words = " ".join(housekeeping.split())

        self.assertIn("## Main-thread control plane", orchestrator)
        self.assertIn("do not copy or fork the full main transcript", orchestrator_words)
        self.assertIn(
            "Do not silently execute the substantive assignment", orchestrator_words
        )
        self.assertIn(
            "sole reconciler, synthesizer, and decision-register writer",
            orchestrator_words,
        )
        self.assertIn("## Assignment packet", roles)
        self.assertIn("Do not pass the main transcript", roles_words)
        self.assertIn("Do not return raw logs", roles_words)
        self.assertIn("first recovery read after compaction", housekeeping_words)
        self.assertIn("sole writer of `program.md`", housekeeping_words)
        self.assertIn("Source-read-only workers may write only", roles_words)
        for trace_field in (
            '"assignment_id"',
            '"success_condition"',
            '"report_path"',
            '"stop_condition"',
            '"conclusion"',
            '"validation"',
            '"next_action"',
        ):
            self.assertIn(trace_field, housekeeping)

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
            "OPENCLIP_CACHE_DIR",
            "CACHE_DIR",
            "TORCH_NUM_WORKERS",
        ):
            self.assertGreaterEqual(templates.count(f"{key}="), 2, key)
        # Machine-varying cache paths must NOT be assigned in a project .env: the
        # file is loaded after the shell environment and silently overrides a
        # correctly configured rig, sending downloads to whatever volume it names.
        for key in machine_env_vars():
            self.assertNotRegex(templates, rf"(?m)^{key}=\S", key)
        # No rig's absolute layout, and no other project's paths, may be baked in.
        self.assertNotIn("/mnt/KS_2TB", templates)
        self.assertNotIn("qat-transfer", templates)
        # Project-scoped storage is repo-relative, so one .env is correct on every
        # rig and no personal directory layout leaks into a distributed template.
        self.assertIn("OPENCLIP_CACHE_DIR=storage/openclip", templates)
        self.assertIn("CACHE_DIR=storage/cache", templates)
        self.assertNotIn("<storage_root>/", templates)
        self.assertNotIn("PARA/Projects", templates)
        self.assertIn("## code/common/paths.py", templates)
        self.assertIn("def project_path(", templates)
        self.assertIn("TORCH_NUM_WORKERS=16", templates)
        self.assertIn("never copy or commit a token", templates)
        self.assertIn("Do not create this file with placeholders", templates)
        self.assertIn("`.gitkeep` in every directory", skill)
        self.assertIn("Ask which single directory name", skill)
        self.assertIn('name = "{{ENVIRONMENT_NAME}}"', templates)
        self.assertIn("{{ENVIRONMENT_NAME}}/", templates)

    def test_project_init_bootstraps_the_machine_registry_before_any_rig_write(self) -> None:
        """A scaffold that skips the registry leaves every rig on ~/.cache.

        The caches a rig uses are declared in the user registry and installed by
        `provision-env`; nothing else puts them there. Init predated that model
        and never mentioned either, so a fresh project fell back to $HOME -- the
        small quota'd volume on a shared machine.
        """
        init_root = ROOT / "plugins/research/skills/research-project-init"
        skill = (init_root / "SKILL.md").read_text()
        conventions = (init_root / "references/conventions.md").read_text()

        for token in (
            "~/.config/rigsync/machines.toml",
            "storage_root",
            "[machines.<rig>.caches]",
            "check-paths",
            "provision-env",
            "push-env",
        ):
            self.assertIn(token, skill, token)
        # The order is the contract: a path validated after the clone, or caches
        # installed after the first run, are checks that arrive too late.
        gate = skill.split("Bring up every intended rig")[1]
        positions = [
            gate.index(f"rig-sync {name}")
            for name in ("check-paths", "provision-env", "prepare", "push-env", "doctor")
        ]
        self.assertEqual(positions, sorted(positions))
        # The pre-3.5.0 prose pointed at a cache profile the templates no longer
        # carry, which is how cache vars ended up back in .env.
        self.assertNotIn("standard cache profile", skill)
        self.assertNotIn("standard cache paths verbatim", skill)
        self.assertIn("prerequisite for scaffolding", conventions)
        self.assertNotIn("such as the shared cache paths", conventions)

    def test_rig_paths_and_storage_are_read_back_never_retyped(self) -> None:
        rig_root = ROOT / "plugins/research/skills/rig-sync"
        rig_skill = (rig_root / "SKILL.md").read_text()
        rig_config = (rig_root / "references/configuration.md").read_text()
        dispatch = (
            ROOT / "plugins/research/skills/sweep-dispatch/references/templates.md"
        ).read_text()
        dispatch_skill = (
            ROOT / "plugins/research/skills/sweep-dispatch/SKILL.md"
        ).read_text()

        for command in ("check-paths", "repo-path", "storage-env", "push-env"):
            self.assertIn(command, rig_skill, command)
            self.assertIn(command, rig_config, command)
        # Dispatch must resolve the project path from sync.toml rather than
        # spelling a second copy of it into every launch command.
        self.assertNotIn("<project path", dispatch)
        self.assertIn("rig-sync repo-path --machine <rig>", dispatch)
        self.assertIn("rig-sync storage-env --machine <rig>", dispatch)
        self.assertIn("rig-sync repo-path --machine <rig>", dispatch_skill)
        # Every rig in the canonical fleet must be configurable, not just the
        # three that happened to appear in the examples.
        for rig in ("rig-4090", "rig-3090-ti", "rig-3080-ti", "behemoth"):
            self.assertIn(rig, rig_config, rig)

    def test_machine_env_var_list_matches_the_code_that_enforces_it(self) -> None:
        """The prose list and the enforced list drifted apart once already."""
        config = (
            ROOT / "plugins/research/skills/rig-sync/references/configuration.md"
        ).read_text()
        section = config.split("### `[machines.<rig>.caches]`")[1].split("##")[0]
        documented = set(re.findall(r"`([A-Z][A-Z0-9_]+)`", section))
        self.assertEqual(documented & machine_env_vars(), machine_env_vars())

    def test_no_distributed_skill_carries_a_personal_absolute_path(self) -> None:
        offenders = []
        for path in (ROOT / "plugins").rglob("*"):
            if not path.is_file() or path.suffix not in {".md", ".py", ".yaml", ".json"}:
                continue
            text = path.read_text(errors="replace")
            if "PARA/Projects" in text or "/mnt/KS_2TB" in text:
                offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual(offenders, [])

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

    def test_experiment_design_mandates_the_whole_config_reaches_wandb(self) -> None:
        """A run's config cannot change retroactively, so a subset is permanent loss."""
        skill = (
            ROOT / "plugins/research/skills/experiment-design/SKILL.md"
        ).read_text()
        conventions = (
            ROOT / "plugins/research/skills/research-project-init/references/conventions.md"
        ).read_text()
        section = skill.split("## 5. WandB checklist")[1].split("\n## ")[0]

        self.assertIn("wandb.init(config=wandb_config(cfg)", section)
        self.assertIn("ENTIRE resolved config", section)
        self.assertIn("cannot be changed retroactively", section)
        self.assertIn("provenance", section)
        self.assertIn("wandb_config(cfg)", conventions)

        # the pre-launch gate that checks helper presence must know about them,
        # or an older run_id.py passes and the script NameErrors at launch
        dispatch = (ROOT / "plugins/research/skills/sweep-dispatch/SKILL.md").read_text()
        gate = dispatch.split("**helper safety check**")[1].split("\n1c.")[0]
        self.assertIn("wandb_config", gate)

    def test_run_id_template_resolves_the_config_in_exactly_one_place(self) -> None:
        """One resolver, every recorder -- so wandb and .run_config.json cannot drift."""
        block = run_id_template()
        self.assertEqual(block.count("OmegaConf.to_container"), 1)
        self.assertIn("def resolved_config(cfg)", block)
        self.assertIn("def wandb_config(cfg)", block)
        self.assertIn("resolved = resolved_config(cfg)", block)

    def test_wandb_config_carries_the_whole_config_plus_wave_provenance(self) -> None:
        namespace: dict[str, object] = {}
        stub = type(sys)("omegaconf")
        stub.OmegaConf = type(
            "OmegaConf",
            (),
            {"is_config": staticmethod(lambda cfg: False)},
        )
        sys.modules["omegaconf"] = stub
        try:
            exec(compile(run_id_template(), "run_id.py", "exec"), namespace)
            wandb_config = namespace["wandb_config"]
            guard_run_config = namespace["guard_run_config"]

            cfg = {"model": "mlp", "lr": 0.001, "seed": 0, "batch_size": 32}
            logged = wandb_config(cfg)
            provenance = logged.pop("provenance")

            # everything, not just the run_id params
            self.assertEqual(logged, cfg)
            self.assertEqual(set(provenance), status_provenance_fields())

            with tempfile.TemporaryDirectory() as tmp:
                run_dir = Path(tmp) / "run"
                guard_run_config(cfg, ["model", "lr", "seed"], run_dir)
                snapshot = json.loads((run_dir / ".run_config.json").read_text())
            # the wandb payload is the snapshot plus provenance -- never a subset
            self.assertEqual(logged, snapshot)

            with self.assertRaises(RuntimeError):
                wandb_config({**cfg, "provenance": "mine"})
        finally:
            del sys.modules["omegaconf"]


if __name__ == "__main__":
    unittest.main()
