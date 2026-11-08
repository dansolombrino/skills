#!/usr/bin/env python3
"""State, at session start, the facts of every live wave in this project. No instructions.

Runs as a SessionStart hook on every host: from the plugin's hooks/hooks.json, or on omp from its
extension hooks/omp_session_start.ts, with the same payload. Reads the hook payload on stdin, looks for
`<project>/.waves/_state/<wave_id>/queue.json` with no `finished_at`, and prints one
`additionalContext` line per live wave:

    Live wave <id> in this project: <done>/<total> done, ETA <clock>, <n> warnings. Dashboard: <url>.

A wave with no fresh marker from a running `supervisor.py watch` (`watching.json` in its state
directory: not expired and, on this machine, its pid alive) also gets the fact that no chat is
following it and the exact command that does. Two or more such waves, with none followed, share one
closing line naming `watch --all`.

The numbers come from the supervisor's own `brief_view` of the wave's snapshot (the same data the
printout and the rig-board viewer show); the dashboard is the rig-board viewer's Experiments tab,
named only when the machine registry declares the viewer's port.

In a main checkout holding an EXPERIMENTS.md it also states what has drifted, from local files plus
two local git reads, never ssh: `inpr` rows in closed waves, worktrees of finished waves left behind,
a journal guard not installed (`core.hooksPath` not `.githooks`, or `.githooks/pre-commit` untracked),
and a branch other than the one sync.toml names. A linked worktree is on its own branch by design and holds a copy of the file, so it
gets none of these.

Prints nothing for any other hook event, for a supervisor agent, or when there is nothing to state,
and never fails: a hook error must not get in the way of a session.

The wave layout constants are copied from supervisor.py and rigsync.py rather than imported, so a
project without live waves costs no import at all; tests/test_hooks.py keeps the copies in step.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shlex
import subprocess
import sys
import time
import tomllib
from pathlib import Path

WAVE_RE = re.compile(r"\d{8}-\d{6}")  # rigsync.WAVE_RE
WILDCARD_BINDS = {"", "0.0.0.0", "::", "[::]"}
TERMINAL_ROWS = {"done", "failed", "superseded"}
GIT_TIMEOUT_S = 1.5  # of the hook's 5 s
MAX_NAMED = 5


def project_root(cwd: str) -> Path | None:
    """Nearest ancestor of cwd (or the host's project dir) holding `.waves/_state`."""
    starts = [os.environ.get("CLAUDE_PROJECT_DIR"), cwd]
    for start in filter(None, starts):
        for d in [Path(start), *Path(start).parents]:
            if (d / ".waves" / "_state").is_dir():
                return d
    return None


def live_waves(root: Path) -> list[tuple[str, dict]]:
    """(wave_id, queue) for every wave whose queue has no finished_at."""
    out = []
    for qp in sorted((root / ".waves" / "_state").glob("*/queue.json")):
        wave_id = qp.parent.name
        if not WAVE_RE.fullmatch(wave_id):
            continue
        try:
            queue = json.loads(qp.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(queue, dict) and not queue.get("finished_at"):
            out.append((wave_id, queue))
    return out


def registry_path() -> Path:
    return Path(os.environ.get("RIGSYNC_REGISTRY") or "~/.config/rigsync/machines.toml").expanduser()


def dashboard_url() -> str | None:
    """The rig-board viewer's Experiments tab, from the registry's `[board]` port and bind address."""
    try:
        board = tomllib.loads(registry_path().read_text()).get("board")
    except (OSError, ValueError):
        return None
    port = board.get("port") if isinstance(board, dict) else None
    if not isinstance(port, int) or isinstance(port, bool):
        return None
    bind = str(board.get("bind") or "")
    host = "localhost" if bind in WILDCARD_BINDS else bind
    return f"http://{host}:{port}/#experiments"


def followed(root: Path, wave_id: str) -> bool:
    """Whether a running watch keeps this wave's marker fresh. Local files and this machine's pids only."""
    try:
        marker = json.loads((root / ".waves" / "_state" / wave_id / "watching.json").read_text())
        if time.time() >= float(marker["until"]):
            return False
        if marker.get("host") == os.uname().nodename:
            os.kill(int(marker["pid"]), 0)
    except PermissionError:  # alive, under another user
        return True
    except (OSError, ValueError, KeyError, TypeError, OverflowError):
        return False
    return True


def watch_command(root: Path, scope: str) -> str:
    script = Path(__file__).resolve().with_name("supervisor.py")
    return f"`python3 {shlex.quote(str(script))} --root {shlex.quote(str(root))} watch {scope}`"


def load_supervisor():
    path = Path(__file__).resolve().with_name("supervisor.py")
    spec = importlib.util.spec_from_file_location("wave_reminder_supervisor", path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def fact(root: Path, wave_id: str, queue: dict, sup, board_config, url: str | None) -> str:
    runs = queue.get("runs") or []
    done, total, eta, warnings = sum(r.get("state") == "done" for r in runs), len(runs), "not computed yet", None
    if sup is not None:
        wave = sup.Wave(root, wave_id)
        snapshot = sup.board.read_json(wave.snapshot_path)
        if snapshot:
            at = sup.now()
            view = sup.brief_view(snapshot, at)
            done, total = view["done"], view["total"]
            moment = view["eta"]
            eta = sup.clock(moment["at"], at) if moment["kind"] == "eta" else "unavailable"
            warnings = len(sup.wave_warnings(wave, snapshot, at, board_config))
    parts = [f"{done}/{total} done", f"ETA {eta}"]
    if warnings is not None:
        parts.append(f"{warnings} warning{'s' if warnings != 1 else ''}")
    line = f"Live wave {wave_id} in this project: {', '.join(parts)}."
    return line + (f" Dashboard: {url}." if url else "")


def experiments_root(cwd: str) -> Path | None:
    """The main checkout holding the EXPERIMENTS.md nearest cwd; None in a linked worktree or anywhere else."""
    for start in filter(None, (os.environ.get("CLAUDE_PROJECT_DIR"), cwd)):
        for d in [Path(start), *Path(start).parents]:
            if (d / "EXPERIMENTS.md").is_file():
                return d if (d / ".git").is_dir() else None
    return None


def table_rows(text: str) -> list[dict[str, str]]:
    """Every row of a table with `wave` and `status` columns, outside code fences; a row of the wrong shape is reconcile.py's to report."""
    rows: list[dict[str, str]] = []
    columns: list[str] | None = None
    fenced = False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            fenced = not fenced
        if fenced or not line.lstrip().startswith("|"):
            columns = None
            continue
        cells = [cell.strip() for cell in re.split(r"(?<!\\)\|", line.strip().strip("|"))]
        if columns is None:
            columns = cells if {"wave", "status"} <= set(cells) else []
        elif columns and len(cells) == len(columns) and not set("".join(cells)) <= set("-: "):
            rows.append(dict(zip(columns, cells)))
    return rows


def queue_of(root: Path, wave_id: str) -> dict | None:
    try:
        queue = json.loads((root / ".waves" / "_state" / wave_id / "queue.json").read_text())
    except (OSError, ValueError):
        return None
    return queue if isinstance(queue, dict) else None


def named(items: list[str]) -> str:
    more = f" and {len(items) - MAX_NAMED} more" if len(items) > MAX_NAMED else ""
    return ", ".join(items[:MAX_NAMED]) + more


def guard_problem(root: Path) -> str | None:
    """Why the journal guard is not installed: the repository's own core.hooksPath must be `.githooks`
    and `.githooks/pre-commit` tracked. A global value is not this project's guard; exit 1 means unset
    or untracked, and any other failure (no repository, a timeout) says nothing."""
    environment = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE")}

    def git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=GIT_TIMEOUT_S, env=environment, check=False)

    try:
        config = git("config", "--local", "--get", "core.hooksPath")
        if config.returncode == 1:
            return "core.hooksPath is unset"
        if config.returncode:
            return None
        value = config.stdout.strip()
        if value.rstrip("/") != ".githooks":
            return f"core.hooksPath is {value}, not .githooks"
        if git("ls-files", "--error-unmatch", ".githooks/pre-commit").returncode == 1:
            return ".githooks/pre-commit is not tracked"
    except (OSError, subprocess.SubprocessError):
        return None
    return None


def branch_drift(root: Path) -> str | None:
    """The checkout's branch against sync.toml's `git.branch`, from the files alone."""
    try:
        wanted = tomllib.loads((root / "sync.toml").read_text()).get("git", {}).get("branch")
        head = (root / ".git" / "HEAD").read_text().strip()
    except (OSError, ValueError, AttributeError):
        return None
    if not isinstance(wanted, str) or not wanted or head == f"ref: refs/heads/{wanted}":
        return None
    current = f"on branch {head.removeprefix('ref: refs/heads/')}" if head.startswith("ref: refs/heads/") else "on a detached HEAD"
    return f"This checkout is {current}; sync.toml names {wanted} as the project's branch."


def drift_facts(cwd: str) -> list[str]:
    root = experiments_root(cwd)
    if root is None:
        return []
    rows = table_rows((root / "EXPERIMENTS.md").read_text(errors="replace"))
    by_wave: dict[str, list[str]] = {}
    for row in rows:
        by_wave.setdefault(row["wave"], []).append(row["status"])
    facts = []
    closed = [
        f"{wave} ({statuses.count('inpr')})" for wave, statuses in sorted(by_wave.items())
        if "inpr" in statuses and WAVE_RE.fullmatch(wave) and (queue_of(root, wave) or {}).get("finished_at")
    ]
    if closed:
        facts.append(f"EXPERIMENTS.md has inpr rows in closed waves: {named(closed)}.")
    left = []
    waves_dir = root / ".waves"
    for wave in sorted(p.name for p in waves_dir.iterdir()) if waves_dir.is_dir() else []:
        if not WAVE_RE.fullmatch(wave) or not (waves_dir / wave).is_dir():
            continue
        queue = queue_of(root, wave)
        statuses = by_wave.get(wave, [])
        if (queue.get("finished_at") if queue is not None else statuses and TERMINAL_ROWS.issuperset(statuses)):
            left.append(f".waves/{wave}")
    if left:
        facts.append(f"Worktrees of finished waves remain: {named(left)}.")
    guard = guard_problem(root)
    if guard:
        facts.append(f"The journal guard is not installed in this checkout: {guard}.")
    branch = branch_drift(root)
    if branch:
        facts.append(branch)
    return facts


def live_wave_lines(cwd: str) -> list[str]:
    root = project_root(cwd)
    waves = live_waves(root) if root else []
    if not waves:
        return []
    try:
        sup = load_supervisor()
    except Exception:  # the facts degrade to the queue's own counts
        sup = None
    board_config = None
    if sup is not None:
        try:
            board_config = sup.board.load_config(registry_path())
        except Exception:
            board_config = None
    url = dashboard_url()
    lines = []
    unfollowed = {wave_id for wave_id, _queue in waves if not followed(root, wave_id)}
    together = len(unfollowed) >= 2 and len(unfollowed) == len(waves)
    for wave_id, queue in waves:
        try:
            line = fact(root, wave_id, queue, sup, board_config, url)
        except Exception:
            line = fact(root, wave_id, queue, None, None, url)
        if wave_id in unfollowed and not together:
            line += f" No chat is following it; {watch_command(root, f'--wave {wave_id}')} follows it."
        lines.append(line)
    if together:
        lines.append(f"No chat is following these waves; {watch_command(root, '--all')} follows them all.")
    return lines


def main() -> int:
    if os.environ.get("SWEEP_SUPERVISOR_MODEL"):  # a supervisor agent: it owns the wave, and a watch from it would take a chat's notices
        return 0
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        return 0
    if not isinstance(payload, dict) or (payload.get("hook_event_name") or "SessionStart") != "SessionStart":
        return 0
    cwd = payload.get("cwd") or os.getcwd()
    lines = live_wave_lines(cwd)
    try:
        lines += drift_facts(cwd)
    except Exception:  # a torn file costs its facts, not the live-wave lines
        pass
    if not lines:
        return 0
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "\n".join(lines)}}))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # a hook must never break the session
        sys.exit(0)
