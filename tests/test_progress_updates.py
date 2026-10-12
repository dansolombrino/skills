"""Behavior of core's progress-updates probe: parsing, ETA basis, states, and the table shape."""

from __future__ import annotations

import importlib.util
import os
import tempfile
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
        self.board = self.cli("start", "--every", "5").strip()

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
        self.assertIn("all tasks finished — stop the updates", self.cli("table", "--board", self.board))

    def test_size_kind_against_a_known_total(self) -> None:
        dest = self.dir / "dest"
        dest.mkdir()
        (dest / "f").write_bytes(b"\0" * 1024 * 1024)
        self.cli("add", "--board", self.board, "--name", "scp", "--kind", "size",
                 "--path", str(dest), "--total", "4M")
        [row] = self.rows()
        self.assertTrue(row[1].startswith("25% · "))
        self.assertEqual(row[2], "pending: needs a second sample")

    def test_skill_keeps_the_four_column_table_and_defers_waves(self) -> None:
        skill = " ".join((SKILL / "SKILL.md").read_text().split())
        self.assertIn("paste its output verbatim", skill)
        self.assertIn("Never implement the cadence with shell `sleep`", skill)
        self.assertIn("belongs to `sweep-supervisor`", skill)
        self.assertNotIn("$", skill.split("---", 2)[2].replace("$PROGRESS", "").replace("$?", ""))


if __name__ == "__main__":
    unittest.main()
