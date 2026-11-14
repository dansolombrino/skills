"""token-usage: each agent's log is counted once per call, incrementally, and priced from omp's records."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "plugins/core/skills/token-usage/scripts/tokens.py"


def load():
    spec = importlib.util.spec_from_file_location("tokens", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


t = load()
NOW = 1_800_000_000  # a fixed clock for the series


def iso(ts: int) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z")


LINES = iter(range(10**9))


def claude_line(msg_id: str, ts: int, output: int, model: str = "claude-x", uuid: str | None = None) -> dict:
    return {"type": "assistant", "timestamp": iso(ts), "cwd": "/work/proj-a", "requestId": "r-" + msg_id,
            "uuid": uuid or f"u{next(LINES)}",
            "message": {"id": msg_id, "model": model, "content": [{"type": "text", "text": "secret answer"}],
                        "usage": {"input_tokens": 10, "output_tokens": output, "cache_read_input_tokens": 1000,
                                  "cache_creation_input_tokens": 100}}}


def omp_lines(ts: int) -> list[dict]:
    return [
        {"type": "session", "id": "s1", "timestamp": iso(ts), "cwd": "/work/proj-b"},
        {"type": "message", "id": "aa", "timestamp": iso(ts), "message": {
            "role": "assistant", "model": "claude-x", "content": [],
            "usage": {"input": 100, "output": 50, "cacheRead": 1000, "cacheWrite": 10,
                      "cost": {"input": 0.0003, "output": 0.00075, "cacheRead": 0.0003, "cacheWrite": 0.0000375}}}},
        {"type": "message", "id": "ab", "timestamp": iso(ts), "message": {"role": "toolResult", "content": "usage"}},
    ]


def codex_lines(ts: int) -> list[dict]:
    def count(total, last):
        return {"timestamp": iso(ts), "type": "event_msg", "payload": {"type": "token_count", "info": {
            "total_token_usage": total, "last_token_usage": last}}}
    first = {"input_tokens": 1000, "cached_input_tokens": 800, "output_tokens": 20}
    second = {"input_tokens": 2500, "cached_input_tokens": 2000, "output_tokens": 50}
    return [
        {"timestamp": iso(ts), "type": "session_meta", "payload": {"cwd": "/work/proj-c"}},
        {"timestamp": iso(ts), "type": "turn_context", "payload": {"model": "gpt-y"}},
        count(first, first),
        count(first, first),  # repeated totals: a rate-limit refresh, not a call
        count(second, {"input_tokens": 1500, "cached_input_tokens": 1200, "output_tokens": 30}),
    ]


def write(path: Path, entries: list[dict], tail: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(e) + "\n" for e in entries) + tail)


class TokenUsageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.dirs = {tool: base / tool for tool in t.TOOLS}
        self.base = base
        self.config = t.Config(40978, "127.0.0.1", 60, base / "usage.sqlite", "Token usage",
                               {tool: (path,) for tool, path in self.dirs.items()}, machine="here")
        self.db = t.connect(self.config.db)

    def tearDown(self) -> None:
        self.db.close()
        self.tmp.cleanup()

    def rows(self) -> dict:
        return {(tool, model): (n, i, o, cr, cw) for tool, model, n, i, o, cr, cw in self.db.execute(
            "SELECT tool, model, COUNT(*), SUM(input), SUM(output), SUM(cache_read), SUM(cache_write) FROM calls GROUP BY tool, model")}

    def test_counted_like_the_status_line_and_only_counts_are_kept(self) -> None:
        ts = NOW - 3600
        # Claude Code like /stats: each line of a reply split over two lines, and a resumed session's copy.
        write(self.dirs["claude"] / "p/one.jsonl", [claude_line("m1", ts, 5), claude_line("m1", ts, 40)])
        write(self.dirs["claude"] / "p/resumed.jsonl", [claude_line("m1", ts, 40), claude_line("m2", ts, 7)])
        write(self.dirs["omp"] / "p/s.jsonl", omp_lines(ts))
        write(self.dirs["omp"] / "p/fork.jsonl", omp_lines(ts))
        write(self.dirs["codex"] / "2026/rollout-x.jsonl", codex_lines(ts))
        t.scan(self.db, self.config)
        self.assertEqual(self.rows(), {
            ("claude", "claude-x"): (4, 40, 92, 4000, 400),
            ("omp", "claude-x"): (1, 100, 50, 1000, 10),
            ("codex", "gpt-y"): (2, 500, 50, 2000, 0),  # input excludes the cached part
        })
        stored = " ".join(str(v) for row in self.db.execute("SELECT * FROM calls") for v in row)
        self.assertNotIn("secret", stored)

    def test_scan_reads_only_complete_new_lines(self) -> None:
        path = self.dirs["claude"] / "p/live.jsonl"
        write(path, [claude_line("m1", NOW - 60, 5)], tail='{"type": "assistant", "trunc')
        t.scan(self.db, self.config)
        self.assertEqual(self.rows()[("claude", "claude-x")][0], 1)
        with path.open("r+") as handle:  # the writer finishes the line and adds one more
            text = handle.read()
            handle.seek(0)
            handle.write(text[: text.rindex("\n") + 1] + json.dumps(claude_line("m2", NOW - 30, 5)) + "\n")
        t.scan(self.db, self.config)
        self.assertEqual(self.rows()[("claude", "claude-x")][0], 2)
        offset = self.db.execute("SELECT offset FROM files").fetchone()[0]
        self.assertEqual(offset, path.stat().st_size)

    def test_omp_archive_counts_once_and_imports_are_left_to_claude(self) -> None:
        import gzip
        ts = NOW - 3600
        lines = omp_lines(ts)
        imported = json.loads(json.dumps(lines[1]))
        imported["id"] = "claude-0001"
        write(self.dirs["omp"] / "live/s.jsonl", lines + [imported])
        archived = self.dirs["omp"] / "archive/s.jsonl.gz"  # omp gc moved an older copy of the session here
        archived.parent.mkdir(parents=True)
        archived.write_bytes(gzip.compress("".join(json.dumps(e) + "\n" for e in lines).encode()))
        t.scan(self.db, self.config)
        self.assertEqual(self.rows(), {("omp", "claude-x"): (1, 100, 50, 1000, 10)})

    def test_codex_homes_matched_by_wildcard_count_a_shared_rollout_once(self) -> None:
        ts = NOW - 3600
        accounts = self.base / "orca/codex-accounts"
        for account in ("a1", "a2"):  # two account homes holding a copy of the same rollout
            write(accounts / account / "home/sessions/2026/rollout-x.jsonl", codex_lines(ts))
        config = t.Config(40978, "127.0.0.1", 60, self.config.db, "Token usage",
                          {"codex": (accounts / "*" / "home/sessions",)}, machine="here")
        t.scan(self.db, config)
        self.assertEqual(self.rows(), {("codex", "gpt-y"): (2, 500, 50, 2000, 0)})

    def test_cost_uses_omp_prices_for_every_agent(self) -> None:
        ts = NOW - 3600
        write(self.dirs["omp"] / "p/s.jsonl", omp_lines(ts))
        write(self.dirs["claude"] / "p/c.jsonl", [claude_line("m1", ts, 40)])
        write(self.dirs["codex"] / "r/rollout-x.jsonl", codex_lines(ts))
        t.scan(self.db, self.config)
        data = t.series(self.db, self.config, "1d", "tool", "cost", tools=t.TOOLS, now=NOW)
        totals = {s["name"]: s["total"] for s in data["series"]}
        # omp's own call costs what it recorded; Claude Code's is priced per token from it.
        self.assertAlmostEqual(totals["omp"], 0.0003 + 0.00075 + 0.0003 + 0.0000375, places=6)
        self.assertAlmostEqual(totals["claude"], 10 * 3e-6 + 40 * 15e-6 + 1000 * 3e-7 + 100 * 3.75e-6, places=6)
        self.assertEqual(totals["codex"], 0)  # no omp price for gpt-y: left out, and reported
        self.assertEqual(data["uncosted_tokens"], 2550)

    def test_series_totals_previous_period_and_lead_in(self) -> None:
        write(self.dirs["claude"] / "p/c.jsonl", [
            claude_line("old", NOW - 2 * 86400 + 600, 5),    # the period before 1d
            claude_line("new", NOW - 3600, 5),
        ])
        t.scan(self.db, self.config)
        data = t.series(self.db, self.config, "1d", "tool", "tokens", kinds=("output",), now=NOW)
        self.assertEqual(data["total"], 5)
        self.assertEqual(data["previous_total"], 5)
        (claude,) = data["series"]
        self.assertEqual(sum(claude["values"]), 5)
        self.assertEqual(len(claude["values"]), data["points"])
        self.assertEqual(len(claude["lead"]), data["bucket"] // data["sub"])
        with self.assertRaises(t.TokensError):
            t.series(self.db, self.config, "1d", "tool", "tokens", kinds=("everything",), now=NOW)

    def test_intervals_keep_the_totals_and_follow_the_local_clock(self) -> None:
        import os
        import time
        old = os.environ.get("TZ")
        os.environ["TZ"] = "Europe/Rome"
        time.tzset()
        self.addCleanup(lambda: (os.environ.pop("TZ") if old is None else os.environ.__setitem__("TZ", old), time.tzset()))
        now = int(t.datetime(2026, 10, 28, 12, 0).timestamp())  # a Wednesday; summer time ended on Sunday 25
        write(self.dirs["claude"] / "p/c.jsonl", [claude_line(f"m{h}", now - h * 3600 - 17, 5) for h in range(0, 160, 7)])
        t.scan(self.db, self.config)
        runs = {i: t.series(self.db, self.config, "1w", kinds=("output",), now=now, interval=i)
                for i in ("15m", "1h", "4h", "1D", "1W")}
        self.assertEqual({d["total"] for d in runs.values()}, {5 * len(range(0, 160, 7))})  # switching never moves it
        day = runs["1D"]
        starts = [day["start"] + e * day["sub"] for e in day["edges"]]
        self.assertTrue(all(t.datetime.fromtimestamp(s).hour == 0 for s in starts[1:]))  # local midnights
        self.assertIn(25 * 3600, {b - a for a, b in zip(starts, starts[1:])})  # 25 October has 25 hours
        week = runs["1W"]
        mondays = [t.datetime.fromtimestamp(week["start"] + e * week["sub"]) for e in week["edges"][1:]]
        self.assertEqual([(m.weekday(), m.hour) for m in mondays], [(0, 0)])  # Monday 26 October, 00:00
        self.assertEqual(len(week["series"][0]["lead"]), week["bucket"] // week["sub"])
        for range_name, interval in (("1d", "1W"), ("1m", "5m")):  # longer than the range; over the candle cap
            with self.assertRaises(t.TokensError):
                t.series(self.db, self.config, range_name, now=now, interval=interval)
        # A zoomed view: any window, any interval that fits it; the span before it is the comparison.
        zoom = t.series(self.db, self.config, "1w", kinds=("output",), now=now, interval="1m",
                        view=(now - 30 * 3600, now - 6 * 3600))
        self.assertEqual((zoom["total"], zoom["previous_total"], zoom["live"]), (4 * 5, 3 * 5, False))
        self.assertLessEqual(len(zoom["edges"]), zoom["max_bars"])
        with self.assertRaises(t.TokensError):  # a week of minutes is more candles than the plot has room for
            t.series(self.db, self.config, "1w", now=now, interval="1m")

    def test_rollup_follows_every_write_to_calls(self) -> None:
        def raw() -> tuple:
            return self.db.execute("SELECT COUNT(*), SUM(input), SUM(output), SUM(cache_read), SUM(cache_write) FROM calls").fetchone()

        def rolled(step: int) -> tuple:
            return self.db.execute("SELECT SUM(n), SUM(input), SUM(output), SUM(cache_read), SUM(cache_write) "
                                   "FROM roll WHERE step = ?", (step,)).fetchone()
        row = ["here", "claude", "k1", NOW - 99, "m", "/w/a", 10, 20, 30, 40, 1]
        with self.db:
            self.db.execute(t.UPSERT, row)                                   # a new call
            self.db.execute(t.UPSERT, row[:6] + [15, 25, 30, 40, 2])         # it grew: counted once, larger
            self.db.execute(t.UPSERT, ["here", "omp", "k2", NOW - 5, "m", "/w/b", 1, 2, 3, 4, 3])
            self.db.execute("INSERT OR REPLACE INTO calls VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            ["here", "omp", "k2", NOW - 400, "m2", "/w/c", 5, 6, 7, 8, 4])  # replaced, moved
            self.db.execute("INSERT INTO calls VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            ["here", "codex", "k3", NOW, "m", "/w/a", 1, 1, 1, 1, 5])
            self.db.execute("DELETE FROM calls WHERE key = 'k3'")
        self.assertEqual(raw(), (2, 20, 31, 37, 48))
        for step in t.ROLL_STEPS:
            self.assertEqual(rolled(step), raw())
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM roll WHERE n = 0").fetchone()[0], 0)
        # An index made before the rollup existed gets it on the next connect, from its calls.
        with self.db:
            self.db.execute("DROP TABLE roll")
            self.db.execute("DROP TABLE dims")
            self.db.execute("DELETE FROM meta WHERE key = 'roll'")
            for name in ("calls_roll_insert", "calls_roll_update", "calls_roll_delete"):
                self.db.execute(f"DROP TRIGGER {name}")
        self.db.close()
        self.db = t.connect(self.config.db)
        for step in t.ROLL_STEPS:
            self.assertEqual(rolled(step), raw())

    def test_claude_history_makes_the_totals_match_stats(self) -> None:
        d2 = int(t.datetime(2026, 1, 11, 9, tzinfo=t.timezone.utc).timestamp())
        write(self.dirs["claude"] / "p/c.jsonl", [claude_line("m1", d2, 5),         # a day /stats has computed
                                                  claude_line("m2", d2 + 2 * 86400, 5)])  # after lastComputedDate
        (self.base / "stats-cache.json").write_text(json.dumps({
            "lastComputedDate": "2026-01-11", "firstSessionDate": "2026-01-01T08:00:00.000Z",
            "dailyModelTokens": [{"date": "2026-01-10", "tokensByModel": {"claude-x": 500}},   # transcripts deleted
                                 {"date": "2026-01-11", "tokensByModel": {"claude-x": 1415}}],  # 300 more than kept
            "modelUsage": {"claude-x": {"inputTokens": 110, "outputTokens": 205, "cacheReadInputTokens": 1500,
                                        "cacheCreationInputTokens": 200}},  # 100 older than the daily list
        }))
        t.scan(self.db, self.config)
        self.assertEqual(self.rows()[("claude", "claude-x")][1:], (110 + 10, 205 + 5, 1500 + 1000, 200 + 100))
        data = t.series(self.db, self.config, "all", "project", "tokens", now=NOW)
        self.assertEqual(data["total"], 2015 + 1115)
        self.assertEqual({s["name"]: s["total"] for s in data["series"]}, {"proj-a": 2230, t.HISTORY_CWD: 900})
        t.scan(self.db, self.config)  # recomputed, never added twice
        self.assertEqual(t.series(self.db, self.config, "all", now=NOW)["total"], 2015 + 1115)

    def test_fleet_export_resumes_from_its_cursor(self) -> None:
        import io
        ts = NOW - 3600
        write(self.dirs["claude"] / "p/c.jsonl", [claude_line("m1", ts, 5)])
        write(self.dirs["omp"] / "p/s.jsonl", omp_lines(ts))
        t.scan(self.db, self.config)
        out = io.StringIO()
        t.export(self.db, self.config, 0, out)
        hub = t.connect(self.base / "hub.sqlite")
        self.assertEqual(t.ingest(hub, "rig", out.getvalue().splitlines()), 2)
        self.assertEqual(hub.execute("SELECT machine, COUNT(*) FROM calls GROUP BY machine").fetchall(), [("rig", 2)])
        self.assertEqual(t.load_prices(hub)["claude-x"]["output"], 1.5e-5)  # prices travel with the calls
        cursor = t.get_meta(hub, "host:rig")["cursor"]
        later = io.StringIO()
        t.export(self.db, self.config, cursor, later)
        self.assertEqual(later.getvalue().splitlines()[-1], '{"done": true}')
        self.assertFalse([line for line in later.getvalue().splitlines() if line.startswith("[")])
        with self.assertRaises(t.TokensError):  # a cut-off answer is never stored
            t.ingest(hub, "rig", out.getvalue().splitlines()[:-1])
        hub.close()

    def test_backup_restores_calls_whose_logs_are_gone(self) -> None:
        ts = NOW - 3600
        log = self.dirs["claude"] / "p/c.jsonl"
        write(log, [claude_line("m1", ts, 5), claude_line("m2", ts + 40 * 86400, 7)])
        write(self.dirs["omp"] / "p/s.jsonl", omp_lines(ts))
        t.scan(self.db, self.config)
        before = self.rows()
        backup = self.base / "backup"
        self.assertEqual(t.dump(self.db, backup), 3)
        self.assertEqual(len(list((backup / "calls" / "here").glob("*.jsonl"))), 2)  # one file per month
        log.unlink()  # Claude Code deleted the transcript, then the index was lost
        fresh = t.connect(self.base / "fresh.sqlite")
        self.assertEqual(t.restore(fresh, backup), 3)
        t.restore(fresh, backup)  # restoring twice adds nothing
        self.db.close()
        self.db = fresh
        self.assertEqual(self.rows(), before)
        self.assertEqual(t.load_prices(fresh)["claude-x"]["output"], 1.5e-5)
        t.scan(fresh, self.config)  # a pass over the remaining logs keeps the restored calls
        self.assertEqual(self.rows(), before)

    def test_bind_must_stay_loopback(self) -> None:
        registry = Path(self.tmp.name) / "machines.toml"
        registry.write_text('[tokens]\nbind = "0.0.0.0"\n')
        with self.assertRaises(t.TokensError):
            t.load_config(registry)
        registry.write_text("")
        self.assertEqual(t.load_config(registry).port, 40978)


if __name__ == "__main__":
    unittest.main()
