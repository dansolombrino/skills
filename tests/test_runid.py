from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "plugins/research/skills/run-identity/scripts/runid.py"
TEMPLATES = ROOT / "plugins/research/skills/research-project-init/references/templates.md"
SPEC = importlib.util.spec_from_file_location("runid", SCRIPT)
assert SPEC and SPEC.loader
runid = importlib.util.module_from_spec(SPEC)
sys.modules["runid"] = runid
SPEC.loader.exec_module(runid)
agent = runid.agent

EXP = "000_exp"
WAVE = "20260921-093000"
HASH_RE = re.compile(r"rid-[0-9a-f]{32}")


def run_id_template() -> dict:
    """Exec the project template's code/common/run_id.py and return its namespace."""
    match = re.search(r"## code/common/run_id\.py\n\n```python\n(.*?)\n```", TEMPLATES.read_text(), re.DOTALL)
    assert match is not None
    namespace: dict = {"__name__": "run_id_template"}
    exec(compile(match.group(1), "run_id.py", "exec"), namespace)
    return namespace


def write(path: Path, data: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, bytes):
        path.write_bytes(data)
    else:
        path.write_text(data)


def tree(root: Path) -> dict[str, str]:
    """Every file under root (but migration bookkeeping and the board's lock file) -> sha256 of its bytes."""
    out = {}
    for directory, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d != agent.MIGRATIONS_DIR]
        for name in files:
            if name == ".lock":
                continue
            path = Path(directory) / name
            out[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def local_remote(machine, argv, *, check=True, input_bytes=None):
    """Every rig is a directory on this host: the program runid pipes really runs."""
    return runid.rigsync.run(argv, check=check, input_bytes=input_bytes)


class RunIdTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.hub = self.base / "hub" / "proj"
        self.rig = self.base / "rig" / "proj"
        self.board = self.base / "board"
        for path in (self.hub, self.rig, self.board):
            path.mkdir(parents=True)
        self.registry = self.base / "machines.toml"
        write(self.registry, f"""
[board]
root = "{self.board}"
rigs = ["hub", "rig"]
[machines.hub]
ssh = "hub"
hostname = "{socket.gethostname()}"
[machines.rig]
ssh = "rig"
""")
        write(self.hub / "sync.toml", f"""
[machines.hub]
repo_path = "{self.hub}"
[machines.rig]
repo_path = "{self.rig}"
[artifacts.checkpoints]
path = "checkpoints"
[artifacts.evaluations]
path = "evaluations"
[environment]
name = "venv"
""")
        self.collect_rc = 0
        self.collect_calls: list[str] = []
        patches = [
            mock.patch.object(runid.rigsync, "remote", side_effect=local_remote),
            mock.patch.object(runid, "run_collect_check", side_effect=self.fake_collect_check),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.build_fixture()

    def fake_collect_check(self, ctx, wave_id):
        self.collect_calls.append(wave_id)
        out = f"OK rig wave {wave_id}\n" if self.collect_rc == 0 else f"FAIL rig wave {wave_id}: missing on the hub: x\n"
        return subprocess.CompletedProcess([], self.collect_rc, out, "")

    # ---------------------------------------------------------------- fixture

    def make_run(self, root: Path, values: dict, config: dict, rig: str) -> str:
        run_hash = agent.hash_from_values(values)
        run_dir = root / "evaluations" / EXP / run_hash
        write(run_dir / ".run_id.json", agent.record_bytes({"layout": "hashed-v2", "version": 1, "hash": run_hash, "params": values}))
        write(run_dir / ".run_config.json", agent.config_bytes(config))
        status = {
            "schema_version": 3, "state": "done", "wave_id": WAVE, "rig": rig,
            "source_revision": "b" * 40, "source_tag": f"wave--{WAVE}", "eval_dir": f"evaluations/{EXP}/{run_hash}",
        }
        write(run_dir / ".status.json", json.dumps(status))
        write(run_dir / "metrics.json", json.dumps({"loss": 0.1}))
        return run_hash

    def build_fixture(self) -> None:
        exp_root = self.hub / "evaluations" / EXP
        self.versions = [{"version": 1, "params": ["model", "seed"], "date": "2026-09-01", "reason": "grid"}]
        # the identity files are tracked source on the hub (config/<exp>/), never in evaluations/
        write(self.spec_root / "RUN_ID_VERSIONS.json", json.dumps(self.versions, indent=2) + "\n")
        self.runs: dict[tuple, str] = {}
        for model in ("mlp", "cnn"):
            for seed in (0, 1):
                config = {"model": model, "seed": seed, "wandb": {"entity": "me", "project": "proj"}}
                if model == "cnn":
                    config["lr"] = 0.0001  # cnn configs already record lr; mlp ran with a hardcoded value
                rig = "rig" if model == "mlp" else "hub"
                run_hash = self.make_run(self.hub, {"model": model, "seed": seed}, config, rig)
                self.runs[(model, seed)] = run_hash
                if rig == "rig":  # the rig holds the same records (reconciled) plus its checkpoints
                    for name in (".run_id.json", ".run_config.json", ".status.json", "metrics.json"):
                        write(self.rig / "evaluations" / EXP / run_hash / name, (exp_root / run_hash / name).read_bytes())
                    write(self.rig / "checkpoints" / EXP / run_hash / "model.pt", b"\x00\x01binary" + run_hash.encode())
                    write(self.rig / "checkpoints" / EXP / run_hash / "model.pt.offloaded.json", json.dumps({"path": f"checkpoints/{EXP}/{run_hash}/model.pt"}))
                    write(self.rig / "logs" / EXP / run_hash / f"wave_{WAVE}" / "wave_rig_gpu0.log", f"run {run_hash} started\n")
                else:
                    write(self.hub / "checkpoints" / EXP / run_hash / "model.pt", b"\x00weights")
                script_dir = self.hub / "scripts" / EXP / run_hash / f"wave_{WAVE}"
                write(script_dir / "wave.sh", f"RUN_ID={run_hash}\nEVAL=evaluations/{EXP}/{run_hash}\n")
                write(script_dir / "README.md", f"# run {run_hash}\n")
        mlp0, cnn1 = self.runs[("mlp", 0)], self.runs[("cnn", 1)]
        write(self.rig / "checkpoints" / EXP / f"{mlp0}.superseded-20260901-000000" / "old.pt", b"\x00old")
        write(self.hub / "scripts" / EXP / "_lanes" / f"wave_{WAVE}" / "leonardo.jobs", f"123 {WAVE}__{cnn1}\n")
        all_hashes = list(self.runs.values())
        write(self.hub / "EXPERIMENTS.md", "| run_id | state |\n" + "".join(f"| {h} | done |\n" for h in all_hashes))
        write(self.hub / "JOURNAL.md", f"## 2026-09-21\nLaunched {mlp0} and friends.\n")
        state = self.hub / ".waves" / "_state" / WAVE
        queue = {
            "schema_version": 1, "experiment": EXP, "wave_id": WAVE, "finished_at": "2026-09-22T00:00:00+00:00",
            "pool": {"hub": {"kind": "lanes", "lanes": ["0"]}, "rig": {"kind": "lanes", "lanes": ["0"]}},
            "runs": [{"id": h, "eval_dir": f"evaluations/{EXP}/{h}", "status_path": f"evaluations/{EXP}/{h}/.status.json"} for h in all_hashes],
        }
        write(state / "queue.json", json.dumps(queue, indent=2))
        write(state / "ledger.jsonl", "".join(json.dumps({"event": "assign", "run": h, "rig": "rig"}) + "\n" for h in all_hashes))
        for name in ("snapshot.json", "table.md", "questions.json"):
            write(state / name, f"{{\"mention\": \"{mlp0}\"}}\n")
        write(self.rig / ".waves" / "_state" / WAVE / "lanes" / "gpu0" / "finished" / "0001", f"{mlp0}\n")
        write(self.hub / "plots" / EXP / "plot_loss" / "loss.html", f"<html>{cnn1}</html>")
        write(self.hub / "plots" / EXP / "plot_loss" / "other.html", "<html>nothing</html>")
        write(self.board / "history.jsonl", json.dumps({"event": "claim", "active_run": mlp0}) + "\n")
        agent.write_atomic(exp_root / "RUN_ID_MAP.json", agent.map_bytes(agent.build_index(exp_root, self.versions, [])))
        write(self.base / "backfill.json", json.dumps({"lr": {"value": 0.001, "source": "code/000_exp/train.py:41@" + "b" * 7}}))

    # ---------------------------------------------------------------- helpers

    def cli(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        base = ["--project", str(self.hub), "--registry", str(self.registry)]
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = runid.main(base + list(argv))
        return code, out.getvalue(), err.getvalue()

    def plan(self, *extra: str, backfill: bool = True) -> tuple[int, str, str]:
        argv = ["--experiment", EXP, "migrate", "plan", "--add", "lr", "--reason", "sweep adds lr", "--out", str(self.plan_path)]
        if backfill:
            argv += ["--backfill-file", str(self.base / "backfill.json")]
        return self.cli(*argv, *extra)

    @property
    def spec_root(self) -> Path:
        return self.hub / "config" / EXP

    @property
    def plan_path(self) -> Path:
        return self.base / "plan" / "plan.json"

    def apply(self, *flags: str) -> tuple[int, str, str]:
        return self.cli("--experiment", EXP, "migrate", "apply", "--plan", str(self.plan_path), *(flags or ("--confirm",)))

    def snapshot(self) -> tuple[dict, dict, dict]:
        return tree(self.hub), tree(self.rig), tree(self.board)

    def old_hash_texts(self, root: Path) -> list[str]:
        old = set(self.runs.values())
        found = []
        for directory, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d != agent.MIGRATIONS_DIR]
            for name in files:
                path = Path(directory) / name
                if name in ("RUN_ID_HISTORY.json", "RUN_ID_MAP.json"):
                    continue  # the history (and the map's embedded copy) maps old -> new by design
                rel = path.relative_to(root).as_posix()
                if any(h in rel for h in old):
                    found.append(rel)
                    continue
                data = path.read_bytes()
                if b"\x00" not in data and any(h.encode() in data for h in old):
                    found.append(rel)
        return found

    # ---------------------------------------------------------------- parity

    def test_hash_label_and_index_match_the_template(self) -> None:
        template = run_id_template()
        samples = [
            {"model": "mlp", "seed": 0},
            {"seed": 0, "model": "mlp"},
            {"lr": 0.0001, "layers": [64, 128], "opt": None, "name": "naïve", "flag": True, "nested": {"b": 1, "a": 2}},
        ]
        for values in samples:
            self.assertEqual(agent.hash_from_values(values), template["run_id_hash_v2_from_values"](values))
            params = list(values)
            self.assertEqual(agent.label(values, params), template["run_id_label"](values, params))
            self.assertEqual(agent.canonical_json(values), template["_canonical_json"](values))
        exp_root = self.hub / "evaluations" / EXP
        self.assertEqual(
            agent.build_index(exp_root, self.versions, []),
            template["build_run_id_index"](exp_root, layout="hashed-v2", spec_root=self.spec_root),
        )
        written = template["write_run_id_map"](exp_root, layout="hashed-v2", spec_root=self.spec_root).read_bytes()
        self.assertEqual(written, agent.map_bytes(agent.build_index(exp_root, self.versions, [])))
        record = template["run_id_record"]({"model": "mlp", "seed": 0}, ["model", "seed"], layout="hashed-v2", version=1)
        self.assertEqual(agent.record_bytes(record), (exp_root / self.runs[("mlp", 0)] / ".run_id.json").read_bytes())

    def test_param_glossary_returns_entries_and_names_every_missing_param(self) -> None:
        load = run_id_template()["load_param_glossary"]
        root = self.base / "glossary"
        root.mkdir()
        with self.assertRaisesRegex(FileNotFoundError, "PARAM_GLOSSARY.json is missing"):
            load(root, ["lr"])
        entries = {
            "lr": {"name": "Learning rate", "explanation": "Step size of each update."},
            "seed": {"name": "Random seed", "explanation": "Initialization and data-order seed."},
        }
        write(root / "PARAM_GLOSSARY.json", json.dumps(entries))
        self.assertEqual(load(root, ["seed", "lr"]), {"seed": entries["seed"], "lr": entries["lr"]})
        with self.assertRaisesRegex(ValueError, r"no entry for \['model', 'wd'\]"):
            load(root, ["lr", "model", "wd"])
        write(root / "PARAM_GLOSSARY.json", json.dumps({"lr": {"name": "Learning rate", "explanation": " "}}))
        with self.assertRaisesRegex(ValueError, "non-empty 'name' and 'explanation'"):
            load(root, ["lr"])
        write(root / "PARAM_GLOSSARY.json", json.dumps([entries]))
        with self.assertRaisesRegex(ValueError, "JSON object keyed by param"):
            load(root, ["lr"])

    # ---------------------------------------------------------------- lookup

    def test_lookup_resolves_a_unique_prefix_and_refuses_an_ambiguous_one(self) -> None:
        target = self.runs[("cnn", 1)]
        others = [h for h in self.runs.values() if h != target]
        length = next(n for n in range(5, 36) if not any(h.startswith(target[:n]) for h in others))
        code, out, _ = self.cli("--experiment", EXP, "lookup", target[4:length], "--json")
        self.assertEqual(code, 0)
        result = json.loads(out)
        self.assertEqual(result["hash"], target)
        self.assertEqual(result["params"], {"model": "cnn", "seed": 1})
        self.assertEqual((result["wave_id"], result["state"], result["rig"]), (WAVE, "done", "hub"))
        code, _, err = self.cli("--experiment", EXP, "lookup", "rid-")
        self.assertEqual(code, 2)
        self.assertIn("ambiguous", err)
        code, _, err = self.cli("lookup", "ffffffff")  # no --experiment: every hashed-v2 experiment
        self.assertEqual(code, 1)  # no match is 1 for both lookup forms; errors are 2
        self.assertIn("no run_id matches", err)

    def test_lookup_where_normalizes_numbers_and_reads_config_keys(self) -> None:
        code, out, _ = self.cli("--experiment", EXP, "lookup", "--where", "lr=1e-4", "--json")
        self.assertEqual(code, 0)
        self.assertEqual({row["hash"] for row in json.loads(out)}, {self.runs[("cnn", 0)], self.runs[("cnn", 1)]})
        code, out, _ = self.cli("--experiment", EXP, "lookup", "--where", "model=mlp", "seed=1.0", "--json")
        self.assertEqual([row["hash"] for row in json.loads(out)], [self.runs[("mlp", 1)]])
        code, out, _ = self.cli("--experiment", EXP, "lookup", "--where", "wandb.project=proj", "--json")
        self.assertEqual(len(json.loads(out)), 4)
        code, _, _ = self.cli("--experiment", EXP, "lookup", "--where", "model=rnn")
        self.assertEqual(code, 1)

    def test_lookup_follows_history_from_an_old_hash(self) -> None:
        self.assertEqual(self.plan()[0], 0)
        self.assertEqual(self.apply()[0], 0)
        old = self.runs[("mlp", 0)]
        code, out, _ = self.cli("--experiment", EXP, "lookup", old[:14], "--json")
        self.assertEqual(code, 0)
        result = json.loads(out)
        plan = json.loads(self.plan_path.read_text())
        self.assertEqual(result["resolved_from"], old)
        self.assertEqual(result["hash"], plan["map"][old])
        self.assertEqual(result["chain"], [old, plan["map"][old]])
        self.assertEqual(result["params"], {"model": "mlp", "seed": 0, "lr": 0.001})
        self.assertEqual(result["version"], 2)
        # without the tracked identity files (e.g. a checkout of another branch), the map's embedded
        # history still resolves the old hash
        for name in ("RUN_ID_VERSIONS.json", "RUN_ID_HISTORY.json"):
            (self.spec_root / name).unlink()
        code, out, _ = self.cli("lookup", old, "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["hash"], plan["map"][old])

    # ---------------------------------------------------------------- plan

    def test_plan_reports_runs_needing_backfill_with_their_revision(self) -> None:
        code, out, err = self.plan(backfill=False)
        self.assertEqual(code, 2)
        self.assertIn("2 run(s) still need backfill", err)
        for seed in (0, 1):
            self.assertIn(self.runs[("mlp", seed)], out)
        for seed in (0, 1):
            self.assertNotIn(self.runs[("cnn", seed)], out)
        self.assertIn("b" * 40, out)
        self.assertIn(f"wave--{WAVE}", out)
        self.assertFalse(self.plan_path.exists())

    def test_plan_applies_backfill_defaults_and_per_run_overrides_with_sources(self) -> None:
        mlp1 = self.runs[("mlp", 1)]
        write(self.base / "backfill.json", json.dumps({"lr": {
            "value": 0.001, "source": "code/000_exp/train.py:41@bbbbbbb",
            "runs": {mlp1: {"value": 0.01, "source": "code/000_exp/train.py:44@bbbbbbb"}},
        }}))
        code, out, err = self.plan()
        self.assertEqual(code, 0, err)
        plan = json.loads(self.plan_path.read_text())
        self.assertEqual((plan["from_version"], plan["to_version"]), (1, 2))
        self.assertEqual(plan["params_after"], ["model", "seed", "lr"])
        self.assertEqual(plan["runs"][self.runs[("mlp", 0)]]["backfilled"], {"lr": {"value": 0.001, "source": "code/000_exp/train.py:41@bbbbbbb"}})
        self.assertEqual(plan["runs"][mlp1]["params_after"]["lr"], 0.01)
        self.assertEqual(plan["runs"][self.runs[("cnn", 0)]]["backfilled"], {})
        self.assertEqual(plan["history_entry"]["backfill"]["lr"]["runs"][mlp1]["source"], "code/000_exp/train.py:44@bbbbbbb")
        self.assertEqual(plan["map"][mlp1], agent.hash_from_values({"model": "mlp", "seed": 1, "lr": 0.01}))
        self.assertIn("backfill lr=0.01  source code/000_exp/train.py:44@bbbbbbb", out)
        self.assertEqual(plan["plots"], [f"plots/{EXP}/plot_loss/loss.html"])
        self.assertEqual(plan["wandb"]["paths"], ["me/proj"])
        script = (self.plan_path.parent / "wandb_rename.py").read_text()
        compile(script, "wandb_rename.py", "exec")
        self.assertIn("venv/bin/python wandb_rename.py", script)  # [environment].name of sync.toml
        self.assertNotIn("uv run", script)
        self.assertIn("by_old_hash", script)  # matched by the old-hash tag, renamed to the new label
        self.assertEqual(set(plan["machines"]), {"hub", "rig"})
        self.assertEqual(self.collect_calls, [WAVE])

    def test_plan_rejects_a_dotted_param(self) -> None:
        code, _, err = self.cli("--experiment", EXP, "migrate", "plan", "--add", "optim.lr", "--reason", "r", "--out", str(self.plan_path))
        self.assertEqual(code, 2)
        self.assertIn("top-level config keys", err)
        self.assertFalse(self.plan_path.exists())

    def test_plan_rejects_an_absent_source_with_a_value(self) -> None:
        write(self.base / "backfill.json", json.dumps({"lr": {"value": 0.1, "source": "absent"}}))
        code, _, err = self.plan()
        self.assertEqual(code, 2)
        self.assertIn("absent", err)

    def test_plan_refuses_a_collision(self) -> None:
        code, _, err = self.cli("--experiment", EXP, "migrate", "plan", "--remove", "seed", "--reason", "drop seed", "--out", str(self.plan_path))
        self.assertEqual(code, 2)
        self.assertIn("collision", err)
        self.assertFalse(self.plan_path.exists())

    def test_plan_refuses_a_live_wave(self) -> None:
        queue_path = self.hub / ".waves" / "_state" / WAVE / "queue.json"
        queue = json.loads(queue_path.read_text())
        queue["finished_at"] = None
        queue_path.write_text(json.dumps(queue))
        code, _, err = self.plan()
        self.assertEqual(code, 2)
        self.assertIn("still live", err)

    def test_plan_refuses_unreconciled_evaluations(self) -> None:
        self.collect_rc = 1
        code, _, err = self.plan()
        self.assertEqual(code, 2)
        self.assertIn("not reconciled", err)
        self.assertIn("missing on the hub", err)

    def test_plan_refuses_a_legacy_experiment(self) -> None:
        (self.spec_root / "RUN_ID_VERSIONS.json").unlink()
        code, _, err = self.plan()
        self.assertEqual(code, 2)
        self.assertIn("hashed-v2 only", err)

    def test_plan_refuses_a_rig_record_that_differs_from_the_hub(self) -> None:
        mlp0 = self.runs[("mlp", 0)]
        path = self.rig / "evaluations" / EXP / mlp0 / ".run_config.json"
        path.write_text(path.read_text().replace("mlp", "mlp "))
        code, _, err = self.plan()
        self.assertEqual(code, 2)
        self.assertIn("differs from the hub", err)

    # ---------------------------------------------------------------- apply

    def test_apply_renames_and_rewrites_every_inventoried_kind_on_hub_and_rig(self) -> None:
        self.assertEqual(self.plan()[0], 0)
        plan = json.loads(self.plan_path.read_text())
        mapping = plan["map"]
        code, out, err = self.apply()
        self.assertEqual(code, 0, err)
        self.assertIn("every machine's index equals the planned map", out)
        self.assertIn("venv/bin/python", out)
        self.assertIn("wandb_rename.py", out)
        self.assertIn("update EXPERIMENTS.md", out)
        self.assertIn("label model=mlp,seed=0,lr=0.001  backfilled lr=0.001", out)
        mlp0, cnn1 = self.runs[("mlp", 0)], self.runs[("cnn", 1)]
        # renames: hub and rig, every area, quarantine dir too
        for old, new in mapping.items():
            self.assertFalse((self.hub / "evaluations" / EXP / old).exists())
            self.assertTrue((self.hub / "evaluations" / EXP / new).is_dir())
            self.assertTrue((self.hub / "scripts" / EXP / new).is_dir())
        self.assertTrue((self.rig / "checkpoints" / EXP / mapping[mlp0] / "model.pt").is_file())
        self.assertTrue((self.rig / "checkpoints" / EXP / f"{mapping[mlp0]}.superseded-20260901-000000").is_dir())
        self.assertTrue((self.rig / "logs" / EXP / mapping[mlp0]).is_dir())
        self.assertTrue((self.hub / "checkpoints" / EXP / mapping[cnn1]).is_dir())
        # no text file anywhere still names an old hash (binary checkpoints are left alone)
        self.assertEqual(self.old_hash_texts(self.hub), [f"plots/{EXP}/plot_loss/loss.html"])
        self.assertEqual(self.old_hash_texts(self.rig), [])
        self.assertIn(mapping[mlp0], (self.board / "history.jsonl").read_text())
        self.assertIn(mapping[cnn1], (self.hub / "scripts" / EXP / "_lanes" / f"wave_{WAVE}" / "leonardo.jobs").read_text())
        self.assertIn(mapping[mlp0], (self.rig / ".waves" / "_state" / WAVE / "lanes" / "gpu0" / "finished" / "0001").read_text())
        self.assertIn(mapping[mlp0], (self.rig / "checkpoints" / EXP / mapping[mlp0] / "model.pt.offloaded.json").read_text())
        # structural records
        for root in (self.hub, self.rig):
            record = json.loads((root / "evaluations" / EXP / mapping[mlp0] / ".run_id.json").read_text())
            self.assertEqual(record, {"layout": "hashed-v2", "version": 2, "hash": mapping[mlp0], "params": {"model": "mlp", "seed": 0, "lr": 0.001}})
            config = json.loads((root / "evaluations" / EXP / mapping[mlp0] / ".run_config.json").read_text())
            self.assertEqual(config["lr"], 0.001)
        # identity files: tracked config/<exp>/ on the hub only; the rig gets them with the next deploy
        exp_root = self.hub / "evaluations" / EXP
        versions = json.loads((self.spec_root / "RUN_ID_VERSIONS.json").read_text())
        self.assertEqual([v["version"] for v in versions], [1, 2])
        self.assertEqual(versions[-1]["params"], ["model", "seed", "lr"])
        history = json.loads((self.spec_root / "RUN_ID_HISTORY.json").read_text())
        self.assertEqual(history[-1]["map"], mapping)
        self.assertEqual(history[-1]["backfill"]["lr"]["source"], "code/000_exp/train.py:41@bbbbbbb")
        for root in (self.hub, self.rig):
            self.assertEqual(list((root / "evaluations" / EXP).glob("RUN_ID_VERSIONS.json")), [])
            self.assertEqual(list((root / "evaluations" / EXP).glob("RUN_ID_HISTORY.json")), [])
        self.assertFalse((self.rig / "config").exists())
        index = json.loads((exp_root / "RUN_ID_MAP.json").read_text())
        self.assertEqual(index["version"], 2)
        self.assertEqual(set(index["by_hash"]), set(mapping.values()))
        self.assertEqual((index["versions"], index["history"]), (versions, history))
        self.assertEqual(index, run_id_template()["build_run_id_index"](exp_root, layout="hashed-v2", spec_root=self.spec_root))
        # follow-ups for EXPERIMENTS.md, next to the rollback manifest
        (ts,) = [d for d in (exp_root / agent.MIGRATIONS_DIR).iterdir() if d.is_dir()]
        todo = json.loads((ts / "followups.json").read_text())
        self.assertEqual(todo["params_after"], ["model", "seed", "lr"])
        by_new = {run["new_hash"]: run for run in todo["runs"]}
        self.assertEqual(by_new[mapping[mlp0]]["label_after"], "model=mlp,seed=0,lr=0.001")
        self.assertEqual(by_new[mapping[mlp0]]["backfilled"]["lr"]["value"], 0.001)
        self.assertIn("run_id version:", todo["experiments_md"])

    def test_apply_refuses_a_stale_plan_and_changes_nothing(self) -> None:
        self.assertEqual(self.plan()[0], 0)
        with (self.hub / "JOURNAL.md").open("a") as handle:
            handle.write(f"Also {self.runs[('cnn', 0)]}.\n")
        before = self.snapshot()
        code, _, err = self.apply()
        self.assertEqual(code, 2)
        self.assertIn("stale", err)
        self.assertIn("JOURNAL.md changed", err)
        self.assertEqual(self.snapshot(), before)

    def test_apply_twice_is_refused(self) -> None:
        self.assertEqual(self.plan()[0], 0)
        self.assertEqual(self.apply()[0], 0)
        code, _, err = self.apply()
        self.assertEqual(code, 2)
        self.assertIn("already applied", err)

    def test_apply_needs_an_explicit_mode_and_dry_run_changes_nothing(self) -> None:
        self.assertEqual(self.plan()[0], 0)
        before = self.snapshot()
        code, _, err = self.cli("--experiment", EXP, "migrate", "apply", "--plan", str(self.plan_path))
        self.assertEqual(code, 2)
        code, out, _ = self.apply("--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("the plan is current", out)
        self.assertEqual(self.snapshot(), before)

    # ---------------------------------------------------------------- rollback

    def test_rollback_restores_byte_identical_trees(self) -> None:
        before = self.snapshot()
        self.assertEqual(self.plan()[0], 0)
        self.assertEqual(self.apply()[0], 0)
        self.assertNotEqual(self.snapshot(), before)
        (ts,) = [p.name for p in (self.hub / "evaluations" / EXP / agent.MIGRATIONS_DIR).iterdir() if p.is_dir()]
        code, _, err = self.cli("--experiment", EXP, "migrate", "rollback", ts, "--confirm")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.snapshot(), before)
        manifest = json.loads((self.hub / "evaluations" / EXP / agent.MIGRATIONS_DIR / ts / "rollback.json").read_text())
        self.assertEqual(manifest["status"], "rolled-back")
        code, _, err = self.cli("--experiment", EXP, "migrate", "rollback", ts, "--confirm")
        self.assertEqual(code, 2)

    def test_a_failure_mid_apply_rolls_everything_back(self) -> None:
        before = self.snapshot()
        self.assertEqual(self.plan()[0], 0)
        real = runid.machine_call

        def failing(machine, op, payload):
            if op == "apply" and machine.name == "hub":  # the rig was applied first; the hub fails half way
                payload = {**payload, "fail_after_files": True}
            return real(machine, op, payload)

        with mock.patch.object(runid, "machine_call", side_effect=failing):
            code, _, err = self.apply()
        self.assertEqual(code, 2)
        self.assertIn("rolled back", err)
        self.assertEqual(self.snapshot(), before)
        (ts,) = [p.name for p in (self.hub / "evaluations" / EXP / agent.MIGRATIONS_DIR).iterdir() if p.is_dir()]
        manifest = json.loads((self.hub / "evaluations" / EXP / agent.MIGRATIONS_DIR / ts / "rollback.json").read_text())
        self.assertEqual(manifest["status"], "failed-rolled-back")
        self.assertEqual(manifest["machines"]["rig"]["state"], "rolled-back")


if __name__ == "__main__":
    unittest.main()
