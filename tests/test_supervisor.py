from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "plugins/research/skills/sweep-supervisor/scripts/supervisor.py"
SPEC = importlib.util.spec_from_file_location("supervisor", SCRIPT)
assert SPEC and SPEC.loader
supervisor = importlib.util.module_from_spec(SPEC)
sys.modules["supervisor"] = supervisor
SPEC.loader.exec_module(supervisor)
rigsync = supervisor.rigsync
board = supervisor.board

WAVE = "20260921-093000"
REVISION = "a" * 40

FAKE_TMUX = """#!/bin/sh
# records sessions in $FAKE_TMUX_STATE instead of starting anything
case "$1" in
  ls) cat "$FAKE_TMUX_STATE" 2>/dev/null; exit 0 ;;
  has-session) grep -Fxq "$3" "$FAKE_TMUX_STATE" 2>/dev/null ;;
  new-session) echo "$4" >> "$FAKE_TMUX_STATE" ;;
esac
"""

FAKE_SBATCH = """#!/bin/sh
n=$(cat "$FAKE_SLURM/next" 2>/dev/null || echo 100); echo $((n + 1)) > "$FAKE_SLURM/next"
echo "$@" >> "$FAKE_SLURM/submitted"; echo "$n"
"""
FAKE_SQUEUE = """#!/bin/sh
case "$*" in *--format=%j) cut -d'|' -f2 "$FAKE_SLURM/squeue" 2>/dev/null ;; *) cat "$FAKE_SLURM/squeue" 2>/dev/null ;; esac
"""
FAKE_SACCT = '#!/bin/sh\ncat "$FAKE_SLURM/sacct" 2>/dev/null\n'
FAKE_SCANCEL = '#!/bin/sh\necho "$1" >> "$FAKE_SLURM/cancelled"\n'


def write(path: Path, text: str, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    if executable:
        path.chmod(path.stat().st_mode | stat.S_IEXEC)


def local_remote(machine, argv, *, check=True):
    """Every rig is a directory on this host: the shell the supervisor sends really runs."""
    return rigsync.run(argv, check=check)


class SupervisorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.hub = self.base / "hub" / "proj"
        self.peer = self.base / "peer" / "proj"
        self.cluster = self.base / "cluster" / "proj"
        for path in (self.hub, self.peer, self.cluster):
            path.mkdir(parents=True)
        self.bin = self.base / "bin"
        for name, body in (("tmux", FAKE_TMUX), ("sbatch", FAKE_SBATCH), ("squeue", FAKE_SQUEUE), ("sacct", FAKE_SACCT), ("scancel", FAKE_SCANCEL)):
            write(self.bin / name, body, executable=True)
        (self.base / "slurm").mkdir()
        self.registry = self.base / "machines.toml"
        self.write_registry(agent=True)
        write(
            self.hub / "sync.toml",
            f"""
            [machines.hub]
            repo_path = "{self.hub}"
            [machines.peer]
            repo_path = "{self.peer}"
            [machines.cluster]
            repo_path = "{self.cluster}"
            [artifacts.checkpoints]
            path = "checkpoints"
            [artifacts.evaluations]
            path = "evaluations"
            """.replace("            ", ""),
        )
        env = {
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "FAKE_TMUX_STATE": str(self.base / "tmux-sessions"),
            "FAKE_SLURM": str(self.base / "slurm"),
        }
        patches = [
            mock.patch.dict(os.environ, env),
            mock.patch.object(rigsync, "remote", side_effect=local_remote),
            mock.patch.object(supervisor.Cycle, "verify_lane", return_value=None),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(self.tmp.cleanup)

    def write_registry(self, agent: bool) -> None:
        profile = (
            """
            [supervisor.agent]
            command = ["sh", "-c", "echo model={model} effort={effort}; echo \\"$0\\"", "{prompt}"]
            model = "opus"
            effort = "high"
            """
            if agent
            else ""
        )
        write(
            self.registry,
            f"""
            [board]
            root = "{self.base / 'board'}"
            rigs = ["hub", "peer"]
            slurm = ["cluster"]
            [supervisor]
            cycle_every = 60
            {profile}
            [machines.hub]
            ssh = "hub"
            hostname = "hub-host"
            storage_root = "{self.base / 'hub'}"
            [machines.peer]
            ssh = "peer"
            hostname = "peer-host"
            storage_root = "{self.base / 'peer'}"
            [machines.cluster]
            ssh = "cluster"
            gpus = "job"
            storage_root = "{self.base / 'cluster'}"
            """.replace("            ", ""),
        )

    def manifest(self, runs: int = 5, pool: dict | None = None, **extra) -> Path:
        entries = []
        for index in range(runs):
            name = f"model=m{index}"
            entries.append(
                {
                    "id": name,
                    "script": f"scripts/000_exp/{name}/wave_{WAVE}/wave.sh",
                    "status_path": f"evaluations/000_exp/{name}/.status.json",
                    "artifact": f"evaluations/000_exp/{name}/result.json",
                    "checkpoint_dir": f"checkpoints/000_exp/{name}",
                    "eval_dir": f"evaluations/000_exp/{name}",
                    "cost_s": 3600 - index,
                    **extra.get("run_extra", {}).get(index, {}),
                }
            )
        payload = {
            "wave_id": WAVE,
            "experiment": "000_exp",
            "revision": REVISION,
            "lane_script": f"scripts/000_exp/_lanes/wave_{WAVE}/lane.sh",
            "pool": pool or {"hub": {"lanes": ["0"]}, "peer": {"lanes": ["0"]}},
            "weights": {"hub": 1.0, "peer": 0.5, "cluster": 1.0},
            "offload": extra.get("offload", []),
            "runs": entries,
        }
        payload["weights"] = {rig: payload["weights"][rig] for rig in payload["pool"]}
        path = self.base / "manifest.json"
        path.write_text(json.dumps(payload))
        return path

    def cli(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = supervisor.main(["--root", str(self.hub), "--registry", str(self.registry), *argv])
        return code, out.getvalue(), err.getvalue()

    def register(self, **kwargs) -> None:
        code, _out, err = self.cli("register", "--wave", WAVE, "--manifest", str(self.manifest(**kwargs)), "--confirm")
        self.assertEqual(code, 0, err)

    def queue(self) -> dict:
        return supervisor.Wave(self.hub, WAVE).load()

    def lane(self, rig_root: Path, gpu: str = "0") -> Path:
        return rig_root / ".waves" / "_state" / WAVE / "lanes" / f"gpu{gpu}"

    def finish_entry(self, rig_root: Path, entry: str, rc: int, artifact: str | None, elapsed: float = 100.0) -> None:
        lane = self.lane(rig_root)
        for folder in ("queue", "running"):
            source = lane / folder / entry
            if source.exists():
                (lane / "finished").mkdir(exist_ok=True)
                source.rename(lane / "finished" / f"{entry}.rc{rc}")
        if artifact:
            write(rig_root / artifact, "{}")
            write(rig_root / Path(artifact).parent / ".status.json", json.dumps({"state": "done", "wave_id": WAVE, "elapsed_s": elapsed}))

    def test_a_wave_is_never_supervised_by_an_undeclared_agent_profile(self) -> None:
        self.write_registry(agent=False)
        code, _out, err = self.cli("register", "--wave", WAVE, "--manifest", str(self.manifest()), "--confirm")
        self.assertEqual(code, 2)
        self.assertIn("[supervisor.agent]", err)
        self.assertFalse((self.hub / ".waves").exists())

    def test_register_needs_confirmation_and_is_single_shot(self) -> None:
        manifest = str(self.manifest())
        self.assertEqual(self.cli("register", "--wave", WAVE, "--manifest", manifest)[0], 2)
        code, out, _ = self.cli("register", "--wave", WAVE, "--manifest", manifest, "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("agent opus/high", out)
        self.assertFalse(supervisor.Wave(self.hub, WAVE).queue_path.exists())
        self.register()
        self.assertTrue((self.base / "board" / "supervisor" / f"proj__{WAVE}.json").exists())
        self.assertEqual(self.cli("register", "--wave", WAVE, "--manifest", manifest, "--confirm")[0], 2)

    def test_cycle_starts_lanes_and_feeds_shallow_buffers_in_queue_order(self) -> None:
        self.register()
        code, out, err = self.cli("cycle", "--wave", WAVE)
        self.assertEqual(code, 0, err)
        sessions = (self.base / "tmux-sessions").read_text().split()
        self.assertEqual(sessions, [f"proj_000_exp_{WAVE}_hub_gpu0", f"proj_000_exp_{WAVE}_peer_gpu0"])
        queue = self.queue()
        placed = {run["id"]: run["lane"]["rig"] for run in queue["runs"] if run["state"] == "assigned"}
        self.assertEqual(placed, {"model=m0": "hub", "model=m1": "hub", "model=m2": "peer", "model=m3": "peer"})
        self.assertEqual(queue["runs"][4]["state"], "queued")
        entries = sorted(p.name for p in (self.lane(self.hub) / "queue").iterdir())
        self.assertEqual(len(entries), supervisor.BUFFER_DEPTH)
        self.assertEqual((self.lane(self.hub) / "queue" / entries[0]).read_text(), queue["runs"][0]["script"])
        held = board.read_json(self.base / "board" / "lanes" / "peer" / "gpu0.json")
        self.assertEqual(held["wave_id"], WAVE)
        # nothing changed on the rigs: a second cycle assigns nothing more
        self.cli("cycle", "--wave", WAVE)
        self.assertEqual(sum(run["state"] == "assigned" for run in self.queue()["runs"]), 4)

    def test_a_fast_lane_takes_more_runs_and_outcomes_follow_the_artifact(self) -> None:
        self.register()
        self.cli("cycle", "--wave", WAVE)
        first = self.queue()["runs"][0]
        self.finish_entry(self.hub, first["entry"], 0, first["artifact"])
        second = self.queue()["runs"][1]
        self.finish_entry(self.hub, second["entry"], 1, None)
        self.cli("cycle", "--wave", WAVE)
        queue = self.queue()
        states = {run["id"]: run["state"] for run in queue["runs"]}
        self.assertEqual(states["model=m0"], "done")
        self.assertEqual(states["model=m1"], "failed")
        self.assertEqual(queue["runs"][4]["lane"], {"rig": "hub", "gpu": "0"})
        events = [record["event"] for record in supervisor.Wave(self.hub, WAVE).ledger()]
        self.assertIn("run-failed", events)
        self.assertEqual(supervisor.wake_due(supervisor.Wave(self.hub, WAVE), supervisor.load_settings(self.registry)), "event: run-failed")

    def test_an_offload_receipt_counts_as_the_artifact(self) -> None:
        self.register()
        self.cli("cycle", "--wave", WAVE)
        run = self.queue()["runs"][0]
        self.finish_entry(self.hub, run["entry"], 0, None)
        write(self.hub / (run["artifact"] + ".offloaded.json"), "{}")
        self.cli("cycle", "--wave", WAVE)
        self.assertEqual(self.queue()["runs"][0]["state"], "done")

    def test_a_reserved_exit_blames_the_lane_and_requeues_the_run(self) -> None:
        self.register()
        self.cli("cycle", "--wave", WAVE)
        run = self.queue()["runs"][2]
        self.finish_entry(self.peer, run["entry"], 87, None)
        self.cli("cycle", "--wave", WAVE)
        after = self.queue()["runs"][2]
        self.assertEqual(after["exclude"], ["peer"])
        self.assertNotEqual((after.get("lane") or {}).get("rig"), "peer")
        stopped = [r for r in supervisor.Wave(self.hub, WAVE).ledger() if r["event"] == "lane-stopped"]
        self.assertEqual((stopped[0]["rc"], stopped[0]["reason"]), (87, "environment drift"))

    def test_eligibility_honours_pins_data_needs_and_vram(self) -> None:
        self.register(run_extra={0: {"needs": ["hf:imagenet"]}, 1: {"min_vram_mib": 20000}})
        board.write_json(self.base / "board" / "rigs" / "peer.json", {"gpus": [{"index": 0, "memory_total_mib": 12000}]})
        board.write_json(self.base / "board" / "rigs" / "hub.json", {"gpus": [{"index": 0, "memory_total_mib": 24000}]})
        self.assertEqual(self.cli("queue", "pin", "--wave", WAVE, "--run", "model=m2", "--rigs", "peer", "--reason", "test")[0], 0)
        self.assertEqual(self.cli("queue", "pin", "--wave", WAVE, "--run", "model=m2", "--rigs", "elsewhere", "--reason", "x")[0], 2)
        self.cli("cycle", "--wave", WAVE)
        queue = self.queue()
        by_id = {run["id"]: run for run in queue["runs"]}
        self.assertEqual(by_id["model=m0"]["state"], "queued")  # its data is ready nowhere yet
        self.assertEqual(by_id["model=m1"]["lane"]["rig"], "hub")  # 12 GB card is too small
        self.assertEqual(by_id["model=m2"]["lane"]["rig"], "peer")
        self.cli("data", "mark-ready", "--wave", WAVE, "--rig", "peer", "--tag", "hf:imagenet")
        self.assertEqual(self.queue()["ready"]["peer"], ["hf:imagenet"])

    def test_a_blocked_lane_is_left_to_the_agent(self) -> None:
        self.register()
        with mock.patch.object(supervisor.Cycle, "verify_lane", return_value="revision: drift"):
            self.cli("cycle", "--wave", WAVE)
        queue = self.queue()
        self.assertEqual(set(queue["blocked_lanes"]), {"hub:0", "peer:0"})
        self.assertFalse((self.base / "tmux-sessions").exists())
        self.cli("lane", "unblock", "--wave", WAVE, "--rig", "hub", "--gpu", "0", "--reason", "fixed")
        self.cli("cycle", "--wave", WAVE)
        self.assertIn(f"proj_000_exp_{WAVE}_hub_gpu0", (self.base / "tmux-sessions").read_text())

    def test_table_is_one_row_per_gpu_and_opens_with_the_written_time(self) -> None:
        self.register()
        self.cli("cycle", "--wave", WAVE)
        run = self.queue()["runs"][0]
        lane = self.lane(self.hub)
        (lane / "queue" / run["entry"]).rename(lane / "running" / run["entry"])
        now = supervisor.iso(supervisor.now())
        write(self.hub / run["status_path"], json.dumps({"state": "running", "wave_id": WAVE, "heartbeat": now, "elapsed_s": 600, "progress": "epoch 5/10", "progress_completed": 5, "progress_total": 10}))
        self.cli("ask", "--wave", WAVE, "--text", "use behemoth gpu 5?", "--meanwhile", "gpu 5 stays unused")
        self.cli("decide", "--wave", WAVE, "kept m1 on hub: peer is slower than its weight")
        code, out, err = self.cli("table", "--wave", WAVE, "--cycle")
        self.assertEqual(code, 0, err)
        lines = out.splitlines()
        self.assertRegex(lines[0], r"^Status written \d{4}-\d{2}-\d{2} at \d{2}:\d{2} — wave `" + WAVE)
        self.assertIn("(opus/high)", lines[0])
        self.assertIn("| rig | gpu | state | run | progress | hb | run ETA | next | done | lane free | basis |", out)
        rows = [line for line in lines if line.startswith("| hub ") or line.startswith("| peer ")]
        self.assertEqual(len(rows), 2)
        self.assertIn("█████░░░░░  50%", rows[0])
        self.assertIn("structured progress", rows[0])
        self.assertIn("[q1] use behemoth gpu 5?", out)
        self.assertIn("[opus/high] kept m1 on hub", out)
        self.assertIn("wave ETA", lines[0])

    def test_wake_runs_the_declared_profile_once_and_records_it(self) -> None:
        self.register()
        wave = supervisor.Wave(self.hub, WAVE)
        settings = supervisor.load_settings(self.registry)
        self.assertEqual(supervisor.wake_due(wave, settings), "scheduled tick")
        code, out, _ = self.cli("wake", "--wave", WAVE, "--reason", "test")
        self.assertEqual(code, 0, out)
        log = Path(wave.runtime()["agent_last_log"]).read_text()
        self.assertIn("model=opus effort=high", log)
        self.assertIn("never ask a blocking question", log)
        self.assertIsNone(supervisor.wake_due(wave, settings))
        self.cli("ask", "--wave", WAVE, "--text", "q?", "--meanwhile", "status quo")
        self.cli("answer", "--wave", WAVE, "--id", "q1", "--text", "yes")
        self.assertEqual(supervisor.wake_due(wave, settings), "event: answer")
        tick = [r for r in wave.ledger() if r["event"] == "agent-tick"][0]
        self.assertEqual((tick["model"], tick["effort"], tick["outcome"]), ("opus", "high", "exit 0"))

    def test_steal_refuses_a_lane_the_board_does_not_give_us_and_retracts_a_buffered_run(self) -> None:
        self.register()
        self.cli("cycle", "--wave", WAVE)
        lane_file = self.base / "board" / "lanes" / "peer" / "gpu0.json"
        held = board.read_json(lane_file)
        board.write_json(lane_file, {**held, "tmux_session": "other_000_exp_20260101-000000_peer_gpu0"})
        code, _out, err = self.cli("steal", "--wave", WAVE, "--run", "model=m3", "--reason", "tail", "--confirm")
        self.assertEqual(code, 2)
        self.assertIn("not held by this wave", err)
        board.write_json(lane_file, held)
        self.assertEqual(self.cli("steal", "--wave", WAVE, "--run", "model=m3", "--reason", "tail")[0], 2)
        code, _out, err = self.cli("steal", "--wave", WAVE, "--run", "model=m3", "--reason", "tail", "--exclude-source", "--confirm")
        self.assertEqual(code, 0, err)
        run = [r for r in self.queue()["runs"] if r["id"] == "model=m3"][0]
        self.assertEqual((run["state"], run["exclude"]), ("queued", ["peer"]))
        self.assertEqual(len(list((self.lane(self.peer) / "retracted").iterdir())), 1)

    def test_cluster_takes_from_the_tail_and_an_idle_lane_pulls_a_pending_job(self) -> None:
        pool = {"hub": {"lanes": ["0"]}, "cluster": {"max_jobs": 2, "core_hours_cap": 1000, "default_walltime_s": 3600}}
        self.register(runs=4, pool=pool)
        self.cli("cycle", "--wave", WAVE)
        queue = self.queue()
        placed = {run["id"]: run["lane"]["rig"] for run in queue["runs"] if run["lane"]}
        self.assertEqual(placed, {"model=m0": "hub", "model=m1": "hub", "model=m3": "cluster", "model=m2": "cluster"})
        submitted = (self.base / "slurm" / "submitted").read_text()
        self.assertIn("--time=01:30:00", submitted)  # 3597 s x 1.5, stepped up to 15 min
        self.assertIn("LANE_RIG=cluster", submitted)
        jobs = {run["id"]: run["job_id"] for run in queue["runs"] if run["job_id"]}
        write(self.base / "slurm" / "squeue", "".join(f"{job}|{WAVE}__{name}|PENDING|0:00|1:30:00|Priority\n" for name, job in jobs.items()))
        for name in ("model=m0", "model=m1"):
            run = [r for r in self.queue()["runs"] if r["id"] == name][0]
            self.finish_entry(self.hub, run["entry"], 0, run["artifact"])
        self.cli("cycle", "--wave", WAVE)  # learns PENDING, settles the two hub runs, finds the lane idle
        cancelled = (self.base / "slurm" / "cancelled").read_text().split()
        self.assertEqual(len(cancelled), 1)
        moved = [r for r in self.queue()["runs"] if r["lane"] == {"rig": "hub", "gpu": "0"} and r["state"] == "assigned"]
        self.assertEqual(len(moved), 1)

    def test_a_timed_out_job_is_resized_from_what_it_measured(self) -> None:
        pool = {"cluster": {"max_jobs": 1, "core_hours_cap": 1000, "default_walltime_s": 3600}}
        self.register(runs=1, pool=pool)
        self.cli("cycle", "--wave", WAVE)
        run = self.queue()["runs"][0]
        write(self.base / "slurm" / "sacct", f"{run['job_id']}|{WAVE}__{run['id']}|TIMEOUT|01:30:00|0:0\n")
        write(self.cluster / run["status_path"], json.dumps({"state": "running", "wave_id": WAVE, "elapsed_s": 5400, "progress_completed": 100, "progress_total": 500}))
        self.cli("cycle", "--wave", WAVE)
        after = self.queue()["runs"][0]
        self.assertEqual(after["walltime_s"], 40500)  # 5400 s / 100 x 500 x 1.5
        self.assertIn("--time=11:15:00", (self.base / "slurm" / "submitted").read_text())

    def test_finish_refuses_a_live_wave_without_abandon(self) -> None:
        self.register()
        self.assertEqual(self.cli("finish", "--wave", WAVE, "--confirm")[0], 2)
        self.assertEqual(self.cli("finish", "--wave", WAVE, "--abandon", "--confirm")[0], 0)
        self.assertEqual(self.cli("waves")[1], "")


if __name__ == "__main__":
    unittest.main()
