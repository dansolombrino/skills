#!/usr/bin/env python3
"""progress-updates: sample long-running tasks and render one fixed status/ETA table.

A *board* groups the tasks one chat is watching:

    start  [--every N]                     -> prints the board id (N: the user's update minutes)
    add    --board B --name N --kind K ... -> registers a task
    table  --board B                       -> samples every task, prints the table
    watch  --board B [--poll S] [--timeout S]
                                           -> blocks, sampling every S seconds, and returns as
                                              soon as something changes (a task done, failed,
                                              ended, newly stalled, error in log, or all tasks
                                              finished) or the timeout passes; prints
                                              "watch: <reason>" and then the table. Exit 0.
    remove --board B --name N | stop --board B | list

Task kinds (each may live on another machine with --host, read over ssh):

    rsync  --log F           rsync --info=progress2 output captured in F
    log    --log F           last tqdm bar, "epoch|step|iter|batch a/b", or "NN%" in F;
                             --pattern REGEX with named groups done and total overrides
    size   --path P --total  bytes under P against a known total (62G, or --total-of SRC)

Every kind accepts --exit-file F (holds the exit code once the command ends) and --pid N.
ETAs are linear over the recent samples; with fewer than two samples the tool's own
estimate (rsync, tqdm) is used, and otherwise the cell says why there is none.
State lives in ${XDG_STATE_HOME:-~/.local/state}/progress-updates/<board>.json.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import secrets
import shlex
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

TAIL_BYTES = 65536
MAX_SAMPLES = 60
FAIL_RE = r"Traceback \(most recent call last\)|rsync error|CUDA out of memory|\bKilled\b"
TERMINAL = ("done", "failed", "ended")
DEFAULT_WINDOW = 600.0
WATCH_POLL = 30.0
WATCH_TIMEOUT = 6 * 3600.0

RSYNC_RE = re.compile(r"([\d,]+)\s+(\d{1,3})%\s+\S+/s\s+(\d+):(\d\d):(\d\d)")
TQDM_RE = re.compile(r"(\d+)/(\d+)\s*\[(?:[\d:]+)<((?:\d+:)?\d+:\d\d|\?)")
KEYWORD_RE = re.compile(r"(?i)\b(epoch|step|iter(?:ation)?|batch)\s*[:=]?\s*\[?\s*(\d+)\s*/\s*(\d+)")
PERCENT_RE = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%")
SIZE_RE = re.compile(r"^\s*([\d.]+)\s*([KMGTP]?)i?B?\s*$", re.IGNORECASE)


def state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return Path(base) / "progress-updates"


def board_path(board: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", board):
        sys.exit(f"invalid board id: {board!r}")
    return state_dir() / f"{board}.json"


def load(board: str) -> dict:
    path = board_path(board)
    if not path.exists():
        sys.exit(f"no board {board!r}; run `start` first")
    return json.loads(path.read_text())


def try_load(board: str) -> dict | None:
    """Like load, but None for a missing board and {} for one caught mid-rewrite."""
    try:
        return json.loads(board_path(board).read_text())
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return {}


def save(board: str, data: dict) -> None:
    path = board_path(board)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    tmp.replace(path)


@contextmanager
def locked(board: str):
    """Serialize read-modify-write of one board between concurrent commands (add vs watch)."""
    path = board_path(board).with_suffix(".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def commit(board: str, data: dict) -> bool:
    """Write sampled tasks back without losing a concurrent add, remove, or stop.

    Sampling runs outside the lock (it can take a while over ssh), so the board on disk may have
    changed meanwhile: keep its task list, and update only the tasks that are still the same
    registration. False when the board was stopped."""
    with locked(board):
        fresh = try_load(board)
        if fresh is None:
            return False
        if not fresh:
            fresh = data
        for name, task in data["tasks"].items():
            current = fresh["tasks"].get(name)
            if current is not None and current.get("id") == task.get("id"):
                fresh["tasks"][name] = task
        save(board, fresh)
        return True


def sh(host: str | None, command: str) -> tuple[int, str]:
    argv = ["bash", "-c", command] if not host else [
        "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host, command]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=60, errors="replace")
    except subprocess.TimeoutExpired:
        return 124, ""
    except OSError:
        return 127, ""
    return proc.returncode, proc.stdout


def parse_size(text: str) -> int:
    match = SIZE_RE.match(text)
    if not match:
        sys.exit(f"cannot parse size {text!r} (use e.g. 62G, 512M, 1048576)")
    number, unit = match.groups()
    return int(float(number) * 1024 ** " KMGTP".index(unit.upper() or " "))


def human_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def hms_seconds(text: str) -> float | None:
    if text == "?":
        return None
    parts = [int(p) for p in text.split(":")]
    seconds = 0
    for part in parts:
        seconds = seconds * 60 + part
    return float(seconds)


def du_bytes(host: str | None, path: str) -> int | None:
    code, out = sh(host, f"du -sb -- {shlex.quote(path)} 2>/dev/null | cut -f1")
    return int(out.strip()) if code == 0 and out.strip().isdigit() else None


def parse_log(kind: str, text: str, pattern: str | None) -> dict | None:
    """Return the last progress reading in a log tail: done, total, unit, tool_remaining."""
    lines = re.split(r"[\r\n]+", text)
    if kind == "rsync":
        for line in reversed(lines):
            match = RSYNC_RE.search(line)
            if match:
                done = int(match.group(1).replace(",", ""))
                pct = int(match.group(2))
                remaining = int(match.group(3)) * 3600 + int(match.group(4)) * 60 + int(match.group(5))
                total = done * 100 / pct if pct else None
                return {"done": done, "total": total, "unit": "bytes", "pct": pct,
                        "tool_remaining": float(remaining), "tool": "rsync"}
        return None
    if pattern:
        regex = re.compile(pattern)
        for line in reversed(lines):
            match = regex.search(line)
            if match:
                return {"done": float(match.group("done")), "total": float(match.group("total")),
                        "unit": "count"}
        return None
    for line in reversed(lines):
        match = TQDM_RE.search(line)
        if match:
            return {"done": int(match.group(1)), "total": int(match.group(2)), "unit": "count",
                    "tool_remaining": hms_seconds(match.group(3)), "tool": "tqdm"}
        match = KEYWORD_RE.search(line)
        if match:
            return {"done": int(match.group(2)), "total": int(match.group(3)),
                    "unit": match.group(1).lower()}
        match = PERCENT_RE.search(line)
        if match:
            return {"done": float(match.group(1)), "total": 100.0, "unit": "percent"}
    return None


def sample(task: dict) -> dict:
    """Read one task's current progress, liveness, and exit status."""
    host = task.get("host")
    reading: dict = {}
    tail = ""
    if task["kind"] in ("rsync", "log"):
        code, tail = sh(host, f"tail -c {TAIL_BYTES} -- {shlex.quote(task['log'])}")
        if code != 0:
            reading["error"] = "log unreadable"
        else:
            reading.update(parse_log(task["kind"], tail, task.get("pattern")) or {})
    else:
        done = du_bytes(host, task["path"])
        if done is None:
            reading["error"] = "path unreadable"
        else:
            reading.update({"done": done, "total": task["total"], "unit": "bytes"})
    if task.get("exit_file"):
        code, out = sh(host, f"cat -- {shlex.quote(task['exit_file'])} 2>/dev/null")
        if code == 0 and out.strip().lstrip("-").isdigit():
            reading["exit"] = int(out.strip())
    if task.get("pid") and "exit" not in reading:
        code, _ = sh(host, f"kill -0 {int(task['pid'])} 2>/dev/null")
        reading["alive"] = code == 0
    if tail and re.search(task.get("fail_pattern") or FAIL_RE, tail):
        reading["fail_hit"] = True
    return reading


def safe_sample(task: dict) -> dict:
    """sample(), but a probe that blows up reads as an unreadable task instead of a crash."""
    try:
        return sample(task)
    except Exception:  # noqa: BLE001 - one bad task must never stop the board
        return {"error": "probe unreadable"}


def fraction(reading: dict) -> float | None:
    if "pct" in reading:
        return reading["pct"] / 100
    if reading.get("total"):
        return min(reading["done"] / reading["total"], 1.0)
    return None


def progress_cell(reading: dict) -> str:
    frac = fraction(reading)
    if frac is None:
        return "—"
    pct = f"{frac * 100:.0f}%"
    unit = reading.get("unit")
    if unit == "percent":
        return pct
    if unit == "bytes":
        total = reading.get("total")
        return f"{pct} · {human_bytes(reading['done'])}/{human_bytes(total)}" if total else pct
    label = "" if unit == "count" else f" {unit}"
    return f"{pct} · {reading['done']:g}/{reading['total']:g}{label}"


def clock(ts: float, now: float) -> str:
    moment = datetime.fromtimestamp(ts)
    today = datetime.fromtimestamp(now).date()
    return moment.strftime("%H:%M") if moment.date() == today else moment.strftime("%a %d %b %H:%M")


def eta_cell(task: dict, reading: dict, now: float, window: float) -> str:
    frac = fraction(reading)
    if frac is None:
        return "unavailable: no progress reading"
    if frac >= 1:
        return "—"
    samples = [s for s in task["samples"] if now - s[0] <= window]
    if len(samples) >= 2 and samples[-1][0] - samples[0][0] >= 60:
        (t0, f0), (t1, f1) = samples[0], samples[-1]
        minutes = round((t1 - t0) / 60)
        if f1 <= f0:
            return f"unavailable: no progress in {minutes}m"
        eta = t1 + (1 - f1) * (t1 - t0) / (f1 - f0)
        return f"{clock(eta, now)} (linear, {minutes}m)"
    if reading.get("tool_remaining") is not None:
        return f"{clock(now + reading['tool_remaining'], now)} ({reading['tool']})"
    return "pending: needs a second sample"


def state_cell(task: dict, reading: dict, now: float, stall: float) -> str:
    frac = fraction(reading)
    if "exit" in reading:
        return "done" if reading["exit"] == 0 else f"failed (exit {reading['exit']})"
    if reading.get("alive") is False:
        return "done" if frac is not None and frac >= 1 else "ended (no exit code)"
    if frac is not None and frac >= 1 and not task.get("pid") and not task.get("exit_file"):
        return "done"
    if reading.get("error"):
        return reading["error"]
    if reading.get("fail_hit"):
        return "error in log"
    changed = task.get("last_change")
    if changed is not None and now - changed >= stall:
        return f"stalled {round((now - changed) / 60)}m"
    return "ok"


def cmd_start(args: argparse.Namespace) -> None:
    board = args.board or datetime.now().strftime("%Y%m%d-%H%M") + "-" + secrets.token_hex(2)
    if board_path(board).exists():
        sys.exit(f"board {board!r} already exists")
    save(board, {"every": args.every, "anchor": time.time(), "tasks": {}})
    print(board)


def cmd_add(args: argparse.Namespace) -> None:
    load(args.board)
    if args.kind in ("rsync", "log") and not args.log:
        sys.exit(f"--kind {args.kind} needs --log")
    if args.kind == "size":
        if not args.path or not (args.total or args.total_of):
            sys.exit("--kind size needs --path and --total or --total-of")
    if args.pattern:
        groups = re.compile(args.pattern).groupindex
        if not {"done", "total"} <= set(groups):
            sys.exit("--pattern needs named groups (?P<done>...) and (?P<total>...)")
    total = None
    if args.kind == "size":
        total = parse_size(args.total) if args.total else du_bytes(args.total_of_host, args.total_of)
        if not total:
            sys.exit("could not determine the total size")
    task = {
        "id": secrets.token_hex(4), "kind": args.kind, "host": args.host, "log": args.log, "path": args.path, "total": total,
        "pattern": args.pattern, "fail_pattern": args.fail_pattern, "exit_file": args.exit_file,
        "pid": args.pid, "samples": [], "last_change": None, "final": None,
    }
    with locked(args.board):
        data = load(args.board)
        data["tasks"][args.name] = task
        save(args.board, data)
    print(f"watching {args.name!r} on board {args.board}")


def timing(data: dict, stall_minutes: float | None) -> tuple[float | None, float, float]:
    """(cadence seconds or None, ETA window seconds, stall threshold seconds) for a board."""
    period = data["every"] * 60 if data.get("every") else None
    window = max(2 * period, DEFAULT_WINDOW) if period else DEFAULT_WINDOW
    stall = stall_minutes * 60 if stall_minutes else window
    return period, window, stall


def refresh(data: dict, now: float, stall_minutes: float | None = None) -> list[list[str]]:
    """Sample every live task, update its history in place, and return the table rows."""
    _, window, stall = timing(data, stall_minutes)
    rows = []
    for name, task in data["tasks"].items():
        if task.get("final"):
            rows.append([name] + task["final"])
            continue
        reading = safe_sample(task)
        frac = fraction(reading)
        if frac is not None:
            if not task["samples"] or frac != task["samples"][-1][1]:
                task["last_change"] = now
            elif task["last_change"] is None:
                task["last_change"] = now
            task["samples"] = (task["samples"] + [[now, frac]])[-MAX_SAMPLES:]
        state = state_cell(task, reading, now, stall)
        row = [progress_cell(reading), eta_cell(task, reading, now, window), state]
        if state.split()[0] in TERMINAL:
            row[1] = f"finished {clock(now, now)}"
            task["final"] = row
        rows.append([name] + row)
    return rows


def all_finished(rows: list[list[str]]) -> bool:
    return bool(rows) and all(r[3].split()[0] in TERMINAL for r in rows)


def render(data: dict, rows: list[list[str]], now: float) -> str:
    period, _, _ = timing(data, None)
    stamp = datetime.fromtimestamp(now).strftime("%H:%M")
    if all_finished(rows):
        header = f"⏱ {stamp} · all tasks finished"
    elif period:
        tick = int((now - data["anchor"] + 30) // period)
        header = f"⏱ {stamp} · tick {tick} · next {clock(data['anchor'] + (tick + 1) * period, now)}"
    else:
        header = f"⏱ {stamp}"
    lines = [header, "", "| task | progress | ETA (basis) | state |", "|---|---|---|---|"]
    for row in rows:
        lines.append("| " + " | ".join(cell.replace("|", "/") for cell in row) + " |")
    if not rows:
        lines.append("| (no tasks) | — | — | — |")
    return "\n".join(lines)


def cmd_table(args: argparse.Namespace) -> None:
    data = load(args.board)
    now = time.time()
    rows = refresh(data, now, args.stall)
    commit(args.board, data)
    print(render(data, rows, now))


def alert_class(state: str) -> str | None:
    if state.startswith("stalled"):
        return "stalled"
    if state == "error in log":
        return "error in log"
    if state.endswith("unreadable"):
        return "unreadable"
    return None


def events(data: dict, before: dict, rows: list[list[str]], now: float, stall: float) -> list[str]:
    """What changed since the last report, updating each task's notified marker in place.

    A task reaching done/failed/ended is reported once (its row freezes). Stalled and error in
    log are reported when a task newly enters them; an unreadable log or path only once it has
    stayed unreadable for the stall threshold, so a single ssh hiccup stays quiet."""
    found = []
    for name, _, _, state in rows:
        task = data["tasks"][name]
        if task.get("final"):
            if not before.get(name):
                found.append(f"{name} {state}")
            continue
        cls = alert_class(state)
        if cls == "unreadable":
            since = task.setdefault("unreadable_since", now)
            if now - since < stall:
                continue
        else:
            task.pop("unreadable_since", None)
        if cls is None:
            task.pop("notified", None)
        elif task.get("notified") != cls:
            task["notified"] = cls
            found.append(f"{name} {state}")
    return found


def cmd_watch(args: argparse.Namespace) -> None:
    load(args.board)  # a missing board is a usage error, reported before blocking
    poll = max(args.poll, 0.01)
    deadline = time.monotonic() + args.timeout
    data: dict | None = {}
    rows: list[list[str]] = []
    now = time.time()
    while True:
        data = try_load(args.board)
        now = time.time()
        if data is None:
            print("watch: board removed")
            return
        if data:
            if not data.get("tasks"):
                print("watch: no tasks on board")
                print()
                print(render(data, [], now))
                return
            before = {name: bool(task.get("final")) for name, task in data["tasks"].items()}
            _, _, stall = timing(data, args.stall)
            rows = refresh(data, now, args.stall)
            found = events(data, before, rows, now, stall)
            if all_finished(rows):
                found.append("all tasks finished")
            try:
                if not commit(args.board, data):
                    print("watch: board removed")
                    return
            except OSError:
                pass
            if found:
                print("watch: " + "; ".join(found))
                print()
                print(render(data, rows, now))
                return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            t = args.timeout
            span = f"{t / 3600:g}h" if t >= 3600 else f"{t / 60:g}m" if t >= 60 else f"{t:g}s"
            print(f"watch: timeout after {span} with nothing new; run watch again")
            if data:
                print()
                print(render(data, rows, now))
            return
        time.sleep(min(poll, remaining))


def cmd_remove(args: argparse.Namespace) -> None:
    with locked(args.board):
        data = load(args.board)
        if data["tasks"].pop(args.name, None) is None:
            sys.exit(f"no task {args.name!r} on board {args.board}")
        save(args.board, data)


def cmd_stop(args: argparse.Namespace) -> None:
    with locked(args.board):
        board_path(args.board).unlink(missing_ok=True)
    board_path(args.board).with_suffix(".lock").unlink(missing_ok=True)
    print(f"board {args.board} removed")


def cmd_list(args: argparse.Namespace) -> None:
    for path in sorted(state_dir().glob("*.json")):
        data = json.loads(path.read_text())
        every = f"every {data['every']:g}m" if data.get("every") else "on change"
        print(f"{path.stem}  {every}  tasks: {', '.join(data['tasks']) or '-'}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("start", help="create a board")
    p.add_argument("--every", type=float, help="the user's requested update interval in minutes "
                   "(only when they asked for timed updates; sets the header's next time)")
    p.add_argument("--board", help="board id (default: timestamp + random suffix)")
    p.set_defaults(func=cmd_start)

    p = sub.add_parser("add", help="watch a task")
    p.add_argument("--board", required=True)
    p.add_argument("--name", required=True, help="short task label shown in the table")
    p.add_argument("--kind", required=True, choices=("rsync", "log", "size"))
    p.add_argument("--host", help="ssh host where the log/path/pid live (default: local)")
    p.add_argument("--log")
    p.add_argument("--path")
    p.add_argument("--total", help="size kind: total bytes, e.g. 62G")
    p.add_argument("--total-of", help="size kind: measure the total from this source path")
    p.add_argument("--total-of-host", help="ssh host of --total-of (default: local)")
    p.add_argument("--pattern", help="log kind: regex with named groups done and total")
    p.add_argument("--fail-pattern", help=f"regex marking an error in the log (default: {FAIL_RE})")
    p.add_argument("--exit-file", help="file the command writes its exit code to when it ends")
    p.add_argument("--pid", type=int, help="process to check for liveness")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("table", help="sample every task and print the status table")
    p.add_argument("--board", required=True)
    p.add_argument("--stall", type=float, help="minutes without progress before 'stalled'")
    p.set_defaults(func=cmd_table)

    p = sub.add_parser("watch", help="block until a task finishes, stalls, or errors, then print "
                       "the reason and the table")
    p.add_argument("--board", required=True)
    p.add_argument("--poll", type=float, default=WATCH_POLL,
                   help=f"seconds between samples (default {WATCH_POLL:g})")
    p.add_argument("--timeout", type=float, default=WATCH_TIMEOUT,
                   help=f"seconds before giving up with reason 'timeout' (default {WATCH_TIMEOUT:g})")
    p.add_argument("--stall", type=float, help="minutes without progress before 'stalled'")
    p.set_defaults(func=cmd_watch)

    p = sub.add_parser("remove", help="stop watching one task")
    p.add_argument("--board", required=True)
    p.add_argument("--name", required=True)
    p.set_defaults(func=cmd_remove)

    p = sub.add_parser("stop", help="delete a board")
    p.add_argument("--board", required=True)
    p.set_defaults(func=cmd_stop)

    p = sub.add_parser("list", help="list boards")
    p.set_defaults(func=cmd_list)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
