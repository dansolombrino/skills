#!/usr/bin/env python3
"""PostToolUse hook: nudge the chat to put a long-running shell task on a progress-updates board.

Both hosts report their shell tool as `Bash` with the command in `tool_input.command`. One
`additionalContext` line is printed when the command was backgrounded or a command-position word
looks long-running (a transfer, a download, a training or eval launch). Commands already tracked
(progress.py, sweep-supervisor, rig-board, `.waves/`) stay silent. Never fails: a hook error must
not get in the way of the tool result.
"""

from __future__ import annotations

import json
import re
import sys

# Heredoc bodies and quoted strings are data, not commands, so they are blanked first.
HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1[^\n]*\n.*?(?:\n\2\b|\Z)", re.S)
QUOTED = re.compile(r"'[^']*'|\"(?:[^\"\\]|\\.)*\"")
# Matched only in command position: the start of each `;`/`&`/`|`/`(`/newline segment, after any
# `VAR=value`, `sudo`, `time`, `env`, `exec`, `uv run` prefixes. So `grep rsync notes.md` is quiet.
SEGMENTS = re.compile(r"[;&|()\n]+")
PREFIX = re.compile(r"^(?:\s*(?:[A-Za-z_][A-Za-z0-9_]*=\S*|sudo|time|env|exec|uv\s+run|poetry\s+run)\s+)*\s*")
LONG = re.compile(
    r"(?:\S*/)?(?:rsync|scp|rclone|gsutil\s+(?:-m\s+)?cp|aws\s+s3\s+(?:cp|sync)|azcopy|wget|"
    r"huggingface-cli\s+download|hf\s+download|nohup|torchrun|accelerate\s+launch|deepspeed|sbatch|"
    # a training/eval script: the keyword must be in the script's own name, not its directory
    r"python3?\s+(?:-\S+\s+)*(?:\S*/)?[^/\s]*(?:train|eval|finetune|pretrain|benchmark)[^/\s]*)(?:\s|$)"
)
QUICK = re.compile(r"\s(?:--version|--help|-h|-V)(?:\s|$)")
TRACKED = re.compile(r"progress\.py|supervisor\.py|sweep-supervisor|rig-board|rigboard|\.waves/")


def command_of(tool_input) -> str:
    """The shell command one tool call carries; argv `["bash", "-lc", "<script>"]` yields the script."""
    if not isinstance(tool_input, dict):
        return ""
    value = tool_input.get("command", tool_input.get("cmd"))
    if isinstance(value, list):
        argv = [str(a) for a in value]
        value = argv[2] if len(argv) >= 3 and argv[1] in ("-c", "-lc") else " ".join(argv)
    return value if isinstance(value, str) else ""


def looks_long(command: str) -> bool:
    code = QUOTED.sub("''", HEREDOC.sub("", command))
    for segment in SEGMENTS.split(code):
        segment = PREFIX.sub("", segment, count=1)
        if LONG.match(segment) and not QUICK.search(segment):
            return True
    return False


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        return 0
    if not isinstance(payload, dict) or payload.get("tool_name") != "Bash":
        return 0
    tool_input = payload.get("tool_input")
    command = command_of(tool_input)
    background = isinstance(tool_input, dict) and tool_input.get("run_in_background") is True
    if not command or TRACKED.search(command) or not (background or looks_long(command)):
        return 0
    shown = " ".join(command.split())
    shown = shown if len(shown) <= 120 else shown[:117] + "..."
    context = (
        f"Possibly long-running task started (`{shown}`): unless it will finish within about two "
        "minutes, put it on the `progress-updates` board (start or add, then arm the scheduled "
        "wake-up). Skip this if it is already on a board or belongs to a sweep-dispatch wave."
    )
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": context}}))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # a reminder hook must never break the session
        sys.exit(0)
