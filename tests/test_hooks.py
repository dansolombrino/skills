"""Behavior of the plugin hooks: the live-wave reminder (research); core ships no hooks."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WAVE = ROOT / "plugins/research/skills/sweep-supervisor/scripts/wave_reminder.py"
HOOK_SCRIPT_RE = re.compile(r"\$\{CLAUDE_PLUGIN_ROOT\}/([^\"'\s]+)")


def run(script: Path, payload: object = None, env: dict[str, str] | None = None, raw: str | None = None):
    full = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE_", "PLUGIN_"))}
    full.update(env or {}, PYTHONDONTWRITEBYTECODE="1")
    started = time.monotonic()
    proc = subprocess.run(
        [sys.executable, "-I", str(script)],
        input=raw if raw is not None else json.dumps(payload),
        capture_output=True,
        text=True,
        env=full,
    )
    return proc, time.monotonic() - started


def context(proc) -> str | None:
    return json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"] if proc.stdout.strip() else None


class WaveReminderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.state = self.root / ".waves" / "_state"
        self.registry = self.root / "machines.toml"  # absent unless a test writes it: never the real one
        self.env = {"RIGSYNC_REGISTRY": str(self.registry)}

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def wave(self, wave_id: str, states: list[str], finished_at: str | None = None) -> None:
        directory = self.state / wave_id
        directory.mkdir(parents=True)
        queue = {"wave_id": wave_id, "finished_at": finished_at, "runs": [{"state": s} for s in states]}
        (directory / "queue.json").write_text(json.dumps(queue))

    def snapshot(self, wave_id: str, **fields) -> str:
        now = datetime.now().astimezone()
        snapshot = {
            "written_at": now.isoformat(timespec="seconds"),
            "counts": {"done": 2, "failed": 1}, "total": 5,
            "wave_eta": (now + timedelta(hours=2)).isoformat(timespec="seconds"), "wave_eta_basis": "feeder simulation",
            "finished_at": None, "failed": [{"run": "m3", "note": "exit 1"}], "questions": [],
            "lanes": [{"kind": "lane", "rig": "hub", "gpu": "0", "state": "stale", "run": "m4", "done": 2, "lane_free_at": None, "problem": None}],
            "rigs": {"hub": {"kind": "lanes", "done": 2}}, "certs": {}, **fields,
        }
        (self.state / wave_id / "snapshot.json").write_text(json.dumps(snapshot))
        return (now + timedelta(hours=2)).strftime("%H:%M")

    def payload(self, event: str = "SessionStart", cwd: Path | None = None) -> dict:
        return {"hook_event_name": event, "cwd": str(cwd or self.root), "session_id": "s1"}

    def test_project_without_waves_is_silent(self) -> None:
        proc, _ = run(WAVE, self.payload(), self.env)
        self.assertEqual((proc.returncode, proc.stdout), (0, ""))

    def test_finished_wave_is_silent(self) -> None:
        self.wave("20261016-101500", ["done", "done"], finished_at="2026-10-16T12:00:00")
        self.assertEqual(run(WAVE, self.payload(), self.env)[0].stdout, "")

    def test_a_live_wave_is_one_line_of_facts_from_the_supervisors_view(self) -> None:
        self.wave("20261016-101500", ["done", "done", "failed", "running", "queued"])
        eta = self.snapshot("20261016-101500")
        self.registry.write_text('[board]\nroot = "/x"\nrigs = []\nport = 40970\nbind = "0.0.0.0"\n')
        sub = self.root / "src" / "deep"
        sub.mkdir(parents=True)
        out = json.loads(run(WAVE, self.payload(cwd=sub), self.env)[0].stdout)["hookSpecificOutput"]
        self.assertEqual(out["hookEventName"], "SessionStart")
        self.assertEqual(
            out["additionalContext"],
            f"Live wave 20261016-101500 in this project: 2/5 done, ETA {eta}, 2 warnings. "
            "Dashboard: http://localhost:40970/#experiments.",
        )

    def test_facts_carry_no_instructions(self) -> None:
        self.wave("20261016-101500", ["running"])
        self.snapshot("20261016-101500")
        text = context(run(WAVE, self.payload(), self.env)[0])
        for word in ("keep", "re-arm", "start", "post", "without asking", "must", "printout", "wake-up"):
            self.assertNotIn(word, text.lower())

    def test_without_a_snapshot_or_viewer_port_the_line_says_less(self) -> None:
        self.wave("20261016-101500", ["done", "queued"])
        self.registry.write_text('[board]\nroot = "/x"\nrigs = []\n')
        self.assertEqual(context(run(WAVE, self.payload(), self.env)[0]),
                         "Live wave 20261016-101500 in this project: 1/2 done, ETA not computed yet.")

    def test_two_live_waves_get_one_line_each(self) -> None:
        self.wave("20261016-101500", ["running"])
        self.wave("20261016-111500", ["queued"])
        self.assertEqual(len(context(run(WAVE, self.payload(), self.env)[0]).splitlines()), 2)

    def test_every_other_hook_event_is_silent(self) -> None:
        self.wave("20261016-101500", ["running"])
        register = "python3 /x/supervisor.py register --wave 20261016-101500 --manifest m.json --confirm"
        for payload in (
            self.payload("UserPromptSubmit"),
            {**self.payload("PostToolUse"), "tool_name": "Bash", "tool_input": {"command": register}},
            self.payload("Stop"),
        ):
            proc, _ = run(WAVE, payload, self.env)
            self.assertEqual((proc.returncode, proc.stdout), (0, ""), payload["hook_event_name"])

    def test_non_wave_dirs_and_corrupt_queues_are_ignored(self) -> None:
        (self.state / "notawave").mkdir(parents=True)
        (self.state / "notawave" / "queue.json").write_text("{}")
        (self.state / "20261016-101500").mkdir()
        (self.state / "20261016-101500" / "queue.json").write_text("{not json")
        proc, _ = run(WAVE, self.payload(), self.env)
        self.assertEqual((proc.returncode, proc.stdout), (0, ""))

    def test_a_damaged_snapshot_degrades_to_the_queue_counts(self) -> None:
        self.wave("20261016-101500", ["done", "queued"])
        (self.state / "20261016-101500" / "snapshot.json").write_text('{"counts": 3}')
        self.assertEqual(context(run(WAVE, self.payload(), self.env)[0]),
                         "Live wave 20261016-101500 in this project: 1/2 done, ETA not computed yet.")

    def test_garbage_stdin_never_fails(self) -> None:
        proc, _ = run(WAVE, raw="garbage")
        self.assertEqual((proc.returncode, proc.stdout), (0, ""))

    def test_is_fast(self) -> None:
        for i in range(20):
            self.wave(f"20261016-1015{i:02d}", ["done"], finished_at="x")
        self.assertLess(run(WAVE, self.payload(), self.env)[1], 0.5)

    def test_copied_constants_match_the_supervisor(self) -> None:
        supervisor = (WAVE.parent / "supervisor.py").read_text()
        rigsync = (ROOT / "plugins/research/skills/rig-sync/scripts/rigsync.py").read_text()
        self.assertIn('STATE_DIR = "_state"', supervisor)
        self.assertIn('return self.dir / "queue.json"', supervisor)
        self.assertIn('return self.dir / "snapshot.json"', supervisor)
        self.assertIn('WAVES_DIR = ".waves"', rigsync)
        self.assertIn(r'WAVE_RE = re.compile(r"^\d{8}-\d{6}$")', rigsync)
        self.assertIn('RIGSYNC_REGISTRY", "~/.config/rigsync/machines.toml"', supervisor)


class HooksJsonTests(unittest.TestCase):
    def test_each_plugin_hook_runs_its_reminder(self) -> None:
        expected = {
            "research": {"SessionStart": WAVE},
        }
        for plugin, events in expected.items():
            plugin_root = ROOT / "plugins" / plugin
            spec = json.loads((plugin_root / "hooks/hooks.json").read_text())
            self.assertEqual(set(spec["hooks"]), set(events), plugin)
            for event, script in events.items():
                commands = [h["command"] for g in spec["hooks"][event] for h in g["hooks"]]
                targets = [plugin_root / rel for c in commands for rel in HOOK_SCRIPT_RE.findall(c)]
                self.assertEqual(targets, [script], f"{plugin} {event}")

    def test_core_ships_no_hooks(self) -> None:
        core = ROOT / "plugins/core"
        self.assertFalse((core / "hooks").exists())
        self.assertFalse((core / "skills/progress-updates/scripts/long_task_reminder.py").exists())
        for manifest in (".claude-plugin/plugin.json", ".codex-plugin/plugin.json"):
            self.assertNotIn("hooks", json.loads((core / manifest).read_text()), manifest)

if __name__ == "__main__":
    unittest.main()
