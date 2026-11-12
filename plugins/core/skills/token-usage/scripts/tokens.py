#!/usr/bin/env python3
"""token-usage: every model call the coding agents made across the fleet, charted like a stock.

The agents already log each model call with its token counts in their local session files. A
scanner indexes those calls into SQLite incrementally (only the bytes appended since the last pass
are read), other machines are pulled over ssh, and a read-only page on loopback, behind
service-hub, charts them: the running total as a price line or the burn rate as candles, with a
volume panel, split by agent, model, project or machine.

It counts the way Claude Code's /stats and omp's own stats do, so it agrees with a status line that sums them:

  claude  Claude Code, counted like its /stats: every assistant line of the transcripts under
          ~/.claude/projects (a reply logged over several lines counts on each), and the totals
          ~/.claude/stats-cache.json keeps for days whose transcripts Claude Code has deleted.
  omp     omp sessions, ~/.omp/agent/sessions and ~/.omp/agent/archive/sessions (gzipped by gc):
          assistant messages and side calls, once per (entry id, time).
  codex   Codex rollouts, ~/.codex/sessions and archived_sessions: the growth of the session's
          running totals. Off by default on the page, to match that sum.

Only timestamps, model names, working directories and token counts are stored: never prompts,
answers or tool output. Cost is an estimate: the per-token prices omp records for each model,
applied to every agent's calls of that model.

Configuration is the optional [tokens] table of the machines registry; see references/setup.md.

Subcommands:
  serve    scan and pull the fleet in the background, serve the page (the service runs this)
  scan     index this machine's new calls now
  pull     fetch every configured machine now
  report   print the totals for a range
  export   (run over ssh by `pull`) scan here, print the calls recorded since a cursor
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from urllib.parse import parse_qs, urlparse

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11, e.g. a fleet machine running `export`: no registry needed there
    tomllib = None

DEFAULT_REGISTRY = Path("~/.config/rigsync/machines.toml").expanduser()
DEFAULT_DB = "~/.local/share/token-usage/usage.sqlite"
DEFAULTS = {"port": 40978, "bind": "127.0.0.1", "every": 60, "pull_every": 300, "title": "Token usage"}
DEFAULT_SOURCES = {
    "claude": ["~/.claude/projects"],
    "omp": ["~/.omp/agent/sessions", "~/.omp/agent/archive/sessions"],
    "codex": ["~/.codex/sessions", "~/.codex/archived_sessions"],
}
TOOLS = tuple(DEFAULT_SOURCES)
STATUS_BAR_TOOLS = ("claude", "omp")  # what /stats plus omp's own count cover: the default
KINDS = ("input", "output", "cache_read", "cache_write")
GROUPS = ("tool", "model", "project", "machine")
METRICS = ("tokens", "cost")
MAX_SERIES = 8  # the rest fold into "other"
OTHER = "other"
HOUR, DAY = 3600, 86400
RANGES = {"1d": DAY, "1w": 7 * DAY, "1m": 30 * DAY, "3m": 91 * DAY, "ytd": None, "all": None}
SCRIPT = Path(globals().get("__file__") or "tokens.py").resolve()  # no __file__ when piped to `python3 -`
ASSETS = SCRIPT.parents[1] / "assets"
STATIC = {"/": ("index.html", "text/html; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/app.css": ("app.css", "text/css; charset=utf-8")}
SECURITY_HEADERS = {
    # Inline styles are allowed for service-hub's back-to-hub button (a <style> in its shadow root);
    # scripts stay 'self' only, and the page never parses data as HTML.
    "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; "
                               "img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Cross-Origin-Resource-Policy": "same-origin",
}
ID_DIR = re.compile(r"[0-9a-f][0-9a-f-]{5,}")  # throwaway run directories named by a hash
DATE_SUFFIX = re.compile(r"-\d{8}$")
COMPRESSED = {"codex": ("*.jsonl.zst",), "omp": ("*.jsonl.gz",)}  # archived logs: written once, read whole
HISTORY = "stats:"  # key prefix of the rows taken from Claude Code's stats-cache.json
HISTORY_CWD = "(Claude Code history)"  # their project: /stats keeps no directory
STATS_FIELDS = ("inputTokens", "outputTokens", "cacheReadInputTokens", "cacheCreationInputTokens")
SCHEMA_VERSION = "2"


class TokensError(Exception):
    pass


@dataclass(frozen=True)
class Config:
    port: int
    bind: str
    every: int
    db: Path
    title: str
    sources: dict[str, tuple[Path, ...]]
    machine: str = "local"
    hosts: tuple[str, ...] = ()
    pull_every: int = 300


@dataclass(frozen=True)
class Call:
    key: str
    ts: int
    model: str
    cwd: str
    input: int
    output: int
    cache_read: int
    cache_write: int


def is_loopback(address: str) -> bool:
    return address in ("127.0.0.1", "::1", "localhost")


def read_hosts(path: Path) -> list[str]:
    """One ssh alias per line; # starts a comment."""
    try:
        text = path.read_text()
    except OSError:
        return []
    return [line.split("#", 1)[0].strip() for line in text.splitlines() if line.split("#", 1)[0].strip()]


def load_config(path: Path) -> Config:
    registry: dict = {}
    if tomllib is not None:
        try:
            registry = tomllib.loads(path.read_text())
        except FileNotFoundError:
            pass
        except tomllib.TOMLDecodeError as exc:
            raise TokensError(f"invalid TOML in {path}: {exc}") from exc
    table = registry.get("tokens", {})
    if not isinstance(table, dict):
        raise TokensError(f"{path}: [tokens] must be a table")
    values = {**DEFAULTS, **table}
    for key in ("port", "every", "pull_every"):
        if isinstance(values[key], bool) or not isinstance(values[key], int):
            raise TokensError(f"{path}: tokens.{key} must be an integer")
    if not 1 <= values["port"] <= 65535 or values["every"] < 10 or values["pull_every"] < 60:
        raise TokensError(f"{path}: tokens needs port 1..65535, every >= 10 and pull_every >= 60")
    if not is_loopback(values["bind"]):
        raise TokensError(f"{path}: tokens.bind must be loopback; share the page through service-hub")
    for key in ("title", "machine"):
        if key in values and (not isinstance(values[key], str) or not values[key]):
            raise TokensError(f"{path}: tokens.{key} must be a non-empty string")
    sources = {}
    configured = table.get("sources", {})
    if not isinstance(configured, dict) or set(configured) - set(TOOLS):
        raise TokensError(f"{path}: tokens.sources may only set {', '.join(TOOLS)}")
    for tool, default in DEFAULT_SOURCES.items():
        dirs = configured.get(tool, default)
        if not isinstance(dirs, list) or not all(isinstance(d, str) for d in dirs):
            raise TokensError(f"{path}: tokens.sources.{tool} must be a list of directories")
        sources[tool] = tuple(Path(d).expanduser() for d in dirs)
    hosts = table.get("hosts", [])
    if not isinstance(hosts, list) or not all(isinstance(h, str) and h for h in hosts):
        raise TokensError(f"{path}: tokens.hosts must be a list of ssh aliases")
    if "hosts_file" in table:
        if not isinstance(table["hosts_file"], str):
            raise TokensError(f"{path}: tokens.hosts_file must be a path")
        hosts = hosts + read_hosts(Path(table["hosts_file"]).expanduser())
    machine = values.get("machine") or socket.gethostname().split(".")[0] or "local"
    return Config(values["port"], values["bind"], values["every"], Path(str(values.get("db", DEFAULT_DB))).expanduser(),
                  values["title"], sources, machine, tuple(dict.fromkeys(h for h in hosts if h != machine)),
                  values["pull_every"])


# ---------------------------------------------------------------------------- parsing


def to_ts(text: object) -> int | None:
    if not isinstance(text, str):
        return None
    try:
        return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def count(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def parse_claude(entry: dict, file_key: str) -> Call | None:
    """A Claude Code assistant line, counted the way /stats counts: every line, even when one reply
    spans several lines that repeat its usage. The key is the line, so a re-read never counts it twice."""
    message = entry.get("message")
    if entry.get("type") != "assistant" or not isinstance(message, dict):
        return None
    usage, ts = message.get("usage"), to_ts(entry.get("timestamp"))
    line = entry.get("uuid") or message.get("id")
    if not isinstance(usage, dict) or ts is None or not line:
        return None
    call = Call(f"{file_key}:{line}", ts, str(message.get("model") or "unknown"), str(entry.get("cwd") or ""),
                count(usage.get("input_tokens")), count(usage.get("output_tokens")),
                count(usage.get("cache_read_input_tokens")), count(usage.get("cache_creation_input_tokens")))
    return call if call.input or call.output or call.cache_read or call.cache_write else None


OMP_KINDS = {"input": "input", "output": "output", "cache_read": "cacheRead", "cache_write": "cacheWrite"}


def parse_omp(entry: dict, state: dict) -> tuple[Call | None, dict[str, float]]:
    """An omp session line -> the call it records and the per-token prices its cost implies."""
    kind = entry.get("type")
    if kind == "session":
        state["cwd"] = str(entry.get("cwd") or "")
        return None, {}
    if kind == "message" and isinstance(entry.get("message"), dict) and entry["message"].get("role") == "assistant":
        source = entry["message"]
    elif kind == "model_usage":
        source = entry
    else:
        return None, {}
    usage, model, ts = source.get("usage"), source.get("model"), to_ts(entry.get("timestamp"))
    if not isinstance(usage, dict) or not isinstance(model, str) or ts is None or not entry.get("id"):
        return None, {}
    if str(entry["id"]).startswith("claude-"):  # a Claude Code session imported into omp: already counted there
        return None, {}
    tokens = {k: count(usage.get(field)) for k, field in OMP_KINDS.items()}
    if not any(tokens.values()):
        return None, {}
    cost = usage.get("cost") if isinstance(usage.get("cost"), dict) else {}
    prices = {}
    for k, field in OMP_KINDS.items():
        spent = cost.get(field)
        if tokens[k] and isinstance(spent, (int, float)) and not isinstance(spent, bool) and spent >= 0:
            prices[k] = spent / tokens[k]
    # A fork copies entries with their id and timestamp, so the pair names one call across files.
    return Call(f"{entry['id']}@{entry['timestamp']}", ts, model, state.get("cwd", ""), **tokens), prices


CODEX_FIELDS = ("input_tokens", "cached_input_tokens", "cache_write_input_tokens", "output_tokens")


def parse_codex(entry: dict, state: dict, key: str) -> Call | None:
    """A Codex rollout line. Token events carry running totals; a call is the growth since the last one."""
    payload = entry.get("payload")
    if not isinstance(payload, dict):
        return None
    kind = entry.get("type")
    if kind == "session_meta":
        state["cwd"] = str(payload.get("cwd") or "")
    elif kind == "turn_context":
        if isinstance(payload.get("model"), str):
            state["model"] = payload["model"]
    elif kind == "event_msg" and payload.get("type") == "token_count" and isinstance(payload.get("info"), dict):
        total, last = payload["info"].get("total_token_usage"), payload["info"].get("last_token_usage")
        ts = to_ts(entry.get("timestamp"))
        if not isinstance(total, dict) or ts is None:
            return None
        now = [count(total.get(f)) for f in CODEX_FIELDS]
        before = state.get("total") or [0] * len(CODEX_FIELDS)
        delta = [a - b for a, b in zip(now, before)]
        state["total"] = now
        if any(d < 0 for d in delta):  # totals restarted: trust the per-call figure
            delta = [count((last or {}).get(f)) for f in CODEX_FIELDS]
        if not any(delta):  # the same totals repeated (a rate-limit refresh)
            return None
        fresh, cached, written, output = delta
        return Call(key, ts, state.get("model", "unknown"), state.get("cwd", ""),
                    max(fresh - cached - written, 0), output, cached, written)
    return None


# ---------------------------------------------------------------------------- index

SCHEMA = """
CREATE TABLE IF NOT EXISTS calls(machine TEXT NOT NULL, tool TEXT NOT NULL, key TEXT NOT NULL, ts INTEGER NOT NULL,
  model TEXT NOT NULL, cwd TEXT NOT NULL, input INTEGER NOT NULL, output INTEGER NOT NULL,
  cache_read INTEGER NOT NULL, cache_write INTEGER NOT NULL, seen INTEGER NOT NULL,
  PRIMARY KEY(machine, tool, key)) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS calls_ts ON calls(ts);
CREATE INDEX IF NOT EXISTS calls_seen ON calls(machine, seen);
CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY, size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL,
  offset INTEGER NOT NULL, state TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS prices(model TEXT NOT NULL, kind TEXT NOT NULL, ts INTEGER NOT NULL, price REAL NOT NULL,
  PRIMARY KEY(model, kind));
"""
# `seen` is when (ms) a row last changed: the cursor a machine's export resumes after.
UPSERT = """INSERT INTO calls VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(machine, tool, key) DO UPDATE SET
  input = max(input, excluded.input), output = max(output, excluded.output),
  cache_read = max(cache_read, excluded.cache_read), cache_write = max(cache_write, excluded.cache_write),
  seen = excluded.seen
  WHERE excluded.input > input OR excluded.output > output OR excluded.cache_read > cache_read
     OR excluded.cache_write > cache_write"""
PRICE_UPSERT = """INSERT INTO prices VALUES (?, ?, ?, ?) ON CONFLICT(model, kind) DO UPDATE SET
  ts = excluded.ts, price = excluded.price WHERE excluded.ts >= prices.ts"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    version = db.execute("SELECT value FROM meta WHERE key = 'schema'").fetchone()
    if not version or version[0] != SCHEMA_VERSION:  # an older layout: rebuild it from the logs
        with db:
            for table in ("calls", "files", "prices"):
                db.execute(f"DROP TABLE IF EXISTS {table}")
            db.execute("DELETE FROM meta")
            db.execute("INSERT INTO meta VALUES ('schema', ?)", (SCHEMA_VERSION,))
    db.executescript(SCHEMA)
    return db


def get_meta(db: sqlite3.Connection, key: str, default=None):
    row = db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def set_meta(db: sqlite3.Connection, key: str, value) -> None:
    db.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, json.dumps(value)))


def source_files(config: Config):
    for tool, dirs in config.sources.items():
        for root in dirs:
            if root.is_dir():
                for pattern in COMPRESSED.get(tool, ()) + ("*.jsonl",):
                    for path in sorted(root.rglob(pattern)):
                        yield tool, root, path


def read_lines(path: Path, offset: int):
    """(byte offset, line) for each complete line from `offset`; a compressed file is read whole."""
    if path.suffix in (".zst", ".gz"):
        if path.suffix == ".gz":
            try:
                with gzip.open(path, "rb") as handle:
                    data = handle.read()
            except (OSError, EOFError):
                return
        else:
            if not shutil.which("zstd"):
                return
            done = subprocess.run(["zstd", "-dcq", str(path)], capture_output=True, timeout=300)
            if done.returncode:
                return
            data = done.stdout
        position = 0
        for line in data.splitlines(keepends=True):
            if line.endswith(b"\n"):
                yield position, line
            position += len(line)
        return
    with path.open("rb") as handle:
        handle.seek(offset)
        position = offset
        for line in handle:
            if not line.endswith(b"\n"):  # still being written
                return
            yield position, line
            position += len(line)


def scan_file(db: sqlite3.Connection, config: Config, tool: str, root: Path, path: Path, offset: int,
              state: dict, seen: int) -> tuple[int, int]:
    """Index one file from `offset`; returns (new offset, calls read). Writes nothing it cannot parse."""
    stem = path.name.split(".jsonl")[0]
    file_key = path.relative_to(root).as_posix()
    end, read, rows, prices = offset, 0, [], {}
    needle = b'"token_count"' if tool == "codex" else b'"usage"'
    for position, line in read_lines(path, offset):
        end = position + len(line)
        # Most lines are prompts and tool output; decode only those that can carry a call or context.
        if needle not in line and not (tool == "omp" and b'"type":"session"' in line) \
                and not (tool == "codex" and (b'"session_meta"' in line or b'"turn_context"' in line)):
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        if tool == "claude":
            call = parse_claude(entry, file_key)
        elif tool == "omp":
            call, found = parse_omp(entry, state)
            for kind, price in found.items():
                if call and prices.get((call.model, kind), (0, 0))[0] <= call.ts:
                    prices[(call.model, kind)] = (call.ts, price)
        else:
            call = parse_codex(entry, state, f"{stem}:{position}")
        if call:
            read += 1
            rows.append((config.machine, tool, call.key, call.ts, call.model, call.cwd, call.input, call.output,
                         call.cache_read, call.cache_write, seen))
    db.executemany(UPSERT, rows)
    db.executemany(PRICE_UPSERT, [(model, kind, ts, price) for (model, kind), (ts, price) in prices.items()])
    return end, read


def split(total: int, weights: list[int]) -> list[int]:
    """`total` split in proportion to `weights` (all of it to the first when they are all zero), summing exactly."""
    whole = sum(weights)
    if whole <= 0:
        return [total] + [0] * (len(weights) - 1)
    parts = [total * w // whole for w in weights]
    parts[max(range(len(weights)), key=lambda i: weights[i])] += total - sum(parts)
    return parts


def claude_history(db: sqlite3.Connection, config: Config, seen: int) -> None:
    """Fill in what Claude Code's /stats counted that the transcripts no longer hold.

    stats-cache.json keeps per-model totals per UTC day up to lastComputedDate (by kind only for all
    time). For each such day and model, whatever /stats counted beyond the transcript lines indexed for
    that day becomes one row at noon UTC; totals older than the daily list land on the first session
    date. The kinds are split so that, per model, they add up to the all-time kinds of /stats."""
    with db:
        db.execute("DELETE FROM calls WHERE machine = ? AND tool = 'claude' AND key LIKE ?", (config.machine, HISTORY + "%"))
        roots = config.sources.get("claude") or ()
        if not roots:
            return
        try:
            cache = json.loads((roots[0].parent / "stats-cache.json").read_text())
            usage, daily = cache["modelUsage"], cache["dailyModelTokens"]
            cutoff = int(datetime.fromisoformat(cache["lastComputedDate"] + "T00:00:00+00:00").timestamp()) + DAY
        except (OSError, ValueError, KeyError, TypeError):
            return
        held: dict[tuple[str, str], list[int]] = {}
        for day, model, *kinds in db.execute(
                "SELECT date(ts, 'unixepoch'), model, SUM(input), SUM(output), SUM(cache_read), SUM(cache_write) "
                "FROM calls WHERE machine = ? AND tool = 'claude' AND ts < ? GROUP BY 1, 2", (config.machine, cutoff)):
            held[(day, model)] = kinds
        gaps: list[tuple[int, str, int]] = []
        dated: Counter = Counter()
        for entry in daily if isinstance(daily, list) else []:
            day, by_model = entry.get("date"), entry.get("tokensByModel")
            if not isinstance(day, str) or not isinstance(by_model, dict):
                continue
            noon = to_ts(day + "T12:00:00Z")
            for model, total in by_model.items():
                dated[model] += count(total)
                gap = count(total) - sum(held.get((day, model), ()))
                if noon is not None and gap > 0:
                    gaps.append((noon, model, gap))
        first = to_ts(cache.get("firstSessionDate")) or min((ts for ts, _, _ in gaps), default=None)
        residual: dict[str, list[int]] = {}
        for model, kinds in usage.items():
            if not isinstance(kinds, dict):
                continue
            stats = [count(kinds.get(f)) for f in STATS_FIELDS]
            undated = sum(stats) - dated[model]
            if undated > 0 and first is not None:
                gaps.append((first, model, undated))
            have = [sum(v[i] for (_, m), v in held.items() if m == model) for i in range(4)]
            residual[model] = [max(s - h, 0) for s, h in zip(stats, have)]
        rows = []
        for model in {m for _, m, _ in gaps}:
            mine = [(ts, gap) for ts, m, gap in gaps if m == model]
            whole = sum(gap for _, gap in mine)
            target = split(whole, residual.get(model, [1, 0, 0, 0]))  # per kind, summing to the gaps exactly
            absorb = max(range(4), key=lambda i: target[i])  # takes each row's rounding, so rows stay exact too
            done, given = 0, [0, 0, 0, 0]
            for ts, gap in mine:
                done += gap
                parts = [0, 0, 0, 0]
                for i in range(4):
                    if i != absorb:
                        parts[i] = target[i] * done // whole - given[i]
                        given[i] += parts[i]
                parts[absorb] = gap - sum(parts)
                day = datetime.fromtimestamp(ts, timezone.utc).date().isoformat()
                rows.append((config.machine, "claude", f"{HISTORY}{day}:{model}", ts, model, HISTORY_CWD, *parts, seen))
        db.executemany("INSERT OR REPLACE INTO calls VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)


STATUS = {"scanning": False, "pulling": False, "error": None}


def scan(db: sqlite3.Connection, config: Config, log=None) -> dict:
    """Index what each source appended since the last pass. A file that shrank is read again from the start."""
    known = {row[0]: row[1:] for row in db.execute("SELECT path, size, mtime_ns, offset, state FROM files")}
    added, files, started = 0, 0, time.time()
    seen = int(started * 1000)  # ms: the export cursor is strict, and no two passes share a millisecond
    STATUS["scanning"] = True
    try:
        for tool, root, path in source_files(config):
            try:
                stat = path.stat()
            except OSError:
                continue
            files += 1
            previous = known.get(str(path))
            if previous and previous[0] == stat.st_size and previous[1] == stat.st_mtime_ns:
                continue
            offset, state = 0, {}
            whole = path.suffix in (".zst", ".gz")
            if previous and not whole and stat.st_size >= previous[2]:
                offset, state = previous[2], json.loads(previous[3])
            with db:
                end, read = scan_file(db, config, tool, root, path, offset, state, seen)
                db.execute("INSERT OR REPLACE INTO files VALUES (?, ?, ?, ?, ?)",
                           (str(path), stat.st_size, stat.st_mtime_ns, stat.st_size if whole else end, json.dumps(state)))
            added += read
            if log and read and not previous:
                log(f"[scan] {tool} {path.name}: {read} calls")
        claude_history(db, config, seen)
        with db:
            set_meta(db, "last_scan", int(time.time()))
    finally:
        STATUS["scanning"] = False
    return {"files": files, "calls_read": added, "seconds": round(time.time() - started, 1)}


# ---------------------------------------------------------------------------- fleet


def export(db: sqlite3.Connection, config: Config, since: int, out) -> None:
    """This machine's rows changed after `since`, all its history rows, the prices, then a done line."""
    for row in db.execute("SELECT tool, key, ts, model, cwd, input, output, cache_read, cache_write, seen FROM calls "
                          "WHERE machine = ? AND (seen > ? OR key LIKE ?)", (config.machine, since, HISTORY + "%")):
        out.write(json.dumps(row, separators=(",", ":")) + "\n")
    for row in db.execute("SELECT model, kind, ts, price FROM prices"):
        out.write(json.dumps({"price": row}) + "\n")
    out.write(json.dumps({"done": True}) + "\n")


def ingest(db: sqlite3.Connection, machine: str, lines) -> int:
    """Store one machine's export under `machine`; refuses an export that did not finish."""
    rows, prices, done = [], [], False
    for line in lines:
        if not line.strip():
            continue
        item = json.loads(line)
        if isinstance(item, list) and len(item) == 10:
            rows.append((machine, *item))
        elif isinstance(item, dict) and isinstance(item.get("price"), list) and len(item["price"]) == 4:
            prices.append(tuple(item["price"]))
        elif isinstance(item, dict) and item.get("done") is True:
            done = True
    if not done:
        raise TokensError("the export stopped before its end")
    state = get_meta(db, f"host:{machine}", {})
    with db:
        db.execute("DELETE FROM calls WHERE machine = ? AND key LIKE ?", (machine, HISTORY + "%"))
        db.executemany("INSERT OR REPLACE INTO calls VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
        db.executemany(PRICE_UPSERT, prices)
        cursor = max([state.get("cursor", 0)] + [r[10] for r in rows if not r[2].startswith(HISTORY)])
        set_meta(db, f"host:{machine}", {"cursor": cursor, "ok": int(time.time()), "error": None})
    return len(rows)


def pull_host(db: sqlite3.Connection, config: Config, host: str, timeout: int = 1800) -> int:
    """Run this script's `export` on `host` over ssh (it needs only python3 there) and store the answer.
    A host that fails keeps its rows, so the totals never drop while it is unreachable."""
    state = get_meta(db, f"host:{host}", {})
    cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=15", "-C", host,
           "python3", "-", "export", "--since", str(state.get("cursor", 0))]
    try:
        done = subprocess.run(cmd, input=SCRIPT.read_bytes(), capture_output=True, timeout=timeout)
        if done.returncode:
            raise TokensError((done.stderr.decode(errors="replace").strip().splitlines() or ["ssh failed"])[-1][:200])
        return ingest(db, host, done.stdout.decode().splitlines())
    except (TokensError, subprocess.TimeoutExpired, ValueError, OSError) as exc:
        with db:
            set_meta(db, f"host:{host}", {**state, "error": str(exc)[:200] or exc.__class__.__name__, "tried": int(time.time())})
        raise TokensError(f"{host}: {exc}") from exc


def pull(db: sqlite3.Connection, config: Config, log=None) -> None:
    STATUS["pulling"] = True
    try:
        for host in config.hosts:
            try:
                n = pull_host(db, config, host)
                if log:
                    log(f"[pull] {host}: {n} rows")
            except TokensError as exc:
                if log:
                    log(f"[pull] {exc}")
    finally:
        STATUS["pulling"] = False


def scanner(config: Config, stop: threading.Event, log=print) -> None:
    db = connect(config.db)
    while not stop.is_set():
        started = time.time()
        try:
            scan(db, config)
            STATUS["error"] = None
        except Exception as exc:  # never let one bad file kill the scanner
            STATUS["error"] = f"{exc.__class__.__name__}: {exc}"
            log(f"[scan] {STATUS['error']}")
        stop.wait(max(1.0, config.every - (time.time() - started)))


def puller(config: Config, stop: threading.Event, log=print) -> None:
    """The other machines, on their own thread: a first export can take minutes and must not stall the scans."""
    db = connect(config.db)
    while not stop.is_set():
        started = time.time()
        try:
            pull(db, config, log)
        except Exception as exc:
            log(f"[pull] unexpected {exc.__class__.__name__}: {exc}")
        stop.wait(max(1.0, config.pull_every - (time.time() - started)))


# ---------------------------------------------------------------------------- series


def project_label(cwd: str) -> str:
    if not cwd:
        return "(unknown)"
    path = PurePosixPath(cwd)
    if ID_DIR.fullmatch(path.name) and path.parent.name:
        return path.parent.name
    return path.name or cwd


def load_prices(db: sqlite3.Connection) -> dict[str, dict[str, float]]:
    prices: dict[str, dict[str, float]] = {}
    for model, kind, price in db.execute("SELECT model, kind, price FROM prices"):
        prices.setdefault(model, {})[kind] = price
    return prices


def price_for(prices: dict, model: str) -> dict[str, float]:
    return prices.get(model) or prices.get(DATE_SUFFIX.sub("", model)) or {}


def utc_offset(ts: float) -> int:
    return time.localtime(ts).tm_gmtoff


def floor_to(ts: int, step: int, offset: int) -> int:
    return (ts + offset) // step * step - offset


def steps_for(span: int) -> tuple[int, int]:
    """(candle bucket, sub-bucket) seconds for a span; candles stay between ~20 and ~120."""
    if span <= DAY:
        return HOUR, 300
    if span <= 7 * DAY:
        return 6 * HOUR, 1800
    if span <= 120 * DAY:
        return DAY, HOUR
    return 7 * DAY, 6 * HOUR


def window(db: sqlite3.Connection, name: str, now: int, where: str, args: tuple) -> tuple[int, int, int, int, int | None]:
    """(start, end, bucket, sub, span of the previous period or None) for a range name."""
    if name not in RANGES:
        raise TokensError(f"unknown range {name!r}; one of {', '.join(RANGES)}")
    offset = utc_offset(now)
    if name == "all":
        first = db.execute(f"SELECT MIN(ts) FROM calls WHERE {where}", args).fetchone()[0]
        span = max(now - (first if first is not None else now), DAY)
    elif name == "ytd":
        span = max(now - int(datetime(datetime.fromtimestamp(now).year, 1, 1).timestamp()), DAY)
    else:
        span = RANGES[name]
    bucket, sub = steps_for(span)
    start = floor_to(now - span, bucket if bucket <= DAY else DAY, offset)
    if name == "ytd":
        start = now - span
    return start, now, bucket, sub, (None if name == "all" else now - start)


def machines(db: sqlite3.Connection, config: Config) -> list[dict]:
    counts = dict(db.execute("SELECT machine, COUNT(*) FROM calls GROUP BY machine"))
    out = [{"name": config.machine, "local": True, "calls": counts.get(config.machine, 0),
            "ok": get_meta(db, "last_scan"), "error": STATUS["error"]}]
    for host in config.hosts:
        state = get_meta(db, f"host:{host}", {})
        out.append({"name": host, "local": False, "calls": counts.get(host, 0), "ok": state.get("ok"),
                    "error": state.get("error")})
    return out


def series(db: sqlite3.Connection, config: Config, range_name: str = "1w", group: str = "tool",
           metric: str = "tokens", kinds: tuple[str, ...] = KINDS, tools: tuple[str, ...] = STATUS_BAR_TOOLS,
           now: int | None = None) -> dict:
    if group not in GROUPS:
        raise TokensError(f"unknown group {group!r}; one of {', '.join(GROUPS)}")
    if metric not in METRICS:
        raise TokensError(f"unknown metric {metric!r}; one of {', '.join(METRICS)}")
    if not kinds or set(kinds) - set(KINDS):
        raise TokensError(f"kinds must be some of {', '.join(KINDS)}")
    if not tools or set(tools) - set(TOOLS):
        raise TokensError(f"tools must be some of {', '.join(TOOLS)}")
    now = int(time.time()) if now is None else now
    where = f"tool IN ({', '.join('?' * len(tools))})"
    start, end, bucket, sub, previous = window(db, range_name, now, where, tuple(tools))
    offset = utc_offset(now)
    prices = load_prices(db) if metric == "cost" else {}
    n = max(1, -(-(end - start) // sub))
    lead = bucket // sub  # one candle of history before the range, so the first candle has an open
    uncosted = 0

    def value(model: str, tokens: dict[str, int]) -> float:
        nonlocal uncosted
        if metric == "tokens":
            return float(sum(tokens[k] for k in kinds))
        unit, total = price_for(prices, model), 0.0
        for k in kinds:
            if tokens[k] and k not in unit:
                uncosted += tokens[k]
            total += tokens[k] * unit.get(k, 0.0)
        return total

    def label(machine: str, tool: str, model: str, cwd: str) -> str:
        return {"tool": tool, "model": model, "machine": machine}.get(group) or project_label(cwd)

    sums = "SUM(input), SUM(output), SUM(cache_read), SUM(cache_write)"
    values: dict[str, list[float]] = {}
    for b, machine, tool, model, cwd, *t in db.execute(
            f"SELECT (ts - ?) / ?, machine, tool, model, cwd, {sums} FROM calls WHERE ts >= ? AND ts < ? AND {where} "
            "GROUP BY 1, machine, tool, model, cwd", (start - lead * sub, sub, start - lead * sub, end + 1, *tools)):
        row = values.setdefault(label(machine, tool, model, cwd), [0.0] * (lead + n))
        kept = uncosted
        row[min(int(b), lead + n - 1)] += value(model, dict(zip(KINDS, t)))
        if int(b) < lead:  # the lead-in only gives the first candle an open; it is not this range's usage
            uncosted = kept
    before: dict[str, float] = {}
    if previous:
        kept = uncosted
        for machine, tool, model, cwd, *t in db.execute(
                f"SELECT machine, tool, model, cwd, {sums} FROM calls WHERE ts >= ? AND ts < ? AND {where} "
                "GROUP BY machine, tool, model, cwd", (start - previous, start, *tools)):
            name = label(machine, tool, model, cwd)
            before[name] = before.get(name, 0.0) + value(model, dict(zip(KINDS, t)))
        uncosted = kept  # report only what the shown range could not price
    ranked = sorted(values, key=lambda k: -sum(values[k][lead:]))
    if len(ranked) > MAX_SERIES:
        folded = [0.0] * (lead + n)
        for name in ranked[MAX_SERIES - 1:]:
            folded = [a + b for a, b in zip(folded, values.pop(name))]
            before[OTHER] = before.get(OTHER, 0.0) + before.pop(name, 0.0)
        values[OTHER] = folded
        ranked = ranked[:MAX_SERIES - 1] + [OTHER]
    first, last, calls = db.execute(f"SELECT MIN(ts), MAX(ts), COUNT(*) FROM calls WHERE {where}", tools).fetchone()
    digits = 6 if metric == "cost" else 0
    return {
        "title": config.title, "range": range_name, "group": group, "metric": metric, "kinds": list(kinds),
        "tools": list(tools), "start": start, "end": end, "bucket": bucket, "sub": sub, "points": n, "tz_offset": offset,
        "total": round(sum(sum(v[lead:]) for v in values.values()), digits),
        "previous_total": round(sum(before.values()), digits) if previous else None,
        "series": [{"name": name, "total": round(sum(values[name][lead:]), digits),
                    "previous": round(before.get(name, 0.0), digits) if previous else None,
                    "lead": [round(v, digits) for v in values[name][:lead]],
                    "values": [round(v, digits) for v in values[name][lead:]]} for name in ranked],
        "uncosted_tokens": uncosted,
        "index": {"calls": calls, "first": first, "last": last, "scanning": STATUS["scanning"],
                  "pulling": STATUS["pulling"], "machines": machines(db, config)},
    }


# ---------------------------------------------------------------------------- web


def make_handler(config: Config):
    class Handler(BaseHTTPRequestHandler):
        server_version = "token-usage"
        sys_version = ""

        def log_message(self, *args: object) -> None:
            pass

        def do_GET(self) -> None:  # noqa: N802
            url = urlparse(self.path)
            try:
                if url.path == "/healthz":
                    self._send(200, "text/plain; charset=utf-8", b"ok\n")
                elif url.path in STATIC:
                    name, ctype = STATIC[url.path]
                    self._send(200, ctype, (ASSETS / name).read_bytes(), cache="no-cache")
                elif url.path == "/api/series":
                    query = {k: v[0] for k, v in parse_qs(url.query).items()}
                    kinds = tuple(k for k in query.get("kinds", ",".join(KINDS)).split(",") if k)
                    tools = tuple(k for k in query.get("tools", ",".join(STATUS_BAR_TOOLS)).split(",") if k)
                    db = connect(config.db)
                    try:
                        data = series(db, config, query.get("range", "1w"), query.get("group", "tool"),
                                      query.get("metric", "tokens"), kinds, tools)
                    finally:
                        db.close()
                    self._send(200, "application/json; charset=utf-8", json.dumps(data).encode())
                else:
                    self._send(404, "text/plain; charset=utf-8", b"not found\n")
            except TokensError as exc:
                self._send(400, "text/plain; charset=utf-8", f"{exc}\n".encode())
            except (OSError, sqlite3.Error):
                self._send(503, "text/plain; charset=utf-8", b"usage is unavailable right now\n")

        do_HEAD = do_GET  # noqa: N815

        def _refuse(self) -> None:
            self._send(405, "text/plain; charset=utf-8", b"read-only\n")

        do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = _refuse  # noqa: N815

        def _send(self, code: int, ctype: str, body: bytes, cache: str = "no-store") -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            for key, value in SECURITY_HEADERS.items():
                self.send_header(key, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

    return Handler


# ---------------------------------------------------------------------------- commands


def human(x: float, metric: str) -> str:
    if metric == "cost":
        return f"${x:,.2f}"
    for unit, size in (("B", 1e9), ("M", 1e6), ("k", 1e3)):
        if x >= size:
            return f"{x / size:.2f}{unit}"
    return f"{x:.0f}"


def log_line(line: str) -> None:
    print(line, file=sys.stderr, flush=True)


def cmd_serve(config: Config, args: argparse.Namespace) -> int:
    connect(config.db).close()
    stop = threading.Event()
    if not args.no_scan:
        threading.Thread(target=scanner, args=(config, stop, log_line), daemon=True).start()
        if config.hosts:
            threading.Thread(target=puller, args=(config, stop, log_line), daemon=True).start()
    server = ThreadingHTTPServer((config.bind, config.port), make_handler(config))
    server.daemon_threads = True
    print(f"[serve] token usage of {config.machine} and {len(config.hosts)} more machines on "
          f"http://{config.bind}:{config.port}  every {config.every}s  db {config.db}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
    return 0


def cmd_scan(config: Config, args: argparse.Namespace) -> int:
    db = connect(config.db)
    result = scan(db, config, log=(lambda line: print(line, flush=True)) if args.verbose else None)
    print(f"scanned {result['files']} files in {result['seconds']} s: {result['calls_read']} calls read")
    return 0


def cmd_pull(config: Config, args: argparse.Namespace) -> int:
    if not config.hosts:
        print("no hosts configured (tokens.hosts or tokens.hosts_file)")
        return 0
    pull(connect(config.db), config, log=lambda line: print(line, flush=True))
    return 0


def cmd_export(config: Config, args: argparse.Namespace) -> int:
    db = connect(config.db)
    scan(db, config)
    export(db, config, args.since, sys.stdout)
    return 0


def cmd_report(config: Config, args: argparse.Namespace) -> int:
    db = connect(config.db)
    tools = tuple(t for t in args.tools.split(",") if t)
    data = series(db, config, args.range, args.group, args.metric, tools=tools)
    db.close()
    if args.json:
        print(json.dumps({k: v for k, v in data.items() if k != "series"} | {
            "series": [{k: v for k, v in s.items() if k not in ("values", "lead")} for s in data["series"]]}, indent=2))
        return 0
    change = ""
    if data["previous_total"]:
        change = f"  ({(data['total'] / data['previous_total'] - 1) * 100:+.1f}% vs the previous period)"
    print(f"{config.title} - {args.range}, {args.metric} of {args.tools} by {args.group}: "
          f"{human(data['total'], args.metric)}{change}")
    for s in data["series"]:
        share = s["total"] / data["total"] * 100 if data["total"] else 0
        print(f"  {s['name']:<32}{human(s['total'], args.metric):>12}{share:>8.1f}%")
    if args.metric == "cost" and data["uncosted_tokens"]:
        print(f"  ({human(data['uncosted_tokens'], 'tokens')} tokens of models with no known price are not counted)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--registry", help="machines registry (default: $RIGSYNC_REGISTRY or ~/.config/rigsync/machines.toml)")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="scan and pull in the background, serve the page")
    serve.add_argument("--no-scan", action="store_true", help="serve the indexed data without scanning or pulling")
    scan_parser = sub.add_parser("scan", help="index this machine's new calls now")
    scan_parser.add_argument("--verbose", action="store_true", help="print each newly indexed file")
    sub.add_parser("pull", help="fetch every configured machine now")
    export_parser = sub.add_parser("export", help="scan, then print the calls changed since a cursor (for pull)")
    export_parser.add_argument("--since", type=int, default=0)
    report = sub.add_parser("report", help="print the totals for a range")
    report.add_argument("--range", default="1w", choices=list(RANGES))
    report.add_argument("--group", default="tool", choices=GROUPS)
    report.add_argument("--metric", default="tokens", choices=METRICS)
    report.add_argument("--tools", default=",".join(STATUS_BAR_TOOLS), help="comma list of claude, omp, codex")
    report.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        path = Path(args.registry or os.environ.get("RIGSYNC_REGISTRY") or DEFAULT_REGISTRY).expanduser()
        config = load_config(path)
        commands = {"serve": cmd_serve, "scan": cmd_scan, "pull": cmd_pull, "export": cmd_export, "report": cmd_report}
        return commands[args.command](config, args)
    except TokensError as exc:
        print(f"tokens: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
