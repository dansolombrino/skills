from __future__ import annotations

import copy
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "plugins/research/skills"
TEMPLATES = SKILLS / "sweep-dispatch/references/templates.md"
SPEC = importlib.util.spec_from_file_location("render_wave", SKILLS / "sweep-dispatch/scripts/render_wave.py")
assert SPEC and SPEC.loader
render_wave = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = render_wave
SPEC.loader.exec_module(render_wave)

WAVE = "20260802-120000"
RUN_A = "rid-" + "a" * 32
RUN_B = "rid-" + "b" * 32
FINGERPRINT = "f" * 64
ENV_DIR = ".envs/0123456789abcdef"


def run_entry(run_id: str, **extra) -> dict:
    return {
        "id": run_id,
        "label": "lr=0.001,seed=0",
        "checkpoint_dir": f"checkpoints/000_exp/{run_id}",
        "eval_dir": f"evaluations/000_exp/{run_id}",
        "artifact": f"evaluations/000_exp/{run_id}/result.json",
        "overrides": ["seed=0", "'name=a b'"],
        "footprint_kib": 0,
        **extra,
    }


def wave_input(**extra) -> dict:
    return {
        "wave_id": WAVE,
        "experiment": "000_exp",
        "purpose": "first probe",
        "why": "first probe of the grid",
        "hub": "rig-4090",
        "entrypoint": "train.py",
        "environment_dir": ENV_DIR,
        "environment_fingerprint": FINGERPRINT,
        "identity_version": 1,
        "pool": {"rig-4090": {"lanes": ["0"]}, "rig-3090-ti": {"lanes": ["0"]}},
        "weights": {"rig-4090": 1.0, "rig-3090-ti": 0.5},
        "runs": [run_entry(RUN_A), run_entry(RUN_B, cost_s=3600, resumes=True, needs=["hf:x"], min_vram_mib=100)],
        **extra,
    }


class RenderWaveBase(unittest.TestCase):
    def setUp(self) -> None:
        raw = tempfile.mkdtemp(dir="/tmp")
        self.addCleanup(shutil.rmtree, raw, ignore_errors=True)
        self.root = Path(raw) / "project"
        self.root.mkdir()
        (self.root / "sync.toml").write_text(
            'version = 1\n[environment]\nmanager = "uv"\nname = ".venv"\n'
            'gpu_smoke = ["python", "code/common/environment_smoke.py", "--quick mode"]\n'
        )

    def cli(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = render_wave.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def render(self, wave: dict, *flags: str) -> tuple[int, str, str]:
        path = self.root.parent / "input.json"
        path.write_text(json.dumps(wave))
        return self.cli("scripts", "--input", str(path), "--root", str(self.root), *flags)

    def script(self, run_id: str = RUN_A) -> Path:
        return self.root / "scripts/000_exp" / run_id / f"wave_{WAVE}" / "wave.sh"


class RenderedFilesTests(RenderWaveBase):
    def test_renders_every_file_with_no_placeholder_left_and_valid_bash(self) -> None:
        code, out, err = self.render(wave_input())
        self.assertEqual(code, 0, err)
        lane = self.root / "scripts/000_exp/_lanes" / f"wave_{WAVE}" / "lane.sh"
        scripts = [self.script(RUN_A), self.script(RUN_B), lane]
        for script in scripts:
            text = script.read_text()
            self.assertNotRegex(text, r"<[A-Za-z_\"][^<>\n]*>", script)
            subprocess.run(["bash", "-n", str(script)], check=True)
        text = self.script().read_text()
        self.assertIn(f'WAVE_ID="{WAVE}"', text)
        self.assertIn(f'RUN_ID_NAME="{RUN_A}"', text)
        self.assertIn(f'ENVIRONMENT_DIR="{ENV_DIR}"', text)
        self.assertIn(f'EXPECTED_ENVIRONMENT_FINGERPRINT="{FINGERPRINT}"', text)
        self.assertIn("# RUN_ID_VERSION: 1", text)
        self.assertNotIn("RUN_ID_SEGMENTS", text)
        self.assertIn("""ENVIRONMENT_SMOKE=("$WAVE_TREE/code/common/environment_smoke.py" '--quick mode')""", text)
        self.assertIn("HYDRA_ARGS=(seed=0 'name=a b')", text)
        self.assertIn('"$WAVE_TREE/code/000_exp/train.py"', text)
        # nothing else names a placement or the shared rig's guard
        self.assertNotIn("BEHEMOTH", text)
        self.assertIn(f'WAVE_ID="{WAVE}"', lane.read_text())
        self.assertEqual(out.count("[write]"), 5)

    def test_dry_run_writes_nothing(self) -> None:
        code, out, _err = self.render(wave_input(), "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("[dry-run]", out)
        self.assertFalse((self.root / "scripts").exists())

    def test_rendering_again_is_a_no_op_but_a_changed_wave_is_refused(self) -> None:
        self.assertEqual(self.render(wave_input())[0], 0)
        self.assertEqual(self.render(wave_input())[0], 0)
        code, _out, err = self.render(wave_input(why="a different reason"))
        self.assertEqual(code, 2)
        self.assertIn("refusing to overwrite", err)

    def test_behemoth_block_appears_only_with_behemoth_and_quotes_the_grant(self) -> None:
        wave = wave_input(
            pool={"rig-4090": {"lanes": ["0"]}, "behemoth": {"lanes": ["0", "5"]}},
            gpu_grants={"behemoth": {"gpus": [5], "user_words": "use gpu 5 today", "date": "2026-08-02"}},
        )
        self.assertEqual(self.render(wave)[0], 0)
        text = self.script().read_text()
        self.assertIn('BEHEMOTH_AUTHORIZED_GPUS="0,5"', text)
        self.assertIn(f'# GPU auth: "user-granted 2026-08-02 for wave {WAVE}: gpu 5" (use gpu 5 today)', text)
        readme = self.script().with_name("README.md").read_text()
        self.assertIn('"use gpu 5 today"', readme)
        self.assertIn("offload: behemoth", readme)

    def test_a_label_cannot_break_out_of_its_quotes(self) -> None:
        wave = wave_input(runs=[run_entry(RUN_A, label='a"$(touch pwned)`x`')])
        self.assertEqual(self.render(wave)[0], 0)
        result = subprocess.run(
            ["bash", "-c", 'eval "$(grep ^RUN_ID_LABEL= "$0")"; printf %s "$RUN_ID_LABEL"', str(self.script())],
            capture_output=True, text=True, cwd=self.root, check=True,
        )
        self.assertEqual(result.stdout, 'a"$(touch pwned)`x`')
        self.assertFalse((self.root / "pwned").exists())

    def test_invalid_input_is_refused_by_name(self) -> None:
        cases = {
            "wave id": dict(wave_id="latest"),
            "environment_fingerprint": dict(environment_fingerprint="abc"),
            "environment_dir": dict(environment_dir=".venv"),
            "rid-<32 hex>": dict(runs=[run_entry("seed0")]),
            "stay inside": dict(runs=[run_entry(RUN_A, artifact="../result.json")]),
            "under checkpoint_dir": dict(runs=[run_entry(RUN_A, artifact="elsewhere/result.json")]),
            "hydra_override_arg": dict(runs=[run_entry(RUN_A, overrides=["a=$(reboot)"])]),
            "footprint_kib": dict(runs=[run_entry(RUN_A, footprint_kib=None)]),
            "duplicate": dict(runs=[run_entry(RUN_A), run_entry(RUN_A)]),
            "one non-empty line": dict(why="two\nlines"),
            "must list GPU indices": dict(gpu_grants={"behemoth": {"gpus": ["5"], "user_words": "ok", "date": "d"}}),
            "user_words": dict(gpu_grants={"behemoth": {"gpus": [5], "date": "d"}}),
        }
        for message, change in cases.items():
            with self.subTest(message):
                code, _out, err = self.render(wave_input(**change))
                self.assertEqual(code, 2)
                self.assertIn(message, err)
        self.assertFalse((self.root / "scripts").exists())

    def test_a_legacy_pin_keeps_its_own_folder_and_segments(self) -> None:
        run = run_entry("lr-0.001/seed-0", folder="lr-0.001/seed-0")
        run.update(
            checkpoint_dir="checkpoints/000_exp/lr-0.001/seed-0",
            eval_dir="evaluations/000_exp/lr-0.001/seed-0",
            artifact="evaluations/000_exp/lr-0.001/seed-0/result.json",
        )
        wave = wave_input(run_id_layout="segments-v2", run_id_segments="lr/seed", runs=[run])
        del wave["identity_version"]
        code, _out, err = self.render(wave)
        self.assertEqual(code, 0, err)
        text = self.script("lr-0.001/seed-0").read_text()
        self.assertIn('RUN_ID_PATH_LAYOUT="segments-v2"', text)
        self.assertIn("# RUN_ID_SEGMENTS: lr/seed", text)
        self.assertNotIn("# RUN_ID_VERSION", text)

    def test_the_template_is_the_source_a_new_placeholder_stops_the_render(self) -> None:
        original = render_wave._fence

        def with_new_placeholder(path, heading):
            return original(path, heading) + 'NEW="<something new>"\n'

        render_wave._fence = with_new_placeholder
        self.addCleanup(setattr, render_wave, "_fence", original)
        code, _out, err = self.render(wave_input())
        self.assertEqual(code, 2)
        self.assertIn("<something new>", err)

    def test_readme_standing_authorization_matches_the_template(self) -> None:
        flat = " ".join(TEMPLATES.read_text().split())
        self.assertIn(" ".join(render_wave.STANDING_AUTHORIZATION.split()), flat)


class SlurmVariantTests(RenderWaveBase):
    def wave(self) -> dict:
        return wave_input(
            pool={"rig-4090": {"lanes": ["0"]}, "leonardo": {"max_jobs": 8, "default_walltime_s": 7300}},
            weights={"rig-4090": 1.0, "leonardo": 1.0},
            slurm_account="ACCOUNT_1",
        )

    def test_a_cluster_in_the_pool_adds_the_job_header_to_one_shared_file(self) -> None:
        code, _out, err = self.render(self.wave())
        self.assertEqual(code, 0, err)
        text = self.script().read_text()
        self.assertTrue(text.startswith("#!/usr/bin/env bash\n#SBATCH --job-name="))
        self.assertIn(f"#SBATCH --job-name={WAVE}__{RUN_A}", text)
        self.assertIn("#SBATCH --account=ACCOUNT_1", text)
        self.assertIn("#SBATCH --time=02:15:00", text)
        self.assertIn(f"#SBATCH --output=logs/000_exp/{RUN_A}/wave_{WAVE}/slurm-%j.out", text)
        self.assertEqual(text.count("set -uo pipefail"), 1)
        self.assertEqual(text.count('export WAVE_ID="'), 1)
        self.assertNotIn("...then", text)
        self.assertNotRegex(text, r"<[A-Za-z_\"][^<>\n]*>")
        subprocess.run(["bash", "-n", str(self.script())], check=True)
        readme = self.script().with_name("README.md").read_text()
        self.assertIn("account `ACCOUNT_1`", readme)
        self.assertIn("boost_usr_prod", readme)
        self.assertIn("core-hours", readme)
        self.assertNotIn("leonardo", readme.split("offload:")[1].split(".")[0])

    def test_a_cluster_needs_an_account(self) -> None:
        wave = self.wave()
        del wave["slurm_account"]
        code, _out, err = self.render(wave)
        self.assertEqual(code, 2)
        self.assertIn("slurm_account", err)

    def test_walltime_rounds_up_to_fifteen_minutes_and_caps_at_a_day(self) -> None:
        self.assertEqual(render_wave.slurm_time(3600), "01:00:00")
        self.assertEqual(render_wave.slurm_time(3601), "01:15:00")
        self.assertEqual(render_wave.slurm_time(10**6), "24:00:00")


class RenderedScriptRunsTests(RenderWaveBase):
    """Run what the renderer wrote, in a real tagged worktree with a fake environment."""

    def git(self, *args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(self.root), *args], check=True, capture_output=True, text=True
        ).stdout.strip()

    def build(self, wave: dict, fingerprint: str = FINGERPRINT) -> None:
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.com")
        (self.root / ".gitignore").write_text(".waves/\n.envs/\nevaluations/\ncheckpoints/\nlogs/\nshitpads/\n")
        for name in ("code/common/environment.py", "code/common/environment_smoke.py", "code/000_exp/train.py"):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# fake\n")
        (self.root / "uv.lock").write_text("version = 1\n")
        code, _out, err = self.render(wave)
        self.assertEqual(code, 0, err)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "dispatch")
        self.git("tag", "-a", f"wave--{WAVE}", "-m", "wave")
        self.git("worktree", "add", "-q", "--detach", f".waves/{WAVE}", f"wave--{WAVE}")
        python = self.root / ENV_DIR / "bin/python"
        python.parent.mkdir(parents=True)
        python.write_text(
            "#!/bin/sh\n"
            'case "$1" in\n'
            f'  *environment.py) echo {fingerprint} ;;\n'
            '  *environment_smoke.py) [ "$2" = "--quick mode" ] ;;\n'
            '  *train.py) mkdir -p "$(dirname "$ARTIFACT_FOR_TEST")"; printf "%s\\n" "$@" > "$ARTIFACT_FOR_TEST" ;;\n'
            "  *) exit 9 ;;\n"
            "esac\n"
        )
        python.chmod(0o755)

    def launch(self, run_id: str = RUN_A) -> subprocess.CompletedProcess:
        env = {
            **os.environ, "LANE_RIG": "rig-4090", "LANE_GPUS": "0", "MIN_FREE_KIB": "0", "QUOTA_FS": "",
            "ARTIFACT_FOR_TEST": str(self.root / f"evaluations/000_exp/{run_id}/result.json"),
        }
        script = f".waves/{WAVE}/scripts/000_exp/{run_id}/wave_{WAVE}/wave.sh"
        return subprocess.run(["bash", script], cwd=self.root, env=env, capture_output=True, text=True, timeout=60)

    def test_it_runs_the_entrypoint_then_skips_itself(self) -> None:
        self.build(wave_input())
        result = self.launch()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        artifact = self.root / f"evaluations/000_exp/{RUN_A}/result.json"
        self.assertEqual(artifact.read_text().splitlines()[1:], ["seed=0", "name=a b"])
        again = self.launch()
        self.assertEqual(again.returncode, 0)
        self.assertIn("[skip]", again.stdout)

    def test_a_script_with_the_job_header_still_runs_on_a_rig_lane(self) -> None:
        wave = wave_input(
            pool={"rig-4090": {"lanes": ["0"]}, "leonardo": {"max_jobs": 8, "default_walltime_s": 7200}},
            weights={"rig-4090": 1.0, "leonardo": 1.0},
            slurm_account="ACCOUNT_1",
        )
        self.build(wave)
        result = self.launch()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

    def test_an_environment_with_another_fingerprint_stops_the_lane(self) -> None:
        self.build(wave_input(), fingerprint="e" * 64)
        result = self.launch()
        self.assertEqual(result.returncode, 87, result.stderr)
        self.assertIn("[environment-drift]", result.stderr)

    def test_source_drift_stops_the_lane(self) -> None:
        self.build(wave_input())
        (self.root / ".waves" / WAVE / "code/000_exp/train.py").write_text("# changed\n")
        result = self.launch()
        self.assertEqual(result.returncode, 86, result.stderr)


class ManifestTests(RenderWaveBase):
    def tag(self) -> str:
        for args in (
            ["init", "-q", "-b", "main"],
            ["config", "user.name", "Test"],
            ["config", "user.email", "test@example.com"],
            ["commit", "-q", "--allow-empty", "-m", "dispatch"],
            ["tag", "-a", f"wave--{WAVE}", "-m", "wave"],
        ):
            subprocess.run(["git", "-C", str(self.root), *args], check=True)
        return subprocess.run(
            ["git", "-C", str(self.root), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
        ).stdout.strip()

    def write(self, wave: dict, *flags: str) -> tuple[int, str, str]:
        path = self.root.parent / "input.json"
        path.write_text(json.dumps(wave))
        return self.cli("manifest", "--input", str(path), "--root", str(self.root), *flags)

    def manifest(self) -> dict:
        return json.loads((self.root / f"shitpads/wave_{WAVE}/manifest.json").read_text())

    def test_manifest_waits_for_the_tag(self) -> None:
        code, _out, err = self.write(wave_input())
        self.assertEqual(code, 2)
        self.assertIn("commit and tag", err)

    def test_manifest_takes_the_commit_from_the_tag_and_orders_longest_first(self) -> None:
        revision = self.tag()
        code, _out, err = self.write(wave_input())
        self.assertEqual(code, 0, err)
        manifest = self.manifest()
        self.assertEqual(manifest["revision"], revision)
        self.assertEqual(manifest["lane_script"], f"scripts/000_exp/_lanes/wave_{WAVE}/lane.sh")
        self.assertEqual([run["id"] for run in manifest["runs"]], [RUN_B, RUN_A])
        self.assertEqual(manifest["offload"], ["rig-3090-ti"])
        first = manifest["runs"][0]
        self.assertEqual(first["status_path"], f"evaluations/000_exp/{RUN_B}/.status.json")
        self.assertEqual(first["script"], f"scripts/000_exp/{RUN_B}/wave_{WAVE}/wave.sh")
        self.assertEqual(first["cost_s"], 3600)
        self.assertNotIn("cost_s", manifest["runs"][1])
        self.assertNotIn("gpu_grants", manifest)

    def test_manifest_never_lands_under_scripts(self) -> None:
        self.tag()
        code, _out, err = self.write(wave_input(), "--output", str(self.root / "scripts/manifest.json"))
        self.assertEqual(code, 2)
        self.assertIn("never under scripts/", err)

    def test_the_supervisor_accepts_a_rendered_manifest(self) -> None:
        spec = importlib.util.spec_from_file_location(
            "dispatch_supervisor", SKILLS / "sweep-supervisor/scripts/supervisor.py"
        )
        supervisor = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = supervisor
        spec.loader.exec_module(supervisor)
        rigsync = supervisor.rigsync
        machines = {
            name: rigsync.Machine(name, name, None, Path("/tmp") / name, name == "rig-4090")
            for name in ("rig-4090", "rig-3090-ti")
        }
        config = rigsync.Config(self.root, {}, machines)
        settings = SimpleNamespace(agent=SimpleNamespace(model="m", effort="e"), registry_path=Path("/tmp/r"))
        revision = self.tag()
        self.assertEqual(self.write(wave_input())[0], 0)
        queue = supervisor.build_queue(self.manifest(), self.root, config, settings)
        self.assertEqual(queue["revision"], revision)
        self.assertEqual([run["id"] for run in queue["runs"]], [RUN_B, RUN_A])
        self.assertIsNone(queue["runs"][1]["cost_s"])


class RenderInputDocTests(unittest.TestCase):
    def test_every_input_field_the_template_lists_is_one_the_renderer_uses(self) -> None:
        text = TEMPLATES.read_text()
        block = text[text.index("## render_wave.py"):]
        block = block[block.index("```json\n") + 8:]
        fields = set(re.findall(r'"([a-z_]+)":', block[:block.index("```")]))
        source = (SKILLS / "sweep-dispatch/scripts/render_wave.py").read_text()
        for field in fields - {"lanes"}:
            self.assertIn(f'"{field}"', source, field)


if __name__ == "__main__":
    unittest.main()
