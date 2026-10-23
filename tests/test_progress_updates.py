"""Behavior of core's progress-updates probe: parsing, ETA basis, states, the table, and watch."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).parents[1]
SKILL = ROOT / "plugins/core/skills/progress-updates"
PROGRESS = SKILL / "scripts/progress.py"


def load_progress():
    spec = importlib.util.spec_from_file_location("progress", PROGRESS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ProgressUpdatesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_progress()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        env = mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(self.dir / "state")})
        env.start()
        self.addCleanup(env.stop)
        self.board = self.cli("start").strip()

    def cli(self, *args: str) -> str:
        out = StringIO()
        with redirect_stdout(out):
            self.module.main(list(args))
        return out.getvalue()

    def write(self, name: str, text: str) -> str:
        path = self.dir / name
        path.write_text(text)
        return str(path)

    def rows(self) -> list[list[str]]:
        lines = self.cli("table", "--board", self.board).splitlines()
        self.assertTrue(lines[0].startswith("⏱ "))
        self.assertEqual(lines[2], "| task | progress | ETA (basis) | state |")
        return [[c.strip() for c in line.strip("|").split("|")] for line in lines[4:]]

    def test_rsync_progress2_uses_rsync_eta_until_two_samples(self) -> None:
        log = self.write("rs.log", "  1,000  10%  1MB/s  0:01:00\r  6,100  61%  1MB/s  0:05:00 (xfr#4)\r")
        self.cli("add", "--board", self.board, "--name", "copy", "--kind", "rsync", "--log", log)
        [row] = self.rows()
        self.assertEqual(row[0], "copy")
        self.assertTrue(row[1].startswith("61% · "))
        self.assertTrue(row[2].endswith("(rsync)"))
        self.assertEqual(row[3], "ok")

    def test_tqdm_and_keyword_lines_are_parsed(self) -> None:
        tqdm = self.module.parse_log("log", "Epoch 3:  35%|###| 7/20 [00:41<01:10, 4.1it/s]", None)
        self.assertEqual((tqdm["done"], tqdm["total"], tqdm["tool_remaining"]), (7, 20, 70.0))
        step = self.module.parse_log("log", "step 3/10 loss=1.2\n", None)
        self.assertEqual((step["done"], step["total"], step["unit"]), (3, 10, "step"))
        custom = self.module.parse_log("log", "wrote 4 of 8", r"(?P<done>\d+) of (?P<total>\d+)")
        self.assertEqual((custom["done"], custom["total"]), (4.0, 8.0))

    def test_a_metric_percentage_is_never_read_as_progress(self) -> None:
        parse = self.module.parse_log
        step = parse("log", "step 3/10\nval acc 100.0%\n", None)
        self.assertEqual((step["done"], step["total"]), (3, 10))
        self.assertIsNone(parse("log", "epoch 1 loss 0.3 acc 93.1%\ngpu mem 45%\n", None))
        self.assertEqual(parse("log", "Progress: 45%\nacc 99%\n", None)["done"], 45.0)
        self.assertEqual(parse("log", "  12%  \n", None)["done"], 12.0)
        self.assertEqual(parse("log", "12% done\n", None)["done"], 12.0)

    def test_a_finished_metric_line_does_not_mark_a_task_done(self) -> None:
        log = self.write("train.log", "loading\ntrain acc 100.0%\n")
        self.cli("add", "--board", self.board, "--name", "train", "--kind", "log", "--log", log)
        [row] = self.rows()
        self.assertEqual(row[3], "ok")

    def test_linear_eta_after_two_samples(self) -> None:
        task = {"samples": [[0.0, 0.2], [600.0, 0.4]]}
        cell = self.module.eta_cell(task, {"done": 4, "total": 10}, 600.0, 1200.0)
        self.assertTrue(cell.endswith("(linear, 10m)"))
        flat = {"samples": [[0.0, 0.4], [600.0, 0.4]]}
        self.assertEqual(
            self.module.eta_cell(flat, {"done": 4, "total": 10}, 600.0, 1200.0),
            "unavailable: no progress in 10m",
        )

    def test_exit_file_decides_terminal_state_and_finishes_the_board(self) -> None:
        log = self.write("run.log", "step 3/10\nTraceback (most recent call last)\n")
        exit_file = self.write("run.exit", "1\n")
        self.cli("add", "--board", self.board, "--name", "run", "--kind", "log",
                 "--log", log, "--exit-file", exit_file)
        [row] = self.rows()
        self.assertEqual(row[3], "failed (exit 1)")
        self.assertIn("all tasks finished", self.cli("table", "--board", self.board).splitlines()[0])

    def test_size_kind_against_a_known_total(self) -> None:
        dest = self.dir / "dest"
        dest.mkdir()
        (dest / "f").write_bytes(b"\0" * 1024 * 1024)
        self.cli("add", "--board", self.board, "--name", "scp", "--kind", "size",
                 "--path", str(dest), "--total", "4M")
        [row] = self.rows()
        self.assertTrue(row[1].startswith("25% · "))
        self.assertEqual(row[2], "pending: needs a second sample")

    def test_every_puts_the_next_update_in_the_header(self) -> None:
        board = self.cli("start", "--every", "5").strip()
        self.assertIn("· next ", self.cli("table", "--board", board).splitlines()[0])
        self.assertNotIn("next", self.cli("table", "--board", self.board).splitlines()[0])

    # --- watch -----------------------------------------------------------------------------

    def watch(self, *extra: str) -> tuple[str, list[list[str]], float]:
        started = time.monotonic()
        out = self.cli("watch", "--board", self.board, "--poll", "0.05", *extra)
        lines = out.splitlines()
        self.assertTrue(lines[0].startswith("watch: "), out)
        rows = [[c.strip() for c in line.strip("|").split("|")] for line in lines[6:]]
        return lines[0][len("watch: "):], rows, time.monotonic() - started

    def add_log(self, name: str, text: str, exit_code: str | None = None) -> tuple[str, str]:
        log = self.write(f"{name}.log", text)
        exit_file = str(self.dir / f"{name}.exit")
        if exit_code is not None:
            Path(exit_file).write_text(exit_code)
        self.cli("add", "--board", self.board, "--name", name, "--kind", "log",
                 "--log", log, "--exit-file", exit_file)
        return log, exit_file

    def later(self, seconds: float, action) -> None:
        timer = threading.Timer(seconds, action)
        timer.start()
        self.addCleanup(timer.cancel)

    def test_watch_returns_when_a_task_finishes(self) -> None:
        _, exit_file = self.add_log("run", "step 3/10\n")
        self.later(0.3, lambda: Path(exit_file).write_text("0\n"))
        reason, [row], elapsed = self.watch("--timeout", "10")
        self.assertEqual(reason, "run done; all tasks finished")
        self.assertEqual(row[3], "done")
        self.assertLess(elapsed, 5)

    def test_watch_reports_a_failure_while_other_tasks_keep_running(self) -> None:
        self.add_log("copy", "step 1/10\n")
        _, exit_file = self.add_log("run", "step 3/10\n")
        self.later(0.2, lambda: Path(exit_file).write_text("2\n"))
        reason, rows, _ = self.watch("--timeout", "10")
        self.assertEqual(reason, "run failed (exit 2)")
        self.assertEqual([r[0] for r in rows], ["copy", "run"])
        self.assertEqual(rows[0][3], "ok")
        # The failure was reported once; the next watch waits for something new.
        reason, _, _ = self.watch("--timeout", "0.3")
        self.assertTrue(reason.startswith("timeout after"), reason)

    def test_watch_reports_a_new_error_in_the_log_once(self) -> None:
        log, _ = self.add_log("run", "step 3/10\n")
        self.later(0.2, lambda: Path(log).write_text("step 4/10\nTraceback (most recent call last)\n"))
        reason, [row], _ = self.watch("--timeout", "10")
        self.assertEqual(reason, "run error in log")
        reason, _, _ = self.watch("--timeout", "0.3")
        self.assertTrue(reason.startswith("timeout"), reason)

    def test_watch_reports_a_stall(self) -> None:
        self.add_log("run", "step 3/10\n")
        reason, [row], _ = self.watch("--timeout", "10", "--stall", "0.005")
        self.assertRegex(reason, r"^run stalled \d+m$")
        self.assertTrue(row[3].startswith("stalled"))
        # Still stalled: not reported again. Progress clears it, so a later stall is new.
        self.assertTrue(self.watch("--timeout", "0.3", "--stall", "0.005")[0].startswith("timeout"))

    def test_watch_times_out_quietly_when_nothing_changes(self) -> None:
        self.add_log("run", "step 3/10\n")
        reason, [row], elapsed = self.watch("--timeout", "0.4")
        self.assertEqual(reason, "timeout after 0.4s with nothing new; run watch again")
        self.assertEqual(row[3], "ok")
        self.assertGreaterEqual(elapsed, 0.35)

    def test_watch_survives_an_unreadable_log(self) -> None:
        self.cli("add", "--board", self.board, "--name", "gone", "--kind", "log",
                 "--log", str(self.dir / "missing.log"))
        # A brief blip stays quiet ...
        reason, [row], _ = self.watch("--timeout", "0.3")
        self.assertTrue(reason.startswith("timeout"), reason)
        self.assertEqual(row[3], "log unreadable")
        # ... one that lasts past the stall threshold is reported.
        reason, _, _ = self.watch("--timeout", "10", "--stall", "0.002")
        self.assertEqual(reason, "gone log unreadable")

    def test_watch_survives_a_broken_probe_and_ssh(self) -> None:
        self.cli("add", "--board", self.board, "--name", "far", "--kind", "log",
                 "--log", "/x.log", "--host", "no-such-host.invalid")
        with mock.patch.object(self.module.subprocess, "run", side_effect=OSError("no ssh")):
            reason, [row], _ = self.watch("--timeout", "0.2")
        self.assertTrue(reason.startswith("timeout"), reason)
        self.assertEqual(row[3], "log unreadable")
        with mock.patch.object(self.module, "sample", side_effect=RuntimeError("boom")):
            reason, [row], _ = self.watch("--timeout", "0.2")
        self.assertEqual(row[3], "probe unreadable")

    def test_watch_returns_at_once_for_finished_empty_or_removed_boards(self) -> None:
        self.assertEqual(self.watch()[0], "no tasks on board")
        self.add_log("run", "step 10/10\n", exit_code="0")
        self.assertEqual(self.watch()[0], "run done; all tasks finished")
        self.assertEqual(self.watch()[0], "all tasks finished")
        self.add_log("next", "step 1/10\n")
        self.later(0.2, lambda: self.module.board_path(self.board).unlink())
        out = self.cli("watch", "--board", self.board, "--poll", "0.05", "--timeout", "10")
        self.assertEqual(out.strip(), "watch: board removed")

    def test_watch_picks_up_tasks_added_while_it_runs(self) -> None:
        self.add_log("first", "step 1/10\n")
        log = self.write("second.log", "step 1/1\n")
        exit_file = self.write("second.exit", "0\n")
        # A separate process, as in real use (and stdout redirection is not thread-safe).
        add = [sys.executable, str(PROGRESS), "add", "--board", self.board, "--name", "second",
               "--kind", "log", "--log", log, "--exit-file", exit_file]
        self.later(0.2, lambda: subprocess.run(add, capture_output=True, check=True))
        reason, rows, _ = self.watch("--timeout", "10")
        self.assertEqual(reason, "second done")
        self.assertEqual([r[0] for r in rows], ["first", "second"])

    def test_a_slow_sample_never_clobbers_a_concurrent_add_or_remove(self) -> None:
        self.add_log("keep", "step 1/10\n")
        self.add_log("drop", "step 1/10\n")
        stale = self.module.load(self.board)
        self.module.refresh(stale, time.time())
        self.cli("remove", "--board", self.board, "--name", "drop")
        self.cli("remove", "--board", self.board, "--name", "keep")
        self.add_log("keep", "step 5/10\n")  # same name, new registration
        self.add_log("new", "step 1/10\n")
        self.assertTrue(self.module.commit(self.board, stale))
        tasks = self.module.load(self.board)["tasks"]
        self.assertEqual(set(tasks), {"keep", "new"})
        self.assertEqual(tasks["keep"]["samples"], [])
        self.cli("stop", "--board", self.board)
        self.assertFalse(self.module.commit(self.board, stale))
        self.assertFalse(self.module.board_path(self.board).exists())

    def test_skill_is_event_driven_and_defers_waves(self) -> None:
        skill = " ".join((SKILL / "SKILL.md").read_text().split())
        self.assertIn("watch --board <id>", skill)
        self.assertIn("the host's background-command capability", skill)
        self.assertIn("the host's scheduled wake-up primitive", skill)
        self.assertIn("There is no default cadence", skill)
        self.assertIn("Never use shell `sleep`", skill)
        self.assertIn("belongs to `sweep-supervisor`", skill)
        self.assertNotIn("even when nothing changed", skill)
        self.assertNotIn("hook", skill.lower())
        self.assertNotIn("$", skill.split("---", 2)[2].replace("$PROGRESS", "").replace("$?", ""))
        yaml = (SKILL / "agents/openai.yaml").read_text()
        self.assertIn("$progress-updates", yaml)
        self.assertNotIn("every minute", yaml)

if __name__ == "__main__":
    unittest.main()
