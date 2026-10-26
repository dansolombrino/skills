from __future__ import annotations

import importlib.util
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock


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
        self.assertEqual(manifest["version"], "14.9.0")
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
            (ROOT / "plugins/research/skills/environment-sync/SKILL.md").is_file()
        )
        skill_names = {
            path.parent.name
            for path in (ROOT / "plugins/research/skills").glob("*/SKILL.md")
        }
        self.assertEqual(len(skill_names), 12)
        self.assertEqual(
            skill_names,
            {
                "environment-sync",
                "experiment-design",
                "experiments-tracking",
                "phone-bridge",
                "research-journal",
                "research-project-init",
                "rig-board",
                "rig-sync",
                "run-identity",
                "sweep-dispatch",
                "sweep-supervisor",
                "visualizations",
            },
        )
        # The scientific-process and Flywheel families were removed in 5.0.0 and
        # must not reappear: the plugin is the engineering execution layer only.
        self.assertEqual(
            skill_names
            & {
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
            },
            set(),
        )

    def test_plot_download_asset_meets_the_page_contract(self) -> None:
        skills = ROOT / "plugins/research/skills"
        asset = (skills / "visualizations/assets/plot-download.js").read_text()
        browser = (skills / "visualizations/assets/plot-browser.html").read_text()
        for needle in ("All runs", "Selection", "data-plot-leaf", "plotSelectedRuns", "plot-data"):
            self.assertIn(needle, asset)
        # The shared file must not keep the viewer's injected <base>.
        self.assertIn("base[data-plot-browser]", asset)
        self.assertIn("<base data-plot-browser", browser)
        self.assertIn('id="download"', browser)

    def test_reader_toolbar_asset_and_its_server_injection(self) -> None:
        skills = ROOT / "plugins/research/skills"
        shell = (skills / "visualizations/assets/plot-shell.js").read_text()
        browser = (skills / "visualizations/assets/plot-browser.html").read_text()
        for mark in ("data-plot-controls", "data-plot-provenance"):
            self.assertIn(mark, shell)
        # Hide/show the selection and a real zoom, kept across the page's own redraws.
        for act in ('sel:', 'in:', 'out:', 'reset', 'lock:', 'wheel:', 'fit:', 'png:', 'svg:'):
            self.assertIn(act, shell)
        self.assertIn('for (const name of ["newPlot", "react"])', shell)
        # Older plots get it from the server; plot keys still reach the browser from inside a frame.
        self.assertIn('fetch("plot-shell.js"', browser)
        self.assertIn("window.__plotShell", browser)
        self.assertIn("plot-shell-key", browser)
        self.assertIn("plot-shell-key", shell)

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

    def test_hashed_v1_stays_legacy_and_never_lossy(self) -> None:
        import hashlib
        import tempfile

        namespace: dict[str, object] = {}
        exec(compile(run_id_template(), "run_id.py", "exec"), namespace)
        run_id_path = namespace["run_id_path"]
        run_id_flat = namespace["run_id_flat"]
        guard_run_config = namespace["guard_run_config"]
        write_run_id_map = namespace["write_run_id_map"]

        cfg = {"model": "vit/base", "lr": 0.001, "seed": 3}
        params = ["model", "lr", "seed"]
        flat = run_id_flat(cfg, params)
        hashed = run_id_path(cfg, params, layout="hashed-v1")
        expected = "rid-" + hashlib.sha256(flat.encode()).hexdigest()[:16]
        self.assertEqual(hashed.parts, (expected,))

        with tempfile.TemporaryDirectory() as tmp:
            exp_root = Path(tmp) / "evaluations" / "009_schema"
            run_dir = exp_root / hashed
            guard_run_config(cfg, params, run_dir, layout="hashed-v1")
            record = json.loads((run_dir / ".run_id.json").read_text())
            self.assertEqual(record["hash"], expected)
            self.assertEqual(record["run_id_flat"], flat)
            self.assertEqual(record["run_id"], {"model": "vit/base", "lr": 0.001, "seed": 3})
            # the per-run guard never writes the map; the wave's end rebuilds it once
            self.assertFalse((exp_root / "RUN_ID_MAP.json").exists())
            write_run_id_map(exp_root)
            run_map = json.loads((exp_root / "RUN_ID_MAP.json").read_text())
            self.assertEqual(run_map["by_hash"][expected]["run_id"], record["run_id"])
            self.assertEqual(run_map["by_run_id_flat"][flat], expected)
            # same run again: idempotent
            guard_run_config(cfg, params, run_dir, layout="hashed-v1")
            # a different flat id claiming the same hash directory is a hard error
            (run_dir / ".run_id.json").write_text(
                json.dumps({**record, "run_id_flat": flat + ",extra=1"})
            )
            with self.assertRaisesRegex(RuntimeError, "hash collision"):
                guard_run_config(cfg, params, run_dir, layout="hashed-v1")


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

        version_match = re.search(
            r"^run_id version:\s*(?P<version>\d+) — (?P<params>[^\n(]+?)\s+"
            r"\(mirrors (?P<source>config/[^)]+/RUN_ID_VERSIONS\.json)\)$",
            example,
            re.MULTILINE,
        )
        layout_match = re.search(
            r"^run_id path layout:\s*(?P<layout>hashed-v2)\s+"
            r"\(mirrors RUN_ID_PATH_LAYOUT in (?P<source>[^)]+)\)$",
            example,
            re.MULTILINE,
        )
        self.assertIsNotNone(version_match)
        self.assertIsNotNone(layout_match)
        self.assertIn("run_id map: evaluations/000_grokking/RUN_ID_MAP.json", example)

        params = [value.strip() for value in version_match.group("params").split(",")]
        table_header = next(line for line in example.splitlines() if line.startswith("|"))
        table_fields = [field.strip() for field in table_header.strip("|").split("|")]
        self.assertEqual(table_fields[: len(params)], params)
        # the hash, then its readable label, right after the param columns
        self.assertEqual(table_fields[len(params) : len(params) + 2], ["run_id", "label"])
        for row in example.splitlines()[2:]:
            if row.startswith("|") and not row.startswith("|-"):
                cells = [cell.strip() for cell in row.strip("|").split("|")]
                if cells[0] == "model":
                    continue
                self.assertRegex(cells[len(params)], r"^rid-[0-9a-f]{32}$")


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

    def test_segments_v1_renders_losslessly_and_fails_hard_on_collisions(self) -> None:
        import hashlib

        namespace: dict[str, object] = {}
        exec(compile(run_id_template(), "run_id.py", "exec"), namespace)
        run_id_path = namespace["run_id_path"]
        run_id_name = namespace["run_id_name"]
        run_id_flat = namespace["run_id_flat"]
        validate = namespace["validate_run_id_segments"]
        group_hash = namespace["run_id_group_hash"]
        guard_run_config = namespace["guard_run_config"]
        check_run_id_plan = namespace["check_run_id_plan"]
        preflight = namespace["preflight_run_id_path"]
        write_run_id_map = namespace["write_run_id_map"]

        params = ["model", "lr", "wd", "seed"]
        segments = [("model",), ({"optim_params": ("lr", "wd")}, "seed")]
        cfg = {"model": "vit/base", "lr": 0.001, "wd": 0.01, "seed": 3}

        # Rendering: explicit items keep the canonical pairs; a group is name=<16 hex>.
        digest = hashlib.sha256(
            "segments-v1\0optim_params\0lr=0.001,wd=0.01".encode()
        ).hexdigest()[:16]
        path = run_id_path(cfg, params, layout="segments-v1", segments=segments)
        self.assertEqual(
            path.parts, ("model=vit%2Fbase", f"optim_params={digest},seed=3")
        )
        self.assertEqual(
            run_id_name(cfg, params, layout="segments-v1", segments=segments),
            f"model=vit%2Fbase/optim_params={digest},seed=3",
        )
        # A group hash depends only on its own params.
        self.assertEqual(
            group_hash(cfg, "optim_params", ("lr", "wd"), layout="segments-v1"),
            group_hash({**cfg, "seed": 9, "model": "x"}, "optim_params", ("lr", "wd"), layout="segments-v1"),
        )
        self.assertNotEqual(
            group_hash(cfg, "optim_params", ("lr", "wd"), layout="segments-v1"),
            group_hash({**cfg, "wd": 0.02}, "optim_params", ("lr", "wd"), layout="segments-v1"),
        )
        # nested and collapsed are special cases of segments-v1 with no groups.
        self.assertEqual(
            run_id_path(cfg, params, layout="segments-v1", segments=[(p,) for p in params]),
            run_id_path(cfg, params, layout="nested"),
        )
        self.assertEqual(
            run_id_path(cfg, params, layout="segments-v1", segments=[tuple(params)]),
            run_id_path(cfg, params, layout="collapsed-v1"),
        )
        # Legacy layouts keep flat naming.
        self.assertEqual(run_id_name(cfg, params), run_id_flat(cfg, params))

        # Validation: every param exactly once, safe unique group names.
        for bad in (
            [("model",), ({"optim_params": ("lr", "wd")},)],
            [("model", "seed"), ({"optim_params": ("lr", "wd", "seed")},)],
            [("model", "seed", "extra"), ({"optim_params": ("lr", "wd")},)],
            [("model", "seed"), ({"lr": ("lr", "wd")},)],
            [("model", "seed"), ({"optim/params": ("lr", "wd")},)],
            [("model", "seed"), ({"g": ()}, "lr", "wd")],
            [(), ("model", "seed", "lr", "wd")],
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate(params, bad, layout="segments-v1")
        with self.assertRaisesRegex(ValueError, "requires the experiment's RUN_ID_SEGMENTS"):
            run_id_path(cfg, params, layout="segments-v1")
        with self.assertRaisesRegex(ValueError, "only applies to segments"):
            run_id_path(cfg, params, layout="nested", segments=segments)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "evaluations" / "009_schema"
            run_dir = root / path
            kwargs = {"layout": "segments-v1", "segments": segments, "experiment_root": root}
            guard_run_config(cfg, params, run_dir, **kwargs)
            guard_run_config(cfg, params, run_dir, **kwargs)  # idempotent rerun
            other = {**cfg, "seed": 4}
            guard_run_config(other, params, root / run_id_path(other, params, layout="segments-v1", segments=segments), **kwargs)

            record = json.loads((run_dir / ".run_id.json").read_text())
            self.assertEqual(record["layout"], "segments-v1")
            self.assertEqual(record["path"], path.as_posix())
            self.assertEqual(record["run_id_flat"], run_id_flat(cfg, params))
            self.assertEqual(record["groups"]["optim_params"], {"hash": digest, "params": {"lr": 0.001, "wd": 0.01}})
            self.assertFalse((root / "RUN_ID_MAP.json").exists())
            write_run_id_map(root, layout="segments-v1")
            run_map = json.loads((root / "RUN_ID_MAP.json").read_text())
            self.assertEqual(run_map["by_path"][path.as_posix()], run_id_flat(cfg, params))
            self.assertEqual(run_map["by_run_id_flat"][run_id_flat(cfg, params)], path.as_posix())
            self.assertEqual(run_map["groups"]["optim_params"], {digest: {"lr": 0.001, "wd": 0.01}})

            with self.assertRaisesRegex(RuntimeError, "is not the segments-v1 path"):
                guard_run_config(cfg, params, root / "elsewhere", **kwargs)
            with self.assertRaisesRegex(ValueError, "requires experiment_root"):
                guard_run_config(cfg, params, run_dir, layout="segments-v1", segments=segments)

            # The plan check passes for fresh runs and rejects duplicates.
            fresh = {**cfg, "seed": 5}
            self.assertEqual(
                check_run_id_plan([fresh], params, segments, root, layout="segments-v1"),
                [run_id_path(fresh, params, layout="segments-v1", segments=segments)],
            )
            with self.assertRaisesRegex(RuntimeError, "planned twice"):
                check_run_id_plan([fresh, dict(fresh)], params, segments, root, layout="segments-v1")

            # Force a group-hash collision: different optimizer values, same hash.
            real_hash = namespace["run_id_group_hash"]
            namespace["run_id_group_hash"] = lambda c, n, g, **_: digest
            try:
                clash = {**cfg, "wd": 0.5}
                with self.assertRaisesRegex(RuntimeError, "group hash collision"):
                    check_run_id_plan([{**clash, "seed": 7}], params, segments, root, layout="segments-v1")
                with self.assertRaisesRegex(RuntimeError, "run_id path collision"):
                    check_run_id_plan([clash], params, segments, root, layout="segments-v1")
                # The O(1) guard cannot see another run's groups; the once-per-wave
                # map rebuild catches a clash that bypassed the plan check.
                clash_dir = root / run_id_path({**clash, "seed": 6}, params, layout="segments-v1", segments=segments)
                guard_run_config({**clash, "seed": 6}, params, clash_dir, **kwargs)
                with self.assertRaisesRegex(RuntimeError, "group hash collision"):
                    write_run_id_map(root, layout="segments-v1")
                (clash_dir / ".run_id.json").unlink()
                (clash_dir / ".run_config.json").unlink()
                # Same directory claimed by a different run is a hard error too.
                with self.assertRaisesRegex(RuntimeError, "hash collision"):
                    guard_run_config(clash, params, run_dir, **kwargs)
            finally:
                namespace["run_id_group_hash"] = real_hash

            # Filesystem limits are preflighted, never truncated.
            report = preflight(root, path)
            self.assertEqual(report["components"][0], ("model=vit%2Fbase", 16))
            with self.assertRaisesRegex(ValueError, "overflows"):
                preflight(root, Path("x" * 256))
            with self.assertRaisesRegex(ValueError, "overflows"):
                check_run_id_plan([{**cfg, "model": "m" * 300}], params, segments, root, layout="segments-v1")

    def test_segments_v2_renders_only_safe_characters(self) -> None:
        import hashlib

        namespace: dict[str, object] = {}
        exec(compile(run_id_template(), "run_id.py", "exec"), namespace)
        run_id_path = namespace["run_id_path"]
        run_id_name = namespace["run_id_name"]
        check_run_id_plan = namespace["check_run_id_plan"]
        validate = namespace["validate_run_id_segments"]

        params = ["model", "hidden", "betas", "lr", "seed"]
        segments = [("model",), ("hidden", "betas"), ({"optim_params": ("lr",)}, "seed")]
        cfg = {"model": "vit-b_16", "hidden": [64, 128], "betas": (0.9, 0.999), "lr": 1e-3, "seed": 0}
        digest = hashlib.sha256("segments-v2\0optim_params\0lr=0.001".encode()).hexdigest()[:16]

        path = run_id_path(cfg, params, layout="segments-v2", segments=segments)
        self.assertEqual(
            path.parts,
            ("model=vit-b_16", "hidden=64-128,betas=0.9-0.999", f"optim_params={digest},seed=0"),
        )
        self.assertEqual(run_id_name(cfg, params, layout="segments-v2", segments=segments), path.as_posix())
        self.assertRegex(path.as_posix(), r"^[A-Za-z0-9._=,/-]+$")
        # The same run under the legacy pin keeps its percent-encoded directory.
        self.assertIn(
            "hidden=%5B64%2C128%5D",
            run_id_path(cfg, params, layout="segments-v1", segments=segments).as_posix(),
        )

        # Anything that would need escaping is refused, never escaped.
        for bad in ([-1, 2], ["a-b"], [], [[1]], {"k": 1}, "a b", "a/b", "x%y", "~x", "a,b"):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, "hash group"):
                run_id_path({**cfg, "hidden": bad}, params, layout="segments-v2", segments=segments)
        with tempfile.TemporaryDirectory() as tmp, self.assertRaisesRegex(ValueError, "hash group"):
            check_run_id_plan([{**cfg, "hidden": [-1, 2]}], params, segments, Path(tmp))
        # ...and renders once its param sits in a hash group.
        grouped = [("model",), ({"arch": ("hidden",)}, "betas"), ({"optim_params": ("lr",)}, "seed")]
        rendered = run_id_path({**cfg, "hidden": [-1, 2]}, params, layout="segments-v2", segments=grouped)
        self.assertRegex(rendered.parts[1], r"^arch=[0-9a-f]{16},betas=0\.9-0\.999$")
        # Group names are held to the same alphabet.
        with self.assertRaises(ValueError):
            validate(params, [("model", "hidden", "betas"), ({"optim~params": ("lr",)}, "seed")])

    def test_run_id_guard_is_o1_per_run_and_writes_atomically(self) -> None:
        namespace: dict[str, object] = {}
        exec(compile(run_id_template(), "run_id.py", "exec"), namespace)
        run_id_path = namespace["run_id_path"]
        guard_run_config = namespace["guard_run_config"]
        check_run_id_plan = namespace["check_run_id_plan"]
        write_run_id_map = namespace["write_run_id_map"]

        params = ["model", "lr", "seed"]
        segments = [("model",), ({"optim": ("lr",)}, "seed")]
        cfg = {"model": "mlp", "lr": 0.1, "seed": 0}

        def run_dir(root, c):
            return root / run_id_path(c, params, layout="segments-v2", segments=segments)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "evaluations" / "001_scale"
            kwargs = {"layout": "segments-v2", "segments": segments, "experiment_root": root}

            # The guard never reads another run: a torn sibling record cannot crash it.
            torn = run_dir(root, {**cfg, "seed": 99})
            torn.mkdir(parents=True)
            (torn / ".run_id.json").write_text('{"layout": "segm')
            guard_run_config(cfg, params, run_dir(root, cfg), **kwargs)
            self.assertFalse((root / "RUN_ID_MAP.json").exists())
            # The once-per-wave rescans still see it and fail loudly.
            with self.assertRaises(json.JSONDecodeError):
                write_run_id_map(root, layout="segments-v2")
            with self.assertRaises(json.JSONDecodeError):
                check_run_id_plan([{**cfg, "seed": 1}], params, segments, root)
            (torn / ".run_id.json").unlink()

            # A stale map never hides a collision from the plan check: it rescans records.
            write_run_id_map(root, layout="segments-v2")
            guard_run_config({**cfg, "seed": 2}, params, run_dir(root, {**cfg, "seed": 2}), **kwargs)
            real_hash = namespace["run_id_group_hash"]
            existing = real_hash(cfg, "optim", ("lr",))
            namespace["run_id_group_hash"] = lambda c, n, g, **_: existing
            try:
                with self.assertRaisesRegex(RuntimeError, "run_id path collision"):
                    check_run_id_plan([{**cfg, "lr": 0.5, "seed": 2}], params, segments, root)
            finally:
                namespace["run_id_group_hash"] = real_hash

            write_run_id_map(root, layout="segments-v2")
            leftovers = [p.name for p in root.rglob("*") if ".tmp." in p.name]
            self.assertEqual(leftovers, [])

        body = run_id_template()
        guard = body.split("def guard_run_config(", 1)[1].split("\ndef ", 1)[0]
        self.assertNotIn("write_run_id_map(", guard)
        self.assertNotIn("build_run_id_index(", guard)
        self.assertNotIn("RUN_ID_MAP_NAME", guard)
        self.assertNotIn(".write_text(", guard)
        self.assertIn("socket.gethostname()", body)


    def test_no_skill_anywhere_describes_a_run_id_derived_plot_path(self) -> None:
        """plots/ stopped being run-scoped in research 4.0.0.

        Swept over every distributed markdown file rather than a hand-listed
        map: the surface most likely to still assert the old contract is the
        one whose author forgot it existed, and such a surface would be
        missing from a hand-maintained list for exactly the same reason.
        """
        skills = ROOT / "plugins/research/skills"
        offenders: list[tuple[str, str]] = []

        for path in sorted(skills.rglob("*.md")):
            contract = " ".join(path.read_text().lower().split())
            rel = str(path.relative_to(skills))
            if "run-id-derived" in contract:
                offenders.append((rel, "run-ID-derived plot path"))
            if "<partial_run_id path>" in contract:
                offenders.append((rel, "partial run_id plot path"))
            embedded = re.search(r"plots/[^ ]*/(?:model|lr|seed)=", contract)
            if embedded:
                offenders.append((rel, f"run_id in plot path: {embedded.group(0)}"))

        self.assertEqual(offenders, [], f"run_id-derived plot paths survive: {offenders}")

    def test_the_lane_loop_is_the_only_launcher_and_stops_on_reserved_exits(self) -> None:
        templates = (
            ROOT / "plugins/research/skills/sweep-dispatch/references/templates.md"
        ).read_text()
        # the static per-lane glob is gone: a run is placed when a lane claims it, never by its file name
        self.assertNotIn("for s in .waves/<wave_id>/scripts/", templates)
        self.assertNotIn("wave_<rig>_gpu<ids>.sh", templates)
        stops = [line for line in self.render_lane_script("20260801-000000").splitlines() if "exit \"$rc\"" in line]
        self.assertEqual(len(stops), 1)
        handled = {int(code) for code in re.findall(r"-eq\s+(8[678])", stops[0])}
        self.assertEqual(handled, {86, 87, 88})
        launch = [line for line in templates.splitlines() if "tmux new-session -d -s" in line]
        self.assertEqual(len(launch), 1)
        self.assertIn("tmux has-session -t", launch[0])
        self.assertIn("_lanes/wave_<wave_id>/lane.sh", launch[0])

    def test_scaffold_templates_keep_tests_runnable_and_outside_the_journal_guard(self) -> None:
        templates = PROJECT_TEMPLATES.read_text()
        self.assertIn('dev = ["pytest>=8"]', templates)
        self.assertIn(".pytest_cache/", templates)
        # Test edits deliberately do NOT trip the journal guard.
        self.assertIn(
            "'^(code|config|scripts|evaluations|visualizations)/'", templates
        )

    def test_project_init_templates_scaffold_research_2_records(self) -> None:
        templates = PROJECT_TEMPLATES.read_text()
        for heading in ("README.md", "AGENTS.md", "EXPERIMENTS.md (initial)", "JOURNAL.md (initial)"):
            markdown_template_block(heading)
        self.assertIn("## CLAUDE.md", templates)
        self.assertNotIn("## program/", templates)
        self.assertNotIn("engineering_mode", templates)
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
        # Project-scoped storage is repo-relative; no personal layout is baked in.
        self.assertIn("OPENCLIP_CACHE_DIR=storage/openclip", templates)
        self.assertIn("CACHE_DIR=storage/cache", templates)
        self.assertIn("TORCH_NUM_WORKERS=16", templates)
        self.assertNotIn("<storage_root>/", templates)
        self.assertIn("def project_path(", templates)
        self.assertIn('name = "{{ENVIRONMENT_NAME}}"', templates)
        self.assertIn("{{ENVIRONMENT_NAME}}/", templates)
        self.assertIn("scripts/**/leonardo.jobs", templates)

    def test_project_init_brings_rigs_up_in_order(self) -> None:
        """Paths are checked before the clone, caches installed before the first run."""
        skill = (ROOT / "plugins/research/skills/research-project-init/SKILL.md").read_text()
        gate = skill.split("Bring up every intended rig")[1]
        positions = [
            gate.index(f"rig-sync {name}")
            for name in ("check-paths", "provision-env", "prepare", "push-env", "doctor")
        ]
        self.assertEqual(positions, sorted(positions))

    def test_project_init_asks_nothing_it_resolves(self) -> None:
        """Rigs, an empty-repo re-init and the private GitHub repo are taken, not asked."""
        skill_dir = ROOT / "plugins/research/skills/research-project-init"
        skill = " ".join((skill_dir / "SKILL.md").read_text().split())
        templates = (skill_dir / "references/templates.md").read_text()
        self.assertIn("Never ask what this skill resolves", skill)
        self.assertIn("every `board.rigs` machine in the user registry, never asked", skill)
        self.assertIn("delete `.git` and `git init -b main`", skill)
        self.assertIn("There is no project description", skill)
        self.assertNotIn("PROJECT_DESCRIPTION", templates)

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

    def test_rig_sync_documents_canonical_3090_ti_hostname(self) -> None:
        configuration = (
            ROOT / "plugins/research/skills/rig-sync/references/configuration.md"
        ).read_text()
        self.assertIn('hostname = "rig-3090-ti"', configuration)
        self.assertNotIn('hostname = "rig-3090"', configuration)

    def test_wave_script_verifies_the_environment_before_python(self) -> None:
        templates = (
            ROOT / "plugins/research/skills/sweep-dispatch/references/templates.md"
        ).read_text()
        self.assertIn("EXPECTED_ENVIRONMENT_FINGERPRINT", templates)
        self.assertIn(
            '"environment_fingerprint": os.environ.get("ENVIRONMENT_FINGERPRINT")',
            template_block("code/common/status.py"),
        )
        environment_guard = templates.index("EXPECTED_ENVIRONMENT_FINGERPRINT")
        python = templates.index('"$ENVIRONMENT_DIR/bin/python" "$WAVE_TREE/code/<NNN_exp>/<script>.py"')
        self.assertLess(environment_guard, python)
        self.assertIn('"$rc" -eq 87', templates)
        self.assertIn('ENVIRONMENT_DIR="<ENVIRONMENT_DIR printed by environment-sync verify', templates)
        self.assertNotIn(".venv/bin/python", templates)

    def test_wave_isolation_is_built_into_the_templates(self) -> None:
        skills = ROOT / "plugins/research/skills"
        templates = (skills / "sweep-dispatch/references/templates.md").read_text()
        slurm = (skills / "sweep-dispatch/references/cineca-slurm.md").read_text()
        init_templates = PROJECT_TEMPLATES.read_text()
        # Scripts run from the shared root and read code from the worktree.
        self.assertIn('PROJECT_ROOT="${SLURM_SUBMIT_DIR:-$PWD}"', templates)
        self.assertIn('WAVE_TREE="$PROJECT_ROOT/.waves/$WAVE_ID"', templates)
        self.assertIn('export RESEARCH_PROJECT_ROOT="$PROJECT_ROOT"', templates)
        self.assertNotIn('cd "$(dirname "$0")', templates)
        self.assertNotIn('cd "$(dirname "$0")', init_templates)
        guard = templates.index("git rev-parse --git-common-dir")
        python = templates.index('"$WAVE_TREE/code/<NNN_exp>/<script>.py"')
        self.assertLess(guard, python)
        self.assertIn("LANE_GPUS=1 .waves/<wave_id>/scripts/", slurm)
        self.assertNotIn('cd "$SLURM_SUBMIT_DIR"', slurm)
        # Ignores, editor excludes, and the paths helper.
        for entry in (".waves/", ".envs/"):
            self.assertIn(f"\n{entry}\n", init_templates)
        self.assertIn('".waves/**": true', init_templates)
        self.assertIn('ROOT_ENV = "RESEARCH_PROJECT_ROOT"', init_templates)
        self.assertIn("def source_path(", init_templates)

    def render_wave_script(self, wave: str, env_dir: str) -> str:
        templates = (
            ROOT / "plugins/research/skills/sweep-dispatch/references/templates.md"
        ).read_text()
        start = templates.index("## wave.sh")
        body = templates[templates.index("```bash\n", start) + len("```bash\n"):]
        body = body[: body.index("\n```\n")]
        self.assertIn("# ---- behemoth in the pool ONLY", body)
        values = {
            '"<0 | the granted set>"': '"0"',
            '"<wave_id>"': f'"{wave}"',
            '"<run_id_name: the hash under hashed-v2; the rendered run_id under a segments pin; the flat run_id under another legacy pin>"': '"rid-' + "a" * 32 + '"',
            '"<run_id_label under hashed-v2; the flat run_id under a legacy pin>"': '"seed=0"',
            '"<hashed-v2, or a legacy segments-v2 | segments-v1 | nested | collapsed-v1 | hashed-v1, copied from the experiment record>"': '"hashed-v2"',
            '"<exact checkpoint directory resolved by canonical run_id_path>"': f'"checkpoints/000_x/{wave}"',
            '"<exact evaluation directory resolved by canonical run_id_path>"': f'"evaluations/000_x/{wave}"',
            '"logs/<NNN_exp>/<run_id folder>/wave_<wave_id>"': f'"logs/000_x/seed=0/wave_{wave}"',
            '"<exact expected final artifact path under CHECKPOINT_DIR or EVAL_DIR>"': f'"evaluations/000_x/{wave}/result.json"',
            "<this run's retained checkpoint footprint in KiB, from experiment-design>": "0",
            '"<ENVIRONMENT_DIR printed by environment-sync verify: .envs/<env_key>>"': f'"{env_dir}"',
            '"<64-char fingerprint verified on every assigned rig>"': '"' + "f" * 64 + '"',
            '("$WAVE_TREE/<gpu_smoke script>" <shell-quoted remaining gpu_smoke tokens>)': '("$WAVE_TREE/code/common/environment_smoke.py")',
            "(<tokens produced by hydra_override_arg; one per override>)": "(seed=0)",
            "code/<NNN_exp>/<script>.py": "code/000_x/train.py",
        }
        for placeholder, value in values.items():
            self.assertIn(placeholder, body)
            body = body.replace(placeholder, value)
        code = "\n".join(line for line in body.splitlines() if not line.lstrip().startswith("#"))
        self.assertNotRegex(code, r"<[a-z_]+>")
        # placement is an input, never baked in: no rig and no card is spelled in the script
        self.assertNotRegex(code, r'CUDA_VISIBLE_DEVICES="\d')
        return body

    def render_lane_script(self, wave: str) -> str:
        templates = (
            ROOT / "plugins/research/skills/sweep-dispatch/references/templates.md"
        ).read_text()
        start = templates.index("## lane.sh")
        body = templates[templates.index("```bash\n", start) + len("```bash\n"):]
        body = body[: body.index("\n```\n")]
        self.assertIn('WAVE_ID="<wave_id>"', body)
        body = body.replace("<wave_id>", wave)
        code = "\n".join(line for line in body.splitlines() if not line.lstrip().startswith("#"))
        self.assertNotRegex(code, r"<[a-z_]+>")
        return body

    def test_rendered_lane_loop_claims_retracts_waits_and_stops(self) -> None:
        wave = "20260801-000000"
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            tree = root / ".waves" / wave
            tree.mkdir(parents=True)
            (root / "lane.sh").write_text(self.render_lane_script(wave))
            lane = root / ".waves/_state" / wave / "lanes/gpu0"
            for name in ("queue", "running", "finished"):
                (lane / name).mkdir(parents=True)
            (tree / "ok.sh").write_text('echo "$LANE_RIG/$LANE_GPUS/$MIN_FREE_KIB" >> "$PWD/ran"; exit 0\n')
            (tree / "fail.sh").write_text("exit 3\n")
            (tree / "drift.sh").write_text("exit 87\n")
            (tree / "full.sh").write_text('[ -e "$PWD/room" ] && exit 0; : > "$PWD/room"; exit 88\n')
            env = {
                **os.environ, "LANE_RIG": "rig", "LANE_GPUS": "0", "MIN_FREE_KIB": "5",
                "LANE_IDLE_POLL_S": "0", "LANE_STORAGE_WAIT_S": "0",
            }

            def run_lane() -> int:
                return subprocess.run(["bash", "lane.sh"], cwd=root, env=env, capture_output=True, text=True, timeout=30).returncode

            # an entry interrupted by a machine fault goes back to the buffer; an ordinary failure falls through
            (lane / "running/000001__a").write_text("ok.sh")
            (lane / "running/000001__a.pid").write_text("1")
            (lane / "queue/000002__b").write_text("fail.sh")
            (lane / "queue/000003__c").write_text("ok.sh")
            (lane / "drain").write_text("")
            self.assertEqual(run_lane(), 0)
            self.assertEqual(sorted(p.name for p in (lane / "finished").iterdir()), ["000001__a.rc0", "000002__b.rc3", "000003__c.rc0"])
            self.assertEqual((root / "ran").read_text().splitlines(), ["rig/0/5", "rig/0/5"])
            self.assertEqual(list((lane / "running").iterdir()), [])
            # insufficient storage waits for offload when it is on, and costs the lane when it is not
            (lane / "queue/000004__d").write_text("full.sh")
            (root / ".waves/_state" / wave / "offload").write_text("")
            self.assertEqual(run_lane(), 0)
            self.assertTrue((lane / "finished/000004__d.rc0").exists())
            (root / "room").unlink()
            (root / ".waves/_state" / wave / "offload").unlink()
            (lane / "queue/000005__e").write_text("full.sh")
            self.assertEqual(run_lane(), 88)
            # environment drift stops the lane and leaves the rest of the buffer untouched
            (lane / "queue/000006__f").write_text("drift.sh")
            (lane / "queue/000007__g").write_text("ok.sh")
            self.assertEqual(run_lane(), 87)
            self.assertTrue((lane / "queue/000007__g").exists())

    def test_rendered_wave_scripts_of_two_revisions_run_side_by_side(self) -> None:
        def git(*args: str, cwd: Path) -> str:
            return subprocess.run(
                ["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True
            ).stdout.strip()

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "project"
            root.mkdir()
            git("init", "-q", "-b", "main", cwd=root)
            git("config", "user.name", "T", cwd=root)
            git("config", "user.email", "t@example.com", cwd=root)
            (root / ".gitignore").write_text(".waves/\n.envs/\nevaluations/\nlogs/\n__pycache__/\n")
            (root / "code/common").mkdir(parents=True)
            (root / "code/000_x").mkdir()
            (root / "code/common/environment.py").write_text("")
            (root / "code/common/environment_smoke.py").write_text("")
            # The stub interpreter below "runs" train.py by copying its VERSION line into the artifact.
            waves = {"20260801-000000": "v1", "20260802-000000": "v2"}
            env_dir = ".envs/0123456789abcdef"
            for wave, version in waves.items():
                (root / "code/000_x/train.py").write_text(f"VERSION={version}\n")
                script = root / "scripts/000_x/seed=0" / f"wave_{wave}" / "wave.sh"
                script.parent.mkdir(parents=True, exist_ok=True)
                script.write_text(self.render_wave_script(wave, env_dir))
                git("add", ".", cwd=root)
                git("commit", "-qm", f"wave {wave}", cwd=root)
                git("tag", "-a", f"wave--{wave}", "-m", wave, cwd=root)
                git("worktree", "add", "-q", "--detach", str(root / ".waves" / wave), f"wave--{wave}", cwd=root)
            python = root / env_dir / "bin/python"
            python.parent.mkdir(parents=True)
            python.write_text(
                "#!/usr/bin/env bash\n"
                'case "$1" in\n'
                "  *environment.py) echo " + "f" * 64 + ";;\n"
                "  *environment_smoke.py) exit 0;;\n"
                '  *train.py) test "$PWD" = "$RESEARCH_PROJECT_ROOT" || exit 9;\n'
                '     mkdir -p "$(dirname "$ARTIFACT_UNDER_TEST")"; grep VERSION "$1" > "$ARTIFACT_UNDER_TEST";;\n'
                "  -) cat > /dev/null;;\n"
                "esac\n"
            )
            python.chmod(0o755)
            # Move the main checkout past both waves: neither wave may notice.
            (root / "code/000_x/train.py").write_text("VERSION=v3-uncommitted\n")

            def run_wave(wave: str) -> subprocess.CompletedProcess:
                artifact = root / "evaluations/000_x" / wave / "result.json"
                env = {**os.environ, "ARTIFACT_UNDER_TEST": str(artifact), "LANE_RIG": "rig", "LANE_GPUS": "0"}
                env.pop("SLURM_JOB_ID", None)
                env.pop("SLURM_SUBMIT_DIR", None)
                env.pop("RESEARCH_PROJECT_ROOT", None)
                script = f".waves/{wave}/scripts/000_x/seed=0/wave_{wave}/wave.sh"
                return subprocess.run(["bash", script], cwd=root, env=env, capture_output=True, text=True)

            for wave, version in waves.items():
                with self.subTest(wave=wave):
                    result = run_wave(wave)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    artifact = root / "evaluations/000_x" / wave / "result.json"
                    self.assertEqual(artifact.read_text().strip(), f"VERSION={version}")
                    self.assertTrue(list((root / f"logs/000_x/seed=0/wave_{wave}").glob("wave_rig_gpu0-*.log")))
            # An offloaded artifact leaves a receipt, and the receipt alone makes the run skip.
            first = "20260801-000000"
            artifact = root / "evaluations/000_x" / first / "result.json"
            artifact.rename(artifact.with_name("result.json.offloaded.json"))
            skipped = run_wave(first)
            self.assertEqual(skipped.returncode, 0, skipped.stderr)
            self.assertIn("artifact offloaded to the hub", skipped.stdout)
            self.assertFalse(artifact.exists())
            artifact.with_name("result.json.offloaded.json").unlink()
            # A wave script is never started bare: placement comes from the lane.
            bare = subprocess.run(
                ["bash", f".waves/{first}/scripts/000_x/seed=0/wave_{first}/wave.sh"], cwd=root, capture_output=True, text=True,
                env={k: v for k, v in os.environ.items() if k not in ("LANE_RIG", "LANE_GPUS", "SLURM_SUBMIT_DIR")},
            )
            self.assertNotEqual(bare.returncode, 0)
            self.assertIn("LANE_RIG", bare.stderr)
            # Tampering with a wave's worktree is source drift.
            (root / ".waves" / first / "code/000_x/train.py").write_text("VERSION=tampered\n")
            self.assertEqual(run_wave(first).returncode, 86)
            # A missing (pruned) worktree is source drift, not a crash.
            second = "20260802-000000"
            (root / "evaluations/000_x" / second / "result.json").unlink()
            relative = f"scripts/000_x/seed=0/wave_{second}/wave.sh"
            copy = Path(raw) / "copy.sh"
            copy.write_text((root / ".waves" / second / relative).read_text())
            git("worktree", "remove", str(root / ".waves" / second), cwd=root)
            missing = subprocess.run(["bash", str(copy)], cwd=root, capture_output=True, text=True, env={**os.environ, "LANE_RIG": "rig", "LANE_GPUS": "0"})
            self.assertEqual(missing.returncode, 86, missing.stderr)
            self.assertIn("missing wave worktree", missing.stderr)

    def test_paths_template_splits_storage_and_source_roots(self) -> None:
        templates = (
            ROOT / "plugins/research/skills/research-project-init/references/templates.md"
        ).read_text()
        start = templates.index("## code/common/paths.py")
        code = templates[templates.index("```python\n", start) + len("```python\n"):]
        code = code[: code.index("```\n")]
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            shared = base / "project"
            tree = shared / ".waves" / "20260802-120000"
            (shared / ".git").mkdir(parents=True)
            (tree / "code/common").mkdir(parents=True)
            (tree / "pyproject.toml").write_text("[project]\n")
            (tree / "code/common/paths.py").write_text(code)
            spec = importlib.util.spec_from_file_location("paths_under_test", tree / "code/common/paths.py")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            with mock.patch.dict(os.environ, {"RESEARCH_PROJECT_ROOT": str(shared)}):
                self.assertEqual(module.project_path("evaluations"), shared / "evaluations")
                self.assertEqual(module.source_path("code/x.csv"), tree.resolve() / "code/x.csv")
                self.assertEqual(module.storage_path("CACHE_DIR_UNSET_FOR_TEST", "storage/cache"), shared / "storage/cache")
            with mock.patch.dict(os.environ, {"RESEARCH_PROJECT_ROOT": ""}):
                self.assertEqual(module.project_root(), tree.resolve())
            with mock.patch.dict(os.environ, {"RESEARCH_PROJECT_ROOT": str(base)}):
                with self.assertRaises(RuntimeError):
                    module.project_root()
            with self.assertRaises(ValueError):
                module.source_path("/abs")

    def test_behemoth_guard_precedes_python(self) -> None:
        templates = (
            ROOT / "plugins/research/skills/sweep-dispatch/references/templates.md"
        ).read_text()
        guard = templates.index("BEHEMOTH_AUTHORIZED_GPUS")
        python = templates.index('"$ENVIRONMENT_DIR/bin/python" "$WAVE_TREE/code/<NNN_exp>/<script>.py"')
        self.assertLess(guard, python)

    def test_git_revision_guard_precedes_python_and_records_provenance(self) -> None:
        templates = (
            ROOT / "plugins/research/skills/sweep-dispatch/references/templates.md"
        ).read_text()
        status_template = (
            ROOT / "plugins/research/skills/research-project-init/references/templates.md"
        ).read_text()
        source_guard = templates.index('SOURCE_REVISION=$(git -C "$WAVE_TREE" rev-parse')
        python = templates.index('"$ENVIRONMENT_DIR/bin/python" "$WAVE_TREE/code/<NNN_exp>/<script>.py"')
        self.assertLess(source_guard, python)
        self.assertIn(
            '"source_revision": os.environ.get("SOURCE_REVISION")', status_template
        )
        self.assertIn('"source_tag": os.environ.get("SOURCE_TAG")', status_template)
        self.assertIn('"$rc" -eq 86', templates)

    def test_status_writer_template_is_canonical_and_machine_readable(self) -> None:
        project_templates = (
            ROOT / "plugins/research/skills/research-project-init/references/templates.md"
        ).read_text()
        dispatch_templates = (
            ROOT / "plugins/research/skills/sweep-dispatch/references/templates.md"
        ).read_text()

        self.assertEqual(dispatch_templates.count("class StatusWriter"), 0)
        self.assertIn("../../research-project-init/references/templates.md", dispatch_templates)
        self.assertIn('"schema_version": 3', project_templates)
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
                self.assertEqual(started["schema_version"], 3)
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

    def test_run_id_template_resolves_the_config_in_exactly_one_place(self) -> None:
        """One resolver for every recorder of the whole config."""
        block = run_id_template()
        self.assertEqual(block.count("OmegaConf.to_container"), 1)
        self.assertIn("def resolved_config(cfg)", block)
        self.assertNotIn("wandb", block.lower())

    def test_metrics_writer_logs_offline_and_reads_back_last_value_per_step(self) -> None:
        namespace: dict[str, object] = {}
        exec(compile(template_block("code/common/metrics.py"), "metrics.py", "exec"), namespace)
        metrics_writer, read_metrics = namespace["MetricsWriter"], namespace["read_metrics"]

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "rid-x"
            with metrics_writer(run_dir) as writer:
                writer.log({"train/loss": 2.0, "train/acc": 0.1}, step=0)
                writer.log({"train/loss": 1.5}, step=1)
                writer.log({"val/loss": float("nan")}, step=1)
                for bad in ({}, {"step": 1.0}, {"train/ok": True}):
                    with self.assertRaises(ValueError):
                        writer.log(bad, step=2)
                with self.assertRaises(ValueError):
                    writer.log({"train/loss": 1.0}, step=-1)
                with self.assertRaises(ValueError):
                    writer.log({"train/loss": 1.0}, step=1.0)
            first = (run_dir / "metrics.jsonl").read_text().splitlines()
            self.assertEqual(len(first), 3)
            self.assertIsNotNone(datetime.fromisoformat(json.loads(first[0])["time"]).tzinfo)

            # a resumed run appends and repeats step 1: the later value wins
            with metrics_writer(run_dir, resume=True) as writer:
                writer.log({"train/loss": 1.25}, step=1)
                writer.log({"train/loss": 1.0}, step=2)
            with (run_dir / "metrics.jsonl").open("a") as torn:
                torn.write('{"step": 3, "train/lo')
            curves = read_metrics(run_dir)
            self.assertEqual(curves["train/loss"], [(0, 2.0), (1, 1.25), (2, 1.0)])
            self.assertEqual(curves["train/acc"], [(0, 0.1)])
            self.assertTrue(math.isnan(curves["val/loss"][0][1]))

            # a fresh (non-resumed) run starts the log over
            with metrics_writer(run_dir) as writer:
                writer.log({"train/loss": 9.0}, step=0)
            self.assertEqual(read_metrics(run_dir), {"train/loss": [(0, 9.0)]})
            self.assertEqual(read_metrics(Path(tmp) / "missing"), {})

    def test_new_projects_track_metrics_offline_without_wandb(self) -> None:
        templates = PROJECT_TEMPLATES.read_text()
        self.assertNotIn("WANDB_API_KEY", templates)
        self.assertNotIn("wandb_config", templates)
        skill = (ROOT / "plugins/research/skills/experiment-design/SKILL.md").read_text()
        self.assertNotIn("wandb", skill.lower())
        self.assertIn("metrics.jsonl", skill)
        self.assertIn("MetricsWriter(", skill)
        browser = (ROOT / "plugins/research/skills/visualizations/assets/plot-browser.html").read_text()
        self.assertNotIn("wandb", browser.lower())
        identity = (ROOT / "plugins/research/skills/run-identity/SKILL.md").read_text()
        self.assertIn("only when `apply` lists WandB renames", identity)


    def test_dispatch_triggers_on_the_cluster_and_loads_its_reference_lazily(self) -> None:
        skill = (ROOT / "plugins/research/skills/sweep-dispatch/SKILL.md").read_text()
        metadata = (ROOT / "plugins/research/skills/sweep-dispatch/agents/openai.yaml").read_text()
        for word in ("leonardo", "cineca", "slurm", "sbatch"):
            self.assertIn(word, skill.split("\n---", 2)[1].lower(), word)
        self.assertIn("references/cineca-slurm.md", skill)
        self.assertIn("do not load it otherwise", skill)
        self.assertIn("Leonardo", metadata)

    def test_status_schema_docs_match_the_v3_writer(self) -> None:
        conventions = compact_text(SKILLS / "research-project-init/references/conventions.md")
        self.assertIn('{"schema_version": 3,', conventions)
        self.assertIn('"rig": "rig-4090"', conventions)
        dispatch_templates = (SKILLS / "sweep-dispatch/references/templates.md").read_text()
        self.assertIn('s.setdefault("schema_version", 3)', dispatch_templates)
        self.assertIn('rig=os.environ.get("LANE_RIG")', dispatch_templates)
        self.assertIn('"schema_version": 3', template_block("code/common/status.py"))

    def test_run_id_errors_point_at_an_existing_conventions_section(self) -> None:
        """The run_id.py template's collision errors cite conventions.md by section name."""
        cited = set(re.findall(r"See conventions\.md,\s*(?:\"\s*\")?'([^']+)'", run_id_template()))
        self.assertTrue(cited)
        conventions = (SKILLS / "research-project-init/references/conventions.md").read_text()
        for section in cited:
            self.assertIn(f"### {section}", conventions)


def compact_text(path: Path) -> str:
    return " ".join(path.read_text().split())


SKILLS = ROOT / "plugins/research/skills"


# Every distributed research skill file, scripts included, for repo-wide lints.
RESEARCH_TEXT = (".md", ".yaml", ".py")
LEGACY_LAYOUT = re.compile(r"segments-v[12]|collapsed-v1|hashed-v1|RUN_ID_SEGMENTS|`nested`")
LEGACY_REFERENCE = SKILLS / "run-identity/references/legacy-layouts.md"
FENCE = re.compile(r"^```.*?^```", re.MULTILINE | re.DOTALL)

# Byte budgets: slightly above the slimmed sizes, so a file cannot silently regrow.
SIZE_BUDGETS = {
    "environment-sync/SKILL.md": 8_500,
    "experiment-design/SKILL.md": 6_500,
    "experiments-tracking/SKILL.md": 7_500,
    "phone-bridge/SKILL.md": 5_100,
    "research-journal/SKILL.md": 2_500,
    "research-project-init/SKILL.md": 5_500,
    "rig-board/SKILL.md": 13_000,
    "rig-sync/SKILL.md": 8_000,
    "run-identity/SKILL.md": 9_000,
    "sweep-dispatch/SKILL.md": 9_500,
    "sweep-supervisor/SKILL.md": 14_000,
    "visualizations/SKILL.md": 8_000,
    "research-project-init/references/conventions.md": 14_000,
}


class LeanSkillTests(unittest.TestCase):
    """The research skills state facts, taste, and unenforced invariants once, and stay short."""

    def research_files(self, suffixes=RESEARCH_TEXT):
        for path in sorted(SKILLS.rglob("*")):
            if path.is_file() and path.suffix in suffixes and "__pycache__" not in path.parts:
                yield path

    def test_no_research_surface_carries_the_removed_autonomy_regime(self) -> None:
        offenders = []
        for path in self.research_files():
            text = path.read_text(errors="replace")
            for pattern in (r"DECISIONS\.md", r"(?i)execution[- ]agreement", r"\bAlways[ -]ask\b"):
                if re.search(pattern, text):
                    offenders.append(f"{path.relative_to(SKILLS)}: {pattern}")
        self.assertEqual(offenders, [])

    def test_legacy_layout_names_live_only_in_the_legacy_reference(self) -> None:
        self.assertTrue(LEGACY_REFERENCE.is_file())
        self.assertIn("references/legacy-layouts.md", (SKILLS / "run-identity/SKILL.md").read_text())
        offenders = []
        for path in self.research_files((".md", ".yaml")):
            if path == LEGACY_REFERENCE:
                continue
            prose = FENCE.sub("", path.read_text())
            for match in LEGACY_LAYOUT.finditer(prose):
                offenders.append(f"{path.relative_to(SKILLS)}: {match.group(0)}")
        self.assertEqual(offenders, [], "legacy layouts belong in run-identity/references/legacy-layouts.md")

    def test_skill_files_stay_within_their_size_budget(self) -> None:
        skills = {str(path.relative_to(SKILLS)) for path in SKILLS.glob("*/SKILL.md")}
        self.assertLessEqual(skills, set(SIZE_BUDGETS), "give every new research skill a size budget")
        for rel, budget in SIZE_BUDGETS.items():
            with self.subTest(file=rel):
                self.assertLessEqual(len((SKILLS / rel).read_bytes()), budget)


class HashedV2RunIdTemplateTests(unittest.TestCase):
    """The hashed-v2 run_id layout, its identity files, and the status rig field."""

    PARAMS = ["model", "lr", "seed"]

    def setUp(self) -> None:
        self.ns: dict[str, object] = {}
        exec(compile(run_id_template(), "run_id.py", "exec"), self.ns)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.project = Path(tmp.name)
        self.root = self.project / "evaluations" / "001_scale"
        self.root.mkdir(parents=True)
        # the identity files live in the tracked source tree, read via source_path("config/<exp>")
        self.spec = self.project / "config" / "001_scale"
        self.spec.mkdir(parents=True)
        self.write_versions([{"version": 1, "params": self.PARAMS, "date": "2026-10-16", "reason": "first"}])

    def write_versions(self, entries) -> None:
        (self.spec / "RUN_ID_VERSIONS.json").write_text(json.dumps(entries))

    def guard(self, cfg, params=None, run_dir=None, **kwargs) -> Path:
        params = params or self.PARAMS
        run_dir = run_dir or self.root / self.ns["run_id_path"](cfg, params, layout="hashed-v2")
        kwargs.setdefault("spec_root", self.spec)
        self.ns["guard_run_config"](
            cfg, params, run_dir, layout="hashed-v2", experiment_root=self.root, **kwargs
        )
        return run_dir

    def test_hash_is_canonical_order_independent_and_null_for_missing(self) -> None:
        import hashlib

        h = self.ns["run_id_hash_v2"]
        from_values = self.ns["run_id_hash_v2_from_values"]
        cfg = {"model": "vit/base", "lr": 0.001, "seed": 3, "batch": 32}
        value = h(cfg, self.PARAMS)
        canonical = json.dumps(
            {"layout": "hashed-v2", "params": {"model": "vit/base", "lr": 0.001, "seed": 3}},
            sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        )
        self.assertEqual(value, "rid-" + hashlib.sha256(canonical.encode()).hexdigest()[:32])
        self.assertRegex(value, r"^rid-[0-9a-f]{32}$")
        self.assertEqual(h(cfg, list(reversed(self.PARAMS))), value)
        self.assertEqual(h(dict(reversed(list(cfg.items()))), self.PARAMS), value)
        self.assertEqual(from_values({"seed": 3, "lr": 0.001, "model": "vit/base"}), value)
        # a param missing from the config hashes as null
        self.assertEqual(
            h(cfg, [*self.PARAMS, "wd"]),
            from_values({"model": "vit/base", "lr": 0.001, "seed": 3, "wd": None}),
        )
        self.assertNotEqual(h(cfg, [*self.PARAMS, "wd"]), value)
        # a stored .run_config.json hashes the same as the launch-time config
        stored = json.loads(json.dumps(self.ns["resolved_config"](cfg)))
        self.assertEqual(from_values({p: stored.get(p) for p in self.PARAMS}), value)

    def test_path_name_label_and_record(self) -> None:
        cfg = {"model": "mlp", "lr": 0.1, "seed": 0}
        value = self.ns["run_id_hash_v2"](cfg, self.PARAMS)
        self.assertEqual(self.ns["run_id_path"](cfg, self.PARAMS, layout="hashed-v2"), Path(value))
        self.assertEqual(self.ns["run_id_name"](cfg, self.PARAMS, layout="hashed-v2"), value)
        self.assertEqual(self.ns["run_id_label"](cfg, self.PARAMS), "model=mlp,lr=0.1,seed=0")
        record = self.ns["run_id_record"](cfg, self.PARAMS, layout="hashed-v2", version=2)
        self.assertEqual(
            record,
            {"layout": "hashed-v2", "version": 2, "hash": value,
             "params": {"model": "mlp", "lr": 0.1, "seed": 0}},
        )
        with self.assertRaisesRegex(ValueError, "version"):
            self.ns["run_id_record"](cfg, self.PARAMS, layout="hashed-v2")
        # legacy renderings are untouched
        self.assertEqual(
            self.ns["run_id_path"](cfg, self.PARAMS, layout="hashed-v1"),
            Path(self.ns["run_id_hash"](cfg, self.PARAMS)),
        )
        self.assertEqual(len(self.ns["run_id_hash"](cfg, self.PARAMS)), 4 + 16)

    def test_guard_writes_record_and_refuses_mismatches(self) -> None:
        cfg = {"model": "mlp", "lr": 0.1, "seed": 0, "batch": 32}
        run_dir = self.guard(cfg)
        record = json.loads((run_dir / ".run_id.json").read_text())
        self.assertEqual(record["layout"], "hashed-v2")
        self.assertEqual(record["version"], 1)
        self.assertEqual(record["hash"], run_dir.name)
        self.assertEqual(json.loads((run_dir / ".run_config.json").read_text()), cfg)
        self.assertFalse((self.root / "RUN_ID_MAP.json").exists())
        self.guard(cfg)  # resume/retry is idempotent

        with self.assertRaisesRegex(RuntimeError, "is not the hashed-v2 path"):
            self.guard(cfg, run_dir=self.root / "elsewhere")
        with self.assertRaisesRegex(ValueError, "requires experiment_root"):
            self.ns["guard_run_config"](cfg, self.PARAMS, run_dir, layout="hashed-v2")
        with self.assertRaisesRegex(ValueError, "requires spec_root"):
            self.guard(cfg, spec_root=None)
        # evaluations/ holds no identity file: it is never read from there
        self.assertFalse(any(self.root.glob("RUN_ID_*.json")))
        with self.assertRaisesRegex(RuntimeError, "not the current RUN_ID_VERSIONS.json"):
            self.guard(cfg, params=["model", "lr"])
        with self.assertRaisesRegex(RuntimeError, "version 7"):
            self.guard(cfg, version=7)
        # an existing record naming other params is a hash collision
        (run_dir / ".run_id.json").write_text(json.dumps({**record, "params": {"model": "x"}}))
        with self.assertRaisesRegex(RuntimeError, "hashed-v2 hash collision"):
            self.guard(cfg)
        (run_dir / ".run_id.json").write_text(json.dumps(record))
        # a non-identifying difference trips the config diff, pointing at a migration
        with self.assertRaisesRegex(RuntimeError, "(?s)batch.*run_id migration"):
            self.guard({**cfg, "batch": 64})

    def test_map_structure_embeds_configs_and_status(self) -> None:
        a = {"model": "mlp", "lr": 0.1, "seed": 0}
        b = {"model": "mlp", "lr": 0.1, "seed": 1}
        dir_a, dir_b = self.guard(a), self.guard(b)
        (dir_a / ".status.json").write_text(
            json.dumps({"schema_version": 3, "state": "done", "wave_id": "w1", "rig": "rig-a"})
        )
        history = [{"from_version": 0, "to_version": 1, "date": "d", "map": {}, "backfill": {}}]
        (self.spec / "RUN_ID_HISTORY.json").write_text(json.dumps(history))
        with self.assertRaisesRegex(ValueError, "requires spec_root"):
            self.ns["write_run_id_map"](self.root, layout="hashed-v2")
        map_file = self.ns["write_run_id_map"](self.root, layout="hashed-v2", spec_root=self.spec)
        self.assertEqual(map_file, self.root / "RUN_ID_MAP.json")
        run_map = json.loads(map_file.read_text())
        self.assertEqual(
            set(run_map),
            {"layout", "version", "versions", "history", "by_hash", "by_params", "configs"},
        )
        self.assertEqual((run_map["layout"], run_map["version"]), ("hashed-v2", 1))
        # viewers need only the map: it embeds copies of both identity files
        self.assertEqual(run_map["versions"], json.loads((self.spec / "RUN_ID_VERSIONS.json").read_text()))
        self.assertEqual(run_map["history"], history)
        self.assertEqual(
            run_map["by_hash"][dir_a.name],
            {"params": a, "version": 1, "wave_id": "w1", "state": "done", "rig": "rig-a"},
        )
        self.assertEqual(
            run_map["by_hash"][dir_b.name],
            {"params": b, "version": 1, "wave_id": None, "state": None, "rig": None},
        )
        key = json.dumps(a, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        self.assertEqual(run_map["by_params"][key], dir_a.name)
        self.assertEqual(run_map["configs"], {dir_a.name: a, dir_b.name: b})

        # a moved or hand-edited record blocks the map
        record = json.loads((dir_b / ".run_id.json").read_text())
        (dir_b / ".run_id.json").write_text(json.dumps({**record, "params": {**b, "seed": 9}}))
        with self.assertRaisesRegex(RuntimeError, "does not match its params"):
            self.ns["write_run_id_map"](self.root, layout="hashed-v2", spec_root=self.spec)
        moved = self.root / "rid-moved"
        dir_b.rename(moved)
        (moved / ".run_id.json").write_text(json.dumps(record))
        with self.assertRaisesRegex(RuntimeError, "does not match hashed-v2 path"):
            self.ns["write_run_id_map"](self.root, layout="hashed-v2", spec_root=self.spec)

    def test_hashed_v1_map_hard_fails_on_hash_collision(self) -> None:
        params = self.PARAMS
        cfg = {"model": "mlp", "lr": 0.1, "seed": 0}
        root = self.root.parent / "002_legacy"
        first = root / self.ns["run_id_path"](cfg, params, layout="hashed-v1")
        self.ns["guard_run_config"](cfg, params, first, layout="hashed-v1")
        record = json.loads((first / ".run_id.json").read_text())
        clash = root / "rid-copy"
        clash.mkdir()
        (clash / ".run_id.json").write_text(
            json.dumps({**record, "run_id_flat": "model=other", "run_id": {"model": "other"}})
        )
        with self.assertRaisesRegex(RuntimeError, "hashed-v1 hash collision"):
            self.ns["write_run_id_map"](root)
        (clash / ".run_id.json").write_text(json.dumps(record))  # same identity: fine
        self.ns["write_run_id_map"](root)

    def test_hashed_v2_plan_hard_fails_on_hash_collision(self) -> None:
        a = {"model": "mlp", "lr": 0.1, "seed": 0}
        self.guard(a)
        existing = self.ns["run_id_hash_v2"](a, self.PARAMS)
        real = self.ns["run_id_hash_v2_from_values"]
        self.ns["run_id_hash_v2_from_values"] = lambda values: existing
        try:
            with self.assertRaisesRegex(RuntimeError, "run_id hash collision"):
                self.ns["check_run_id_plan"](
                    [{**a, "seed": 5}], self.PARAMS, None, self.root, layout="hashed-v2",
                    spec_root=self.spec,
                )
        finally:
            self.ns["run_id_hash_v2_from_values"] = real

    def test_plan_refuses_config_drift_but_allows_same_config_resume(self) -> None:
        plan = self.ns["check_run_id_plan"]

        def check(*args, **kwargs):
            if kwargs.get("layout") == "hashed-v2":
                kwargs.setdefault("spec_root", self.spec)
            return plan(*args, **kwargs)

        cfg = {"model": "mlp", "lr": 0.1, "seed": 0, "batch": 32}
        run_dir = self.guard(cfg)
        fresh = {**cfg, "seed": 1}
        self.assertEqual(
            check([cfg, fresh], self.PARAMS, experiment_root=self.root, layout="hashed-v2"),
            [Path(run_dir.name), Path(self.ns["run_id_hash_v2"](fresh, self.PARAMS))],
        )
        with self.assertRaisesRegex(RuntimeError, r"(?s)\['batch'\].*run_id migration"):
            check([{**cfg, "batch": 64}], self.PARAMS, None, self.root, layout="hashed-v2")
        with self.assertRaisesRegex(RuntimeError, "planned twice"):
            check([fresh, dict(fresh)], self.PARAMS, None, self.root, layout="hashed-v2")
        with self.assertRaisesRegex(RuntimeError, "not the current RUN_ID_VERSIONS.json"):
            check([fresh], ["model"], None, self.root, layout="hashed-v2")
        with self.assertRaisesRegex(ValueError, "does not apply to hashed-v2"):
            check([fresh], self.PARAMS, [("model",)], self.root, layout="hashed-v2")
        with self.assertRaisesRegex(ValueError, "hashed-v2 or a segments layout"):
            check([fresh], self.PARAMS, None, self.root, layout="nested")
        with self.assertRaisesRegex(ValueError, "requires spec_root"):
            plan([fresh], self.PARAMS, None, self.root, layout="hashed-v2")

        # the same gate under a legacy segments-v2 pin, positional as before
        segments = [("model",), ({"optim": ("lr",)}, "seed")]
        legacy = self.root.parent / "003_segments"
        seg_dir = legacy / self.ns["run_id_path"](cfg, self.PARAMS, layout="segments-v2", segments=segments)
        self.ns["guard_run_config"](
            cfg, self.PARAMS, seg_dir, layout="segments-v2", segments=segments, experiment_root=legacy
        )
        self.assertEqual(len(check([cfg], self.PARAMS, segments, legacy)), 1)
        with self.assertRaisesRegex(RuntimeError, r"(?s)\['batch'\].*new numbered sub-experiment"):
            check([{**cfg, "batch": 64}], self.PARAMS, segments, legacy)

    def test_load_run_id_spec_validates_the_versions_file(self) -> None:
        load = self.ns["load_run_id_spec"]
        self.write_versions([
            {"version": 1, "params": ["model"], "date": "d", "reason": "r"},
            {"version": 2, "params": ["model", "lr"], "date": "d", "reason": "r"},
        ])
        self.assertEqual(load(self.spec), (2, ["model", "lr"]))
        bad = {
            "is empty": [],
            "lacks": [{"version": 1, "params": ["a"]}],
            "positive integer": [{"version": "1", "params": ["a"], "date": "d", "reason": "r"}],
            "does not follow": [
                {"version": 2, "params": ["a"], "date": "d", "reason": "r"},
                {"version": 2, "params": ["b"], "date": "d", "reason": "r"},
            ],
            "non-empty list": [{"version": 1, "params": [], "date": "d", "reason": "r"}],
            "duplicated params": [{"version": 1, "params": ["a", "a"], "date": "d", "reason": "r"}],
            "JSON list": {"version": 1},
            "top-level": [{"version": 1, "params": ["optim.lr"], "date": "d", "reason": "r"}],
        }
        for message, entries in bad.items():
            with self.subTest(message=message):
                self.write_versions(entries)
                with self.assertRaisesRegex(ValueError, message):
                    load(self.spec)
        (self.spec / "RUN_ID_VERSIONS.json").write_text("[{")
        with self.assertRaisesRegex(ValueError, "not valid JSON"):
            load(self.spec)
        (self.spec / "RUN_ID_VERSIONS.json").unlink()
        with self.assertRaisesRegex(FileNotFoundError, "RUN_ID_VERSIONS.json"):
            load(self.spec)

    def test_resolve_run_id_hash_follows_the_history_chain(self) -> None:
        resolve = self.ns["resolve_run_id_hash"]
        self.assertEqual(resolve(self.spec, "rid-a"), "rid-a")  # no history yet
        (self.spec / "RUN_ID_HISTORY.json").write_text(json.dumps([
            {"from_version": 1, "to_version": 2, "date": "d", "map": {"rid-a": "rid-b", "rid-x": "rid-y"}, "backfill": {}},
            {"from_version": 2, "to_version": 3, "date": "d", "map": {"rid-b": "rid-c", "rid-y": "rid-z"},
             "backfill": {"wd": {"value": 0.0, "source": "config default"}}},
        ]))
        self.assertEqual(resolve(self.spec, "rid-a"), "rid-c")
        self.assertEqual(resolve(self.spec, "rid-b"), "rid-c")
        self.assertEqual(resolve(self.spec, "rid-c"), "rid-c")
        self.assertEqual(resolve(self.spec, "rid-x"), "rid-z")
        self.assertEqual(resolve(self.spec, "rid-unknown"), "rid-unknown")
        (self.spec / "RUN_ID_HISTORY.json").write_text(json.dumps([{"from_version": 1}]))
        with self.assertRaisesRegex(ValueError, "'map'"):
            resolve(self.spec, "rid-a")

    def test_guard_on_a_rig_reads_identity_from_the_source_tree_not_evaluations(self) -> None:
        # A non-hub rig: its project root (RESEARCH_PROJECT_ROOT) holds evaluations/ storage with
        # no identity file at all; the wave worktree (source root) carries config/<exp>/.
        rig = self.project / "rig"
        (rig / ".git").mkdir(parents=True)
        (rig / "evaluations" / "001_scale").mkdir(parents=True)
        source = rig / ".waves" / "20260921-093000"
        (source / "config" / "001_scale").mkdir(parents=True)
        (source / "pyproject.toml").write_text("[project]\nname = 'x'\n")
        (source / "config" / "001_scale" / "RUN_ID_VERSIONS.json").write_text(
            json.dumps([{"version": 1, "params": self.PARAMS, "date": "d", "reason": "r"}])
        )
        paths: dict[str, object] = {"__file__": str(source / "code" / "common" / "paths.py")}
        exec(compile(template_block("code/common/paths.py"), "paths.py", "exec"), paths)
        with mock.patch.dict(os.environ, {"RESEARCH_PROJECT_ROOT": str(rig)}):
            spec_root = paths["source_path"]("config/001_scale")
            exp_root = paths["project_path"]("evaluations/001_scale")
            cfg = {"model": "mlp", "lr": 0.1, "seed": 0}
            run_dir = exp_root / self.ns["run_id_path"](cfg, self.PARAMS, layout="hashed-v2")
            self.ns["guard_run_config"](
                cfg, self.PARAMS, run_dir, layout="hashed-v2", experiment_root=exp_root, spec_root=spec_root
            )
        self.assertEqual(spec_root, source / "config" / "001_scale")
        self.assertEqual(exp_root, rig / "evaluations" / "001_scale")
        self.assertEqual(json.loads((run_dir / ".run_id.json").read_text())["version"], 1)
        self.assertEqual(sorted(p.name for p in exp_root.iterdir()), [run_dir.name])

    def test_status_writer_records_the_lane_rig(self) -> None:
        namespace: dict[str, object] = {}
        exec(compile(template_block("code/common/status.py"), "status.py", "exec"), namespace)
        with mock.patch.dict(os.environ, {"LANE_RIG": "rig-4090", "WAVE_ID": "w1"}):
            with namespace["StatusWriter"](self.root / "run") as writer:
                started = json.loads(writer.path.read_text())
        ended = json.loads(writer.path.read_text())
        self.assertEqual(started["schema_version"], 3)
        self.assertEqual((started["rig"], ended["rig"]), ("rig-4090", "rig-4090"))
        self.assertIn("rig", status_provenance_fields())


if __name__ == "__main__":
    unittest.main()
