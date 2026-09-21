from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import textwrap
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).parents[1] / "plugins/research/skills/rig-board/scripts/board.py"
SPEC = importlib.util.spec_from_file_location("board", SCRIPT)
assert SPEC and SPEC.loader
board = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = board
SPEC.loader.exec_module(board)


SESSION = "grokking_000_grokking_20260908-101500_rig-4090_gpu0"
SQUEUE_EMPTY = "__SQUEUE_OK__\n"
SQUEUE_TWO = (
    "100|qat_003_sweep_20260908-101500_leonardo_gpu1|RUNNING|None|boost_usr_prod|lrdn0421|0:40:00|1:20:00|2026-09-08T10:20:00|2026-09-08T10:15:00\n"
    "101|adhoc-eval|PENDING|Priority|boost_usr_prod||0:00|2:00:00|N/A|2026-09-08T10:30:00\n"
    "__SQUEUE_OK__\n"
)
SQUEUE_ONE_LEFT = (
    "101|adhoc-eval|RUNNING|None|boost_usr_prod|lrdn0100|0:05:00|1:55:00|2026-09-08T11:00:00|2026-09-08T10:30:00\n"
    "__SQUEUE_OK__\n"
)


def probe_output(sessions: list[str], gpus: list[tuple[int, int, int]], boot: str) -> str:
    """Legacy three-column probe (pre-5.8 rigs answer this shape); the parser must still accept it."""
    lines = list(sessions) + ["__GPUS__"]
    lines += [f"{i}, {mem}, {util}" for i, mem, util in gpus]
    lines += ["__BOOT__", boot]
    return "\n".join(lines) + "\n"


RICH_PROBE = (
    "grokking_000_grokking_20260908-101500_rig-4090_gpu0\n"
    "bxaxis_20260810-222115_rig-4090_ev0\n"
    "rig-4090-computer-management\n"
    "__GPUS__\n"
    "0, GPU-aaaa, NVIDIA GeForce RTX 4090, 24564, 13868, 0, 46, 19.16\n"
    "1, GPU-bbbb, NVIDIA GeForce RTX 4090, 24564, 0, 0, 30, [N/A]\n"
    "__APPS__\n"
    "GPU-aaaa, 956236, 13858, /opt/llama.cpp/build/bin/llama-server\n"
    "__PS__\n"
    " 956236 dansolombrino  245713\n"
    "__LOAD__\n"
    "0.52 0.58 0.59 1/661 2258164\n"
    "16\n"
    "__NET__\n"
    "1000.0\n"
    "enp5s0 1000000000 500000000\n"
    "wlp4s0 0 0\n"
    "1002.0\n"
    "enp5s0 1250000000 502000000\n"
    "wlp4s0 0 0\n"
    "__BOOT__\n"
    "2026-08-11 13:25:49\n"
)

RICH_PROBE_NO_LANE = RICH_PROBE.split("\n", 1)[1]  # same rig, but nobody on the board holds gpu0

RICH_PROBE_NO_NVIDIA = (
    "__GPUS__\n__NVIDIA_SMI_FAILED__\n__APPS__\n__PS__\n__LOAD__\n0.10 0.10 0.10 1/100 5\n8\n__BOOT__\n2026-08-11 13:25:49\n"
)


class BoardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.registry = root / "machines.toml"
        self.board_root = root / "rig_board"
        self.registry.write_text(
            textwrap.dedent(
                f"""
                [board]
                root = "{self.board_root}"
                rigs = ["rig-4090", "behemoth"]
                shared = ["behemoth"]
                slurm = ["leonardo"]

                [machines.rig-4090]
                ssh = "rig-4090"
                hostname = "rig-4090"

                [machines.behemoth]
                ssh = "behemoth"

                [machines.leonardo]
                ssh = "leonardo"
                """
            ).lstrip()
        )
        self.config = board.load_config(self.registry)

    def run_cli(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = board.main(["--registry", str(self.registry), *argv])
        return code, out.getvalue(), err.getvalue()

    def claim(self, **overrides: str) -> tuple[int, str, str]:
        args = {
            "--rig": "rig-4090",
            "--gpu": "0",
            "--project": "grokking",
            "--experiment": "000_grokking",
            "--wave": "20260908-101500",
            "--project-root": "/projects/grokking",
            "--runs-total": "6",
        }
        args.update(overrides)
        return self.run_cli("claim", *[item for pair in args.items() for item in pair])

    def lane(self, rig: str = "rig-4090", gpu: str = "0") -> dict | None:
        return board.read_json(self.config.lane_path(rig, gpu))

    def history(self) -> list[dict]:
        path = self.config.history_path
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines()]

    # ── registry ──

    def test_registry_requires_board_table_and_known_rigs(self) -> None:
        self.registry.write_text('[machines.rig-4090]\nssh = "rig-4090"\n')
        with self.assertRaisesRegex(board.BoardError, r"\[board\]"):
            board.load_config(self.registry)
        self.registry.write_text(
            f'[board]\nroot = "{self.board_root}"\nrigs = ["ghost"]\n[machines.rig-4090]\nssh = "rig-4090"\n'
        )
        with self.assertRaisesRegex(board.BoardError, "unknown machine 'ghost'"):
            board.load_config(self.registry)

    def test_registry_reconcile_every_defaults_and_refuses_hammering(self) -> None:
        self.assertEqual(self.config.reconcile_every, board.DEFAULT_RECONCILE_EVERY_S)
        base = self.registry.read_text()
        self.registry.write_text(base.replace("[board]\n", "[board]\nreconcile_every = 30\n", 1))
        self.assertEqual(board.load_config(self.registry).reconcile_every, 30)
        self.registry.write_text(base.replace("[board]\n", "[board]\nreconcile_every = 0\n", 1))
        self.assertEqual(board.load_config(self.registry).reconcile_every, 0)
        self.registry.write_text(base.replace("[board]\n", "[board]\nreconcile_every = 1\n", 1))
        with self.assertRaisesRegex(board.BoardError, "below 10 s"):
            board.load_config(self.registry)
        self.registry.write_text(base.replace("[board]\n", '[board]\nreconcile_every = "fast"\n', 1))
        with self.assertRaisesRegex(board.BoardError, "integer number of seconds"):
            board.load_config(self.registry)
        with self.assertRaisesRegex(board.BoardError, "--reconcile-every = 1"):
            board.check_reconcile_every(1, "--reconcile-every")
        self.assertIn('id="every"', board.VIEWER_PATH.read_text())

    def test_registry_excludes_unlisted_machines(self) -> None:
        self.assertEqual(sorted(self.config.rigs), ["behemoth", "rig-4090"])
        self.assertTrue(self.config.rigs["behemoth"].shared)
        self.assertFalse(self.config.rigs["rig-4090"].shared)

    # ── session names ──

    def test_session_name_parses_from_the_right(self) -> None:
        parsed = board.parse_session_name("my_proj_001_compression_20260908-101500_rig-3090-ti_gpu0,1")
        self.assertEqual(
            parsed,
            {
                "project": "my_proj",
                "experiment": "001_compression",
                "wave_id": "20260908-101500",
                "rig": "rig-3090-ti",
                "gpu": "0,1",
                "tmux_session": "my_proj_001_compression_20260908-101500_rig-3090-ti_gpu0,1",
            },
        )
        self.assertIsNone(board.parse_session_name("random_session"))
        self.assertIsNone(board.parse_session_name("proj_20260908-101500_rig-4090_gpu0"))

    # ── claim / refresh / release ──

    def test_claim_refresh_release_round_trip(self) -> None:
        code, out, _ = self.claim()
        self.assertEqual(code, 0, out)
        lane = self.lane()
        assert lane is not None
        self.assertEqual(lane["tmux_session"], SESSION)
        self.assertEqual(lane["runs_total"], 6)
        self.assertEqual(lane["observed"]["state"], "claimed")

        code, _, err = self.run_cli("free", "rig-4090", "0")
        self.assertEqual(code, 1, err)

        code, out, _ = self.run_cli(
            "refresh", "--rig", "rig-4090", "--gpu", "0", "--wave", "20260908-101500",
            "--active-run", "model=mlp", "--progress", "epoch 3/10",
            "--eta", "2026-09-08T12:00:00+02:00", "--eta-basis", "exact-run history", "--runs-done", "2",
        )
        self.assertEqual(code, 0, out)
        lane = self.lane()
        assert lane is not None
        self.assertEqual(lane["active_run"], "model=mlp")
        self.assertEqual(lane["runs_done"], 2)
        self.assertEqual(lane["eta"], "2026-09-08T12:00:00+02:00")

        code, _, _ = self.run_cli("release", "--rig", "rig-4090", "--gpu", "0", "--reason", "wave done")
        self.assertEqual(code, 0)
        self.assertIsNone(self.lane())
        code, _, _ = self.run_cli("free", "rig-4090", "0")
        self.assertEqual(code, 0)
        self.assertEqual([h["event"] for h in self.history()], ["claim", "refresh", "release"])

    def test_claim_refuses_a_lane_held_by_another_wave(self) -> None:
        self.assertEqual(self.claim()[0], 0)
        code, _, err = self.claim(**{"--project": "other", "--wave": "20260908-120000"})
        self.assertEqual(code, 3)
        self.assertIn("held by grokking wave 20260908-101500", err)
        lane = self.lane()
        assert lane is not None
        self.assertEqual(lane["project"], "grokking")

    def test_claim_refuses_overlapping_gpu_sets(self) -> None:
        self.assertEqual(self.claim(**{"--rig": "behemoth", "--gpu": "0,1"})[0], 0)
        code, _, err = self.claim(**{"--rig": "behemoth", "--gpu": "1", "--project": "other"})
        self.assertEqual(code, 3)
        self.assertIn("overlaps", err)

    def test_reclaim_same_session_keeps_claimed_at_and_progress(self) -> None:
        self.assertEqual(self.claim()[0], 0)
        first = self.lane()
        assert first is not None
        self.run_cli("refresh", "--rig", "rig-4090", "--gpu", "0", "--runs-done", "3")
        code, out, _ = self.claim()
        self.assertEqual(code, 0, out)
        lane = self.lane()
        assert lane is not None
        self.assertEqual(lane["claimed_at"], first["claimed_at"])
        self.assertEqual(lane["runs_done"], 3)
        self.assertEqual(self.history()[-1]["event"], "reclaim")

    def test_refresh_guards_wave_and_requires_a_claim(self) -> None:
        code, _, err = self.run_cli("refresh", "--rig", "rig-4090", "--gpu", "0", "--progress", "x")
        self.assertEqual(code, 2)
        self.assertIn("not held", err)
        self.claim()
        code, _, err = self.run_cli("refresh", "--rig", "rig-4090", "--gpu", "0", "--wave", "20260101-000000", "--progress", "x")
        self.assertEqual(code, 2)
        self.assertIn("held by wave 20260908-101500", err)

    def test_claim_rejects_bad_inputs(self) -> None:
        code, _, err = self.claim(**{"--gpu": "zero"})
        self.assertEqual(code, 2)
        self.assertIn("gpu must be", err)
        code, _, err = self.claim(**{"--wave": "wave-one"})
        self.assertEqual(code, 2)
        code, _, err = self.claim(**{"--rig": "leonardo"})
        self.assertEqual(code, 2)
        self.assertIn("unknown rig", err)

    # ── reconcile ──

    def reconcile_with(
        self, outputs: dict[str, str | None], slurm: dict[str, tuple[str | None, str]] | None = None
    ) -> tuple[int, str]:
        def fake(rig: board.Rig) -> str | None:
            return outputs.get(rig.name)

        def fake_slurm(target: board.SlurmTarget) -> tuple[str | None, str]:
            return (slurm or {}).get(target.name, (SQUEUE_EMPTY, ""))

        out = io.StringIO()
        with mock.patch.object(board, "run_probe", side_effect=fake), mock.patch.object(
            board, "run_slurm_probe", side_effect=fake_slurm
        ), redirect_stdout(out):
            code = board.main(["--registry", str(self.registry), "reconcile"])
        return code, out.getvalue()

    # ── slurm ──

    def test_registry_rejects_a_machine_that_is_both_rig_and_slurm(self) -> None:
        self.registry.write_text(self.registry.read_text().replace('slurm = ["leonardo"]', 'slurm = ["behemoth"]'))
        with self.assertRaisesRegex(board.BoardError, "both a rig and a Slurm target"):
            board.load_config(self.registry)

    def test_slurm_probe_lists_jobs_and_diffs_the_queue(self) -> None:
        probe = probe_output([], [(0, 0, 0)], "2026-09-01 08:00:00")
        rigs = {"rig-4090": probe, "behemoth": probe}
        code, out = self.reconcile_with(rigs, {"leonardo": (SQUEUE_TWO, "")})
        self.assertEqual(code, 0)
        self.assertIn("leonardo: job 100 appeared (RUNNING)", out)
        self.assertIn("leonardo: job 101 appeared (PENDING)", out)
        cluster = board.read_json(self.config.rig_path("leonardo"))
        assert cluster is not None
        self.assertEqual(cluster["kind"], "slurm")
        self.assertEqual((cluster["running"], cluster["pending"]), (1, 1))
        running, pending = cluster["jobs"]
        self.assertEqual(running["job_id"], "100")
        self.assertEqual(running["project"], "qat")
        self.assertEqual(running["experiment"], "003_sweep")
        self.assertEqual(running["wave_id"], "20260908-101500")
        self.assertEqual(running["time_left"], "1:20:00")
        self.assertEqual(pending["reason"], "Priority")
        self.assertIsNone(pending["nodes"])
        self.assertIsNone(pending["project"])
        _, out = self.reconcile_with(rigs, {"leonardo": (SQUEUE_ONE_LEFT, "")})
        self.assertIn("leonardo: job 100 left the queue (was RUNNING)", out)
        self.assertIn("leonardo: job 101 PENDING → RUNNING", out)
        code, out, _ = self.run_cli("status")
        self.assertIn("leonardo  [slurm, probed", out)
        self.assertIn("101        RUNNING", out)
        code, out, _ = self.run_cli("status", "--json")
        data = json.loads(out)
        self.assertEqual(data["slurm"][0]["rig"], "leonardo")
        self.assertEqual([r["rig"] for r in data["rigs"]], ["rig-4090", "behemoth"])

    def test_slurm_unreachable_keeps_last_known_jobs_and_names_the_reason(self) -> None:
        probe = probe_output([], [(0, 0, 0)], "2026-09-01 08:00:00")
        rigs = {"rig-4090": probe, "behemoth": probe}
        self.reconcile_with(rigs, {"leonardo": (SQUEUE_TWO, "")})
        _, out = self.reconcile_with(rigs, {"leonardo": (None, "ssh authentication failed: renew the cluster certificate")})
        self.assertIn("leonardo: unreachable (ssh authentication failed: renew the cluster certificate); kept 2 job(s)", out)
        _, out = self.reconcile_with(rigs, {"leonardo": (None, "ssh authentication failed: renew the cluster certificate")})
        self.assertNotIn("leonardo: unreachable", out)
        cluster = board.read_json(self.config.rig_path("leonardo"))
        assert cluster is not None
        self.assertFalse(cluster["reachable"])
        self.assertEqual(len(cluster["jobs"]), 2)
        self.assertIsNotNone(cluster["last_reachable_at"])
        code, out, _ = self.run_cli("status")
        self.assertIn("unreachable: ssh authentication failed", out)

    def test_slurm_probe_failure_reasons(self) -> None:
        class Result:
            def __init__(self, stdout: str, stderr: str, rc: int) -> None:
                self.stdout, self.stderr, self.returncode = stdout, stderr, rc

        target = self.config.slurm["leonardo"]
        with mock.patch.object(board.subprocess, "run", return_value=Result("", "user@login: Permission denied (publickey).", 255)):
            self.assertEqual(board.run_slurm_probe(target), (None, "ssh authentication failed: renew the cluster certificate"))
        with mock.patch.object(board.subprocess, "run", return_value=Result(SQUEUE_TWO, "", 0)):
            self.assertEqual(board.run_slurm_probe(target)[1], "")
        with mock.patch.object(board.subprocess, "run", side_effect=board.subprocess.TimeoutExpired("ssh", 1)):
            self.assertEqual(board.run_slurm_probe(target), (None, "ssh timed out"))

    def test_reconcile_keeps_live_lane_and_records_orchestrator_silence(self) -> None:
        self.claim()
        code, out = self.reconcile_with(
            {"rig-4090": probe_output([SESSION], [(0, 20000, 95)], "2026-09-01 08:00:00"), "behemoth": probe_output([], [], "2026-09-01 08:00:00")}
        )
        self.assertEqual(code, 0)
        self.assertIn("already matched", out)
        lane = self.lane()
        assert lane is not None
        self.assertEqual(lane["observed"]["state"], "running")
        self.assertTrue(lane["observed"]["session_alive"])
        self.assertIsInstance(lane["observed"]["orchestrator_silent_s"], int)
        rig = board.read_json(self.config.rig_path("rig-4090"))
        assert rig is not None
        self.assertEqual(rig["foreign"], [])
        self.assertTrue(rig["reachable"])

    def test_reconcile_releases_lane_whose_session_is_gone(self) -> None:
        self.claim()
        code, out = self.reconcile_with(
            {"rig-4090": probe_output([], [(0, 0, 0)], "2026-09-01 08:00:00"), "behemoth": probe_output([], [], "2026-09-01 08:00:00")}
        )
        self.assertEqual(code, 0)
        self.assertIn("session gone → released", out)
        self.assertIsNone(self.lane())
        self.assertEqual(self.history()[-2]["reason"], "reconcile: session gone")

    def test_reconcile_marks_interrupted_after_a_reboot_and_keeps_the_lane(self) -> None:
        self.claim()
        boot = (datetime.now().astimezone() + timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
        code, out = self.reconcile_with(
            {"rig-4090": probe_output([], [(0, 0, 0)], boot), "behemoth": probe_output([], [], "2026-09-01 08:00:00")}
        )
        self.assertEqual(code, 0)
        self.assertIn("interrupted", out)
        lane = self.lane()
        assert lane is not None
        self.assertEqual(lane["observed"]["state"], "interrupted")
        # a second reconcile after the same reboot must not re-log the interruption
        _, out = self.reconcile_with(
            {"rig-4090": probe_output([], [(0, 0, 0)], boot), "behemoth": probe_output([], [], "2026-09-01 08:00:00")}
        )
        self.assertIn("already matched", out)
        self.assertEqual([h["event"] for h in self.history() if h["event"] == "interrupted"], ["interrupted"])
        # recovery re-claims the same session and the lane is live again
        self.assertEqual(self.claim()[0], 0)
        self.reconcile_with(
            {"rig-4090": probe_output([SESSION], [(0, 3000, 50)], boot), "behemoth": probe_output([], [], "2026-09-01 08:00:00")}
        )
        lane = self.lane()
        assert lane is not None
        self.assertEqual(lane["observed"]["state"], "running")

    def test_live_run_is_read_from_the_status_file_of_the_lane_session(self) -> None:
        root = "/data/projects/grokking"
        other = "/data/projects/other"
        hb = board.iso(board.now() - timedelta(seconds=20))
        status = {"schema_version": 2, "state": "running", "started": "2026-09-09T13:05:08+02:00", "ended": None, "elapsed_s": 600.0, "heartbeat": hb, "progress": "step 1800/2250", "progress_completed": 1800, "progress_total": 2250, "progress_unit": "step", "wave_id": "20260908-101500", "gpu": "0"}
        wrong_gpu = {**status, "gpu": "1"}
        done = {**status, "state": "done", "ended": "2026-09-09T13:00:00+02:00", "progress_completed": 2250}
        lines = [
            f"{SESSION}|{root}",
            "__TAIL__",
            f"{SESSION}|260 E[bits]=3.085 loss=0.41",
            "__STATUS__",
            f"{root}/evaluations/000_grokking/model=mlp/lr=0.001/seed=1/.status.json\t{json.dumps(status)}",
            f"{root}/evaluations/000_grokking/model=mlp/lr=0.001/seed=2/.status.json\t{json.dumps(wrong_gpu)}",
            f"{root}/evaluations/000_grokking/model=mlp/lr=0.001/seed=0/.status.json\t{json.dumps(done)}",
            f"{other}/evaluations/000_grokking/model=mlp/lr=0.1/seed=1/.status.json\t{json.dumps(status)}",
        ]
        probe = probe_output([SESSION], [(0, 8000, 90)], "2026-09-01 08:00:00").replace("__BOOT__", "__PANES__\n" + "\n".join(lines) + "\n__BOOT__")
        parsed = board.parse_probe(self.config.rigs["rig-4090"], probe)
        self.assertEqual(parsed["panes"], {SESSION: root})
        self.assertEqual(parsed["tails"], {SESSION: "260 E[bits]=3.085 loss=0.41"})
        self.assertEqual(len(parsed["statuses"]), 4)
        self.claim()
        self.reconcile_with({"rig-4090": probe, "behemoth": probe_output([], [(0, 0, 0)], "2026-09-01 08:00:00")})
        lane = self.lane()
        assert lane is not None
        run = lane["observed"]["run"]
        self.assertEqual(run["path"], "000_grokking/model=mlp/lr=0.001/seed=1")
        self.assertEqual((run["state"], run["progress"], run["completed"], run["total"], run["unit"]), ("running", "step 1800/2250", 1800, 2250, "step"))
        self.assertFalse(run["heartbeat_stale"])
        self.assertAlmostEqual((board.parse_iso(run["eta"]) - board.parse_iso(hb)).total_seconds(), 150.0, delta=1.0)
        self.assertEqual(run["eta_basis"], board.RUN_ETA_BASIS)
        self.assertEqual(lane["observed"]["last_output"], "260 E[bits]=3.085 loss=0.41")
        rig = board.read_json(self.config.rig_path("rig-4090"))
        assert rig is not None
        self.assertNotIn("statuses", rig)
        code, out, _ = self.run_cli("status")
        self.assertIn("live: 000_grokking/model=mlp/lr=0.001/seed=1  running  step 1800/2250 (80.00%)  elapsed 10m  heartbeat 20s ago", out)
        self.assertIn("last output: 260 E[bits]=3.085 loss=0.41", out)
        self.assertIn("overall: 13.33% of the lane (0/6 runs done + 80.00% of the current run)", out)
        # stale heartbeat, then between runs
        stale = {**status, "heartbeat": board.iso(board.now() - timedelta(seconds=600))}
        probe_stale = probe.replace(json.dumps(status), json.dumps(stale))
        self.reconcile_with({"rig-4090": probe_stale, "behemoth": probe_output([], [(0, 0, 0)], "2026-09-01 08:00:00")})
        self.assertTrue(self.lane()["observed"]["run"]["heartbeat_stale"])
        probe_done = probe.replace(json.dumps(status), json.dumps(done))
        self.reconcile_with({"rig-4090": probe_done, "behemoth": probe_output([], [(0, 0, 0)], "2026-09-01 08:00:00")})
        run = self.lane()["observed"]["run"]
        self.assertEqual(run["state"], "done")
        self.assertIsNone(run["eta"])

    def test_reclaim_after_adoption_clears_the_placeholder_basis(self) -> None:
        probe = probe_output([SESSION], [(0, 8000, 90)], "2026-09-01 08:00:00")
        self.reconcile_with({"rig-4090": probe, "behemoth": probe})
        lane = self.lane()
        assert lane is not None
        self.assertTrue(lane["observed"]["adopted"])
        self.assertTrue(lane["eta_basis"].startswith("unavailable:"))
        code, out, _ = self.claim()
        self.assertEqual(code, 0)
        self.assertIn("[claimed]", out)
        lane = self.lane()
        assert lane is not None
        self.assertIsNone(lane["eta_basis"])
        self.assertNotIn("adopted", lane["observed"])
        self.assertEqual(lane["runs_total"], 6)

    def test_reconcile_adopts_live_session_without_a_lane(self) -> None:
        session = "other_proj_002_ablation_20260908-093000_behemoth_gpu0"
        code, out = self.reconcile_with(
            {"rig-4090": probe_output([], [(0, 0, 0)], "2026-09-01 08:00:00"), "behemoth": probe_output([session, "unrelated"], [(0, 40000, 99), (1, 60000, 80)], "2026-09-01 08:00:00")}
        )
        self.assertEqual(code, 0)
        self.assertIn("adopted live session", out)
        lane = self.lane("behemoth", "0")
        assert lane is not None
        self.assertEqual(lane["project"], "other_proj")
        self.assertEqual(lane["experiment"], "002_ablation")
        self.assertIsNone(lane["eta"])
        self.assertTrue(lane["observed"]["adopted"])
        rig = board.read_json(self.config.rig_path("behemoth"))
        assert rig is not None
        self.assertEqual(rig["foreign"], [1])
        code, out, _ = self.run_cli("free", "behemoth", "1")
        self.assertEqual(code, 1)
        self.assertIn("foreign", out)

    def test_reconcile_keeps_lanes_when_a_rig_is_unreachable(self) -> None:
        self.claim()
        code, out = self.reconcile_with({"rig-4090": None, "behemoth": None})
        self.assertEqual(code, 0)
        self.assertIn("unreachable; kept 1 lane", out)
        lane = self.lane()
        assert lane is not None
        self.assertEqual(lane["observed"]["state"], "unreachable")
        rig = board.read_json(self.config.rig_path("rig-4090"))
        assert rig is not None
        self.assertFalse(rig["reachable"])
        self.run_cli("release", "--rig", "rig-4090", "--gpu", "0")
        code, out, _ = self.run_cli("free", "rig-4090", "0")
        self.assertEqual(code, 2)
        self.assertIn("unverified", out)

    def test_rich_probe_reports_gpu_facts_processes_owners_load_and_strays(self) -> None:
        probe = board.parse_probe(self.config.rigs["rig-4090"], RICH_PROBE)
        self.assertTrue(probe["gpu_probe_ok"])
        self.assertEqual(probe["load"], [0.52, 0.58, 0.59])
        self.assertEqual(probe["cpus"], 16)
        # 250 MB down and 2 MB up over two seconds, summed over the physical NICs
        self.assertEqual((probe["net"]["rx_mb_s"], probe["net"]["tx_mb_s"], probe["net"]["interval_s"]), (125.0, 1.0, 2.0))
        self.assertEqual([i["name"] for i in probe["net"]["interfaces"]], ["enp5s0", "wlp4s0"])
        self.assertEqual(probe["gpus"][0]["index"], 0)
        g0, g1 = probe["gpus"]
        self.assertEqual((g0["name"], g0["memory_total_mib"], g0["memory_used_mib"], g0["utilization"], g0["temperature_c"], g0["power_w"]), ("NVIDIA GeForce RTX 4090", 24564, 13868, 0, 46, 19))
        self.assertIsNone(g1["power_w"])
        self.assertEqual(g0["processes"], [{"pid": 956236, "user": "dansolombrino", "memory_mib": 13858, "name": "/opt/llama.cpp/build/bin/llama-server", "elapsed_s": 245713}])
        self.assertEqual(g1["processes"], [])
        # legacy three-column rigs still parse
        legacy = board.parse_probe(self.config.rigs["rig-4090"], probe_output([], [(0, 2048, 50)], "2026-09-01 08:00:00"))
        self.assertEqual(legacy["gpus"], [{"index": 0, "memory_used_mib": 2048, "utilization": 50}])
        self.assertTrue(legacy["gpu_probe_ok"])
        self.assertIsNone(legacy["net"])  # rigs without the net section report no rate, not zero
        self.assertIsNone(board.parse_net("1000.0\neth0 10 10\n1001.0\neth0 5 10\n"))  # counter reset
        broken = board.parse_probe(self.config.rigs["rig-4090"], RICH_PROBE_NO_NVIDIA)
        self.assertFalse(broken["gpu_probe_ok"])
        self.assertEqual(broken["gpus"], [])
        self.assertEqual(broken["cpus"], 8)
        # through reconcile: gpu0 is held by the lane, gpu1 free, the stray session is surfaced, not adopted
        self.claim()
        code, out = self.reconcile_with({"rig-4090": RICH_PROBE, "behemoth": RICH_PROBE_NO_NVIDIA}, {"leonardo": (SQUEUE_EMPTY, "")})
        self.assertEqual(code, 0)
        data = json.loads(self.run_cli("status", "--json")[1])
        rig = data["rigs"][0]
        self.assertEqual(rig["free_gpus"], [1])
        self.assertEqual(rig["foreign"], [])
        self.assertEqual(rig["stray_sessions"], ["bxaxis_20260810-222115_rig-4090_ev0"])
        self.assertEqual(data["api_version"], board.API_VERSION)
        summary = data["summary"]
        self.assertEqual((summary["gpus"], summary["free"], summary["held"], summary["foreign"], summary["unknown_rigs"]), (2, 1, 1, 0, 1))
        self.assertIsNone(summary["next_free"])
        self.run_cli("refresh", "--rig", "rig-4090", "--gpu", "0", "--eta", "2026-09-08T12:05:00+02:00", "--eta-basis", "exact-run history")
        summary = json.loads(self.run_cli("status", "--json")[1])["summary"]
        self.assertEqual(summary["next_free"]["rig"], "rig-4090")
        self.assertEqual(summary["next_free"]["eta"], "2026-09-08T12:05:00+02:00")
        behemoth = data["rigs"][1]
        self.assertFalse(behemoth["gpu_probe_ok"])
        self.assertEqual(behemoth["free_gpus"], [])
        code, out, _ = self.run_cli("status")
        self.assertIn("nvidia-smi failed on the rig", out)
        self.assertIn("stray tmux session (not a lane): bxaxis_20260810-222115_rig-4090_ev0", out)
        self.assertIn("gpu1  free         NVIDIA GeForce RTX 4090 · 0/24564 MiB", out)
        self.assertIn("fleet: 1 free · 1 held", out)

    def test_foreign_card_names_its_owner_process(self) -> None:
        code, out = self.reconcile_with({"rig-4090": RICH_PROBE_NO_LANE, "behemoth": RICH_PROBE_NO_NVIDIA}, {"leonardo": (SQUEUE_EMPTY, "")})
        data = json.loads(self.run_cli("status", "--json")[1])
        self.assertEqual(data["rigs"][0]["foreign"], [0])
        code, out, _ = self.run_cli("status")
        self.assertIn("gpu0  foreign      busy without a lane on the board · NVIDIA GeForce RTX 4090 · 13868/24564 MiB · 0% util · 46°C · idle, holding memory · dansolombrino pid 956236 llama-server 13858 MiB for 2d20h", out)
        self.assertEqual(self.run_cli("free", "rig-4090", "0")[0], 1)
        self.assertEqual(self.run_cli("free", "rig-4090", "1")[0], 0)

    def test_history_endpoint_and_cli_skip_quiet_reconciles_and_refresh_ticks(self) -> None:
        self.claim()
        self.run_cli("refresh", "--rig", "rig-4090", "--gpu", "0", "--progress", "epoch 1")
        probe = probe_output([SESSION], [(0, 8000, 90)], "2026-09-01 08:00:00")
        self.reconcile_with({"rig-4090": probe, "behemoth": probe}, {"leonardo": (SQUEUE_EMPTY, "")})  # nothing changes
        self.run_cli("release", "--rig", "rig-4090", "--gpu", "0", "--reason", "done")
        events = board.read_history(self.config)
        self.assertEqual([e["event"] for e in events], ["release", "claim"])
        with_refresh = board.read_history(self.config, include_refresh=True)
        self.assertEqual([e["event"] for e in with_refresh], ["release", "refresh", "claim"])
        self.assertEqual(board.read_history(self.config, hours=0), [])
        code, out, _ = self.run_cli("history")
        self.assertEqual(code, 0)
        self.assertIn("release      rig-4090 gpu0      done", out)
        self.assertNotIn("refresh", out)
        Fake = self.fake_request(self.config)
        api = Fake("/api/history?hours=48&limit=1&refresh=1")
        api.do_GET()
        self.assertEqual(api.code, 200)
        payload = json.loads(api.wfile.getvalue())
        self.assertEqual([e["event"] for e in payload["events"]], ["release"])
        bad = Fake("/api/history?hours=soon")
        bad.do_GET()
        self.assertEqual(bad.code, 400)

    def test_queue_groups_sweep_jobs_and_folds_bulk_diffs(self) -> None:
        names = [f"20260909-121443__model=vit,seed=1,pct={pct},combo={c}" for pct in (10, 20) for c in range(4)]
        wd = "/leonardo_work/IscrC_QATT/qat-transfer"
        cmd = wd + "/scripts/000_finetune/000_vision/001_class_subsets/{n}.sbatch"
        lines = [f"{200 + i}|{n}|{'RUNNING' if i < 3 else 'PENDING'}|{'None' if i < 3 else 'Priority'}|boost|{'lrdn0' + str(i) if i < 3 else ''}|0:10:00|0:20:00|2026-09-09T12:00:00|2026-09-09T11:00:00|{wd}|{cmd.format(n=n)}|iscrc_qatt|{'boost_qos_dbg' if i == 0 else 'normal'}" for i, n in enumerate(names)]
        lines.append("300|eval_7|PENDING|Resources|boost||0:00|1:00:00|N/A|2026-09-09T11:30:00")
        lines.append("301|eval_8|PENDING|Resources|boost||0:00|1:00:00|N/A|2026-09-09T11:30:00")
        lines.append("302|qat_003_sweep_20260908-101500_leonardo_gpu1|RUNNING|None|boost|lrdn9|0:40:00|1:20:00|2026-09-08T10:20:00|2026-09-08T10:15:00")
        sacct = [
            f"150|20260909-121443__model=vit,seed=1,pct=5,combo=0|COMPLETED|2026-09-09T11:50:00|{wd}|iscrc_qatt|normal",
            f"151|20260909-121443__model=vit,seed=1,pct=5,combo=1|COMPLETED|2026-09-09T11:52:00|{wd}",
            f"152|20260909-121443__model=vit,seed=1,pct=5,combo=2|FAILED|2026-09-09T11:40:00|{wd}",
            f"153|20260909-121443__model=vit,seed=1,pct=5,combo=3|CANCELLED by 1000|2026-09-09T11:41:00|{wd}",
            "200|20260909-121443__model=vit,seed=1,pct=10,combo=0|RUNNING|Unknown",
            "90|old_wave_20260901-000000_leonardo_gpu0|TIMEOUT|2026-09-08T23:00:00",
            "91|eval_3|OUT_OF_MEMORY|2026-09-08T22:00:00",
        ]
        saldo = [
            "IscrC_OLD           20250620    20260320         80000               86430         86430           108.0             0                  0",
            "IscrC_QATT          20260702    20270402         68000                   0             0             0.0          7445                  0",
            "IscrC_SOON          20260101    20260920         10000                9500          9500            95.0          1000                200",
        ]
        output = "\n".join(lines) + "\n__SACCT__\n" + "\n".join(sacct) + "\n__SALDO__\n" + "\n".join(saldo) + "\n__SQUEUE_OK__\n"
        probe = probe_output([], [(0, 0, 0)], "2026-09-01 08:00:00")
        # the saldo fixture carries real dates: freeze the clock so "expiring" does not rot into "expired"
        with mock.patch.object(board, "now", return_value=datetime.fromisoformat("2026-09-10T10:00:00+02:00")):
            code, out = self.reconcile_with({"rig-4090": probe, "behemoth": probe}, {"leonardo": (output, "")})
        self.assertIn("leonardo: 7 jobs appeared (PENDING) [203…301]", out)
        self.assertIn("leonardo: job 200 appeared (RUNNING)", out)
        cluster = board.read_json(self.config.rig_path("leonardo"))
        assert cluster is not None
        groups = {g["key"]: g for g in cluster["groups"]}
        sweep = groups["qat-transfer · wave 20260909-121443"]
        self.assertEqual((sweep["project"], sweep["experiment"], sweep["wave_id"]), ("qat-transfer", "000_finetune/000_vision/001_class_subsets", "20260909-121443"))
        self.assertEqual((sweep["accounts"], sweep["qos"]), (["iscrc_qatt"], ["normal", "boost_qos_dbg"]))
        self.assertEqual(cluster["accounts"], ["iscrc_qatt"])
        budgets = {b["account"]: b for b in cluster["budgets"]}
        qatt = budgets["IscrC_QATT"]
        self.assertTrue(qatt["in_use"])
        self.assertEqual((qatt["status"], qatt["remaining_h"], qatt["month_total_h"], qatt["month_remaining_h"], qatt["end"]), ("ok", 68000.0, 7445.0, 7445.0, "2027-04-02"))
        self.assertEqual(budgets["IscrC_OLD"]["status"], "expired")
        self.assertFalse(budgets["IscrC_OLD"]["in_use"])
        self.assertEqual(budgets["IscrC_SOON"]["status"], "expiring")
        self.assertEqual([b["account"] for b in cluster["budgets"]][0], "IscrC_QATT")
        data = json.loads(self.run_cli("status", "--json")[1])
        self.assertEqual(data["summary"]["budget_alerts"], [])
        with mock.patch.object(board, "now", return_value=datetime.fromisoformat("2027-03-20T10:00:00+02:00")):
            expiring = board.parse_saldo(saldo[1], {"iscrc_qatt"})[0]
        self.assertEqual((expiring["status"], expiring["days_left"]), ("expiring", 13))
        self.assertEqual(board.budget_alerts({"rig": "leonardo", "budgets": [expiring]}), ["leonardo account IscrC_QATT: expires 2027-04-02 (13 days)"])
        used_up = board.parse_saldo("IscrC_X 20260101 20270101 100 100 100 100.0 10 10", {"iscrc_x"})[0]
        self.assertEqual(used_up["status"], "exhausted")
        monthly = board.parse_saldo("IscrC_Y 20260101 20270101 100 10 10 10.0 10 10", {"iscrc_y"})[0]
        self.assertEqual(monthly["status"], "month_exhausted")
        self.assertEqual(by_id_acct := {j["job_id"]: j.get("account") for j in cluster["finished"]}["150"], "iscrc_qatt")
        self.assertEqual(by_project := {j["job_id"]: j["project_inferred"] for j in cluster["jobs"]}["200"], "qat-transfer")
        self.assertEqual((sweep["running"], sweep["pending"], len(sweep["jobs"])), (3, 5, 8))
        self.assertEqual(sweep["finished"], {"completed": 2, "failed": 1, "cancelled": 1, "timeout": 0})
        self.assertEqual((sweep["finished_total"], sweep["total"], sweep["last_end"]), (4, 12, "2026-09-09T11:52:00"))
        self.assertEqual((cluster["completed"], cluster["failed"], cluster["cancelled"], cluster["timeout"]), (2, 2, 1, 1))
        self.assertEqual([j["job_id"] for j in cluster["finished"]][:2], ["151", "150"])
        self.assertEqual(groups["eval"]["finished"]["failed"], 1)
        self.assertEqual(groups["eval"]["total"], 3)
        old_wave = groups["old_wave / 20260901-000000 · wave 20260901-000000"] if "old_wave / 20260901-000000 · wave 20260901-000000" in groups else None
        self.assertTrue(any(g["jobs"] == [] and g["finished"]["timeout"] == 1 for g in cluster["groups"]))
        self.assertEqual(sweep["common"], "model=vit,seed=1")
        self.assertEqual(sweep["min_time_left"], "0:20:00")
        self.assertEqual(sweep["reasons"], ["Priority"])
        by_id = {j["job_id"]: j for j in cluster["jobs"]}
        self.assertEqual(by_id["200"]["variant"], "pct=10,combo=0")
        self.assertEqual(by_id["207"]["variant"], "pct=20,combo=3")
        self.assertEqual(groups["eval"]["jobs"], ["300", "301"])
        self.assertEqual(groups["qat / 003_sweep · wave 20260908-101500"]["running"], 1)
        code, out, _ = self.run_cli("status")
        self.assertIn("── qat-transfer · wave 20260909-121443  boost  account iscrc_qatt  qos normal, boost_qos_dbg  12 total: 3 running (left 0:20:00…0:20:00) · 5 pending", out)
        self.assertIn("account iscrc_qatt", out.split("leonardo  [slurm")[1].splitlines()[0])
        self.assertIn("budget IscrC_QATT       0/68000 h (0.00%)  remaining 68000 h  month 0/7445 h (0.00%)  until 2027-04-02", out)
        self.assertIn("budget IscrC_SOON", out)
        self.assertNotIn("budget IscrC_OLD", out)
        self.assertIn("experiment: 000_finetune/000_vision/001_class_subsets", out)
        self.assertIn("2 completed (16.67%) · 1 failed (8.33%) · 1 cancelled (8.33%)", out)
        self.assertIn("last 3days: 2 completed, 2 failed, 1 cancelled, 1 timeout", out)
        self.assertIn("common: model=vit,seed=1", out)
        self.assertIn("… 5 more in this group (status --all lists every job)", out)
        code, out, _ = self.run_cli("status", "--all")
        self.assertNotIn("more in this group", out)
        self.assertIn("207        PENDING      pct=20,combo=3", out)
        self.assertEqual(board.slurm_seconds("1-02:03:04"), 93784)
        self.assertEqual(board.slurm_seconds("23:12"), 1392)
        self.assertEqual(board.slurm_seconds("5"), 300)

    def test_probe_runs_locally_on_the_hub_and_over_ssh_elsewhere(self) -> None:
        with mock.patch.object(board.socket, "gethostname", return_value="rig-4090"):
            self.assertEqual(board.probe_command(self.config.rigs["rig-4090"])[:2], ["bash", "-lc"])
            remote = board.probe_command(self.config.rigs["behemoth"])
        self.assertEqual(remote[:5], ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"])
        self.assertEqual(remote[5], "behemoth")

    # ── status / snapshot ──

    def test_status_json_lists_every_configured_rig(self) -> None:
        self.claim()
        code, out, _ = self.run_cli("status", "--json")
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual([r["rig"] for r in data["rigs"]], ["rig-4090", "behemoth"])
        self.assertEqual(data["rigs"][0]["lanes"][0]["tmux_session"], SESSION)
        self.assertIsNone(data["rigs"][1]["reachable"])
        code, out, _ = self.run_cli("status")
        self.assertEqual(code, 0)
        self.assertIn("grokking / 000_grokking", out)
        self.assertIn("never probed", out)

    def fake_request(self, config: board.BoardConfig):
        handler = board.make_handler(config)

        class Fake(handler):  # type: ignore[misc,valid-type]
            def __init__(self, path: str, headers: dict[str, str] | None = None) -> None:
                self.path = path
                self.headers = headers or {}
                self.wfile = io.BytesIO()
                self.headers_sent: list[tuple[str, str]] = []

            def send_response(self, code: int, message: str | None = None) -> None:
                self.code = code

            def send_header(self, key: str, value: str) -> None:
                self.headers_sent.append((key, value))

            def end_headers(self) -> None:
                pass

        return Fake

    def test_viewer_requires_the_token_when_configured(self) -> None:
        self.claim()
        token = "x" * 24
        config = board.BoardConfig(root=self.config.root, rigs=self.config.rigs, token=token)
        Fake = self.fake_request(config)
        for path in ("/", "/api/board", "/?token=wrong"):
            denied = Fake(path)
            denied.do_GET()
            self.assertEqual(denied.code, 401, path)
            self.assertNotIn(b"grokking", denied.wfile.getvalue())
        health = Fake("/healthz")
        health.do_GET()
        self.assertEqual(health.code, 200)
        page = Fake(f"/?token={token}")
        page.do_GET()
        self.assertEqual(page.code, 200)
        cookie = dict(page.headers_sent)["Set-Cookie"]
        self.assertIn(f"{board.COOKIE_NAME}={token}", cookie)
        self.assertIn("HttpOnly", cookie)
        by_cookie = Fake("/api/board", {"Cookie": cookie.split(";")[0]})
        by_cookie.do_GET()
        self.assertEqual(by_cookie.code, 200)
        self.assertNotIn("Set-Cookie", dict(by_cookie.headers_sent))
        by_header = Fake("/api/board", {"X-Board-Token": token})
        by_header.do_GET()
        self.assertEqual(by_header.code, 200)
        by_bearer = Fake("/api/board", {"Authorization": f"Bearer {token}"})
        by_bearer.do_GET()
        self.assertEqual(by_bearer.code, 200)

    def test_registry_rejects_a_short_token(self) -> None:
        self.registry.write_text(self.registry.read_text().replace("[board]\n", '[board]\ntoken = "short"\n', 1))
        with self.assertRaisesRegex(board.BoardError, "board.token"):
            board.load_config(self.registry)

    def test_token_command_prints_a_fresh_secret(self) -> None:
        code, out, _ = self.run_cli("token")
        self.assertEqual(code, 0)
        self.assertGreaterEqual(len(out.strip()), 32)

    def test_viewer_serves_page_and_json(self) -> None:
        self.claim()
        handler = board.make_handler(self.config, {"reconcile_every": 120})

        class Fake(handler):  # type: ignore[misc,valid-type]
            def __init__(self, path: str) -> None:
                self.path = path
                self.headers = {}
                self.wfile = io.BytesIO()
                self.headers_sent: list[tuple[str, str]] = []

            def send_response(self, code: int, message: str | None = None) -> None:
                self.code = code

            def send_header(self, key: str, value: str) -> None:
                self.headers_sent.append((key, value))

            def end_headers(self) -> None:
                pass

        page = Fake("/")
        page.do_GET()
        self.assertEqual(page.code, 200)
        body = page.wfile.getvalue()
        self.assertIn(b"/api/board", body)
        self.assertIn(b"/api/history", body)
        self.assertTrue(board.VIEWER_PATH.is_file(), board.VIEWER_PATH)
        self.assertEqual(body, board.VIEWER_PATH.read_bytes())
        with mock.patch.object(board, "VIEWER_PATH", board.VIEWER_PATH.with_name("missing.html")):
            fallback = Fake("/")
            fallback.do_GET()
            self.assertEqual(fallback.code, 200)
            self.assertIn(b"viewer.html is missing", fallback.wfile.getvalue())
        api = Fake("/api/board?x=1")
        api.do_GET()
        self.assertEqual(api.code, 200)
        payload = json.loads(api.wfile.getvalue())
        self.assertEqual(payload["rigs"][0]["lanes"][0]["project"], "grokking")
        self.assertEqual(payload["serve"]["reconcile_every_s"], 120)
        self.assertIn("summary", payload)
        missing = Fake("/nope")
        missing.do_GET()
        self.assertEqual(missing.code, 404)

    def test_status_shows_supervised_waves_and_how_recently_they_were_driven(self) -> None:
        project = Path(self.tmp.name) / "proj"
        state = project / ".waves" / "_state" / "20260921-093000"
        board.write_json(self.config.root / "supervisor" / "proj__20260921-093000.json", {"project_root": str(project), "wave_id": "20260921-093000"})
        board.write_json(state / "runtime.json", {"cycled_at": board.iso(board.now()), "agent_ticked_at": None})
        board.write_json(state / "snapshot.json", {"project": "proj", "counts": {"done": 3}, "total": 9, "questions": [{"id": "q1"}]})
        code, out, _ = self.run_cli("status")
        self.assertEqual(code, 0)
        self.assertRegex(out, r"supervised: proj wave 20260921-093000 · done 3/9 · supervisor cycle \S+ ago · agent tick \S+ ago · 1 open question")
        data = json.loads(self.run_cli("status", "--json")[1])
        self.assertEqual(data["supervised_waves"][0]["wave_id"], "20260921-093000")


if __name__ == "__main__":
    unittest.main()
