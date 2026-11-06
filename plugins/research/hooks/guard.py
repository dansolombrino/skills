#!/usr/bin/env python3
"""PreToolUse guard: the written rules of a research project, checked on the agent's own tool calls.

This catches accidents. It is not a security boundary: it reads a command the way a shell would,
but anything that hides the action (a script, `python -c`, a variable holding the path) gets past
it, and the agent can turn it off like any other user setting. Skills' own scripts run their
subprocesses unseen, which is correct: `rigsync prune` may delete, a hand-written `rm` may not.

Log-only by default: a would-be refusal is appended to a JSONL log and the call proceeds. Mode
`enforce` denies instead; mode `off` does nothing. See hooks/README.md, and `guard.py --help`.

It applies only in a project tagged `research`, and it looks only at the tool's input. Any error
lets the call proceed: a guard bug must never block all Bash. Bash commands are parsed into simple
commands, so words inside a commit message, an echo or a grep pattern do not match; `ssh <host>
...`, `sh -c`, `eval` and here-documents fed to a shell or to ssh are parsed in turn.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

MAX_LOG_BYTES = 5_000_000
MAX_LOGGED_INPUT = 400
MODES = ("log", "enforce", "off")

RULES = {
    "git-push-force": "force-pushing rewrites history that rigs and wave tags depend on; rig-sync moves revisions",
    "git-reset-hard": "it discards work that may not exist anywhere else",
    "git-clean": "it deletes untracked files, which include gitignored run data with -x",
    "git-stash": "the stash stack is shared by every worktree and other sessions; use a work-in-progress commit",
    "git-commit-no-verify": "it skips the project's pre-commit guards; fix what they report",
    "rm-mv-run-data": "run data under checkpoints/, evaluations/ and rid-* moves only through rig-sync (prune, offload) and run-identity",
    "remote-copy": "scp and hand-written rsync bypass rig-sync's checks; use rig-sync",
    "runid-rollback-force": "`migrate rollback --force` needs the user",
    "kill-by-hand": "a hand-written kill can hit another project's job; go through the supervisor or rig-board",
    "read-env": ".env holds secrets and is never echoed into a chat",
    "run-record": "run records are written only by the run itself and by run-identity",
    "wave-state": ".waves/_state belongs to sweep-supervisor and sweep-dispatch; use their commands",
    "board-lane": "lane files belong to rig-board; use its commands",
    "run-id-versions": "outputs exist, so this needs a run_id migration through run-identity",
    "experiments-during-wave": "during a supervised wave only its supervisor agent edits EXPERIMENTS.md",
}

RUN_RECORDS = {".run_id.json", ".run_config.json", "RUN_ID_MAP.json", "RUN_ID_HISTORY.json"}
OUTPUT_AREAS = ("checkpoints", "evaluations", "plots", "logs")
RUN_DATA_DIRS = {"checkpoints", "evaluations"}
LANE_RE = re.compile(r"/lanes/[^/]+/gpu[^/]*\.json$")
WAVE_RE = re.compile(r"\d{8}-\d{6}")  # rigsync.WAVE_RE
ASSIGNMENT_RE = re.compile(r"[A-Za-z_]\w*\+?=")
REMOTE_ARG_RE = re.compile(r"(?:[\w.-]+@)?[\w.-]+:|rsync://")
ENV_NAME_RE = re.compile(r"\.env(?:\.(?!example$|sample$|template$)[^/]+)?$")

SHELLS = {"sh", "bash", "zsh", "dash", "ksh"}
RESERVED = {"if", "then", "elif", "else", "do", "while", "until", "!", "{"}
# Wrappers that run the command after their own options; the value is the options taking a value.
WRAPPERS = {
    "sudo": {"-u", "-g", "-h", "-p", "-C", "-D", "-R", "-T"},
    "env": {"-u", "-C", "-S"},
    "nice": {"-n"},
    "ionice": {"-c", "-n", "-p"},
    "timeout": {"-s", "-k"},
    "stdbuf": {"-i", "-o", "-e"},
    "xargs": {"-I", "-n", "-P", "-L", "-d", "-a", "-s", "-E"},
    "time": {"-f", "-o"},
    "exec": {"-a"},
    "nohup": set(),
    "command": set(),
    "builtin": set(),
    "setsid": set(),
}
SSH_VALUE_OPTIONS = set("BbcDEeFIiJLlmOoPpQRSWw")
GIT_VALUE_OPTIONS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path", "--super-prefix", "--config-env"}
COMMIT_SHORT_VALUE = set("mFcCtS")  # in a short-option cluster, these end it (the rest is their value)
COMMIT_LONG_VALUE = {"--message", "--file", "--author", "--date", "--template", "--reuse-message", "--reedit-message", "--fixup", "--squash", "--trailer", "--cleanup", "--pathspec-from-file"}
RSYNC_VALUE_OPTIONS = {"-e", "--rsh", "--exclude", "--include", "--filter", "-f", "--exclude-from", "--include-from", "--files-from", "--rsync-path"}
SCP_VALUE_OPTIONS = {"-i", "-P", "-o", "-F", "-J", "-l", "-c", "-S", "-D", "-X"}
RUNNERS = re.compile(r"(python[\d.]*|uv|uvx|pipx)$")
# Programs that print what they read, so that a `.env` argument reaches the chat. `source .env` and
# `test -f .env` do not, on purpose.
ENV_READERS = {"cat", "tac", "less", "more", "head", "tail", "bat", "batcat", "nl", "cut", "sort", "uniq", "strings", "xxd", "hexdump", "od", "base64", "diff"}
ENV_PATTERN_READERS = {"grep", "egrep", "fgrep", "rg", "ag", "sed", "awk", "gawk", "mawk"}  # first positional is a pattern
MAX_DEPTH = 5


class Command:
    """One simple command: its words, what it redirects from and to, and any here-document bodies."""

    __slots__ = ("argv", "reads", "writes", "bodies")

    def __init__(self) -> None:
        self.argv: list[str] = []
        self.reads: list[str] = []
        self.writes: list[str] = []
        self.bodies: list[str] = []


class ShellParser:
    """Just enough of the shell grammar to tell a command from a word inside a quoted argument.

    Never raises on odd input: an unterminated quote ends at the end of the text. Command
    substitutions are parsed too, since they run, wherever they appear (a double-quoted commit
    message included). Here-document bodies are kept as text, never parsed as commands here."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.i = 0
        self.commands: list[Command] = []

    def parse(self) -> list[Command]:
        self.script(None)
        return self.commands

    def peek(self, offset: int = 0) -> str:
        position = self.i + offset
        return self.text[position] if position < len(self.text) else ""

    def nested(self, skip: int, closer: str) -> None:
        self.i += skip
        self.script(closer)

    def single_quoted(self) -> str:
        end = self.text.find("'", self.i + 1)
        end = len(self.text) if end < 0 else end
        value = self.text[self.i + 1:end]
        self.i = end + 1
        return value

    def double_quoted(self) -> str:
        self.i += 1
        out = []
        while self.i < len(self.text):
            c = self.text[self.i]
            if c == '"':
                self.i += 1
                break
            if c == "\\":
                out.append(self.peek(1))
                self.i += 2
            elif c == "$" and self.peek(1) == "(":
                self.nested(2, ")")
                out.append("$()")
            elif c == "`":
                self.nested(1, "`")
                out.append("$()")
            else:
                out.append(c)
                self.i += 1
        return "".join(out)

    def heredoc_bodies(self, pending: list[tuple[Command, str, bool]]) -> None:
        for command, delimiter, strip_tabs in pending:
            lines = []
            while self.i < len(self.text):
                end = self.text.find("\n", self.i)
                end = len(self.text) if end < 0 else end
                line = self.text[self.i:end]
                self.i = end + 1
                if (line.lstrip("\t") if strip_tabs else line) == delimiter:
                    break
                lines.append(line)
            command.bodies.append("\n".join(lines))
        pending.clear()

    def redirect_operator(self) -> str | None:
        """Consume the operator at the cursor; return what its target word is (None: process substitution)."""
        c, rest = self.peek(), self.text[self.i:self.i + 3]
        if rest.startswith(("<(", ">(")):
            self.nested(2, ")")
            return None
        if rest == "<<<":
            self.i += 3
            return "herestring"
        if rest == "<<-":
            self.i += 3
            return "heredoc-strip"
        if rest.startswith("<<"):
            self.i += 2
            return "heredoc"
        if c == ">":
            self.i += 2 if rest[1:2] in (">", "&", "|") else 1
            return "write"
        self.i += 2 if rest[1:2] in ("&", ">") else 1
        return "read"

    def script(self, closer: str | None) -> None:
        command = Command()
        word: list[str] | None = None
        target: str | None = None  # the next word is a redirect target of this kind
        pending: list[tuple[Command, str, bool]] = []

        def add(text: str) -> None:
            nonlocal word
            if word is None:
                word = []
            word.append(text)

        def end_word() -> None:
            nonlocal word, target
            if word is None:
                return
            value, word = "".join(word), None
            kind, target = target, None
            if kind is None:
                command.argv.append(value)
            elif kind == "read":
                command.reads.append(value)
            elif kind == "write":
                command.writes.append(value)
            elif kind == "herestring":
                command.bodies.append(value)
            else:
                pending.append((command, value, kind == "heredoc-strip"))

        def end_command() -> None:
            nonlocal command
            end_word()
            self.commands.append(command)
            command = Command()

        while self.i < len(self.text):
            c = self.text[self.i]
            if closer == "`" and c == "`":
                self.i += 1
                break
            if c == ")" and closer == ")":
                self.i += 1
                break
            if c in " \t":
                end_word()
                self.i += 1
            elif c == "\n":
                end_command()
                self.i += 1
                self.heredoc_bodies(pending)
            elif c == "\\":
                if self.peek(1) != "\n":
                    add(self.peek(1))
                self.i += 2
            elif c == "'":
                add(self.single_quoted())
            elif c == '"':
                add(self.double_quoted())
            elif c == "$" and self.peek(1) == "(":
                self.nested(2, ")")
                add("$()")
            elif c == "`":
                self.nested(1, "`")
                add("$()")
            elif c == "#" and word is None:
                end = self.text.find("\n", self.i)
                self.i = len(self.text) if end < 0 else end
            elif c == "&" and self.peek(1) == ">":
                end_word()
                self.i += 1
                target = self.redirect_operator()
            elif c in "<>":
                if word is not None and "".join(word).isdigit():
                    word = None  # a file descriptor number
                end_word()
                target = self.redirect_operator()
            elif c in ";|&":
                end_command()
                self.i += 1
            elif c == "(":
                end_command()
                self.nested(1, ")")
            elif c == ")":
                end_command()
                self.i += 1
            else:
                add(c)
                self.i += 1
        end_command()
        self.heredoc_bodies(pending)


def base(word: str) -> str:
    return word.rsplit("/", 1)[-1]


def unwrap(argv: list[str]) -> list[str]:
    """The command actually run: without leading keywords, assignments and `sudo`-like wrappers."""
    argv = list(argv)
    while argv:
        word = argv[0]
        if word in RESERVED or ASSIGNMENT_RE.match(word):
            argv.pop(0)
            continue
        wrapper = base(word)
        if wrapper not in WRAPPERS:
            break
        if wrapper == "command" and argv[1:2] and argv[1] in ("-v", "-V"):
            return []  # only looks a program up
        argv.pop(0)
        while argv and (argv[0].startswith("-") or (wrapper in ("env", "sudo") and ASSIGNMENT_RE.match(argv[0]))):
            option = argv.pop(0)
            if option in WRAPPERS[wrapper] and argv:
                argv.pop(0)
        if wrapper == "timeout" and argv:
            argv.pop(0)  # the duration
    return argv


def positional(args: list[str], value_options: set[str] = frozenset()) -> list[str]:
    out, i = [], 0
    while i < len(args):
        word = args[i]
        i += 1
        if word == "--":
            out.extend(args[i:])
            break
        if word.startswith("-") and word != "-":
            if word in value_options:
                i += 1
            continue
        out.append(word)
    return out


def is_env_file(word: str) -> bool:
    return bool(ENV_NAME_RE.fullmatch(base(word)))


def static_file_rule(path: str) -> str | None:
    """A rule that follows from the path alone, wherever the path is."""
    name, slashed = base(path), "/" + path
    if name in RUN_RECORDS or name.endswith(".offloaded.json"):
        return "run-record"
    if "/.waves/_state/" in slashed:
        return "wave-state"
    if LANE_RE.search(slashed):
        return "board-lane"
    return None


def is_run_data(path: str, scratch: tuple[str, ...], cwd: str) -> bool:
    if path.startswith(scratch) and not path.startswith(cwd.rstrip("/") + "/"):
        return False  # a throwaway copy somewhere else, not this project's data
    return any(part in RUN_DATA_DIRS or part.startswith("rid-") for part in path.split("/"))


def check_git(args: list[str]) -> list[str]:
    i = 0
    while i < len(args) and args[i].startswith("-"):
        i += 2 if args[i] in GIT_VALUE_OPTIONS else 1
    if i >= len(args):
        return []
    sub, rest = args[i], args[i + 1:]
    if sub == "push":
        for word in before_dashes(rest):
            if word in ("--force", "--force-with-lease", "--force-if-includes") or word.startswith("--force-with-lease=") or word.startswith("+") or (word.startswith("-") and not word.startswith("--") and "f" in word):
                return ["git-push-force"]
    elif sub == "reset" and "--hard" in before_dashes(rest):
        return ["git-reset-hard"]
    elif sub == "clean" and not any(word == "--dry-run" or (word.startswith("-") and not word.startswith("--") and "n" in word) for word in before_dashes(rest)):
        return ["git-clean"]
    elif sub == "stash" and not (rest and rest[0] in ("list", "show")):
        return ["git-stash"]
    elif sub == "commit" and commit_skips_hooks(rest):
        return ["git-commit-no-verify"]
    return []


def before_dashes(args: list[str]) -> list[str]:
    """Words before a bare `--`: what is after it is a path."""
    return args[:args.index("--")] if "--" in args else args


def commit_skips_hooks(args: list[str]) -> bool:
    i = 0
    while i < len(args):
        word = args[i]
        i += 1
        if word == "--":
            return False
        if word == "--no-verify":
            return True
        if word.startswith("--"):
            if word in COMMIT_LONG_VALUE:
                i += 1
        elif word.startswith("-"):
            for j, letter in enumerate(word[1:], start=1):
                if letter == "n":
                    return True
                if letter in COMMIT_SHORT_VALUE:
                    if j == len(word) - 1:
                        i += 1  # the value is the next word
                    break
    return False


def check_ssh(args: list[str], ctx: dict, command: Command, depth: int) -> list[str]:
    i = 0
    while i < len(args) and args[i].startswith("-"):
        option = args[i]
        i += 2 if len(option) == 2 and option[1] in SSH_VALUE_OPTIONS else 1
    payload = args[i + 1:]  # after the destination
    hits = check_command(" ".join(payload), ctx, depth + 1) if payload else []
    for body in command.bodies:
        hits += check_command(body, ctx, depth + 1)
    return ["ssh:" + hit.removeprefix("ssh:") for hit in hits]


def check_simple(command: Command, ctx: dict, depth: int) -> list[str]:
    hits = ["read-env" for path in command.reads if is_env_file(path)]
    hits += [rule for rule in map(static_file_rule, command.writes) if rule]
    argv = unwrap(command.argv)
    if not argv:
        return hits
    program, args = base(argv[0]), argv[1:]
    if program in SHELLS:
        flags = [i for i, word in enumerate(args) if word.startswith("-") and not word.startswith("--") and "c" in word]
        if flags and flags[0] + 1 < len(args):
            hits += check_command(args[flags[0] + 1], ctx, depth + 1)
        for body in command.bodies:
            hits += check_command(body, ctx, depth + 1)
    elif program == "eval":
        hits += check_command(" ".join(args), ctx, depth + 1)
    elif program == "ssh":
        hits += check_ssh(args, ctx, command, depth)
    elif program == "git":
        hits += check_git(args)
    elif program in ("rm", "mv", "rmdir", "unlink"):
        paths = positional(args)
        if any(is_run_data(path, ctx["scratch"], ctx["cwd"]) for path in paths):
            hits.append("rm-mv-run-data")
        hits += [rule for rule in map(static_file_rule, paths) if rule]
    elif program == "find":
        deletes = "-delete" in args or any(args[i] in ("-exec", "-execdir", "-ok") and base(args[i + 1]) == "rm" for i in range(len(args) - 1))
        roots = args[:next((i for i, word in enumerate(args) if word.startswith("-")), len(args))]
        if deletes and any(is_run_data(path, ctx["scratch"], ctx["cwd"]) for path in roots):
            hits.append("rm-mv-run-data")
    elif program in ("scp", "rsync"):
        options = SCP_VALUE_OPTIONS if program == "scp" else RSYNC_VALUE_OPTIONS
        if any(REMOTE_ARG_RE.match(word) for word in positional(args, options)):
            hits.append("remote-copy")
    elif program in ("scancel", "pkill", "killall"):
        hits.append("kill-by-hand")
    elif program == "kill":
        if not any(word in ("-0", "-l", "-L", "--list") for word in args):  # -0 only tests a pid
            hits.append("kill-by-hand")
    elif program in ENV_READERS:
        if any(is_env_file(path) for path in positional(args)):
            hits.append("read-env")
    elif program in ENV_PATTERN_READERS:
        words = positional(args)
        pattern_given = any(word in ("-e", "-f", "--regexp", "--file") or word.startswith(("--regexp=", "--file=")) for word in args)
        if any(is_env_file(path) for path in (words if pattern_given else words[1:])):
            hits.append("read-env")
    if program in ("runid", "runid.py") or RUNNERS.fullmatch(program):
        if "rollback" in args and "--force" in args and (program.startswith("runid") or any(base(word) in ("runid", "runid.py") for word in args)):
            hits.append("runid-rollback-force")
    return hits


def check_command(text: str, ctx: dict, depth: int = 0) -> list[str]:
    if depth > MAX_DEPTH:
        return []
    hits: list[str] = []
    for command in ShellParser(text).parse():
        hits += check_simple(command, ctx, depth)
    return hits


def supervised() -> bool:
    """A supervisor agent run. `SWEEP_SUPERVISOR_SESSION` is set only when the configured command
    takes `{session}`; `SWEEP_SUPERVISOR_MODEL` is always set (supervisor.py, run_agent)."""
    return bool(os.environ.get("SWEEP_SUPERVISOR_SESSION") or os.environ.get("SWEEP_SUPERVISOR_MODEL"))


def has_live_wave(path: Path) -> bool:
    for directory in path.parents:
        state = directory / ".waves" / "_state"
        if not state.is_dir():
            continue
        for queue_path in state.glob("*/queue.json"):
            if not WAVE_RE.fullmatch(queue_path.parent.name):
                continue
            try:
                queue = json.loads(queue_path.read_text())
            except (OSError, ValueError):
                continue
            if isinstance(queue, dict) and not queue.get("finished_at"):
                return True
        return False
    return False


def has_outputs(versions_path: Path) -> bool:
    """config/<exp>/RUN_ID_VERSIONS.json: does any output of <exp> exist? (<exp> may be nested.)"""
    parts = versions_path.parts
    if "config" not in parts:
        return False
    at = len(parts) - 1 - parts[::-1].index("config")
    root, experiment = Path(*parts[:at]), Path(*parts[at + 1:-1])
    if not experiment.parts:
        return False
    for area in OUTPUT_AREAS:
        directory = root / area / experiment
        if directory.is_dir() and any(not entry.name.startswith(".") for entry in directory.iterdir()):
            return True
    return False


def check_edit(path: str) -> list[str]:
    rule = static_file_rule(path)
    if rule:
        return [rule]
    name = base(path)
    if name == "RUN_ID_VERSIONS.json" and has_outputs(Path(path)):
        return ["run-id-versions"]
    if name == "EXPERIMENTS.md" and not supervised() and has_live_wave(Path(path)):
        return ["experiments-during-wave"]
    return []


def check_call(tool: str, tool_input: dict, ctx: dict) -> tuple[list[str], str]:
    """(rules hit, the input to log). Never the content of a Write or Edit: only its path."""
    if tool == "Bash":
        command = tool_input.get("command")
        if isinstance(command, str):
            return list(dict.fromkeys(check_command(command, ctx))), command
    elif tool in ("Edit", "Write"):
        path = tool_input.get("file_path")
        if isinstance(path, str):
            return check_edit(path), path
    elif tool == "Read":
        path = tool_input.get("file_path")
        if isinstance(path, str) and is_env_file(path):
            return ["read-env"], path
    return [], ""


def applies(cwd: str, path: str | None) -> bool:
    from orchestration_note import is_research_project  # lazily: tomllib costs time, and most calls never get here

    starts = [cwd, os.environ.get("CLAUDE_PROJECT_DIR"), os.path.dirname(path) if path else None]
    return any(is_research_project(Path(start)) for start in starts if start)


def state_dir() -> Path:
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "research-guard"


def log_path() -> Path:
    return state_dir() / "guard.jsonl"


def mode_file() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "research-guard" / "mode"


def current_mode() -> str:
    """RESEARCH_GUARD, else the mode file, else log-only. An unknown value means log-only."""
    mode = os.environ.get("RESEARCH_GUARD", "").strip().lower()
    if mode in MODES:
        return mode
    try:
        mode = mode_file().read_text().strip().lower()
    except OSError:
        return "log"
    return mode if mode in MODES else "log"


def append_log(entry: dict) -> None:
    path = log_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        if path.stat().st_size > MAX_LOG_BYTES:
            os.replace(path, path.with_name(path.name + ".1"))
    except OSError:
        pass
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(descriptor, (json.dumps(entry) + "\n").encode())
    finally:
        os.close(descriptor)


def hook() -> None:
    payload = json.load(sys.stdin)
    if payload.get("hook_event_name") != "PreToolUse":
        return
    tool, tool_input, cwd = payload.get("tool_name"), payload.get("tool_input"), payload.get("cwd")
    if not isinstance(tool_input, dict) or not isinstance(cwd, str):
        return
    scratch = tuple(p.rstrip("/") + "/" for p in ("/tmp", payload.get("scratchpad_dir")) if isinstance(p, str) and p)
    rules, logged_input = check_call(tool, tool_input, {"cwd": cwd, "scratch": scratch})
    if not rules or not applies(cwd, tool_input.get("file_path") if tool != "Bash" else None):
        return
    mode = current_mode()
    if mode == "off":
        return
    enforce = mode == "enforce"
    try:
        append_log({
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "decision": "denied" if enforce else "logged",
            "rules": rules,
            "tool": tool,
            "input": logged_input[:MAX_LOGGED_INPUT],
            "cwd": cwd,
            "session_id": payload.get("session_id"),
            "permission_mode": payload.get("permission_mode"),
            "agent_type": payload.get("agent_type"),
            "supervisor": supervised(),
        })
    except OSError:
        pass  # an unwritable log must not change the decision
    if enforce:
        why = "; ".join(f"{rule}: {RULES[rule.removeprefix('ssh:')]}" for rule in rules)
        reason = f"research-guard refused this. {why}. If it is really needed, ask the user to run it themselves."
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason}}))


def read_log(days: float | None, rule: str | None) -> list[dict]:
    from datetime import datetime, timedelta, timezone

    cutoff = datetime.now(timezone.utc) - timedelta(days=days) if days is not None else None
    entries = []
    path = log_path()
    for candidate in (path.with_name(path.name + ".1"), path):
        try:
            lines = candidate.read_text().splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                entry = json.loads(line)
                if cutoff and datetime.strptime(entry["ts"], "%Y-%m-%dT%H:%M:%S%z") < cutoff:
                    continue
            except (ValueError, KeyError, TypeError):
                continue
            if rule is None or rule in entry.get("rules", []):
                entries.append(entry)
    return entries


def report(days: float | None, rule: str | None, examples: int) -> int:
    entries = read_log(days, rule)
    print(f"log: {log_path()}")
    print(f"mode: {current_mode()}")
    if not entries:
        print("no entries" + (f" for rule {rule}" if rule else "") + (f" in the last {days:g} days" if days is not None else ""))
        return 0
    denied = sum(entry.get("decision") == "denied" for entry in entries)
    ticks = sum(bool(entry.get("supervisor")) for entry in entries)
    print(f"{len(entries)} entries, {entries[0].get('ts')} to {entries[-1].get('ts')}: {len(entries) - denied} logged, {denied} denied; {ticks} from supervisor agents, {len(entries) - ticks} from chats")
    by_rule: dict[str, list[dict]] = {}
    for entry in entries:
        for hit in entry.get("rules", []):
            by_rule.setdefault(hit, []).append(entry)
    for hit, hit_entries in sorted(by_rule.items(), key=lambda item: -len(item[1])):
        print(f"\n{hit}: {len(hit_entries)}  ({RULES.get(hit.removeprefix('ssh:'), 'unknown rule')})")
        seen = list(dict.fromkeys(" ".join(str(entry.get("input", "")).split()) for entry in reversed(hit_entries)))
        for sample in seen[:examples]:
            print(f"    {sample[:160]}")
        if len(seen) > examples:
            print(f"    ... and {len(seen) - examples} more distinct")
    return 0


def set_mode(mode: str | None) -> int:
    if mode is None:
        overridden = os.environ.get("RESEARCH_GUARD", "").strip().lower() in MODES
        print(current_mode() + (" (from RESEARCH_GUARD)" if overridden else f" (mode file: {mode_file()})"))
        return 0
    path = mode_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(mode + "\n")
    print(f"{mode} (written to {path}; applies to the next tool call of every session)")
    if os.environ.get("RESEARCH_GUARD", "").strip().lower() in MODES:
        print("note: RESEARCH_GUARD is set in this shell and wins over the file wherever it is set")
    return 0


def cli() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="guard.py",
        description="Log, or refuse, tool calls that break a research project's written rules. Run with no arguments it is the PreToolUse hook. It catches accidents and is not a security boundary.",
        epilog=f"Log: {log_path()}. Modes: log (default: record and let the call proceed), enforce (deny), off (nothing). The RESEARCH_GUARD variable wins over the mode file. More in hooks/README.md.",
    )
    sub = parser.add_subparsers(dest="action", required=True)
    report_parser = sub.add_parser("report", help="summarize the log per rule, with examples")
    report_parser.add_argument("--days", type=float, help="only the last N days")
    report_parser.add_argument("--rule", help="only this rule, for example git-stash or ssh:kill-by-hand")
    report_parser.add_argument("--examples", type=int, default=5, help="distinct examples per rule (default 5)")
    mode_parser = sub.add_parser("mode", help="print the mode, or set it")
    mode_parser.add_argument("mode", nargs="?", choices=MODES)
    args = parser.parse_args()
    if args.action == "report":
        return report(args.days, args.rule, args.examples)
    return set_mode(args.mode)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        sys.exit(cli())
    try:
        hook()
    except Exception:  # noqa: BLE001 - fail open, by design
        pass
    sys.exit(0)
