#!/usr/bin/env python3
"""agent-fleet-orca: start and resume a fleet coordinator workspace in Orca.

A *group* is one fleet: a level-0 coordinator chat running in its own top-level Orca workspace
named `l0-<group>`, plus one worker per item. This script keeps the group's state and makes the
Orca calls whose order matters; every subcommand prints ONE JSON object and exits 0 when ok,
1 when refused or failed with {"ok": false, "error", "message", "next"}.

    detect                                   -> where this chat runs (Orca? which workspace?)
    bootstrap --group G --brief FILE --agent A [--branch-prefix P] [--base-branch REF]
              [--model M] [--dry-run]        -> state + `worktree create --name l0-G --no-parent`
    resume-check                             -> in l0-G: adopt the workspace, rebind the Run
    adopt --group G [--brief FILE]           -> state for a hand-made l0-G workspace
    record-run [--run R]                     -> store the Run (default: the one bound here)
    record-worker --item NAME --dispatch D [--workspace W] [--agent A] [--status S]
    show                                     -> the state of this l0 workspace

The Orca CLI is $ORCA_CLI_COMMAND, else `orca-ide` on Linux (bare `orca` there is the GNOME
screen reader), else `orca`. State lives in $AGENT_FLEET_STATE_DIR, else
${XDG_STATE_HOME:-~/.local/state}/agent-fleet, as <repoId>/<group>/{brief.md,state.json,specs/},
where repoId is that of the workspace the cwd is in (`worktree current`), the repo the l0
workspace is created in; the terminal's ORCA_WORKTREE_ID may belong to another repo.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

GROUP_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
GROUP_MAX = 40
L0_PREFIX = "l0-"
CLI_TIMEOUT = 120
TAIL_CHARS = 800
STATE_VERSION = 1
ITEM_FIELDS = ("dispatch", "workspace", "agent", "status")


class FleetError(Exception):
    def __init__(self, code: str, message: str, next_step: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.next_step = next_step


# --- environment -------------------------------------------------------------------------------


def resolve_cli() -> list[str] | None:
    """The Orca CLI argv prefix, or None when it cannot be found."""
    configured = os.environ.get("ORCA_CLI_COMMAND", "").strip()
    if configured:
        argv = [configured] if os.path.exists(configured) else shlex.split(configured)
    elif sys.platform.startswith("linux"):
        argv = ["orca-ide"]  # never bare `orca` here: that is the GNOME screen reader
    else:
        argv = ["orca"]
    head = argv[0] if argv else ""
    if os.path.isabs(head):
        return argv if os.access(head, os.X_OK) else None
    found = shutil.which(head) if head else None
    return [found, *argv[1:]] if found else None


def orca_env() -> dict:
    return {
        "terminal_handle": os.environ.get("ORCA_TERMINAL_HANDLE") or None,
        "worktree_id": os.environ.get("ORCA_WORKTREE_ID") or None,
    }


def require_orca() -> list[str]:
    env = orca_env()
    cli = resolve_cli()
    if not (env["terminal_handle"] and env["worktree_id"] and cli):
        raise FleetError(
            "outside-orca",
            "This chat is not running inside an Orca terminal (ORCA_TERMINAL_HANDLE, "
            "ORCA_WORKTREE_ID or the Orca CLI is missing).",
            "Open the repository in Orca and run the agent CLI in an Orca terminal, then retry.",
        )
    return cli


def repo_id_of(worktree_id: str | None) -> str | None:
    if not worktree_id or "::" not in worktree_id:
        return None
    repo = worktree_id.split("::", 1)[0]
    return repo if repo and "/" not in repo and repo not in (".", "..") else None


# --- Orca CLI ----------------------------------------------------------------------------------


def run_orca(cli: list[str], *args: str) -> dict:
    """Run one Orca CLI command with --json-style output and return its `result`."""
    argv = [*cli, *args]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=CLI_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise FleetError(
            "orca-cli-failed",
            f"`{' '.join(args[:2])}` could not run: {exc}",
            "Check that Orca is running and the CLI works, then retry.",
        ) from exc
    tail = proc.stderr.strip()[-TAIL_CHARS:]
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        payload = None
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        detail = ""
        if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
            error = payload["error"]
            detail = f"{error.get('code', 'error')}: {error.get('message', '')}".strip()
        elif payload is None and proc.stdout.strip():
            detail = "non-JSON output: " + proc.stdout.strip()[-TAIL_CHARS:]
        parts = [f"`{' '.join(args[:2])}` failed (exit {proc.returncode})"]
        if detail:
            parts.append(detail)
        if tail:
            parts.append("stderr: " + tail)
        raise FleetError(
            "orca-cli-failed",
            "; ".join(parts),
            "Read the Orca error above, fix its cause, then retry.",
        )
    result = payload.get("result")
    return result if isinstance(result, dict) else {}


def current_workspace(cli: list[str]) -> dict:
    record = run_orca(cli, "worktree", "current", "--json").get("worktree")
    if not isinstance(record, dict) or not record.get("id"):
        raise FleetError(
            "orca-cli-failed",
            "`worktree current` returned no worktree record.",
            "Run the command from inside an Orca-managed worktree.",
        )
    return record


def current_run_id(cli: list[str]) -> str | None:
    run_id = run_orca(cli, "orchestration", "run-current", "--json").get("runId")
    return run_id if isinstance(run_id, str) and run_id else None


def workspace_summary(record: dict) -> dict:
    return {
        key: record.get(key)
        for key in ("id", "path", "displayName", "isMainWorktree", "parentWorktreeId", "branch")
    }


def workspace_name(record: dict) -> str:
    return record.get("displayName") or Path(record.get("path") or "").name


def l0_group(record: dict) -> str | None:
    """The group of an `l0-<group>` workspace, or None for any other workspace."""
    if record.get("isMainWorktree"):
        return None
    for name in (record.get("displayName") or "", Path(record.get("path") or "").name):
        if name.startswith(L0_PREFIX) and valid_group(name[len(L0_PREFIX) :]):
            return name[len(L0_PREFIX) :]
    return None


# --- state -------------------------------------------------------------------------------------


def valid_group(group: str) -> bool:
    return bool(GROUP_RE.fullmatch(group)) and len(group) <= GROUP_MAX


def check_group(group: str) -> str:
    if not valid_group(group):
        raise FleetError(
            "bad-group",
            f"Group {group!r} is not a lowercase kebab-case slug of at most {GROUP_MAX} characters.",
            "Pick a name such as `eval-sweep` and retry.",
        )
    return group


def state_root() -> Path:
    configured = os.environ.get("AGENT_FLEET_STATE_DIR")
    if configured:
        return Path(configured).expanduser()
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local/state")
    return Path(base).expanduser() / "agent-fleet"


def group_dir(repo_id: str, group: str) -> Path:
    return state_root() / repo_id / group


def workspace_repo(record: dict) -> str:
    """The repo id of the cwd's workspace: `worktree create` infers its repo from the cwd, which
    may belong to another repo than the terminal's ORCA_WORKTREE_ID."""
    repo = record.get("repoId") or repo_id_of(record.get("id"))
    if not repo or "/" in repo or repo in (".", ".."):
        raise FleetError(
            "orca-cli-failed",
            f"Workspace {record.get('id')!r} has no usable repo id.",
            "Run this from inside an Orca-managed repository checkout.",
        )
    return repo


def load_state(directory: Path) -> dict | None:
    path = directory / "state.json"
    if not path.is_file():
        return None
    try:
        state = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise FleetError(
            "bad-state",
            f"{path} is unreadable: {exc}",
            "Fix or remove that file by hand, then retry.",
        ) from exc
    if not isinstance(state, dict):
        raise FleetError("bad-state", f"{path} is not a JSON object.", "Fix or remove it, then retry.")
    state.setdefault("items", {})
    return state


def save_state(directory: Path, state: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "state.json"
    tmp = path.with_name(f".state.json.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, path)


def new_state(group: str, repo_id: str, workspace_id: str | None, path: str | None) -> dict:
    return {
        "version": STATE_VERSION,
        "group": group,
        "repo_id": repo_id,
        "workspace_id": workspace_id,
        "worktree_path": path,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "run_id": None,
        "items": {},
    }


def copy_brief(source: str, directory: Path) -> Path:
    src = Path(source).expanduser()
    if not src.is_file():
        raise FleetError("no-brief", f"Brief file {source!r} does not exist.", "Write the brief first, then retry.")
    dest = directory / "brief.md"
    if src.resolve() != dest.resolve():
        shutil.copyfile(src, dest)
    return dest


class L0:
    """The l0 workspace the cwd belongs to, with its group state."""

    def __init__(self, cli: list[str]) -> None:
        self.cli = cli
        self.env_id = orca_env()["worktree_id"]
        self.record = current_workspace(cli)
        group = l0_group(self.record)
        if group is None:
            raise FleetError(
                "not-l0-workspace",
                f"The current directory is in workspace {workspace_name(self.record)!r}, "
                "not an `l0-<group>` coordinator workspace.",
                "Bootstrap a group from the main checkout, or open a terminal in the group's "
                "l0-<group> workspace and run the command there.",
            )
        self.group = group
        self.dir = group_dir(workspace_repo(self.record), group)
        self.state = load_state(self.dir)

    def require_state(self) -> dict:
        if self.state is None:
            raise FleetError(
                "no-state",
                f"No fleet state for group {self.group!r} at {self.dir}.",
                f"Run `bootstrap --group {self.group}` from the main checkout, or "
                f"`adopt --group {self.group}` here if you made this workspace by hand.",
            )
        return self.state

    def wrong_terminal(self) -> FleetError:
        return FleetError(
            "wrong-terminal",
            "This chat runs in a terminal of another Orca workspace "
            f"({self.env_id}), not of l0-{self.group}.",
            f"Open a terminal tab in workspace l0-{self.group} and resume the chat there.",
        )

    def require_terminal(self) -> dict:
        state = self.require_state()
        if state.get("workspace_id") is None:
            raise FleetError(
                "not-adopted",
                f"Group {self.group!r} has no workspace recorded yet.",
                "Run `resume-check` in this workspace first.",
            )
        if state["workspace_id"] != self.env_id:
            raise self.wrong_terminal()
        return state


# --- subcommands -------------------------------------------------------------------------------


def cmd_detect(_: argparse.Namespace) -> dict:
    env = orca_env()
    cli = resolve_cli()
    in_orca = bool(env["terminal_handle"] and env["worktree_id"] and cli)
    out = {
        "ok": True,
        "in_orca": in_orca,
        "orca_cli": " ".join(cli) if cli else None,
        **env,
        "workspace": None,
        "role_hint": "outside-orca",
        "group": None,
        "state_dir": None,
        "state": None,
    }
    if not in_orca:
        return out
    try:
        record = current_workspace(cli)
    except FleetError as exc:
        out["role_hint"] = "other-workspace"
        out["workspace_error"] = exc.message
        return out
    out["workspace"] = workspace_summary(record)
    group = l0_group(record)
    if record.get("isMainWorktree"):
        out["role_hint"] = "main-checkout"
    elif group is None:
        out["role_hint"] = "other-workspace"
    else:
        out["role_hint"] = "l0-workspace"
        out["group"] = group
        directory = group_dir(workspace_repo(record), group)
        out["state_dir"] = str(directory)
        out["state"] = load_state(directory)
    return out


def bootstrap_prompt(group: str, brief: Path) -> str:
    return (
        f"Use the agent-fleet skill: you are level 0 (coordinator) of fleet group {group}. "
        f"Your brief is {brief}. Start with the agent-fleet-orca resume check."
    )


def created_worktree(result: dict) -> tuple[str | None, str | None]:
    """The new worktree's (id, path) from a `worktree create --json` result."""
    record = result.get("worktree")
    worktree_id = record.get("id") if isinstance(record, dict) else None
    path = record.get("path") if isinstance(record, dict) else None
    if not worktree_id:
        launch = result.get("launch")
        worktree_id = launch.get("worktreeId") if isinstance(launch, dict) else None
    if worktree_id and not path and "::" in worktree_id:
        path = worktree_id.split("::", 1)[1]
    return worktree_id or None, path or None


def cmd_bootstrap(args: argparse.Namespace) -> dict:
    cli = require_orca()
    group = check_group(args.group)
    record = current_workspace(cli)
    repo = workspace_repo(record)
    directory = group_dir(repo, group)
    existing = load_state(directory)
    if existing:
        where = existing.get("workspace_id") or f"l0-{group} (its id was not recorded yet)"
        raise FleetError(
            "group-exists",
            f"Group {group!r} already has workspace {where}.",
            f"Resume the chat in workspace l0-{group} and run resume-check, or pick another group name.",
        )
    if not Path(args.brief).expanduser().is_file():
        raise FleetError("no-brief", f"Brief file {args.brief!r} does not exist.", "Write the brief first, then retry.")
    if not args.agent:
        raise FleetError("usage", "--agent must not be empty.", "Pass the agent id to launch, e.g. --agent claude.")

    name = L0_PREFIX + group
    brief = (directory / "brief.md").absolute()
    prompt = bootstrap_prompt(group, brief)
    argv = ["worktree", "create", "--name", name, "--no-parent", "--repo", f"id:{repo}"]
    argv += ["--agent", args.agent, "--prompt", prompt, "--activate", "--json"]
    if args.branch_prefix:
        argv += ["--branch", f"{args.branch_prefix.rstrip('/')}/{name}"]
    if args.base_branch:
        argv += ["--base-branch", args.base_branch]
    if args.model:
        argv += ["--model", args.model]
    state = new_state(group, repo, None, None)
    if args.dry_run:
        return {"ok": True, "dry_run": True, "argv": [*cli, *argv], "state_dir": str(directory), "state": state}

    made = not directory.exists()
    try:
        directory.mkdir(parents=True, exist_ok=True)
        copy_brief(args.brief, directory)
        (directory / "specs").mkdir(exist_ok=True)
        if existing:
            state["items"] = existing.get("items", {})
        save_state(directory, state)
        result = run_orca(cli, *argv)
    except BaseException:
        if made:
            shutil.rmtree(directory, ignore_errors=True)
        raise
    worktree_id, path = created_worktree(result)
    state["workspace_id"] = worktree_id
    state["worktree_path"] = path
    save_state(directory, state)
    out = {"ok": True, "group": group, "workspace_id": worktree_id, "worktree_path": path, "state_dir": str(directory)}
    if not worktree_id:
        out["warning"] = (
            "Orca created the workspace but its id was not in the reply; `resume-check` in "
            f"l0-{group} adopts it."
        )
    return out


def cmd_resume_check(_: argparse.Namespace) -> dict:
    l0 = L0(require_orca())
    state = l0.require_state()
    adopted = False
    if state.get("workspace_id") is None:
        if l0.record.get("id") != l0.env_id:
            raise l0.wrong_terminal()
        state["workspace_id"] = l0.env_id
        state["worktree_path"] = l0.record.get("path")
        save_state(l0.dir, state)
        adopted = True
    elif state["workspace_id"] != l0.env_id:
        raise l0.wrong_terminal()

    stored = state.get("run_id")
    current = current_run_id(l0.cli)
    rebound = False
    if stored and current is None:
        try:
            run_orca(l0.cli, "orchestration", "run-use", "--id", stored, "--json")
        except FleetError as exc:
            raise FleetError(
                "run-use-failed",
                f"Could not rebind this terminal to Run {stored}: {exc.message}",
                "Check the Run with `orchestration run-show`/`run-list`; if it ended, start a new "
                "one and store it with `record-run --run <id>`.",
            ) from exc
        rebound = True
    elif stored and current != stored:
        raise FleetError(
            "bound-to-other-run",
            f"This terminal is bound to Run {current}, but group {l0.group!r} coordinates Run {stored}.",
            "Ask the user which Run this chat should coordinate; to switch the group, run "
            f"`record-run --run {current}`.",
        )
    elif not stored and current:
        state["run_id"] = current
        save_state(l0.dir, state)
    return {
        "ok": True,
        "group": l0.group,
        "run_id": state.get("run_id"),
        "rebound": rebound,
        "adopted": adopted,
        "state_dir": str(l0.dir),
        "workspace_id": state["workspace_id"],
    }


def cmd_adopt(args: argparse.Namespace) -> dict:
    cli = require_orca()
    group = check_group(args.group)
    env_id = orca_env()["worktree_id"]
    record = current_workspace(cli)
    repo = workspace_repo(record)
    if record.get("isMainWorktree"):
        raise FleetError(
            "main-checkout",
            "The current workspace is the repository's main checkout, not a coordinator workspace.",
            f"Use `bootstrap --group {group}` to create l0-{group}, or run adopt inside it.",
        )
    if l0_group(record) != group:
        raise FleetError(
            "not-l0-workspace",
            f"The current workspace is {workspace_name(record)!r}, not l0-{group}.",
            f"Run adopt from a terminal of workspace l0-{group}.",
        )
    if record.get("id") != env_id:
        raise FleetError(
            "wrong-terminal",
            f"This chat runs in a terminal of another Orca workspace ({env_id}), not of l0-{group}.",
            f"Open a terminal tab in workspace l0-{group} and resume the chat there.",
        )
    directory = group_dir(repo, group)
    state = load_state(directory)
    adopted = False
    if state is None:
        state = new_state(group, repo, env_id, record.get("path"))
        adopted = True
    elif state.get("workspace_id") is None:
        state["workspace_id"] = env_id
        state["worktree_path"] = record.get("path")
        adopted = True
    elif state["workspace_id"] != env_id:
        raise FleetError(
            "group-exists",
            f"Group {group!r} already belongs to workspace {state['workspace_id']}.",
            "Resume the chat in that workspace, or rename this one and pick another group name.",
        )
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "specs").mkdir(exist_ok=True)
    if args.brief:
        copy_brief(args.brief, directory)
    save_state(directory, state)
    return {"ok": True, "group": group, "adopted": adopted, "state_dir": str(directory), "workspace_id": env_id}


def cmd_record_run(args: argparse.Namespace) -> dict:
    l0 = L0(require_orca())
    state = l0.require_terminal()
    run_id = args.run or current_run_id(l0.cli)
    if not run_id:
        raise FleetError(
            "no-run",
            "No Run is bound to this terminal and none was given.",
            "Create one with `orchestration run-create`, or pass --run <id>.",
        )
    state["run_id"] = run_id
    save_state(l0.dir, state)
    return {"ok": True, "group": l0.group, "run_id": run_id, "state_dir": str(l0.dir)}


def cmd_record_worker(args: argparse.Namespace) -> dict:
    if not args.item.strip():
        raise FleetError("usage", "--item must not be empty.", "Pass the item name.")
    l0 = L0(require_orca())
    state = l0.require_terminal()
    item = state["items"].setdefault(args.item, {})
    for field in ITEM_FIELDS:
        value = getattr(args, field)
        if value is not None:
            item[field] = value
    save_state(l0.dir, state)
    return {"ok": True, "group": l0.group, "item": args.item, "record": item}


def cmd_show(_: argparse.Namespace) -> dict:
    l0 = L0(require_orca())
    return {"ok": True, "group": l0.group, "state_dir": str(l0.dir), "state": l0.require_state()}


# --- entry point -------------------------------------------------------------------------------


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # type: ignore[override]
        raise FleetError("usage", f"{self.prog}: {message}", "Fix the arguments; see --help.")


def build_parser() -> Parser:
    parser = Parser(prog="orca_fleet.py", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True, parser_class=Parser)
    sub.add_parser("detect").set_defaults(func=cmd_detect)

    boot = sub.add_parser("bootstrap")
    boot.add_argument("--group", required=True)
    boot.add_argument("--brief", required=True)
    boot.add_argument("--agent", required=True)
    boot.add_argument("--branch-prefix")
    boot.add_argument("--base-branch")
    boot.add_argument("--model")
    boot.add_argument("--dry-run", action="store_true")
    boot.set_defaults(func=cmd_bootstrap)

    sub.add_parser("resume-check").set_defaults(func=cmd_resume_check)

    adopt = sub.add_parser("adopt")
    adopt.add_argument("--group", required=True)
    adopt.add_argument("--brief")
    adopt.set_defaults(func=cmd_adopt)

    record_run = sub.add_parser("record-run")
    record_run.add_argument("--run")
    record_run.set_defaults(func=cmd_record_run)

    worker = sub.add_parser("record-worker")
    worker.add_argument("--item", required=True)
    worker.add_argument("--dispatch", required=True)
    worker.add_argument("--workspace")
    worker.add_argument("--agent")
    worker.add_argument("--status")
    worker.set_defaults(func=cmd_record_worker)

    sub.add_parser("show").set_defaults(func=cmd_show)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        out = args.func(args)
    except FleetError as exc:
        out = {"ok": False, "error": exc.code, "message": exc.message, "next": exc.next_step}
    print(json.dumps(out, indent=2))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
