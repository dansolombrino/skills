#!/usr/bin/env python3
"""Remind the chat that a dispatched wave is live, so the sweep-supervisor printout keeps coming.

Runs as a SessionStart and UserPromptSubmit hook on both hosts. Reads the hook payload on stdin,
looks for `<project>/.waves/_state/<wave_id>/queue.json` with no `finished_at`, and prints one
`additionalContext` line per live wave. Prints nothing when no wave is live. Never fails: a hook
error must not get in the way of the user's prompt.

Also runs as a PostToolUse hook on shell commands: right after `supervisor.py register ... --confirm`
it tells the agent to start the printout in the same turn, so monitoring never waits for a prompt.
Every other shell command is silent.

The wave layout constants are copied from supervisor.py and rigsync.py rather than imported, so the
hook starts in milliseconds on every prompt; tests/test_hooks.py keeps the copies in step.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

WAVE_RE = re.compile(r"\d{8}-\d{6}")  # rigsync.WAVE_RE
TERMINAL = {"done", "failed", "excluded"}  # supervisor.TERMINAL_STATES
PROMPT_EVERY_S = 5 * 60  # on UserPromptSubmit, remind at most this often per session


def project_root(cwd: str) -> Path | None:
    """Nearest ancestor of cwd (or the host's project dir) holding `.waves/_state`."""
    starts = [os.environ.get("CLAUDE_PROJECT_DIR"), cwd]
    for start in filter(None, starts):
        for d in [Path(start), *Path(start).parents]:
            if (d / ".waves" / "_state").is_dir():
                return d
    return None


def live_waves(root: Path) -> list[tuple[str, int, int]]:
    """(wave_id, done, total) for every wave whose queue has no finished_at."""
    out = []
    for qp in sorted((root / ".waves" / "_state").glob("*/queue.json")):
        wave_id = qp.parent.name
        if not WAVE_RE.fullmatch(wave_id):
            continue
        try:
            queue = json.loads(qp.read_text())
        except (OSError, ValueError):
            continue
        if queue.get("finished_at"):
            continue
        runs = queue.get("runs") or []
        done = sum(r.get("state") in TERMINAL for r in runs)
        out.append((wave_id, done, len(runs)))
    return out


def throttled(session: str, key: str) -> bool:
    """True when this session was already reminded about `key` within PROMPT_EVERY_S."""
    data = os.environ.get("CLAUDE_PLUGIN_DATA") or os.environ.get("PLUGIN_DATA")
    if not data or not session:
        return False
    stamp = Path(data) / "wave_reminder" / f"{re.sub(r'[^A-Za-z0-9_.-]', '_', session)}.json"
    try:
        seen = json.loads(stamp.read_text())
    except (OSError, ValueError):
        seen = {}
    now = time.time()
    if now - seen.get(key, 0) < PROMPT_EVERY_S:
        return True
    seen[key] = now
    try:
        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.write_text(json.dumps(seen))
    except OSError:
        pass
    return False


REGISTER_RE = re.compile(r"supervisor\.py\b.*\bregister\b.*--wave[ =](\d{8}-\d{6})")


def registered_wave(payload: dict) -> str | None:
    """The wave id a just-run `supervisor.py register --confirm` registered, else None."""
    if payload.get("tool_name") not in ("Bash", "shell", "local_shell"):
        return None
    tool_input = payload.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if isinstance(command, list):
        command = " ".join(str(part) for part in command)
    if not isinstance(command, str) or "--confirm" not in command or "--dry-run" in command:
        return None
    match = REGISTER_RE.search(command)
    return match.group(1) if match else None


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        return 0
    if not isinstance(payload, dict):
        return 0
    event = payload.get("hook_event_name") or "SessionStart"
    if event == "PostToolUse":
        wave = registered_wave(payload)
        if wave:
            text = (
                f"Wave {wave} was just registered. Start its `sweep-supervisor` printout now, in this turn, "
                f"without asking: run `table --wave {wave} --cycle --full`, post it, then arm the host's "
                f"scheduled wake-up at the chat update interval (the user's N minutes, else 1) and paste "
                f"`table --wave {wave}` verbatim at every tick until it says finished."
            )
            print(json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}))
        return 0
    root = project_root(payload.get("cwd") or os.getcwd())
    waves = live_waves(root) if root else []
    if not waves:
        return 0
    key = ",".join(w for w, _, _ in waves)
    # SessionStart always reminds and starts the throttle window, so the first prompt does not repeat it
    if throttled(payload.get("session_id") or "", key) and event == "UserPromptSubmit":
        return 0
    lines = [
        f"Wave {w} is live ({done}/{total} runs finished): keep posting the `sweep-supervisor` "
        f"printout (`table --wave {w}`) on its cadence, and re-arm the scheduled wake-up if none "
        f"is armed in this chat."
        for w, done, total in waves
    ]
    print(json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": "\n".join(lines)}}))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # a reminder hook must never break the session
        sys.exit(0)
