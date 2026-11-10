from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import os
import re
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
REGISTER_GATES = supervisor.register_gates  # setUp stands in for these; their own tests put them back

WAVE = "20260921-093000"
FIX_WAVE = "20260921-101500"  # the wave an automatic fix round dispatched to replace WAVE
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
  *--name=*) echo "squeue --name splits on commas" >&2; exit 1 ;;
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


def local_remote(machine, argv, *, check=True, input_bytes=None, timeout=None):
    """Every rig is a directory on this host: the shell the supervisor sends really runs."""
    return rigsync.run(argv, check=check, input_bytes=input_bytes, timeout=timeout)


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
            mock.patch.object(supervisor, "register_gates", return_value=[]),
            mock.patch.object(rigsync.socket, "gethostname", return_value="hub-host"),  # this host is the hub
        ]
        self.rigsync_calls: list[tuple[str, ...]] = []
        self.rigsync_failures: list[str] = []  # each entry fails one rig-sync call with that message
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(self.tmp.cleanup)

    def fake_rigsync(self, wave, settings, *argv: str, timeout: int | None = None) -> subprocess.CompletedProcess:
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
            "wave_id": extra.get("wave_id", WAVE),
            "experiment": "000_exp",
            "revision": REVISION,
            "lane_script": f"scripts/000_exp/_lanes/wave_{WAVE}/lane.sh",
            "pool": pool or {"hub": {"lanes": ["0"]}, "peer": {"lanes": ["0"]}},
            "weights": {"hub": 1.0, "peer": 0.5, "cluster": 1.0},
            "offload": extra.get("offload", []),
            "runs": entries,
            **{key: extra[key] for key in ("fix_depth", "fixes_wave") if key in extra},
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

    def test_opt_in_rig_lanes_need_a_user_grant_for_every_card(self) -> None:
        self.registry.write_text(self.registry.read_text().replace('ssh = "peer"\n', 'ssh = "peer"\nopt_in = true\n'))
        pool = {"hub": {"lanes": ["0"]}, "peer": {"lanes": ["0"]}}
        code, _out, err = self.cli("register", "--wave", WAVE, "--manifest", str(self.manifest(pool=pool)), "--dry-run")
        self.assertNotEqual(code, 0)
        self.assertIn("opt-in", err)
        path = self.manifest(pool=pool)
        payload = json.loads(path.read_text())
        payload["gpu_grants"] = {"peer": {"gpus": [0], "user_words": "yes, use gauss gpu 0 too", "date": "2026-10-26"}}
        path.write_text(json.dumps(payload))
        code, _out, err = self.cli("register", "--wave", WAVE, "--manifest", str(path), "--dry-run")
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

    def test_cycle_starts_lanes_and_feeds_each_run_to_the_lane_that_finishes_it_first(self) -> None:
        self.register()
        code, out, err = self.cli("cycle", "--wave", WAVE)
        self.assertEqual(code, 0, err)
        sessions = (self.base / "tmux-sessions").read_text().split()
        self.assertEqual(sessions, [f"proj_000_exp_{WAVE}_hub_gpu0", f"proj_000_exp_{WAVE}_peer_gpu0"])
        queue = self.queue()
        placed = {run["id"]: run["lane"]["rig"] for run in queue["runs"] if run["state"] == "assigned"}
        # peer runs at half hub's speed: it skips m3, which hub finishes sooner, and takes m4 behind m1
        self.assertEqual(placed, {"model=m0": "hub", "model=m1": "peer", "model=m2": "hub", "model=m4": "peer"})
        self.assertEqual(queue["runs"][3]["state"], "queued")
        entries = sorted(p.name for p in (self.lane(self.hub) / "queue").iterdir())
        self.assertEqual(len(entries), supervisor.BUFFER_DEPTH)
        self.assertEqual((self.lane(self.hub) / "queue" / entries[0]).read_text(), queue["runs"][0]["script"])
        held = board.read_json(self.base / "board" / "lanes" / "peer" / "gpu0.json")
        self.assertEqual(held["wave_id"], WAVE)
        # nothing changed on the rigs: a second cycle assigns nothing more
        self.cli("cycle", "--wave", WAVE)
        self.assertEqual(sum(run["state"] == "assigned" for run in self.queue()["runs"]), 4)

    def test_a_fast_lane_takes_more_runs_reclaims_a_slow_lanes_buffer_and_outcomes_follow_the_artifact(self) -> None:
        self.register()
        self.cli("cycle", "--wave", WAVE)
        first = self.queue()["runs"][0]
        self.finish_entry(self.hub, first["entry"], 0, first["artifact"])
        second = self.queue()["runs"][2]
        self.finish_entry(self.hub, second["entry"], 1, None)
        self.cli("cycle", "--wave", WAVE)
        queue = self.queue()
        states = {run["id"]: run["state"] for run in queue["runs"]}
        self.assertEqual(states["model=m0"], "done")
        self.assertEqual(states["model=m2"], "failed")
        # hub measured ~36x its weight: peer's unstarted runs come back, and m4 waits for hub, not peer
        self.assertEqual(sorted((e["run"], e["rig"], e["to_rig"]) for e in self.events("run-reclaimed")), [("model=m1", "peer", "hub"), ("model=m4", "peer", "hub")])
        self.assertEqual(len(list((self.lane(self.peer) / "retracted").iterdir())), 2)
        self.assertEqual([queue["runs"][i]["lane"] for i in (1, 3)], [{"rig": "hub", "gpu": "0"}] * 2)
        self.assertEqual(queue["runs"][4]["state"], "queued")
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
        run = self.queue()["runs"][1]
        self.finish_entry(self.peer, run["entry"], 87, None)
        self.cli("cycle", "--wave", WAVE)
        after = self.queue()["runs"][1]
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
        self.assertIn("❓ waiting on you: q1", out)
        self.assertNotIn("use behemoth gpu 5?", out, "the question's text went out once with ask; the status only names it")
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
            if item["kind"] == "question":
                self.assertIn(f"❓ waiting on you: {item['id']}", brief)
                self.assertNotIn(item["text"], brief)
            else:
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
        code, _out, err = self.cli("steal", "--wave", WAVE, "--run", "model=m4", "--reason", "tail", "--confirm")
        self.assertEqual(code, 2)
        self.assertIn("not held by this wave", err)
        board.write_json(lane_file, held)
        self.assertEqual(self.cli("steal", "--wave", WAVE, "--run", "model=m4", "--reason", "tail")[0], 2)
        code, _out, err = self.cli("steal", "--wave", WAVE, "--run", "model=m4", "--reason", "tail", "--exclude-source", "--confirm")
        self.assertEqual(code, 0, err)
        run = [r for r in self.queue()["runs"] if r["id"] == "model=m4"][0]
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

    def test_a_pending_job_is_never_cancelled_for_a_card_another_wave_holds(self) -> None:
        held = self.hold_for_another_wave("hub")
        pool = {"hub": {"lanes": ["0"]}, "cluster": {"max_jobs": 2, "core_hours_cap": 1000, "default_walltime_s": 3600}}
        self.register(runs=2, pool=pool)
        self.cli("cycle", "--wave", WAVE)
        jobs = {run["id"]: run["job_id"] for run in self.queue()["runs"]}
        self.assertTrue(all(jobs.values()), "the lane has no card, so the cluster takes both runs")
        write(self.base / "slurm" / "squeue", "".join(f"{job}|{WAVE}__{name}|PENDING|0:00|1:30:00|Priority\n" for name, job in jobs.items()))
        self.cli("cycle", "--wave", WAVE)
        self.cli("cycle", "--wave", WAVE)
        self.assertFalse((self.base / "slurm" / "cancelled").exists(), "the job would lose its place for a lane that cannot run it")
        held.unlink()
        self.cli("cycle", "--wave", WAVE)
        self.assertEqual(len((self.base / "slurm" / "cancelled").read_text().split()), 1, "the card is free now: the lane pulls one")

    def test_a_slow_card_the_plan_gives_nothing_is_left_on_the_board_for_other_waves(self) -> None:
        self.register(runs=1)  # hub finishes the one run sooner than peer (weight 0.5) ever could
        self.cli("cycle", "--wave", WAVE)
        self.assertEqual(self.queue()["runs"][0]["lane"], {"rig": "hub", "gpu": "0"})
        self.assertFalse((self.base / "board" / "lanes" / "peer" / "gpu0.json").exists())
        sessions = (self.base / "tmux-sessions").read_text() if (self.base / "tmux-sessions").exists() else ""
        self.assertNotIn("_peer_gpu0", sessions)
        self.assertNotIn("peer:0", self.queue().get("waiting_lanes", {}))

    def test_a_card_held_elsewhere_is_not_waited_for_when_the_plan_would_not_use_it(self) -> None:
        self.hold_for_another_wave("peer")
        self.register(runs=1)
        self.cli("cycle", "--wave", WAVE)
        self.assertEqual(self.events("claim-refused"), [], "nothing to wait for: hub finishes the run sooner")

    def test_unstarted_cluster_jobs_make_the_wave_eta_a_lower_bound_not_now(self) -> None:
        pool = {"cluster": {"max_jobs": 1, "default_walltime_s": 3600}}
        self.register(runs=2, pool=pool)
        self.cli("cycle", "--wave", WAVE)  # one job submitted and pending, one run still queued behind the job cap
        snapshot = board.read_json(supervisor.Wave(self.hub, WAVE).snapshot_path)
        self.assertTrue(snapshot["wave_eta_basis"].startswith(supervisor.EARLIEST_BASIS), snapshot["wave_eta_basis"])
        self.assertIn("2 cluster jobs not started", snapshot["wave_eta_basis"])
        ahead = (supervisor.parse_iso(snapshot["wave_eta"]) - supervisor.parse_iso(snapshot["written_at"])).total_seconds()
        self.assertAlmostEqual(ahead, 3600 + 3599, delta=5, msg="one slot: the two jobs run one after the other")
        self.assertIn("at the earliest", self.cli("table", "--wave", WAVE)[1])

    def test_cluster_jobs_drain_through_the_job_slots(self) -> None:
        self.assertEqual(supervisor.cluster_drain(2, [100.0], [50.0, 50.0, 50.0]), 150.0)  # slots: 100 -> 150, 0 -> 50 -> 100
        self.assertEqual(supervisor.cluster_drain(1, [], []), 0.0)
        clusters = {"c": {"slots": 1, "running": [600.0], "unstarted": [300.0]}}
        horizon, basis = supervisor.with_cluster_jobs(0.0, "feeder simulation", {"c job 1": 600.0}, clusters)
        self.assertEqual(horizon, 900.0)
        self.assertTrue(basis.startswith(supervisor.EARLIEST_BASIS))

    def test_a_rig_that_answers_and_hangs_reads_as_a_failed_command_not_a_stalled_service(self) -> None:
        machine = argparse.Namespace(name="peer", local=False)
        with mock.patch.object(rigsync, "remote", side_effect=subprocess.TimeoutExpired("ssh", supervisor.SH_TIMEOUT_S)):
            result = supervisor.sh(machine, "true")
        self.assertEqual(result.returncode, 124)
        self.assertIn("no answer within", result.stderr)
        done = supervisor.bounded([sys.executable, "-c", "import time; time.sleep(30)"], 1)
        self.assertEqual((done.returncode, done.stderr), (124, "timed out after 1s"))

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

    def test_a_legacy_job_name_with_commas_is_matched_exactly_and_adopted(self) -> None:
        pool = {"cluster": {"max_jobs": 1, "default_walltime_s": 3600}}
        self.register(runs=1, pool=pool, run_extra={0: {"id": "lr=0.1,bs=8"}})
        write(self.base / "slurm" / "squeue", f"776|{WAVE}__lr=0.1,bs=88|RUNNING|0:00|1:30:00|n1\n777|{WAVE}__lr=0.1,bs=8|PENDING|0:00|1:30:00|Priority\n")
        self.cli("cycle", "--wave", WAVE)
        self.assertEqual(self.queue()["runs"][0]["job_id"], 777)
        self.assertFalse((self.base / "slurm" / "submitted").exists())

    def test_a_superseded_cluster_job_is_cancelled_by_id_and_never_resubmitted(self) -> None:
        pool = {"cluster": {"max_jobs": 1, "core_hours_cap": 1000, "default_walltime_s": 3600}}
        self.register(runs=1, pool=pool)
        self.cli("cycle", "--wave", WAVE)
        run = self.queue()["runs"][0]
        self.assertEqual(self.cli("supersede", "--wave", WAVE, "--run", run["id"], "--by", WAVE, "--confirm")[0], 2)
        code, out, _err = self.cli("supersede", "--wave", WAVE, "--run", run["id"], "--by", FIX_WAVE, "--dry-run")
        self.assertIn(f"scancel job {run['job_id']}", out)
        self.assertFalse((self.base / "slurm" / "cancelled").exists())
        code, _out, err = self.cli("supersede", "--wave", WAVE, "--run", run["id"], "--by", FIX_WAVE, "--confirm")
        self.assertEqual(code, 0, err)
        self.assertEqual((self.base / "slurm" / "cancelled").read_text().split(), [str(run["job_id"])])
        write(self.base / "slurm" / "sacct", f"{run['job_id']}|{WAVE}__{run['id']}|CANCELLED|00:10:00|0:0\n")
        self.cli("cycle", "--wave", WAVE)
        after = self.queue()["runs"][0]
        self.assertEqual((after["state"], after["note"]), ("excluded", f"superseded by wave {FIX_WAVE}"))
        self.assertEqual(len((self.base / "slurm" / "submitted").read_text().splitlines()), 1)

    def test_a_cancelled_job_nobody_superseded_is_resubmitted_as_a_machine_fault(self) -> None:
        pool = {"cluster": {"max_jobs": 1, "core_hours_cap": 1000, "default_walltime_s": 3600}}
        self.register(runs=1, pool=pool)
        self.cli("cycle", "--wave", WAVE)
        run = self.queue()["runs"][0]
        write(self.base / "slurm" / "sacct", f"{run['job_id']}|{WAVE}__{run['id']}|CANCELLED|00:10:00|0:0\n")
        self.cli("cycle", "--wave", WAVE)
        self.assertEqual(len((self.base / "slurm" / "submitted").read_text().splitlines()), 2)

    def test_a_lane_run_is_superseded_only_after_steal_and_ends_excluded_once_killed(self) -> None:
        self.register()
        self.cli("cycle", "--wave", WAVE)
        run = [r for r in self.queue()["runs"] if (r["lane"] or {}).get("rig") == "hub"][0]
        code, _out, err = self.cli("supersede", "--wave", WAVE, "--run", run["id"], "--by", FIX_WAVE, "--confirm")
        self.assertEqual(code, 2)
        self.assertIn("steal it first", err)
        wave = supervisor.Wave(self.hub, WAVE)
        queue = wave.load()
        [target] = [r for r in queue["runs"] if r["id"] == run["id"]]
        target.update(state="running", stolen=True)  # what an in-flight steal leaves behind
        wave.save(queue)
        code, _out, err = self.cli("supersede", "--wave", WAVE, "--run", run["id"], "--by", FIX_WAVE, "--confirm")
        self.assertEqual(code, 0, err)
        self.assertEqual([r for r in self.queue()["runs"] if r["id"] == run["id"]][0]["state"], "running")
        self.finish_entry(self.hub, run["entry"], 143, None)
        self.cli("cycle", "--wave", WAVE)
        after = [r for r in self.queue()["runs"] if r["id"] == run["id"]][0]
        self.assertEqual(after["state"], "excluded")
        self.assertIn("run-superseded", [record["event"] for record in wave.ledger()])

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
        board.write_json(supervisor.Wave(self.hub, WAVE).queue_path, {"fix_depth": 1})  # the round before
        path = self.manifest(wave_id=FIX_WAVE, fix_depth=2, fixes_wave=WAVE)
        self.assertEqual(self.cli("register", "--wave", FIX_WAVE, "--manifest", str(path), "--confirm")[0], 0)
        code, _out, err = self.cli("followup", "--wave", FIX_WAVE, "--kind", "fix", "--text", "probe.py clobbers metrics.json")
        self.assertEqual(code, 2)
        self.assertIn("goes to the user", err)
        self.assertEqual(self.cli("followup", "--wave", FIX_WAVE, "--kind", "analyze", "--text", "partial look")[0], 0)

    def test_a_fix_round_names_the_wave_it_replaces(self) -> None:
        self.register()
        cases = [
            ("no link", dict(fix_depth=1), "names the earlier wave it replaces in `fixes_wave`"),
            ("link without depth", dict(fixes_wave=WAVE), "this fix round's is 1"),
            ("unknown wave", dict(fix_depth=1, fixes_wave="20260921-080000"), "not a registered wave of this project"),
            ("later wave", dict(fix_depth=1, fixes_wave="20260921-120000"), "names the earlier wave"),
            ("wrong depth", dict(fix_depth=2, fixes_wave=WAVE), "this fix round's is 1"),
        ]
        for name, extra, message in cases:
            with self.subTest(name):
                code, _out, err = self.cli("register", "--wave", FIX_WAVE, "--manifest", str(self.manifest(wave_id=FIX_WAVE, **extra)), "--dry-run")
                self.assertEqual(code, 2)
                self.assertIn(message, err)
        code, _out, err = self.cli("register", "--wave", FIX_WAVE, "--manifest", str(self.manifest(wave_id=FIX_WAVE, fix_depth=1, fixes_wave=WAVE)), "--confirm")
        self.assertEqual(code, 0, err)
        self.assertEqual(supervisor.Wave(self.hub, FIX_WAVE).load()["fixes_wave"], WAVE)
        self.assertIsNone(self.queue()["fixes_wave"])

    def test_a_fix_followup_carries_the_next_depth_into_its_prompt(self) -> None:
        self.register()
        self.assertEqual(self.cli("followup", "--wave", WAVE, "--kind", "fix", "--text", "hydra over-quotes optim.betas")[0], 0)
        [record] = self.followups()
        prompt = supervisor.followup_prompt(record)
        self.assertIn("`fix_depth` = 1", prompt)
        self.assertIn(f'`fixes_wave` = "{WAVE}"', prompt)
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

    def test_notices_reach_the_user_once_even_from_two_waves(self) -> None:
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

    # ── what a notice carries ──

    def notice_registry(self) -> Path:
        sink = self.base / "notices.jsonl"
        text = self.registry.read_text().replace(
            'command = ["sh", "-c", "echo model={model} effort={effort}; echo \\"$0\\"", "{prompt}"]',
            'command = ["sh", "-c", "echo NEW session={session}; echo \\"$0\\"", "{prompt}"]',
        ).replace("cycle_every = 60", "cycle_every = 60\n"
                  f'notify_command = ["sh", "-c", "printf \'%s\\\\t%s\\\\n\' \\"$0\\" \\"$1\\" >> {sink}", "{{text}}", "{{context}}"]')
        self.registry.write_text(text)
        return sink

    def notices(self, sink: Path) -> list[tuple[str, dict]]:
        lines = sink.read_text().splitlines() if sink.exists() else []
        return [(text, json.loads(context)) for text, _, context in (line.rpartition("\t") for line in lines)]

    def test_ask_and_say_reach_the_user_with_the_session_that_wrote_them(self) -> None:
        sink = self.notice_registry()
        self.register()
        wave = supervisor.Wave(self.hub, WAVE)
        self.assertEqual(self.cli("wake", "--wave", WAVE)[0], 0)
        session = wave.runtime()["agent_session"]
        self.assertIn(f"NEW session={session}", Path(wave.runtime()["agent_last_log"]).read_text())
        with mock.patch.dict(os.environ, {"SWEEP_SUPERVISOR_SESSION": session}):
            self.cli("ask", "--wave", WAVE, "--text", "use behemoth gpu 2?", "--meanwhile", "four lanes")
            self.assertEqual(self.cli("say", "--wave", WAVE, "--text", "moved m3 to the 4090")[0], 0)
        (asked, ask_context), (said, say_context) = self.notices(sink)
        self.assertIn("use behemoth gpu 2?", asked)
        self.assertEqual((ask_context["wave"], ask_context["root"], ask_context["session"], ask_context["question"]),
                         (WAVE, str(self.hub.resolve()), session, "q1"))
        self.assertFalse(ask_context["silent"])
        self.assertEqual((said, say_context["body"], say_context["kind"]), (f"proj wave {WAVE}: moved m3 to the 4090", "moved m3 to the 4090", "say"))
        self.assertNotIn("chat_session", wave.runtime())

    def test_notices_carry_their_wave_and_only_what_needs_the_user_makes_a_sound(self) -> None:
        sink = self.notice_registry()
        settings = supervisor.load_settings(self.registry)
        expiring = {"event": "cert-expiring", "rig": "cluster", "expires_at": supervisor.iso(supervisor.now())}
        blocked = {"event": "recovery-blocked", "rig": "peer", "gpu": "0", "problem": "env drift"}
        supervisor.notify_events(supervisor.Wave(self.hub, WAVE), settings, [expiring, blocked, {"event": "run-failed"}], {})
        notices = {context["kind"]: (text, context) for text, context in self.notices(sink)}
        self.assertNotIn("wave", notices["cert-expiring"][1], "a certificate is the fleet's, not a wave's")
        self.assertEqual(notices["recovery-blocked"][1]["wave"], WAVE)
        self.assertFalse(notices["recovery-blocked"][1]["silent"])
        self.assertTrue(notices["run-failed"][1]["silent"], "the supervisor is already on it")

    # ── the ledger cursor ──

    def backdate(self, wave, **fields: float) -> None:
        runtime = wave.runtime()
        runtime.update({key: supervisor.iso(supervisor.now() - supervisor.timedelta(seconds=seconds)) for key, seconds in fields.items()})
        wave.save_runtime(runtime)

    def test_the_default_ledger_shows_an_event_from_during_the_last_tick(self) -> None:
        self.register()
        wave = supervisor.Wave(self.hub, WAVE)
        self.registry.write_text(self.registry.read_text().replace(
            'command = ["sh", "-c", "echo model={model} effort={effort}; echo \\"$0\\"", "{prompt}"]',
            'command = ["sh", "-c", "sleep 1.1; echo \\"$0\\"", "{prompt}"]'))
        settings = supervisor.load_settings(self.registry)
        real = supervisor.run_agent

        def agent_with_an_event_meanwhile(*args, **kwargs):
            wave.log("run-failed", run="during-the-tick", rig="hub", gpu="0", rc=1)
            return real(*args, **kwargs)

        with mock.patch.object(supervisor, "run_agent", side_effect=agent_with_an_event_meanwhile), contextlib.redirect_stdout(io.StringIO()):
            supervisor.wake(wave, settings, "test")
        self.assertEqual(supervisor.wake_due(wave, settings), "event: run-failed", "the event wakes the next tick")
        self.assertIn("during-the-tick", self.cli("ledger", "--wave", WAVE)[1], "and that tick must be able to read it")

    def test_the_default_ledger_keeps_what_a_failed_tick_never_handled(self) -> None:
        self.register()
        wave = supervisor.Wave(self.hub, WAVE)
        self.assertEqual(self.cli("wake", "--wave", WAVE)[0], 0)
        wave.log("run-failed", run="behind-the-failure", rig="hub", gpu="0", rc=1)
        self.registry.write_text(self.registry.read_text().replace(
            'command = ["sh", "-c", "echo model={model} effort={effort}; echo \\"$0\\"", "{prompt}"]',
            'command = ["sh", "-c", "exit 1", "{prompt}"]'))
        self.assertEqual(self.cli("wake", "--wave", WAVE)[0], 1)
        self.assertIn("behind-the-failure", self.cli("ledger", "--wave", WAVE)[1])

    # ── which scheduled ticks run ──

    def ticked(self, *, result: str = "exit 0", ago_s: int = 2000) -> supervisor.Wave:
        """A wave whose last tick began and ended `ago_s` ago, and nothing logged since."""
        self.register()
        wave = supervisor.Wave(self.hub, WAVE)
        wave.ledger_path.write_text("")
        runtime = wave.runtime()
        runtime["agent_last_result"] = result
        wave.save_runtime(runtime)
        fields = {"agent_tick_started_at": ago_s, "agent_ticked_at": ago_s}
        if result == "exit 0":
            fields["agent_ok_started_at"] = ago_s
        self.backdate(wave, **fields)
        return wave

    def test_a_scheduled_tick_with_nothing_new_is_skipped_until_the_idle_period(self) -> None:
        wave = self.ticked()
        settings = supervisor.load_settings(self.registry)
        self.assertEqual(settings.idle_tick_every, 6 * 3600)
        self.assertIsNone(supervisor.wake_due(wave, settings))
        self.backdate(wave, agent_ticked_at=6 * 3600 + 5, agent_ok_started_at=6 * 3600 + 5)
        self.assertEqual(supervisor.wake_due(wave, settings), "idle tick")
        self.registry.write_text(self.registry.read_text().replace("cycle_every = 60", "cycle_every = 60\nidle_tick_every = 90000"))
        self.assertIsNone(supervisor.wake_due(wave, supervisor.load_settings(self.registry)), "the registry can set it")

    def test_routine_progress_is_not_news_but_a_persons_edit_and_a_budget_cap_are(self) -> None:
        wave = self.ticked()
        settings = supervisor.load_settings(self.registry)
        wave.log("agent-tick", reason="x", outcome="exit 0")
        wave.log("run-done", run="m0", rig="hub", gpu="0")
        wave.log("decide", text="nothing to do")
        self.assertIsNone(supervisor.wake_due(wave, settings), "the service settles runs; a tick's own records are not news")
        experiments = self.hub / "EXPERIMENTS.md"
        experiments.write_text("| run | state |\n")
        self.assertEqual(supervisor.wake_due(wave, settings), "scheduled tick", "a person edited EXPERIMENTS.md")
        supervisor.record_experiments_seen(wave)
        self.assertIsNone(supervisor.wake_due(wave, settings), "what a tick of this project left behind is not a person's edit")
        wave.log("budget-cap", rig="leo", spent_h=10.0, cap_h=10)
        self.assertEqual(supervisor.wake_due(wave, settings), "scheduled tick")

    def test_two_waves_of_one_project_never_tick_at_once(self) -> None:
        self.register()
        wave = supervisor.Wave(self.hub, WAVE)
        settings = supervisor.load_settings(self.registry)
        import fcntl

        with (wave.dir.parent / "tick.lock").open("a+") as sibling, contextlib.redirect_stdout(io.StringIO()) as out:
            fcntl.flock(sibling, fcntl.LOCK_EX)  # another wave of this project is ticking
            with mock.patch.object(supervisor, "run_agent") as run:
                self.assertEqual(supervisor.wake(wave, settings, "scheduled tick"), 0)
            run.assert_not_called()
        self.assertIn("another wave of this project is ticking", out.getvalue())
        self.assertEqual(supervisor.wake_due(wave, settings), "scheduled tick", "the skipped tick stays due")
        self.assertEqual(self.cli("wake", "--wave", WAVE)[0], 0)
        self.assertIsNotNone(wave.runtime().get("agent_ticked_at"))

    def test_a_failed_tick_forces_the_next_one_at_the_tick_period_not_each_cycle(self) -> None:
        wave = self.ticked(result="exit 1")
        settings = supervisor.load_settings(self.registry)
        self.assertEqual(supervisor.wake_due(wave, settings), "scheduled tick (the last one failed)")
        self.backdate(wave, agent_ticked_at=60)
        self.assertIsNone(supervisor.wake_due(wave, settings))

    def test_a_persons_edit_of_the_tracking_file_is_news_and_a_ticks_own_is_not(self) -> None:
        wave = self.ticked()
        settings = supervisor.load_settings(self.registry)
        tracking = self.hub / "EXPERIMENTS.md"
        tracking.write_text("| run | state |\n")
        written_during_the_tick = supervisor.now().timestamp() - 2100
        os.utime(tracking, (written_during_the_tick, written_during_the_tick))
        self.assertIsNone(supervisor.wake_due(wave, settings))
        os.utime(tracking)
        self.assertEqual(supervisor.wake_due(wave, settings), "scheduled tick")

    def test_a_terminal_wave_nobody_finished_keeps_its_scheduled_tick(self) -> None:
        wave = self.ticked()
        queue = self.queue()
        queue["finished_at"] = supervisor.iso(supervisor.now())
        wave.save(queue)
        self.assertEqual(supervisor.wake_due(wave, supervisor.load_settings(self.registry)), "scheduled tick")

    def test_only_a_closing_tick_gets_the_longer_limit(self) -> None:
        self.register()
        wave = supervisor.Wave(self.hub, WAVE)
        settings = supervisor.load_settings(self.registry)
        with mock.patch.object(supervisor, "run_agent", return_value=("exit 0", None)) as run, contextlib.redirect_stdout(io.StringIO()):
            supervisor.wake(wave, settings, "scheduled tick")
            queue = self.queue()
            queue["finished_at"] = supervisor.iso(supervisor.now())
            wave.save(queue)
            supervisor.wake(wave, settings, "event: wave-terminal")
        self.assertEqual([call.args[4] for call in run.call_args_list], [900, 2700])
        self.assertLess(settings.closing_timeout, supervisor.WATCH_SETTLE_S, "watch waits on this tick")

    # ── a failing agent ──

    def failing_agent(self) -> None:
        self.registry.write_text(re.sub(r'^command = .*$', lambda _m: 'command = ["sh", "-c", "exit 3", "{prompt}"]', self.registry.read_text(), flags=re.M))

    def test_two_failed_ticks_in_a_row_raise_a_warning_and_one_notice(self) -> None:
        sink = self.notice_registry()
        self.register()
        wave = supervisor.Wave(self.hub, WAVE)
        self.failing_agent()
        self.assertEqual(self.cli("wake", "--wave", WAVE)[0], 1)
        self.cli("cycle", "--wave", WAVE)
        self.assertNotIn("agent ticks are failing", self.cli("table", "--wave", WAVE)[1], "one failure may be a blip")
        self.assertEqual(self.notices(sink), [])
        self.cli("wake", "--wave", WAVE)
        self.cli("wake", "--wave", WAVE)
        self.cli("cycle", "--wave", WAVE)
        brief = self.cli("table", "--wave", WAVE)[1]
        self.assertIn("⚠ agent ticks are failing: 3 in a row, the last exit 3", brief)
        self.assertIn("agent ticks are failing", self.cli("table", "--wave", WAVE, "--full")[1])
        notices = [text for text, context in self.notices(sink) if context["kind"] == "agent-failing"]
        self.assertEqual(len(notices), 1)
        self.assertIn("2 in a row", notices[0])
        snapshot = board.read_json(wave.snapshot_path)
        keys = {item["key"] for item in supervisor.wave_warnings(wave, snapshot, supervisor.now())}
        self.assertIn("agent:ticks", keys, "a stable key, though the line counts")

    def test_a_good_tick_clears_the_warning(self) -> None:
        self.register()
        wave = supervisor.Wave(self.hub, WAVE)
        good = self.registry.read_text()
        self.failing_agent()
        self.cli("wake", "--wave", WAVE)
        self.cli("wake", "--wave", WAVE)
        self.registry.write_text(good)
        self.assertEqual(self.cli("wake", "--wave", WAVE)[0], 0)
        self.assertEqual(wave.runtime()["agent_failures"], 0)
        self.cli("cycle", "--wave", WAVE)
        self.assertNotIn("agent ticks are failing", self.cli("table", "--wave", WAVE)[1])

    def health_registry(self, rc: int, secret: str = "") -> None:
        script = self.base / "health.sh"
        write(script, f"#!/bin/sh\necho '{secret}'\necho '{secret}' >&2\nexit {rc}\n", executable=True)
        self.registry.write_text(self.registry.read_text().replace('model = "opus"', f'health_command = ["{script}"]\nmodel = "opus"'))

    def test_a_failing_health_command_warns_check_and_fails_the_dispatch_gate_without_its_output(self) -> None:
        self.health_registry(1, secret="sk-secret-token")
        code, out, err = self.cli("check")
        self.assertEqual(code, 0, "the launcher and self-update run check: a login must not keep the service from starting")
        self.assertIn("WARN agent health: ", out)
        self.assertIn("exit 1", out)
        self.heartbeat()
        code, out, err = self.cli("check", "--service")
        self.assertEqual(code, 2)
        self.assertIn("health_command fails", err)
        self.assertNotIn("sk-secret-token", out + err)

    def test_a_passing_health_command_says_so_and_none_is_defaulted(self) -> None:
        self.assertNotIn("agent health", self.cli("check")[1])
        self.assertEqual(supervisor.load_settings(self.registry).agent.health_command, ())
        self.health_registry(0)
        self.assertIn("OK agent health", self.cli("check")[1])

    def test_a_health_command_that_cannot_run_is_a_failure(self) -> None:
        self.registry.write_text(self.registry.read_text().replace('model = "opus"', 'health_command = ["/no/such/program"]\nmodel = "opus"'))
        self.assertIn("could not run", self.cli("check")[1])

    def test_the_service_checks_health_hourly_and_tells_every_wave_and_notifies_once(self) -> None:
        sink = self.notice_registry()
        self.health_registry(1, secret="sk-secret-token")
        self.register()
        settings = supervisor.load_settings(self.registry)
        board_config = board.load_config(self.registry)
        with (mock.patch.object(supervisor, "agent_health", wraps=supervisor.agent_health) as check,
              mock.patch.object(supervisor, "wake_due", return_value=None),
              mock.patch.object(supervisor, "start_followups", side_effect=[None, KeyboardInterrupt]),
              mock.patch.object(supervisor.time, "sleep"), contextlib.redirect_stdout(io.StringIO())):
            self.assertEqual(supervisor.serve(board_config, settings), 0)
        self.assertEqual(check.call_count, 1, "two passes inside the hour run it once")
        supervisor.record_agent_health(board_config, settings)
        notices = [text for text, context in self.notices(sink) if context["kind"] == "agent-health"]
        self.assertEqual(len(notices), 1, "said when it starts failing, not every hour")
        self.assertNotIn("sk-secret-token", " ".join(notices))
        self.assertIn("health_command fails", self.cli("table", "--wave", WAVE)[1])
        write(self.base / "health.sh", "#!/bin/sh\nexit 0\n", executable=True)
        supervisor.record_agent_health(board_config, settings)
        self.cli("cycle", "--wave", WAVE)
        self.assertNotIn("health_command fails", self.cli("table", "--wave", WAVE)[1])

    # ── what `register --confirm` reruns ──

    def gated_register(self, *flags: str) -> tuple[int, str, str]:
        with mock.patch.object(supervisor, "register_gates", REGISTER_GATES):
            return self.cli("register", "--wave", WAVE, "--manifest", str(self.manifest()), *flags)

    def test_register_refuses_with_the_service_down_and_registers_nothing(self) -> None:
        self.fake_systemctl("inactive", 3)
        code, _out, err = self.gated_register("--confirm")
        self.assertEqual(code, 2)
        self.assertIn(f"wave {WAVE} not registered: the supervisor service is not running", err)
        self.assertFalse(supervisor.Wave(self.hub, WAVE).queue_path.exists())
        self.assertFalse((self.base / "board" / "supervisor" / f"proj__{WAVE}.json").exists())

    def test_register_passes_its_gates_with_the_service_up_and_a_dry_run_runs_none(self) -> None:
        self.fake_systemctl("inactive", 3)
        self.assertEqual(self.gated_register("--dry-run")[0], 0, "a preview, as it was")
        self.assertFalse((self.base / "systemctl.log").exists())
        self.heartbeat()
        code, out, err = self.gated_register("--confirm")
        self.assertEqual(code, 0, err)
        self.assertIn("[register] gate passed: supervisor service: heartbeat", out)

    def test_register_refuses_a_failing_health_command(self) -> None:
        self.heartbeat()
        self.health_registry(1)
        code, _out, err = self.gated_register("--confirm")
        self.assertEqual(code, 2)
        self.assertIn("health_command fails", err)

    def test_register_refuses_a_checkout_off_its_branch(self) -> None:
        self.heartbeat()
        self.git_hub()
        self.git("switch", "-c", "analyze/x")
        code, _out, err = self.gated_register("--confirm")
        self.assertEqual(code, 2)
        self.assertIn("is on branch analyze/x, not main", err)
        self.git("switch", "main")
        self.assertEqual(self.gated_register("--confirm")[0], 0)

    # ── the project checkout ──

    def git(self, *argv: str) -> None:
        subprocess.run(["git", "-C", str(self.hub), *argv], check=True, capture_output=True)

    def git_hub(self) -> None:
        self.git("init", "-b", "main")
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "--allow-empty", "-m", "first")
        sync = self.hub / "sync.toml"
        sync.write_text(sync.read_text() + '\n[git]\nremote = "origin"\nbranch = "main"\n')

    def test_a_checkout_off_its_branch_is_a_warning_and_one_notice_and_nothing_moves_it(self) -> None:
        sink = self.notice_registry()
        self.register()
        self.git_hub()
        wave = supervisor.Wave(self.hub, WAVE)
        self.cli("cycle", "--wave", WAVE)
        self.assertNotIn("project checkout", self.cli("table", "--wave", WAVE)[1])
        self.git("switch", "-c", "by-hand")
        settings = supervisor.load_settings(self.registry)
        for _ in range(2):
            outcome = supervisor.cycle_wave(wave, board.load_config(self.registry), settings)
            supervisor.notify_events(wave, settings, outcome["events"], {})
        self.assertIn("⚠ project checkout is on branch by-hand, not main", self.cli("table", "--wave", WAVE)[1])
        self.assertEqual(len(self.events("checkout-off-branch")), 1)
        self.assertEqual(sum(context["kind"] == "checkout-off-branch" for _text, context in self.notices(sink)), 1)
        self.assertEqual(supervisor.current_branch(self.hub), "by-hand", "a person may be working there")
        self.git("switch", "main")
        self.cli("cycle", "--wave", WAVE)
        self.assertNotIn("project checkout", self.cli("table", "--wave", WAVE)[1])

    def test_a_checkout_off_its_branch_means_nothing_without_a_declared_branch(self) -> None:
        self.register()
        self.git("init", "-b", "whatever")
        self.cli("cycle", "--wave", WAVE)
        self.assertNotIn("project checkout", self.cli("table", "--wave", WAVE)[1])

    def run_one_followup(self, board_config, settings) -> None:
        threads: dict = {}
        supervisor.start_followups(board_config, settings, threads)
        for thread in threads.values():
            thread.join(30)

    def test_a_followup_that_leaves_the_checkout_off_its_branch_is_told_though_the_wave_is_over(self) -> None:
        sink = self.notice_registry()
        self.register()
        self.git_hub()
        script = self.base / "branching_agent.sh"
        write(script, f'#!/bin/sh\ngit -C "{self.hub}" switch -c analyze/wave-{WAVE}\n', executable=True)
        self.registry.write_text(re.sub(r'^command = .*$', lambda _m: f'command = ["{script}", "{{prompt}}"]', self.registry.read_text(), flags=re.M))
        board_config = board.load_config(self.registry)
        settings = supervisor.load_settings(self.registry)
        wave = supervisor.Wave(self.hub, WAVE)
        record = supervisor.enqueue_followup(board_config, wave, self.queue(), "analyze", "5 done")
        self.run_one_followup(board_config, settings)
        self.assertEqual(self.followups()[0]["off_branch"], {"branch": f"analyze/wave-{WAVE}", "expected": "main"})
        self.assertEqual(len(self.events("checkout-off-branch")), 1)
        self.assertEqual(sum(context["kind"] == "checkout-off-branch" for _text, context in self.notices(sink)), 1)
        lines = [item["line"] for item in supervisor.followup_warnings(wave, board_config)]
        self.assertTrue(any(f"on branch analyze/wave-{WAVE}, not main" in line for line in lines), lines)
        self.git("switch", "main")
        self.assertFalse(any("project checkout" in item["line"] for item in supervisor.followup_warnings(wave, board_config)), record["id"])

    def test_a_followup_waits_while_the_checkout_is_off_its_branch_then_runs(self) -> None:
        sink = self.notice_registry()
        self.register()
        self.git_hub()
        self.git("switch", "-c", "by-hand")
        board_config = board.load_config(self.registry)
        settings = supervisor.load_settings(self.registry)
        wave = supervisor.Wave(self.hub, WAVE)
        supervisor.enqueue_followup(board_config, wave, self.queue(), "analyze", "5 done")
        for _ in range(2):
            self.run_one_followup(board_config, settings)
        record = self.followups()[0]
        self.assertEqual(record["status"], "pending", "never on a branch a person may be working on")
        self.assertEqual(sum(context["kind"] == "checkout-off-branch" for _text, context in self.notices(sink)), 1)
        self.assertTrue(any("waits for it" in item["line"] for item in supervisor.followup_warnings(wave, board_config)))
        self.git("switch", "main")
        self.run_one_followup(board_config, settings)
        record = self.followups()[0]
        self.assertEqual(record["status"], "done")
        self.assertNotIn("off_branch", record)

    # ── prompts ──

    def test_unattended_prompts_name_this_copy_of_the_script_and_the_tick_procedure(self) -> None:
        self.register()
        self.assertEqual(self.cli("followup", "--wave", WAVE, "--kind", "analyze", "--text", "done")[0], 0)
        wave = supervisor.Wave(self.hub, WAVE)
        tick = supervisor.agent_prompt(wave, "test")
        followup = supervisor.followup_prompt(self.followups()[0])
        self.assertIn(str(SCRIPT.resolve()), tick)
        self.assertIn(str(SCRIPT.resolve().parents[1] / "references/tick.md"), tick)
        self.assertIn(f"{SCRIPT.resolve()} --root . followup-report", followup)
        self.assertNotIn("<the sweep-supervisor skill", followup)
        self.assertTrue(supervisor.TICK_DOC.is_file())

    def test_an_agent_is_told_the_scripts_path_in_its_environment(self) -> None:
        self.registry.write_text(self.registry.read_text().replace(
            'command = ["sh", "-c", "echo model={model} effort={effort}; echo \\"$0\\"", "{prompt}"]',
            'command = ["sh", "-c", "echo script=$SWEEP_SUPERVISOR_SCRIPT", "{prompt}"]'))
        self.register()
        self.cli("wake", "--wave", WAVE)
        log = Path(supervisor.Wave(self.hub, WAVE).runtime()["agent_last_log"]).read_text()
        self.assertIn(f"script={SCRIPT.resolve()}", log)

    def test_prompts_name_the_configured_branch_and_only_a_followup_publishes(self) -> None:
        self.register()
        self.git_hub()
        self.assertEqual(self.cli("followup", "--wave", WAVE, "--kind", "analyze", "--text", "done")[0], 0)
        self.assertEqual(self.cli("followup", "--wave", WAVE, "--kind", "fix", "--text", "bug")[0], 0)
        wave = supervisor.Wave(self.hub, WAVE)
        git = supervisor.project_git(wave, supervisor.load_settings(self.registry))
        for record in self.followups():
            prompt = supervisor.followup_prompt(record, git)
            self.assertIn("`main`", prompt)
            self.assertIn("origin", prompt)
        tick = supervisor.agent_prompt(wave, "test", git)
        self.assertIn("`main`", tick)
        self.assertNotIn("origin", tick, "a tick leaves publishing to its follow-up or dispatch")

    # ── certificate renewal ──

    def renewal_registry(self, command: str = "") -> Path:
        """`cluster` with a certificate and a renewal command that records each run and exits as $RENEW_EXIT says."""
        script = self.base / "renew.sh"
        write(script, f'#!/bin/sh\necho run >> "{self.base / "renewed"}"\necho sk-secret-token\nexit ${{RENEW_EXIT:-0}}\n', executable=True)
        self.registry.write_text(self.registry.read_text().replace(
            'gpus = "job"', f'gpus = "job"\nssh_cert = "~/.ssh/id_cluster-cert.pub"\ncert_renew_command = "{command or script}"'))
        return script

    def renewals(self) -> int:
        path = self.base / "renewed"
        return len(path.read_text().splitlines()) if path.exists() else 0

    def register_cluster(self) -> supervisor.Wave:
        self.register(pool={"hub": {"lanes": ["0"]}, "cluster": {"max_jobs": 1, "default_walltime_s": 3600}})
        return supervisor.Wave(self.hub, WAVE)

    def test_a_certificate_in_its_last_hour_is_renewed_once_and_the_notice_says_so(self) -> None:
        self.renewal_registry()
        wave = self.register_cluster()
        soon = supervisor.now() + supervisor.timedelta(minutes=30)
        later = supervisor.now() + supervisor.timedelta(hours=12)
        expiries = iter([soon, later])
        with mock.patch.object(supervisor, "cert_expiry", side_effect=lambda _path: next(expiries, later)):
            self.cli("cycle", "--wave", WAVE)
            self.cli("cycle", "--wave", WAVE)
        self.assertEqual(self.renewals(), 1)
        ledger = wave.ledger()
        renewed = [r for r in ledger if r["event"] == "cert-renewed"]
        self.assertEqual(renewed[0]["expires_at"], supervisor.iso(later))
        self.assertTrue([r for r in ledger if r["event"] == "cert-expiring"][0]["auto_renew"])
        self.assertNotIn("sk-secret-token", wave.ledger_path.read_text())
        self.assertNotIn("SSH certificate expire", self.cli("table", "--wave", WAVE)[1])
        self.assertIsNone(supervisor.event_notice(wave, [r for r in ledger if r["event"] == "cert-expiring"][0]), "the outcome is the notice")
        self.assertIn("renewed automatically", supervisor.event_notice(wave, renewed[0]))

    def test_a_failed_renewal_is_recorded_tried_once_and_keeps_the_expired_notice(self) -> None:
        self.renewal_registry()
        wave = self.register_cluster()
        soon = supervisor.now() + supervisor.timedelta(minutes=30)
        with mock.patch.dict(os.environ, {"RENEW_EXIT": "4"}), mock.patch.object(supervisor, "cert_expiry", return_value=soon):
            self.cli("cycle", "--wave", WAVE)
            self.cli("cycle", "--wave", WAVE)
        self.assertEqual(self.renewals(), 1)
        failed = [r for r in wave.ledger() if r["event"] == "cert-renew-failed"]
        self.assertEqual(failed[0]["problem"], "exit 4")
        self.assertIn("automatic certificate renewal failed (exit 4)", supervisor.event_notice(wave, failed[0]))
        self.assertIn("cluster: SSH certificate expires", self.cli("table", "--wave", WAVE)[1])
        settings = supervisor.load_settings(self.registry)
        cycle = supervisor.Cycle(wave, supervisor.project_config(wave, settings), board.load_config(self.registry), settings)
        cycle.at = soon + supervisor.timedelta(minutes=1)
        with mock.patch.object(supervisor, "cert_expiry", return_value=soon):
            runtime = wave.runtime()
            cycle.watch_certs(self.queue(), runtime)
        self.assertEqual(self.renewals(), 1, "the same window: not tried again")
        self.assertEqual([e["event"] for e in cycle.events], ["cert-expired"])

    def test_a_renewal_that_exits_0_but_changes_nothing_has_failed(self) -> None:
        self.renewal_registry()
        wave = self.register_cluster()
        soon = supervisor.now() + supervisor.timedelta(minutes=30)
        with mock.patch.object(supervisor, "cert_expiry", return_value=soon):
            self.cli("cycle", "--wave", WAVE)
        self.assertIn("did not get a later expiry", [r for r in wave.ledger() if r["event"] == "cert-renew-failed"][0]["problem"])

    def test_two_waves_on_one_cluster_share_one_attempt_per_window(self) -> None:
        self.renewal_registry()
        wave = self.register_cluster()
        other = supervisor.Wave(self.hub, "20260921-093001")
        settings = supervisor.load_settings(self.registry)
        board_config = board.load_config(self.registry)
        soon = supervisor.now() + supervisor.timedelta(minutes=30)
        config = supervisor.project_config(wave, settings)
        with mock.patch.dict(os.environ, {"RENEW_EXIT": "1"}), mock.patch.object(supervisor, "cert_expiry", return_value=soon):
            for item in (wave, other):
                cycle = supervisor.Cycle(item, config, board_config, settings)
                cycle.watch_certs(self.queue(), {})
        self.assertEqual(self.renewals(), 1)
        told = [r for r in other.ledger() if r["event"] == "cert-expiring"]
        self.assertNotIn("auto_renew", told[0], "the other wave has no attempt to wait for")

    def test_an_already_expired_certificate_gets_its_one_attempt_too(self) -> None:
        self.renewal_registry()
        wave = self.register_cluster()
        past = supervisor.now() - supervisor.timedelta(minutes=5)
        later = supervisor.now() + supervisor.timedelta(hours=12)
        expiries = iter([past, later])
        with mock.patch.object(supervisor, "cert_expiry", side_effect=lambda _path: next(expiries, later)):
            self.cli("cycle", "--wave", WAVE)
        events = [r["event"] for r in wave.ledger()]
        self.assertIn("cert-renewed", events)
        self.assertNotIn("cert-expired", events)

    def test_the_renew_command_takes_a_string_or_a_list_and_a_bad_one_is_refused(self) -> None:
        self.renewal_registry("systemctl --user start cineca-cert-renew.service")
        settings = supervisor.load_settings(self.registry)
        self.assertEqual(settings.cert_renew, (("cluster", ("systemctl", "--user", "start", "cineca-cert-renew.service")),))
        self.assertIn("OK certificate renewal: cluster through systemctl", self.cli("check")[1])
        self.registry.write_text(self.registry.read_text().replace(
            'cert_renew_command = "systemctl --user start cineca-cert-renew.service"', 'cert_renew_command = ["renew", "--quiet"]'))
        self.assertEqual(supervisor.load_settings(self.registry).cert_renew, (("cluster", ("renew", "--quiet")),))
        self.registry.write_text(self.registry.read_text().replace('cert_renew_command = ["renew", "--quiet"]', "cert_renew_command = 3"))
        self.assertEqual(self.cli("check")[0], 2)

    def test_check_flags_a_renew_command_with_no_certificate_to_watch(self) -> None:
        self.registry.write_text(self.registry.read_text().replace('gpus = "job"', 'gpus = "job"\ncert_renew_command = "renew"'))
        self.assertIn("WARN certificate renewal: cluster declares cert_renew_command but no ssh_cert", self.cli("check")[1])

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
        self.register(runs=3)
        self.cli("cycle", "--wave", WAVE)  # hub takes m0 and m2, peer m1
        [slow] = [r for r in self.queue()["runs"] if r["lane"] == {"rig": "peer", "gpu": "0"}]
        lane = self.lane(self.peer)
        (lane / "queue" / slow["entry"]).rename(lane / "running" / slow["entry"])  # started: no longer reclaimable
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

    def snap(self, *, lanes=(), failed=(), questions=(), certs=None, written_ago_s: float = 0, finished_at=None, wave: str = WAVE) -> None:
        at = supervisor.now()
        lanes = list(lanes) or [self.row("hub", "running", "model=m0")]
        snapshot = {
            "written_at": supervisor.iso(at - supervisor.timedelta(seconds=written_ago_s)),
            "counts": {"done": 1, "failed": len(failed), "queued": 0, "assigned": 0, "running": 1, "excluded": 0}, "total": 5,
            "wave_eta": supervisor.iso(at + supervisor.timedelta(hours=1)), "wave_eta_basis": "feeder simulation",
            "finished_at": finished_at, "lanes": lanes, "failed": list(failed), "questions": list(questions),
            "rigs": {"hub": {"kind": "lanes", "done": 1}, "peer": {"kind": "lanes", "done": 0}}, "certs": certs or {},
        }
        board.write_json(supervisor.Wave(self.hub, wave).snapshot_path, snapshot)

    @staticmethod
    def row(rig: str, state: str, run=None, problem=None) -> dict:
        return {"kind": "lane", "rig": rig, "gpu": "0", "state": state, "run": run, "done": 0, "lane_free_at": None, "problem": problem}

    def watch(self, timeout: str = "0", wave: str = WAVE) -> str:
        code, out, err = self.cli("watch", "--wave", wave, "--poll", "1", "--timeout", timeout)
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

    def closing_tick(self, wave: str = WAVE) -> None:
        """The agent's tick on the ended wave starts and ends."""
        later = supervisor.iso(supervisor.now() + supervisor.timedelta(seconds=5))
        board.write_json(supervisor.Wave(self.hub, wave).runtime_path, {"agent_tick_started_at": later, "agent_ticked_at": later})

    def test_watch_returns_when_the_wave_turns_terminal_and_its_closing_tick_is_over(self) -> None:
        self.register()
        failed = [{"run": "model=m3", "note": None}]
        self.snap(failed=failed)
        self.watch()
        ticks: list[float] = []

        def later(_seconds: float) -> None:
            ticks.append(_seconds)
            if len(ticks) == 1:
                self.snap(failed=failed, finished_at=supervisor.iso(supervisor.now()))
            elif len(ticks) == 3:
                self.closing_tick()  # where a fix follow-up would have been queued: none was

        with mock.patch.object(supervisor.time, "sleep", side_effect=later):
            out = self.watch(timeout="3600")
        self.assertEqual(len(ticks), 3, "an ended wave with a failed run waits for its closing tick")
        self.assertTrue(out.startswith("watch: wave finished\n"), out)
        self.assertIn("finished ", out)
        self.assertTrue(self.watch().startswith("watch: wave finished"), "a finished wave answers at once")

    def test_watch_follows_a_wave_into_its_fix_round(self) -> None:
        """The case that lost a chat its wave: every run failed on a code error, the closing tick queued
        a fix, and the fix dispatched a new wave whose question nobody saw. Each step of the fix round
        returns once, so a chat that reads watch only when it ends hears each when it happens."""
        self.register()
        board_config = board.load_config(self.registry)
        failed = [{"run": f"model=m{i}", "note": "exit 1"} for i in range(5)]
        self.snap(failed=failed, finished_at=supervisor.iso(supervisor.now()))
        self.assertTrue(self.watch().startswith("watch: new ⚠ failed: `model=m0`"), "ended, but its closing tick is still to come")
        ticks: list[float] = []
        state: dict = {}

        def closing_tick_queues_a_fix(_seconds: float) -> None:
            ticks.append(_seconds)
            wave = supervisor.Wave(self.hub, WAVE)
            with supervisor.WaveLock(wave):
                queue = self.queue()
                for run in queue["runs"]:
                    run["state"] = "failed"
                wave.save(queue)
            state["fix"] = supervisor.enqueue_followup(board_config, wave, self.queue(), "fix", "wandb.util.generate_id is gone")
            self.closing_tick()

        with mock.patch.object(supervisor.time, "sleep", side_effect=closing_tick_queues_a_fix):
            out = self.watch(timeout="3600")
        self.assertEqual(len(ticks), 1)
        fix_id = state["fix"]["id"]
        self.assertTrue(out.startswith(f"watch: wave {WAVE} ended: 0 done, 5 failed; fix follow-up {fix_id} queued; "
                                       "run watch again for the wave it dispatches\n"), out)
        path, record = supervisor.find_followup(board_config, fix_id)
        board.write_json(path, {**record, "status": "running"})
        self.assertTrue(self.watch().startswith("watch: timeout"), "the fix is told once, queued or running")
        code, _out, err = self.cli("register", "--wave", FIX_WAVE, "--confirm", "--manifest",
                                   str(self.manifest(wave_id=FIX_WAVE, fix_depth=1, fixes_wave=WAVE)))
        self.assertEqual(code, 0, err)
        self.assertEqual(self.watch(), f"watch: continuing as wave {FIX_WAVE} (fix round 1); run watch --wave {FIX_WAVE} next\n")
        self.snap(wave=FIX_WAVE, questions=[{"id": "q1", "text": "run wandb login on rig-3080-ti?", "meanwhile": "skip it", "status": "open"}])
        out = self.watch()  # a chat that kept the old id still lands on the new wave, and is told so
        self.assertEqual(out.splitlines()[:2], [
            f"watch: following wave {FIX_WAVE} (fix round 1 after wave {WAVE})",
            "watch: new ⚠ needs you [q1] run wandb login on rig-3080-ti? — meanwhile: skip it",
        ])
        self.assertTrue(self.watch(wave=FIX_WAVE).startswith("watch: timeout"), "the new wave's question was announced once")

    def test_a_fix_that_dispatches_nothing_ends_the_watch_with_its_report(self) -> None:
        self.register()
        board_config = board.load_config(self.registry)
        self.snap(finished_at=supervisor.iso(supervisor.now()))
        record = supervisor.enqueue_followup(board_config, supervisor.Wave(self.hub, WAVE), self.queue(), "fix", "a bug")
        path, _ = supervisor.find_followup(board_config, record["id"])
        board.write_json(path, {**record, "status": "done", "outcome": "exit 0", "report": "the fix changes the science; asked q2\nmore"})
        self.closing_tick()
        out = self.watch()
        self.assertTrue(out.startswith(
            f"watch: fix follow-up {record['id']} ended without a new wave: the fix changes the science; asked q2\nwatch: wave finished\n"), out)
        board.write_json(path, {**record, "status": "failed", "outcome": "exit 1", "log": "/tmp/f.log"})
        self.assertIn(f"{record['id']} ended without a new wave: exit 1 without a report; log /tmp/f.log\nwatch: wave finished", self.watch())

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

    # ── what else watch tells, once ──

    def watch_all(self, timeout: str = "0") -> str:
        code, out, err = self.cli("watch", "--all", "--poll", "1", "--timeout", timeout)
        self.assertEqual(code, 0, err)
        return out

    def second_wave(self) -> None:
        code, _out, err = self.cli("register", "--wave", FIX_WAVE, "--confirm", "--manifest", str(self.manifest(wave_id=FIX_WAVE)))
        self.assertEqual(code, 0, err)

    def test_watch_tells_a_say_once_even_from_the_gap_between_watches(self) -> None:
        self.register()
        self.snap()
        self.assertTrue(self.watch().startswith("watch: timeout"))
        self.cli("say", "--wave", WAVE, "--text", "moved m3 to the 4090")  # nobody is watching
        out = self.watch()
        self.assertTrue(out.startswith("watch: say: moved m3 to the 4090\n"), out)
        self.assertIn("Status written ", out)
        self.assertTrue(self.watch().startswith("watch: timeout"), "re-arming repeats nothing")

        def later(_seconds: float) -> None:
            self.cli("say", "--wave", WAVE, "--text", "and m4")

        with mock.patch.object(supervisor.time, "sleep", side_effect=later):
            self.assertTrue(self.watch(timeout="3600").startswith("watch: say: and m4\n"))

    def test_watch_tells_a_question_at_once_without_waiting_for_a_snapshot(self) -> None:
        self.register()
        self.snap()
        self.cli("ask", "--wave", WAVE, "--text", "use gpu 5?", "--meanwhile", "unused")
        self.assertTrue(self.watch().startswith("watch: new ⚠ needs you [q1] use gpu 5? — meanwhile: unused\n"))
        self.assertTrue(self.watch().startswith("watch: timeout"))
        self.snap(questions=[{"id": "q1", "text": "use gpu 5?", "meanwhile": "unused", "status": "open"}])
        self.assertTrue(self.watch().startswith("watch: timeout"), "the snapshot naming it later is the same question")

    def test_watch_tells_every_kind_of_follow_up_report_once(self) -> None:
        self.register()
        self.snap()
        board_config = board.load_config(self.registry)
        wave = supervisor.Wave(self.hub, WAVE)
        for kind in ("analyze", "fix"):
            with self.subTest(kind):
                record = supervisor.enqueue_followup(board_config, wave, self.queue(), kind, "x")
                self.assertTrue(self.watch().startswith("watch: timeout"), "queued, no report yet")
                self.assertEqual(self.cli("followup-report", "--id", record["id"], "--text", "plots at http://x/1\nand http://x/2")[0], 0)
                out = self.watch()
                self.assertTrue(out.startswith(f"watch: follow-up {kind} {record['id']} report: plots at http://x/1\nand http://x/2\n"), out)
                self.assertTrue(self.watch().startswith("watch: timeout"))

    def test_a_finished_wave_stays_open_until_its_analyze_report_is_told(self) -> None:
        self.register()
        board_config = board.load_config(self.registry)
        wave = supervisor.Wave(self.hub, WAVE)
        with supervisor.WaveLock(wave):
            queue = self.queue()
            for run in queue["runs"]:
                run["state"] = "done"
            wave.save(queue)
        self.snap(finished_at=supervisor.iso(supervisor.now()))
        out = self.watch()
        self.assertTrue(out.startswith(f"watch: wave {WAVE} ended: 5 done, 0 failed; its closing tick is still running; "), out)
        self.assertTrue(self.watch().startswith("watch: timeout"), "the end is told once")
        record = supervisor.enqueue_followup(board_config, wave, self.queue(), "analyze", "5 done")
        self.closing_tick()
        self.assertTrue(self.watch().startswith("watch: timeout"), "the report is awaited in silence")
        path, _ = supervisor.find_followup(board_config, record["id"])
        board.write_json(path, {**record, "status": "running", "report": "the plots"})
        self.assertTrue(self.watch().startswith(f"watch: follow-up analyze {record['id']} report: the plots\n"))
        board.write_json(path, {**record, "status": "done", "outcome": "exit 0", "report": "the plots"})
        self.assertTrue(self.watch().startswith("watch: wave finished"))

    def test_a_finish_still_pruning_is_not_the_end_of_the_wave(self) -> None:
        """`finish` drops the pointer, prunes, and only then queues `analyze`."""
        self.register()
        board_config = board.load_config(self.registry)
        wave = supervisor.Wave(self.hub, WAVE)
        self.snap(finished_at=supervisor.iso(supervisor.now()))
        queue = self.queue()
        supervisor.index_path(board_config, queue["project"], WAVE).unlink()
        wave.log("finish", abandoned=False)
        self.assertTrue(self.watch().startswith("watch: timeout"))
        wave.log("finish-closed")
        self.assertTrue(self.watch().startswith("watch: wave finished"))

    def test_watch_tells_a_failed_analyze_after_the_wave_ended(self) -> None:
        self.register()
        board_config = board.load_config(self.registry)
        wave = supervisor.Wave(self.hub, WAVE)
        self.snap(finished_at=supervisor.iso(supervisor.now()))
        self.closing_tick()
        record = supervisor.enqueue_followup(board_config, wave, self.queue(), "analyze", "x")
        self.assertIn(f"analyze follow-up {record['id']} queued; run watch again for its report", self.watch())
        path, _ = supervisor.find_followup(board_config, record["id"])
        board.write_json(path, {**record, "status": "failed", "outcome": "exit 1", "log": "/tmp/f.log"})
        self.assertEqual(self.watch().splitlines()[:2], ["watch: new ⚠ follow-up analyze exit 1 without a report; log /tmp/f.log", "watch: wave finished"])

    def test_watch_tells_events_the_snapshot_never_shows_or_that_cleared_in_the_gap(self) -> None:
        self.register()
        wave = supervisor.Wave(self.hub, WAVE)
        self.snap()
        self.assertTrue(self.watch().startswith("watch: timeout"))
        cases = [
            ("submit-failed", dict(rig="cluster", run="model=m0", problem="sbatch: quota"), "submission to cluster failed: "),
            ("rig-down", dict(rig="cluster", since=supervisor.iso(supervisor.now())), "cluster unreachable since "),
            ("run-failed", dict(run="model=m1", rig="hub", gpu="0", rc=1), "failed: `model=m1` on hub gpu0 (exit 1)"),
            ("job-failed", dict(run="model=m2", slurm_state="FAILED", exit="1:0"), "failed: `model=m2` (FAILED)"),
            ("recovery-blocked", dict(rig="peer", gpu="0", problem="env drift"), "peer gpu0 cannot start: env drift"),
            ("cert-expiring", dict(rig="cluster", expires_at=supervisor.iso(supervisor.now())), "cluster: SSH certificate expires"),
            ("cert-expired", dict(rig="cluster", expired_at=supervisor.iso(supervisor.now())), "cluster: SSH certificate expired"),
        ]
        for event, fields, line in cases:
            with self.subTest(event):
                wave.log(event, **fields)
                self.snap()  # the cycle that logged it writes its snapshot after; nothing stands in it now
                self.assertIn(f"watch: new ⚠ {line}", self.watch())
                self.assertTrue(self.watch().startswith("watch: timeout"))

    def test_an_event_waits_for_its_snapshot_and_a_standing_warning_tells_it_only_once(self) -> None:
        self.register()
        wave = supervisor.Wave(self.hub, WAVE)
        self.snap(written_ago_s=30)
        wave.log("rig-down", rig="peer", since=supervisor.iso(supervisor.now()))
        self.assertTrue(self.watch().startswith("watch: timeout"), "its cycle has not written the snapshot yet")
        self.snap(lanes=[self.row("peer", "rig down", problem="down since 09:00")])
        out = self.watch()
        self.assertIn("watch: new ⚠ peer gpu0: down since 09:00", out)
        self.assertNotIn("unreachable", out)
        self.snap()
        self.assertTrue(self.watch().startswith("watch: timeout"))

    def test_an_event_repeating_every_cycle_is_told_once_not_every_minute(self) -> None:
        self.register()
        wave = supervisor.Wave(self.hub, WAVE)
        self.snap()
        self.watch()
        for _ in range(3):
            wave.log("submit-failed", rig="cluster", run="model=m0", problem="quota")
        self.snap()
        self.assertEqual(self.watch().count("submission to cluster failed"), 1)
        wave.log("submit-failed", rig="cluster", run="model=m1", problem="quota")
        self.snap()
        self.assertTrue(self.watch().startswith("watch: timeout"), "the same notice within the hour")

    def test_a_chat_attaching_late_gets_the_newest_notices_and_a_pointer_to_the_rest(self) -> None:
        self.register()
        for index in range(25):
            self.cli("say", "--wave", WAVE, "--text", f"note {index}")
        self.snap()
        lines = self.watch().splitlines()
        self.assertIn("… and 5 earlier notices; `ledger --wave", lines[0])
        self.assertEqual((lines[1], lines[20]), ("watch: say: note 5", "watch: say: note 24"))

    def test_a_watch_on_a_fix_round_still_hears_the_old_wave(self) -> None:
        self.register()
        board_config = board.load_config(self.registry)
        code, _out, err = self.cli("register", "--wave", FIX_WAVE, "--confirm", "--manifest", str(self.manifest(wave_id=FIX_WAVE, fix_depth=1, fixes_wave=WAVE)))
        self.assertEqual(code, 0, err)
        self.snap(wave=FIX_WAVE)
        record = supervisor.enqueue_followup(board_config, supervisor.Wave(self.hub, WAVE), self.queue(), "fix", "x")
        self.cli("followup-report", "--id", record["id"], "--text", "dispatched the new wave")
        self.cli("say", "--wave", WAVE, "--text", "last words")
        out = self.watch(wave=FIX_WAVE)
        self.assertIn("watch: say: last words", out)
        self.assertIn("report: dispatched the new wave", out)
        self.assertTrue(self.watch(wave=FIX_WAVE).startswith("watch: timeout"))

    # ── one watch for a project ──

    def test_watch_needs_a_wave_or_all_and_not_both(self) -> None:
        for argv in (("watch",), ("watch", "--all", "--wave", WAVE)):
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                self.cli(*argv)

    def test_watch_from_a_linked_worktree_follows_the_main_checkout(self) -> None:
        self.register()
        self.snap()
        git = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(self.hub)]
        subprocess.run(["git", "init", "-q", str(self.hub)], check=True)
        subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "first"], check=True)
        tree = self.base / "session-tree"
        subprocess.run([*git, "worktree", "add", "-q", "-b", "session", str(tree)], check=True)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = supervisor.main(["--root", str(tree), "--registry", str(self.registry), "watch", "--all", "--poll", "1", "--timeout", "0"])
        self.assertEqual(code, 0)
        self.assertTrue(out.getvalue().startswith(f"watch: timeout, nothing new in {WAVE}"), out.getvalue())

    def test_watch_all_names_the_wave_of_each_new_thing(self) -> None:
        self.register()
        self.second_wave()
        self.snap()
        self.snap(wave=FIX_WAVE)
        out = self.watch_all()
        self.assertTrue(out.startswith(f"watch: timeout, nothing new in {WAVE}, {FIX_WAVE}; run watch --all again\n"), out)
        self.assertIn(f"\nwave {WAVE}:\n", out)
        self.cli("say", "--wave", FIX_WAVE, "--text", "hello")
        self.snap(wave=FIX_WAVE, failed=[{"run": "model=m3", "note": None}])
        out = self.watch_all()
        self.assertTrue(out.startswith(f"watch: wave {FIX_WAVE}: new ⚠ failed: `model=m3`\nwatch: wave {FIX_WAVE}: say: hello\n"), out)
        self.assertNotIn(f"wave {WAVE}:", out)
        self.assertTrue(self.watch_all().startswith("watch: timeout"))
        self.assertTrue(self.watch(wave=FIX_WAVE).startswith("watch: timeout"), "--all and --wave share what was told")

    def test_watch_all_picks_up_a_wave_that_appears_while_it_runs(self) -> None:
        self.register()
        self.snap()

        def later(_seconds: float) -> None:
            self.second_wave()
            self.cli("say", "--wave", FIX_WAVE, "--text", "new wave here")

        with mock.patch.object(supervisor.time, "sleep", side_effect=later):
            out = self.watch_all(timeout="3600")
        self.assertTrue(out.startswith(f"watch: wave {FIX_WAVE}: say: new wave here\n"), out)

    def test_watch_all_ends_only_when_no_live_wave_remains(self) -> None:
        self.assertTrue(self.watch_all().startswith("watch: no live wave in "), "nothing registered")
        self.register()
        self.second_wave()
        self.snap()
        self.snap(wave=FIX_WAVE)
        self.watch_all()
        self.snap(finished_at=supervisor.iso(supervisor.now()))
        self.closing_tick()
        self.assertEqual(self.watch_all().splitlines()[0], f"watch: wave {WAVE}: wave finished")
        out = self.watch_all()
        self.assertTrue(out.startswith("watch: timeout"), "the other wave is still live; the finished one is told once")
        self.assertNotIn(f"wave {WAVE}:", out)
        self.snap(wave=FIX_WAVE, finished_at=supervisor.iso(supervisor.now()))
        self.closing_tick(FIX_WAVE)
        self.assertEqual(self.watch_all().splitlines()[0], f"watch: wave {FIX_WAVE}: wave finished")
        self.assertEqual(self.watch_all(), f"watch: no live wave in {self.hub.resolve()}; nothing left to follow\n")

    def test_watch_all_ignores_old_waves_but_tells_one_it_followed_that_ended_in_the_gap(self) -> None:
        self.register()
        board_config = board.load_config(self.registry)
        queue = self.queue()
        self.snap()
        self.watch_all()  # followed from here on; then it ends, finished and all, with nobody watching
        wave = supervisor.Wave(self.hub, WAVE)
        with supervisor.WaveLock(wave):
            queue["finished_at"] = supervisor.iso(supervisor.now())
            wave.save(queue)
        supervisor.index_path(board_config, queue["project"], WAVE).unlink()
        self.cli("say", "--wave", WAVE, "--text", "last words")
        out = self.watch_all()
        self.assertEqual(out.splitlines()[:2], [f"watch: wave {WAVE}: say: last words", f"watch: wave {WAVE}: wave finished"])
        self.assertTrue(self.watch_all().startswith("watch: no live wave"))
        # the same end on a wave no new watch ever followed is history
        (wave.dir / "watch.json").unlink()
        self.assertTrue(self.watch_all().startswith("watch: no live wave"))

    def test_watch_all_follows_a_fix_round_and_drops_the_old_wave(self) -> None:
        self.register()
        self.snap(failed=[{"run": "model=m0", "note": None}], finished_at=supervisor.iso(supervisor.now()))
        self.closing_tick()
        code, _out, err = self.cli("register", "--wave", FIX_WAVE, "--confirm", "--manifest", str(self.manifest(wave_id=FIX_WAVE, fix_depth=1, fixes_wave=WAVE)))
        self.assertEqual(code, 0, err)
        self.snap(wave=FIX_WAVE)
        self.assertEqual(self.watch_all().splitlines()[:2], [
            f"watch: wave {WAVE}: new ⚠ failed: `model=m0`",
            f"watch: wave {FIX_WAVE}: continuing as wave {FIX_WAVE} (fix round 1)",
        ])
        out = self.watch_all()
        self.assertTrue(out.startswith(f"watch: timeout, nothing new in {FIX_WAVE}; "), out)
        self.cli("say", "--wave", FIX_WAVE, "--text", "round one")
        self.assertEqual(self.watch_all().splitlines()[0], f"watch: wave {FIX_WAVE}: say: round one")

    # ── a running watch's marker ──

    def test_a_running_watch_keeps_a_fresh_marker_and_withdraws_it(self) -> None:
        self.register()
        self.snap()
        wave = supervisor.Wave(self.hub, WAVE)
        seen: list[dict] = []
        spot_check: list[float] = []  # when the marker was read

        def later(_seconds: float) -> None:
            seen.append(json.loads(wave.watching_path.read_text()))
            spot_check.append(supervisor.time.time())
            self.cli("say", "--wave", WAVE, "--text", "done")

        with mock.patch.object(supervisor.time, "sleep", side_effect=later):
            self.watch(timeout="3600")
        self.assertEqual(seen[0]["pid"], os.getpid())
        self.assertGreater(seen[0]["until"], spot_check[0] + 60)
        self.assertFalse(wave.watching_path.exists(), "a watch that returned follows nothing")
        board.write_json(wave.watching_path, {"pid": os.getpid() + 1, "until": 9e12})
        supervisor.unmark_watching(wave)
        self.assertTrue(wave.watching_path.exists(), "another running watch's marker stays")

    def test_watch_all_marks_every_wave_it_follows(self) -> None:
        self.register()
        self.second_wave()
        self.snap()
        self.snap(wave=FIX_WAVE)
        paths = [supervisor.Wave(self.hub, wave).watching_path for wave in (WAVE, FIX_WAVE)]
        seen: list[list[bool]] = []

        def later(_seconds: float) -> None:
            seen.append([path.exists() for path in paths])
            self.cli("say", "--wave", WAVE, "--text", "x")

        with mock.patch.object(supervisor.time, "sleep", side_effect=later):
            self.watch_all(timeout="3600")
        self.assertEqual(seen, [[True, True]])
        self.assertEqual([path.exists() for path in paths], [False, False])

    # ── honest about where a notice went ──

    def failing_notify_registry(self, command: str = '["sh", "-c", "echo sink is down >&2; exit 3", "{text}"]') -> None:
        self.registry.write_text(self.registry.read_text().replace("cycle_every = 60", f"cycle_every = 60\nnotify_command = {command}"))

    def test_with_no_sink_check_is_ok_and_ask_and_say_do_not_claim_sent(self) -> None:
        code, out, err = self.cli("check")
        self.assertEqual(code, 0, err)
        self.assertIn("OK notices: none declared; ", out)
        self.register()
        code, out, _err = self.cli("ask", "--wave", WAVE, "--text", "q?", "--meanwhile", "m")
        self.assertEqual(code, 0)
        self.assertIn("[ask] recorded, not pushed: no notify_command is declared", out)
        self.assertNotIn("sent", out)
        code, out, _err = self.cli("say", "--wave", WAVE, "--text", "hello")
        self.assertEqual(code, 0)
        self.assertEqual(out, "[say] recorded, not pushed: no notify_command is declared\n")

    def test_with_a_sink_check_names_only_its_program_and_notices_say_sent(self) -> None:
        self.notice_registry()
        _code, out, _err = self.cli("check")
        self.assertIn("OK notices: notify_command declared (sh)\n", out)
        self.assertNotIn("printf", out)
        self.register()
        self.assertIn("[ask] sent", self.cli("ask", "--wave", WAVE, "--text", "q?", "--meanwhile", "m")[1])
        self.assertEqual(self.cli("say", "--wave", WAVE, "--text", "hello")[1], "[say] sent\n")

    def test_a_failed_notify_command_is_logged_and_reported_not_claimed_sent(self) -> None:
        self.failing_notify_registry()
        self.register()
        wave = supervisor.Wave(self.hub, WAVE)
        code, out, _err = self.cli("say", "--wave", WAVE, "--text", "hello")
        self.assertEqual(code, 0, "the words are recorded; a dead sink stops nothing")
        self.assertEqual(out, "[say] recorded, not pushed: notify_command failed: exit 3: sink is down\n")
        self.assertIn("failed: exit 3", self.cli("ask", "--wave", WAVE, "--text", "q?", "--meanwhile", "m")[1])
        failures = self.events("notify-failed")
        self.assertEqual([record["kind"] for record in failures], ["say", "ask"])
        self.assertEqual(failures[0]["problem"], "exit 3: sink is down")
        self.assertEqual(len(wave.questions()), 1)

    def test_a_notify_command_that_cannot_run_or_hangs_is_a_failure_too(self) -> None:
        self.failing_notify_registry('["/nonexistent/sink", "{text}"]')
        self.register()
        self.assertIn("could not run", self.cli("say", "--wave", WAVE, "--text", "hello")[1])
        settings = supervisor.load_settings(self.registry)
        with mock.patch.object(supervisor.subprocess, "run", side_effect=subprocess.TimeoutExpired("x", 30)):
            self.assertEqual(supervisor.notify(settings, "t"), "failed: timed out after 30s")

    def test_a_failed_event_notice_is_logged_and_tried_again_at_the_next_occurrence(self) -> None:
        self.failing_notify_registry()
        self.register()
        settings = supervisor.load_settings(self.registry)
        wave = supervisor.Wave(self.hub, WAVE)
        sent: dict = {}
        blocked = {"event": "recovery-blocked", "rig": "peer", "gpu": "0", "problem": "env drift"}
        supervisor.notify_events(wave, settings, [blocked], sent)
        self.assertEqual(sent, {})
        supervisor.notify_events(wave, settings, [blocked], sent)
        self.assertEqual(len(self.events("notify-failed")), 2)

    def test_a_follow_up_report_says_where_it_went(self) -> None:
        self.failing_notify_registry()
        self.register()
        board_config = board.load_config(self.registry)
        record = supervisor.enqueue_followup(board_config, supervisor.Wave(self.hub, WAVE), self.queue(), "analyze", "x")
        code, out, _err = self.cli("followup-report", "--id", record["id"], "--text", "plots")
        self.assertEqual(code, 0)
        self.assertIn(f"[followup-report] {record['id']}: recorded\n[followup-report] recorded, not pushed: notify_command failed: exit 3", out)
        self.assertEqual(self.events("notify-failed")[0]["kind"], "followup-report")

    # ── check proves the service only when asked ──

    def fake_systemctl(self, state: str, rc: int) -> None:
        write(self.bin / "systemctl", f'#!/bin/sh\necho "$*" >> "{self.base / "systemctl.log"}"\necho {state}\nexit {rc}\n', executable=True)

    def heartbeat(self, **fields) -> None:
        beat = {"at": supervisor.iso(supervisor.now()), "pid": os.getpid(), "host": os.uname().nodename, **fields}
        board.write_json(supervisor.heartbeat_path(board.load_config(self.registry)), beat)

    def test_check_service_fails_clearly_with_the_service_down(self) -> None:
        self.fake_systemctl("inactive", 3)
        code, out, err = self.cli("check", "--service")
        self.assertEqual(code, 2)
        self.assertIn("the supervisor service is not running on this machine: no heartbeat", err)
        self.assertIn("is-active sweep-supervisor.service` says inactive", err)
        self.assertIn("--user is-active sweep-supervisor.service", (self.base / "systemctl.log").read_text())
        self.assertIn("OK agent profile", out, "what check proved before that still shows")

    def test_check_service_fails_with_no_systemd_to_ask(self) -> None:
        with mock.patch.object(supervisor.subprocess, "run", side_effect=FileNotFoundError("systemctl")):
            code, _out, err = self.cli("check", "--service")
        self.assertEqual(code, 2)
        self.assertIn("could not ask systemctl", err)

    def test_check_service_passes_on_a_fresh_heartbeat_without_asking_systemd(self) -> None:
        self.fake_systemctl("inactive", 3)
        self.heartbeat()
        code, out, err = self.cli("check", "--service")
        self.assertEqual(code, 0, err)
        self.assertIn(f"from pid {os.getpid()}", out)
        self.assertFalse((self.base / "systemctl.log").exists())

    def test_a_stale_or_dead_or_foreign_heartbeat_proves_nothing(self) -> None:
        old = supervisor.iso(supervisor.now() - supervisor.timedelta(seconds=supervisor.SERVICE_SILENT_S + 60))
        dead = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"], capture_output=True, text=True, check=True)
        for name, fields in (("stale", dict(at=old)), ("dead pid", dict(pid=int(dead.stdout))), ("other host", dict(host="elsewhere"))):
            with self.subTest(name):
                self.fake_systemctl("inactive", 3)
                self.heartbeat(**fields)
                self.assertEqual(self.cli("check", "--service")[0], 2)
                self.fake_systemctl("active", 0)
                code, out, err = self.cli("check", "--service")
                self.assertEqual(code, 0, err)
                self.assertIn("OK service: sweep-supervisor.service is active", out)

    def test_the_service_writes_its_heartbeat_every_pass_with_no_wave_live(self) -> None:
        settings = supervisor.load_settings(self.registry)
        board_config = board.load_config(self.registry)
        with mock.patch.object(supervisor.time, "sleep", side_effect=KeyboardInterrupt), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(supervisor.serve(board_config, settings), 0)
        beat = board.read_json(supervisor.heartbeat_path(board_config))
        self.assertEqual((beat["pid"], beat["host"]), (os.getpid(), os.uname().nodename))
        self.assertEqual(supervisor.active_waves(board_config), [], "the heartbeat is no wave pointer")
        self.assertEqual(self.cli("check", "--service")[0], 0)

    def test_plain_check_needs_no_service_because_a_copy_is_validated_before_one_runs(self) -> None:
        self.fake_systemctl("inactive", 3)
        self.assertEqual(self.cli("check")[0], 0)
        self.assertFalse((self.base / "systemctl.log").exists())

    def test_self_update_validates_a_new_copy_with_the_service_check_off(self) -> None:
        self.fake_systemctl("inactive", 3)  # what `--service` would fail on, were the probe to ask it
        settings = supervisor.load_settings(self.registry)
        with (mock.patch.object(supervisor, "newer_installed_copy", return_value=SCRIPT),
              mock.patch.object(supervisor.os, "execv") as execv, contextlib.redirect_stdout(io.StringIO())):
            supervisor.maybe_self_update(settings, busy=False)
        execv.assert_called_once()
        self.assertEqual(execv.call_args.args[1][-1], "serve")

    def test_the_launcher_starts_an_installed_copy_with_no_service_running(self) -> None:
        self.fake_systemctl("inactive", 3)
        home = self.base / "home"
        version_dir = home / ".claude/plugins/cache/market/research/9.9.9"
        version_dir.parent.mkdir(parents=True)
        version_dir.symlink_to(ROOT / "plugins/research")
        launcher = ROOT / "plugins/research/skills/sweep-supervisor/assets/launch_supervisor.py"
        env = {**os.environ, "HOME": str(home), "RIGSYNC_REGISTRY": str(self.registry), "PYTHONDONTWRITEBYTECODE": "1"}
        proc = subprocess.run([sys.executable, str(launcher), "waves"], capture_output=True, text=True, env=env, cwd=self.hub)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("launch_supervisor: starting", proc.stdout)
        self.assertFalse((self.base / "systemctl.log").exists())

    def test_the_docs_name_the_service_check_at_the_gates(self) -> None:
        skills = ROOT / "plugins/research/skills"
        self.assertIn("`sweep-supervisor check --service` passes", (skills / "sweep-dispatch/SKILL.md").read_text())
        self.assertIn("`check --service` fails", (skills / "sweep-supervisor/SKILL.md").read_text())

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
