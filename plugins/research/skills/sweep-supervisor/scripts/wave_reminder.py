#!/usr/bin/env python3
"""State, at session start, the facts of every live wave in this project. No instructions.

Runs as a SessionStart hook on both hosts. Reads the hook payload on stdin, looks for
`<project>/.waves/_state/<wave_id>/queue.json` with no `finished_at`, and prints one
`additionalContext` line per live wave:

    Live wave <id> in this project: <done>/<total> done, ETA <clock>, <n> warnings. Dashboard: <url>.

The numbers come from the supervisor's own `brief_view` of the wave's snapshot (the same data the
printout and the rig-board viewer show); the dashboard is the rig-board viewer's Experiments tab,
named only when the machine registry declares the viewer's port. Prints nothing for any other hook
event or when no wave is live, and never fails: a hook error must not get in the way of a session.

The wave layout constants are copied from supervisor.py and rigsync.py rather than imported, so a
project without live waves costs no import at all; tests/test_hooks.py keeps the copies in step.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import tomllib
from pathlib import Path

WAVE_RE = re.compile(r"\d{8}-\d{6}")  # rigsync.WAVE_RE
WILDCARD_BINDS = {"", "0.0.0.0", "::", "[::]"}


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


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        return 0
    if not isinstance(payload, dict) or (payload.get("hook_event_name") or "SessionStart") != "SessionStart":
        return 0
    root = project_root(payload.get("cwd") or os.getcwd())
    waves = live_waves(root) if root else []
    if not waves:
        return 0
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
    for wave_id, queue in waves:
        try:
            lines.append(fact(root, wave_id, queue, sup, board_config, url))
        except Exception:
            lines.append(fact(root, wave_id, queue, None, None, url))
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "\n".join(lines)}}))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # a hook must never break the session
        sys.exit(0)
