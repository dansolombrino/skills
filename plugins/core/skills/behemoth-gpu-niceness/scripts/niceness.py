#!/usr/bin/env python3
"""behemoth-gpu-niceness: who uses the shared GPUs, and how well.

A sampler probes one shared machine over ssh every `every` seconds (nvidia-smi plus the owner of
each GPU process) and keeps, per sample and GPU, only the utilization, the memory, and the memory
each *user* holds there. Process names, command lines, paths and PIDs are dropped at parse time:
they never reach the database, so no page or API can leak them. A read-only web page (behind
service-hub, on loopback only) ranks users by the GPU-hours they used, with each one's share of
all the machine's GPU time.

Configuration is the [niceness] table of the machines registry; see references/setup.md.

Subcommands:
  serve    sample in the background and serve the page (the service runs this)
  sample   take one sample now and print it (nothing is stored with --dry-run)
  report   print the ranking for a window
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
import tomllib
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

DEFAULT_REGISTRY = Path("~/.config/rigsync/machines.toml").expanduser()
DEFAULT_DB = "~/.local/share/behemoth-gpu-niceness/samples.sqlite"
DEFAULTS = {"machine": "behemoth", "port": 40977, "bind": "127.0.0.1", "every": 60, "retention_days": 400}
ASSETS = Path(__file__).resolve().parents[1] / "assets"
STATIC = {"/": ("index.html", "text/html; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/app.css": ("app.css", "text/css; charset=utf-8")}
# window -> (seconds or None for everything kept, timeline bucket seconds)
WINDOWS = {"24h": 86400, "7d": 7 * 86400, "30d": 30 * 86400, "all": None}  # None: everything kept
UNATTRIBUTED = "(unattributed)"   # GPU memory in use with no process visible to ps (another namespace)
UNATTRIBUTED_MIN_MIB = 1024
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
PROBE_SCRIPT = (
    "nvidia-smi --query-gpu=index,uuid,utilization.gpu,memory.used,memory.total --format=csv,noheader,nounits "
    "2>/dev/null || echo __NVSMI_FAILED__; echo __APPS__; "
    "nvidia-smi --query-compute-apps=gpu_uuid,pid,used_memory --format=csv,noheader,nounits 2>/dev/null; "
    "echo __OWNERS__; "
    "for p in $(nvidia-smi --query-compute-apps=pid --format=csv,noheader 2>/dev/null | sort -u); do "
    "ps -o pid=,user:32= -p \"$p\" 2>/dev/null; done"
)


class NicenessError(Exception):
    pass


@dataclass(frozen=True)
class Config:
    machine: str
    ssh: str
    port: int
    bind: str
    db: Path
    every: int
    retention_days: int
    title: str


@dataclass
class Gpu:
    index: int
    util: int
    mem_used: int
    mem_total: int
    holders: dict[str, int] = field(default_factory=dict)  # user -> MiB on this GPU


def is_loopback(address: str) -> bool:
    return address in ("127.0.0.1", "::1", "localhost")


def load_config(path: Path) -> Config:
    try:
        registry = tomllib.loads(path.read_text())
    except FileNotFoundError as exc:
        raise NicenessError(f"missing registry: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise NicenessError(f"invalid TOML in {path}: {exc}") from exc
    table = registry.get("niceness")
    if not isinstance(table, dict):
        raise NicenessError(f"{path}: missing [niceness] table")
    values = {**DEFAULTS, **table}
    machine = values["machine"]
    entry = registry.get("machines", {}).get(machine)
    if not isinstance(entry, dict) or not isinstance(entry.get("ssh"), str):
        raise NicenessError(f"{path}: niceness.machine {machine!r} needs a [machines.{machine}] table with ssh")
    for key in ("port", "every", "retention_days"):
        if isinstance(values[key], bool) or not isinstance(values[key], int):
            raise NicenessError(f"{path}: niceness.{key} must be an integer")
    if not 1 <= values["port"] <= 65535 or values["every"] < 10 or values["retention_days"] < 0:
        raise NicenessError(f"{path}: niceness needs port 1..65535, every >= 10, retention_days >= 0")
    if not is_loopback(values["bind"]):
        raise NicenessError(f"{path}: niceness.bind must be loopback; share the page through service-hub")
    title = values.get("title", f"{machine} GPU niceness")
    if not isinstance(title, str) or not title:
        raise NicenessError(f"{path}: niceness.title must be a non-empty string")
    return Config(machine, entry["ssh"], values["port"], values["bind"], Path(str(values.get("db", DEFAULT_DB))).expanduser(),
                  values["every"], values["retention_days"], title)


# ---------------------------------------------------------------------------- sampling


def to_int(text: str) -> int:
    try:
        return int(float(text.strip()))
    except ValueError:  # "[N/A]", "[Not Supported]"
        return 0


def parse_probe(output: str) -> list[Gpu]:
    """nvidia-smi + ps output -> GPUs with per-user memory. Nothing but user names survives."""
    if "__NVSMI_FAILED__" in output or "__APPS__" not in output:
        raise NicenessError("nvidia-smi failed on the machine")
    gpu_part, rest = output.split("__APPS__", 1)
    apps_part, _, owners_part = rest.partition("__OWNERS__")
    gpus: dict[str, Gpu] = {}
    for line in gpu_part.strip().splitlines():
        cols = [c.strip() for c in line.split(",")]
        if len(cols) != 5 or not cols[0].isdigit():
            continue
        gpus[cols[1]] = Gpu(int(cols[0]), to_int(cols[2]), to_int(cols[3]), to_int(cols[4]))
    if not gpus:
        raise NicenessError("nvidia-smi listed no GPUs")
    owners: dict[str, str] = {}
    for line in owners_part.strip().splitlines():
        cols = line.split()
        if len(cols) == 2 and cols[0].isdigit():
            owners[cols[0]] = cols[1]
    for line in apps_part.strip().splitlines():
        cols = [c.strip() for c in line.split(",")]
        if len(cols) != 3 or cols[0] not in gpus:
            continue
        user = owners.get(cols[1], UNATTRIBUTED)
        holders = gpus[cols[0]].holders
        holders[user] = holders.get(user, 0) + to_int(cols[2])
    for gpu in gpus.values():
        unseen = gpu.mem_used - sum(gpu.holders.values())
        if not gpu.holders and unseen >= UNATTRIBUTED_MIN_MIB:
            gpu.holders[UNATTRIBUTED] = unseen
    return sorted(gpus.values(), key=lambda g: g.index)


def probe(config: Config, timeout: float = 30.0) -> list[Gpu]:
    cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", config.ssh, PROBE_SCRIPT]
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise NicenessError("ssh timed out") from exc
    if done.returncode == 255:
        raise NicenessError(f"ssh failed: {done.stderr.strip()[-200:]}")
    return parse_probe(done.stdout)


SCHEMA = """
CREATE TABLE IF NOT EXISTS probes(ts INTEGER PRIMARY KEY, dt REAL NOT NULL, ok INTEGER NOT NULL, error TEXT);
CREATE TABLE IF NOT EXISTS gpus(ts INTEGER NOT NULL, gpu INTEGER NOT NULL, util INTEGER NOT NULL,
  mem_used INTEGER NOT NULL, mem_total INTEGER NOT NULL, PRIMARY KEY(ts, gpu));
CREATE TABLE IF NOT EXISTS holds(ts INTEGER NOT NULL, gpu INTEGER NOT NULL, user TEXT NOT NULL,
  mem INTEGER NOT NULL, PRIMARY KEY(ts, gpu, user));
CREATE TABLE IF NOT EXISTS users(user TEXT PRIMARY KEY, first_seen INTEGER NOT NULL);
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(SCHEMA)
    return db


def record(db: sqlite3.Connection, config: Config, ts: int, gpus: list[Gpu] | None, error: str | None = None) -> None:
    """Store one probe. A sample is credited with the time since the last good one, capped at 3x the period."""
    last = db.execute("SELECT MAX(ts) FROM probes WHERE ok = 1").fetchone()[0]
    dt = float(min(ts - last, 3 * config.every)) if last is not None and ts > last else float(config.every)
    with db:
        if gpus is None:
            db.execute("INSERT OR REPLACE INTO probes VALUES (?, 0, 0, ?)", (ts, (error or "")[:300]))
            return
        db.execute("INSERT OR REPLACE INTO probes VALUES (?, ?, 1, NULL)", (ts, dt))
        db.executemany("INSERT OR REPLACE INTO gpus VALUES (?, ?, ?, ?, ?)",
                       [(ts, g.index, g.util, g.mem_used, g.mem_total) for g in gpus])
        rows = [(ts, g.index, user, mem) for g in gpus for user, mem in g.holders.items()]
        db.executemany("INSERT OR REPLACE INTO holds VALUES (?, ?, ?, ?)", rows)
        db.executemany("INSERT OR IGNORE INTO users VALUES (?, ?)", [(user, ts) for _, _, user, _ in rows])
        if config.retention_days:
            cut = ts - config.retention_days * 86400
            for table in ("probes", "gpus", "holds"):
                db.execute(f"DELETE FROM {table} WHERE ts < ?", (cut,))


def sampler(config: Config, stop: threading.Event, log=print) -> None:
    db = connect(config.db)
    while not stop.is_set():
        started = time.time()
        ts = int(started)
        try:
            record(db, config, ts, probe(config))
        except NicenessError as exc:
            record(db, config, ts, None, str(exc))
            log(f"[sample] {exc}")
        except Exception as exc:  # never let one bad probe kill the sampler
            log(f"[sample] unexpected {exc.__class__.__name__}: {exc}")
        stop.wait(max(1.0, config.every - (time.time() - started)))


# ---------------------------------------------------------------------------- stats


# A GPU shared by several users is split by the memory each holds there.
USED = """
WITH h AS (
  SELECT ts, gpu, user, mem, SUM(mem) OVER (PARTITION BY ts, gpu) AS tot, COUNT(*) OVER (PARTITION BY ts, gpu) AS n
  FROM holds WHERE ts >= ?)
SELECT h.user, SUM(p.dt * CASE WHEN tot > 0 THEN h.mem * 1.0 / tot ELSE 1.0 / n END)
FROM h JOIN probes p ON p.ts = h.ts AND p.ok = 1 GROUP BY h.user
"""


def stats(db: sqlite3.Connection, config: Config, window: str, now: float | None = None) -> dict:
    """Users ranked by GPU-hours used in the window, with their share of all the machine's GPU time."""
    if window not in WINDOWS:
        raise NicenessError(f"window must be one of {', '.join(WINDOWS)}")
    span = WINDOWS[window]
    now = int(now if now is not None else time.time())
    since = now - span if span else 0
    first, last, covered = db.execute(
        "SELECT MIN(ts), MAX(ts), COALESCE(SUM(dt), 0) FROM probes WHERE ok = 1 AND ts >= ?", (since,)
    ).fetchone()
    n_gpus = db.execute("SELECT COUNT(DISTINCT gpu) FROM gpus WHERE ts = (SELECT MAX(ts) FROM gpus)").fetchone()[0] or 0
    capacity = covered * n_gpus
    used = dict(db.execute(USED, (since,)).fetchall())
    total = sum(used.values())
    # Colour slots follow first appearance on the machine; the unattributed bucket never takes one.
    order = [row[0] for row in db.execute("SELECT user FROM users WHERE user != ? ORDER BY first_seen, user", (UNATTRIBUTED,))]
    users = [
        {"user": user, "gpu_h": round(seconds / 3600, 2),
         "share": round(seconds / capacity, 4) if capacity else 0.0,
         "slot": order.index(user) if user in order else None}
        for user, seconds in sorted(used.items(), key=lambda kv: (kv[0] == UNATTRIBUTED, -kv[1]))
    ]
    return {
        "title": config.title,
        "window": window,
        "generated_at": now,
        "every_s": config.every,
        "sampled": {"from": first, "to": last, "hours": round(covered / 3600, 2)},
        "gpus": n_gpus,
        "capacity_gpu_h": round(capacity / 3600, 2),
        "used_gpu_h": round(total / 3600, 2),
        "unused_gpu_h": round(max(0.0, capacity - total) / 3600, 2),
        "users": users,
    }


# ---------------------------------------------------------------------------- web


def make_handler(config: Config):
    class Handler(BaseHTTPRequestHandler):
        server_version = "niceness"
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
                elif url.path == "/api/stats":
                    db = connect(config.db)
                    try:
                        data = stats(db, config, parse_qs(url.query).get("window", ["7d"])[0])
                    finally:
                        db.close()
                    self._send(200, "application/json; charset=utf-8", json.dumps(data).encode())
                else:
                    self._send(404, "text/plain; charset=utf-8", b"not found\n")
            except NicenessError as exc:
                self._send(400, "text/plain; charset=utf-8", f"{exc}\n".encode())
            except (OSError, sqlite3.Error):
                self._send(503, "text/plain; charset=utf-8", b"statistics are unavailable right now\n")

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


def cmd_serve(config: Config, args: argparse.Namespace) -> int:
    connect(config.db).close()
    stop = threading.Event()
    log = lambda line: print(line, file=sys.stderr, flush=True)  # noqa: E731
    if not args.no_sample:
        threading.Thread(target=sampler, args=(config, stop, log), daemon=True).start()
    server = ThreadingHTTPServer((config.bind, config.port), make_handler(config))
    server.daemon_threads = True
    print(f"[serve] niceness for {config.machine} on http://{config.bind}:{config.port}  every {config.every}s  db {config.db}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
    return 0


def cmd_sample(config: Config, args: argparse.Namespace) -> int:
    gpus = probe(config)
    for g in gpus:
        holders = ", ".join(f"{u} {m} MiB" for u, m in sorted(g.holders.items(), key=lambda kv: -kv[1])) or "free"
        print(f"GPU {g.index}: {g.util:>3}%  {g.mem_used:>6}/{g.mem_total} MiB  {holders}")
    if not args.dry_run:
        db = connect(config.db)
        record(db, config, int(time.time()), gpus)
        db.close()
    return 0


def cmd_report(config: Config, args: argparse.Namespace) -> int:
    db = connect(config.db)
    data = stats(db, config, args.window)
    db.close()
    if args.json:
        print(json.dumps(data, indent=2))
        return 0
    print(f"{config.title} - {args.window}: {data['used_gpu_h']} of {data['capacity_gpu_h']} GPU-h used "
          f"({data['gpus']} GPUs, {data['sampled']['hours']} h sampled)")
    for rank, u in enumerate(data["users"], 1):
        label = "   -" if u["user"] == UNATTRIBUTED else f"{rank:>3}."
        print(f"  {label} {u['user']:<20}{u['gpu_h']:>10.1f} GPU-h{u['share'] * 100:>8.1f}%")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--registry", help="machines registry (default: $RIGSYNC_REGISTRY or ~/.config/rigsync/machines.toml)")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="sample and serve the page")
    serve.add_argument("--no-sample", action="store_true", help="serve the stored data without probing")
    sample = sub.add_parser("sample", help="take one sample now")
    sample.add_argument("--dry-run", action="store_true", help="print it without storing it")
    report = sub.add_parser("report", help="print the leaderboard")
    report.add_argument("--window", default="7d", choices=list(WINDOWS))
    report.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        path = Path(args.registry or os.environ.get("RIGSYNC_REGISTRY") or DEFAULT_REGISTRY).expanduser()
        config = load_config(path)
        return {"serve": cmd_serve, "sample": cmd_sample, "report": cmd_report}[args.command](config, args)
    except NicenessError as exc:
        print(f"niceness: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
