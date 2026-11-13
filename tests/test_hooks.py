"""Behavior of the plugin hooks: the live-wave reminder (research) and its omp extension; core ships no hooks."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
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
OMP_EXTENSION = ROOT / "plugins/research/hooks/omp_session_start.ts"
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
        eta = now + timedelta(hours=2)
        # The reminder names the day too when the ETA falls on another day than today.
        return eta.strftime("%H:%M" if eta.date() == now.date() else "%m-%d %H:%M")

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


class DriftFactTests(unittest.TestCase):
    """The checkout-level facts: local files plus one git config read, in a main checkout with an EXPERIMENTS.md."""

    WAVE = "20261016-101500"
    OTHER = "20261017-101500"

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.env = {"RIGSYNC_REGISTRY": str(self.root / "machines.toml")}
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.root)], check=True)
        self.guard()

    def guard(self, installed: bool = True) -> None:
        args = ["config", "core.hooksPath", ".githooks"] if installed else ["config", "--unset", "core.hooksPath"]
        subprocess.run(["git", "-C", str(self.root), *args], check=True)
        hook = self.root / ".githooks" / "pre-commit"
        if installed and not hook.exists():
            hook.parent.mkdir()
            hook.write_text("#!/bin/sh\n")
            subprocess.run(["git", "-C", str(self.root), "add", ".githooks/pre-commit"], check=True)

    def test_the_guard_needs_the_path_and_the_tracked_hook(self) -> None:
        self.table()
        subprocess.run(["git", "-C", str(self.root), "config", "core.hooksPath", "elsewhere"], check=True)
        self.assertEqual(self.facts(), "The journal guard is not installed in this checkout: core.hooksPath is elsewhere, not .githooks.")
        self.guard()
        subprocess.run(["git", "-C", str(self.root), "rm", "-q", "--cached", ".githooks/pre-commit"], check=True)
        self.assertEqual(self.facts(), "The journal guard is not installed in this checkout: .githooks/pre-commit is not tracked.")

    def table(self, *rows: tuple[str, str]) -> None:
        body = "\n".join(f"| rid | {wave} | {status} | n |" for wave, status in rows)
        (self.root / "EXPERIMENTS.md").write_text(f"## 000_x\nrun_id version: 1 — a\n\n| run_id | wave | status | notes |\n|---|---|---|---|\n{body}\n\nProse.\n")

    def queue(self, wave: str, finished: bool) -> None:
        state = self.root / ".waves" / "_state" / wave
        state.mkdir(parents=True)
        (state / "queue.json").write_text(json.dumps({"finished_at": "2026-10-16T12:00:00" if finished else None, "runs": []}))

    def facts(self, env: dict | None = None, cwd: Path | None = None) -> str | None:
        proc, _ = run(WAVE, {"hook_event_name": "SessionStart", "cwd": str(cwd or self.root)}, {**self.env, **(env or {})})
        self.assertEqual(proc.returncode, 0)
        return context(proc)

    def test_a_tidy_project_is_silent(self) -> None:
        self.table((self.WAVE, "done"))
        self.queue(self.WAVE, finished=True)
        (self.root / "sync.toml").write_text('[git]\nremote = "origin"\nbranch = "main"\n')
        self.assertIsNone(self.facts())

    def test_silent_without_an_experiments_file(self) -> None:
        self.guard(installed=False)
        (self.root / "sync.toml").write_text('[git]\nbranch = "other"\n')
        self.assertIsNone(self.facts())

    def test_silent_for_a_supervisor_agent(self) -> None:
        self.guard(installed=False)
        self.table((self.WAVE, "inpr"))
        self.assertIsNone(self.facts({"SWEEP_SUPERVISOR_MODEL": "opus"}))

    def test_inpr_rows_of_a_closed_wave_are_counted(self) -> None:
        self.table((self.WAVE, "inpr"), (self.WAVE, "inpr"), (self.WAVE, "done"), (self.OTHER, "inpr"))
        self.queue(self.WAVE, finished=True)
        self.queue(self.OTHER, finished=False)
        self.assertEqual(self.facts().splitlines()[-1], f"EXPERIMENTS.md has inpr rows in closed waves: {self.WAVE} (2).")

    def test_a_wave_with_no_supervisor_record_is_not_called_closed(self) -> None:
        self.table((self.WAVE, "inpr"))
        self.assertIsNone(self.facts())

    def test_worktrees_of_finished_waves_are_named(self) -> None:
        self.table((self.WAVE, "done"), (self.OTHER, "failed"), ("20261018-101500", "inpr"))
        self.queue(self.WAVE, finished=True)
        for wave in (self.WAVE, self.OTHER, "20261018-101500"):
            (self.root / ".waves" / wave).mkdir(parents=True)
        self.assertEqual(self.facts(), f"Worktrees of finished waves remain: .waves/{self.WAVE}, .waves/{self.OTHER}.")

    def test_a_live_waves_worktree_is_no_fact(self) -> None:
        self.table((self.WAVE, "done"))
        self.queue(self.WAVE, finished=False)
        (self.root / ".waves" / self.WAVE).mkdir(parents=True)
        self.assertNotIn("Worktrees", self.facts())

    def test_long_lists_are_cut(self) -> None:
        waves = [f"20261016-1015{i:02d}" for i in range(7)]
        self.table(*[(w, "inpr") for w in waves])
        for wave in waves:
            self.queue(wave, finished=True)
        self.assertTrue(self.facts().endswith("and 2 more."))

    def test_an_unset_hooks_path_is_stated(self) -> None:
        self.guard(installed=False)
        self.table()
        self.assertEqual(self.facts(), "The journal guard is not installed in this checkout: core.hooksPath is unset.")

    def test_a_hook_path_outside_a_repository_says_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as other:
            plain = Path(other)
            (plain / ".git").mkdir()  # not a repository git recognises: exit 128, not "unset"
            (plain / "EXPERIMENTS.md").write_text("# nothing\n")
            self.assertIsNone(self.facts(cwd=plain))

    def test_a_checkout_off_the_projects_branch_is_stated(self) -> None:
        self.table()
        (self.root / "sync.toml").write_text('[git]\nremote = "origin"\nbranch = "trunk"\n')
        self.assertEqual(self.facts(), "This checkout is on branch main; sync.toml names trunk as the project's branch.")
        (self.root / ".git" / "HEAD").write_text("0" * 40 + "\n")
        self.assertEqual(self.facts(), "This checkout is on a detached HEAD; sync.toml names trunk as the project's branch.")

    def test_a_linked_worktree_gets_no_facts(self) -> None:
        linked = self.root / "linked"
        linked.mkdir()
        (linked / ".git").write_text("gitdir: /elsewhere\n")
        (linked / "EXPERIMENTS.md").write_text(f"| wave | status |\n|---|---|\n| {self.WAVE} | inpr |\n")
        self.guard(installed=False)
        self.assertIsNone(self.facts(cwd=linked))

    def test_it_comes_after_the_live_wave_lines(self) -> None:
        self.guard(installed=False)
        self.table((self.WAVE, "inpr"))
        self.queue(self.WAVE, finished=False)
        lines = self.facts().splitlines()
        self.assertTrue(lines[0].startswith(f"Live wave {self.WAVE}"))
        self.assertEqual(lines[-1], "The journal guard is not installed in this checkout: core.hooksPath is unset.")

    def test_facts_carry_no_instructions(self) -> None:
        self.guard(installed=False)
        self.table((self.WAVE, "inpr"), (self.OTHER, "done"))
        self.queue(self.WAVE, finished=True)
        (self.root / ".waves" / self.OTHER).mkdir(parents=True)
        (self.root / "sync.toml").write_text('[git]\nbranch = "trunk"\n')
        text = self.facts().lower()
        self.assertEqual(len(text.splitlines()), 4)
        for word in ("keep", "re-arm", "start", "post", "without asking", "must", "printout", "wake-up", "should", "run ", "prune", "reconcile", "install it"):
            self.assertNotIn(word, text)

    def test_a_large_table_is_fast(self) -> None:
        self.guard(installed=False)
        self.table(*[(self.WAVE, "done")] * 5000)
        self.assertLess(run(WAVE, self.payload_for_speed(), self.env)[1], 0.5)

    def payload_for_speed(self) -> dict:
        return {"hook_event_name": "SessionStart", "cwd": str(self.root)}

    def test_the_row_reader_agrees_with_reconcile(self) -> None:
        spec = importlib.util.spec_from_file_location("reconcile_for_hooks", ROOT / "plugins/research/skills/experiments-tracking/scripts/reconcile.py")
        reconcile = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = reconcile
        spec.loader.exec_module(reconcile)
        spec = importlib.util.spec_from_file_location("wave_reminder_for_hooks", WAVE)
        reminder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(reminder)
        skill = (ROOT / "plugins/research/skills/experiments-tracking/SKILL.md").read_text()
        example = skill.split("```markdown\n", 1)[1].split("\n```", 1)[0]
        ours = [(row["wave"], row["status"]) for row in reminder.table_rows(example)]
        theirs = [(row.wave, row.status) for section in reconcile.parse(example).sections for row in section.rows]
        self.assertEqual(ours, theirs)
        self.assertEqual(len(ours), 3)


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

    def test_the_omp_extension_runs_the_same_scripts(self) -> None:
        research = ROOT / "plugins/research"
        spec = json.loads((research / "hooks/hooks.json").read_text())
        commands = [h["command"] for g in spec["hooks"]["SessionStart"] for h in g["hooks"]]
        scripts = re.search(r"const SCRIPTS = \[([^\]]*)\]", OMP_EXTENSION.read_text()).group(1)
        self.assertEqual(re.findall(r'"([^"]+)"', scripts), [rel for c in commands for rel in HOOK_SCRIPT_RE.findall(c)])
        manifest = json.loads((research / "package.json").read_text())
        self.assertEqual([(research / e).resolve() for e in manifest["omp"]["extensions"]], [OMP_EXTENSION])

    def test_core_ships_no_hooks(self) -> None:
        core = ROOT / "plugins/core"
        self.assertFalse((core / "hooks").exists())
        self.assertFalse((core / "skills/progress-updates/scripts/long_task_reminder.py").exists())
        for manifest in (".claude-plugin/plugin.json", ".codex-plugin/plugin.json"):
            self.assertNotIn("hooks", json.loads((core / manifest).read_text()), manifest)


# Drives the extension's default factory with a stand-in for omp's ExtensionAPI: each step fires one
# event's handlers with a stand-in context and records what they return and what they sent.
OMP_HARNESS = """
const [extension, steps] = process.argv.slice(2);
const handlers = new Map();
const sent = [];
const pi = {
    on(name, handler) { handlers.set(name, [...(handlers.get(name) ?? []), handler]); },
    sendMessage(message, options) { sent.push({ message, options }); },
};
(await import(extension)).default(pi);
const out = [];
for (const [name, event, c] of JSON.parse(steps)) {
    const ctx = {
        cwd: c.cwd,
        agent: { kind: c.kind ?? "main" },
        sessionManager: {
            getSessionId: () => "s1",
            getSessionFile: () => "/sessions/s1.jsonl",
            getBranch: () => (c.messages ? [{ type: "message" }] : []),
        },
    };
    let result = null;
    for (const handler of handlers.get(name) ?? []) result = (await handler(event, ctx)) ?? result;
    out.push({ result, sent: sent.splice(0) });
}
console.log(JSON.stringify(out));
"""


@unittest.skipUnless(shutil.which("bun"), "needs bun to load the omp extension")
class OmpSessionStartTests(unittest.TestCase):
    """plugins/research/hooks/omp_session_start.ts: the SessionStart hooks, run as an omp extension."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        self.research = self.tmp / "research"
        self.plain = self.tmp / "plain"
        for project, tag in ((self.research, '["research"]'), (self.plain, '["teaching"]')):
            project.mkdir()
            (project / ".project.toml").write_text(f"categories = {tag}\n")
        self.harness = self.tmp / "harness.mjs"
        self.harness.write_text(OMP_HARNESS)

    def drive(self, steps: list, extension: Path = OMP_EXTENSION) -> list[dict]:
        env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE_", "SWEEP_SUPERVISOR_"))}
        env.update(RIGSYNC_REGISTRY=str(self.tmp / "machines.toml"), PYTHONDONTWRITEBYTECODE="1")
        proc = subprocess.run(["bun", str(self.harness), str(extension), json.dumps(steps)],
                              capture_output=True, text=True, env=env, timeout=60, check=True)
        return json.loads(proc.stdout)

    @staticmethod
    def prompt(cwd: Path) -> list:
        return ["before_agent_start", {"reason": "prompt", "prompt": "hi"}, {"cwd": str(cwd)}]

    @staticmethod
    def turn(cwd: Path) -> list:
        return ["turn_start", {}, {"cwd": str(cwd)}]

    def test_a_research_session_gets_the_note_with_its_first_prompt_only(self) -> None:
        r = str(self.research)
        out = self.drive([["session_start", {}, {"cwd": r}], self.prompt(self.research), self.turn(self.research), self.prompt(self.research)])
        message = out[1]["result"]["message"]
        self.assertIn("orchestrator mode", message["content"])
        self.assertEqual((message["customType"], message["display"]), ("research-session-start", False))
        self.assertIsNone(out[3]["result"])
        self.assertEqual([step["sent"] for step in out], [[], [], [], []])

    def test_a_prompt_prepared_twice_carries_the_note_both_times(self) -> None:
        out = self.drive([["session_start", {}, {"cwd": str(self.research)}], self.prompt(self.research), self.prompt(self.research)])
        self.assertIn("orchestrator mode", out[2]["result"]["message"]["content"])

    def test_events_before_a_prompt_give_one_note(self) -> None:
        r = str(self.research)
        out = self.drive([["session_start", {}, {"cwd": r}], ["session_switch", {"reason": "new"}, {"cwd": r}],
                          self.prompt(self.research), self.turn(self.research), self.prompt(self.research)])
        self.assertIn("orchestrator mode", out[2]["result"]["message"]["content"])
        self.assertIsNone(out[4]["result"])

    def test_a_compaction_sends_the_note_into_the_next_turn(self) -> None:
        out = self.drive([["session_compact", {}, {"cwd": str(self.research)}]])
        [sent] = out[0]["sent"]
        self.assertIn("orchestrator mode", sent["message"]["content"])
        self.assertEqual(sent["options"], {"deliverAs": "nextTurn"})

    def test_silent_outside_research_for_subagents_and_for_a_revived_subagent(self) -> None:
        p, r = str(self.plain), str(self.research)
        out = self.drive([["session_start", {}, {"cwd": p}], self.prompt(self.plain), ["session_compact", {}, {"cwd": p}],
                          ["session_start", {}, {"cwd": r, "kind": "sub"}], self.prompt(self.research),
                          ["session_compact", {}, {"cwd": r, "kind": "sub"}],
                          ["session_start", {}, {"cwd": r}], ["before_agent_start", {"reason": "revive", "prompt": ""}, {"cwd": r}]])
        self.assertEqual(out, [{"result": None, "sent": []}] * len(out))

    def test_each_event_runs_both_scripts_with_the_hook_contract(self) -> None:
        """Stand-in scripts echo the payload and environment the extension gives them."""
        root = self.tmp / "plugin"
        (root / "hooks").mkdir(parents=True)
        (root / "skills/sweep-supervisor/scripts").mkdir(parents=True)
        extension = root / "hooks/omp_session_start.ts"
        shutil.copy(OMP_EXTENSION, extension)
        (root / "hooks/orchestration_note.py").write_text(
            "import json, os, sys\np = json.load(sys.stdin)\n"
            "said = f\"{p['hook_event_name']} {p['source']} {p['session_id']} {p['cwd'] == os.getcwd()} "
            "{os.environ['CLAUDE_PLUGIN_ROOT']} {os.environ['CLAUDE_PROJECT_DIR'] == os.getcwd()}\"\n"
            "print(json.dumps({'hookSpecificOutput': {'hookEventName': 'SessionStart', 'additionalContext': said}}))\n")
        (root / "skills/sweep-supervisor/scripts/wave_reminder.py").write_text(  # plain stdout; a failure says nothing
            "import json, sys\np = json.load(sys.stdin)\nprint('plain', p['source'])\nsys.exit(1 if p['source'] == 'compact' else 0)\n")
        r = str(self.research)
        events = [
            (["session_start", {}, {"cwd": r}], "startup"),
            (["session_start", {}, {"cwd": r, "messages": True}], "resume"),
            (["session_switch", {"reason": "new"}, {"cwd": r}], "clear"),
            (["session_switch", {"reason": "resume"}, {"cwd": r}], "resume"),
            (["session_switch", {"reason": "fork"}, {"cwd": r}], "fork"),
            (["session_branch", {"reason": "fork"}, {"cwd": r}], "fork"),
        ]
        steps = [s for event, _ in events for s in (event, self.prompt(self.research), self.turn(self.research))]
        out = self.drive(steps + [["session_compact", {}, {"cwd": r}]], extension)
        for i, (_, source) in enumerate(events):
            with self.subTest(source=source):
                self.assertEqual(out[3 * i + 1]["result"]["message"]["content"],
                                 f"<system-reminder>\nSessionStart hook additional context: SessionStart {source} s1 True {root} True"
                                 f"\n\nplain {source}\n</system-reminder>")
        self.assertEqual(out[-1]["sent"][0]["message"]["content"],
                         f"<system-reminder>\nSessionStart hook additional context: SessionStart compact s1 True {root} True\n</system-reminder>")


if __name__ == "__main__":
    unittest.main()
