"""Behavior of the plugin hooks: the live-wave reminder (research) and the long-task reminder (core)."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WAVE = ROOT / "plugins/research/skills/sweep-supervisor/scripts/wave_reminder.py"
LONG = ROOT / "plugins/core/skills/progress-updates/scripts/long_task_reminder.py"
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
        self.env = {"CLAUDE_PLUGIN_DATA": str(self.root / "plugin-data")}

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def wave(self, wave_id: str, states: list[str], finished_at: str | None = None) -> None:
        directory = self.state / wave_id
        directory.mkdir(parents=True)
        queue = {"wave_id": wave_id, "finished_at": finished_at, "runs": [{"state": s} for s in states]}
        (directory / "queue.json").write_text(json.dumps(queue))

    def payload(self, event: str = "SessionStart", cwd: Path | None = None, session: str = "s1") -> dict:
        return {"hook_event_name": event, "cwd": str(cwd or self.root), "session_id": session}

    def test_project_without_waves_is_silent(self) -> None:
        proc, _ = run(WAVE, self.payload())
        self.assertEqual((proc.returncode, proc.stdout), (0, ""))

    def test_finished_wave_is_silent(self) -> None:
        self.wave("20261016-101500", ["done", "done"], finished_at="2026-10-16T12:00:00")
        self.assertEqual(run(WAVE, self.payload())[0].stdout, "")

    def test_live_wave_reminds_with_counts_from_a_subdirectory(self) -> None:
        self.wave("20261016-101500", ["done", "failed", "excluded", "running", "queued"])
        sub = self.root / "src" / "deep"
        sub.mkdir(parents=True)
        out = json.loads(run(WAVE, self.payload(cwd=sub))[0].stdout)["hookSpecificOutput"]
        self.assertEqual(out["hookEventName"], "SessionStart")
        self.assertIn("Wave 20261016-101500 is live (3/5", out["additionalContext"])
        self.assertIn("sweep-supervisor", out["additionalContext"])
        self.assertIn("table --wave 20261016-101500", out["additionalContext"])

    def test_two_live_waves_get_one_line_each(self) -> None:
        self.wave("20261016-101500", ["running"])
        self.wave("20261016-111500", ["queued"])
        self.assertEqual(len(context(run(WAVE, self.payload())[0]).splitlines()), 2)

    def test_non_wave_dirs_and_corrupt_queues_are_ignored(self) -> None:
        (self.state / "notawave").mkdir(parents=True)
        (self.state / "notawave" / "queue.json").write_text("{}")
        (self.state / "20261016-101500").mkdir()
        (self.state / "20261016-101500" / "queue.json").write_text("{not json")
        proc, _ = run(WAVE, self.payload())
        self.assertEqual((proc.returncode, proc.stdout), (0, ""))

    def test_prompts_are_throttled_per_session(self) -> None:
        self.wave("20261016-101500", ["running"])
        self.assertIsNotNone(context(run(WAVE, self.payload("UserPromptSubmit"), self.env)[0]))
        self.assertIsNone(context(run(WAVE, self.payload("UserPromptSubmit"), self.env)[0]))
        self.assertIsNotNone(context(run(WAVE, self.payload("UserPromptSubmit", session="s2"), self.env)[0]))
        self.assertIsNotNone(context(run(WAVE, self.payload("SessionStart"), self.env)[0]))

    def test_session_start_covers_the_first_prompt(self) -> None:
        self.wave("20261016-101500", ["running"])
        self.assertIsNotNone(context(run(WAVE, self.payload("SessionStart"), self.env)[0]))
        self.assertIsNone(context(run(WAVE, self.payload("UserPromptSubmit"), self.env)[0]))

    def test_a_new_wave_breaks_the_throttle(self) -> None:
        self.wave("20261016-101500", ["running"])
        run(WAVE, self.payload("UserPromptSubmit"), self.env)
        self.wave("20261016-111500", ["queued"])
        self.assertIsNotNone(context(run(WAVE, self.payload("UserPromptSubmit"), self.env)[0]))

    def test_garbage_stdin_never_fails(self) -> None:
        proc, _ = run(WAVE, raw="garbage")
        self.assertEqual((proc.returncode, proc.stdout), (0, ""))

    def test_is_fast(self) -> None:
        for i in range(20):
            self.wave(f"20261016-1015{i:02d}", ["done"], finished_at="x")
        self.assertLess(run(WAVE, self.payload())[1], 0.5)

    def test_copied_constants_match_the_supervisor(self) -> None:
        reminder = WAVE.read_text()
        supervisor = (WAVE.parent / "supervisor.py").read_text()
        rigsync = (ROOT / "plugins/research/skills/rig-sync/scripts/rigsync.py").read_text()
        terminal = re.search(r"^TERMINAL_STATES = \(([^)]*)\)", supervisor, re.M).group(1)
        self.assertEqual(set(re.findall(r'"(\w+)"', terminal)),
                         set(re.findall(r'"(\w+)"', re.search(r"^TERMINAL = \{([^}]*)\}", reminder, re.M).group(1))))
        self.assertIn('STATE_DIR = "_state"', supervisor)
        self.assertIn('return self.dir / "queue.json"', supervisor)
        self.assertIn('WAVES_DIR = ".waves"', rigsync)
        self.assertIn(r'WAVE_RE = re.compile(r"^\d{8}-\d{6}$")', rigsync)


class WaveRegisterTriggerTests(unittest.TestCase):
    def bash(self, command: object) -> dict:
        return {"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_input": {"command": command}}

    def test_confirmed_register_starts_the_printout(self) -> None:
        command = "python3 /x/sweep-supervisor/scripts/supervisor.py register --wave 20261021-120000 --manifest m.json --confirm"
        out = context(run(WAVE, self.bash(command))[0])
        self.assertIsNotNone(out)
        self.assertIn("table --wave 20261021-120000 --cycle --full", out)
        self.assertIn("without asking", out)

    def test_dry_runs_and_other_commands_are_silent(self) -> None:
        for command in [
            "python3 /x/supervisor.py register --wave 20261021-120000 --manifest m.json --dry-run",
            "python3 /x/supervisor.py table --wave 20261021-120000",
            "ls",
        ]:
            self.assertEqual(run(WAVE, self.bash(command))[0].stdout, "", command)

    def test_other_tools_and_garbage_are_silent(self) -> None:
        self.assertEqual(run(WAVE, {"hook_event_name": "PostToolUse", "tool_name": "Write", "tool_input": {}})[0].stdout, "")
        proc, _ = run(WAVE, raw='{"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_input": 3}')
        self.assertEqual((proc.returncode, proc.stdout), (0, ""))


class LongTaskReminderTests(unittest.TestCase):
    def bash(self, command: object, background: bool = False) -> dict:
        return {
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": command, "description": "x", "run_in_background": background},
        }

    def test_quick_commands_are_silent(self) -> None:
        for command in [
            "ls -la", "git status", "python3 -m unittest discover tests", "cat train.log",
            "grep rsync notes.md", "echo 'use scp later'", "ls | grep nohup", "pytest tests/test_train.py",
            "rsync --version | head -1", "torchrun --help", "python3 /p/zip-aware-training/scratch/plot.py",
            "cat >> EXPERIMENTS.md <<'E'\nrsync later\nE\nls", "sed -i 's/a | rsync b/c/' f",
            'curl -s -o /dev/null -w "%{http_code}" https://x',
        ]:
            self.assertEqual(run(LONG, self.bash(command))[0].stdout, "", command)

    def test_long_commands_remind(self) -> None:
        for command in [
            "rsync -a --info=progress2 data/ rig:/data/", "cd x && scp big.tar rig:",
            "nohup python train.py > log 2>&1 &", "torchrun --nproc_per_node 4 main.py",
            "python3 scripts/eval_ood.py --ckpt c", "aws s3 sync s3://b/ .", "huggingface-cli download meta/llama",
            "python -m src.train cfg=a", "CUDA_VISIBLE_DEVICES=0 python -u train.py",
            "uv run python scripts/train_cat.py", "ls && (sudo rsync -a a b)",
            "cd /p/zip-aware-training && .venv/bin/python code/train_mae.py", "cat > f <<'E'\nx\nE\nrsync -a a b",
        ]:
            out = context(run(LONG, self.bash(command))[0])
            self.assertIsNotNone(out, command)
            self.assertIn("progress-updates", out)

    def test_any_backgrounded_command_reminds(self) -> None:
        self.assertIsNotNone(context(run(LONG, self.bash("make all", background=True))[0]))

    def test_argv_form_yields_the_script(self) -> None:
        self.assertIsNotNone(context(run(LONG, self.bash(["bash", "-lc", "rsync a b"]))[0]))
        self.assertEqual(run(LONG, self.bash(["bash", "-lc", "ls"]))[0].stdout, "")

    def test_already_tracked_commands_are_silent(self) -> None:
        for command in [
            "python3 /x/progress-updates/scripts/progress.py table --board b",
            "python3 /x/sweep-supervisor/scripts/supervisor.py table --wave 1",
            "rsync -a rig:repo/.waves/ .waves/",
        ]:
            self.assertEqual(run(LONG, self.bash(command, background=True))[0].stdout, "", command)

    def test_other_tools_are_silent(self) -> None:
        proc, _ = run(LONG, {"tool_name": "Write", "tool_input": {"command": "rsync -a a b"}})
        self.assertEqual(proc.stdout, "")

    def test_long_command_is_truncated(self) -> None:
        self.assertLess(len(context(run(LONG, self.bash("rsync " + "x" * 500))[0])), 400)

    def test_garbage_never_fails(self) -> None:
        for raw in ["", "nope", "[]", '{"tool_name": "Bash", "tool_input": 3}']:
            proc, _ = run(LONG, raw=raw)
            self.assertEqual((proc.returncode, proc.stdout), (0, ""), raw)


class HooksJsonTests(unittest.TestCase):
    def test_each_plugin_hook_runs_its_reminder(self) -> None:
        expected = {
            "core": {"PostToolUse": LONG},
            "research": {"SessionStart": WAVE, "UserPromptSubmit": WAVE, "PostToolUse": WAVE},
        }
        for plugin, events in expected.items():
            plugin_root = ROOT / "plugins" / plugin
            spec = json.loads((plugin_root / "hooks/hooks.json").read_text())
            self.assertEqual(set(spec["hooks"]), set(events), plugin)
            for event, script in events.items():
                commands = [h["command"] for g in spec["hooks"][event] for h in g["hooks"]]
                targets = [plugin_root / rel for c in commands for rel in HOOK_SCRIPT_RE.findall(c)]
                self.assertEqual(targets, [script], f"{plugin} {event}")
        self.assertEqual(json.loads((ROOT / "plugins/core/hooks/hooks.json").read_text())
                         ["hooks"]["PostToolUse"][0]["matcher"], "Bash")


if __name__ == "__main__":
    unittest.main()
