from __future__ import annotations

import argparse
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
case "$*" in
  *--name=*--format=%i) name=$(echo "$*" | sed 's/.*--name=\\([^ ]*\\).*/\\1/'); awk -F'|' -v n="$name" '$2 == n {print $1}' "$FAKE_SLURM/squeue" 2>/dev/null ;;
  *--format=%j) cut -d'|' -f2 "$FAKE_SLURM/squeue" 2>/dev/null ;;
  *) cat "$FAKE_SLURM/squeue" 2>/dev/null ;;
esac
"""
FAKE_SACCT = '#!/bin/sh\ncat "$FAKE_SLURM/sacct" 2>/dev/null\n'
FAKE_SCANCEL = '#!/bin/sh\necho "$1" >> "$FAKE_SLURM/cancelled"\n'


def write(path: Path, text: str, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    if executable:
        path.chmod(path.stat().st_mode | stat.S_IEXEC)


def local_remote(machine, argv, *, check=True, input_bytes=None):
    """Every rig is a directory on this host: the shell the supervisor sends really runs."""
    return rigsync.run(argv, check=check, input_bytes=input_bytes)


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
            mock.patch.object(supervisor, "run_rigsync", side_effect=self.fake_rigsync),
            mock.patch.object(rigsync.socket, "gethostname", return_value="hub-host"),  # this host is the hub
        ]
        self.rigsync_calls: list[tuple[str, ...]] = []
        self.rigsync_failures: list[str] = []  # each entry fails one rig-sync call with that message
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(self.tmp.cleanup)

    def fake_rigsync(self, wave, settings, *argv: str) -> subprocess.CompletedProcess:
        self.rigsync_calls.append(argv)
        if self.rigsync_failures:
            return subprocess.CompletedProcess(argv, 2, "", f"rigsync: {self.rigsync_failures.pop(0)}")
        flag = "--machine" if "--machine" in argv else "--machines"
        return subprocess.CompletedProcess(argv, 0, f"[{argv[0]}-summary] {argv[argv.index(flag) + 1]}: copied 0 (0 kB)\n", "")

    def collected(self) -> list[str]:
        return [argv[argv.index("--machine") + 1] for argv in self.rigsync_calls if argv[0] == "collect"]

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

    def test_shared_rig_lanes_need_a_user_grant_beyond_owned_cards(self) -> None:
        self.registry.write_text(self.registry.read_text().replace('slurm = ["cluster"]', 'slurm = ["cluster"]\nshared = ["peer"]'))
        pool = {"hub": {"lanes": ["0"]}, "peer": {"lanes": ["0", "2"]}}
        code, _out, err = self.cli("register", "--wave", WAVE, "--manifest", str(self.manifest(pool=pool)), "--dry-run")
        self.assertNotEqual(code, 0)
        self.assertIn("not ours", err)
        path = self.manifest(pool=pool)
        payload = json.loads(path.read_text())
        payload["gpu_grants"] = {"peer": {"gpus": [2], "user_words": "", "date": "2026-10-21"}}
        path.write_text(json.dumps(payload))
        code, _out, err = self.cli("register", "--wave", WAVE, "--manifest", str(path), "--dry-run")
        self.assertIn("verbatim", err)
        payload["gpu_grants"]["peer"]["user_words"] = "Marco lent me gpu 2 for tonight"
        path.write_text(json.dumps(payload))
        code, _out, err = self.cli("register", "--wave", WAVE, "--manifest", str(path), "--confirm")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.queue()["gpu_grants"]["peer"]["gpus"], [2])

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

    def test_check_refuses_a_prompt_swallowed_by_a_variadic_option(self) -> None:
        self.write_registry(agent=True)
        text = self.registry.read_text()
        swallowed = '["claude", "-p", "--model", "{model}", "--allowedTools", "Read,Bash", "{prompt}"]'
        self.registry.write_text(text.replace('["sh", "-c", "echo model={model} effort={effort}; echo \\"$0\\"", "{prompt}"]', swallowed))
        code, _out, err = self.cli("check")
        self.assertEqual(code, 2)
        self.assertIn("--allowedTools", err)
        fixed = '["claude", "-p", "{prompt}", "--model", "{model}", "--allowedTools", "Read,Bash"]'
        self.registry.write_text(text.replace('["sh", "-c", "echo model={model} effort={effort}; echo \\"$0\\"", "{prompt}"]', fixed))
        self.assertEqual(supervisor.load_settings(self.registry).agent.command[2], "{prompt}")
        self.assertIsNone(supervisor._prompt_swallowed_by(("x", "--allowedTools", "Read", "--", "{prompt}")))
        self.assertIsNone(supervisor._prompt_swallowed_by(("x", "--allowedTools", "Read", "--model", "m", "{prompt}")))

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
        code, out, err = self.cli("table", "--wave", WAVE, "--cycle", "--full")
        self.assertEqual(code, 0, err)
        lines = out.splitlines()
        self.assertRegex(lines[0], r"^Status written \d{4}-\d{2}-\d{2} at \d{2}:\d{2} — wave `" + WAVE)
        self.assertIn("(opus/high)", lines[0])
        self.assertIn("| rig | gpu | state | run | progress | hb | run ETA | next | done | lane free | basis |", out)
        rows = [line for line in lines if line.startswith("| hub ") or line.startswith("| peer ")]
        self.assertEqual(len(rows), 2)
        self.assertIn("🟨🟨🟨🟨🟨⬜⬜⬜⬜⬜  50%", rows[0])
        self.assertIn("structured progress", rows[0])
        self.assertIn("[q1] use behemoth gpu 5?", out)
        self.assertIn("[opus/high] kept m1 on hub", out)
        self.assertIn("wave ETA", lines[0])

    def test_brief_printout_leads_with_the_eta_and_one_line_per_rig(self) -> None:
        self.register()
        self.cli("cycle", "--wave", WAVE)
        self.cli("ask", "--wave", WAVE, "--text", "use behemoth gpu 5?", "--meanwhile", "gpu 5 stays unused")
        code, out, err = self.cli("table", "--wave", WAVE, "--cycle")
        self.assertEqual(code, 0, err)
        lines = out.splitlines()
        self.assertRegex(lines[0], r"^Status written \d{4}-\d{2}-\d{2} at \d{2}:\d{2} — ETA ")
        self.assertRegex(lines[0], r"· 0/\d+ done$")
        self.assertTrue(lines[1].startswith("⬜⬜⬜⬜⬜⬜⬜⬜⬜⬜"), lines[1])
        rig_lines = [line for line in lines if line.startswith("  hub ") or line.startswith("  peer ")]
        self.assertEqual(len(rig_lines), 2)
        self.assertIn("1 GPU ·", rig_lines[0])
        self.assertIn("⚠ needs you [q1] use behemoth gpu 5? — meanwhile: gpu 5 stays unused", out)
        self.assertNotIn("| rig | gpu |", out)

    def test_brief_counts_a_slurm_rigs_finished_jobs_and_reports_what_is_in_flight(self) -> None:
        at = supervisor.now()
        lane = {"kind": "lane", "rig": "hub", "gpu": "0", "state": "running", "run": "m9", "done": 3, "lane_free_at": supervisor.iso(at + supervisor.timedelta(hours=1)), "problem": None}
        job = lambda state: {"kind": "job", "rig": "cluster", "gpu": "job 1", "state": state, "run": "m1", "done": 0, "lane_free_at": None, "problem": None}
        snapshot = {
            "counts": {"done": 10, "failed": 0}, "total": 20, "wave_eta": supervisor.iso(at + supervisor.timedelta(hours=2)), "wave_eta_basis": "",
            "lanes": [lane, job("running"), job("running"), job("pending (Priority)")], "failed": [], "questions": [],
            "rigs": {"cluster": {"kind": "slurm", "done": 7}, "hub": {"kind": "lanes", "done": 3}, "spare": {"kind": "slurm", "done": 0}},
        }
        lines = supervisor.render_brief(snapshot).splitlines()
        self.assertIn("  cluster  3 jobs · 7 done · 2 running, 1 waiting", lines)
        self.assertIn("  spare    0 jobs · 0 done · no jobs in flight", lines)
        self.assertTrue(any(line.startswith("  hub      1 GPU · 3 done · free ") and line.endswith("← last to finish") for line in lines))

    def test_brief_view_is_the_printout_as_data(self) -> None:
        at = supervisor.now()
        free = supervisor.iso(at + supervisor.timedelta(hours=1))
        snapshot = {
            "counts": {"done": 2, "failed": 1}, "total": 4, "wave_eta": supervisor.iso(at + supervisor.timedelta(hours=2)), "wave_eta_basis": "feeder simulation",
            "lanes": [{"kind": "lane", "rig": "hub", "gpu": "0", "state": "running", "run": "m1", "done": 2, "lane_free_at": free, "problem": None},
                      {"kind": "lane", "rig": "peer", "gpu": "0", "state": "blocked", "run": None, "done": 0, "lane_free_at": None, "problem": "exit 87"}],
            "failed": [{"run": "m3", "note": "exit 1"}], "questions": [{"id": "q1", "text": "more cards?", "meanwhile": "as is"}],
            "rigs": {"hub": {"kind": "lanes", "done": 2}, "peer": {"kind": "lanes", "done": 0}},
            "certs": {"cluster": {"expires_at": supervisor.iso(at + supervisor.timedelta(minutes=5))}},
        }
        view = supervisor.brief_view(snapshot, at)
        self.assertEqual((view["eta"]["kind"], view["eta"]["basis"]), ("eta", "feeder simulation"))
        self.assertEqual([(r["rig"], r["free_at"], r["last_to_finish"]) for r in view["rigs"]], [("hub", free, True), ("peer", None, False)])
        self.assertEqual([w["kind"] for w in view["warnings"]], ["lane", "failed", "cert", "question"])
        brief = supervisor.render_brief(snapshot)
        for item in view["warnings"]:
            self.assertIn(f"⚠ {item['line']}", brief, "the page and the chat print the same warnings")

    def test_running_cluster_jobs_bound_the_wave_eta(self) -> None:
        self.assertEqual(supervisor.with_cluster_jobs(0.0, "feeder simulation", {"c job 1": 3600.0}), (3600.0, "running cluster jobs' progress"))
        self.assertEqual(supervisor.with_cluster_jobs(7200.0, "feeder simulation", {"c job 1": 3600.0}), (7200.0, "feeder simulation"))
        self.assertEqual(supervisor.with_cluster_jobs(0.0, "feeder simulation", {"c job 1": None})[0], None)
        self.assertEqual(supervisor.with_cluster_jobs(None, "lane ETA unavailable: x", {"c job 1": 10.0}), (None, "lane ETA unavailable: x"))

    def test_a_finished_wave_leaves_a_pointer_for_the_board_viewer(self) -> None:
        self.register()
        self.assertEqual(self.cli("finish", "--wave", WAVE, "--abandon", "--confirm")[0], 0)
        board_config = board.load_config(self.registry)
        self.assertFalse(supervisor.index_path(board_config, "proj", WAVE).exists())
        pointer = board.read_json(supervisor.finished_dir(board_config) / f"proj__{WAVE}.json")
        self.assertEqual((pointer["project_root"], pointer["wave_id"], pointer["experiment"]), (str(self.hub.resolve()), WAVE, "000_exp"))
        self.assertTrue(pointer["finished_at"])
        self.assertEqual([w.wave_id for w in supervisor.active_waves(board_config)], [], "a finished pointer is never supervised")

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

    def hold_for_another_wave(self, rig: str, gpu: str = "0") -> Path:
        other = argparse.Namespace(
            rig=rig, gpu=gpu, project="other", experiment="000_exp", wave="20260101-000000",
            project_root="/elsewhere", runs_total=1, tmux_session=f"other_000_exp_20260101-000000_{rig}_gpu{gpu}",
        )
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(board.claim(board.load_config(self.registry), other), 0)
        return self.base / "board" / "lanes" / rig / f"gpu{gpu}.json"

    def test_a_refused_claim_keeps_the_lane_out_until_the_board_frees_it(self) -> None:
        held = self.hold_for_another_wave("peer")
        self.register()
        self.cli("cycle", "--wave", WAVE)
        self.cli("cycle", "--wave", WAVE)
        sessions = (self.base / "tmux-sessions").read_text() if (self.base / "tmux-sessions").exists() else ""
        self.assertNotIn("_peer_gpu0", sessions)
        queue = self.queue()
        self.assertFalse([r for r in queue["runs"] if (r["lane"] or {}).get("rig") == "peer"])
        self.assertIn("peer:0", queue["waiting_lanes"])
        events = [r["event"] for r in supervisor.Wave(self.hub, WAVE).ledger()]
        self.assertEqual(events.count("claim-refused"), 1, "a standing refusal is reported once")
        self.assertEqual(board.read_json(held)["project"], "other")
        snapshot = board.read_json(supervisor.Wave(self.hub, WAVE).snapshot_path)
        self.assertIn("waiting for board", [row["state"] for row in snapshot["lanes"] if row["kind"] == "lane"])
        held.unlink()
        self.cli("cycle", "--wave", WAVE)
        queue = self.queue()
        self.assertNotIn("peer:0", queue["waiting_lanes"])
        self.assertTrue([r for r in queue["runs"] if (r["lane"] or {}).get("rig") == "peer"])
        self.assertEqual(board.read_json(held)["wave_id"], WAVE)

    def test_an_alive_lane_the_board_gives_to_another_wave_is_not_fed(self) -> None:
        self.register()
        self.cli("cycle", "--wave", WAVE)
        lane_file = self.base / "board" / "lanes" / "peer" / "gpu0.json"
        lane_file.unlink()
        self.hold_for_another_wave("peer")
        before = [r["id"] for r in self.queue()["runs"] if (r["lane"] or {}).get("rig") == "peer"]
        for run in [r for r in self.queue()["runs"] if (r["lane"] or {}).get("rig") == "peer"]:
            self.finish_entry(self.peer, run["entry"], 0, run["artifact"])
        self.cli("cycle", "--wave", WAVE)
        fed = [r["id"] for r in self.queue()["runs"] if (r["lane"] or {}).get("rig") == "peer" and r["state"] == "assigned"]
        self.assertTrue(before)
        self.assertEqual(fed, [])
        self.assertEqual(board.read_json(lane_file)["project"], "other")

    def test_finish_releases_only_the_lanes_this_wave_holds(self) -> None:
        self.register()
        self.cli("cycle", "--wave", WAVE)
        lane_file = self.base / "board" / "lanes" / "peer" / "gpu0.json"
        lane_file.unlink()
        self.hold_for_another_wave("peer")
        self.assertEqual(self.cli("finish", "--wave", WAVE, "--abandon", "--confirm")[0], 0)
        self.assertEqual(board.read_json(lane_file)["project"], "other")
        self.assertFalse((self.base / "board" / "lanes" / "hub" / "gpu0.json").exists())
        self.assertTrue(self.queue()["finished_at"], "rig-sync prune reads finished_at")

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

    def test_a_job_slurm_has_but_the_queue_lost_is_adopted_not_resubmitted(self) -> None:
        pool = {"cluster": {"max_jobs": 1, "core_hours_cap": 1000, "default_walltime_s": 3600}}
        self.register(runs=1, pool=pool)
        run = self.queue()["runs"][0]
        write(self.base / "slurm" / "squeue", f"777|{WAVE}__{run['id']}|PENDING|0:00|1:30:00|Priority\n")
        self.cli("cycle", "--wave", WAVE)
        after = self.queue()["runs"][0]
        self.assertEqual((after["state"], after["job_id"]), ("assigned", 777))
        self.assertFalse((self.base / "slurm" / "submitted").exists())
        events = [record["event"] for record in supervisor.Wave(self.hub, WAVE).ledger()]
        self.assertIn("job-adopted", events)
        self.assertNotIn("claim-refused", events)

    def test_pool_raises_a_cluster_cap_and_resumes_submission(self) -> None:
        pool = {"cluster": {"max_jobs": 2, "core_hours_cap": 1, "default_walltime_s": 3600}}
        self.register(runs=2, pool=pool)
        self.cli("cycle", "--wave", WAVE)
        self.assertTrue(self.queue().get("budget_notified"))
        self.assertFalse([r for r in self.queue()["runs"] if r["job_id"]])
        self.assertEqual(self.cli("pool", "--wave", WAVE, "--rig", "hub", "--core-hours-cap", "9", "--reason", "x")[0], 2)
        code, out, err = self.cli("pool", "--wave", WAVE, "--rig", "cluster", "--core-hours-cap", "1000", "--reason", "user: raise")
        self.assertEqual(code, 0, err)
        self.assertIn("1.0 -> 1000.0", out)
        self.assertNotIn("budget_notified", self.queue())
        self.cli("cycle", "--wave", WAVE)
        self.assertEqual(len([r for r in self.queue()["runs"] if r["job_id"]]), 2)
        events = [record["event"] for record in supervisor.Wave(self.hub, WAVE).ledger()]
        self.assertIn("pool-cap", events)

    def test_a_cluster_without_a_core_hour_cap_submits_up_to_its_job_cap(self) -> None:
        pool = {"cluster": {"max_jobs": 2, "default_walltime_s": 3600}}
        self.register(runs=2, pool=pool)
        self.cli("cycle", "--wave", WAVE)
        self.assertFalse(self.queue().get("budget_notified"))
        self.assertEqual(len([r for r in self.queue()["runs"] if r["job_id"]]), 2)

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

    def test_every_cycle_collects_from_each_reachable_rig_but_the_hub(self) -> None:
        pool = {"hub": {"lanes": ["0"]}, "peer": {"lanes": ["0"]}, "cluster": {"max_jobs": 1, "core_hours_cap": 1000, "default_walltime_s": 3600}}
        self.register(pool=pool)
        code, _out, err = self.cli("cycle", "--wave", WAVE)
        self.assertEqual(code, 0, err)
        self.assertEqual(sorted(self.collected()), ["cluster", "peer"])
        call = next(argv for argv in self.rigsync_calls if argv[0] == "collect")
        self.assertEqual(call[1:3], ("--wave", WAVE))
        self.assertIn("--confirm", call)
        last = self.queue()["collect_last"]
        self.assertTrue(last["peer"]["ok"])
        self.assertIn("[collect-summary] peer", last["peer"]["summary"])
        snapshot = board.read_json(supervisor.Wave(self.hub, WAVE).snapshot_path)
        self.assertEqual(sorted(snapshot["collect"]), ["cluster", "peer"])

    def test_a_failing_collect_is_one_event_and_never_breaks_the_cycle(self) -> None:
        self.register()
        self.rigsync_failures = ["identity differs", "identity differs"]
        for _ in range(2):
            code, _out, err = self.cli("cycle", "--wave", WAVE)
            self.assertEqual(code, 0, err)
        events = [r for r in supervisor.Wave(self.hub, WAVE).ledger() if r["event"] == "collect-failed"]
        self.assertEqual(len(events), 1, "the same problem twice is one event")
        self.assertEqual(events[0]["rig"], "peer")
        self.assertIn("identity differs", self.queue()["collect_last"]["peer"]["problem"])
        self.assertEqual(sum(r["state"] == "assigned" for r in self.queue()["runs"]), 4, "lanes were still fed")

    def test_the_wave_turns_terminal_only_after_a_final_collect_from_every_rig(self) -> None:
        self.register(runs=1, pool={"peer": {"lanes": ["0"]}})
        self.cli("cycle", "--wave", WAVE)
        run = self.queue()["runs"][0]
        self.finish_entry(self.peer, run["entry"], 0, run["artifact"])
        self.rigsync_calls.clear()
        self.rigsync_failures = ["ssh connection lost"]  # the per-cycle pass fails; the final one retries
        self.cli("cycle", "--wave", WAVE)
        self.assertEqual(self.collected(), ["peer", "peer"])
        terminal = [r for r in supervisor.Wave(self.hub, WAVE).ledger() if r["event"] == "wave-terminal"]
        self.assertEqual(len(terminal), 1)
        self.assertEqual(terminal[0]["collect_failed"], [])
        self.assertTrue(self.queue()["collect_last"]["peer"]["ok"])

    def test_finish_collects_from_every_rig_but_the_hub(self) -> None:
        self.register()
        self.rigsync_calls.clear()
        code, out, err = self.cli("finish", "--wave", WAVE, "--abandon", "--confirm")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.collected(), ["peer"])
        self.rigsync_calls.clear()
        self.assertEqual(self.cli("finish", "--wave", WAVE, "--abandon", "--dry-run")[0], 0)
        self.assertEqual(self.collected(), [], "a dry run collects nothing")

    def test_finish_refuses_a_live_wave_without_abandon(self) -> None:
        self.register()
        self.assertEqual(self.cli("finish", "--wave", WAVE, "--confirm")[0], 2)
        self.assertEqual(self.cli("finish", "--wave", WAVE, "--abandon", "--confirm")[0], 0)
        self.assertEqual(self.cli("waves")[1], "")

    # ── closing the loop without a chat ──

    def terminal_wave(self) -> None:
        self.register(runs=2, pool={"peer": {"lanes": ["0"]}})
        for _ in range(3):
            self.cli("cycle", "--wave", WAVE)
            for run in self.queue()["runs"]:
                if run["state"] in ("assigned", "running"):
                    self.finish_entry(self.peer, run["entry"], 0, run["artifact"])
        self.cli("cycle", "--wave", WAVE)
        self.assertTrue(self.queue()["finished_at"])

    def followups(self) -> list[dict]:
        return [record for _path, record in supervisor.followup_records(board.load_config(self.registry))]

    def test_finishing_a_wave_prunes_every_machine_and_queues_its_analysis(self) -> None:
        self.terminal_wave()
        self.rigsync_calls.clear()
        code, out, err = self.cli("finish", "--wave", WAVE, "--confirm")
        self.assertEqual(code, 0, err)
        pruned = sorted(argv[argv.index("--machines") + 1] for argv in self.rigsync_calls if argv[0] == "prune")
        self.assertEqual(pruned, ["hub", "peer"], "each machine on its own, the hub included")
        [record] = self.followups()
        self.assertEqual((record["kind"], record["status"], record["wave_id"]), ("analyze", "pending", WAVE))
        self.assertIn("2 done, 0 failed", record["text"])
        self.assertIn("queued follow-up", out)

    def test_an_abandoned_wave_is_neither_pruned_nor_analyzed(self) -> None:
        self.register()
        self.rigsync_calls.clear()
        self.assertEqual(self.cli("finish", "--wave", WAVE, "--abandon", "--confirm")[0], 0)
        self.assertFalse([argv for argv in self.rigsync_calls if argv[0] == "prune"])
        self.assertEqual(self.followups(), [])

    def test_fix_followups_stop_after_two_automatic_rounds(self) -> None:
        path = self.manifest()
        payload = json.loads(path.read_text())
        payload["fix_depth"] = 2
        path.write_text(json.dumps(payload))
        self.assertEqual(self.cli("register", "--wave", WAVE, "--manifest", str(path), "--confirm")[0], 0)
        code, _out, err = self.cli("followup", "--wave", WAVE, "--kind", "fix", "--text", "probe.py clobbers metrics.json")
        self.assertEqual(code, 2)
        self.assertIn("goes to the user", err)
        self.assertEqual(self.cli("followup", "--wave", WAVE, "--kind", "analyze", "--text", "partial look")[0], 0)

    def test_a_fix_followup_carries_the_next_depth_into_its_prompt(self) -> None:
        self.register()
        self.assertEqual(self.cli("followup", "--wave", WAVE, "--kind", "fix", "--text", "hydra over-quotes optim.betas")[0], 0)
        [record] = self.followups()
        prompt = supervisor.followup_prompt(record)
        self.assertIn("`fix_depth` = 1", prompt)
        self.assertIn("hydra over-quotes optim.betas", prompt)
        self.assertIn("never change what the experiment measures", prompt)

    def test_the_service_runs_followups_one_per_project_and_keeps_the_report(self) -> None:
        self.register()
        board_config = board.load_config(self.registry)
        settings = supervisor.load_settings(self.registry)
        queue = self.queue()
        wave = supervisor.Wave(self.hub, WAVE)
        first = supervisor.enqueue_followup(board_config, wave, queue, "analyze", "5 done, 0 failed")
        second = supervisor.enqueue_followup(board_config, wave, queue, "fix", "a bug")
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(self.cli("followup-report", "--id", first["id"], "--text", "loss falls with lr; http://x/p.html")[0], 0)
        threads: dict = {}
        supervisor.start_followups(board_config, settings, threads)
        [thread] = threads.values()
        thread.join(30)
        records = {r["id"]: r for r in self.followups()}
        self.assertEqual(records[first["id"]]["status"], "done")
        self.assertEqual(records[first["id"]]["report"], "loss falls with lr; http://x/p.html")
        self.assertEqual(records[second["id"]]["status"], "pending", "one follow-up at a time per project")
        self.assertIn("Unattended follow-up", Path(records[first["id"]]["log"]).read_text())

    def test_an_interrupted_followup_runs_again(self) -> None:
        self.register()
        board_config = board.load_config(self.registry)
        record = supervisor.enqueue_followup(board_config, supervisor.Wave(self.hub, WAVE), self.queue(), "analyze", "x")
        path, _ = supervisor.find_followup(board_config, record["id"])
        board.write_json(path, {**record, "status": "running"})
        supervisor.requeue_interrupted_followups(board_config)
        self.assertEqual(board.read_json(path)["status"], "pending")

    def test_lane_add_admits_an_owned_card_and_a_shared_one_only_with_the_users_words(self) -> None:
        self.registry.write_text(self.registry.read_text().replace('slurm = ["cluster"]', 'slurm = ["cluster"]\nshared = ["peer"]'))
        self.register()
        code, _out, err = self.cli("lane", "add", "--wave", WAVE, "--rig", "peer", "--gpu", "3", "--reason", "free on the board")
        self.assertEqual(code, 2)
        self.assertIn("not ours", err)
        code, _out, err = self.cli("lane", "add", "--wave", WAVE, "--rig", "hub", "--gpu", "1", "--reason", "free on the board")
        self.assertEqual(code, 0, err)
        code, _out, err = self.cli("lane", "add", "--wave", WAVE, "--rig", "peer", "--gpu", "3", "--reason", "user: add gpu3",
                                   "--user-words", "Marco lent me gpu 3 until Friday")
        self.assertEqual(code, 0, err)
        queue = self.queue()
        self.assertEqual(queue["pool"]["hub"]["lanes"], ["0", "1"])
        self.assertEqual(queue["gpu_grants"]["peer"]["gpus"], [3])
        self.assertEqual(queue["gpu_grants"]["peer"]["user_words"], "Marco lent me gpu 3 until Friday")
        self.assertEqual(self.cli("lane", "add", "--wave", WAVE, "--rig", "hub", "--gpu", "0,1", "--reason", "x")[0], 2)

    def test_a_lane_waiting_for_the_board_never_hides_the_wave_eta(self) -> None:
        self.hold_for_another_wave("peer")
        self.register()
        self.cli("cycle", "--wave", WAVE)
        snapshot = board.read_json(supervisor.Wave(self.hub, WAVE).snapshot_path)
        self.assertIsNotNone(snapshot["wave_eta"], snapshot["wave_eta_basis"])
        self.assertTrue(snapshot["registered_at"], "the viewer shows how long ago the wave launched")
        events = [r["event"] for r in supervisor.Wave(self.hub, WAVE).ledger()]
        self.assertNotIn("claim-refused", [e for e in events if e in supervisor.URGENT_EVENTS], "waiting needs no agent")

    def test_a_certificate_is_announced_before_and_when_it_expires_never_asked(self) -> None:
        self.registry.write_text(self.registry.read_text().replace('gpus = "job"', 'gpus = "job"\nssh_cert = "~/.ssh/id_cluster-cert.pub"'))
        pool = {"hub": {"lanes": ["0"]}, "cluster": {"max_jobs": 1, "default_walltime_s": 3600}}
        self.register(pool=pool)
        soon = supervisor.now() + supervisor.timedelta(minutes=30)
        with mock.patch.object(supervisor, "cert_expiry", return_value=soon):
            self.cli("cycle", "--wave", WAVE)
            self.cli("cycle", "--wave", WAVE)
        events = [r["event"] for r in supervisor.Wave(self.hub, WAVE).ledger()]
        self.assertEqual(events.count("cert-expiring"), 1)
        brief = self.cli("table", "--wave", WAVE)[1]
        self.assertIn("cluster: SSH certificate expires", brief)
        past = supervisor.now() - supervisor.timedelta(minutes=1)
        with mock.patch.object(supervisor, "cert_expiry", return_value=past):
            self.cli("cycle", "--wave", WAVE)
        events = [r["event"] for r in supervisor.Wave(self.hub, WAVE).ledger()]
        self.assertEqual(events.count("cert-expired"), 1)
        self.assertFalse({"cert-expiring", "cert-expired"} & supervisor.URGENT_EVENTS, "a renewal is a notice, never an agent wake-up")
        self.assertIn("resumes by itself", self.cli("table", "--wave", WAVE)[1])

    def test_notices_reach_the_phone_once_even_from_two_waves(self) -> None:
        sink = self.base / "notices"
        self.registry.write_text(self.registry.read_text().replace(
            "cycle_every = 60", f'cycle_every = 60\nnotify_command = ["sh", "-c", "echo \\"$0\\" >> {sink}", "{{text}}"]'))
        settings = supervisor.load_settings(self.registry)
        sent: dict = {}
        expiring = {"event": "cert-expiring", "rig": "cluster", "expires_at": supervisor.iso(supervisor.now())}
        for wave_id in (WAVE, "20260921-093001"):
            supervisor.notify_events(supervisor.Wave(self.hub, wave_id), settings, [expiring, {"event": "run-failed"}], sent)
        lines = sink.read_text().splitlines()
        self.assertEqual(sum("SSH certificate" in line for line in lines), 1)
        self.assertEqual(sum("1 run(s) failed" in line for line in lines), 2, "per wave")

    # ── talking with the agents from the phone ──

    def converse_registry(self, resume: str = '["sh", "-c", "echo RESUMED {session}; echo \\"$0\\"", "{prompt}"]') -> Path:
        sink = self.base / "notices.jsonl"
        text = self.registry.read_text().replace(
            'command = ["sh", "-c", "echo model={model} effort={effort}; echo \\"$0\\"", "{prompt}"]',
            'command = ["sh", "-c", "echo NEW session={session}; echo \\"$0\\"", "{prompt}"]\n'
            f"resume_command = {resume}",
        ).replace("cycle_every = 60", "cycle_every = 60\n"
                  f'notify_command = ["sh", "-c", "printf \'%s\\\\t%s\\\\n\' \\"$0\\" \\"$1\\" >> {sink}", "{{text}}", "{{context}}"]')
        self.registry.write_text(text)
        return sink

    def notices(self, sink: Path) -> list[tuple[str, dict]]:
        lines = sink.read_text().splitlines() if sink.exists() else []
        return [(text, json.loads(context)) for text, _, context in (line.rpartition("\t") for line in lines)]

    def test_a_phone_reply_resumes_the_session_that_asked_and_always_gets_an_answer(self) -> None:
        sink = self.converse_registry()
        self.register()
        wave = supervisor.Wave(self.hub, WAVE)
        settings = supervisor.load_settings(self.registry)
        board_config = board.load_config(self.registry)
        self.assertEqual(self.cli("wake", "--wave", WAVE)[0], 0)
        session = wave.runtime()["agent_session"]
        self.assertIn(f"NEW session={session}", Path(wave.runtime()["agent_last_log"]).read_text())
        with mock.patch.dict(os.environ, {"SWEEP_SUPERVISOR_SESSION": session}):
            self.cli("ask", "--wave", WAVE, "--text", "use behemoth gpu 2?", "--meanwhile", "four lanes")
        [(text, context)] = self.notices(sink)
        self.assertIn("use behemoth gpu 2?", text)
        self.assertEqual((context["wave"], context["root"], context["session"], context["question"]), (WAVE, str(self.hub.resolve()), session, "q1"))
        self.assertFalse(context["silent"])
        earlier = supervisor.now() - supervisor.timedelta(minutes=1)  # the service picks a message up on its next cycle
        with mock.patch.object(supervisor, "now", return_value=earlier):
            code, _out, err = self.cli("tell", "--wave", WAVE, "--text", "yes, until Friday", "--session", session, "--question", "q1")
        self.assertEqual(code, 0, err)
        self.assertEqual(wave.questions()[0]["answer"], "yes, until Friday", "the answer is recorded before any agent runs")
        [(key, chat)] = supervisor.pending_chats(board_config).items()
        self.assertEqual(key, (str(self.hub.resolve()), WAVE))
        supervisor.wake(wave, settings, "phone message", chat)
        log = Path(wave.runtime()["agent_last_log"]).read_text()
        self.assertIn(f"RESUMED {session}", log)
        self.assertIn("«yes, until Friday» (answering your question q1)", log)
        self.assertIn("they grant what its Authority section reserves for the user", log)
        self.assertEqual(supervisor.pending_chats(board_config), {})
        text, context = self.notices(sink)[-1]
        self.assertIn("ended without a reply", text, "a turn that says nothing still answers the phone")
        self.assertEqual(context["session"], session)
        self.assertIsNone(supervisor.wake_due(wave, settings), "the turn already handled the answer: no second agent")

    def test_say_reaches_the_wave_thread_and_plain_messages_follow_the_last_speaker(self) -> None:
        sink = self.converse_registry()
        self.register()
        wave = supervisor.Wave(self.hub, WAVE)
        with mock.patch.dict(os.environ, {"SWEEP_SUPERVISOR_SESSION": "s-42"}):
            self.assertEqual(self.cli("say", "--wave", WAVE, "--text", "moved m3 to the 4090")[0], 0)
        [(text, context)] = self.notices(sink)
        self.assertEqual((text, context["body"], context["kind"]), (f"proj wave {WAVE}: moved m3 to the 4090", "moved m3 to the 4090", "say"))
        self.assertEqual(wave.runtime()["chat_session"], "s-42")
        self.cli("tell", "--wave", WAVE, "--text", "why?")
        [(_key, chat)] = supervisor.pending_chats(board.load_config(self.registry)).items()
        supervisor.wake(wave, supervisor.load_settings(self.registry), "phone message", chat)
        self.assertIn("RESUMED s-42", Path(wave.runtime()["agent_last_log"]).read_text())

    def test_a_session_that_cannot_be_resumed_hands_the_words_to_a_fresh_agent(self) -> None:
        self.converse_registry(resume='["sh", "-c", "exit 3", "{prompt}", "{session}"]')
        self.register()
        wave = supervisor.Wave(self.hub, WAVE)
        self.cli("wake", "--wave", WAVE)
        self.cli("tell", "--wave", WAVE, "--text", "status?", "--session", "gone")
        [(_key, chat)] = supervisor.pending_chats(board.load_config(self.registry)).items()
        supervisor.wake(wave, supervisor.load_settings(self.registry), "phone message", chat)
        fresh = sorted(wave.agent_logs.glob("*-chat-fresh.log"))
        self.assertEqual(len(fresh), 1)
        self.assertIn("Use the sweep-supervisor skill", fresh[0].read_text())
        self.assertIn("«status?»", fresh[0].read_text())

    def test_messages_queue_per_wave_and_a_finished_wave_can_still_be_told(self) -> None:
        self.register()
        board_config = board.load_config(self.registry)
        for text, session in (("a", None), ("b", None), ("c", "other")):
            argv = ["tell", "--wave", WAVE, "--text", text] + (["--session", session] if session else [])
            self.assertEqual(self.cli(*argv)[0], 0)
        [batch] = supervisor.pending_chats(board_config).values()
        self.assertEqual([record["text"] for _path, record in batch], ["a", "b"], "one turn per target session")
        self.assertEqual(self.cli("tell", "--wave", "20260921-093001", "--text", "x")[0], 2, "an unknown wave is refused")

    def test_resume_needs_a_way_to_learn_each_session(self) -> None:
        text = self.registry.read_text().replace('effort = "high"', 'effort = "high"\nresume_command = ["x", "{prompt}", "--resume", "{session}"]')
        self.registry.write_text(text)
        with self.assertRaisesRegex(supervisor.SupervisorError, "session_regex"):
            supervisor.load_settings(self.registry)
        self.registry.write_text(text.replace('effort = "high"', 'effort = "high"\nsession_regex = "session id: (\\\\S+)"', 1))
        profile = supervisor.load_settings(self.registry).agent
        self.assertEqual(profile.session_regex, "session id: (\\S+)")
        self.assertEqual(self.cli("check")[0], 0)

    def test_notices_carry_their_wave_and_only_what_needs_the_user_makes_a_sound(self) -> None:
        sink = self.converse_registry()
        settings = supervisor.load_settings(self.registry)
        expiring = {"event": "cert-expiring", "rig": "cluster", "expires_at": supervisor.iso(supervisor.now())}
        blocked = {"event": "recovery-blocked", "rig": "peer", "gpu": "0", "problem": "env drift"}
        supervisor.notify_events(supervisor.Wave(self.hub, WAVE), settings, [expiring, blocked, {"event": "run-failed"}], {})
        notices = {context["kind"]: (text, context) for text, context in self.notices(sink)}
        self.assertNotIn("wave", notices["cert-expiring"][1], "a certificate is the fleet's, not a wave's")
        self.assertEqual(notices["recovery-blocked"][1]["wave"], WAVE)
        self.assertFalse(notices["recovery-blocked"][1]["silent"])
        self.assertTrue(notices["run-failed"][1]["silent"], "the supervisor is already on it")

    # ── events that wake the agent, and the chat's watch ──

    def test_the_scheduled_tick_is_a_half_hour_safety_net_unless_the_registry_says_otherwise(self) -> None:
        self.assertEqual(supervisor.load_settings(self.registry).tick_every, 1800)
        self.registry.write_text(self.registry.read_text().replace("cycle_every = 60", "cycle_every = 60\ntick_every = 120"))
        self.assertEqual(supervisor.load_settings(self.registry).tick_every, 120)

    def test_register_points_the_chat_at_watch(self) -> None:
        code, out, err = self.cli("register", "--wave", WAVE, "--manifest", str(self.manifest()), "--confirm")
        self.assertEqual(code, 0, err)
        self.assertIn(f"NEXT: in a chat, run `watch --wave {WAVE}` as a background command", out)
        self.assertNotIn("without asking", out)

    def test_unattended_prompts_keep_their_envelope_in_the_wave_readme_and_decide(self) -> None:
        self.register()
        self.assertEqual(self.cli("followup", "--wave", WAVE, "--kind", "fix", "--text", "bug")[0], 0)
        self.assertEqual(self.cli("followup", "--wave", WAVE, "--kind", "analyze", "--text", "done")[0], 0)
        prompts = [supervisor.followup_prompt(record) for record in self.followups()]
        prompts.append(supervisor.agent_prompt(supervisor.Wave(self.hub, WAVE), "test"))
        for prompt in prompts:
            self.assertNotIn("DECISIONS.md", prompt)
            self.assertNotIn("execution-agreement", prompt)
            self.assertIn("README", prompt)
        for prompt in prompts[:2]:
            self.assertIn(f"`decide --wave {WAVE}`", prompt)

    def stale_heartbeat(self, minutes_ago: float) -> None:
        run = self.queue()["runs"][0]
        lane = self.lane(self.hub)
        if (lane / "queue" / run["entry"]).exists():
            (lane / "queue" / run["entry"]).rename(lane / "running" / run["entry"])
        beat = supervisor.iso(supervisor.now() - supervisor.timedelta(minutes=minutes_ago))
        write(self.hub / run["status_path"], json.dumps({"state": "running", "wave_id": WAVE, "heartbeat": beat, "elapsed_s": 600}))

    def events(self, name: str) -> list[dict]:
        return [r for r in supervisor.Wave(self.hub, WAVE).ledger() if r["event"] == name]

    def test_a_stale_heartbeat_wakes_the_agent_once_per_episode(self) -> None:
        self.register()
        self.cli("cycle", "--wave", WAVE)
        self.stale_heartbeat(minutes_ago=10)
        self.cli("cycle", "--wave", WAVE)
        self.cli("cycle", "--wave", WAVE)
        [event] = self.events("heartbeat-stale")
        self.assertEqual((event["rig"], event["gpu"], event["run"]), ("hub", "0", "model=m0"))
        self.assertGreaterEqual(event["heartbeat_age_s"], 590)
        self.assertIn("heartbeat-stale", supervisor.URGENT_EVENTS)
        self.assertIn("heartbeat-stale", supervisor.wake_due(supervisor.Wave(self.hub, WAVE), supervisor.load_settings(self.registry)))
        self.stale_heartbeat(minutes_ago=0)  # it beat again: the episode is over
        self.cli("cycle", "--wave", WAVE)
        self.assertEqual(len(self.events("heartbeat-stale")), 1)
        self.stale_heartbeat(minutes_ago=5)
        self.cli("cycle", "--wave", WAVE)
        self.assertEqual(len(self.events("heartbeat-stale")), 2, "a new staleness episode is news again")

    def test_an_idle_lane_with_nothing_to_take_wakes_the_agent_for_tail_stealing_once(self) -> None:
        self.register(runs=4)
        self.cli("cycle", "--wave", WAVE)
        for run in [r for r in self.queue()["runs"] if r["lane"] == {"rig": "hub", "gpu": "0"}]:
            self.finish_entry(self.hub, run["entry"], 0, run["artifact"])
        self.cli("cycle", "--wave", WAVE)
        self.cli("cycle", "--wave", WAVE)
        [event] = self.events("lane-idle-tail")
        self.assertEqual((event["rig"], event["gpu"], event["busy"]), ("hub", "0", ["peer gpu0"]))
        self.assertIn("lane-idle-tail", supervisor.URGENT_EVENTS)
        self.assertIn("lane-idle-tail", supervisor.wake_due(supervisor.Wave(self.hub, WAVE), supervisor.load_settings(self.registry)))

    def test_an_idle_lane_is_not_a_tail_while_the_queue_still_feeds_it_or_nothing_else_runs(self) -> None:
        self.register(runs=1, pool={"hub": {"lanes": ["0"]}, "peer": {"lanes": ["0"]}})
        self.cli("cycle", "--wave", WAVE)  # one run: hub takes it, peer has nothing - a tail from the start
        self.assertEqual([e["rig"] for e in self.events("lane-idle-tail")], ["peer"])
        run = self.queue()["runs"][0]
        self.finish_entry(self.hub, run["entry"], 0, run["artifact"])
        self.cli("cycle", "--wave", WAVE)  # terminal: nothing in flight anywhere, so no new tail
        self.assertEqual(len(self.events("lane-idle-tail")), 1)

    def snap(self, *, lanes=(), failed=(), questions=(), certs=None, written_ago_s: float = 0, finished_at=None) -> None:
        at = supervisor.now()
        lanes = list(lanes) or [self.row("hub", "running", "model=m0")]
        snapshot = {
            "written_at": supervisor.iso(at - supervisor.timedelta(seconds=written_ago_s)),
            "counts": {"done": 1, "failed": len(failed), "queued": 0, "assigned": 0, "running": 1, "excluded": 0}, "total": 5,
            "wave_eta": supervisor.iso(at + supervisor.timedelta(hours=1)), "wave_eta_basis": "feeder simulation",
            "finished_at": finished_at, "lanes": lanes, "failed": list(failed), "questions": list(questions),
            "rigs": {"hub": {"kind": "lanes", "done": 1}, "peer": {"kind": "lanes", "done": 0}}, "certs": certs or {},
        }
        board.write_json(supervisor.Wave(self.hub, WAVE).snapshot_path, snapshot)

    @staticmethod
    def row(rig: str, state: str, run=None, problem=None) -> dict:
        return {"kind": "lane", "rig": rig, "gpu": "0", "state": state, "run": run, "done": 0, "lane_free_at": None, "problem": problem}

    def watch(self, timeout: str = "0") -> str:
        code, out, err = self.cli("watch", "--wave", WAVE, "--poll", "1", "--timeout", timeout)
        self.assertEqual(code, 0, err)
        return out

    def test_watch_fires_once_on_each_kind_of_warning(self) -> None:
        self.register()
        board_config = board.load_config(self.registry)
        at = supervisor.now()
        soon = supervisor.iso(at + supervisor.timedelta(minutes=20))
        gone = supervisor.iso(at - supervisor.timedelta(minutes=1))
        question = {"id": "q1", "text": "use gpu 5?", "meanwhile": "unused", "status": "open"}
        steps = [
            ("stale lane", dict(lanes=[self.row("hub", "stale", "model=m0")]), "hub gpu0 stale (run model=m0)"),
            ("blocked lane", dict(lanes=[self.row("hub", "blocked", problem="revision: drift")]), "hub gpu0: revision: drift"),
            ("rig down", dict(lanes=[self.row("peer", "rig down", problem="down since 09:00")]), "peer gpu0: down since 09:00"),
            ("waiting lane", dict(lanes=[self.row("peer", "waiting for board")]), "peer gpu0 waiting for board"),
            ("failed run", dict(failed=[{"run": "model=m3", "note": "exit 1 on hub gpu0"}]), "failed: `model=m3` (exit 1 on hub gpu0)"),
            ("question", dict(questions=[question]), "needs you [q1] use gpu 5?"),
            ("cert expiring", dict(certs={"cluster": {"expires_at": soon}}), "cluster: SSH certificate expires"),
            ("cert expired", dict(certs={"cluster": {"expires_at": gone}}), "cluster: SSH certificate expired"),
            ("service silent", dict(written_ago_s=1200), "no supervisor cycle for 20m"),
        ]
        for name, snapshot, line in steps:
            with self.subTest(name):
                self.snap(**snapshot)
                out = self.watch()
                self.assertIn(f"watch: new ⚠ {line}", out)
                self.assertIn("Status written ", out, "the printout follows the reason")
                self.assertIn("watch: timeout, nothing new; run watch again", self.watch(), "an announced warning does not fire twice")
        self.snap()
        record = supervisor.enqueue_followup(board_config, supervisor.Wave(self.hub, WAVE), self.queue(), "analyze", "x")
        path, _ = supervisor.find_followup(board_config, record["id"])
        board.write_json(path, {**record, "status": "failed", "outcome": "exit 1", "log": "/tmp/f.log"})
        self.assertIn("watch: new ⚠ follow-up analyze exit 1 without a report; log /tmp/f.log", self.watch())
        self.assertIn("timeout", self.watch())

    def test_watch_fires_again_when_a_cleared_warning_comes_back(self) -> None:
        self.register()
        failed = [{"run": "model=m3", "note": None}]
        self.snap(failed=failed)
        self.assertIn("watch: new ⚠ failed: `model=m3`", self.watch())
        self.assertIn("timeout", self.watch())
        self.snap(failed=failed + [{"run": "model=m4", "note": None}])
        out = self.watch()
        self.assertIn("watch: new ⚠ failed: `model=m4`", out)
        self.assertNotIn("new ⚠ failed: `model=m3`", out, "only the new failure is the reason")
        self.snap()  # requeued: the failures cleared
        self.assertIn("timeout", self.watch())
        self.snap(failed=failed)
        self.assertIn("watch: new ⚠ failed: `model=m3`", self.watch())

    def test_watch_blocks_until_something_happens_and_survives_a_torn_snapshot(self) -> None:
        self.register()
        self.snap()
        snapshot_path = supervisor.Wave(self.hub, WAVE).snapshot_path
        ticks: list[float] = []

        def later(seconds: float) -> None:
            ticks.append(seconds)
            if len(ticks) == 1:
                snapshot_path.write_text('{"torn')  # the service caught mid-write
            elif len(ticks) == 2:
                self.snap(lanes=[self.row("hub", "stale", "model=m0")])

        with mock.patch.object(supervisor.time, "sleep", side_effect=later):
            out = self.watch(timeout="3600")
        self.assertEqual(len(ticks), 2)
        self.assertTrue(out.startswith("watch: new ⚠ hub gpu0 stale (run model=m0)\n"), out)

    def test_watch_returns_when_the_wave_turns_terminal(self) -> None:
        self.register()
        self.snap(failed=[{"run": "model=m3", "note": None}])
        self.watch()

        def finish(_seconds: float) -> None:
            self.snap(failed=[{"run": "model=m3", "note": None}], finished_at=supervisor.iso(supervisor.now()))

        with mock.patch.object(supervisor.time, "sleep", side_effect=finish):
            out = self.watch(timeout="3600")
        self.assertTrue(out.startswith("watch: wave finished\n"), out)
        self.assertIn("finished ", out)
        self.assertTrue(self.watch().startswith("watch: wave finished"), "a finished wave answers at once")

    def test_watch_times_out_with_nothing_new_and_refuses_an_unknown_wave(self) -> None:
        self.register()
        self.snap()
        out = self.watch()
        self.assertTrue(out.startswith("watch: timeout, nothing new; run watch again\n"), out)
        self.assertIn("Status written ", out)
        code, _out, err = self.cli("watch", "--wave", "20990101-000000", "--poll", "1", "--timeout", "0")
        self.assertEqual(code, 2)
        self.assertIn("not registered", err)
        self.assertEqual(self.cli("watch", "--wave", WAVE, "--poll", "0")[0], 2)

    def test_the_service_follows_a_newer_installed_release(self) -> None:
        root = self.base / "cache" / "research"
        relative = Path("skills/sweep-supervisor/scripts/supervisor.py")
        for version in ("9.5.0", "12.1.0", "13.0.0", "not-a-version"):
            write(root / version / relative, "")
        self.assertEqual(supervisor.newer_installed_copy(root / "12.1.0" / relative), root / "13.0.0" / relative)
        self.assertIsNone(supervisor.newer_installed_copy(root / "13.0.0" / relative))
        self.assertIsNone(supervisor.newer_installed_copy(SCRIPT), "a development checkout never moves")


FAKE_SSH = """#!/bin/sh
# stands in for ssh + sshd: drop the options and the host, run the one command string as sshd would
for last; do :; done
exec sh -c "$last"
"""


class ShellOverStdinTest(unittest.TestCase):
    """A probe over ~100 live runs is >128 KiB; as one argv string exec failed with E2BIG."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        bin_dir = Path(self.tmp.name) / "bin"
        write(bin_dir / "ssh", FAKE_SSH, executable=True)
        self.ssh_base = [str(bin_dir / "ssh"), "-o", "BatchMode=yes", "peer"]

    def machine(self, local: bool):
        return rigsync.Machine(name="hub" if local else "peer", ssh="peer", hostname=None, repo_path=Path(self.tmp.name), local=local)

    def big_probe(self) -> str:
        runs = [{"token": f"t{i:04d}", "artifact": f"runs/{'x' * 120}/{i}.pt", "status_path": f"runs/{'y' * 120}/{i}.json"} for i in range(400)]
        return supervisor.SLURM_PROBE.split("echo __RUNS__")[0].replace("squeue", ": squeue").replace("sacct", ": sacct") + 'echo __RUNS__; cd "$3" || exit 3; ' + supervisor.runs_section(runs) + 'read stolen; echo "args=$1|$2|$#"; echo __END__'

    def test_script_past_the_per_argument_limit_runs_on_hub_and_peer(self) -> None:
        script = self.big_probe()
        self.assertGreater(len(script.encode()), 128 * 1024 + 1024)
        for local in (True, False):
            with self.subTest(local=local), mock.patch.object(rigsync, "ssh_base", return_value=self.ssh_base):
                result = supervisor.sh(self.machine(local), script, "wave id", "2026-10-14T10:00:00", self.tmp.name)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.count("\t-\t"), 400)
                # `read` inside the script gets /dev/null, never the rest of the script
                self.assertIn("args=wave id|2026-10-14T10:00:00|3", result.stdout)
                self.assertTrue(result.stdout.rstrip().endswith("__END__"))

    def test_exit_code_and_quoted_arguments_survive(self) -> None:
        for local in (True, False):
            with self.subTest(local=local), mock.patch.object(rigsync, "ssh_base", return_value=self.ssh_base):
                result = supervisor.sh(self.machine(local), 'printf "%s|" "$@"; exit 7', "a b", "it's", "$HOME")
                self.assertEqual((result.returncode, result.stdout), (7, "a b|it's|$HOME|"))


if __name__ == "__main__":
    unittest.main()
