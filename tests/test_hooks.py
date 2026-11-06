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
NOTE = ROOT / "plugins/research/hooks/orchestration_note.py"
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

    def marker(self, wave_id: str, **fields) -> None:
        """What a running `watch` keeps fresh in the wave's state directory."""
        marker = {"pid": os.getpid(), "host": os.uname().nodename, "until": time.time() + 120, **fields}
        (self.state / wave_id / "watching.json").write_text(json.dumps(marker))

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
        self.marker("20261016-101500")
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

    def test_silent_for_a_supervisor_agent(self) -> None:
        self.wave("20261016-101500", ["running"])
        proc, _ = run(WAVE, self.payload(), {**self.env, "SWEEP_SUPERVISOR_MODEL": "opus"})
        self.assertEqual((proc.returncode, proc.stdout), (0, ""))

    def test_facts_carry_no_instructions(self) -> None:
        self.wave("20261016-101500", ["running"])
        self.snapshot("20261016-101500")
        for followed in (False, True):
            if followed:
                self.marker("20261016-101500")
            text = re.sub(r"`[^`]*`", "", context(run(WAVE, self.payload(), self.env)[0]))  # a command's path is not advice
            for word in ("keep", "re-arm", "start", "post", "without asking", "must", "printout", "wake-up", "should", "run "):
                self.assertNotIn(word, text.lower())

    def test_without_a_snapshot_or_viewer_port_the_line_says_less(self) -> None:
        self.wave("20261016-101500", ["done", "queued"])
        self.marker("20261016-101500")
        self.registry.write_text('[board]\nroot = "/x"\nrigs = []\n')
        self.assertEqual(context(run(WAVE, self.payload(), self.env)[0]),
                         "Live wave 20261016-101500 in this project: 1/2 done, ETA not computed yet.")

    def watch_command(self, scope: str) -> str:
        return f"`python3 {WAVE.with_name('supervisor.py')} --root {self.root} watch {scope}`"

    def test_a_wave_no_watch_follows_says_so_with_the_exact_command(self) -> None:
        self.wave("20261016-101500", ["done", "queued"])
        self.assertEqual(
            context(run(WAVE, self.payload(), self.env)[0]),
            "Live wave 20261016-101500 in this project: 1/2 done, ETA not computed yet. No chat is following it; "
            f"{self.watch_command('--wave 20261016-101500')} follows it.",
        )

    def test_an_expired_or_dead_marker_is_no_chat_following(self) -> None:
        self.wave("20261016-101500", ["running"])
        dead = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"], capture_output=True, text=True, check=True)
        for name, fields in (("expired", {"until": time.time() - 1}), ("dead pid", {"pid": int(dead.stdout)})):
            with self.subTest(name):
                self.marker("20261016-101500", **fields)
                self.assertIn("No chat is following it", context(run(WAVE, self.payload(), self.env)[0]))
        (self.state / "20261016-101500" / "watching.json").write_text("{torn")
        self.assertIn("No chat is following it", context(run(WAVE, self.payload(), self.env)[0]))

    def test_a_marker_from_another_machine_is_believed_until_it_expires(self) -> None:
        self.wave("20261016-101500", ["running"])
        self.marker("20261016-101500", host="elsewhere", pid=999999999)
        self.assertNotIn("No chat", context(run(WAVE, self.payload(), self.env)[0]))

    def test_a_fresh_marker_from_the_real_watch_silences_the_fact(self) -> None:
        """The marker the supervisor really writes, not this file's copy of its shape."""
        self.wave("20261016-101500", ["running"])
        code = (
            "import importlib.util, sys, pathlib;"
            f"spec = importlib.util.spec_from_file_location('s', {str(WAVE.with_name('supervisor.py'))!r});"
            "m = importlib.util.module_from_spec(spec); sys.modules['s'] = m; spec.loader.exec_module(m);"
            f"m.mark_watching(m.Wave(pathlib.Path({str(self.root)!r}), '20261016-101500'), 20);"
            "print('marked', flush=True); sys.stdin.read()"
        )
        proc = subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(proc.stdout.readline().strip(), "marked")
            self.assertNotIn("No chat", context(run(WAVE, self.payload(), self.env)[0]))
        finally:
            proc.communicate("")
        self.assertIn("No chat is following it", context(run(WAVE, self.payload(), self.env)[0]), "the watch process is gone")

    def test_two_live_waves_nobody_follows_get_one_line_each_and_one_command(self) -> None:
        self.wave("20261016-101500", ["running"])
        self.wave("20261016-111500", ["queued"])
        lines = context(run(WAVE, self.payload(), self.env)[0]).splitlines()
        self.assertEqual(len(lines), 3)
        self.assertNotIn("No chat", lines[0] + lines[1])
        self.assertEqual(lines[2], f"No chat is following these waves; {self.watch_command('--all')} follows them all.")

    def test_two_live_waves_both_followed_get_one_line_each(self) -> None:
        self.wave("20261016-101500", ["running"])
        self.wave("20261016-111500", ["queued"])
        self.marker("20261016-101500")
        self.marker("20261016-111500")
        lines = context(run(WAVE, self.payload(), self.env)[0]).splitlines()
        self.assertEqual(len(lines), 2)
        self.assertNotIn("No chat", "\n".join(lines))

    def test_when_only_one_of_two_is_followed_its_own_command_is_named_not_all(self) -> None:
        self.wave("20261016-101500", ["running"])
        self.wave("20261016-111500", ["queued"])
        self.marker("20261016-101500")
        lines = context(run(WAVE, self.payload(), self.env)[0]).splitlines()
        self.assertEqual(len(lines), 2)
        self.assertNotIn("No chat", lines[0])
        self.assertIn(self.watch_command("--wave 20261016-111500"), lines[1])

    def test_a_live_wave_with_a_snapshot_is_fast_enough_for_the_hook_timeout(self) -> None:
        self.wave("20261016-101500", ["running"])
        self.snapshot("20261016-101500")
        self.assertLess(run(WAVE, self.payload(), self.env)[1], 2.5)

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
        self.marker("20261016-101500")
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
        self.assertIn('return self.dir / "watching.json"', supervisor)
        self.assertIn('WAVES_DIR = ".waves"', rigsync)
        self.assertIn(r'WAVE_RE = re.compile(r"^\d{8}-\d{6}$")', rigsync)
        self.assertIn('RIGSYNC_REGISTRY", "~/.config/rigsync/machines.toml"', supervisor)


class OrchestrationNoteTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def tag(self, categories: str) -> None:
        (self.root / ".project.toml").write_text(f"categories = {categories}\n")

    def test_states_the_mode_in_a_research_project(self) -> None:
        self.tag('["research"]')
        text = context(run(NOTE, {"cwd": str(self.root)})[0])
        self.assertIn("orchestrator mode", text)
        self.assertIn("One writer per checkout", text)

    def test_finds_the_tag_from_a_subdirectory(self) -> None:
        self.tag('["research"]')
        below = self.root / "code/001"
        below.mkdir(parents=True)
        self.assertIn("orchestrator mode", context(run(NOTE, {"cwd": str(below)})[0]))

    def test_silent_everywhere_else(self) -> None:
        for categories in (None, "[]", '["teaching"]'):
            with self.subTest(categories=categories):
                if categories is not None:
                    self.tag(categories)
                proc = run(NOTE, {"cwd": str(self.root)})[0]
                self.assertEqual((proc.returncode, proc.stdout), (0, ""))

    def test_a_broken_tag_file_or_payload_says_nothing(self) -> None:
        (self.root / ".project.toml").write_text("categories = [")
        for proc in (run(NOTE, {"cwd": str(self.root)})[0], run(NOTE, raw="not json")[0], run(NOTE, {})[0]):
            self.assertEqual((proc.returncode, proc.stdout), (0, ""))

    def test_silent_for_a_supervisor_agent(self) -> None:
        self.tag('["research"]')
        proc = run(NOTE, {"cwd": str(self.root)}, {"SWEEP_SUPERVISOR_MODEL": "opus"})[0]
        self.assertEqual((proc.returncode, proc.stdout), (0, ""))

    def test_the_note_names_no_host(self) -> None:
        text = (NOTE.parent / "orchestration.md").read_text().lower()
        for host in ("claude", "codex"):
            self.assertNotIn(host, text)


class HooksJsonTests(unittest.TestCase):
    def test_each_plugin_hook_runs_its_reminder(self) -> None:
        expected = {
            "research": {"SessionStart": [NOTE, WAVE]},
        }
        for plugin, events in expected.items():
            plugin_root = ROOT / "plugins" / plugin
            spec = json.loads((plugin_root / "hooks/hooks.json").read_text())
            self.assertEqual(set(spec["hooks"]), set(events), plugin)
            for event, scripts in events.items():
                commands = [h["command"] for g in spec["hooks"][event] for h in g["hooks"]]
                targets = [plugin_root / rel for c in commands for rel in HOOK_SCRIPT_RE.findall(c)]
                self.assertEqual(targets, scripts, f"{plugin} {event}")

    def test_the_reminder_runs_at_every_kind_of_session_start(self) -> None:
        spec = json.loads((ROOT / "plugins/research/hooks/hooks.json").read_text())
        matcher = spec["hooks"]["SessionStart"][0]["matcher"]
        self.assertEqual(set(matcher.split("|")), {"startup", "resume", "clear", "compact", "fork"})
        self.assertEqual(spec["hooks"]["SessionStart"][0]["hooks"][0]["timeout"], 5)

    def test_core_ships_no_hooks(self) -> None:
        core = ROOT / "plugins/core"
        self.assertFalse((core / "hooks").exists())
        self.assertFalse((core / "skills/progress-updates/scripts/long_task_reminder.py").exists())
        for manifest in (".claude-plugin/plugin.json", ".codex-plugin/plugin.json"):
            self.assertNotIn("hooks", json.loads((core / manifest).read_text()), manifest)

if __name__ == "__main__":
    unittest.main()
