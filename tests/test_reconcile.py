"""experiments-tracking's reconcile.py: what it proposes, what it flags, and that --confirm stays narrow."""

from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "plugins/research/skills/experiments-tracking/scripts/reconcile.py"
SPEC = importlib.util.spec_from_file_location("reconcile_under_test", SCRIPT)
assert SPEC and SPEC.loader
reconcile = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = reconcile
SPEC.loader.exec_module(reconcile)

WAVE = "20261016-101500"
LATER = "20261017-101500"
FINGERPRINT = "f" * 64
OTHER = "e" * 64
RID_A = "rid-" + "a" * 32
RID_B = "rid-" + "b" * 32
RID_OLD = "rid-" + "0" * 32
SECTION = "000_x"
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def git(root: Path, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True, env={**env, **GIT_ENV}).stdout.strip()


class Fixture(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve() / "proj"
        self.root.mkdir()
        git(self.root, "init", "-q", "-b", "main")
        git(self.root, "commit", "-q", "--allow-empty", "-m", "c")
        git(self.root, "tag", "-a", f"wave--{WAVE}", "-m", "w")
        self.revision = git(self.root, "rev-parse", "HEAD")
        self.now = datetime.now().astimezone().replace(microsecond=0)
        spec = self.root / "config" / SECTION
        spec.mkdir(parents=True)
        (spec / "RUN_ID_VERSIONS.json").write_text(json.dumps([{"version": 1, "params": ["lr", "seed"]}]))
        code = self.root / "code" / SECTION
        code.mkdir(parents=True)
        (code / "train.py").write_text('RUN_ID_PATH_LAYOUT = "hashed-v2"\n')
        self.rows: list[str] = []
        self.readme(RID_A)
        self.readme(RID_B)

    def readme(self, rid: str, wave: str = WAVE, fingerprint: str = FINGERPRINT) -> None:
        folder = self.root / "scripts" / SECTION / rid / f"wave_{wave}"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "README.md").write_text(f"# wave\n\nEnvironment fingerprint: `{fingerprint}` (verified)\n")

    def row(self, rid: str = RID_A, status: str = "inpr", wave: str = WAVE, rig: str = "hub", gpu: str = "0", lr: str = "1e-3",
            label: str | None = None, started: str = "10-16 10:15", progress: str = "", eta: str = "", ended: str = "", elapsed: str = "", notes: str = "") -> str:
        label = label if label is not None else f"lr={lr},seed=0"
        return f"| {lr} | 0 | {rid} | {label} | {wave} | {rig} | {gpu} | {status} | {started} | {progress} | {eta} | {ended} | {elapsed} | {notes} |"

    def write(self, *rows: str, version_line: str = "run_id version: 1 — lr, seed   (mirrors config/000_x/RUN_ID_VERSIONS.json)") -> Path:
        text = "\n".join([
            "# Experiments", "", f"## {SECTION}",
            f"run_id path layout: hashed-v2   (mirrors RUN_ID_PATH_LAYOUT in code/{SECTION}/train.py)",
            version_line,
            f"run_id map: evaluations/{SECTION}/RUN_ID_MAP.json", "",
            "| lr | seed | run_id | label | wave | rig | gpu | status | started | progress | eta | ended | elapsed | notes |",
            "|----|------|--------|-------|------|-----|-----|--------|---------|----------|-----|-------|---------|-------|",
            *rows, "", "Prose after the table stays exactly as it is.", "",
        ])
        path = self.root / "EXPERIMENTS.md"
        path.write_text(text)
        return path

    def status(self, rid: str = RID_A, state: str = "done", wave: str = WAVE, **fields) -> None:
        folder = self.root / "evaluations" / SECTION / rid
        folder.mkdir(parents=True, exist_ok=True)
        ended = self.now.isoformat() if state in ("done", "failed") else None
        record = {
            "state": state, "started": (self.now - timedelta(hours=1)).isoformat(), "ended": ended, "elapsed_s": 3600,
            "heartbeat": self.now.isoformat(), "progress": "epoch 5/10", "progress_completed": 5, "progress_total": 10, "progress_unit": "epoch",
            "wave_id": wave, "rig": "hub", "gpu": "0", "source_revision": self.revision, "source_tag": f"wave--{wave}",
            "environment_fingerprint": FINGERPRINT, **fields,
        }
        (folder / ".status.json").write_text(json.dumps(record))

    def queue(self, wave: str = WAVE, finished: bool = False, runs: list[dict] | None = None, **fields) -> None:
        state = self.root / ".waves" / "_state" / wave
        state.mkdir(parents=True, exist_ok=True)
        record = {
            "schema_version": 1, "wave_id": wave, "revision": self.revision, "weights": {"hub": 1.0, "peer": 0.5},
            "finished_at": self.now.isoformat() if finished else None, "runs": runs or [], **fields,
        }
        (state / "queue.json").write_text(json.dumps(record))

    def queue_run(self, rid: str = RID_A, state: str = "running", rig: str = "hub", **fields) -> dict:
        artifact = f"evaluations/{SECTION}/{rid}/final.json"
        return {
            "id": rid, "state": state, "lane": {"rig": rig, "gpu": "0"}, "cost_s": None, "note": None,
            "script": f"scripts/{SECTION}/{rid}/wave_{WAVE}/wave.sh", "status_path": f"evaluations/{SECTION}/{rid}/.status.json",
            "artifact": artifact, "eval_dir": f"evaluations/{SECTION}/{rid}", "observed": {}, **fields,
        }

    def artifact(self, rid: str = RID_A, receipt: bool = False) -> None:
        folder = self.root / "evaluations" / SECTION / rid
        folder.mkdir(parents=True, exist_ok=True)
        (folder / ("final.json.offloaded.json" if receipt else "final.json")).write_text("{}")

    def run_cli(self, *args: str, env: dict | None = None) -> tuple[int, dict, str]:
        out, err = io.StringIO(), io.StringIO()
        clean = {k: v for k, v in os.environ.items() if k != "SWEEP_SUPERVISOR_MODEL"}
        with mock.patch.dict(os.environ, {**clean, **(env or {})}, clear=True), redirect_stdout(out), redirect_stderr(err):
            code = reconcile.main(["--root", str(self.root), "--json", *args])
        return code, json.loads(out.getvalue()) if out.getvalue() else {}, err.getvalue()

    def kinds(self, report: dict) -> list[str]:
        return [item["kind"] for item in report["integrity_errors"] + report["judgment"]]

    def changes(self, report: dict, index: int = 0) -> dict:
        return {column: change["to"] for column, change in report["proposed"][index]["changes"].items()}


class ProposalTests(Fixture):
    def test_a_valid_done_status_with_its_artifact_flips_and_fills(self) -> None:
        self.write(self.row(eta="10-16 12:00"))
        self.status()
        self.queue(runs=[self.queue_run(state="done")], finished=True)
        self.artifact()
        code, report, _ = self.run_cli()
        self.assertEqual(code, 0)
        self.assertEqual(self.changes(report), {
            "status": "done", "ended": self.now.strftime("%m-%d %H:%M"), "elapsed": "1h", "eta": "", "progress": "epoch 5/10",
        })

    def test_an_offload_receipt_counts_as_the_artifact(self) -> None:
        self.write(self.row())
        self.status()
        self.queue(runs=[self.queue_run(state="assigned", lane={"rig": "hub", "gpu": "0"})], finished=True)
        self.artifact(receipt=True)
        # the supervisor's queue still says assigned, so a terminal flip is not unambiguous
        self.assertIn("queue-disagrees", self.kinds(self.run_cli()[1]))
        self.queue(runs=[self.queue_run(state="done", artifact="gone/elsewhere.json")], finished=True)
        self.assertEqual(self.changes(self.run_cli()[1])["status"], "done")

    def test_failed_status_flips_to_failed(self) -> None:
        self.write(self.row())
        self.status(state="failed")
        self.queue(runs=[self.queue_run(state="failed")], finished=True)
        code, report, _ = self.run_cli()
        self.assertEqual((code, self.changes(report)["status"]), (0, "failed"))

    def test_a_running_status_updates_progress_and_a_structured_eta(self) -> None:
        self.write(self.row(status="todo", rig="", gpu="", started=""))
        self.status(state="running")
        self.queue(finished=True, runs=[self.queue_run()])
        code, report, _ = self.run_cli()
        self.assertEqual(code, 0)
        change = self.changes(report)
        self.assertEqual((change["status"], change["rig"], change["gpu"], change["progress"]), ("inpr", "hub", "0", "epoch 5/10"))
        self.assertEqual(report["etas"][0]["basis"], "structured progress")
        self.assertEqual(report["etas"][0]["remaining_s"], 3600)

    def test_a_supervisor_assignment_fills_rig_and_gpu_only(self) -> None:
        self.write(self.row(status="todo", rig="", gpu="", started=""))
        self.queue(runs=[self.queue_run(state="assigned", rig="peer")], finished=True)
        self.assertEqual(self.changes(self.run_cli()[1]), {"rig": "peer", "gpu": "0"})

    def test_superseded_rows_are_skipped(self) -> None:
        self.write(self.row(status="superseded", notes="replaced by x"))
        self.status(state="failed")
        code, report, _ = self.run_cli()
        self.assertEqual((code, report["proposed"]), (0, []))


class FlagTests(Fixture):
    def assert_flagged_and_left(self, kind: str) -> None:
        code, report, _ = self.run_cli()
        self.assertEqual(code, 1)
        self.assertIn(kind, self.kinds(report))
        self.assertNotIn("status", {column for item in report["proposed"] for column in item["changes"]}, report["proposed"])

    def test_provenance_mismatches_are_integrity_errors(self) -> None:
        for field, value in (("source_tag", "wave--other"), ("source_revision", "0" * 40), ("environment_fingerprint", OTHER)):
            with self.subTest(field=field):
                self.write(self.row())
                self.status(**{field: value})
                self.assert_flagged_and_left("provenance")

    def test_done_without_the_artifact_is_flagged_not_marked_done(self) -> None:
        self.write(self.row())
        self.status()
        self.queue(runs=[self.queue_run(state="failed")], finished=True)
        self.assert_flagged_and_left("artifact-missing")

    def test_done_with_no_record_of_the_artifact_is_flagged(self) -> None:
        self.write(self.row())
        self.status()
        self.assert_flagged_and_left("artifact-unverified")

    def test_a_frozen_running_is_judgment_and_loses_its_eta(self) -> None:
        self.write(self.row(eta="10-16 12:00"))
        self.status(state="running", heartbeat=(self.now - timedelta(minutes=30)).isoformat())
        code, report, _ = self.run_cli()
        self.assertEqual((code, self.kinds(report)), (1, ["frozen-running"]))
        self.assertEqual(self.changes(report), {"eta": ""})
        self.assertEqual(report["etas"][0]["unavailable"], "heartbeat older than three minutes")

    def test_a_stale_local_copy_defers_to_the_supervisors_fresher_read(self) -> None:
        self.write(self.row(rig="peer"))
        self.status(state="running", rig="peer", heartbeat=(self.now - timedelta(minutes=30)).isoformat())
        fresh = {"state": "running", "heartbeat": self.now.isoformat(), "elapsed_s": 100, "progress_completed": 1, "progress_total": 2, "progress": "1/2"}
        self.queue(runs=[self.queue_run(rig="peer", observed=fresh)], finished=True)
        code, report, _ = self.run_cli()
        self.assertEqual(code, 0)
        self.assertEqual(report["etas"][0]["remaining_s"], 100)

    def test_a_moved_run_is_flagged_unless_its_notes_say_so(self) -> None:
        self.write(self.row(rig="peer"))
        self.status(state="running")
        self.assert_flagged_and_left("moved-run")
        self.write(self.row(rig="peer", notes="moved to hub"))
        self.assertEqual(self.run_cli()[0], 0)

    def test_a_status_of_a_later_wave_is_not_this_rows(self) -> None:
        self.write(self.row())
        self.status(state="running", wave=LATER)
        self.assert_flagged_and_left("status-other-wave")

    def test_the_supervisor_saying_done_without_a_local_status_is_flagged(self) -> None:
        self.write(self.row())
        self.queue(runs=[self.queue_run(state="done")], finished=True)
        self.assert_flagged_and_left("no-local-status")

    def test_an_evaluation_directory_gone_is_judgment(self) -> None:
        self.write(self.row())
        self.assert_flagged_and_left("evaluation-gone")

    def test_a_terminal_row_disagreeing_with_its_status_is_flagged(self) -> None:
        self.write(self.row(status="done"))
        self.status(state="failed")
        self.assert_flagged_and_left("terminal-disagrees")

    def test_a_broken_row_is_flagged_and_the_rest_still_read(self) -> None:
        self.write(self.row() + " extra |", self.row(rid=RID_B, status="todo"))
        code, report, _ = self.run_cli()
        self.assertEqual(code, 1)
        self.assertEqual(self.kinds(report), ["row-shape"])

    def test_row_vocabulary_and_duplicates(self) -> None:
        self.write(self.row(status="maybe"), self.row(rid="not-a-hash", status="todo"), self.row(rid=RID_B, status="todo"), self.row(rid=RID_B, status="todo"))
        self.assertEqual(sorted(self.kinds(self.run_cli()[1])), ["bad-run-id", "bad-status", "duplicate-row"])

    def test_a_wave_with_no_trace_is_not_recoverable(self) -> None:
        self.write(self.row(status="done", wave=LATER))
        self.assertIn("not-recoverable", self.kinds(self.run_cli()[1]))


class IdentityTests(Fixture):
    def test_the_header_is_checked_against_what_it_mirrors(self) -> None:
        self.write(self.row(status="todo"), version_line="run_id version: 2 — lr, seed   (mirrors config/000_x/RUN_ID_VERSIONS.json)")
        self.assertEqual(self.kinds(self.run_cli()[1]), ["header-mirror"])
        (self.root / "code" / SECTION / "train.py").write_text('RUN_ID_PATH_LAYOUT = "flat"\n')
        self.write(self.row(status="todo"))
        self.assertEqual(self.kinds(self.run_cli()[1]), ["header-mirror"])

    def test_param_columns_must_be_the_current_identity(self) -> None:
        (self.root / "config" / SECTION / "RUN_ID_VERSIONS.json").write_text(json.dumps([{"version": 1, "params": ["lr", "seed", "wd"]}]))
        self.write(self.row(status="todo"), version_line="run_id version: 1 — lr, seed, wd   (mirrors x)")
        errors = self.run_cli()[1]["integrity_errors"]
        self.assertEqual([e["kind"] for e in errors], ["header-mirror"])
        self.assertIn("are not the current identity params", errors[0]["msg"])

    def test_rows_are_checked_against_the_map_and_history(self) -> None:
        evaluations = self.root / "evaluations" / SECTION
        evaluations.mkdir(parents=True)
        (evaluations / "RUN_ID_MAP.json").write_text(json.dumps({"by_hash": {RID_A: {"params": {"lr": 0.001, "seed": 0}}, RID_B: {"params": {"lr": 0.1, "seed": 0}}}}))
        (self.root / "config" / SECTION / "RUN_ID_HISTORY.json").write_text(json.dumps([{"map": {RID_OLD: RID_A}}]))
        self.write(self.row(status="superseded"), self.row(rid=RID_B, status="superseded"), self.row(rid=RID_OLD, status="superseded"), self.row(rid="rid-" + "c" * 32, status="superseded"))
        self.assertEqual(sorted(self.kinds(self.run_cli()[1])), ["params-mismatch", "stale-hash", "unknown-hash"])  # 1e-3 == 0.001 passes

    def test_label_must_agree_with_the_columns(self) -> None:
        self.write(self.row(status="todo", label="lr=1e-3,seed=7"), self.row(rid=RID_B, status="todo", label="whatever"), self.row(rid=RID_A, wave=LATER, status="todo", label="lr=0.001,seed=0"))
        self.assertEqual(self.kinds(self.run_cli()[1]), ["label-mismatch", "label-mismatch"])


class EtaTests(Fixture):
    def eta_of(self, *rows: str, **queue_fields) -> dict:
        self.write(*rows)
        self.status(state="running", progress_completed=None, progress_total=None, progress=None, elapsed_s=600)
        if queue_fields:
            self.queue(**queue_fields)
        return self.run_cli()[1]["etas"][0]

    def test_it_uses_the_supervisors_estimate_for_a_recorded_run(self) -> None:
        done = {**self.queue_run(rid=RID_B, state="done"), "observed": {"elapsed_s": 3000}}
        eta = self.eta_of(self.row(), runs=[self.queue_run(), done], finished=True)
        self.assertEqual((eta["remaining_s"], eta["basis"]), (2400, "measured this wave"))

    def test_with_no_record_this_waves_rows_are_the_measurement(self) -> None:
        eta = self.eta_of(self.row(), self.row(rid=RID_B, status="done", elapsed="1h"))
        self.assertEqual((eta["remaining_s"], eta["basis"]), (3000, "measured this wave"))

    def test_then_the_same_run_on_the_same_rig(self) -> None:
        eta = self.eta_of(self.row(), self.row(status="done", wave=LATER, elapsed="1h"))
        self.assertEqual((eta["remaining_s"], eta["basis"]), (3000, "exact-run history"))

    def test_then_the_same_run_elsewhere_and_the_experiment_median_weight_normalized(self) -> None:
        self.queue(wave=LATER, finished=True)  # the record that holds the weights: peer is half as fast
        eta = self.eta_of(self.row(), self.row(status="done", wave=LATER, rig="peer", elapsed="2h"))
        self.assertEqual((eta["remaining_s"], eta["basis"]), (3000, "exact-run history (weight-normalized)"))
        eta = self.eta_of(self.row(), self.row(rid=RID_B, status="done", wave=LATER, rig="peer", elapsed="2h"))
        self.assertEqual((eta["remaining_s"], eta["basis"]), (3000, "experiment median (weight-normalized)"))

    def test_rigs_are_never_pooled_unnormalized(self) -> None:
        eta = self.eta_of(self.row(), self.row(rid=RID_B, status="done", wave=LATER, rig="peer", elapsed="2h"))
        self.assertEqual(eta["unavailable"], "no basis yet")

    def test_invalid_bounds_make_it_unavailable(self) -> None:
        self.write(self.row())
        self.status(state="running", progress_completed=12, progress_total=10)
        self.assertEqual(self.run_cli()[1]["etas"][0]["unavailable"], "invalid progress bounds")

    def test_a_live_waves_eta_is_the_supervisors_view(self) -> None:
        self.write(self.row())
        self.status(state="running")
        self.queue(runs=[self.queue_run()])
        moment = (self.now + timedelta(hours=3)).isoformat()
        snapshot = {"counts": {"done": 0}, "total": 1, "wave_eta": moment, "wave_eta_basis": "feeder simulation", "finished_at": None,
                    "lanes": [], "rigs": {}, "failed": [], "questions": [], "certs": {}}
        (self.root / ".waves" / "_state" / WAVE / "snapshot.json").write_text(json.dumps(snapshot))
        wave = self.run_cli()[1]["waves"][WAVE]
        self.assertEqual((wave["kind"], wave["at"], wave["source"], wave["live"]), ("eta", moment, "supervisor", True))

    def test_an_unsupervised_waves_eta_needs_every_lane(self) -> None:
        self.write(self.row(), self.row(rid=RID_B, status="todo"))
        self.status(state="running")
        wave = self.run_cli()[1]["waves"][WAVE]
        self.assertEqual(wave["kind"], "unavailable")
        self.assertIn("1 todo rows have no lane", wave["basis"])


class ConfirmTests(Fixture):
    def ready(self, **queue_fields) -> Path:
        path = self.write(self.row(eta="10-16 12:00", notes="a \\| b"), self.row(rid=RID_B, status="todo", rig="", gpu="", started=""))
        self.status()
        self.artifact()
        self.queue(runs=[self.queue_run(state="done")], **queue_fields)
        return path

    def test_the_default_writes_nothing(self) -> None:
        path = self.ready(finished=True)
        before = path.read_text()
        self.assertTrue(self.run_cli()[1]["proposed"])
        self.assertTrue(self.run_cli("--dry-run")[1]["proposed"])
        self.assertEqual(path.read_text(), before)

    def test_confirm_changes_only_the_cells_it_proposed_and_settles(self) -> None:
        path = self.ready(finished=True)
        before = path.read_text()
        code, report, _ = self.run_cli("--confirm")
        self.assertEqual((code, report["applied"], len(report["proposed"])), (0, True, 1))
        after = path.read_text().splitlines()
        lines = before.splitlines()
        self.assertEqual([i for i in range(len(lines)) if lines[i] != after[i]], [next(i for i, l in enumerate(lines) if RID_A in l)])
        cells = dict(zip(reconcile.REQUIRED_COLUMNS, [c.strip() for c in reconcile.split_cells(next(l for l in after if RID_A in l))[2:]]))
        self.assertEqual((cells["status"], cells["elapsed"], cells["eta"], cells["notes"]), ("done", "1h", "", "a \\| b"))
        self.assertEqual(self.run_cli("--confirm")[1]["proposed"], [], "a second pass finds nothing")

    def test_flagged_rows_stay_when_others_apply(self) -> None:
        path = self.write(self.row(), self.row(rid=RID_B))
        self.status()
        self.status(rid=RID_B, source_tag="wave--other")
        self.artifact()
        self.queue(runs=[self.queue_run(state="done"), self.queue_run(rid=RID_B, state="done")], finished=True)
        code, _report, _ = self.run_cli("--confirm")
        self.assertEqual(code, 1)
        rows = {rid: [c.strip() for c in reconcile.split_cells(next(l for l in path.read_text().splitlines() if rid in l))] for rid in (RID_A, RID_B)}
        self.assertEqual((rows[RID_A][7], rows[RID_B][7]), ("done", "inpr"))

    def test_a_live_waves_rows_belong_to_its_supervisor_agent(self) -> None:
        path = self.ready()
        before = path.read_text()
        report = self.run_cli("--confirm")[1]
        self.assertEqual(report["proposed"], [])
        self.assertTrue(report["held_for_supervisor"])
        self.assertEqual(path.read_text(), before)
        # the agent must name its own wave: another live wave stays held
        self.assertEqual(self.run_cli("--confirm", env={"SWEEP_SUPERVISOR_MODEL": "opus"})[1]["proposed"], [])
        self.assertEqual(path.read_text(), before)
        report = self.run_cli("--confirm", "--wave", WAVE, env={"SWEEP_SUPERVISOR_MODEL": "opus"})[1]
        self.assertTrue(report["proposed"])
        self.assertNotEqual(path.read_text(), before)

    def test_a_closed_waves_rows_go_through_for_anyone(self) -> None:
        path = self.ready(finished=True)
        self.run_cli("--confirm")
        self.assertIn("| done ", path.read_text())

    def test_a_concurrent_edit_aborts_the_write(self) -> None:
        path = self.ready(finished=True)
        document = reconcile.parse(path.read_text())
        text = path.read_text()
        path.write_text(text + "\nsomeone else\n")
        with self.assertRaises(reconcile.ReconcileError):
            reconcile.apply_changes(path, text, document, [])
        self.assertIn("someone else", path.read_text())

    def test_confirm_refuses_a_linked_worktree_and_a_dry_run_reads_the_main_checkout(self) -> None:
        self.ready(finished=True)
        (self.root / ".gitignore").write_text(".waves\n")
        linked = self.root.parent / "linked"
        git(self.root, "worktree", "add", "-q", "--detach", str(linked))
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            self.assertEqual(reconcile.main(["--root", str(linked), "--confirm"]), 2)
        self.assertIn("main checkout", err.getvalue())
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            reconcile.main(["--root", str(linked), "--json"])
        self.assertEqual(json.loads(out.getvalue())["root"], str(self.root))

    def test_no_experiments_file_cannot_run(self) -> None:
        code, _report, err = self.run_cli()
        self.assertEqual(code, 2)
        self.assertIn("EXPERIMENTS.md", err)


class ReportTests(Fixture):
    def test_finished_waves_with_a_worktree_are_listed_for_pruning(self) -> None:
        self.write(self.row(status="done"), self.row(rid=RID_B, status="failed"))
        (self.root / ".waves" / WAVE).mkdir(parents=True)
        self.assertEqual(self.run_cli()[1]["prunable_worktrees"], [WAVE])
        self.write(self.row(status="todo"))
        self.assertEqual(self.run_cli()[1]["prunable_worktrees"], [])

    def test_the_text_report_is_printed(self) -> None:
        self.write(self.row(), self.row(rid=RID_B, status="maybe"))
        self.status(state="running")
        out = io.StringIO()
        with redirect_stdout(out):
            code = reconcile.main(["--root", str(self.root)])
        self.assertEqual(code, 1)
        for needle in ("dry run", "Proposed", "Integrity errors", "ETAs", "Waves:"):
            self.assertIn(needle, out.getvalue())

    def test_elapsed_cells_round_trip(self) -> None:
        for seconds, cell in ((45 * 60, "45m"), (105 * 60, "1h45m"), (51 * 3600, "2d3h"), (30, "30s"), (3600, "1h")):
            self.assertEqual(reconcile.compact(seconds), cell)
            self.assertEqual(reconcile.parse_elapsed(cell), seconds)
        self.assertIsNone(reconcile.parse_elapsed("soon"))

    def test_the_skill_example_parses_with_no_shape_errors(self) -> None:
        skill = (SCRIPT.parents[1] / "SKILL.md").read_text()
        example = skill.split("```markdown\n", 1)[1].split("\n```", 1)[0]
        document = reconcile.parse(example)
        self.assertEqual(document.shape_errors, [])
        self.assertEqual(len(document.sections[0].rows), 3)
        self.assertEqual(reconcile.VERSION_RE.search(document.sections[0].preamble).group(1), "1")


if __name__ == "__main__":
    unittest.main()
