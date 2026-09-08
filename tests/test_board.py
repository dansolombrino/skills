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


def probe_output(sessions: list[str], gpus: list[tuple[int, int, int]], boot: str) -> str:
    lines = list(sessions) + ["__GPUS__"]
    lines += [f"{i}, {mem}, {util}" for i, mem, util in gpus]
    lines += ["__BOOT__", boot]
    return "\n".join(lines) + "\n"


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

    def reconcile_with(self, outputs: dict[str, str | None]) -> tuple[int, str]:
        def fake(rig: board.Rig) -> str | None:
            return outputs.get(rig.name)

        out = io.StringIO()
        with mock.patch.object(board, "run_probe", side_effect=fake), redirect_stdout(out):
            code = board.main(["--registry", str(self.registry), "reconcile"])
        return code, out.getvalue()

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

    def test_viewer_serves_page_and_json(self) -> None:
        self.claim()
        handler = board.make_handler(self.config)

        class Fake(handler):  # type: ignore[misc,valid-type]
            def __init__(self, path: str) -> None:
                self.path = path
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
        self.assertIn(b"/api/board", page.wfile.getvalue())
        api = Fake("/api/board?x=1")
        api.do_GET()
        self.assertEqual(api.code, 200)
        self.assertEqual(json.loads(api.wfile.getvalue())["rigs"][0]["lanes"][0]["project"], "grokking")
        missing = Fake("/nope")
        missing.do_GET()
        self.assertEqual(missing.code, 404)


if __name__ == "__main__":
    unittest.main()
