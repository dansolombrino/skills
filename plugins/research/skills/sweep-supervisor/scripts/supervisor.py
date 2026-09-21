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
DEFAULT_TICK_EVERY_S = 600
DEFAULT_AGENT_TIMEOUT_S = 900
MIN_CYCLE_EVERY_S = 10
DOWN_AFTER_FAILURES = 3
HEARTBEAT_STALE_S = 180
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
        "claim-refused",
    }
)


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


@dataclass(frozen=True)
class Settings:
    registry_path: Path
    cycle_every: int
    tick_every: int
    agent_timeout: int
    agent: AgentProfile | None
    notify_command: tuple[str, ...]


def _string_list(value: object, where: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or not all(isinstance(v, str) and v for v in value):
        raise SupervisorError(f"{where} must be a non-empty list of strings")
    return tuple(value)


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
    for name, value in (("cycle_every", cycle_every), ("tick_every", tick_every), ("agent_timeout", agent_timeout)):
        if not isinstance(value, int) or isinstance(value, bool) or value < MIN_CYCLE_EVERY_S:
            raise SupervisorError(f"{registry_path}: supervisor.{name} must be an integer >= {MIN_CYCLE_EVERY_S}")
    agent = None
    raw_agent = table.get("agent")
    if raw_agent is not None:
        if not isinstance(raw_agent, dict):
            raise SupervisorError(f"{registry_path}: [supervisor.agent] must be a table")
        command = _string_list(raw_agent.get("command"), f"{registry_path}: supervisor.agent.command")
        if not any("{prompt}" in part for part in command):
            raise SupervisorError(f"{registry_path}: supervisor.agent.command must contain a {{prompt}} placeholder")
        model = raw_agent.get("model")
        effort = raw_agent.get("effort")
        if not isinstance(model, str) or not model:
            raise SupervisorError(f"{registry_path}: supervisor.agent.model is required; it is never defaulted")
        if effort not in EFFORT_VALUES:
            raise SupervisorError(f"{registry_path}: supervisor.agent.effort must be one of {', '.join(EFFORT_VALUES)}")
        agent = AgentProfile(command, model, effort)
    notify = table.get("notify_command")
    notify_command = _string_list(notify, f"{registry_path}: supervisor.notify_command") if notify is not None else ()
    return Settings(registry_path, cycle_every, tick_every, agent_timeout, agent, notify_command)


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


def build_queue(manifest: dict, project_root: Path, config, settings: Settings) -> dict:
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
            cap = entry.get("core_hours_cap")
            if max_jobs < 1 or not isinstance(cap, (int, float)) or isinstance(cap, bool) or cap <= 0:
                raise SupervisorError(f"manifest: pool.{rig} needs max_jobs >= 1 and a positive core_hours_cap")
            pool[rig] = {
                "kind": "slurm",
                "max_jobs": max_jobs,
                "core_hours_cap": float(cap),
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
            pool[rig] = {"kind": "lanes", "lanes": list(lanes)}
    offload = manifest.get("offload", [])
    if not isinstance(offload, list) or any(rig not in pool or pool[rig]["kind"] != "lanes" for rig in offload):
        raise SupervisorError("manifest: `offload` must list pool rigs that run lanes")
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
        "offload": sorted(set(offload)),
        "ready": {rig: [] for rig in pool},
        "blocked_lanes": {},
        "next_seq": 1,
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
    queue = build_queue(manifest, wave.project_root, config, settings)
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
    """Run a shell snippet on a rig (or here, for the hub) and hand back text, not bytes."""
    result = rigsync.remote(machine, ["sh", "-c", script, "supervisor", *args], check=check)
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
        """Return whether the lane's session is alive, launching or relaunching it when it should be."""
        session = session_name(queue, machine.name, gpu)
        if session in view["sessions"]:
            return True
        key = f"{machine.name}:{gpu}"
        if not has_work or key in queue["blocked_lanes"]:
            return False
        problem = self.verify_lane(queue, machine, gpu)
        if problem:
            queue["blocked_lanes"][key] = problem
            self.event("recovery-blocked", rig=machine.name, gpu=gpu, problem=problem)
            return False
        sh(machine, f'rm -f "$1/drain"', lane_dir(machine, queue["wave_id"], gpu))
        result = sh(machine, self.lane_launch_command(queue, machine, gpu))
        if result.returncode != 0:
            queue["blocked_lanes"][key] = f"tmux: {result.stderr.strip()[-200:]}"
            self.event("recovery-blocked", rig=machine.name, gpu=gpu, problem=queue["blocked_lanes"][key])
            return False
        rebooted = bool(view.get("boot_at") and parse_iso(view["boot_at"]) and parse_iso(view["boot_at"]) > (parse_iso(queue["registered_at"]) or self.at))
        launched_before = any(r.get("event") == "lane-launched" and r.get("rig") == machine.name and r.get("gpu") == gpu for r in self.wave.ledger())
        self.event("lane-relaunched" if launched_before else "lane-launched", rig=machine.name, gpu=gpu, session=session, rebooted=rebooted)
        self.board_claim(queue, machine.name, gpu, session)
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
            if spent + cost_h > pool["core_hours_cap"]:
                if not queue.get("budget_notified"):
                    queue["budget_notified"] = True
                    self.event("budget-cap", rig=rig, spent_h=round(spent, 1), cap_h=pool["core_hours_cap"])
                break
            name = f"{queue['wave_id']}__{run['id']}"
            script = f"{rigsync.WAVES_DIR}/{queue['wave_id']}/{run['script']}"
            hms = f"{walltime // 3600:02d}:{walltime % 3600 // 60:02d}:00"
            result = sh(
                machine,
                'cd "$1" && if squeue --me --noheader --format=%j | grep -Fxq "$2"; then echo ALREADY-QUEUED; '
                'else sbatch --parsable --time="$3" --export=ALL,LANE_RIG="$5",LANE_GPUS=1 "$4"; fi',
                str(machine.repo_path), name, hms, script, rig,
            )
            job_id = result.stdout.strip().split(";")[0]
            if result.returncode != 0 or not job_id.isdigit():
                self.event("claim-refused", rig=rig, run=run["id"], problem=(result.stderr or result.stdout).strip()[-300:])
                break
            run.update(state="assigned", lane={"rig": rig, "gpu": "1"}, entry=None, job_id=int(job_id), walltime_s=walltime,
                       attempts=run["attempts"] + 1, budget_h=run.get("budget_h", 0.0) + cost_h, budget_rig=rig)
            run.pop("front", None)
            self.record_job(run, job_id, "submitted" if run["attempts"] == 1 else "resubmitted")
            self.event("job-submitted", run=run["id"], rig=rig, job=int(job_id), walltime_s=walltime)
            spent += cost_h
            room -= 1

    def record_job(self, run: dict, job_id: str, verb: str) -> None:
        path = self.wave.project_root / Path(run["script"]).parent / "leonardo.jobs"
        with contextlib.suppress(OSError):
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a") as handle:
                handle.write(f"{iso(self.at)} {job_id} {verb}\n")

    # -- board ------------------------------------------------------------

    def board_claim(self, queue: dict, rig: str, gpu: str, session: str) -> None:
        args = argparse.Namespace(
            rig=rig, gpu=gpu, project=queue["project"], experiment=queue["experiment"], wave=queue["wave_id"],
            project_root=queue["project_root"], runs_total=len(queue["runs"]), tmux_session=session,
        )
        sink = io.StringIO()
        try:
            with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                code = board.claim(self.board_config, args)
        except board.BoardError as exc:
            code, sink = 2, io.StringIO(str(exc))
        if code != 0:
            self.event("claim-refused", rig=rig, gpu=gpu, problem=sink.getvalue().strip()[-300:])

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

    # -- offload ----------------------------------------------------------

    def offload(self, queue: dict, rig: str) -> None:
        command = [
            sys.executable, str(RIGSYNC_SCRIPT), "--root", str(self.wave.project_root), "--registry", str(self.settings.registry_path),
            "offload", "--machine", rig, "--wave", queue["wave_id"], "--confirm",
        ]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        summary = [line for line in result.stdout.splitlines() if line.startswith("[offload-summary]")]
        if result.returncode != 0:
            self.event("offload-failed", rig=rig, problem=(result.stderr.strip() or result.stdout.strip())[-300:])
        elif summary:
            queue.setdefault("offload_last", {})[rig] = {"at": iso(self.at), "summary": summary[-1]}

    # -- the pass ---------------------------------------------------------

    def run(self) -> dict:
        wave = self.wave
        with WaveLock(wave):
            queue = wave.load()
            runtime = wave.runtime()
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
                        if view["lanes"].get(gpu, {}).get("drain") and self.board_config.lane_path(rig, gpu).exists():
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
            terminal = all(run["state"] in TERMINAL_STATES for run in queue["runs"])
            if terminal and not queue.get("finished_at"):
                queue["finished_at"] = iso(self.at)
                self.event("wave-terminal", done=sum(r["state"] == "done" for r in queue["runs"]), failed=sum(r["state"] == "failed" for r in queue["runs"]))
            snapshot = build_snapshot(queue, runtime, wave, vram, self.at)
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
    for rig, pool in sorted(queue["pool"].items()):
        down = (runtime.get("rigs", {}).get(rig) or {}).get("down_since")
        if pool["kind"] == "slurm":
            for run in queue["runs"]:
                if (run.get("lane") or {}).get("rig") == rig and run["state"] in ("assigned", "running"):
                    rows.append(job_row(queue, run, rig, at))
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
            if down:
                state = "rig down"
            elif blocked:
                state = "blocked"
            elif active:
                state = "stale" if stale else "running"
            elif buffered:
                state = "starting"
            else:
                state = "idle"
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
    questions = [q for q in wave.questions() if q.get("status") == "open"]
    return {
        "schema_version": SCHEMA_VERSION,
        "written_at": iso(at),
        "project": queue["project"], "experiment": queue["experiment"], "wave_id": queue["wave_id"],
        "counts": counts, "total": len(queue["runs"]),
        "wave_eta": iso(at + timedelta(seconds=horizon)) if horizon is not None else None,
        "wave_eta_basis": basis,
        "finished_at": queue.get("finished_at"),
        "agent": queue["agent"],
        "agent_ticked_at": runtime.get("agent_ticked_at"),
        "agent_last_result": runtime.get("agent_last_result"),
        "cycled_at": iso(at),
        "offload": {rig: (queue.get("offload_last") or {}).get(rig) for rig in queue["offload"]},
        "lanes": rows,
        "failed": [{"run": r["id"], "note": r.get("note")} for r in queue["runs"] if r["state"] == "failed"],
        "excluded": [{"run": r["id"], "note": r.get("note")} for r in queue["runs"] if r["state"] == "excluded"],
        "questions": questions,
        "decisions": recent_decisions(wave),
    }


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
    if value is None:
        return "—"
    filled = int(round(value * width))
    return "█" * filled + "░" * (width - filled) + f" {value * 100:3.0f}%"


def short(text: str | None, width: int = 44) -> str:
    if not text:
        return "—"
    return text if len(text) <= width else "…" + text[-(width - 1):]


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


# ───────────────────────────── agent wake-up ─────────────────────────────


def agent_prompt(wave: Wave, reason: str) -> str:
    return (
        f"Use the sweep-supervisor skill and run one supervisor tick for wave {wave.wave_id} of the research project "
        f"in the current directory. State directory: {wave.dir}. Wake reason: {reason}. "
        "You are unattended: never ask a blocking question, use the supervisor's ask command instead, "
        "record every decision with its decide command, and finish the tick."
    )


def wake(wave: Wave, settings: Settings, reason: str) -> int:
    """Run one fresh headless agent on this wave. Single-flight: a tick in progress is never doubled."""
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
        substitutions = {"prompt": agent_prompt(wave, reason), "model": profile.model, "effort": profile.effort}
        command = [part.format(**substitutions) for part in profile.command]
        started = now()
        with WaveLock(wave):
            runtime = wave.runtime()
            runtime["agent_tick_started_at"] = iso(started)
            wave.save_runtime(runtime)
        log_path = wave.agent_logs / f"{started.strftime('%Y%m%d-%H%M%S')}.log"
        environment = {**os.environ, "SWEEP_SUPERVISOR_MODEL": profile.model, "SWEEP_SUPERVISOR_EFFORT": profile.effort}
        with log_path.open("w") as log:
            log.write(f"# {iso(started)} reason: {reason}\n# {shlex.join(command[:-1])} <prompt>\n")
            log.flush()
            try:
                result = subprocess.run(command, cwd=wave.project_root, stdout=log, stderr=subprocess.STDOUT, timeout=settings.agent_timeout, env=environment, check=False)
                outcome = f"exit {result.returncode}"
            except subprocess.TimeoutExpired:
                outcome = f"timed out after {settings.agent_timeout}s"
            except OSError as exc:
                outcome = f"could not start: {exc}"
        with WaveLock(wave):
            runtime = wave.runtime()
            runtime.update(agent_ticked_at=iso(now()), agent_last_result=outcome, agent_last_log=str(log_path))
            wave.save_runtime(runtime)
            wave.log("agent-tick", reason=reason, outcome=outcome, model=profile.model, effort=profile.effort)
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


def notify(settings: Settings, text: str) -> None:
    if not settings.notify_command:
        return
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        subprocess.run([part.format(text=text) for part in settings.notify_command], timeout=30, check=False, capture_output=True)


# ───────────────────────────── service ─────────────────────────────


def project_config(wave: Wave, settings: Settings):
    return rigsync.load_config(wave.project_root, wave.project_root / "sync.toml", settings.registry_path)


def cycle_wave(wave: Wave, board_config, settings: Settings) -> dict:
    return Cycle(wave, project_config(wave, settings), board_config, settings).run()


def serve(board_config, settings: Settings) -> int:
    require_agent(settings)
    print(f"[serve] cycle every {settings.cycle_every}s, agent tick every {settings.tick_every}s")
    wakers: dict[str, threading.Thread] = {}
    try:
        while True:
            for wave in active_waves(board_config):
                try:
                    outcome = cycle_wave(wave, board_config, settings)
                except (SupervisorError, rigsync.RigSyncError, board.BoardError, OSError) as exc:
                    print(f"[serve] wave {wave.wave_id}: cycle failed: {exc}", file=sys.stderr)
                    continue
                reason = wake_due(wave, settings)
                running = wakers.get(wave.wave_id)
                if reason and not (running and running.is_alive()):
                    thread = threading.Thread(target=wake, args=(wave, settings, reason), daemon=True)
                    wakers[wave.wave_id] = thread
                    thread.start()
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
        raise SupervisorError(f"not in this wave's pool: {', '.join(unknown)}; the pool is the approved envelope")
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


def lane_command(wave: Wave, args: argparse.Namespace) -> int:
    with WaveLock(wave):
        queue = wave.load()
        require_pool_rigs(queue, [args.rig])
        key = f"{args.rig}:{args.gpu}"
        if args.action == "unblock":
            queue["blocked_lanes"].pop(key, None)
        elif args.action == "offload-on":
            queue["offload"] = sorted(set(queue["offload"]) | {args.rig})
        elif args.action == "offload-off":
            queue["offload"] = sorted(set(queue["offload"]) - {args.rig})
        wave.save(queue)
        wave.log(f"lane-{args.action}", rig=args.rig, gpu=args.gpu, reason=args.reason)
    print(f"[lane] {args.action} {key}")
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
        items.append({"id": identifier, "asked_at": iso(now()), "text": args.text, "meanwhile": args.meanwhile, "status": "open", "answer": None})
        wave.save_questions(items)
        wave.log("ask", id=identifier, text=args.text)
    notify(settings, f"sweep {wave.wave_id}: {args.text}")
    print(f"[ask] {identifier}: {args.text}")
    return 0


def answer(wave: Wave, args: argparse.Namespace) -> int:
    with WaveLock(wave):
        items = wave.questions()
        for item in items:
            if item["id"] == args.id and item["status"] == "open":
                item.update(status="answered", answer=args.text, answered_at=iso(now()))
                break
        else:
            raise SupervisorError(f"no open question {args.id!r}")
        wave.save_questions(items)
        wave.log("answer", id=args.id, text=args.text)
    print(f"[answer] {args.id}: {args.text}")
    return 0


def decide(wave: Wave, settings: Settings, args: argparse.Namespace) -> int:
    model = os.environ.get("SWEEP_SUPERVISOR_MODEL") or (settings.agent.model if settings.agent else "unknown")
    effort = os.environ.get("SWEEP_SUPERVISOR_EFFORT") or (settings.agent.effort if settings.agent else "unknown")
    wave.dir.mkdir(parents=True, exist_ok=True)
    with WaveLock(wave), wave.decisions_path.open("a") as handle:
        handle.write(f"- {iso(now())} [{model}/{effort}] {' '.join(args.text.split())}\n")
    print("[decide] recorded")
    return 0


def finish(wave: Wave, board_config, args: argparse.Namespace) -> int:
    rigsync.require_confirmation(args.dry_run, args.confirm)
    queue = wave.load()
    live = [run["id"] for run in queue["runs"] if run["state"] not in TERMINAL_STATES]
    if live and not args.abandon:
        raise SupervisorError(f"{len(live)} run(s) are not terminal; pass --abandon only when the user gave the wave up")
    print(f"[{'dry-run' if args.dry_run else 'finish'}] wave {wave.wave_id}: stop supervising ({len(live)} non-terminal)")
    if args.dry_run:
        return 0
    wave.log("finish", abandoned=bool(live))
    for rig, pool in queue["pool"].items():
        for gpu in pool.get("lanes", []):
            with contextlib.suppress(board.BoardError), contextlib.redirect_stdout(io.StringIO()):
                board.release(board_config, argparse.Namespace(rig=rig, gpu=gpu, reason=f"wave {wave.wave_id} finished"))
    index_path(board_config, queue["project"], wave.wave_id).unlink(missing_ok=True)
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
    table_p.add_argument("--cycle", action="store_true", help="run one cycle first instead of reading the last snapshot")
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
    lane_p.add_argument("action", choices=("unblock", "offload-on", "offload-off"))
    lane_p.add_argument("--rig", required=True)
    lane_p.add_argument("--gpu", default="0")
    lane_p.add_argument("--reason", required=True)
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
    decide_p = wave_parser("decide")
    decide_p.add_argument("text")
    wake_p = wave_parser("wake")
    wake_p.add_argument("--reason", default="manual")
    finish_p = wave_parser("finish")
    finish_p.add_argument("--abandon", action="store_true")
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
            return 0
        if args.command == "serve":
            return serve(board_config, settings)
        if args.command == "waves":
            for wave in active_waves(board_config):
                print(f"{wave.wave_id}\t{wave.project_root}")
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
            print(render_table(snapshot, args.format), end="")
            return 0
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
            return lane_command(wave, args)
        if args.command == "steal":
            return steal(wave, project_config(wave, settings), board_config, args)
        if args.command == "ask":
            return ask(wave, settings, args)
        if args.command == "answer":
            return answer(wave, args)
        if args.command == "decide":
            return decide(wave, settings, args)
        if args.command == "wake":
            return wake(wave, settings, args.reason)
        if args.command == "finish":
            return finish(wave, board_config, args)
    except (SupervisorError, rigsync.RigSyncError, board.BoardError) as exc:
        print(f"supervisor: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
