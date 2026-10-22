#!/usr/bin/env python3
"""Hub-side supervisor for Research 2.0 waves: pull queue, reflex recovery, table, agent wake-ups.

A wave's runs live in one ordered queue on the hub. Every lane (one GPU set on one rig) keeps a
shallow local buffer that its own shell loop claims from atomically; this service tops the buffers
up, so a fast card simply takes more runs and a wrong initial estimate corrects itself. Rigs are
only ever reached hub -> rig, never the other way.

The service owns what must keep happening while no agent is awake (feeding, relaunching a dead
lane, offload, the fleet board, the status table). Judgment (why did it fail, move this run, fetch
that dataset) belongs to a fresh headless agent that the service wakes on a timer and on events.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import importlib.util
import io
import json
import os
import re
import shlex
import statistics
import subprocess
import sys
import threading
import time
import tomllib
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path


def _load_sibling(skill: str, script: str, alias: str):
    path = Path(__file__).resolve().parents[2] / skill / "scripts" / script
    spec = importlib.util.spec_from_file_location(alias, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load sibling helper: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


rigsync = _load_sibling("rig-sync", "rigsync.py", "sweep_supervisor_rigsync")
board = _load_sibling("rig-board", "board.py", "sweep_supervisor_board")

RIGSYNC_SCRIPT = Path(rigsync.__file__)
ENVSYNC_SCRIPT = Path(__file__).resolve().parents[2] / "environment-sync" / "scripts" / "envsync.py"


class SupervisorError(RuntimeError):
    pass


SCHEMA_VERSION = 1
STATE_DIR = "_state"  # under .waves/, already ignored by every supported project; never a wave id
BUFFER_DEPTH = 2  # the run in flight plus one waiting: shallow, so late data can still re-route
DEFAULT_CYCLE_EVERY_S = 60
DEFAULT_TICK_EVERY_S = 1800  # a safety net: every event that needs judgment wakes the agent at once
DEFAULT_AGENT_TIMEOUT_S = 900
DEFAULT_FOLLOWUP_TIMEOUT_S = 3 * 3600
MAX_FIX_DEPTH = 2  # automatic fix-and-redispatch rounds in one chain before a person looks
CERT_WARN_S = 3600  # an SSH certificate this close to expiry is announced once
FOLLOWUP_KINDS = ("analyze", "fix")
MIN_CYCLE_EVERY_S = 10
DOWN_AFTER_FAILURES = 3
HEARTBEAT_STALE_S = 180
SERVICE_SILENT_S = 10 * 60  # the service cycles every wave each minute; this long without a snapshot means it is down
WATCH_POLL_S = 20
WATCH_TIMEOUT_S = 6 * 3600
WATCH_SETTLE_S = 3600  # longest watch waits on an ended wave's closing agent tick, where a `fix` follow-up is queued
BOARD_REFRESH_EVERY_S = 600
RESERVED_EXITS = {86: "source drift", 87: "environment drift", 88: "insufficient storage"}
LANE_STOPPING = {86, 87}
WALLTIME_MARGIN = 1.5
WALLTIME_CAP_S = 24 * 3600
WALLTIME_STEP_S = 15 * 60
EFFORT_VALUES = ("low", "medium", "high", "xhigh", "max")
RUN_STATES = ("queued", "assigned", "running", "done", "failed", "excluded")
TERMINAL_STATES = ("done", "failed", "excluded")
SLURM_MACHINE_FAULTS = ("NODE_FAIL", "PREEMPTED", "BOOT_FAIL")
GPU_SET_RE = re.compile(r"^\d+(?:,\d+)*$")
TAG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")
URGENT_EVENTS = frozenset(
    {
        "run-failed",
        "lane-stopped",
        "rig-down",
        "rig-up",
        "lane-relaunched",
        "recovery-blocked",
        "job-failed",
        "job-timeout",
        "job-interrupted",
        "run-lost",
        "answer",
        "wave-terminal",
        "offload-failed",
        "collect-failed",
        "submit-failed",
        "heartbeat-stale",
        "lane-idle-tail",
    }
)
# What reaches the user's phone (`notify_command`) without any chat open. `ask` always does too.
NOTIFY_EVENTS = frozenset({"wave-terminal", "rig-down", "recovery-blocked", "cert-expiring", "cert-expired", "submit-failed"})
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


def now() -> datetime:
    return datetime.now().astimezone()


def iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def parse_iso(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.astimezone()


# ───────────────────────────── settings ─────────────────────────────


@dataclass(frozen=True)
class AgentProfile:
    command: tuple[str, ...]
    model: str
    effort: str
    resume: tuple[str, ...] = ()  # continues a recorded session with a new prompt: the phone talks to the agent that wrote
    session_regex: str | None = None  # finds the session id in the log of a `command` that cannot be given one


@dataclass(frozen=True)
class Settings:
    registry_path: Path
    cycle_every: int
    tick_every: int
    agent_timeout: int
    agent: AgentProfile | None
    notify_command: tuple[str, ...]
    followup_timeout: int = DEFAULT_FOLLOWUP_TIMEOUT_S
    ssh_certs: tuple[tuple[str, Path], ...] = ()  # (rig, certificate) pairs from `[machines.<rig>] ssh_cert`


def _string_list(value: object, where: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or not all(isinstance(v, str) and v for v in value):
        raise SupervisorError(f"{where} must be a non-empty list of strings")
    return tuple(value)


# Options a headless agent CLI declares variadic (Claude Code's `--allowedTools <tools...>` and
# kin): each swallows every following bare argument, so a `{prompt}` placed after one is consumed
# as a value and the agent starts with no prompt at all.
VARIADIC_AGENT_OPTIONS = frozenset({
    "--allowedTools", "--allowed-tools", "--disallowedTools", "--disallowed-tools",
    "--tools", "--add-dir", "--mcp-config", "--betas", "--plugin-dir", "--file",
})


def _prompt_swallowed_by(command: tuple[str, ...]) -> str | None:
    """The variadic option that would consume the `{prompt}` argument, if any."""
    swallowing = None
    for part in command:
        if part == "--":
            return None
        if part.startswith("-"):
            name = part.split("=", 1)[0]
            swallowing = name if name in VARIADIC_AGENT_OPTIONS else None
        elif "{prompt}" in part:
            return swallowing
    return None


def _agent_command(raw_agent: dict, key: str, registry_path: Path, required: tuple[str, ...]) -> tuple[str, ...]:
    command = _string_list(raw_agent.get(key), f"{registry_path}: supervisor.agent.{key}")
    for name in required:
        if not any("{" + name + "}" in part for part in command):
            raise SupervisorError(f"{registry_path}: supervisor.agent.{key} must contain a {{{name}}} placeholder")
    swallowed_by = _prompt_swallowed_by(command)
    if swallowed_by:
        raise SupervisorError(
            f"{registry_path}: supervisor.agent.{key} puts {{prompt}} after the variadic option "
            f"{swallowed_by}, which would consume it as a value; place {{prompt}} before it "
            '(e.g. right after "-p")'
        )
    return command


def load_settings(registry_path: Path) -> Settings:
    """Read `[supervisor]` from the machine registry. The agent profile is never defaulted."""
    try:
        data = tomllib.loads(registry_path.read_text())
    except OSError as exc:
        raise SupervisorError(f"missing registry: {registry_path}") from exc
    table = data.get("supervisor", {})
    if not isinstance(table, dict):
        raise SupervisorError(f"{registry_path}: [supervisor] must be a table")
    cycle_every = table.get("cycle_every", DEFAULT_CYCLE_EVERY_S)
    tick_every = table.get("tick_every", DEFAULT_TICK_EVERY_S)
    agent_timeout = table.get("agent_timeout", DEFAULT_AGENT_TIMEOUT_S)
    followup_timeout = table.get("followup_timeout", DEFAULT_FOLLOWUP_TIMEOUT_S)
    for name, value in (
        ("cycle_every", cycle_every), ("tick_every", tick_every), ("agent_timeout", agent_timeout), ("followup_timeout", followup_timeout),
    ):
        if not isinstance(value, int) or isinstance(value, bool) or value < MIN_CYCLE_EVERY_S:
            raise SupervisorError(f"{registry_path}: supervisor.{name} must be an integer >= {MIN_CYCLE_EVERY_S}")
    agent = None
    raw_agent = table.get("agent")
    if raw_agent is not None:
        if not isinstance(raw_agent, dict):
            raise SupervisorError(f"{registry_path}: [supervisor.agent] must be a table")
        command = _agent_command(raw_agent, "command", registry_path, ("prompt",))
        resume = _agent_command(raw_agent, "resume_command", registry_path, ("prompt", "session")) if "resume_command" in raw_agent else ()
        session_regex = raw_agent.get("session_regex")
        if session_regex is not None:
            try:
                if not isinstance(session_regex, str) or re.compile(session_regex).groups != 1:
                    raise re.error("needs exactly one group")
            except re.error as exc:
                raise SupervisorError(f"{registry_path}: supervisor.agent.session_regex must be a regex with one group (the id): {exc}") from None
        if resume and not session_regex and not any("{session}" in part for part in command):
            raise SupervisorError(
                f"{registry_path}: supervisor.agent.resume_command needs the session of every run: put {{session}} in "
                "command (an executable that takes a preset id) or declare session_regex (one that prints its id)"
            )
        model = raw_agent.get("model")
        effort = raw_agent.get("effort")
        if not isinstance(model, str) or not model:
            raise SupervisorError(f"{registry_path}: supervisor.agent.model is required; it is never defaulted")
        if effort not in EFFORT_VALUES:
            raise SupervisorError(f"{registry_path}: supervisor.agent.effort must be one of {', '.join(EFFORT_VALUES)}")
        agent = AgentProfile(command, model, effort, resume, session_regex)
    notify = table.get("notify_command")
    notify_command = _string_list(notify, f"{registry_path}: supervisor.notify_command") if notify is not None else ()
    if notify_command and not any("{text}" in part for part in notify_command):
        raise SupervisorError(f"{registry_path}: supervisor.notify_command must contain a {{text}} placeholder")
    certs = []
    machines = data.get("machines", {})
    for rig, entry in (machines.items() if isinstance(machines, dict) else ()):
        cert = entry.get("ssh_cert") if isinstance(entry, dict) else None
        if cert is not None:
            if not isinstance(cert, str) or not cert:
                raise SupervisorError(f"{registry_path}: machines.{rig}.ssh_cert must be a path")
            certs.append((rig, Path(cert).expanduser()))
    return Settings(registry_path, cycle_every, tick_every, agent_timeout, agent, notify_command, followup_timeout, tuple(certs))


def require_agent(settings: Settings) -> AgentProfile:
    if settings.agent is None:
        raise SupervisorError(
            f"{settings.registry_path}: [supervisor.agent] (command, model, effort) is not declared; "
            "a wave is never supervised by an undeclared agent profile"
        )
    return settings.agent


# ───────────────────────────── wave state ─────────────────────────────


class Wave:
    """One wave's state directory on the hub: `<project>/.waves/_state/<wave_id>/`."""

    def __init__(self, project_root: Path, wave_id: str) -> None:
        if not rigsync.WAVE_RE.fullmatch(wave_id):
            raise SupervisorError(f"wave must be YYYYMMDD-HHMMSS; got {wave_id!r}")
        self.project_root = project_root.resolve()
        self.wave_id = wave_id
        self.dir = self.project_root / rigsync.WAVES_DIR / STATE_DIR / wave_id

    @property
    def queue_path(self) -> Path:
        return self.dir / "queue.json"

    @property
    def ledger_path(self) -> Path:
        return self.dir / "ledger.jsonl"

    @property
    def snapshot_path(self) -> Path:
        return self.dir / "snapshot.json"

    @property
    def table_path(self) -> Path:
        return self.dir / "table.md"

    @property
    def decisions_path(self) -> Path:
        return self.dir / "decisions.md"

    @property
    def questions_path(self) -> Path:
        return self.dir / "questions.json"

    @property
    def runtime_path(self) -> Path:
        return self.dir / "runtime.json"

    @property
    def agent_logs(self) -> Path:
        return self.dir / "agent-logs"

    def load(self) -> dict:
        queue = board.read_json(self.queue_path)
        if queue is None:
            raise SupervisorError(f"wave {self.wave_id} is not registered here: {self.queue_path}")
        if queue.get("schema_version") != SCHEMA_VERSION:
            raise SupervisorError(f"{self.queue_path}: unsupported schema_version {queue.get('schema_version')!r}")
        return queue

    def save(self, queue: dict) -> None:
        board.write_json(self.queue_path, queue)

    def log(self, event: str, **fields: object) -> dict:
        record = {"at": iso(now()), "event": event, **fields}
        self.dir.mkdir(parents=True, exist_ok=True)
        with self.ledger_path.open("a") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
        return record

    def ledger(self) -> list[dict]:
        try:
            lines = self.ledger_path.read_text().splitlines()
        except OSError:
            return []
        records = []
        for line in lines:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                records.append(record)
        return records

    def runtime(self) -> dict:
        return board.read_json(self.runtime_path) or {}

    def save_runtime(self, runtime: dict) -> None:
        board.write_json(self.runtime_path, runtime)

    def questions(self) -> list[dict]:
        payload = board.read_json(self.questions_path) or {}
        items = payload.get("questions", [])
        return items if isinstance(items, list) else []

    def save_questions(self, items: list[dict]) -> None:
        board.write_json(self.questions_path, {"questions": items})


class WaveLock:
    """Serializes the service, the agent's CLI calls and the chat on one wave."""

    def __init__(self, wave: Wave) -> None:
        wave.dir.mkdir(parents=True, exist_ok=True)
        self.path = wave.dir / ".lock"

    def __enter__(self) -> "WaveLock":
        self.handle = self.path.open("a+")
        fcntl.flock(self.handle, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc: object) -> None:
        fcntl.flock(self.handle, fcntl.LOCK_UN)
        self.handle.close()


def run_token(run_id: str) -> str:
    return hashlib.sha1(run_id.encode()).hexdigest()[:12]


def index_dir(board_config) -> Path:
    return board_config.root / "supervisor"


def index_path(board_config, project: str, wave_id: str) -> Path:
    return index_dir(board_config) / f"{project}__{wave_id}.json"


def finished_dir(board_config) -> Path:
    """Pointers to finished waves, so the rig-board viewer can still show them once nothing supervises them."""
    return index_dir(board_config) / "finished"


FINISHED_KEEP_S = 14 * 86400


def record_finished(board_config, wave: Wave, queue: dict) -> None:
    directory = finished_dir(board_config)
    board.write_json(directory / f"{queue['project']}__{wave.wave_id}.json", {
        "project_root": str(wave.project_root), "wave_id": wave.wave_id, "experiment": queue["experiment"],
        "registered_at": queue.get("registered_at"), "finished_at": queue.get("finished_at") or iso(now()),
    })
    cutoff = now() - timedelta(seconds=FINISHED_KEEP_S)
    for path in directory.glob("*.json"):
        moment = parse_iso((board.read_json(path) or {}).get("finished_at"))
        if moment is not None and moment < cutoff:
            path.unlink(missing_ok=True)


def active_waves(board_config) -> list[Wave]:
    waves = []
    directory = index_dir(board_config)
    if not directory.is_dir():
        return waves
    for path in sorted(directory.glob("*.json")):
        pointer = board.read_json(path)
        if not pointer:
            continue
        try:
            waves.append(Wave(Path(str(pointer.get("project_root"))), str(pointer.get("wave_id"))))
        except SupervisorError:
            continue
    return waves


# ───────────────────────────── registration ─────────────────────────────


def _require(mapping: dict, key: str, kind: type, where: str):
    value = mapping.get(key)
    if not isinstance(value, kind) or isinstance(value, bool) or (kind is str and not value):
        raise SupervisorError(f"{where}: `{key}` must be a {kind.__name__}")
    return value


def _relative(value: object, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise SupervisorError(f"{where} must be a project-relative path")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise SupervisorError(f"{where} must be a project-relative path without `..`: {value!r}")
    return value


def gpu_grants(manifest: dict) -> dict[str, dict]:
    """A user grant of cards beyond a shared rig's owned set: per wave, quoted verbatim, never inferred."""
    grants_in = manifest.get("gpu_grants", {})
    if not isinstance(grants_in, dict):
        raise SupervisorError("manifest: `gpu_grants` must be a table of rig -> grant")
    grants: dict[str, dict] = {}
    for rig, grant in grants_in.items():
        spot = f"manifest: gpu_grants.{rig}"
        if not isinstance(grant, dict):
            raise SupervisorError(f"{spot} must be a table")
        gpus = grant.get("gpus")
        if not isinstance(gpus, list) or not gpus or not all(isinstance(g, int) and not isinstance(g, bool) and g >= 0 for g in gpus):
            raise SupervisorError(f"{spot}.gpus must list GPU indices")
        words = grant.get("user_words")
        if not isinstance(words, str) or not words.strip():
            raise SupervisorError(f"{spot}.user_words must quote the user's grant verbatim")
        grants[rig] = {"gpus": sorted(set(gpus)), "user_words": words.strip(), "date": _require(grant, "date", str, spot)}
    return grants


def build_queue(manifest: dict, project_root: Path, config, settings: Settings, board_config=None) -> dict:
    """Validate a dispatch manifest and turn it into the wave's queue. Nothing here is inferred."""
    where = "manifest"
    wave_id = _require(manifest, "wave_id", str, where)
    revision = _require(manifest, "revision", str, where)
    rigsync.validate_wave_revision(wave_id, revision)
    agent = require_agent(settings)
    pool_in = _require(manifest, "pool", dict, where)
    if not pool_in:
        raise SupervisorError("manifest: `pool` names no rig")
    weights_in = _require(manifest, "weights", dict, where)
    grants = gpu_grants(manifest)
    pool: dict[str, dict] = {}
    for rig, entry in pool_in.items():
        machine = config.machines.get(rig)
        if machine is None:
            raise SupervisorError(f"manifest: pool rig {rig!r} is not declared in sync.toml")
        if not isinstance(entry, dict):
            raise SupervisorError(f"manifest: pool.{rig} must be a table")
        weight = weights_in.get(rig)
        if not isinstance(weight, (int, float)) or isinstance(weight, bool) or weight <= 0:
            raise SupervisorError(f"manifest: weights.{rig} must be a positive number")
        if machine.gpus_in_job:
            max_jobs = _require(entry, "max_jobs", int, f"manifest: pool.{rig}")
            # no core_hours_cap means no cap: the cluster spends from its account until the job cap
            cap = entry.get("core_hours_cap")
            if max_jobs < 1 or (cap is not None and (not isinstance(cap, (int, float)) or isinstance(cap, bool) or cap <= 0)):
                raise SupervisorError(f"manifest: pool.{rig} needs max_jobs >= 1 and, if set, a positive core_hours_cap")
            pool[rig] = {
                "kind": "slurm",
                "max_jobs": max_jobs,
                "core_hours_cap": None if cap is None else float(cap),
                "cores_per_job": int(entry.get("cores_per_job", 8)),
                "default_walltime_s": int(_require(entry, "default_walltime_s", int, f"manifest: pool.{rig}")),
            }
        else:
            lanes = entry.get("lanes")
            if not isinstance(lanes, list) or not lanes or not all(isinstance(g, str) and GPU_SET_RE.fullmatch(g) for g in lanes):
                raise SupervisorError(f"manifest: pool.{rig}.lanes must list GPU sets such as \"0\" or \"0,1\"")
            seen: set[int] = set()
            for gpu in lanes:
                indices = board.gpu_indices(gpu)
                if indices & seen:
                    raise SupervisorError(f"manifest: pool.{rig}.lanes overlap on gpu {sorted(indices & seen)}")
                seen |= indices
            shared = board_config.rigs.get(rig) if board_config is not None else None
            if shared is not None and shared.shared:
                foreign = seen - shared.owned_gpus - set(grants.get(rig, {}).get("gpus", ()))
                if foreign:
                    raise SupervisorError(
                        f"manifest: {rig} is shared and gpu {sorted(foreign)} is not ours (owned: "
                        f"{sorted(shared.owned_gpus)}); only a user grant in `gpu_grants.{rig}` allows it"
                    )
            pool[rig] = {"kind": "lanes", "lanes": list(lanes)}
    if set(grants) - set(pool):
        raise SupervisorError(f"manifest: gpu_grants names rigs outside the pool: {sorted(set(grants) - set(pool))}")
    offload = manifest.get("offload", [])
    if not isinstance(offload, list) or any(rig not in pool or pool[rig]["kind"] != "lanes" for rig in offload):
        raise SupervisorError("manifest: `offload` must list pool rigs that run lanes")
    fix_depth = manifest.get("fix_depth", 0)
    if not isinstance(fix_depth, int) or isinstance(fix_depth, bool) or fix_depth < 0:
        raise SupervisorError("manifest: `fix_depth` must be a non-negative integer (automatic fix rounds behind this wave)")
    # a fix round names the wave it replaces, so a chat watching that wave follows it here
    fixes_wave = manifest.get("fixes_wave")
    if fix_depth or fixes_wave is not None:
        if not isinstance(fixes_wave, str) or not rigsync.WAVE_RE.fullmatch(fixes_wave) or fixes_wave >= wave_id:
            raise SupervisorError("manifest: a fix round (`fix_depth` > 0) names the earlier wave it replaces in `fixes_wave`")
        parent = board.read_json(Wave(project_root, fixes_wave).queue_path)
        if not parent:
            raise SupervisorError(f"manifest: fixes_wave {fixes_wave} is not a registered wave of this project")
        if fix_depth != int(parent.get("fix_depth", 0)) + 1:
            raise SupervisorError(f"manifest: wave {fixes_wave} has fix_depth {parent.get('fix_depth', 0)}, so this fix round's is {int(parent.get('fix_depth', 0)) + 1}")
    runs_in = _require(manifest, "runs", list, where)
    if not runs_in:
        raise SupervisorError("manifest: `runs` is empty")
    runs = []
    seen_ids: set[str] = set()
    for position, entry in enumerate(runs_in):
        spot = f"manifest: runs[{position}]"
        if not isinstance(entry, dict):
            raise SupervisorError(f"{spot} must be a table")
        run_id = _require(entry, "id", str, spot)
        if run_id in seen_ids:
            raise SupervisorError(f"{spot}: duplicate run id {run_id!r}")
        seen_ids.add(run_id)
        cost = entry.get("cost_s")
        if cost is not None and (not isinstance(cost, (int, float)) or isinstance(cost, bool) or cost <= 0):
            raise SupervisorError(f"{spot}: cost_s must be a positive number of weight-1.0 seconds, or absent")
        needs = entry.get("needs", [])
        if not isinstance(needs, list) or not all(isinstance(tag, str) and TAG_RE.fullmatch(tag) for tag in needs):
            raise SupervisorError(f"{spot}: needs must be a list of data tags")
        min_vram = entry.get("min_vram_mib")
        if min_vram is not None and (not isinstance(min_vram, int) or isinstance(min_vram, bool) or min_vram <= 0):
            raise SupervisorError(f"{spot}: min_vram_mib must be a positive integer or absent")
        runs.append(
            {
                "id": run_id,
                "token": run_token(run_id),
                "script": _relative(entry.get("script"), f"{spot}.script"),
                "status_path": _relative(entry.get("status_path"), f"{spot}.status_path"),
                "artifact": _relative(entry.get("artifact"), f"{spot}.artifact"),
                "checkpoint_dir": _relative(entry.get("checkpoint_dir"), f"{spot}.checkpoint_dir"),
                "eval_dir": _relative(entry.get("eval_dir"), f"{spot}.eval_dir"),
                "resumes": bool(entry.get("resumes", False)),
                "cost_s": float(cost) if cost is not None else None,
                "min_vram_mib": min_vram,
                "needs": sorted(set(needs)),
                "pin": None,
                "exclude": [],
                "state": "queued",
                "lane": None,
                "entry": None,
                "attempts": 0,
                "job_id": None,
                "walltime_s": None,
                "observed": {},
                "note": None,
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "project": project_root.name,
        "project_root": str(project_root),
        "experiment": _require(manifest, "experiment", str, where),
        "wave_id": wave_id,
        "revision": revision,
        "lane_script": _relative(manifest.get("lane_script"), "manifest: lane_script"),
        "registered_at": iso(now()),
        "agent": {"model": agent.model, "effort": agent.effort},
        "pool": pool,
        "weights": {rig: float(weights_in[rig]) for rig in pool},
        "gpu_grants": grants,
        "offload": sorted(set(offload)),
        "ready": {rig: [] for rig in pool},
        "blocked_lanes": {},
        "waiting_lanes": {},
        "next_seq": 1,
        "fix_depth": fix_depth,
        "fixes_wave": fixes_wave,
        "runs": runs,
        "finished_at": None,
    }


def register(wave: Wave, manifest_path: Path, config, board_config, settings: Settings, dry_run: bool, confirmed: bool) -> int:
    rigsync.require_confirmation(dry_run, confirmed)
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise SupervisorError(f"cannot read manifest {manifest_path}: {exc}") from exc
    if not isinstance(manifest, dict) or manifest.get("wave_id") != wave.wave_id:
        raise SupervisorError(f"{manifest_path}: wave_id does not match --wave {wave.wave_id}")
    queue = build_queue(manifest, wave.project_root, config, settings, board_config)
    if wave.queue_path.exists():
        raise SupervisorError(f"wave {wave.wave_id} is already registered; a wave is registered once")
    lanes = sum(len(entry.get("lanes", [])) for entry in queue["pool"].values())
    label = "dry-run" if dry_run else "register"
    print(
        f"[{label}] wave {wave.wave_id}: {len(queue['runs'])} runs, {lanes} lane(s) on "
        f"{', '.join(sorted(queue['pool']))}; offload: {', '.join(queue['offload']) or 'none'}; "
        f"agent {queue['agent']['model']}/{queue['agent']['effort']}"
    )
    if dry_run:
        return 0
    with WaveLock(wave):
        wave.save(queue)
        wave.log("register", runs=len(queue["runs"]), pool=sorted(queue["pool"]))
    board.write_json(
        index_path(board_config, queue["project"], wave.wave_id),
        {"project_root": str(wave.project_root), "wave_id": wave.wave_id, "registered_at": queue["registered_at"]},
    )
    print(
        f"NEXT: in a chat, run `watch --wave {wave.wave_id}` as a background command and post what it prints each "
        f"time it returns (sweep-supervisor, In a chat); status any time with `table --wave {wave.wave_id}`"
    )
    return 0


# ───────────────────────────── rig access ─────────────────────────────


def rig_state_dir(machine, wave_id: str) -> str:
    return str(machine.repo_path / rigsync.WAVES_DIR / STATE_DIR / wave_id)


def lane_dir(machine, wave_id: str, gpu: str) -> str:
    return f"{rig_state_dir(machine, wave_id)}/lanes/gpu{gpu}"


def session_name(queue: dict, rig: str, gpu: str) -> str:
    experiment = str(queue["experiment"]).replace("/", "-")
    return f"{queue['project']}_{experiment}_{queue['wave_id']}_{rig}_gpu{gpu}"


def _text(value: object) -> str:
    return value.decode(errors="replace") if isinstance(value, bytes) else (value or "")


def sh(machine, script: str, *args: str, check: bool = False) -> subprocess.CompletedProcess:
    """Run a shell snippet on a rig (or here, for the hub) and hand back text, not bytes.

    The snippet goes over stdin, not argv: a probe over ~100 live runs outgrows the kernel's
    128 KiB per-argument limit and exec fails with E2BIG. The brace group is parsed whole before
    it runs, and its `</dev/null` keeps any command inside from eating the rest of the script.
    """
    piped = "{\n" + script + "\n} </dev/null\n"
    result = rigsync.remote(machine, ["sh", "-s", "--", *args], check=check, input_bytes=piped.encode())
    return subprocess.CompletedProcess(result.args, result.returncode, _text(result.stdout), _text(result.stderr))


PROBE_HEAD = (
    'cd "$1" || exit 3; S="$2"; '
    'echo __BOOT__; uptime -s 2>/dev/null; '
    "echo __TMUX__; tmux ls -F '#{session_name}' 2>/dev/null; "
    'echo __LANES__; for d in "$S"/lanes/gpu*; do [ -d "$d" ] || continue; g=${d##*/gpu}; '
    'for k in queue running finished; do for f in "$d/$k"/*; do [ -f "$f" ] && echo "$g|$k|${f##*/}"; done; done; '
    '[ -e "$d/drain" ] && echo "$g|drain|"; done; '
    "echo __RUNS__; "
)


def runs_section(runs: list[dict]) -> str:
    """Shell that prints, per live run, `token<TAB>A|-<TAB>status json`: artifact (or receipt) and status."""
    parts = []
    for run in runs:
        artifact = shlex.quote(run["artifact"])
        receipt = shlex.quote(run["artifact"] + ".offloaded.json")
        status = shlex.quote(run["status_path"])
        parts.append(
            f"printf '%s\\t' {shlex.quote(run['token'])}; "
            f"if [ -e {artifact} ] || [ -e {receipt} ]; then printf 'A\\t'; else printf -- '-\\t'; fi; "
            f"tr -d '\\n' < {status} 2>/dev/null; echo; "
        )
    return "".join(parts)


def probe_script(runs: list[dict]) -> str:
    """One round-trip per rig: boot time, sessions, lane buffers, and the signals of its live runs."""
    return PROBE_HEAD + runs_section(runs) + "echo __END__"


def _section(output: str, name: str, following: str) -> list[str]:
    body = output.partition(name)[2].partition(following)[0]
    return [line for line in body.splitlines() if line.strip()]


def parse_run_signals(lines: list[str]) -> dict[str, dict]:
    runs: dict[str, dict] = {}
    for line in lines:
        token, _, rest = line.partition("\t")
        flag, _, payload = rest.partition("\t")
        try:
            status = json.loads(payload) if payload.strip() else {}
        except json.JSONDecodeError:
            status = {}
        runs[token] = {"artifact": flag == "A", "status": status if isinstance(status, dict) else {}}
    return runs


def parse_rig_probe(output: str) -> dict:
    def section(name: str, following: str) -> list[str]:
        return _section(output, name, following)

    lanes: dict[str, dict] = {}
    for line in section("__LANES__", "__RUNS__"):
        gpu, _, rest = line.partition("|")
        kind, _, name = rest.partition("|")
        lane = lanes.setdefault(gpu, {"queue": [], "running": [], "finished": {}, "drain": False})
        if kind == "drain":
            lane["drain"] = True
        elif kind == "finished":
            entry, _, rc = name.rpartition(".rc")
            if entry and rc.lstrip("-").isdigit():
                lane["finished"][entry] = int(rc)
        elif kind == "running":
            if not name.endswith(".pid"):
                lane["running"].append(name)
        elif kind == "queue" and not name.startswith("."):
            lane["queue"].append(name)
    runs = parse_run_signals(section("__RUNS__", "__END__"))
    boot_lines = section("__BOOT__", "__TMUX__")
    boot = None
    if boot_lines:
        with contextlib.suppress(ValueError):
            boot = iso(datetime.strptime(boot_lines[0].strip(), "%Y-%m-%d %H:%M:%S").astimezone())
    return {"boot_at": boot, "sessions": section("__TMUX__", "__LANES__"), "lanes": lanes, "runs": runs}


def probe_rig(machine, queue: dict) -> dict | None:
    mine = [r for r in queue["runs"] if r["state"] in ("assigned", "running") and (r["lane"] or {}).get("rig") == machine.name]
    try:
        result = sh(machine, probe_script(mine), str(machine.repo_path), rig_state_dir(machine, queue["wave_id"]))
    except (rigsync.RigSyncError, OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0 or "__END__" not in result.stdout:
        return None
    return parse_rig_probe(result.stdout)


SLURM_PROBE = (
    "squeue --me --noheader --format='%i|%j|%T|%M|%L|%R' 2>/dev/null | grep -F \"|$1__\"; echo __SACCT__; "
    "sacct -X --noheader --parsable2 --format=JobID,JobName,State,Elapsed,ExitCode --starttime=\"$2\" 2>/dev/null "
    "| grep -F \"|$1__\"; echo __RUNS__; cd \"$3\" || exit 3; "
)


def probe_slurm(machine, queue: dict) -> dict | None:
    start = (parse_iso(queue["registered_at"]) or now()).strftime("%Y-%m-%dT%H:%M:%S")
    mine = [r for r in queue["runs"] if r["state"] in ("assigned", "running") and (r["lane"] or {}).get("rig") == machine.name]
    try:
        result = sh(machine, SLURM_PROBE + runs_section(mine) + "echo __END__", queue["wave_id"], start, str(machine.repo_path))
    except (rigsync.RigSyncError, OSError, subprocess.SubprocessError):
        return None
    if "__END__" not in result.stdout:
        return None
    live, _, accounted = result.stdout.partition("__SACCT__")
    jobs: dict[str, dict] = {}
    for line in accounted.partition("__RUNS__")[0].splitlines():
        cells = line.split("|")
        if len(cells) >= 5 and cells[0].isdigit():
            jobs[cells[0]] = {"name": cells[1], "state": cells[2].split()[0], "elapsed": cells[3], "exit": cells[4]}
    for line in live.splitlines():
        cells = line.split("|")
        if len(cells) >= 6 and cells[0].isdigit():
            jobs[cells[0]] = {"name": cells[1], "state": cells[2], "elapsed": cells[3], "left": cells[4], "reason": cells[5]}
    return {"jobs": jobs, "runs": parse_run_signals(_section(result.stdout, "__RUNS__", "__END__"))}


# ───────────────────────────── estimates ─────────────────────────────


def _elapsed(run: dict) -> float | None:
    value = (run.get("observed") or {}).get("elapsed_s")
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0 else None


def rig_rate(queue: dict, rig: str) -> tuple[float, str]:
    """Seconds of wall clock per weight-1.0 cost second on `rig`: measured in this wave, else the prior."""
    samples = [
        _elapsed(run) / run["cost_s"]
        for run in queue["runs"]
        if run["state"] == "done" and (run.get("lane") or {}).get("rig") == rig and run.get("cost_s") and _elapsed(run)
    ]
    if samples:
        return statistics.median(samples), "measured this wave"
    return 1.0 / queue["weights"][rig], "weight prior"


def estimate_run(queue: dict, run: dict, rig: str) -> tuple[float | None, str]:
    if run.get("cost_s"):
        rate, basis = rig_rate(queue, rig)
        return run["cost_s"] * rate, basis
    same = [_elapsed(r) for r in queue["runs"] if r["state"] == "done" and (r.get("lane") or {}).get("rig") == rig and _elapsed(r)]
    if same:
        return statistics.median(same), "measured this wave"
    normalized = [
        _elapsed(r) * queue["weights"][r["lane"]["rig"]]
        for r in queue["runs"]
        if r["state"] == "done" and r.get("lane") and r["lane"]["rig"] in queue["weights"] and _elapsed(r)
    ]
    if normalized:
        return statistics.median(normalized) / queue["weights"][rig], "measured this wave (weight-normalized)"
    return None, "no basis yet"


def remaining_active(queue: dict, run: dict, rig: str, at: datetime) -> tuple[float | None, str, bool]:
    """Remaining seconds of an in-flight run, its basis, and whether its heartbeat is stale."""
    observed = run.get("observed") or {}
    heartbeat = parse_iso(observed.get("heartbeat"))
    stale = heartbeat is not None and (at - heartbeat).total_seconds() > HEARTBEAT_STALE_S
    if stale:
        return None, "stale heartbeat", True
    done, total, elapsed = observed.get("progress_completed"), observed.get("progress_total"), _elapsed(run)
    if isinstance(done, (int, float)) and isinstance(total, (int, float)) and elapsed and 0 < done < total:
        return elapsed * (total - done) / done, "structured progress", False
    full, basis = estimate_run(queue, run, rig)
    if full is None:
        return None, basis, False
    return max(0.0, full - (elapsed or 0.0)), basis, False


def eligible(queue: dict, run: dict, rig: str, vram_mib: int | None) -> bool:
    if rig in run.get("exclude", []):
        return False
    if run.get("pin") and rig not in run["pin"]:
        return False
    if run.get("min_vram_mib") and vram_mib is not None and vram_mib < run["min_vram_mib"]:
        return False
    return set(run.get("needs", [])) <= set(queue["ready"].get(rig, []))


def lane_vram(board_config, rig: str, gpu: str) -> int | None:
    """Smallest card of the lane, from the fleet board's last probe; unknown stays unknown."""
    probe = board.read_json(board_config.rig_path(rig)) or {}
    wanted = board.gpu_indices(gpu)
    sizes = [g.get("memory_total_mib") for g in probe.get("gpus", []) if g.get("index") in wanted]
    sizes = [int(s) for s in sizes if isinstance(s, (int, float))]
    return min(sizes) if sizes and len(sizes) == len(wanted) else None


def simulate(queue: dict, lane_free: dict[tuple[str, str], float | None], vram: dict[tuple[str, str], int | None]) -> tuple[float | None, str]:
    """Wave completion in seconds from now if the feeder keeps doing what it does; None with the blocker."""
    free = dict(lane_free)
    if not free and not any(pool["kind"] == "slurm" for pool in queue["pool"].values()):
        return None, "every lane is waiting for the fleet board"
    if any(value is None for value in free.values()):
        blocked = [f"{rig} gpu{gpu}" for (rig, gpu), value in free.items() if value is None]
        return None, "lane ETA unavailable: " + ", ".join(sorted(blocked))
    horizon = max(free.values(), default=0.0)
    for run in queue["runs"]:
        if run["state"] != "queued":
            continue
        candidates = [lane for lane in free if eligible(queue, run, lane[0], vram.get(lane))]
        if not candidates:
            if any(pool["kind"] == "slurm" for pool in queue["pool"].values()):
                continue  # a cluster job's start is the scheduler's call; it never bounds the lanes
            return None, f"no eligible lane for {run['id']}"
        lane = min(candidates, key=lambda key: free[key])
        cost, _basis = estimate_run(queue, run, lane[0])
        if cost is None:
            return None, f"no estimate basis for {run['id']}"
        free[lane] += cost
        horizon = max(horizon, free[lane])
    return horizon, "feeder simulation"


# ───────────────────────────── one cycle ─────────────────────────────


def cert_expiry(path: Path) -> datetime | None:
    """When an OpenSSH certificate stops being valid, or None when it is unreadable or never expires."""
    try:
        result = subprocess.run(["ssh-keygen", "-L", "-f", str(path)], capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"Valid: from \S+ to (\S+)", result.stdout)
    if result.returncode != 0 or not match:
        return None
    try:
        return datetime.fromisoformat(match.group(1)).astimezone()  # ssh-keygen prints local time
    except ValueError:
        return None


def run_rigsync(wave: Wave, settings: Settings, *argv: str) -> subprocess.CompletedProcess:
    """rig-sync as a subprocess against this wave's project, so its output and exit code are its own."""
    command = [sys.executable, str(RIGSYNC_SCRIPT), "--root", str(wave.project_root), "--registry", str(settings.registry_path), *argv]
    return subprocess.run(command, capture_output=True, text=True, check=False)


def collect_once(wave: Wave, settings: Settings, queue: dict, rig: str, at: datetime) -> dict:
    """One `rigsync collect` of one rig into `queue["collect_last"]`. Never raises.

    Returns `ok` and, only when the failure is new (it differs from the rig's last recorded one),
    `problem`: the caller logs that, so a rig that keeps failing the same way is one event, not
    one a minute.
    """
    try:
        result = run_rigsync(wave, settings, "collect", "--wave", queue["wave_id"], "--machine", rig, "--confirm")
        code, stdout, stderr = result.returncode, result.stdout, result.stderr
    except (OSError, subprocess.SubprocessError) as exc:
        code, stdout, stderr = 1, "", str(exc)
    last = queue.setdefault("collect_last", {})
    previous = (last.get(rig) or {}).get("problem")
    if code != 0:
        problem = (stderr.strip() or stdout.strip())[-300:] or f"rigsync collect exited {code}"
        last[rig] = {"at": iso(at), "ok": False, "problem": problem}
        return {"ok": False, "problem": problem if problem != previous else None}
    summary = [line for line in stdout.splitlines() if line.startswith("[collect-summary]")]
    last[rig] = {"at": iso(at), "ok": True, "summary": summary[-1] if summary else "nothing to collect"}
    return {"ok": True}


class Cycle:
    """One pass over one wave. Everything it changes goes through the ledger."""

    def __init__(self, wave: Wave, config, board_config, settings: Settings) -> None:
        self.wave = wave
        self.config = config
        self.board_config = board_config
        self.settings = settings
        self.events: list[dict] = []
        self.at = now()

    def event(self, name: str, **fields: object) -> None:
        self.events.append(self.wave.log(name, **fields))

    # -- evidence ---------------------------------------------------------

    def absorb_rig(self, queue: dict, rig: str, view: dict) -> None:
        by_entry = {run["entry"]: run for run in queue["runs"] if run.get("entry") and (run.get("lane") or {}).get("rig") == rig}
        for gpu, lane in view["lanes"].items():
            for entry in lane["running"]:
                run = by_entry.get(entry)
                if run and run["state"] == "assigned":
                    run["state"] = "running"
                    self.event("run-started", run=run["id"], rig=rig, gpu=gpu)
            for entry, rc in lane["finished"].items():
                run = by_entry.get(entry)
                if run is None or run["state"] in TERMINAL_STATES or run["state"] == "queued":
                    continue
                self.settle(queue, run, rig, gpu, rc, view["runs"].get(run["token"], {}))
        for run in queue["runs"]:
            lane = run.get("lane") or {}
            if lane.get("rig") != rig or run["state"] not in ("assigned", "running"):
                continue
            signals = view["runs"].get(run["token"])
            if signals and signals["status"].get("wave_id") in (None, queue["wave_id"]):
                run["observed"] = {k: signals["status"].get(k) for k in (
                    "state", "started", "ended", "elapsed_s", "heartbeat", "progress",
                    "progress_completed", "progress_total", "progress_unit")}
            buffers = view["lanes"].get(lane.get("gpu"), {})
            present = run["entry"] in buffers.get("queue", []) or run["entry"] in buffers.get("running", []) or run["entry"] in buffers.get("finished", {})
            if not present:
                # the rig lost the entry (state wiped, disk swapped): the run is simply due again
                self.requeue(run, front=True)
                self.event("run-lost", run=run["id"], rig=rig, gpu=lane.get("gpu"))

    def settle(self, queue: dict, run: dict, rig: str, gpu: str, rc: int, signals: dict) -> None:
        status = signals.get("status", {})
        run["observed"] = {**(run.get("observed") or {}), **{k: status.get(k) for k in ("state", "started", "ended", "elapsed_s") if k in status}}
        if signals.get("artifact"):
            run["state"] = "done"
            self.event("run-done", run=run["id"], rig=rig, gpu=gpu, elapsed_s=status.get("elapsed_s"))
        elif run.get("stolen"):
            run.pop("stolen", None)
            self.requeue(run, front=True)
            self.event("run-requeued", run=run["id"], reason="stolen from " + rig)
        elif rc in RESERVED_EXITS:
            # a guard stopped the run before any compute: the run is innocent, the lane is not
            self.requeue(run, front=True)
            run["exclude"] = sorted(set(run["exclude"]) | ({rig} if rc in LANE_STOPPING else set()))
            self.event("lane-stopped", rig=rig, gpu=gpu, rc=rc, reason=RESERVED_EXITS[rc], run=run["id"])
        else:
            run["state"] = "failed"
            run["note"] = f"exit {rc} on {rig} gpu{gpu}"
            self.event("run-failed", run=run["id"], rig=rig, gpu=gpu, rc=rc)

    def requeue(self, run: dict, front: bool = False) -> None:
        run["state"] = "queued"
        run["lane"] = None
        run["entry"] = None
        run["job_id"] = None
        run["front"] = bool(front)

    # -- lanes ------------------------------------------------------------

    def lane_launch_command(self, queue: dict, machine, gpu: str) -> str:
        root = rigsync.storage_root_of_project(machine)
        floor_kib = int((root.floor_gb if root else rigsync.DEFAULT_MIN_FREE_GB) * rigsync.KIB_PER_GB)
        env = {
            "LANE_RIG": machine.name,
            "LANE_GPUS": gpu,
            "MIN_FREE_KIB": str(floor_kib),
            "QUOTA_FS": (root.quota_fs if root and root.quota_fs else ""),
        }
        assignments = " ".join(f"{key}={shlex.quote(value)}" for key, value in env.items())
        script = f"{rigsync.WAVES_DIR}/{queue['wave_id']}/{queue['lane_script']}"
        inner = f"cd {shlex.quote(str(machine.repo_path))} && env {assignments} bash {shlex.quote(script)}"
        session = session_name(queue, machine.name, gpu)
        return f"tmux has-session -t {shlex.quote(session)} 2>/dev/null || tmux new-session -d -s {shlex.quote(session)} {shlex.quote(inner)}"

    def verify_lane(self, queue: dict, machine, gpu: str) -> str | None:
        """The same gate a human relaunch passes: exact revision, then environment and lane smoke."""
        sink = io.StringIO()
        try:
            with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                rigsync.verify_revision(self.config, [machine], queue["wave_id"], queue["revision"])
        except rigsync.RigSyncError as exc:
            return f"revision: {exc}"
        command = [
            sys.executable, str(ENVSYNC_SCRIPT), "--root", str(self.wave.project_root), "--registry", str(self.settings.registry_path),
            "verify", "--wave", queue["wave_id"], "--revision", queue["revision"], "--machines", machine.name,
            "--lane", f"{machine.name}:{gpu}",
        ]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            return "environment: " + (result.stderr.strip() or result.stdout.strip())[-400:]
        return None

    def ensure_lane(self, queue: dict, machine, gpu: str, view: dict, has_work: bool) -> bool:
        """Return whether the lane may be fed: its session is alive (or was just launched) and the board gives us the card."""
        session = session_name(queue, machine.name, gpu)
        key = f"{machine.name}:{gpu}"
        alive = session in view["sessions"]
        if alive and self.holds_lane(machine.name, gpu, session):
            return True
        if not alive and (not has_work or key in queue["blocked_lanes"]):
            return False
        # the board decides first: a refused claim keeps the lane out (an alive one is simply not fed)
        if not self.claim_lane(queue, machine.name, gpu, session):
            return False
        if alive:
            return True
        problem = self.verify_lane(queue, machine, gpu)
        if problem:
            queue["blocked_lanes"][key] = problem
            self.event("recovery-blocked", rig=machine.name, gpu=gpu, problem=problem)
            self.board_release(machine.name, gpu, f"wave {queue['wave_id']} could not start this lane")
            return False
        sh(machine, f'rm -f "$1/drain"', lane_dir(machine, queue["wave_id"], gpu))
        result = sh(machine, self.lane_launch_command(queue, machine, gpu))
        if result.returncode != 0:
            queue["blocked_lanes"][key] = f"tmux: {result.stderr.strip()[-200:]}"
            self.event("recovery-blocked", rig=machine.name, gpu=gpu, problem=queue["blocked_lanes"][key])
            self.board_release(machine.name, gpu, f"wave {queue['wave_id']} could not start this lane")
            return False
        rebooted = bool(view.get("boot_at") and parse_iso(view["boot_at"]) and parse_iso(view["boot_at"]) > (parse_iso(queue["registered_at"]) or self.at))
        launched_before = any(r.get("event") == "lane-launched" and r.get("rig") == machine.name and r.get("gpu") == gpu for r in self.wave.ledger())
        self.event("lane-relaunched" if launched_before else "lane-launched", rig=machine.name, gpu=gpu, session=session, rebooted=rebooted)
        return True

    def feed(self, queue: dict, machine, gpu: str, view: dict, vram: int | None) -> None:
        buffers = view["lanes"].get(gpu, {"queue": [], "running": []})
        assigned_here = [r for r in queue["runs"] if r["state"] in ("assigned", "running") and r["lane"] == {"rig": machine.name, "gpu": gpu}]
        room = BUFFER_DEPTH - max(len(buffers["queue"]) + len(buffers["running"]), len(assigned_here))
        ordered = sorted((r for r in queue["runs"] if r["state"] == "queued"), key=lambda r: not r.get("front"))
        for run in ordered:
            if room <= 0:
                break
            if not eligible(queue, run, machine.name, vram):
                continue
            entry = f"{queue['next_seq']:06d}__{run['token']}"
            result = sh(
                machine,
                'mkdir -p "$1/queue" "$1/running" "$1/finished" && printf %s "$3" > "$1/.tmp.$2" && mv "$1/.tmp.$2" "$1/queue/$2"',
                lane_dir(machine, queue["wave_id"], gpu), entry, run["script"],
            )
            if result.returncode != 0:
                break
            queue["next_seq"] += 1
            run.update(state="assigned", lane={"rig": machine.name, "gpu": gpu}, entry=entry, attempts=run["attempts"] + 1)
            run.pop("front", None)
            self.event("run-assigned", run=run["id"], rig=machine.name, gpu=gpu, entry=entry)
            room -= 1

    def steal_pending_job(self, queue: dict, machine, gpu: str, vram: int | None) -> None:
        """An idle lane with nothing left to take pulls a job the cluster has not started yet."""
        for run in reversed(queue["runs"]):
            lane = run.get("lane") or {}
            cluster = self.config.machines.get(lane.get("rig", ""))
            if run["state"] != "assigned" or not run.get("job_id") or cluster is None or not cluster.gpus_in_job:
                continue
            if (run.get("observed") or {}).get("slurm_state") != "PENDING" or not eligible(queue, run, machine.name, vram):
                continue
            result = sh(cluster, 'scancel "$1"', str(run["job_id"]))
            if result.returncode != 0:
                continue
            self.event("job-cancelled", run=run["id"], job=run["job_id"], reason=f"moved to idle {machine.name} gpu{gpu}")
            self.requeue(run, front=True)
            return

    # -- slurm ------------------------------------------------------------

    def walltime(self, queue: dict, run: dict, rig: str) -> int:
        estimate, _basis = estimate_run(queue, run, rig)
        seconds = max(run.get("walltime_s") or 0, (estimate or 0) * WALLTIME_MARGIN) or queue["pool"][rig]["default_walltime_s"]
        stepped = int(-(-seconds // WALLTIME_STEP_S) * WALLTIME_STEP_S)
        return min(max(stepped, WALLTIME_STEP_S), WALLTIME_CAP_S)

    def absorb_slurm(self, queue: dict, rig: str, view: dict) -> None:
        for run in queue["runs"]:
            if (run.get("lane") or {}).get("rig") != rig or run["state"] not in ("assigned", "running") or not run.get("job_id"):
                continue
            job = view["jobs"].get(str(run["job_id"]))
            if job is None:
                continue
            state = job["state"]
            status = (view.get("runs", {}).get(run["token"]) or {}).get("status", {})
            if status.get("wave_id") in (None, queue["wave_id"]):
                run["observed"] = {k: status.get(k) for k in (
                    "state", "started", "ended", "elapsed_s", "heartbeat", "progress",
                    "progress_completed", "progress_total", "progress_unit")}
            run.setdefault("observed", {})["slurm_state"] = state
            run["observed"]["slurm_reason"] = job.get("reason")
            run["observed"]["slurm_left"] = job.get("left")
            if state == "RUNNING":
                if run["state"] == "assigned":
                    run["state"] = "running"
                    self.event("run-started", run=run["id"], rig=rig, job=run["job_id"])
            elif state == "COMPLETED":
                run["needs_artifact_check"] = True
            elif state == "TIMEOUT":
                # the next allocation is sized from what this one measured, never from the same guess again
                previous = run.get("walltime_s") or 0
                done, total, elapsed = run["observed"].get("progress_completed"), run["observed"].get("progress_total"), _elapsed(run)
                measured = elapsed * total / done * WALLTIME_MARGIN if elapsed and isinstance(done, (int, float)) and isinstance(total, (int, float)) and 0 < done <= total else 0
                self.requeue(run, front=True)
                run["walltime_s"] = int(min(max(previous * 2, measured), WALLTIME_CAP_S))
                self.event("job-timeout", run=run["id"], job=job, next_walltime_s=run["walltime_s"], resumes=run.get("resumes"))
            elif state in SLURM_MACHINE_FAULTS or (state == "CANCELLED" and not run.get("stolen")):
                self.requeue(run, front=True)
                self.event("job-interrupted", run=run["id"], slurm_state=state)
            elif state in ("FAILED", "OUT_OF_MEMORY"):
                run["state"] = "failed"
                run["note"] = f"slurm {state} exit {job.get('exit')}"
                self.event("job-failed", run=run["id"], slurm_state=state, exit=job.get("exit"))

    def check_completed_jobs(self, queue: dict, machine) -> None:
        pending = [r for r in queue["runs"] if r.pop("needs_artifact_check", False)]
        for run in pending:
            present = sh(machine, 'cd "$1" && { [ -e "$2" ] || [ -e "$2.offloaded.json" ]; }', str(machine.repo_path), run["artifact"]).returncode == 0
            if present:
                run["state"] = "done"
                self.event("run-done", run=run["id"], rig=machine.name, job=run["job_id"])
            else:
                run["state"] = "failed"
                run["note"] = "exit 0 without artifact"
                self.event("job-failed", run=run["id"], slurm_state="COMPLETED", problem=run["note"])

    def submit_jobs(self, queue: dict, machine) -> None:
        rig = machine.name
        pool = queue["pool"][rig]
        live = [r for r in queue["runs"] if (r.get("lane") or {}).get("rig") == rig and r["state"] in ("assigned", "running")]
        room = pool["max_jobs"] - len(live)
        spent = sum(r.get("budget_h", 0.0) for r in queue["runs"] if r.get("budget_rig") == rig)
        # the cluster takes from the tail of the queue while the lanes take from the head
        for run in reversed([r for r in queue["runs"] if r["state"] == "queued"]):
            if room <= 0:
                break
            if not eligible(queue, run, rig, None):
                continue
            walltime = self.walltime(queue, run, rig)
            cost_h = walltime / 3600 * pool["cores_per_job"]
            if pool["core_hours_cap"] is not None and spent + cost_h > pool["core_hours_cap"]:
                if not queue.get("budget_notified"):
                    queue["budget_notified"] = True
                    self.event("budget-cap", rig=rig, spent_h=round(spent, 1), cap_h=pool["core_hours_cap"])
                break
            name = f"{queue['wave_id']}__{run['id']}"
            script = f"{rigsync.WAVES_DIR}/{queue['wave_id']}/{run['script']}"
            hms = f"{walltime // 3600:02d}:{walltime % 3600 // 60:02d}:00"
            # a live job with this name is one whose submission the queue lost (a restart mid-cycle): adopt it
            result = sh(
                machine,
                'cd "$1" && live=$(squeue --me --noheader --name="$2" --format=%i | head -n 1); '
                'if [ -n "$live" ]; then echo "ALREADY-QUEUED;$live"; '
                'else sbatch --parsable --time="$3" --export=ALL,LANE_RIG="$5",LANE_GPUS=1 "$4"; fi',
                str(machine.repo_path), name, hms, script, rig,
            )
            out = result.stdout.strip()
            adopted = out.startswith("ALREADY-QUEUED;")
            job_id = out.split(";")[1] if adopted else out.split(";")[0]
            if result.returncode != 0 or not job_id.isdigit():
                self.event("submit-failed", rig=rig, run=run["id"], problem=(result.stderr or result.stdout).strip()[-300:])
                break
            run.update(state="assigned", lane={"rig": rig, "gpu": "1"}, entry=None, job_id=int(job_id), walltime_s=walltime,
                       attempts=run["attempts"] + 1, budget_h=run.get("budget_h", 0.0) + cost_h, budget_rig=rig)
            run.pop("front", None)
            if adopted:
                self.record_job(run, job_id, "adopted")
                self.event("job-adopted", run=run["id"], rig=rig, job=int(job_id), walltime_s=walltime)
            else:
                self.record_job(run, job_id, "submitted" if run["attempts"] == 1 else "resubmitted")
                self.event("job-submitted", run=run["id"], rig=rig, job=int(job_id), walltime_s=walltime)
            # saved per job, so a restart mid-cycle cannot lose a submission that already reached Slurm
            self.wave.save(queue)
            spent += cost_h
            room -= 1

    def record_job(self, run: dict, job_id: str, verb: str) -> None:
        path = self.wave.project_root / Path(run["script"]).parent / "leonardo.jobs"
        with contextlib.suppress(OSError):
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a") as handle:
                handle.write(f"{iso(self.at)} {job_id} {verb}\n")

    # -- board ------------------------------------------------------------

    def holds_lane(self, rig: str, gpu: str, session: str) -> bool:
        held = board.read_json(self.board_config.lane_path(rig, gpu)) or {}
        return held.get("tmux_session") == session

    def claim_lane(self, queue: dict, rig: str, gpu: str, session: str) -> bool:
        """Claim the card on the board; a refusal is remembered and reported once, until it changes."""
        problem = self.board_claim(queue, rig, gpu, session)
        waiting = queue.setdefault("waiting_lanes", {})
        key = f"{rig}:{gpu}"
        if problem is None:
            if waiting.pop(key, None) is not None:
                self.event("claim-granted", rig=rig, gpu=gpu)
            return True
        if waiting.get(key) != problem:
            self.event("claim-refused", rig=rig, gpu=gpu, problem=problem)
        waiting[key] = problem
        return False

    def board_claim(self, queue: dict, rig: str, gpu: str, session: str) -> str | None:
        """Return None when the board gives us the lane, else why it refused."""
        args = argparse.Namespace(
            rig=rig, gpu=gpu, project=queue["project"], experiment=queue["experiment"], wave=queue["wave_id"],
            project_root=queue["project_root"], runs_total=len(queue["runs"]), tmux_session=session,
            granted_gpus=queue.get("gpu_grants", {}).get(rig, {}).get("gpus", []),
        )
        sink = io.StringIO()
        try:
            with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                code = board.claim(self.board_config, args)
        except board.BoardError as exc:
            code, sink = 2, io.StringIO(str(exc))
        return None if code == 0 else (sink.getvalue().strip()[-300:] or f"board claim exited {code}")

    def board_refresh(self, queue: dict, lane_rows: list[dict]) -> None:
        for row in lane_rows:
            if row["kind"] != "lane" or not self.board_config.lane_path(row["rig"], row["gpu"]).exists():
                continue
            args = argparse.Namespace(
                rig=row["rig"], gpu=row["gpu"], wave=queue["wave_id"], active_run=row["run"] or "",
                progress=row["progress_text"] or "", eta=row["lane_free_at"] or "", eta_basis=row["basis"] or "",
                runs_done=row["done"], runs_total=None,
            )
            with contextlib.suppress(board.BoardError), contextlib.redirect_stdout(io.StringIO()):
                board.refresh(self.board_config, args)

    def board_release(self, rig: str, gpu: str, reason: str) -> None:
        args = argparse.Namespace(rig=rig, gpu=gpu, reason=reason)
        with contextlib.suppress(board.BoardError), contextlib.redirect_stdout(io.StringIO()):
            board.release(self.board_config, args)

    # -- offload and collect ----------------------------------------------

    def offload(self, queue: dict, rig: str) -> None:
        result = run_rigsync(self.wave, self.settings, "offload", "--machine", rig, "--wave", queue["wave_id"], "--confirm")
        summary = [line for line in result.stdout.splitlines() if line.startswith("[offload-summary]")]
        if result.returncode != 0:
            self.event("offload-failed", rig=rig, problem=(result.stderr.strip() or result.stdout.strip())[-300:])
        elif summary:
            queue.setdefault("offload_last", {})[rig] = {"at": iso(self.at), "summary": summary[-1]}

    def collect(self, queue: dict, rig: str) -> bool:
        """Pull one rig's evaluation records to the hub; never raises. Returns whether it succeeded."""
        outcome = collect_once(self.wave, self.settings, queue, rig, self.at)
        if outcome.get("problem"):
            self.event("collect-failed", rig=rig, problem=outcome["problem"])
        return outcome["ok"]

    # -- certificates -----------------------------------------------------

    def watch_certs(self, queue: dict, runtime: dict) -> None:
        """A cluster login certificate expires on a clock. Say so an hour before and when it does,
        once per certificate; renewing it is the user's 2FA, so this is a notice, never a question,
        and the service resumes by itself once the rig answers again."""
        certs = runtime.setdefault("certs", {})
        for rig, path in self.settings.ssh_certs:
            if rig not in queue["pool"]:
                continue
            expires = cert_expiry(path)
            if expires is None:
                certs.pop(rig, None)
                continue
            record = certs.setdefault(rig, {})
            if record.get("expires_at") != iso(expires):
                record.clear()  # a renewed certificate starts its own warnings
                record["expires_at"] = iso(expires)
            left = (expires - self.at).total_seconds()
            if left <= 0 and not record.get("expired_sent"):
                record["expired_sent"] = True
                self.event("cert-expired", rig=rig, expired_at=record["expires_at"], cert=str(path))
            elif 0 < left <= CERT_WARN_S and not record.get("expiring_sent"):
                record["expiring_sent"] = True
                self.event("cert-expiring", rig=rig, expires_at=record["expires_at"], cert=str(path))

    # -- episodes that need judgment --------------------------------------

    def mark_episodes(self, queue: dict, snapshot: dict, runtime: dict, vram: dict) -> None:
        """Wake the agent the cycle a lane needs judgment, not at the next scheduled tick: a run whose
        heartbeat went stale (a hang or a dead process), and a lane left idle with nothing it can
        take while another lane still holds in-flight or buffered runs (tail stealing). One event per
        episode, kept in runtime.json: a state that persists is not news, one that clears and returns is."""
        episodes = runtime.setdefault("episodes", {})
        rows = snapshot["lanes"]
        stale = {f"{row['rig']}:{row['gpu']}:{row['run']}": row for row in rows if row["state"] == "stale"}
        for key, row in stale.items():
            if key not in episodes.get("heartbeat_stale", []):
                age = row.get("heartbeat_age_s")
                self.event("heartbeat-stale", rig=row["rig"], gpu=row["gpu"], run=row["run"], heartbeat_age_s=int(age) if age is not None else None)
        episodes["heartbeat_stale"] = sorted(stale)
        busy = [row for row in rows if row["kind"] == "lane" and row["state"] in ("running", "stale", "starting")]
        idle: dict[str, dict] = {}
        if busy and not queue.get("finished_at"):
            for row in rows:
                if row["kind"] != "lane" or row["state"] != "idle":
                    continue
                takes = any(r["state"] == "queued" and eligible(queue, r, row["rig"], vram.get((row["rig"], row["gpu"]))) for r in queue["runs"])
                if not takes:
                    idle[f"{row['rig']}:{row['gpu']}"] = row
        for key, row in idle.items():
            if key not in episodes.get("lane_idle_tail", []):
                others = [f"{r['rig']} gpu{r['gpu']}" for r in busy]
                self.event("lane-idle-tail", rig=row["rig"], gpu=row["gpu"], busy=others)
        episodes["lane_idle_tail"] = sorted(idle)

    # -- the pass ---------------------------------------------------------

    def run(self) -> dict:
        wave = self.wave
        with WaveLock(wave):
            queue = wave.load()
            runtime = wave.runtime()
            self.watch_certs(queue, runtime)
            rigs_runtime = runtime.setdefault("rigs", {})
            views: dict[str, dict | None] = {}
            for rig, pool in queue["pool"].items():
                machine = self.config.machines[rig]
                view = probe_slurm(machine, queue) if pool["kind"] == "slurm" else probe_rig(machine, queue)
                views[rig] = view
                state = rigs_runtime.setdefault(rig, {"failures": 0, "down_since": None})
                if view is None:
                    state["failures"] += 1
                    if state["failures"] == DOWN_AFTER_FAILURES:
                        state["down_since"] = iso(self.at)
                        self.event("rig-down", rig=rig, since=state["down_since"])
                    continue
                if state.get("down_since"):
                    self.event("rig-up", rig=rig, down_since=state["down_since"], boot_at=view.get("boot_at"))
                state.update(failures=0, down_since=None)
                if pool["kind"] == "slurm":
                    self.absorb_slurm(queue, rig, view)
                    self.check_completed_jobs(queue, machine)
                else:
                    self.absorb_rig(queue, rig, view)
            vram: dict[tuple[str, str], int | None] = {}
            for rig, pool in queue["pool"].items():
                view = views[rig]
                if view is None:
                    continue
                machine = self.config.machines[rig]
                if pool["kind"] == "slurm":
                    self.submit_jobs(queue, machine)
                    continue
                for gpu in pool["lanes"]:
                    vram[(rig, gpu)] = lane_vram(self.board_config, rig, gpu)
                    waiting = any(r["state"] == "queued" and eligible(queue, r, rig, vram[(rig, gpu)]) for r in queue["runs"])
                    mine = any(r["state"] in ("assigned", "running") and r["lane"] == {"rig": rig, "gpu": gpu} for r in queue["runs"])
                    if not mine and not waiting:
                        # nothing left in the queue for this lane: a job the cluster has not started is fair game
                        self.steal_pending_job(queue, machine, gpu, vram[(rig, gpu)])
                        waiting = any(r["state"] == "queued" and eligible(queue, r, rig, vram[(rig, gpu)]) for r in queue["runs"])
                    alive = self.ensure_lane(queue, machine, gpu, view, waiting or mine)
                    if not alive:
                        if view["lanes"].get(gpu, {}).get("drain") and self.holds_lane(rig, gpu, session_name(queue, rig, gpu)):
                            # the drained loop has exited: only now is the card really free for someone else
                            self.board_release(rig, gpu, f"wave {queue['wave_id']} has nothing left for this lane")
                        continue
                    self.feed(queue, machine, gpu, view, vram[(rig, gpu)])
                    still_mine = any(r["state"] in ("assigned", "running") and r["lane"] == {"rig": rig, "gpu": gpu} for r in queue["runs"])
                    if not still_mine:
                        still_waiting = any(r["state"] == "queued" and eligible(queue, r, rig, vram[(rig, gpu)]) for r in queue["runs"])
                        if not still_waiting and not view["lanes"].get(gpu, {}).get("drain"):
                            sh(machine, 'mkdir -p "$1" && : > "$1/drain"', lane_dir(machine, queue["wave_id"], gpu))
                            self.event("lane-drained", rig=rig, gpu=gpu)
                if rig in queue["offload"]:
                    self.offload(queue, rig)
                    sh(machine, 'mkdir -p "$1" && : > "$1/offload"', rig_state_dir(machine, queue["wave_id"]))
            # every rig's evaluation records converge on the hub, each pass; a rig whose probe failed waits
            collected: set[str] = set()
            for rig in queue["pool"]:
                if views[rig] is not None and not self.config.machines[rig].local:
                    if self.collect(queue, rig):
                        collected.add(rig)
            terminal = all(run["state"] in TERMINAL_STATES for run in queue["runs"])
            if terminal and not queue.get("finished_at"):
                # the final collect: every rig, before the wave is reported terminal and the run_id map is rebuilt
                missed = [
                    rig for rig in sorted(queue["pool"])
                    if not self.config.machines[rig].local and rig not in collected and not self.collect(queue, rig)
                ]
                queue["finished_at"] = iso(self.at)
                self.event(
                    "wave-terminal", done=sum(r["state"] == "done" for r in queue["runs"]), failed=sum(r["state"] == "failed" for r in queue["runs"]),
                    collect_failed=missed,
                )
            snapshot = build_snapshot(queue, runtime, wave, vram, self.at)
            self.mark_episodes(queue, snapshot, runtime, vram)
            last_refresh = parse_iso(runtime.get("board_refreshed_at"))
            if last_refresh is None or (self.at - last_refresh).total_seconds() >= BOARD_REFRESH_EVERY_S:
                self.board_refresh(queue, snapshot["lanes"])
                runtime["board_refreshed_at"] = iso(self.at)
            runtime["cycled_at"] = iso(self.at)
            wave.save(queue)
            wave.save_runtime(runtime)
            board.write_json(wave.snapshot_path, snapshot)
            wave.table_path.write_text(render_table(snapshot, "md"))
        return {"events": self.events, "snapshot": snapshot}


# ───────────────────────────── snapshot and table ─────────────────────────────


def build_snapshot(queue: dict, runtime: dict, wave: Wave, vram: dict, at: datetime) -> dict:
    counts = {state: sum(run["state"] == state for run in queue["runs"]) for state in RUN_STATES}
    rows: list[dict] = []
    lane_free: dict[tuple[str, str], float | None] = {}
    jobs_left: dict[str, float | None] = {}  # running cluster jobs: the wave is not over before they are
    for rig, pool in sorted(queue["pool"].items()):
        down = (runtime.get("rigs", {}).get(rig) or {}).get("down_since")
        if pool["kind"] == "slurm":
            for run in queue["runs"]:
                if (run.get("lane") or {}).get("rig") == rig and run["state"] in ("assigned", "running"):
                    rows.append(job_row(queue, run, rig, at))
                    if run["state"] == "running":
                        jobs_left[f"{rig} job {run['job_id']}"] = remaining_active(queue, run, rig, at)[0]
            continue
        for gpu in pool["lanes"]:
            mine = [r for r in queue["runs"] if r["lane"] == {"rig": rig, "gpu": gpu}]
            active = next((r for r in mine if r["state"] == "running"), None)
            buffered = [r for r in mine if r["state"] == "assigned"]
            remaining, basis, stale = (None, "", False)
            free: float | None = 0.0
            if active:
                remaining, basis, stale = remaining_active(queue, active, rig, at)
                free = remaining
            for run in buffered:
                cost, run_basis = estimate_run(queue, run, rig)
                free = None if free is None or cost is None else free + cost
                basis = basis or run_basis
            blocked = queue["blocked_lanes"].get(f"{rig}:{gpu}")
            waiting = queue.get("waiting_lanes", {}).get(f"{rig}:{gpu}")
            if down:
                state = "rig down"
            elif blocked:
                state = "blocked"
            elif waiting and not active and not buffered:
                state = "waiting for board"
            elif active:
                state = "stale" if stale else "running"
            elif buffered:
                state = "starting"
            else:
                state = "idle"
            if state != "waiting for board":
                lane_free[(rig, gpu)] = None if down or blocked else free
            observed = (active or {}).get("observed") or {}
            heartbeat = parse_iso(observed.get("heartbeat"))
            rows.append(
                {
                    "kind": "lane", "rig": rig, "gpu": gpu, "state": state,
                    "run": active["id"] if active else None,
                    "progress_text": observed.get("progress"),
                    "fraction": fraction(observed),
                    "heartbeat_age_s": (at - heartbeat).total_seconds() if heartbeat else None,
                    "run_eta": iso(at + timedelta(seconds=remaining)) if remaining is not None else None,
                    "next": buffered[0]["id"] if buffered else None,
                    "done": sum(r["state"] == "done" for r in mine),
                    "lane_free_at": iso(at + timedelta(seconds=free)) if free is not None and (active or buffered) else None,
                    "basis": basis or None,
                    "problem": blocked or (f"down since {down}" if down else None),
                }
            )
    horizon, basis = simulate(queue, lane_free, vram) if any(r["state"] not in TERMINAL_STATES for r in queue["runs"]) else (0.0, "terminal")
    horizon, basis = with_cluster_jobs(horizon, basis, jobs_left)
    questions = [q for q in wave.questions() if q.get("status") == "open"]
    return {
        "schema_version": SCHEMA_VERSION,
        "written_at": iso(at),
        "project": queue["project"], "experiment": queue["experiment"], "wave_id": queue["wave_id"],
        "counts": counts, "total": len(queue["runs"]),
        "wave_eta": iso(at + timedelta(seconds=horizon)) if horizon is not None else None,
        "wave_eta_basis": basis,
        "registered_at": queue.get("registered_at"),
        "finished_at": queue.get("finished_at"),
        "agent": queue["agent"],
        "agent_ticked_at": runtime.get("agent_ticked_at"),
        "agent_last_result": runtime.get("agent_last_result"),
        "cycled_at": iso(at),
        "offload": {rig: (queue.get("offload_last") or {}).get(rig) for rig in queue["offload"]},
        "collect": dict(sorted((queue.get("collect_last") or {}).items())),
        "lanes": rows,
        # per rig, so a Slurm rig (whose rows are only its live jobs) still reports what it finished
        "rigs": {
            rig: {"kind": pool["kind"], "done": sum(r["state"] == "done" and (r.get("lane") or {}).get("rig") == rig for r in queue["runs"])}
            for rig, pool in sorted(queue["pool"].items())
        },
        "certs": runtime.get("certs", {}),
        "failed": [{"run": r["id"], "note": r.get("note")} for r in queue["runs"] if r["state"] == "failed"],
        "excluded": [{"run": r["id"], "note": r.get("note")} for r in queue["runs"] if r["state"] == "excluded"],
        "questions": questions,
        "decisions": recent_decisions(wave),
    }


def with_cluster_jobs(horizon: float | None, basis: str, jobs_left: dict[str, float | None]) -> tuple[float | None, str]:
    """The lanes' horizon stretched to the last running cluster job. The feeder simulation only sees
    lanes, so a wave whose remaining work is all on a cluster would otherwise read as due now. A
    running job with no estimate makes the wave ETA unavailable, the way a lane without one does;
    a pending job's start stays the scheduler's call and is not counted."""
    if horizon is None or not jobs_left:
        return horizon, basis
    unknown = sorted(name for name, left in jobs_left.items() if left is None)
    if unknown:
        return None, "cluster job ETA unavailable: " + ", ".join(unknown)
    longest = max(left for left in jobs_left.values() if left is not None)
    if longest > horizon:
        return longest, "running cluster jobs' progress"
    return horizon, basis


def fraction(observed: dict) -> float | None:
    done, total = observed.get("progress_completed"), observed.get("progress_total")
    if isinstance(done, (int, float)) and isinstance(total, (int, float)) and total > 0:
        return max(0.0, min(1.0, done / total))
    return None


def job_row(queue: dict, run: dict, rig: str, at: datetime) -> dict:
    observed = run.get("observed") or {}
    slurm_state = observed.get("slurm_state") or "SUBMITTED"
    remaining, basis, stale = remaining_active(queue, run, rig, at) if run["state"] == "running" else (None, "scheduler", False)
    heartbeat = parse_iso(observed.get("heartbeat"))
    return {
        "kind": "job", "rig": rig, "gpu": f"job {run['job_id']}",
        "state": "stale" if stale else slurm_state.lower() + (f" ({observed.get('slurm_reason')})" if slurm_state == "PENDING" and observed.get("slurm_reason") else ""),
        "run": run["id"], "progress_text": observed.get("progress"), "fraction": fraction(observed),
        "heartbeat_age_s": (at - heartbeat).total_seconds() if heartbeat else None,
        "run_eta": iso(at + timedelta(seconds=remaining)) if remaining is not None else None,
        "next": f"walltime left {observed.get('slurm_left')}" if observed.get("slurm_left") else None,
        "done": 0, "lane_free_at": None, "basis": basis, "problem": None,
    }


def recent_decisions(wave: Wave, limit: int = 3) -> list[str]:
    try:
        lines = [line for line in wave.decisions_path.read_text().splitlines() if line.startswith("- ")]
    except OSError:
        return []
    return lines[-limit:]


def humanize(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    seconds = int(max(0, seconds))
    if seconds < 90:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 90:
        return f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    if hours < 48:
        return f"{hours}h{minutes:02d}m"
    days, hours = divmod(hours, 24)
    return f"{days}d{hours}h"


def clock(value: str | None, at: datetime) -> str:
    moment = parse_iso(value)
    if moment is None:
        return "—"
    local = moment.astimezone(at.tzinfo)
    return local.strftime("%H:%M") if local.date() == at.date() else local.strftime("%m-%d %H:%M")


def bar(value: float | None, width: int = 10) -> str:
    """Emoji squares, so the color survives a markdown chat: red under a third, yellow under two
    thirds, green beyond."""
    if value is None:
        return "—"
    filled = int(round(value * width))
    square = "🟥" if value < 1 / 3 else "🟨" if value < 2 / 3 else "🟩"
    return square * filled + "⬜" * (width - filled) + f" {value * 100:3.0f}%"


def short(text: str | None, width: int = 44) -> str:
    if not text:
        return "—"
    return text if len(text) <= width else "…" + text[-(width - 1):]


TROUBLE_STATES = ("stale", "blocked", "rig down", "waiting for board")


def cert_status(snapshot: dict, at: datetime) -> list[dict]:
    """Certificates that are expired or within CERT_WARN_S of expiring, with the line the printouts show."""
    items = []
    for rig, record in sorted((snapshot.get("certs") or {}).items()):
        expires = parse_iso(record.get("expires_at"))
        if expires is None:
            continue
        left = (expires - at).total_seconds()
        if left <= 0:
            line = f"{rig}: SSH certificate expired {clock(record['expires_at'], at)} — renew it with your 2FA login; the supervisor resumes by itself"
        elif left <= CERT_WARN_S:
            line = f"{rig}: SSH certificate expires {clock(record['expires_at'], at)} (in {humanize(left)}) — renew it with your 2FA login"
        else:
            continue
        items.append({"kind": "cert", "rig": rig, "expires_at": record["expires_at"], "expired": left <= 0, "seconds_left": left, "line": line})
    return items


def cert_warnings(snapshot: dict, at: datetime) -> list[str]:
    return [item["line"] for item in cert_status(snapshot, at)]


def brief_view(snapshot: dict, at: datetime | None = None) -> dict:
    """The brief printout as data: the wave ETA, the tally, one entry per rig (when it falls free and
    which finishes last) and the warnings. `render_brief` prints it and the rig-board viewer shows it,
    so the chat and the page never disagree."""
    at = at or now()
    counts, total = snapshot["counts"], snapshot["total"]
    if snapshot.get("finished_at"):
        eta = {"kind": "finished", "at": snapshot["finished_at"], "seconds": None, "basis": None}
    elif moment := parse_iso(snapshot.get("wave_eta")):
        eta = {"kind": "eta", "at": snapshot["wave_eta"], "seconds": (moment - at).total_seconds(), "basis": snapshot.get("wave_eta_basis")}
    else:
        eta = {"kind": "unavailable", "at": None, "seconds": None, "basis": snapshot.get("wave_eta_basis")}
    info = snapshot.get("rigs") or {}
    grouped: dict[str, list[dict]] = {rig: [] for rig in info}
    for row in snapshot["lanes"]:
        grouped.setdefault(row["rig"], []).append(row)
    free = {rig: max((parse_iso(r["lane_free_at"]) for r in rows if r["lane_free_at"]), default=None) for rig, rows in grouped.items()}
    last = max((moment for moment in free.values() if moment), default=None)
    rigs = []
    for rig, rows in grouped.items():
        slurm = (info.get(rig) or {}).get("kind") == "slurm" or any(r["kind"] == "job" for r in rows)
        moment = free[rig]
        # a Slurm rig never "falls free": it takes new jobs until the queue is empty
        running = sum(r["state"] in ("running", "stale") for r in rows) if slurm else None
        rigs.append({
            "rig": rig, "kind": "slurm" if slurm else "lanes", "units": len(rows),
            "unit": ("job" if slurm else "GPU") + ("s" if len(rows) != 1 else ""),
            "done": (info.get(rig) or {}).get("done", sum(r["done"] for r in rows)),
            "running": running, "waiting": len(rows) - running if slurm else None,
            "free_at": iso(moment) if moment and not slurm else None,
            "last_to_finish": bool(moment and moment == last and not snapshot.get("finished_at")),
        })
    warnings: list[dict] = []
    for row in snapshot["lanes"]:
        where = {"rig": row["rig"], "gpu": row["gpu"], "state": row["state"], "run": row["run"]}
        if row.get("problem"):
            warnings.append({"kind": "lane", **where, "problem": row["problem"], "line": f"{row['rig']} gpu{row['gpu']}: {row['problem']}"})
        elif row["state"] in TROUBLE_STATES:
            line = f"{row['rig']} gpu{row['gpu']} {row['state']}" + (f" (run {row['run']})" if row["run"] else "")
            warnings.append({"kind": "lane", **where, "problem": None, "line": line})
    if snapshot["failed"]:
        names = ", ".join(f"`{item['run']}`" for item in snapshot["failed"][:3])
        more = f" and {len(snapshot['failed']) - 3} more" if len(snapshot["failed"]) > 3 else ""
        warnings.append({"kind": "failed", "runs": snapshot["failed"], "line": f"failed: {names}{more}"})
    warnings += cert_status(snapshot, at)
    for q in snapshot["questions"]:
        warnings.append({
            "kind": "question", "id": q["id"], "text": q["text"], "meanwhile": q.get("meanwhile"), "asked_at": q.get("asked_at"),
            "line": f"needs you [{q['id']}] {q['text']} — meanwhile: {q.get('meanwhile') or 'status quo'}",
        })
    return {
        "eta": eta, "done": counts["done"], "failed": counts.get("failed", 0), "total": total,
        "fraction": counts["done"] / total if total else None, "rigs": rigs, "warnings": warnings,
    }


def render_brief(snapshot: dict) -> str:
    """The chat printout: the wave ETA first, one line per rig only when there are several, and a
    warning line only for what needs a look. An open question is only named by its id: its text went
    out once with `ask`, and repeating it on every refresh buries the status. The per-GPU table stays
    in `table --full` and table.md."""
    at = now()
    view = brief_view(snapshot, at)
    eta = view["eta"]
    if eta["kind"] == "finished":
        head = f"finished {clock(eta['at'], at)}"
    elif eta["kind"] == "eta":
        head = f"ETA {clock(eta['at'], at)} (~{humanize(eta['seconds'])})"
    else:
        head = f"ETA unavailable: {eta['basis']}"
    tally = f"{view['done']}/{view['total']} done" + (f" · {view['failed']} failed" if view["failed"] else "")
    lines = [f"Status written {at.strftime('%Y-%m-%d at %H:%M')} — {head} · {tally}", bar(view["fraction"])]
    if len(view["rigs"]) > 1:
        width = max(len(item["rig"]) for item in view["rigs"])
        for item in view["rigs"]:
            if item["kind"] == "slurm":
                when = f"{item['running']} running, {item['waiting']} waiting" if item["units"] else "no jobs in flight"
            else:
                when = f"free {clock(item['free_at'], at)}" if item["free_at"] else "idle"
            mark = "  ← last to finish" if item["last_to_finish"] else ""
            lines.append(f"  {item['rig'].ljust(width)}  {item['units']} {item['unit']} · {item['done']} done · {when}{mark}")
    warnings = [f"⚠ {item['line']}" for item in view["warnings"] if item["kind"] != "question"]
    if asked := [item["id"] for item in view["warnings"] if item["kind"] == "question"]:
        warnings.append(f"❓ waiting on you: {', '.join(asked)}")
    if warnings:
        lines += [""] + warnings
    return "\n".join(lines) + "\n"


TABLE_COLUMNS = ("rig", "gpu", "state", "run", "progress", "hb", "run ETA", "next", "done", "lane free", "basis")


def render_table(snapshot: dict, fmt: str) -> str:
    """The status report. It opens the way every status message does: `Status written YYYY-MM-DD at HH:MM —`,
    hub local time, written for a person glancing at it rather than for a parser."""
    at = now()
    written = parse_iso(snapshot["written_at"]) or at
    counts = snapshot["counts"]
    agent = snapshot["agent"]
    tick_age = humanize((at - parse_iso(snapshot["agent_ticked_at"])).total_seconds()) + " ago" if parse_iso(snapshot.get("agent_ticked_at")) else "never"
    backlog = "; ".join(f"{rig}: {(info or {}).get('summary', 'no pass yet')}" for rig, info in sorted(snapshot["offload"].items())) or "off"
    eta = f"ETA {clock(snapshot['wave_eta'], at)} (estimated)" if snapshot["wave_eta"] else f"ETA unavailable: {snapshot['wave_eta_basis']}"
    if snapshot.get("finished_at"):
        eta = f"finished {clock(snapshot['finished_at'], at)}"
    header = (
        f"Status written {at.strftime('%Y-%m-%d at %H:%M')} — wave `{snapshot['wave_id']}` · {snapshot['experiment']} · "
        f"**done {counts['done']} · running {counts['running']} · queued {counts['queued'] + counts['assigned']} · "
        f"failed {counts['failed']}** of {snapshot['total']} · wave {eta} · "
        f"supervisor cycle {humanize((at - written).total_seconds())} ago · agent tick {tick_age} "
        f"({agent['model']}/{agent['effort']}) · offload {backlog} · open questions {len(snapshot['questions'])}"
    )
    rows = []
    for row in snapshot["lanes"]:
        rows.append(
            (
                row["rig"], row["gpu"], row["state"], short(row["run"]), bar(row["fraction"]) if row["fraction"] is not None else short(row["progress_text"], 16),
                humanize(row["heartbeat_age_s"]), clock(row["run_eta"], at), short(row["next"], 28), str(row["done"]),
                clock(row["lane_free_at"], at), row["basis"] or "—",
            )
        )
    lines = [header, ""]
    if fmt == "md":
        lines.append("| " + " | ".join(TABLE_COLUMNS) + " |")
        lines.append("|" + "|".join("---" for _ in TABLE_COLUMNS) + "|")
        lines.extend("| " + " | ".join(cell.replace("|", "\\|") for cell in row) + " |" for row in rows)
    else:
        widths = [max(len(TABLE_COLUMNS[i]), *(len(row[i]) for row in rows)) if rows else len(TABLE_COLUMNS[i]) for i in range(len(TABLE_COLUMNS))]
        lines.append("  ".join(name.ljust(widths[i]) for i, name in enumerate(TABLE_COLUMNS)))
        lines.extend("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)) for row in rows)
    problems = [f"{row['rig']} gpu{row['gpu']}: {row['problem']}" for row in snapshot["lanes"] if row.get("problem")]
    problems += cert_warnings(snapshot, at)
    if problems:
        lines += ["", "**Problems**"] + [f"- {text}" for text in problems]
    if snapshot["failed"]:
        lines += ["", "**Failed runs**"] + [f"- `{item['run']}` — {item['note'] or 'see its log'}" for item in snapshot["failed"][:10]]
        if len(snapshot["failed"]) > 10:
            lines.append(f"- … and {len(snapshot['failed']) - 10} more")
    if snapshot["questions"]:
        lines += ["", "**Needs you** (answer in chat; nothing is waiting on you to keep running)"]
        lines += [f"- [{q['id']}] {q['text']} — until answered: {q.get('meanwhile') or 'status quo'}" for q in snapshot["questions"]]
    if snapshot["decisions"]:
        lines += ["", "**Latest supervisor decisions**"] + snapshot["decisions"]
    return "\n".join(lines) + "\n"


# ───────────────────────────── watch ─────────────────────────────
#
# A chat's way to follow a wave without a timer: one blocking command, run in the background,
# that returns only when there is something to say. It reads local files only (the snapshot the
# service writes each cycle, the queue, the follow-up records), never a rig. A wave replaced by an
# automatic fix round is followed into the wave that replaces it, linked by that wave's `fixes_wave`.


def wave_warnings(wave: Wave, snapshot: dict | None, at: datetime, board_config=None) -> list[dict]:
    """Every warning a person should see for this wave right now, each with a stable `key`: the
    printout's `⚠` lines (from `brief_view`, one per failed run), a follow-up that failed or ended
    without a report, and a service that stopped writing snapshots. A key names the condition, not
    its wording, so a countdown in a line never makes an old warning look new."""
    items: list[dict] = []
    if snapshot:
        for item in brief_view(snapshot, at)["warnings"]:
            kind = item["kind"]
            if kind == "failed":
                items += [
                    {"key": f"failed:{run['run']}", "line": f"failed: `{run['run']}`" + (f" ({run['note']})" if run.get("note") else "")}
                    for run in item["runs"]
                ]
            elif kind == "lane":
                items.append({"key": f"lane:{item['rig']}:{item['gpu']}:{item['state']}:{item['run']}", "line": item["line"]})
            elif kind == "cert":
                items.append({"key": f"cert:{item['rig']}:{'expired' if item['expired'] else 'expiring'}:{item['expires_at']}", "line": item["line"]})
            elif kind == "question":
                items.append({"key": f"question:{item['id']}", "line": item["line"]})
            else:
                items.append({"key": f"{kind}:{item['line']}", "line": item["line"]})
        written = parse_iso(snapshot.get("written_at"))
        if written and not snapshot.get("finished_at") and (at - written).total_seconds() > SERVICE_SILENT_S:
            items.append({"key": "service:silent", "line": f"no supervisor cycle for {humanize((at - written).total_seconds())}: is the sweep-supervisor service running?"})
    else:
        registered = parse_iso((board.read_json(wave.queue_path) or {}).get("registered_at"))
        if registered and (at - registered).total_seconds() > SERVICE_SILENT_S:
            items.append({"key": "service:silent", "line": f"no first supervisor cycle {humanize((at - registered).total_seconds())} after registration: is the sweep-supervisor service running?"})
    if board_config is not None:
        for _path, record in followup_records(board_config):
            if record.get("wave_id") != wave.wave_id or record.get("project_root") != str(wave.project_root):
                continue
            if record.get("status") == "failed" or (record.get("status") == "done" and not record.get("report")):
                items.append({
                    "key": f"followup:{record['id']}",
                    "line": f"follow-up {record['kind']} {record.get('outcome') or record['status']} without a report" + (f"; log {record['log']}" if record.get("log") else ""),
                })
    return items


def wave_over(wave: Wave, snapshot: dict | None) -> bool:
    queue = board.read_json(wave.queue_path) or {}
    return bool((snapshot or {}).get("finished_at") or queue.get("finished_at"))


def unannounced(wave: Wave, items: list[dict], at: datetime) -> list[dict]:
    """The warnings not announced yet, now recorded in the wave's `watch.json`; one that cleared is forgotten."""
    state_path = wave.dir / "watch.json"
    current = {item["key"]: item for item in items}
    with (wave.dir / ".watch.lock").open("a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        state = board.read_json(state_path) or {}
        announced = set(state.get("announced", []))
        new = [item for key, item in current.items() if key not in announced]
        keep = (announced & set(current)) | {item["key"] for item in new}
        if keep != announced:
            board.write_json(state_path, {**state, "announced": sorted(keep), "at": iso(at)})
    return new


def first_time(wave: Wave, key: str, at: datetime) -> bool:
    """Whether this step of a fix round (`fix:<id>`, `round:<wave>`) is told for the first time; it is
    recorded in the wave's `watch.json` and, unlike a warning, never forgotten."""
    state_path = wave.dir / "watch.json"
    with (wave.dir / ".watch.lock").open("a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        state = board.read_json(state_path) or {}
        if key in state.get("rounds", []):
            return False
        board.write_json(state_path, {**state, "rounds": sorted({*state.get("rounds", []), key}), "at": iso(at)})
    return True


def fix_round_after(wave: Wave) -> Wave | None:
    """The wave an automatic fix round dispatched to replace this one (its `fixes_wave`), the first when several."""
    for path in sorted(wave.dir.parent.glob("*/queue.json")):
        if path.parent.name > wave.wave_id and (board.read_json(path) or {}).get("fixes_wave") == wave.wave_id:
            return Wave(wave.project_root, path.parent.name)
    return None


def fix_round(wave: Wave) -> object:
    return (board.read_json(wave.queue_path) or {}).get("fix_depth")


def latest_fix(wave: Wave, board_config) -> dict | None:
    fixes = [
        record for _path, record in (followup_records(board_config) if board_config is not None else [])
        if record.get("kind") == "fix" and record.get("wave_id") == wave.wave_id and record.get("project_root") == str(wave.project_root)
    ]
    return fixes[-1] if fixes else None


def closing_tick_done(wave: Wave, queue: dict, snapshot: dict | None, board_config, at: datetime) -> bool:
    """Whether the agent has had its say on an ended wave: `finish` ran, or an agent tick that started
    after the end has ended. That tick is where a `fix` follow-up gets queued; a service that never
    runs it is not waited on past WATCH_SETTLE_S."""
    ended = parse_iso(queue.get("finished_at") or (snapshot or {}).get("finished_at"))
    if board_config is None or ended is None or not index_path(board_config, queue["project"], wave.wave_id).exists():
        return True
    runtime = board.read_json(wave.runtime_path) or {}
    started, ticked = parse_iso(runtime.get("agent_tick_started_at")), parse_iso(runtime.get("agent_ticked_at"))
    if started and ticked and ended <= started <= ticked:
        return True
    return (at - ended).total_seconds() > WATCH_SETTLE_S


def watch(wave: Wave, board_config, poll: float = WATCH_POLL_S, timeout: float = WATCH_TIMEOUT_S) -> int:
    """Block until this wave has a warning not announced yet, or is finished, or `timeout` passes;
    then print why, followed by the brief printout. What was announced lives in the wave's
    `watch.json`, so running it again does not repeat a warning; a warning that clears is forgotten
    and fires again if it comes back. A file caught mid-write is simply read again next poll.

    A wave that ends with runs not done is finished only once its closing agent tick is over. A `fix`
    follow-up queued for it returns once (`ended: …; fix follow-up … running`) and keeps the wave
    open; the wave it dispatches returns once (`continuing as wave …`) and is followed from then on,
    warnings and end included, also by a watch run on the old wave. Each step returns rather than
    being printed along the way, because a chat sees a background command's output when it ends. A
    fix that dispatches nothing ends the watch with its report."""
    if not wave.queue_path.exists():
        raise SupervisorError(f"wave {wave.wave_id} is not registered here: {wave.queue_path}")
    deadline = time.monotonic() + timeout
    started = wave.wave_id
    while True:
        at = now()
        snapshot = board.read_json(wave.snapshot_path)
        reasons: list[str] = []
        try:
            over = wave_over(wave, snapshot)
            new = unannounced(wave, wave_warnings(wave, snapshot, at, board_config), at)
            if over:
                queue = board.read_json(wave.queue_path) or {}
                fix, child = latest_fix(wave, board_config), fix_round_after(wave)
                if child is not None:
                    told = first_time(wave, f"round:{child.wave_id}", at)
                    wave = child
                    if told:
                        snapshot = board.read_json(wave.snapshot_path)
                        print(f"watch: continuing as wave {wave.wave_id} (fix round {fix_round(wave)}); run watch --wave {wave.wave_id} next")
                        if snapshot:
                            with contextlib.suppress(KeyError, TypeError, ValueError, AttributeError):
                                print("\n" + render_brief(snapshot), end="")
                        return 0
                    continue
                if fix and fix.get("status") in ("pending", "running"):
                    over = False
                    if first_time(wave, f"fix:{fix['id']}", at):
                        done = sum(run["state"] == "done" for run in queue["runs"])
                        failed = sum(run["state"] == "failed" for run in queue["runs"])
                        reasons = [f"watch: wave {wave.wave_id} ended: {done} done, {failed} failed; fix follow-up {fix['id']} "
                                   f"{'queued' if fix['status'] == 'pending' else 'running'}; run watch again for the wave it dispatches"]
                elif not all(run["state"] == "done" for run in queue["runs"]) and not closing_tick_done(wave, queue, snapshot, board_config, at):
                    over = False
                elif fix:
                    report = (fix.get("report") or "").strip().splitlines()[:1]
                    ending = report[0] if report else f"{fix.get('outcome') or fix['status']} without a report" + (f"; log {fix['log']}" if fix.get("log") else "")
                    reasons = [f"watch: fix follow-up {fix['id']} ended without a new wave: {ending}"]
        except (KeyError, TypeError, ValueError, AttributeError, OSError, board.BoardError):
            over, new = False, []  # a snapshot from an older script, or a file mid-write: look again next poll
        if over:
            reasons += ["watch: wave finished"]
        elif new:
            reasons = [f"watch: new ⚠ {item['line']}" for item in new] + reasons
        elif not reasons and time.monotonic() >= deadline:
            reasons = ["watch: timeout, nothing new; run watch again"]
        if reasons:
            if wave.wave_id != started:
                reasons.insert(0, f"watch: following wave {wave.wave_id} (fix round {fix_round(wave)} after wave {started})")
            print("\n".join(reasons))
            if snapshot:
                with contextlib.suppress(KeyError, TypeError, ValueError, AttributeError):
                    print("\n" + render_brief(snapshot), end="")
            return 0
        time.sleep(max(0.05, min(poll, deadline - time.monotonic())))


# ───────────────────────────── agent wake-up ─────────────────────────────


def agent_prompt(wave: Wave, reason: str) -> str:
    return (
        f"Use the sweep-supervisor skill and run one supervisor tick for wave {wave.wave_id} of the research project "
        f"in the current directory. State directory: {wave.dir}. Wake reason: {reason}. "
        "You act only within the pool and standing authorization written in the wave README. "
        "You are unattended: never ask a blocking question, use the supervisor's ask command instead, "
        "record every decision with its decide command, and finish the tick."
    )


def chat_prompt(wave: Wave, messages: list[dict], resumed: bool) -> str:
    """The user's words from the phone, for the session that last spoke to them (or a fresh one)."""
    lines = []
    for item in messages:
        about = f" (answering your question {item['question']})" if item.get("question") else ""
        lines.append(f"«{item['text']}»{about}")
    head = (
        "" if resumed else
        f"Use the sweep-supervisor skill for wave {wave.wave_id} of the research project in the current directory. "
        f"State directory: {wave.dir}. "
    )
    return head + (
        f"The user wrote to you from the phone about wave {wave.wave_id}: " + " ".join(lines) + ". "
        + ("Time has passed since your last turn: " if resumed else "")
        + "read the supervisor's ledger and snapshot first, and handle any new events there as a tick would. "
        "Act on the message as the sweep-supervisor skill allows. These are the user's own words: they grant what its "
        "Authority section reserves for the user, so quote them in the --reason (\"user: ...\") or --user-words of every "
        "command they authorize. When they answer an open question, record that with the supervisor's answer command. "
        f"Reply with the supervisor's `say --wave {wave.wave_id} --text \"...\"`: it is the only way your words reach "
        "the user. Keep it short plain text: what you did or found, or the one thing you need to know. Never wait for a "
        "reply; record every decision with decide, then finish."
    )


def agent_argv(profile: AgentProfile, prompt: str, resume: str | None = None) -> tuple[list[str], str | None]:
    """The argv for one agent run and the session it will have: `resume_command` on a recorded session,
    else `command` with a fresh id when it takes one (`{session}`), else unknown until its log says."""
    session = resume or (str(uuid.uuid4()) if any("{session}" in part for part in profile.command) else None)
    template = profile.resume if resume else profile.command
    substitutions = {"prompt": prompt, "model": profile.model, "effort": profile.effort, "session": session or ""}
    return [part.format(**substitutions) for part in template], session


def run_agent(wave: Wave, profile: AgentProfile, prompt: str, log_path: Path, timeout: int, header: str,
              resume: str | None = None) -> tuple[str, str | None]:
    """One headless agent run in the project root, logged; returns (outcome, session id or None)."""
    command, session = agent_argv(profile, prompt, resume)
    environment = {**os.environ, "SWEEP_SUPERVISOR_MODEL": profile.model, "SWEEP_SUPERVISOR_EFFORT": profile.effort}
    if session:
        environment["SWEEP_SUPERVISOR_SESSION"] = session
    with log_path.open("w") as log:
        log.write(f"# {header}\n# {shlex.join(command[:-1])} <prompt>\n")
        log.flush()
        try:
            result = subprocess.run(command, cwd=wave.project_root, stdout=log, stderr=subprocess.STDOUT, timeout=timeout, env=environment, check=False)
            outcome = f"exit {result.returncode}"
        except subprocess.TimeoutExpired:
            outcome = f"timed out after {timeout}s"
        except OSError as exc:
            outcome = f"could not start: {exc}"
    if session is None and profile.session_regex:
        with contextlib.suppress(OSError):
            if match := re.search(profile.session_regex, log_path.read_text(errors="replace")):
                session = match.group(1)
    return outcome, session


def spoke_since(wave: Wave, started: datetime) -> bool:
    """Whether an agent reached the user (`say` or `ask`) since `started`."""
    return any(
        record.get("event") in ("say", "ask") and (parse_iso(record.get("at")) or started) >= started
        for record in wave.ledger()
    )


def wake(wave: Wave, settings: Settings, reason: str, chat: list[tuple[Path, dict]] | None = None) -> int:
    """Run one headless agent on this wave. Single-flight: a tick in progress is never doubled.
    With `chat` (phone messages), the run resumes the session the user is talking to when the profile
    can (`resume_command`), and always ends with a reply reaching the phone."""
    profile = require_agent(settings)
    wave.agent_logs.mkdir(parents=True, exist_ok=True)
    lock = (wave.dir / "agent.lock").open("a+")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print(f"[wake] wave {wave.wave_id}: an agent tick is already running; skipped")
        lock.close()
        return 0
    try:
        started = now()
        with WaveLock(wave):
            runtime = wave.runtime()
            runtime["agent_tick_started_at"] = iso(started)
            wave.save_runtime(runtime)
        messages = [record for _path, record in chat or []]
        for path, record in chat or []:
            board.write_json(path, {**record, "status": "running", "started_at": iso(started)})
        target = None
        if messages and profile.resume:
            target = next((m["session"] for m in messages if m.get("session")), None) or runtime.get("chat_session") or runtime.get("agent_session")
        prompt = chat_prompt(wave, messages, resumed=bool(target)) if messages else agent_prompt(wave, reason)
        stamp = started.strftime("%Y%m%d-%H%M%S")
        log_path = wave.agent_logs / (f"{stamp}-chat.log" if messages else f"{stamp}.log")
        if messages:
            receipt(settings, wave, messages, f"🤖 working since {started.strftime('%H:%M')} "
                    + ("(resuming its conversation)" if target else "(a fresh agent)"))
        outcome, session = run_agent(wave, profile, prompt, log_path, settings.agent_timeout, f"{iso(started)} reason: {reason}", target)
        if messages and target and outcome != "exit 0" and not outcome.startswith("timed out") and not spoke_since(wave, started):
            # the recorded session could not be resumed (expired, moved): the same words to a fresh agent
            outcome, session = run_agent(wave, profile, chat_prompt(wave, messages, resumed=False), log_path.with_name(f"{stamp}-chat-fresh.log"),
                                         settings.agent_timeout, f"{iso(started)} reason: {reason} (fresh: resuming {target} failed)")
        spoke = spoke_since(wave, started)
        with WaveLock(wave):
            runtime = wave.runtime()
            runtime.update(agent_ticked_at=iso(now()), agent_last_result=outcome, agent_last_log=str(log_path))
            if session:
                runtime["agent_session"] = session
                if spoke or messages:
                    runtime["chat_session"] = session  # where the user's next plain message goes
            wave.save_runtime(runtime)
            wave.log("agent-tick", reason=reason, outcome=outcome, model=profile.model, effort=profile.effort, session=session)
        for path, record in chat or []:
            board.write_json(path, {**record, "status": "done" if outcome == "exit 0" else "failed", "outcome": outcome,
                                    "session": session, "ended_at": iso(now()), "log": str(log_path)})
        if messages:
            took = humanize((now() - started).total_seconds())
            receipt(settings, wave, messages, f"✓ answered in {took}" if spoke else
                    f"✗ ended without answering ({outcome}, after {took}) · log {log_path}")
        print(f"[wake] wave {wave.wave_id}: {outcome} ({log_path})")
        return 0 if outcome == "exit 0" else 1
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()


def wake_due(wave: Wave, settings: Settings) -> str | None:
    """Why an agent tick is due now, or None. Events are read from the ledger, so an answer typed in
    a chat and a failure seen while a tick was still running both count."""
    runtime = wave.runtime()
    cursor = parse_iso(runtime.get("agent_tick_started_at"))
    urgent = sorted(
        {
            record["event"]
            for record in wave.ledger()
            if record.get("event") in URGENT_EVENTS and (cursor is None or (parse_iso(record.get("at")) or now()) >= cursor)
        }
    )
    if urgent:
        return "event: " + ", ".join(urgent)
    last = parse_iso(runtime.get("agent_ticked_at"))
    if last is None or (now() - last).total_seconds() >= settings.tick_every:
        return "scheduled tick"
    return None


# Notices that need nothing from the user arrive without a sound; the supervisor is already on them.
SILENT_EVENTS = frozenset({"rig-down", "run-failed"})


def notice_context(wave: Wave | None, kind: str, *, session: str | None = None, question: str | None = None,
                   body: str | None = None, silent: bool = False, project: str | None = None) -> dict:
    """What a notice is about, for a sink that keeps waves apart and routes the user's replies back
    (`{context}` in `notify_command`, JSON): the wave, the agent session that wrote it, the question."""
    context: dict = {"kind": kind, "silent": silent}
    if wave is not None:
        context.update(project=project or wave.project_root.name, root=str(wave.project_root), wave=wave.wave_id)
    for key, value in (("session", session), ("question", question), ("body", body)):
        if value:
            context[key] = value
    return context


def receipt(settings: Settings, wave: Wave, messages: list[dict], line: str) -> None:
    """Where the user's phone messages stand (`📨 received` is the bridge's, then working, answered or
    failed with its log): one line the bridge keeps editing under the user's message, ending with the
    message id, so anything that went wrong can be looked up on the hub."""
    ids = [message["id"] for message in messages]
    body = f"{line} · id {ids[-1]}"
    context = {**notice_context(wave, "receipt", body=body, silent=True), "receipts": ids}
    notify(settings, f"{wave.project_root.name} wave {wave.wave_id}: {body}", context)


def notify(settings: Settings, text: str, context: dict | None = None) -> None:
    if not settings.notify_command:
        return
    payload = json.dumps(context or {"kind": "notice", "silent": False}, sort_keys=True)
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        subprocess.run([part.replace("{text}", text).replace("{context}", payload) for part in settings.notify_command],
                       timeout=30, check=False, capture_output=True)


def event_notice(wave: Wave, record: dict) -> str | None:
    """The body of one notice for the user's phone, or None when the event is not worth one; a wave's
    notices are prefixed with its name by the caller. Certificate notices name no wave, so two waves
    on one cluster send one notice."""
    event, at = record.get("event"), now()
    if event == "wave-terminal":
        missed = record.get("collect_failed") or []
        return f"finished: {record.get('done')} done, {record.get('failed')} failed" + (
            f"; evaluations not collected from {', '.join(missed)}" if missed else "")
    if event == "rig-down":
        return f"{record.get('rig')} unreachable since {clock(record.get('since'), at)}"
    if event == "recovery-blocked":
        return f"{record.get('rig')} gpu{record.get('gpu')} cannot start: {short(record.get('problem'), 160)}"
    if event == "submit-failed":
        return f"submission to {record.get('rig')} failed: {short(record.get('problem'), 160)}"
    if event == "cert-expiring":
        return f"{record.get('rig')}: SSH certificate expires {clock(record.get('expires_at'), at)} — renew it with your 2FA login"
    if event == "cert-expired":
        return f"{record.get('rig')}: SSH certificate expired {clock(record.get('expired_at'), at)} — renew it with your 2FA login; supervision resumes by itself"
    return None


def notify_events(wave: Wave, settings: Settings, events: list[dict], sent: dict[str, float]) -> None:
    """Push what a person should know without any chat open; identical notices within an hour are sent once."""
    where = f"{wave.project_root.name} wave {wave.wave_id}"
    notices: list[tuple[str, dict]] = []
    for record in events:
        event = record.get("event")
        if event not in NOTIFY_EVENTS or not (body := event_notice(wave, record)):
            continue
        if event in ("cert-expiring", "cert-expired"):
            notices.append((body, notice_context(None, event)))
        else:
            notices.append((f"{where}: {body}", notice_context(wave, event, body=body, silent=event in SILENT_EVENTS)))
    failed = [record for record in events if record.get("event") in ("run-failed", "job-failed")]
    if failed:
        body = f"{len(failed)} run(s) failed; the supervisor is on it"
        notices.append((f"{where}: {body}", notice_context(wave, "run-failed", body=body, silent=True)))
    clock_now = time.time()
    for text, context in notices:
        if clock_now - sent.get(text, 0.0) < 3600:
            continue
        sent[text] = clock_now
        notify(settings, text, context)


# ───────────────────────────── follow-ups ─────────────────────────────
#
# Work a wave leads to that is too long for a tick and needs no person: re-rendering the
# experiment's plots and journaling the results when a wave finishes (`analyze`), and fixing a
# deterministic engineering error then dispatching the runs it broke (`fix`). Each is one fresh
# headless agent run by the service, one at a time per project, recorded in the board's index so
# it survives the wave being finished and the service restarting.


def followup_dir(board_config) -> Path:
    return index_dir(board_config) / "followups"


def followup_records(board_config) -> list[tuple[Path, dict]]:
    directory = followup_dir(board_config)
    if not directory.is_dir():
        return []
    return [(path, record) for path in sorted(directory.glob("*.json")) if (record := board.read_json(path))]


def find_followup(board_config, identifier: str) -> tuple[Path, dict]:
    path = followup_dir(board_config) / f"{identifier}.json"
    record = board.read_json(path)
    if not record:
        raise SupervisorError(f"no follow-up {identifier!r}")
    return path, record


def enqueue_followup(board_config, wave: Wave, queue: dict, kind: str, text: str) -> dict:
    if kind not in FOLLOWUP_KINDS:
        raise SupervisorError(f"follow-up kind must be one of {', '.join(FOLLOWUP_KINDS)}")
    depth = int(queue.get("fix_depth", 0))
    if kind == "fix" and depth >= MAX_FIX_DEPTH:
        raise SupervisorError(
            f"wave {wave.wave_id} already comes from {depth} automatic fix round(s); the next fix goes to the user with `ask`"
        )
    at = now()
    stem = f"{at.strftime('%Y%m%d-%H%M%S')}__{queue['project']}__{wave.wave_id}__{kind}"
    identifier, n = stem, 1
    while (followup_dir(board_config) / f"{identifier}.json").exists():
        n += 1
        identifier = f"{stem}-{n}"
    record = {
        "id": identifier, "kind": kind, "text": " ".join(text.split()), "status": "pending",
        "project": queue["project"], "project_root": str(wave.project_root), "wave_id": wave.wave_id,
        "experiment": queue["experiment"], "fix_depth": depth,
        "created_at": iso(at), "started_at": None, "ended_at": None, "outcome": None, "log": None, "report": None,
    }
    board.write_json(followup_dir(board_config) / f"{identifier}.json", record)
    wave.log("followup-queued", id=identifier, kind=kind, text=record["text"])
    return record


def followup_prompt(record: dict) -> str:
    wave_id, experiment, ident = record["wave_id"], record["experiment"], record["id"]
    report = (
        f"Finish with `python3 <the sweep-supervisor skill's scripts/supervisor.py> --root . followup-report --id {ident} "
        "--text \"<one line on the outcome, then every plot or wave it produced, each plot as its public URL>\"`. "
    )
    unattended = (
        f"You act only within the pool and standing authorization written in the README of wave {wave_id}. You are unattended and "
        "nobody can answer: never ask a blocking question; for anything the sweep-supervisor skill reserves for the user "
        f"(its Authority section) use the supervisor's `ask --wave {wave_id}` and carry on with everything else. Record "
        f"every decision with the supervisor's `decide --wave {wave_id}`. "
    )
    if record["kind"] == "analyze":
        return (
            f"Use the visualizations and research-journal skills for the research project in the current directory. "
            f"Unattended follow-up {ident}: wave {wave_id} of experiment {experiment} has finished ({record['text']}). "
            f"Re-render every existing plotter under visualizations/{experiment}/ (sub-experiments included) so its plots "
            "include this wave's runs. When the experiment has no plotter yet, create the smallest set of plots that answers "
            "the question its EXPERIMENTS.md section and JOURNAL.md say it tests, under the complete plot contract "
            "(titles, axis and metric text, glossary, verification). Then append the research-journal entry stating what "
            "the numbers show, without claims beyond them, and commit. " + unattended + report
        )
    return (
        f"Use the sweep-dispatch skill (with experiment-design and run-identity as it requires) for the research project "
        f"in the current directory. Unattended follow-up {ident} for wave {wave_id} of experiment {experiment}: the "
        f"supervisor diagnosed a deterministic engineering error: {record['text']} "
        "Fix it on the hub's main checkout so the code does what the experiment already intends; never change what the "
        "experiment measures, a behavior-affecting default, or the scientific contract. Re-run the experiment's smoke, "
        "commit with the journal entry, and dispatch a follow-up wave of exactly the runs this error broke (failed, dropped, "
        f"or not yet started that would hit it) inside the same pool and standing authorization, with `fix_depth` = {record['fix_depth'] + 1} and "
        f"`fixes_wave` = \"{wave_id}\" in its supervisor manifest (a chat watching wave {wave_id} follows that link). When the only fix would change the science or anything reserved for the user, dispatch "
        f"nothing: `ask --wave {wave_id}` with the diagnosis and the proposed patch. " + unattended + report
    )


def run_followup(path: Path, settings: Settings) -> None:
    """One follow-up, start to end, in a fresh headless agent; never raises."""
    record = board.read_json(path) or {}
    try:
        profile = require_agent(settings)
        wave = Wave(Path(record["project_root"]), record["wave_id"])
    except (SupervisorError, KeyError) as exc:
        record.update(status="failed", outcome=f"cannot start: {exc}", ended_at=iso(now()))
        board.write_json(path, record)
        return
    started = now()
    wave.agent_logs.mkdir(parents=True, exist_ok=True)
    log_path = wave.agent_logs / f"followup-{record['kind']}-{started.strftime('%Y%m%d-%H%M%S')}.log"
    record.update(status="running", started_at=iso(started), log=str(log_path))
    board.write_json(path, record)
    outcome, session = run_agent(wave, profile, followup_prompt(record), log_path, settings.followup_timeout,
                                 f"{iso(started)} follow-up {record['id']}")
    record = board.read_json(path) or record  # the agent's report landed here meanwhile
    record.update(status="done" if outcome == "exit 0" else "failed", outcome=outcome, ended_at=iso(now()), session=session)
    board.write_json(path, record)
    with contextlib.suppress(OSError):
        wave.log("followup-ended", id=record["id"], kind=record["kind"], outcome=outcome, session=session)
    if session:
        with contextlib.suppress(OSError), WaveLock(wave):
            runtime = wave.runtime()
            runtime["chat_session"] = session  # a reply to its report continues this follow-up's conversation
            wave.save_runtime(runtime)
    if record["status"] != "done" or not record.get("report"):
        body = f"follow-up {record['kind']} {outcome} without a report; log {log_path}"
        notify(settings, f"{record['project']} wave {record['wave_id']}: {body}", notice_context(wave, "followup", session=session, body=body, project=record["project"]))


def start_followups(board_config, settings: Settings, threads: dict[str, threading.Thread], busy: frozenset[str] = frozenset()) -> None:
    """Start every pending follow-up whose project has none running and no phone conversation in
    progress (`busy`): two agents never commit in one checkout at once."""
    for path, record in followup_records(board_config):
        if record.get("status") != "pending":
            continue
        key = str(record.get("project_root"))
        if key in busy or (key in threads and threads[key].is_alive()):
            continue
        thread = threading.Thread(target=run_followup, args=(path, settings), daemon=True)
        threads[key] = thread
        thread.start()


def requeue_interrupted_followups(board_config) -> None:
    """A follow-up still `running` when the service starts was cut off by the restart: run it again."""
    for path, record in followup_records(board_config):
        if record.get("status") == "running":
            record.update(status="pending", outcome="interrupted by a service restart; requeued")
            board.write_json(path, record)


def newer_installed_copy(script: Path) -> Path | None:
    """This same script in a newer installed version of the plugin, when running from a versioned
    install (`.../research/<X.Y.Z>/skills/...`). A release thereby reaches the running service
    without anyone re-pointing it."""
    script = script.resolve()
    if len(script.parents) < 5 or not VERSION_RE.fullmatch(script.parents[3].name):
        return None
    version_dir = script.parents[3]
    relative = script.relative_to(version_dir)

    def version(name: str) -> tuple[int, ...]:
        return tuple(int(part) for part in name.split("."))

    best: tuple[tuple[int, ...], Path] | None = None
    for sibling in version_dir.parent.iterdir():
        candidate = sibling / relative
        if VERSION_RE.fullmatch(sibling.name) and version(sibling.name) > version(version_dir.name) and candidate.is_file():
            if best is None or version(sibling.name) > best[0]:
                best = (version(sibling.name), candidate)
    return best[1] if best else None


_REJECTED_COPIES: set[Path] = set()


def maybe_self_update(settings: Settings, busy: bool) -> None:
    """Re-exec onto a newer installed copy between cycles, once its own `check` passes; never while an
    agent this process started is still running (its lock would be dropped). A copy that fails its
    check is not retried until the service restarts."""
    candidate = newer_installed_copy(Path(__file__))
    if candidate is None or busy or candidate in _REJECTED_COPIES:
        return
    probe = subprocess.run([sys.executable, str(candidate), "--registry", str(settings.registry_path), "check"],
                           capture_output=True, text=True, check=False)
    if probe.returncode != 0:
        _REJECTED_COPIES.add(candidate)
        print(f"[serve] newer copy {candidate} fails its check; staying on {Path(__file__).resolve()}", file=sys.stderr)
        return
    print(f"[serve] updating to {candidate}", flush=True)
    os.execv(sys.executable, [sys.executable, str(candidate), "--registry", str(settings.registry_path), "serve"])


# ───────────────────────────── service ─────────────────────────────


def project_config(wave: Wave, settings: Settings):
    return rigsync.load_config(wave.project_root, wave.project_root / "sync.toml", settings.registry_path)


def cycle_wave(wave: Wave, board_config, settings: Settings) -> dict:
    return Cycle(wave, project_config(wave, settings), board_config, settings).run()


def serve(board_config, settings: Settings) -> int:
    require_agent(settings)
    print(f"[serve] {Path(__file__).resolve()}: cycle every {settings.cycle_every}s, agent tick every {settings.tick_every}s", flush=True)
    wakers: dict[str, threading.Thread] = {}
    chatting: dict[str, threading.Thread] = {}  # project root -> its phone conversation in progress
    followups: dict[str, threading.Thread] = {}
    sent: dict[str, float] = {}
    requeue_interrupted_followups(board_config)
    requeue_interrupted_chats(board_config)

    def alive(threads: dict[str, threading.Thread], key: str) -> bool:
        return key in threads and threads[key].is_alive()

    def start(wave: Wave, reason: str, chat: list[tuple[Path, dict]] | None = None) -> None:
        thread = threading.Thread(target=wake, args=(wave, settings, reason, chat), daemon=True)
        wakers[wave.wave_id] = thread
        if chat:
            chatting[str(wave.project_root)] = thread
        thread.start()

    try:
        while True:
            busy = any(thread.is_alive() for thread in (*wakers.values(), *followups.values()))
            maybe_self_update(settings, busy)
            chats = pending_chats(board_config)
            for wave in active_waves(board_config):
                try:
                    outcome = cycle_wave(wave, board_config, settings)
                except (SupervisorError, rigsync.RigSyncError, board.BoardError, OSError) as exc:
                    print(f"[serve] wave {wave.wave_id}: cycle failed: {exc}", file=sys.stderr)
                    continue
                notify_events(wave, settings, outcome["events"], sent)
                if alive(wakers, wave.wave_id):
                    continue
                # a phone message is answered first, by the agent the user is talking to; it handles new events too
                chat = chats.pop((str(wave.project_root), wave.wave_id), None)
                if chat and not alive(followups, str(wave.project_root)):
                    start(wave, "phone message", chat)
                elif reason := wake_due(wave, settings):
                    start(wave, reason)
            for (root, wave_id), chat in chats.items():  # a finished wave, still talked to about its results
                if not alive(wakers, wave_id) and not alive(followups, root) and not alive(chatting, root):
                    with contextlib.suppress(SupervisorError):
                        start(Wave(Path(root), wave_id), "phone message", chat)
            start_followups(board_config, settings, followups, frozenset(root for root in chatting if alive(chatting, root)))
            time.sleep(settings.cycle_every)
    except KeyboardInterrupt:
        return 0


# ───────────────────────────── agent and chat commands ─────────────────────────────


def find_run(queue: dict, run_id: str) -> dict:
    for run in queue["runs"]:
        if run["id"] == run_id or run["token"] == run_id:
            return run
    raise SupervisorError(f"no run {run_id!r} in wave {queue['wave_id']}")


def split_csv(value: str | None) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def require_pool_rigs(queue: dict, rigs: list[str]) -> list[str]:
    unknown = [rig for rig in rigs if rig not in queue["pool"]]
    if unknown:
        raise SupervisorError(f"not in this wave's pool: {', '.join(unknown)}; the pool is what the dispatch gate approved")
    return rigs


def queue_command(wave: Wave, args: argparse.Namespace) -> int:
    with WaveLock(wave):
        queue = wave.load()
        run = find_run(queue, args.run)
        action = args.action
        if action in ("front", "back"):
            if run["state"] != "queued":
                raise SupervisorError(f"{run['id']} is {run['state']}; only a queued run can be reordered")
            queue["runs"].remove(run)
            queue["runs"].insert(0 if action == "front" else len(queue["runs"]), run)
            run.pop("front", None)
        elif action == "pin":
            run["pin"] = require_pool_rigs(queue, split_csv(args.rigs)) or None
        elif action == "exclude-rig":
            run["exclude"] = sorted(set(run["exclude"]) | set(require_pool_rigs(queue, split_csv(args.rigs))))
        elif action == "allow-rig":
            run["exclude"] = sorted(set(run["exclude"]) - set(split_csv(args.rigs)))
        elif action == "drop":
            if run["state"] in ("assigned", "running"):
                raise SupervisorError(f"{run['id']} is {run['state']}; steal it first")
            run["state"] = "excluded"
            run["note"] = args.reason
        elif action == "requeue":
            if run["state"] not in ("failed", "excluded"):
                raise SupervisorError(f"{run['id']} is {run['state']}; only a failed or dropped run is requeued")
            run.update(state="queued", lane=None, entry=None, job_id=None, note=None)
            queue["finished_at"] = None
        elif action == "constrain":
            if args.min_vram_mib is not None:
                run["min_vram_mib"] = args.min_vram_mib or None
            if args.needs is not None:
                run["needs"] = sorted(set(split_csv(args.needs)))
            if args.walltime_s is not None:
                run["walltime_s"] = args.walltime_s
        wave.save(queue)
        wave.log(f"queue-{action}", run=run["id"], rigs=getattr(args, "rigs", None), reason=args.reason)
    print(f"[queue] {action} {run['id']}")
    return 0


def data_command(wave: Wave, args: argparse.Namespace) -> int:
    if not TAG_RE.fullmatch(args.tag):
        raise SupervisorError(f"not a data tag: {args.tag!r}")
    with WaveLock(wave):
        queue = wave.load()
        require_pool_rigs(queue, [args.rig])
        ready = set(queue["ready"].get(args.rig, []))
        ready = ready | {args.tag} if args.action == "mark-ready" else ready - {args.tag}
        queue["ready"][args.rig] = sorted(ready)
        wave.save(queue)
        wave.log(f"data-{args.action}", rig=args.rig, tag=args.tag)
    print(f"[data] {args.rig}: {', '.join(queue['ready'][args.rig]) or '(nothing ready)'}")
    return 0


def add_lane(queue: dict, board_config, rig: str, gpu: str, user_words: str | None) -> None:
    """Admit one more GPU set of a pool rig. A shared rig's card beyond what we own needs the user's
    grant, quoted verbatim, exactly as at registration; nothing else about the pool changes."""
    pool = queue["pool"][rig]
    if pool["kind"] != "lanes":
        raise SupervisorError(f"{rig} runs cluster jobs, not lanes")
    if not GPU_SET_RE.fullmatch(gpu):
        raise SupervisorError(f"--gpu must be a GPU set such as \"0\" or \"0,1\"; got {gpu!r}")
    wanted = board.gpu_indices(gpu)
    taken = set().union(*(board.gpu_indices(lane) for lane in pool["lanes"]))
    if wanted & taken:
        raise SupervisorError(f"{rig} gpu {sorted(wanted & taken)} is already a lane of this wave")
    shared = board_config.rigs.get(rig)
    if shared is not None and shared.shared:
        grant = queue.setdefault("gpu_grants", {}).get(rig)
        foreign = wanted - shared.owned_gpus - set((grant or {}).get("gpus", ()))
        if foreign:
            if not user_words or not user_words.strip():
                raise SupervisorError(
                    f"{rig} is shared and gpu {sorted(foreign)} is not ours (owned: {sorted(shared.owned_gpus)}); "
                    "only the user's grant for this wave, quoted with --user-words, adds it"
                )
            words = user_words.strip() if grant is None else f"{grant['user_words']} | {user_words.strip()}"
            queue["gpu_grants"][rig] = {
                "gpus": sorted(set((grant or {}).get("gpus", ())) | foreign), "user_words": words, "date": now().date().isoformat(),
            }
    pool["lanes"].append(gpu)


def lane_command(wave: Wave, board_config, args: argparse.Namespace) -> int:
    with WaveLock(wave):
        queue = wave.load()
        require_pool_rigs(queue, [args.rig])
        key = f"{args.rig}:{args.gpu}"
        if args.action == "add":
            add_lane(queue, board_config, args.rig, args.gpu, args.user_words)
        elif args.action == "unblock":
            queue["blocked_lanes"].pop(key, None)
        elif args.action == "offload-on":
            queue["offload"] = sorted(set(queue["offload"]) | {args.rig})
        elif args.action == "offload-off":
            queue["offload"] = sorted(set(queue["offload"]) - {args.rig})
        wave.save(queue)
        wave.log(f"lane-{args.action}", rig=args.rig, gpu=args.gpu, reason=args.reason)
    print(f"[lane] {args.action} {key}")
    return 0


def pool_command(wave: Wave, args: argparse.Namespace) -> int:
    """Change a registered cluster's tracked core-hour cap; a raised cap clears the budget stop."""
    if args.core_hours_cap <= 0:
        raise SupervisorError("--core-hours-cap must be positive")
    with WaveLock(wave):
        queue = wave.load()
        require_pool_rigs(queue, [args.rig])
        pool = queue["pool"][args.rig]
        if "core_hours_cap" not in pool:
            raise SupervisorError(f"{args.rig} is not a cluster pool entry; it has no core-hour cap")
        old = pool["core_hours_cap"]
        pool["core_hours_cap"] = float(args.core_hours_cap)
        if old is None or pool["core_hours_cap"] > old:
            queue.pop("budget_notified", None)
        wave.save(queue)
        wave.log("pool-cap", rig=args.rig, old_h=old, new_h=pool["core_hours_cap"], reason=args.reason)
    print(f"[pool] {args.rig}: core_hours_cap {old} -> {pool['core_hours_cap']}")
    return 0


def steal(wave: Wave, config, board_config, args: argparse.Namespace) -> int:
    """Take a run back from its lane. Refuses anything that is not provably this wave's own process."""
    rigsync.require_confirmation(args.dry_run, args.confirm)
    with WaveLock(wave):
        queue = wave.load()
        run = find_run(queue, args.run)
        lane = run.get("lane")
        if run["state"] not in ("assigned", "running") or not lane:
            raise SupervisorError(f"{run['id']} is {run['state']}; nothing to steal")
        machine = config.machines[lane["rig"]]
        if machine.gpus_in_job:
            raise SupervisorError("a cluster job is cancelled by id through the queue, never stolen from a lane")
        session = session_name(queue, lane["rig"], lane["gpu"])
        held = board.read_json(board_config.lane_path(lane["rig"], lane["gpu"])) or {}
        if held.get("tmux_session") != session:
            raise SupervisorError(f"{lane['rig']} gpu{lane['gpu']} is not held by this wave on the board ({held.get('tmux_session')!r}); refusing")
        directory = lane_dir(machine, queue["wave_id"], lane["gpu"])
        print(f"[{'dry-run' if args.dry_run else 'steal'}] {run['id']} from {lane['rig']} gpu{lane['gpu']} ({run['state']}): {args.reason}")
        if args.dry_run:
            return 0
        retract = sh(machine, 'mkdir -p "$1/retracted" && mv "$1/queue/$2" "$1/retracted/$2"', directory, run["entry"])
        if retract.returncode != 0:
            # it is in flight: kill only the children of the recorded wave-script pid, and only if that pid is ours
            script = (
                'pid=$(cat "$1/running/$2.pid" 2>/dev/null) || exit 4; [ -n "$pid" ] || exit 4; '
                '[ "$(ps -o user= -p "$pid" | tr -d " ")" = "$(id -un)" ] || exit 5; '
                'ps -o args= -p "$pid" | grep -Fq "$3" || exit 6; '
                'pkill -TERM -P "$pid"; exit 0'
            )
            result = sh(machine, script, directory, run["entry"], run["script"])
            if result.returncode != 0:
                reasons = {4: "no recorded pid", 5: "the pid belongs to another user", 6: "the pid is not this run's wave script"}
                raise SupervisorError(f"refusing to kill: {reasons.get(result.returncode, result.stderr.strip() or 'ownership check failed')}")
            run["stolen"] = True
        else:
            run.update(state="queued", lane=None, entry=None, front=bool(args.front))
        if args.exclude_source:
            run["exclude"] = sorted(set(run["exclude"]) | {lane["rig"]})
        wave.save(queue)
        wave.log("steal", run=run["id"], rig=lane["rig"], gpu=lane["gpu"], reason=args.reason, in_flight=bool(run.get("stolen")))
    return 0


def ask(wave: Wave, settings: Settings, args: argparse.Namespace) -> int:
    with WaveLock(wave):
        items = wave.questions()
        identifier = f"q{len(items) + 1}"
        session = os.environ.get("SWEEP_SUPERVISOR_SESSION")
        items.append({"id": identifier, "asked_at": iso(now()), "text": args.text, "meanwhile": args.meanwhile, "status": "open",
                      "answer": None, "session": session})
        wave.save_questions(items)
        wave.log("ask", id=identifier, text=args.text, session=session)
        if session:
            runtime = wave.runtime()
            runtime["chat_session"] = session
            wave.save_runtime(runtime)
    body = f"❓ [{identifier}] {args.text} — until you answer: {args.meanwhile}"
    notify(settings, f"{wave.project_root.name} wave {wave.wave_id}: {body}", notice_context(wave, "ask", session=session, question=identifier, body=body))
    print(f"[ask] {identifier}: {args.text}")
    return 0


def record_answer(wave: Wave, question: str, text: str) -> None:
    with WaveLock(wave):
        items = wave.questions()
        for item in items:
            if item["id"] == question and item["status"] == "open":
                item.update(status="answered", answer=text, answered_at=iso(now()))
                break
        else:
            raise SupervisorError(f"no open question {question!r}")
        wave.save_questions(items)
        wave.log("answer", id=question, text=text)


def answer(wave: Wave, args: argparse.Namespace) -> int:
    record_answer(wave, args.id, args.text)
    print(f"[answer] {args.id}: {args.text}")
    return 0


def say(wave: Wave, settings: Settings, args: argparse.Namespace) -> int:
    """An agent's words to the user, in the wave's thread on the phone; a reply continues this session."""
    session = os.environ.get("SWEEP_SUPERVISOR_SESSION")
    text = args.text.strip()
    with WaveLock(wave):
        wave.log("say", text=text, session=session)
        if session:
            runtime = wave.runtime()
            runtime["chat_session"] = session
            wave.save_runtime(runtime)
    notify(settings, f"{wave.project_root.name} wave {wave.wave_id}: {text}", notice_context(wave, "say", session=session, body=text))
    print("[say] sent" if settings.notify_command else "[say] recorded; no notify_command reaches the user")
    return 0


# ───────────────────────────── the user's messages from the phone ─────────────────────────────
#
# A message typed on the phone (`tell`, run by the phone bridge) waits in the board's index until the
# service can give it to an agent of its wave: the session that wrote the message it replies to, else
# the one that last spoke to the user, resumed when the profile can (`resume_command`). Kept outside
# the wave's state so a finished wave can still be talked to about its results.


def chat_dir(board_config) -> Path:
    return index_dir(board_config) / "chat"


def chat_records(board_config) -> list[tuple[Path, dict]]:
    directory = chat_dir(board_config)
    if not directory.is_dir():
        return []
    return [(path, record) for path in sorted(directory.glob("*.json")) if (record := board.read_json(path))]


def tell(wave: Wave, board_config, args: argparse.Namespace) -> int:
    if not wave.queue_path.exists():
        raise SupervisorError(f"wave {wave.wave_id} is not registered here: {wave.queue_path}")
    text = args.text.strip()
    if not text:
        raise SupervisorError("an empty message")
    if args.question:
        with contextlib.suppress(SupervisorError):  # already answered: the words still reach the agent
            record_answer(wave, args.question, text)
    at = now()
    identifier = f"{at.strftime('%Y%m%d-%H%M%S-%f')}"  # sorts in arrival order
    record = {
        "id": identifier, "project": wave.project_root.name, "project_root": str(wave.project_root), "wave_id": wave.wave_id,
        "text": text, "session": args.session, "question": args.question, "status": "pending", "created_at": iso(at),
    }
    board.write_json(chat_dir(board_config) / f"{identifier}.json", record)
    wave.log("user-message", id=identifier, text=text, session=args.session, question=args.question)
    print(f"[tell] {identifier}: queued for wave {wave.wave_id}")
    return 0


def pending_chats(board_config) -> dict[tuple[str, str], list[tuple[Path, dict]]]:
    """Waiting messages per (project root, wave): the oldest one's target session and every later
    message to that same session, so a burst of lines becomes one turn."""
    grouped: dict[tuple[str, str], list[tuple[Path, dict]]] = {}
    for path, record in chat_records(board_config):
        if record.get("status") != "pending":
            continue
        key = (str(record.get("project_root")), str(record.get("wave_id")))
        batch = grouped.setdefault(key, [])
        if not batch or batch[0][1].get("session") == record.get("session"):
            batch.append((path, record))
    return grouped


def requeue_interrupted_chats(board_config) -> None:
    for path, record in chat_records(board_config):
        if record.get("status") == "running":
            board.write_json(path, {**record, "status": "pending"})


def decide(wave: Wave, settings: Settings, args: argparse.Namespace) -> int:
    model = os.environ.get("SWEEP_SUPERVISOR_MODEL") or (settings.agent.model if settings.agent else "unknown")
    effort = os.environ.get("SWEEP_SUPERVISOR_EFFORT") or (settings.agent.effort if settings.agent else "unknown")
    wave.dir.mkdir(parents=True, exist_ok=True)
    with WaveLock(wave), wave.decisions_path.open("a") as handle:
        handle.write(f"- {iso(now())} [{model}/{effort}] {' '.join(args.text.split())}\n")
    print("[decide] recorded")
    return 0


def prune_wave(wave: Wave, settings: Settings, queue: dict, config) -> None:
    """Remove the finished wave's worktrees and unused environments, machine by machine, so one
    unreachable rig never keeps the others' disk; what is left is pruned by a later reconciliation."""
    machines = set(queue["pool"]) & set(config.machines)
    with contextlib.suppress(rigsync.RigSyncError):
        machines.add(rigsync.hub_machine(config).name)
    for rig in sorted(machines):
        result = run_rigsync(wave, settings, "prune", "--machines", rig, "--waves", wave.wave_id, "--confirm")
        if result.returncode == 0:
            wave.log("prune", rig=rig)
        else:
            wave.log("prune-failed", rig=rig, problem=(result.stderr.strip() or result.stdout.strip())[-300:])


def finish(wave: Wave, board_config, settings: Settings, args: argparse.Namespace) -> int:
    rigsync.require_confirmation(args.dry_run, args.confirm)
    queue = wave.load()
    live = [run["id"] for run in queue["runs"] if run["state"] not in TERMINAL_STATES]
    if live and not args.abandon:
        raise SupervisorError(f"{len(live)} run(s) are not terminal; pass --abandon only when the user gave the wave up")
    print(f"[{'dry-run' if args.dry_run else 'finish'}] wave {wave.wave_id}: stop supervising ({len(live)} non-terminal)")
    if args.dry_run:
        return 0
    with WaveLock(wave):
        queue = wave.load()
        # a last collect from every rig, so the hub holds every evaluation record the wave produced
        config = project_config(wave, settings)
        for rig in sorted(queue["pool"]):
            if rig in config.machines and config.machines[rig].local:
                continue
            outcome = collect_once(wave, settings, queue, rig, now())
            if not outcome["ok"]:
                problem = queue["collect_last"][rig]["problem"]
                print(f"[finish] collect from {rig} failed; its evaluation records may be missing on the hub: {problem}")
                if outcome.get("problem"):
                    wave.log("collect-failed", rig=rig, problem=problem)
        # rig-sync prune reads this mark: an abandoned wave is over too
        queue["finished_at"] = queue.get("finished_at") or iso(now())
        wave.save(queue)
        wave.log("finish", abandoned=bool(live))
    for rig, pool in queue["pool"].items():
        for gpu in pool.get("lanes", []):
            held = board.read_json(board_config.lane_path(rig, gpu)) or {}
            if held.get("tmux_session") != session_name(queue, rig, gpu):
                continue  # another wave's card, or already free
            with contextlib.suppress(board.BoardError), contextlib.redirect_stdout(io.StringIO()):
                board.release(board_config, argparse.Namespace(rig=rig, gpu=gpu, reason=f"wave {wave.wave_id} finished"))
    record_finished(board_config, wave, queue)
    index_path(board_config, queue["project"], wave.wave_id).unlink(missing_ok=True)
    if not live:
        # a finished wave needs nobody: its disk is freed and its results plotted and journaled unattended
        prune_wave(wave, settings, queue, config)
        done = sum(run["state"] == "done" for run in queue["runs"])
        failed = sum(run["state"] == "failed" for run in queue["runs"])
        if done and not args.no_analysis:
            record = enqueue_followup(board_config, wave, queue, "analyze", f"{done} done, {failed} failed")
            print(f"[finish] queued follow-up {record['id']}: plots and journal for this wave's results")
    return 0


# ───────────────────────────── cli ─────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="research project root on the hub")
    parser.add_argument("--registry", type=Path, default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    def wave_parser(name: str, **kwargs) -> argparse.ArgumentParser:
        item = sub.add_parser(name, **kwargs)
        item.add_argument("--wave", required=True)
        return item

    def confirmable(item: argparse.ArgumentParser) -> None:
        item.add_argument("--dry-run", action="store_true")
        item.add_argument("--confirm", action="store_true")

    sub.add_parser("check", help="verify the registry declares the agent profile and the board")
    register_p = wave_parser("register")
    register_p.add_argument("--manifest", type=Path, required=True)
    confirmable(register_p)
    sub.add_parser("serve")
    sub.add_parser("waves")
    wave_parser("cycle")
    table_p = wave_parser("table")
    table_p.add_argument("--format", choices=("md", "text"), default="md")
    table_p.add_argument("--full", action="store_true", help="the per-GPU table instead of the brief chat printout")
    table_p.add_argument("--cycle", action="store_true", help="run one cycle first instead of reading the last snapshot")
    watch_p = wave_parser("watch", help="block until a new warning, the wave's end, or the timeout; then print why and the printout")
    watch_p.add_argument("--poll", type=float, default=WATCH_POLL_S, help=f"seconds between reads of the wave's files (default {WATCH_POLL_S})")
    watch_p.add_argument("--timeout", type=float, default=WATCH_TIMEOUT_S, help=f"seconds before giving up with nothing new (default {WATCH_TIMEOUT_S})")
    wave_parser("snapshot")
    ledger_p = wave_parser("ledger")
    ledger_p.add_argument("--since", help="ISO time; default is the last agent tick")
    queue_p = wave_parser("queue")
    queue_p.add_argument("action", choices=("front", "back", "pin", "exclude-rig", "allow-rig", "drop", "requeue", "constrain"))
    queue_p.add_argument("--run", required=True)
    queue_p.add_argument("--rigs")
    queue_p.add_argument("--min-vram-mib", type=int)
    queue_p.add_argument("--needs")
    queue_p.add_argument("--walltime-s", type=int)
    queue_p.add_argument("--reason", required=True)
    data_p = wave_parser("data")
    data_p.add_argument("action", choices=("mark-ready", "unmark"))
    data_p.add_argument("--rig", required=True)
    data_p.add_argument("--tag", required=True)
    lane_p = wave_parser("lane")
    lane_p.add_argument("action", choices=("add", "unblock", "offload-on", "offload-off"))
    lane_p.add_argument("--rig", required=True)
    lane_p.add_argument("--gpu", default="0")
    lane_p.add_argument("--user-words", help="add: the user's grant for a shared rig's card beyond ours, verbatim")
    lane_p.add_argument("--reason", required=True)
    pool_p = wave_parser("pool")
    pool_p.add_argument("--rig", required=True)
    pool_p.add_argument("--core-hours-cap", type=float, required=True)
    pool_p.add_argument("--reason", required=True)
    steal_p = wave_parser("steal")
    steal_p.add_argument("--run", required=True)
    steal_p.add_argument("--reason", required=True)
    steal_p.add_argument("--front", action="store_true")
    steal_p.add_argument("--exclude-source", action="store_true")
    confirmable(steal_p)
    ask_p = wave_parser("ask")
    ask_p.add_argument("--text", required=True)
    ask_p.add_argument("--meanwhile", required=True, help="what stays in force until the user answers")
    answer_p = wave_parser("answer")
    answer_p.add_argument("--id", required=True)
    answer_p.add_argument("--text", required=True)
    say_p = wave_parser("say", help="an agent's reply to the user, pushed to the wave's thread on the phone")
    say_p.add_argument("--text", required=True)
    tell_p = wave_parser("tell", help="the user's message from the phone, for the agent of this wave (run by the phone bridge)")
    tell_p.add_argument("--text", required=True)
    tell_p.add_argument("--session", help="the agent session that wrote the message being replied to")
    tell_p.add_argument("--question", help="the open question being replied to; recorded as its answer")
    decide_p = wave_parser("decide")
    decide_p.add_argument("text")
    followup_p = wave_parser("followup", help="queue unattended work this wave leads to: analyze its results, or fix and redispatch")
    followup_p.add_argument("--kind", choices=FOLLOWUP_KINDS, required=True)
    followup_p.add_argument("--text", required=True, help="analyze: what finished; fix: the diagnosis with its evidence")
    report_p = sub.add_parser("followup-report", help="a follow-up agent's closing line, pushed to the user")
    report_p.add_argument("--id", required=True)
    report_p.add_argument("--text", required=True)
    sub.add_parser("followups", help="every follow-up and its state")
    wake_p = wave_parser("wake")
    wake_p.add_argument("--reason", default="manual")
    finish_p = wave_parser("finish")
    finish_p.add_argument("--abandon", action="store_true")
    finish_p.add_argument("--no-analysis", action="store_true", help="do not queue the results follow-up")
    confirmable(finish_p)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    registry = (args.registry or Path(os.environ.get("RIGSYNC_REGISTRY", "~/.config/rigsync/machines.toml")).expanduser()).resolve()
    try:
        settings = load_settings(registry)
        board_config = board.load_config(registry)
        if args.command == "check":
            profile = require_agent(settings)
            print(f"OK agent profile: {profile.model}/{profile.effort}")
            print(f"OK board root: {board_config.root}")
            print(f"OK cycle every {settings.cycle_every}s, agent tick every {settings.tick_every}s")
            print("OK phone replies resume the agent's session" if profile.resume else
                  "NOTE no resume_command: a phone reply reaches a fresh agent, without the conversation so far")
            return 0
        if args.command == "serve":
            return serve(board_config, settings)
        if args.command == "waves":
            for wave in active_waves(board_config):
                print(f"{wave.wave_id}\t{wave.project_root}")
            return 0
        if args.command == "followups":
            for _path, record in followup_records(board_config):
                report = (record.get("report") or record.get("outcome") or "").splitlines()[:1]
                print(f"{record['id']}\t{record['status']}\t{report[0] if report else ''}")
            return 0
        if args.command == "followup-report":
            path, record = find_followup(board_config, args.id)
            record["report"] = args.text
            board.write_json(path, record)
            context = notice_context(Wave(Path(record["project_root"]), record["wave_id"]), "followup-report", project=record["project"],
                                     session=os.environ.get("SWEEP_SUPERVISOR_SESSION") or record.get("session"), body=f"{record['kind']}: {args.text}")
            notify(settings, f"{record['project']} wave {record['wave_id']} {record['kind']}: {args.text}", context)
            print(f"[followup-report] {args.id}: recorded")
            return 0
        wave = Wave(args.root, args.wave)
        if args.command == "register":
            return register(wave, args.manifest, project_config(wave, settings), board_config, settings, args.dry_run, args.confirm)
        if args.command == "cycle":
            outcome = cycle_wave(wave, board_config, settings)
            for event in outcome["events"]:
                print(json.dumps(event, sort_keys=True))
            return 0
        if args.command == "table":
            if args.cycle:
                cycle_wave(wave, board_config, settings)
            snapshot = board.read_json(wave.snapshot_path)
            if snapshot is None:
                raise SupervisorError(f"wave {wave.wave_id} has no snapshot yet; is the supervisor service running?")
            print(render_table(snapshot, args.format) if args.full else render_brief(snapshot), end="")
            return 0
        if args.command == "watch":
            if args.poll < 1 or args.timeout < 0:
                raise SupervisorError("--poll must be at least 1 second and --timeout not negative")
            return watch(wave, board_config, args.poll, args.timeout)
        if args.command == "snapshot":
            print(json.dumps(board.read_json(wave.snapshot_path) or {}, indent=2, sort_keys=True))
            return 0
        if args.command == "ledger":
            since = parse_iso(args.since) or parse_iso(wave.runtime().get("agent_ticked_at"))
            for record in wave.ledger():
                if since is None or (parse_iso(record.get("at")) or now()) >= since:
                    print(json.dumps(record, sort_keys=True))
            return 0
        if args.command == "queue":
            return queue_command(wave, args)
        if args.command == "data":
            return data_command(wave, args)
        if args.command == "lane":
            return lane_command(wave, board_config, args)
        if args.command == "followup":
            record = enqueue_followup(board_config, wave, wave.load(), args.kind, args.text)
            print(f"[followup] queued {record['id']}; the service starts it on its next cycle")
            return 0
        if args.command == "pool":
            return pool_command(wave, args)
        if args.command == "steal":
            return steal(wave, project_config(wave, settings), board_config, args)
        if args.command == "ask":
            return ask(wave, settings, args)
        if args.command == "answer":
            return answer(wave, args)
        if args.command == "say":
            return say(wave, settings, args)
        if args.command == "tell":
            return tell(wave, board_config, args)
        if args.command == "decide":
            return decide(wave, settings, args)
        if args.command == "wake":
            return wake(wave, settings, args.reason)
        if args.command == "finish":
            return finish(wave, board_config, settings, args)
    except (SupervisorError, rigsync.RigSyncError, board.BoardError) as exc:
        print(f"supervisor: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
