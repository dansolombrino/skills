#!/usr/bin/env python3
"""rig-board: fleet-level GPU lane occupancy across the Research 2.0 rigs.

The board is a directory of small JSON files, one per held GPU lane, shared by every project
and every chat on the hub. It answers "is <rig> gpu<ids> free, who holds it, since when, ETA"
and nothing more. It is fleet state, never run state: `.status.json` and the expected final
artifact stay the only truth about a run.

Layout under `[board] root` from the rigsync user registry:

    lanes/<rig>/gpu<ids>.json   one file per held lane; absent = free
    rigs/<rig>.json             last probe of that rig (sessions, GPUs, boot time)
    history.jsonl               append-only claim / refresh / release / reconcile events

Honesty comes from `reconcile`, which probes each rig (tmux session list + nvidia-smi) and
corrects the board: a lane whose session is gone is released (or marked interrupted after a
reboot), a session with our naming pattern that has no lane file is adopted, and a busy GPU
nobody on the board holds is reported as foreign.
"""

from __future__ import annotations

import argparse
import fcntl
import hmac
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import threading
import tomllib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, urlparse

SCHEMA_VERSION = 1
DEFAULT_REGISTRY = "~/.config/rigsync/machines.toml"
DEFAULT_PORT = 8765
DEFAULT_RECONCILE_EVERY_S = 120
MIN_RECONCILE_EVERY_S = 10  # each tick SSHes into every rig and the Slurm login node
FOREIGN_MEMORY_MIB = 1024
ORCHESTRATOR_SILENT_S = 30 * 60
SSH_TIMEOUT_S = 25

SESSION_RE = re.compile(
    r"^(?P<prefix>.+)_(?P<wave>\d{8}-\d{6})_(?P<rig>[A-Za-z0-9][A-Za-z0-9-]*)_gpu(?P<gpu>\d+(?:,\d+)*)$"
)
EXPERIMENT_SPLIT_RE = re.compile(r"_(?=\d{3}_)")
GPU_RE = re.compile(r"^\d+(?:,\d+)*$")
WAVE_RE = re.compile(r"^\d{8}-\d{6}$")
WAVE_IN_NAME_RE = re.compile(r"\d{8}-\d{6}")
API_VERSION = 2
HISTORY_DEFAULT_HOURS = 24
HISTORY_MAX_EVENTS = 500
RECONCILE_DUPLICATE_RE = re.compile(r"adopted live session|session gone → released|→ lane interrupted")


class BoardError(Exception):
    pass


def now() -> datetime:
    return datetime.now().astimezone()


def iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


# ───────────────────────────── registry ─────────────────────────────


@dataclass(frozen=True)
class Rig:
    name: str
    ssh: str
    hostname: str | None
    shared: bool = False


@dataclass(frozen=True)
class SlurmTarget:
    name: str
    ssh: str


@dataclass(frozen=True)
class BoardConfig:
    root: Path
    rigs: dict[str, Rig]
    slurm: dict[str, SlurmTarget] = field(default_factory=dict)
    port: int = DEFAULT_PORT
    bind: str = "0.0.0.0"
    reconcile_every: int = DEFAULT_RECONCILE_EVERY_S
    registry_path: Path = Path(DEFAULT_REGISTRY)
    token: str | None = None

    @property
    def lanes_dir(self) -> Path:
        return self.root / "lanes"

    @property
    def rigs_dir(self) -> Path:
        return self.root / "rigs"

    @property
    def history_path(self) -> Path:
        return self.root / "history.jsonl"

    def lane_path(self, rig: str, gpu: str) -> Path:
        return self.lanes_dir / rig / f"gpu{gpu}.json"

    def rig_path(self, rig: str) -> Path:
        return self.rigs_dir / f"{rig}.json"


def load_config(registry_path: Path) -> BoardConfig:
    try:
        with registry_path.open("rb") as handle:
            registry = tomllib.load(handle)
    except FileNotFoundError as exc:
        raise BoardError(f"missing registry: {registry_path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise BoardError(f"invalid TOML in {registry_path}: {exc}") from exc
    board = registry.get("board")
    if not isinstance(board, dict):
        raise BoardError(f"{registry_path}: missing [board] table (root, rigs)")
    raw_root = board.get("root")
    if not isinstance(raw_root, str) or not raw_root:
        raise BoardError(f"{registry_path}: board.root must be an absolute path")
    root = Path(raw_root).expanduser()
    if not root.is_absolute():
        raise BoardError(f"{registry_path}: board.root must be absolute")
    names = board.get("rigs")
    if not isinstance(names, list) or not names or not all(isinstance(n, str) for n in names):
        raise BoardError(f"{registry_path}: board.rigs must be a non-empty list of rig names")
    shared = board.get("shared", [])
    if not isinstance(shared, list) or not all(isinstance(n, str) for n in shared):
        raise BoardError(f"{registry_path}: board.shared must be a list of rig names")
    machines = registry.get("machines", {})
    rigs: dict[str, Rig] = {}
    for name in names:
        entry = machines.get(name)
        if not isinstance(entry, dict):
            raise BoardError(f"{registry_path}: board.rigs names unknown machine {name!r}")
        ssh = entry.get("ssh")
        if not isinstance(ssh, str) or not ssh:
            raise BoardError(f"{registry_path}: machines.{name}.ssh must be set")
        hostname = entry.get("hostname")
        rigs[name] = Rig(name, ssh, hostname if isinstance(hostname, str) else None, name in shared)
    slurm_names = board.get("slurm", [])
    if not isinstance(slurm_names, list) or not all(isinstance(n, str) for n in slurm_names):
        raise BoardError(f"{registry_path}: board.slurm must be a list of machine names")
    slurm: dict[str, SlurmTarget] = {}
    for name in slurm_names:
        entry = machines.get(name)
        if not isinstance(entry, dict) or not isinstance(entry.get("ssh"), str) or not entry.get("ssh"):
            raise BoardError(f"{registry_path}: board.slurm names unknown machine {name!r} (needs machines.{name}.ssh)")
        if name in rigs:
            raise BoardError(f"{registry_path}: {name!r} cannot be both a rig and a Slurm target")
        slurm[name] = SlurmTarget(name, entry["ssh"])
    port = board.get("port", DEFAULT_PORT)
    if not isinstance(port, int) or not 1 <= port <= 65535:
        raise BoardError(f"{registry_path}: board.port must be a TCP port")
    bind = board.get("bind", "0.0.0.0")
    if not isinstance(bind, str) or not bind:
        raise BoardError(f"{registry_path}: board.bind must be a string")
    token = board.get("token")
    if token is not None and (not isinstance(token, str) or len(token) < 16):
        raise BoardError(f"{registry_path}: board.token must be a string of at least 16 characters")
    reconcile_every = board.get("reconcile_every", DEFAULT_RECONCILE_EVERY_S)
    if isinstance(reconcile_every, bool) or not isinstance(reconcile_every, int):
        raise BoardError(f"{registry_path}: board.reconcile_every must be an integer number of seconds (0 disables)")
    check_reconcile_every(reconcile_every, f"{registry_path}: board.reconcile_every")
    return BoardConfig(
        root=root, rigs=rigs, slurm=slurm, port=port, bind=bind, reconcile_every=reconcile_every, registry_path=registry_path, token=token
    )


def check_reconcile_every(seconds: int, what: str) -> None:
    """0 disables probing; anything else must leave the rigs and the login node alone between ticks."""
    if seconds != 0 and seconds < MIN_RECONCILE_EVERY_S:
        raise BoardError(
            f"{what} = {seconds}: below {MIN_RECONCILE_EVERY_S} s. Every tick opens an SSH session to each rig "
            f"(nvidia-smi, tmux, status files) and to each Slurm login node (squeue, sacct); the page itself can "
            f"refresh every second, the probes cannot. Use 0 to disable probing."
        )


# ───────────────────────────── storage ─────────────────────────────


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, path)


def read_json(path: Path) -> dict | None:
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


class BoardLock:
    """One process-wide advisory lock so the server thread and chats never interleave."""

    def __init__(self, config: BoardConfig) -> None:
        config.root.mkdir(parents=True, exist_ok=True)
        self.path = config.root / ".lock"

    def __enter__(self) -> "BoardLock":
        self.handle = self.path.open("a+")
        fcntl.flock(self.handle, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc: object) -> None:
        fcntl.flock(self.handle, fcntl.LOCK_UN)
        self.handle.close()


def append_history(config: BoardConfig, event: str, **fields: object) -> None:
    config.root.mkdir(parents=True, exist_ok=True)
    record = {"at": iso(now()), "event": event, **fields}
    with config.history_path.open("a") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


def list_lanes(config: BoardConfig) -> list[dict]:
    lanes: list[dict] = []
    if not config.lanes_dir.is_dir():
        return lanes
    for path in sorted(config.lanes_dir.glob("*/gpu*.json")):
        lane = read_json(path)
        if lane is None:
            continue
        lanes.append(lane)
    return lanes


def parse_session_name(name: str) -> dict | None:
    """Split `<project>_<NNN_exp>_<wave>_<rig>_gpu<ids>` from the right; None if not ours."""
    match = SESSION_RE.match(name)
    if not match:
        return None
    parts = EXPERIMENT_SPLIT_RE.split(match.group("prefix"), maxsplit=1)
    if len(parts) != 2:
        return None
    project, experiment = parts
    return {
        "project": project,
        "experiment": experiment,
        "wave_id": match.group("wave"),
        "rig": match.group("rig"),
        "gpu": match.group("gpu"),
        "tmux_session": name,
    }


def validate_gpu(gpu: str) -> str:
    if not GPU_RE.fullmatch(gpu):
        raise BoardError(f"gpu must be comma-separated indices like 0 or 0,1; got {gpu!r}")
    return gpu


def gpu_indices(gpu: str) -> set[int]:
    return {int(part) for part in gpu.split(",")}


# ───────────────────────────── claim / refresh / release ─────────────────────────────


def claim(config: BoardConfig, args: argparse.Namespace) -> int:
    rig = require_rig(config, args.rig)
    gpu = validate_gpu(args.gpu)
    if not WAVE_RE.fullmatch(args.wave):
        raise BoardError(f"wave must be YYYYMMDD-HHMMSS; got {args.wave!r}")
    session = args.tmux_session or f"{args.project}_{args.experiment}_{args.wave}_{rig.name}_gpu{gpu}"
    parsed = parse_session_name(session)
    if parsed is None or parsed["rig"] != rig.name or parsed["gpu"] != gpu:
        raise BoardError(f"tmux session name does not follow the lane convention: {session!r}")
    path = config.lane_path(rig.name, gpu)
    stamp = iso(now())
    with BoardLock(config):
        existing = read_json(path)
        if existing is not None and existing.get("tmux_session") != session:
            holder = f"{existing.get('project')} wave {existing.get('wave_id')} ({existing.get('tmux_session')})"
            print(f"[held] {rig.name} gpu{gpu} is held by {holder}; refusing to claim", file=sys.stderr)
            return 3
        for other in list_lanes(config):
            if other.get("rig") != rig.name or other.get("tmux_session") == session:
                continue
            if gpu_indices(str(other.get("gpu", ""))) & gpu_indices(gpu):
                print(
                    f"[held] {rig.name} gpu{other.get('gpu')} overlaps gpu{gpu}: "
                    f"{other.get('project')} wave {other.get('wave_id')}; refusing to claim",
                    file=sys.stderr,
                )
                return 3
        lane = {
            "schema_version": SCHEMA_VERSION,
            "rig": rig.name,
            "gpu": gpu,
            "project": args.project,
            "experiment": args.experiment,
            "wave_id": args.wave,
            "tmux_session": session,
            "hub_project_root": args.project_root,
            "claimed_at": existing.get("claimed_at", stamp) if existing else stamp,
            "runs_total": args.runs_total,
            "runs_done": existing.get("runs_done", 0) if existing else 0,
            "active_run": existing.get("active_run") if existing else None,
            "progress": existing.get("progress") if existing else None,
            "eta": existing.get("eta") if existing else None,
            # an adopted lane carried a placeholder basis; the orchestrator now owns the lane
            "eta_basis": (existing.get("eta_basis") if existing and not (existing.get("observed") or {}).get("adopted") else None),
            "updated_at": stamp,
            "observed": {k: v for k, v in (existing or {}).get("observed", {"at": None, "session_alive": None, "state": "claimed"}).items() if k != "adopted"},
        }
        write_json(path, lane)
        append_history(config, "reclaim" if existing else "claim", rig=rig.name, gpu=gpu, session=session)
    print(f"[claimed] {rig.name} gpu{gpu} ← {session}")
    return 0


def refresh(config: BoardConfig, args: argparse.Namespace) -> int:
    rig = require_rig(config, args.rig)
    gpu = validate_gpu(args.gpu)
    path = config.lane_path(rig.name, gpu)
    with BoardLock(config):
        lane = read_json(path)
        if lane is None:
            raise BoardError(f"{rig.name} gpu{gpu} is not held; claim it first")
        if args.wave and lane.get("wave_id") != args.wave:
            raise BoardError(
                f"{rig.name} gpu{gpu} is held by wave {lane.get('wave_id')}, not {args.wave}"
            )
        for key in ("active_run", "progress", "eta", "eta_basis"):
            value = getattr(args, key)
            if value is not None:
                lane[key] = None if value == "" else value
        if args.runs_done is not None:
            lane["runs_done"] = args.runs_done
        if args.runs_total is not None:
            lane["runs_total"] = args.runs_total
        lane["updated_at"] = iso(now())
        write_json(path, lane)
        append_history(config, "refresh", rig=rig.name, gpu=gpu, active_run=lane.get("active_run"))
    print(f"[refreshed] {rig.name} gpu{gpu}: {lane.get('active_run')} {lane.get('progress') or ''}")
    return 0


def release(config: BoardConfig, args: argparse.Namespace) -> int:
    rig = require_rig(config, args.rig)
    gpu = validate_gpu(args.gpu)
    path = config.lane_path(rig.name, gpu)
    with BoardLock(config):
        lane = read_json(path)
        if lane is None:
            print(f"[free] {rig.name} gpu{gpu} was not held")
            return 0
        path.unlink()
        append_history(
            config,
            "release",
            rig=rig.name,
            gpu=gpu,
            session=lane.get("tmux_session"),
            reason=args.reason,
        )
    print(f"[released] {rig.name} gpu{gpu} ({args.reason})")
    return 0


def require_rig(config: BoardConfig, name: str) -> Rig:
    rig = config.rigs.get(name)
    if rig is None:
        raise BoardError(f"unknown rig {name!r}; board.rigs = {sorted(config.rigs)}")
    return rig


# ───────────────────────────── probing rigs ─────────────────────────────

GPU_QUERY = "index,uuid,name,memory.total,memory.used,utilization.gpu,temperature.gpu,power.draw"
APP_QUERY = "gpu_uuid,pid,used_memory,process_name"
PROBE_SCRIPT = (
    "tmux ls -F '#{session_name}' 2>/dev/null; echo __GPUS__; "
    f"nvidia-smi --query-gpu={GPU_QUERY} --format=csv,noheader,nounits 2>/dev/null || echo __NVIDIA_SMI_FAILED__; "
    "echo __APPS__; "
    f"nvidia-smi --query-compute-apps={APP_QUERY} --format=csv,noheader,nounits 2>/dev/null; "
    "echo __PS__; "
    "for p in $(nvidia-smi --query-compute-apps=pid --format=csv,noheader 2>/dev/null); do ps -o pid=,user:32=,etimes= -p \"$p\" 2>/dev/null; done; "
    "echo __LOAD__; cat /proc/loadavg 2>/dev/null; nproc 2>/dev/null; "
    # lane sessions only (they carry a wave id and a gpu suffix): working directory, last visible
    # line of the pane, and every recently heartbeaten .status.json under that project's evaluations/
    "echo __PANES__; tmux list-panes -a -F '#{session_name}|#{pane_current_path}' 2>/dev/null | grep -E '_[0-9]{8}-[0-9]{6}_[^|]*_gpu[0-9]'; "
    "echo __TAIL__; tmux ls -F '#{session_name}' 2>/dev/null | grep -E '_[0-9]{8}-[0-9]{6}_.*_gpu[0-9]' | while IFS= read -r s; do "
    "printf '%s|' \"$s\"; tmux capture-pane -p -t \"$s\" 2>/dev/null | grep -v '^[[:space:]]*$' | tail -1 | cut -c1-400; echo; done; "
    "echo __STATUS__; tmux list-panes -a -F '#{session_name}|#{pane_current_path}' 2>/dev/null | grep -E '_[0-9]{8}-[0-9]{6}_[^|]*_gpu[0-9]' | cut -d'|' -f2- | sort -u | while IFS= read -r d; do "
    f"find \"$d/evaluations\" -name .status.json -mmin -{'{STATUS_RECENT_MIN}'} 2>/dev/null | head -{'{STATUS_MAX_FILES}'} | while IFS= read -r f; do printf '%s\t' \"$f\"; tr -d '\n' < \"$f\"; echo; done; done; "
    "echo __BOOT__; uptime -s 2>/dev/null"
)
STATUS_RECENT_MIN = 30
STATUS_MAX_FILES = 200
PROBE_SCRIPT = PROBE_SCRIPT.replace("{STATUS_RECENT_MIN}", str(STATUS_RECENT_MIN)).replace("{STATUS_MAX_FILES}", str(STATUS_MAX_FILES))
HEARTBEAT_STALE_S = 180
RUN_ETA_BASIS = "linear from the run's own progress and elapsed time"


def is_local(rig: Rig) -> bool:
    return rig.hostname is not None and rig.hostname == socket.gethostname()


def probe_command(rig: Rig) -> list[str]:
    if is_local(rig):
        return ["bash", "-lc", PROBE_SCRIPT]
    return ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", rig.ssh, PROBE_SCRIPT]


def run_probe(rig: Rig) -> str | None:
    try:
        result = subprocess.run(
            probe_command(rig), capture_output=True, text=True, timeout=SSH_TIMEOUT_S, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if "__GPUS__" not in result.stdout:
        return None
    return result.stdout


def _num(cell: str) -> int | None:
    try:
        return int(float(cell))
    except ValueError:
        return None


def _section(output: str, marker: str, next_markers: tuple[str, ...]) -> str:
    """Text between `marker` and the first of `next_markers` (all optional, order-independent)."""
    _, found, rest = output.partition(marker)
    if not found:
        return ""
    cut = len(rest)
    for other in next_markers:
        pos = rest.find(other)
        if pos != -1:
            cut = min(cut, pos)
    return rest[:cut]


PROBE_MARKERS = ("__GPUS__", "__APPS__", "__PS__", "__LOAD__", "__PANES__", "__TAIL__", "__STATUS__", "__BOOT__")


def parse_gpu_line(line: str) -> tuple[dict, str | None] | None:
    """One nvidia-smi --query-gpu row: the legacy 3-column form or the full GPU_QUERY form."""
    cells = [cell.strip() for cell in line.split(",")]
    if len(cells) < 3 or not cells[0].isdigit():
        return None
    if len(cells) < 8:
        used, util = _num(cells[1]), _num(cells[2])
        if used is None or util is None:
            return None
        return {"index": int(cells[0]), "memory_used_mib": used, "utilization": util}, None
    # name may itself contain commas: take the fixed tail from the right
    tail = cells[-5:]
    name = ", ".join(cells[2:-5])
    total, used, util = _num(tail[0]), _num(tail[1]), _num(tail[2])
    if used is None or util is None:
        return None
    gpu = {
        "index": int(cells[0]),
        "name": name or None,
        "memory_total_mib": total,
        "memory_used_mib": used,
        "utilization": util,
        "temperature_c": _num(tail[3]),
        "power_w": _num(tail[4]),
        "processes": [],
    }
    return gpu, cells[1]


def parse_probe(rig: Rig, output: str) -> dict:
    sessions_part = output.partition("__GPUS__")[0]
    sessions = [line.strip() for line in sessions_part.splitlines() if line.strip()]
    gpus_part = _section(output, "__GPUS__", PROBE_MARKERS[1:])
    gpu_probe_ok = "__NVIDIA_SMI_FAILED__" not in gpus_part
    gpus: list[dict] = []
    by_uuid: dict[str, dict] = {}
    for line in gpus_part.splitlines():
        parsed = parse_gpu_line(line)
        if parsed is None:
            continue
        gpu, uuid = parsed
        gpus.append(gpu)
        if uuid:
            by_uuid[uuid] = gpu
    owners: dict[int, dict] = {}
    for line in _section(output, "__PS__", ("__LOAD__", "__BOOT__")).splitlines():
        cells = line.split(None, 2)
        if len(cells) == 3 and cells[0].isdigit():
            owners[int(cells[0])] = {"user": cells[1], "elapsed_s": _num(cells[2])}
    for line in _section(output, "__APPS__", ("__PS__", "__LOAD__", "__BOOT__")).splitlines():
        cells = [cell.strip() for cell in line.split(",", 3)]
        if len(cells) != 4 or not cells[1].isdigit():
            continue
        gpu = by_uuid.get(cells[0])
        if gpu is None:
            continue
        pid = int(cells[1])
        owner = owners.get(pid, {})
        gpu["processes"].append(
            {
                "pid": pid,
                "user": owner.get("user"),
                "memory_mib": _num(cells[2]),
                "name": cells[3] or None,
                "elapsed_s": owner.get("elapsed_s"),
            }
        )
    panes: dict[str, str] = {}
    for line in _section(output, "__PANES__", PROBE_MARKERS[5:]).splitlines():
        name, sep, cwd = line.partition("|")
        if sep and cwd.strip():
            panes[name.strip()] = cwd.strip()
    tails: dict[str, str] = {}
    for line in _section(output, "__TAIL__", PROBE_MARKERS[6:]).splitlines():
        name, sep, text = line.partition("|")
        if sep and text.strip():
            tails[name.strip()] = text.strip()
    statuses: list[dict] = []
    for line in _section(output, "__STATUS__", PROBE_MARKERS[7:]).splitlines():
        path, sep, payload = line.partition("\t")
        if not sep:
            continue
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            statuses.append({"path": path.strip(), "data": data})
    load: list[float] | None = None
    cpus: int | None = None
    load_lines = _section(output, "__LOAD__", PROBE_MARKERS[4:]).split()
    if len(load_lines) >= 3:
        try:
            load = [float(load_lines[0]), float(load_lines[1]), float(load_lines[2])]
        except ValueError:
            load = None
        if load_lines[-1].isdigit() and len(load_lines) >= 6:
            cpus = int(load_lines[-1])
    boot = _section(output, "__BOOT__", ()).strip() or None
    boot_at = None
    if boot:
        try:
            boot_at = iso(datetime.fromisoformat(boot).astimezone())
        except ValueError:
            boot_at = None
    return {
        "rig": rig.name,
        "at": iso(now()),
        "reachable": True,
        "shared": rig.shared,
        "boot_at": boot_at,
        "sessions": sessions,
        "gpus": gpus,
        "gpu_probe_ok": gpu_probe_ok,
        "load": load,
        "cpus": cpus,
        "panes": panes,
        "tails": tails,
        "statuses": statuses,
    }


def live_run(lane: dict, probe: dict) -> dict | None:
    """The run this lane is executing right now, read from the run's own `.status.json` on the rig.

    Candidates are the recently heartbeaten status files under the project the lane's tmux pane
    sits in, matching the lane's wave id and GPU set. A `running` one wins; otherwise the latest
    finished one is reported (the lane is between runs). Only the run's own numbers are copied;
    the ETA is a linear extrapolation of them and is labelled as such."""
    root = (probe.get("panes") or {}).get(lane.get("tmux_session") or "")
    wanted_gpu = str(lane.get("gpu"))
    candidates = []
    for entry in probe.get("statuses") or []:
        data = entry["data"]
        if data.get("wave_id") != lane.get("wave_id") or str(data.get("gpu")) != wanted_gpu:
            continue
        if root and not entry["path"].startswith(root.rstrip("/") + "/"):
            continue
        candidates.append(entry)
    if not candidates:
        return None
    running = [c for c in candidates if c["data"].get("state") == "running"]
    pool = running or candidates
    best = max(pool, key=lambda c: c["data"].get("heartbeat") or c["data"].get("ended") or "")
    data = best["data"]
    path = best["path"]
    marker = "/evaluations/"
    rel = path[path.index(marker) + len(marker):] if marker in path else path
    rel = rel.removesuffix("/.status.json")
    heartbeat = parse_iso(data.get("heartbeat"))
    age = int((now() - heartbeat).total_seconds()) if heartbeat else None
    completed, total, elapsed = data.get("progress_completed"), data.get("progress_total"), data.get("elapsed_s")
    eta = None
    if data.get("state") == "running" and isinstance(completed, (int, float)) and isinstance(total, (int, float)) and isinstance(elapsed, (int, float)) and completed > 0 and total > completed:
        anchor = heartbeat or now()
        eta = iso(anchor + timedelta(seconds=elapsed * (total - completed) / completed))
    return {
        "path": rel,
        "state": data.get("state"),
        "progress": data.get("progress"),
        "completed": completed,
        "total": total,
        "unit": data.get("progress_unit"),
        "elapsed_s": elapsed,
        "started": data.get("started"),
        "ended": data.get("ended"),
        "heartbeat": data.get("heartbeat"),
        "heartbeat_age_s": age,
        "heartbeat_stale": bool(data.get("state") == "running" and age is not None and age > HEARTBEAT_STALE_S),
        "eta": eta,
        "eta_basis": RUN_ETA_BASIS if eta else None,
    }


def unreachable_probe(rig: Rig, previous: dict | None) -> dict:
    return {
        "rig": rig.name,
        "at": iso(now()),
        "reachable": False,
        "shared": rig.shared,
        "boot_at": (previous or {}).get("boot_at"),
        "sessions": (previous or {}).get("sessions", []),
        "gpus": (previous or {}).get("gpus", []),
        "gpu_probe_ok": (previous or {}).get("gpu_probe_ok"),
        "load": (previous or {}).get("load"),
        "cpus": (previous or {}).get("cpus"),
        "last_reachable_at": (previous or {}).get("at") if (previous or {}).get("reachable") else (previous or {}).get("last_reachable_at"),
    }


SQUEUE_FIELDS = ["job_id", "name", "state", "reason", "partition", "nodes", "elapsed", "time_left", "start", "submitted", "workdir", "command", "account", "qos"]
SQUEUE_REQUIRED = 10  # older probes carried no workdir/command/account/qos
SQUEUE_FORMAT = "%i|%j|%T|%r|%P|%N|%M|%L|%S|%V|%Z|%o|%a|%q"
EXPERIMENT_PATH_RE = re.compile(r"(?:^|/)scripts/((?:\d{3}_[^/]+/)*\d{3}_[^/]+)(?:/|$)")


def infer_project(job: dict) -> str | None:
    """Project for a job that does not follow the lane naming: the basename of its working directory."""
    if job.get("project"):
        return job["project"]
    workdir = (job.get("workdir") or "").rstrip("/")
    return workdir.rsplit("/", 1)[-1] or None if workdir else None


def infer_experiment(job: dict) -> str | None:
    if job.get("experiment"):
        return job["experiment"]
    match = EXPERIMENT_PATH_RE.search(job.get("command") or "")
    return match.group(1) if match else None
SACCT_WINDOW = "now-3days"
SACCT_FORMAT = "JobID,JobName%256,State,End,WorkDir%256,Account,QOS"
SQUEUE_SCRIPT = (
    f"squeue --me --noheader --format='{SQUEUE_FORMAT}'; echo __SACCT__; "
    f"sacct -X -P --noheader -S {SACCT_WINDOW} --format={SACCT_FORMAT} 2>/dev/null; "
    "echo __SALDO__; saldo -b -n 2>/dev/null; echo __SQUEUE_OK__"
)
BUDGET_EXPIRING_DAYS = 30
BUDGET_LOW_PCT = 90.0
FINISHED_STATES = {"COMPLETED": "completed", "FAILED": "failed", "CANCELLED": "cancelled", "TIMEOUT": "timeout", "OUT_OF_MEMORY": "failed", "NODE_FAIL": "failed", "DEADLINE": "timeout", "PREEMPTED": "cancelled", "BOOT_FAIL": "failed"}
FINISHED_KINDS = ("completed", "failed", "cancelled", "timeout")


def run_slurm_probe(target: SlurmTarget) -> tuple[str | None, str]:
    """(stdout when the probe answered, else None; a short failure reason)."""
    cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", target.ssh, SQUEUE_SCRIPT]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=SSH_TIMEOUT_S, check=False)
    except subprocess.TimeoutExpired:
        return None, "ssh timed out"
    except OSError as exc:
        return None, f"ssh failed: {exc}"
    if "__SQUEUE_OK__" in result.stdout:
        return result.stdout, ""
    err = result.stderr.strip().splitlines()
    last = err[-1] if err else f"exit {result.returncode}"
    if "Permission denied" in last or "publickey" in last:
        return None, "ssh authentication failed: renew the cluster certificate"
    if "squeue" in last and ("not found" in last or "command" in last):
        return None, "squeue unavailable on the login node"
    return None, last[:160]


def parse_sacct(text: str) -> list[dict]:
    """Finished jobs from `sacct -X -P`; running/pending ones are the queue's business."""
    finished: list[dict] = []
    for line in text.splitlines():
        cells = [cell.strip() for cell in line.split("|")]
        if len(cells) < 4 or not cells[0]:
            continue
        job_id, name, state, end = cells[:4]
        kind = FINISHED_STATES.get(state.split()[0] if state else "")
        if kind is None:
            continue
        job = {
            "job_id": job_id, "name": name, "state": state.split()[0], "kind": kind, "end": end or None,
            "workdir": cells[4] if len(cells) > 4 else None,
            "account": (cells[5] if len(cells) > 5 else "") or None,
            "qos": (cells[6] if len(cells) > 6 else "") or None,
        }
        parsed = parse_session_name(name) or {}
        job["project"] = parsed.get("project")
        job["experiment"] = parsed.get("experiment")
        job["wave_id"] = parsed.get("wave_id")
        job["project_inferred"] = infer_project(job)
        finished.append(job)
    finished.sort(key=lambda j: (j["end"] or "", j["job_id"]), reverse=True)
    return finished


def _saldo_date(raw: str) -> str | None:
    try:
        return datetime.strptime(raw, "%Y%m%d").date().isoformat()
    except ValueError:
        return None


def parse_saldo(text: str, in_use: set[str] | None = None) -> list[dict]:
    """`saldo -b -n` rows (CINECA budgets): account, validity, total / consumed / monthly local hours.

    Adds derived fields: remaining hours, days to expiry, whether the account is currently in use
    on the queue, and a status flag. Accounts are compared case-insensitively because Slurm lowers
    them (`iscrc_qatt`) while saldo keeps the original case (`IscrC_QATT`)."""
    today = now().date()
    used = {a.lower() for a in (in_use or set())}
    rows: list[dict] = []
    for line in text.splitlines():
        cells = line.split()
        if len(cells) < 9 or cells[0] == "account":
            continue
        try:
            total, local, consumed = float(cells[3]), float(cells[4]), float(cells[5])
            pct = float(cells[6])
            month_total, month_consumed = float(cells[7]), float(cells[8])
        except ValueError:
            continue
        start, end = _saldo_date(cells[1]), _saldo_date(cells[2])
        end_date = datetime.fromisoformat(end).date() if end else None
        days_left = (end_date - today).days if end_date else None
        remaining = total - consumed
        month_remaining = (month_total - month_consumed) if month_total else None
        if days_left is not None and days_left < 0:
            status = "expired"
        elif remaining <= 0:
            status = "exhausted"
        elif month_total and month_consumed >= month_total:
            status = "month_exhausted"
        elif days_left is not None and days_left <= BUDGET_EXPIRING_DAYS:
            status = "expiring"
        elif pct >= BUDGET_LOW_PCT or (month_total and 100 * month_consumed / month_total >= BUDGET_LOW_PCT):
            status = "low"
        else:
            status = "ok"
        rows.append(
            {
                "account": cells[0],
                "start": start,
                "end": end,
                "days_left": days_left,
                "total_h": total,
                "local_consumed_h": local,
                "consumed_h": consumed,
                "consumed_pct": pct,
                "remaining_h": remaining,
                "month_total_h": month_total or None,
                "month_consumed_h": month_consumed,
                "month_remaining_h": month_remaining,
                "month_pct": (100 * month_consumed / month_total) if month_total else None,
                "in_use": cells[0].lower() in used,
                "status": status,
            }
        )
    rows.sort(key=lambda r: (not r["in_use"], r["status"] == "expired", r["end"] or ""))
    return rows


def budget_alerts(cluster: dict) -> list[str]:
    """Human lines for budgets that block or threaten the accounts in use."""
    alerts: list[str] = []
    for b in cluster.get("budgets", []):
        if not b.get("in_use") or b.get("status") in ("ok", "low"):
            continue
        text = {
            "expired": f"expired {b['end']}",
            "exhausted": f"exhausted ({b['consumed_pct']:.1f}% of {b['total_h']:.0f} h used)",
            "month_exhausted": f"monthly allowance used up ({b['month_consumed_h']:.0f} of {b['month_total_h']:.0f} h)",
            "expiring": f"expires {b['end']} ({b['days_left']} days)",
        }.get(b["status"], b["status"])
        alerts.append(f"{cluster['rig']} account {b['account']}: {text}")
    return alerts


def parse_slurm_probe(target: SlurmTarget, output: str) -> dict:
    queue_text, _, rest = output.partition("__SACCT__")
    sacct_text, _, saldo_text = rest.partition("__SALDO__")
    jobs: list[dict] = []
    for line in queue_text.splitlines():
        if line.strip() == "__SQUEUE_OK__" or "|" not in line:
            continue
        cells = line.split("|")
        if len(cells) < SQUEUE_REQUIRED:
            continue
        cells = cells[: len(SQUEUE_FIELDS)] + [""] * (len(SQUEUE_FIELDS) - len(cells))
        job = dict(zip(SQUEUE_FIELDS, (cell.strip() for cell in cells)))
        parsed = parse_session_name(job["name"]) or {}
        job["project"] = parsed.get("project")
        job["experiment"] = parsed.get("experiment")
        job["wave_id"] = parsed.get("wave_id")
        for key in ("reason", "nodes", "start", "workdir", "command", "account", "qos"):
            if job[key] in ("None", "N/A", "(null)", ""):
                job[key] = None
        job["project_inferred"] = infer_project(job)
        job["experiment_inferred"] = infer_experiment(job)
        jobs.append(job)
    order = {"RUNNING": 0, "COMPLETING": 1, "CONFIGURING": 2, "PENDING": 3}
    jobs.sort(key=lambda j: (order.get(j["state"], 9), j["job_id"]))
    finished = parse_sacct(sacct_text)
    in_use = {j["account"] for j in [*jobs, *finished] if j.get("account")}
    return {
        "rig": target.name,
        "kind": "slurm",
        "at": iso(now()),
        "reachable": True,
        "jobs": jobs,
        "finished": finished,
        "budgets": parse_saldo(saldo_text, in_use),
        "budgets_at": iso(now()) if saldo_text.strip() else None,
        "finished_window": SACCT_WINDOW,
        "groups": group_jobs(jobs, finished),
        "running": sum(1 for j in jobs if j["state"] == "RUNNING"),
        "pending": sum(1 for j in jobs if j["state"] == "PENDING"),
        "accounts": sorted({j["account"] for j in [*jobs, *finished] if j.get("account")}),
        **{kind: sum(1 for j in finished if j["kind"] == kind) for kind in FINISHED_KINDS},
    }


TRAILING_INDEX_RE = re.compile(r"[_-]?\d+$")


def job_group_key(job: dict) -> tuple[str, str]:
    """(group key, the part of the name that may vary inside the group).

    Lane-convention names group by project/experiment/wave. `<wave>__k=v,k=v` sweep names
    (one Slurm job per configuration) group by the `<wave>` prefix. Anything else groups by the
    name with a trailing index stripped, so `eval_1`, `eval_2` land together."""
    if job.get("wave_id") and job.get("project"):
        return f"{job['project']} / {job['experiment']} · wave {job['wave_id']}", ""
    name = job.get("name") or ""
    project = infer_project(job)
    if "__" in name:
        prefix, _, rest = name.partition("__")
        return (f"{project} · wave {prefix}" if project else prefix), rest
    base = TRAILING_INDEX_RE.sub("", name) or name
    return (f"{project} · {base}" if project else base), name


def new_group(key: str, partition: str | None) -> dict:
    return {
        "key": key, "jobs": [], "_varying": [], "running": 0, "pending": 0, "other": 0, "nodes": [], "partition": partition,
        "finished": {kind: 0 for kind in FINISHED_KINDS}, "finished_total": 0, "total": 0, "last_end": None,
        "project": None, "experiment": None, "wave_id": None, "accounts": [], "qos": [],
    }


def describe_group(group: dict, job: dict) -> None:
    """Fill the group's project / experiment / wave from the first job that knows them."""
    if group["project"] is None:
        group["project"] = infer_project(job)
    if group["experiment"] is None:
        group["experiment"] = infer_experiment(job)
    for key, plural in (("account", "accounts"), ("qos", "qos")):
        value = job.get(key)
        if value and value not in group[plural]:
            group[plural].append(value)
    if group["wave_id"] is None:
        name = job.get("name") or ""
        group["wave_id"] = job.get("wave_id") or (name.partition("__")[0] if "__" in name and WAVE_RE.fullmatch(name.partition("__")[0]) else None)


def group_jobs(jobs: list[dict], finished: list[dict] | None = None) -> list[dict]:
    """Fold a queue (plus recently finished jobs) into groups; annotates every queued job with
    `group` and `variant` in place. A group's `total` is queued + finished, so the finished
    shares are fractions of everything the sweep submitted within the accounting window."""
    groups: dict[str, dict] = {}
    for job in finished or []:
        key, _ = job_group_key(job)
        job["group"] = key
        group = groups.setdefault(key, new_group(key, None))
        describe_group(group, job)
        group["finished"][job["kind"]] += 1
        group["finished_total"] += 1
        if job.get("end") and (group["last_end"] is None or job["end"] > group["last_end"]):
            group["last_end"] = job["end"]
    for job in jobs:
        key, varying = job_group_key(job)
        job["group"] = key
        group = groups.setdefault(key, new_group(key, job.get("partition")))
        describe_group(group, job)
        if group["partition"] is None:
            group["partition"] = job.get("partition")
        group["jobs"].append(job["job_id"])
        group["_varying"].append(varying)
        state = job.get("state")
        if state == "RUNNING":
            group["running"] += 1
            if job.get("nodes"):
                group["nodes"].append(job["nodes"])
        elif state == "PENDING":
            group["pending"] += 1
        else:
            group["other"] += 1
    out: list[dict] = []
    for key, group in groups.items():
        group["total"] = len(group["jobs"]) + group["finished_total"]
        token_lists = [v.split(",") if v else [] for v in group.pop("_varying")]
        common = set(token_lists[0]) if token_lists and token_lists[0] else set()
        for tokens in token_lists[1:]:
            common &= set(tokens)
        if len(token_lists) == 1:
            common = set()
        group["common"] = ",".join(t for t in token_lists[0] if t in common) if token_lists else ""
        members = [j for j in jobs if j.get("group") == key]
        for job, tokens in zip(members, token_lists):
            job["variant"] = ",".join(t for t in tokens if t not in common)
        running = [j for j in members if j.get("state") == "RUNNING"]
        pending = [j for j in members if j.get("state") == "PENDING"]
        group["min_time_left"] = min((j["time_left"] for j in running if j.get("time_left")), key=slurm_seconds, default=None)
        group["max_time_left"] = max((j["time_left"] for j in running if j.get("time_left")), key=slurm_seconds, default=None)
        group["earliest_start"] = min((j["start"] for j in pending if j.get("start")), default=None)
        group["reasons"] = sorted({j["reason"] for j in pending if j.get("reason")})
        out.append(group)
    out.sort(key=lambda g: (-g["running"], -g["pending"], -(g["finished_total"]), g["key"]))
    return out


def slurm_seconds(value: str | None) -> int:
    """`[D-]HH:MM:SS`, `MM:SS` or `M` as seconds; unparsable sorts last."""
    if not value:
        return 10**9
    days = 0
    text = value
    if "-" in text:
        day_part, _, text = text.partition("-")
        days = int(day_part) if day_part.isdigit() else 0
    parts = text.split(":")
    try:
        numbers = [int(part) for part in parts]
    except ValueError:
        return 10**9
    seconds = 0
    for number in numbers:
        seconds = seconds * 60 + number
    if len(numbers) == 1:
        seconds *= 60
    return days * 86400 + seconds


def summarize_queue_changes(target: str, before: dict[str, str], after: dict[str, str], fold_at: int = 5) -> list[str]:
    """Human lines for a queue diff; bulk submissions fold into one line per kind instead of one per job."""
    appeared: dict[str, list[str]] = {}
    for job_id in sorted(after.keys() - before.keys()):
        appeared.setdefault(after[job_id], []).append(job_id)
    left: dict[str, list[str]] = {}
    for job_id in sorted(before.keys() - after.keys()):
        left.setdefault(before[job_id], []).append(job_id)
    moved: dict[tuple[str, str], list[str]] = {}
    for job_id in sorted(before.keys() & after.keys()):
        if before[job_id] != after[job_id]:
            moved.setdefault((before[job_id], after[job_id]), []).append(job_id)
    lines: list[str] = []

    def fold(ids: list[str], single: str, many: str) -> None:
        if len(ids) > fold_at:
            lines.append(many.format(n=len(ids), first=ids[0], last=ids[-1]))
        else:
            lines.extend(single.format(job_id=job_id) for job_id in ids)

    for state, ids in appeared.items():
        fold(ids, f"{target}: job {{job_id}} appeared ({state})", f"{target}: {{n}} jobs appeared ({state}) [{{first}}…{{last}}]")
    for state, ids in left.items():
        fold(ids, f"{target}: job {{job_id}} left the queue (was {state})", f"{target}: {{n}} jobs left the queue (were {state}) [{{first}}…{{last}}]")
    for (was, is_now), ids in moved.items():
        fold(ids, f"{target}: job {{job_id}} {was} → {is_now}", f"{target}: {{n}} jobs {was} → {is_now} [{{first}}…{{last}}]")
    return lines


def unreachable_slurm_probe(target: SlurmTarget, previous: dict | None, reason: str) -> dict:
    previous = previous or {}
    return {
        "rig": target.name,
        "kind": "slurm",
        "at": iso(now()),
        "reachable": False,
        "reason": reason,
        "jobs": previous.get("jobs", []),
        "finished": previous.get("finished", []),
        "budgets": previous.get("budgets", []),
        "budgets_at": previous.get("budgets_at"),
        "accounts": previous.get("accounts", []),
        "finished_window": previous.get("finished_window", SACCT_WINDOW),
        "groups": previous.get("groups", group_jobs(list(previous.get("jobs", [])), list(previous.get("finished", [])))),
        **{kind: previous.get(kind, 0) for kind in FINISHED_KINDS},
        "running": previous.get("running", 0),
        "pending": previous.get("pending", 0),
        "last_reachable_at": previous.get("at") if previous.get("reachable") else previous.get("last_reachable_at"),
    }


def reconcile_rig(config: BoardConfig, rig: Rig, probe: dict) -> list[str]:
    """Apply the honesty rules for one rig. Returns human-readable change lines."""
    changes: list[str] = []
    stamp = iso(now())
    held = {lane["gpu"]: lane for lane in list_lanes(config) if lane.get("rig") == rig.name}
    ours = {name: parse_session_name(name) for name in probe["sessions"]}
    ours = {name: parsed for name, parsed in ours.items() if parsed and parsed["rig"] == rig.name}

    if not probe["reachable"]:
        for gpu, lane in held.items():
            lane["observed"] = {**lane.get("observed", {}), "at": stamp, "state": "unreachable"}
            write_json(config.lane_path(rig.name, gpu), lane)
        return [f"{rig.name}: unreachable; kept {len(held)} lane(s) as last known"]

    boot_at = parse_iso(probe.get("boot_at"))
    alive_sessions = set(ours)
    for gpu, lane in held.items():
        session = lane.get("tmux_session")
        path = config.lane_path(rig.name, gpu)
        if session in alive_sessions:
            updated = parse_iso(lane.get("updated_at"))
            silent = (now() - updated).total_seconds() if updated else None
            adopted = bool(lane.get("observed", {}).get("adopted"))
            lane["observed"] = {
                "at": stamp,
                "session_alive": True,
                "state": "running",
                "orchestrator_silent_s": int(silent) if silent is not None else None,
                "run": live_run(lane, probe),
                "last_output": (probe.get("tails") or {}).get(session),
            }
            if adopted:
                lane["observed"]["adopted"] = True
            write_json(path, lane)
            continue
        claimed = parse_iso(lane.get("claimed_at"))
        if boot_at and claimed and boot_at > claimed:
            if lane.get("observed", {}).get("state") != "interrupted":
                changes.append(f"{rig.name} gpu{gpu}: rebooted at {probe['boot_at']} → lane interrupted ({session})")
                append_history(config, "interrupted", rig=rig.name, gpu=gpu, session=session, boot_at=probe["boot_at"])
            lane["observed"] = {"at": stamp, "session_alive": False, "state": "interrupted", "boot_at": probe["boot_at"]}
            write_json(path, lane)
            continue
        path.unlink()
        append_history(config, "release", rig=rig.name, gpu=gpu, session=session, reason="reconcile: session gone")
        changes.append(f"{rig.name} gpu{gpu}: session gone → released ({session})")

    for name, parsed in ours.items():
        if parsed["gpu"] in held and held[parsed["gpu"]].get("tmux_session") == name:
            continue
        overlap = [g for g in held if gpu_indices(g) & gpu_indices(parsed["gpu"]) and held[g].get("tmux_session") != name]
        if overlap:
            changes.append(f"{rig.name} gpu{parsed['gpu']}: live session {name} overlaps held gpu{overlap[0]}; not adopted")
            continue
        lane = {
            "schema_version": SCHEMA_VERSION,
            **parsed,
            "hub_project_root": None,
            "claimed_at": stamp,
            "runs_total": None,
            "runs_done": None,
            "active_run": None,
            "progress": None,
            "eta": None,
            "eta_basis": "unavailable: adopted from a live session, no orchestrator report",
            "updated_at": stamp,
            "observed": {"at": stamp, "session_alive": True, "state": "running", "adopted": True, "run": None, "last_output": (probe.get("tails") or {}).get(name)},
        }
        lane["observed"]["run"] = live_run(lane, probe)
        write_json(config.lane_path(rig.name, parsed["gpu"]), lane)
        held[parsed["gpu"]] = lane
        append_history(config, "adopt", rig=rig.name, gpu=parsed["gpu"], session=name)
        changes.append(f"{rig.name} gpu{parsed['gpu']}: adopted live session {name}")

    covered: set[int] = set()
    for gpu in held:
        covered |= gpu_indices(gpu)
    foreign = [
        g["index"] for g in probe["gpus"] if g["index"] not in covered and g["memory_used_mib"] >= FOREIGN_MEMORY_MIB
    ]
    probe["foreign"] = foreign
    write_json(config.rig_path(rig.name), {k: v for k, v in probe.items() if k not in ("statuses", "tails", "panes")})
    return changes


def reconcile(config: BoardConfig, rigs: list[str] | None = None, quiet: bool = False) -> int:
    wanted = rigs or [*config.rigs, *config.slurm]
    unknown = [name for name in wanted if name not in config.rigs and name not in config.slurm]
    if unknown:
        raise BoardError(f"unknown rig {unknown[0]!r}; board.rigs = {sorted(config.rigs)}, board.slurm = {sorted(config.slurm)}")
    targets = [config.rigs[name] for name in wanted if name in config.rigs]
    slurm_targets = [config.slurm[name] for name in wanted if name in config.slurm]
    outputs: dict[str, str | None] = {}
    slurm_outputs: dict[str, tuple[str | None, str]] = {}
    threads = []

    def worker(rig: Rig) -> None:
        outputs[rig.name] = run_probe(rig)

    def slurm_worker(target: SlurmTarget) -> None:
        slurm_outputs[target.name] = run_slurm_probe(target)

    for rig in targets:
        threads.append(threading.Thread(target=worker, args=(rig,), daemon=True))
    for target in slurm_targets:
        threads.append(threading.Thread(target=slurm_worker, args=(target,), daemon=True))
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(SSH_TIMEOUT_S + 5)

    changes: list[str] = []
    with BoardLock(config):
        for rig in targets:
            output = outputs.get(rig.name)
            if output is None:
                probe = unreachable_probe(rig, read_json(config.rig_path(rig.name)))
                write_json(config.rig_path(rig.name), probe)
            else:
                probe = parse_probe(rig, output)
            changes.extend(reconcile_rig(config, rig, probe))
        for target in slurm_targets:
            output, reason = slurm_outputs.get(target.name, (None, "probe did not finish"))
            previous = read_json(config.rig_path(target.name))
            if output is None:
                probe = unreachable_slurm_probe(target, previous, reason)
                if not previous or previous.get("reachable") is not False or previous.get("reason") != reason:
                    changes.append(f"{target.name}: unreachable ({reason}); kept {len(probe['jobs'])} job(s) as last known")
            else:
                probe = parse_slurm_probe(target, output)
                before = {j["job_id"]: j["state"] for j in (previous or {}).get("jobs", [])}
                after = {j["job_id"]: j["state"] for j in probe["jobs"]}
                changes.extend(summarize_queue_changes(target.name, before, after))
            write_json(config.rig_path(target.name), probe)
        append_history(config, "reconcile", rigs=[t.name for t in targets] + [t.name for t in slurm_targets], changes=changes)
    if not quiet:
        for line in changes:
            print(f"[reconcile] {line}")
        if not changes:
            print("[reconcile] board already matched the rigs")
    return 0


# ───────────────────────────── status ─────────────────────────────


def supervised_waves(config: BoardConfig) -> list[dict]:
    """Waves the hub's sweep-supervisor service is driving, with how recently it and its agent acted.

    Read-only: the supervisor registers a pointer under `<root>/supervisor/` and keeps its own state
    in the project. A wave whose last cycle is old means the service is down, which is exactly what
    a lane's held-but-silent state cannot say by itself.
    """
    waves = []
    directory = config.root / "supervisor"
    if not directory.is_dir():
        return waves
    for path in sorted(directory.glob("*.json")):
        pointer = read_json(path)
        if not pointer:
            continue
        state = Path(str(pointer.get("project_root"))) / ".waves" / "_state" / str(pointer.get("wave_id"))
        runtime = read_json(state / "runtime.json") or {}
        view = read_json(state / "snapshot.json") or {}
        waves.append(
            {
                "project": view.get("project") or Path(str(pointer.get("project_root"))).name,
                "wave_id": pointer.get("wave_id"),
                "project_root": pointer.get("project_root"),
                "counts": view.get("counts"),
                "total": view.get("total"),
                "wave_eta": view.get("wave_eta"),
                "cycled_at": runtime.get("cycled_at"),
                "agent_ticked_at": runtime.get("agent_ticked_at"),
                "agent_last_result": runtime.get("agent_last_result"),
                "open_questions": len(view.get("questions") or []),
            }
        )
    return waves


def snapshot(config: BoardConfig) -> dict:
    lanes = list_lanes(config)
    rigs = []
    for name, rig in config.rigs.items():
        probe = read_json(config.rig_path(name)) or {
            "rig": name,
            "at": None,
            "reachable": None,
            "shared": rig.shared,
            "boot_at": None,
            "sessions": [],
            "gpus": [],
            "foreign": [],
        }
        probe.setdefault("foreign", [])
        probe["lanes"] = [lane for lane in lanes if lane.get("rig") == name]
        probe.setdefault("kind", "rig")
        probe.setdefault("gpu_probe_ok", None if probe.get("reachable") is None else bool(probe.get("gpus")))
        covered: set[int] = set()
        for lane in probe["lanes"]:
            covered |= gpu_indices(str(lane.get("gpu", "0")))
        probe["free_gpus"] = [
            g["index"] for g in probe.get("gpus", []) if g["index"] not in covered and g["index"] not in probe["foreign"]
        ]
        # experiment-looking tmux sessions (they carry a wave id) that do not follow the lane convention
        probe["stray_sessions"] = [
            s
            for s in probe.get("sessions", [])
            if WAVE_IN_NAME_RE.search(s) and (parse_session_name(s) or {}).get("rig") != name
        ]
        rigs.append(probe)
    slurm = []
    for name in config.slurm:
        probe = read_json(config.rig_path(name)) or {
            "rig": name,
            "kind": "slurm",
            "at": None,
            "reachable": None,
            "reason": None,
            "jobs": [],
            "groups": [],
            "budgets": [],
            "running": 0,
            "pending": 0,
        }
        slurm.append(probe)
    return {
        "api_version": API_VERSION,
        "generated_at": iso(now()),
        "board_root": str(config.root),
        "rigs": rigs,
        "slurm": slurm,
        "supervised_waves": supervised_waves(config),
        "summary": summarize(rigs, slurm),
    }


def summarize(rigs: list[dict], slurm: list[dict]) -> dict:
    """Fleet-level counts and the soonest ETA, so one glance answers 'anything free?'."""
    counts = {"gpus": 0, "free": 0, "held": 0, "foreign": 0, "interrupted": 0, "silent": 0, "unreachable_rigs": 0, "unknown_rigs": 0}
    next_free: dict | None = None
    for rig in rigs:
        if rig.get("reachable") is False:
            counts["unreachable_rigs"] += 1
        elif rig.get("reachable") is None or not rig.get("gpu_probe_ok"):
            counts["unknown_rigs"] += 1
        counts["gpus"] += len(rig.get("gpus", []))
        counts["free"] += len(rig.get("free_gpus", []))
        counts["foreign"] += len(rig.get("foreign", []))
        for lane in rig.get("lanes", []):
            counts["held"] += len(gpu_indices(str(lane.get("gpu", "0"))))
            observed = lane.get("observed") or {}
            if observed.get("state") == "interrupted":
                counts["interrupted"] += 1
            silent = observed.get("orchestrator_silent_s")
            if silent is not None and silent > ORCHESTRATOR_SILENT_S:
                counts["silent"] += 1
            eta = parse_iso(lane.get("eta"))
            if eta and (next_free is None or eta < next_free["_eta"]):
                next_free = {
                    "_eta": eta,
                    "rig": rig["rig"],
                    "gpu": lane.get("gpu"),
                    "project": lane.get("project"),
                    "experiment": lane.get("experiment"),
                    "eta": lane.get("eta"),
                    "eta_basis": lane.get("eta_basis"),
                }
    if next_free:
        next_free.pop("_eta")
    counts["slurm_running"] = sum(int(c.get("running") or 0) for c in slurm)
    counts["slurm_pending"] = sum(int(c.get("pending") or 0) for c in slurm)
    counts["budget_alerts"] = [line for c in slurm for line in budget_alerts(c)]
    counts["next_free"] = next_free
    return counts


QUEUE_LINE_RE = re.compile(r"^(?P<target>[^:]+): job (?P<id>\S+) (?P<kind>appeared \([A-Z_]+\)|left the queue \(was [A-Z_]+\)|[A-Z_]+ → [A-Z_]+)$")


def fold_change_lines(lines: list[str], fold_at: int = 5) -> list[str]:
    """Fold per-job queue lines written before bulk folding existed, so old history reads the same way."""
    buckets: dict[tuple[str, str], list[str]] = {}
    order: list[object] = []
    for line in lines:
        match = QUEUE_LINE_RE.match(line)
        if not match:
            order.append(line)
            continue
        key = (match.group("target"), match.group("kind"))
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(match.group("id"))
    out: list[str] = []
    for item in order:
        if isinstance(item, str):
            out.append(item)
            continue
        target, kind = item
        ids = buckets[item]
        if len(ids) <= fold_at:
            out.extend(f"{target}: job {job_id} {kind}" for job_id in ids)
        elif kind.startswith("appeared"):
            out.append(f"{target}: {len(ids)} jobs {kind} [{ids[0]}…{ids[-1]}]")
        elif kind.startswith("left the queue"):
            out.append(f"{target}: {len(ids)} jobs left the queue ({kind[len('left the queue (was '):-1]}) [{ids[0]}…{ids[-1]}]")
        else:
            out.append(f"{target}: {len(ids)} jobs {kind} [{ids[0]}…{ids[-1]}]")
    return out


def read_history(
    config: BoardConfig, hours: float = HISTORY_DEFAULT_HOURS, limit: int = HISTORY_MAX_EVENTS, include_refresh: bool = False
) -> list[dict]:
    """Newest-first board events within `hours`. Reconcile ticks that changed nothing are dropped,
    and the ten-minute orchestrator refresh ticks only when asked for."""
    path = config.history_path
    if not path.exists():
        return []
    cutoff = now() - timedelta(hours=hours)
    events: list[dict] = []
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return []
    for line in reversed(lines):
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        if record.get("event") == "reconcile":
            # adopt / release / interrupted have their own events; keep only the lines nothing else records
            record = {**record, "changes": fold_change_lines([c for c in record.get("changes", []) if not RECONCILE_DUPLICATE_RE.search(c)])}
            if not record["changes"]:
                continue
        if record.get("event") == "refresh" and not include_refresh:
            continue
        at = parse_iso(record.get("at"))
        if at is None or at < cutoff:
            if at is not None:
                break
            continue
        events.append(record)
        if len(events) >= limit:
            break
    return events


def humanize(seconds: float | None) -> str:
    if seconds is None:
        return "?"
    seconds = int(seconds)
    if seconds < 90:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 90:
        return f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    if hours < 48:
        return f"{hours}h{minutes:02d}m"
    return f"{hours // 24}d{hours % 24}h"


def age(value: str | None) -> str:
    stamp = parse_iso(value)
    return humanize((now() - stamp).total_seconds()) if stamp else "?"


def describe_gpu(gpu: dict | None) -> str:
    if not gpu:
        return "no GPU facts (rig never probed)"
    parts = []
    if gpu.get("name"):
        parts.append(str(gpu["name"]))
    if gpu.get("memory_total_mib"):
        parts.append(f"{gpu['memory_used_mib']}/{gpu['memory_total_mib']} MiB")
    else:
        parts.append(f"{gpu.get('memory_used_mib', '?')} MiB used")
    parts.append(f"{gpu.get('utilization', '?')}% util")
    if gpu.get("temperature_c") is not None:
        parts.append(f"{gpu['temperature_c']}°C")
    return " · ".join(parts)


def describe_foreign(gpu: dict | None) -> str:
    base = "busy without a lane on the board"
    if not gpu:
        return base
    procs = gpu.get("processes") or []
    who = ", ".join(
        f"{p.get('user') or '?'} pid {p['pid']} {Path(p['name']).name if p.get('name') else '?'}"
        f" {p.get('memory_mib') or '?'} MiB" + (f" for {humanize(p['elapsed_s'])}" if p.get("elapsed_s") is not None else "")
        for p in procs
    )
    idle = " · idle, holding memory" if gpu.get("utilization", 100) < 5 else ""
    return f"{base} · {describe_gpu(gpu)}{idle}" + (f" · {who}" if who else "")


def history_cli(config: BoardConfig, args: argparse.Namespace) -> int:
    events = read_history(config, hours=args.hours, limit=args.limit, include_refresh=args.refresh)
    if args.json:
        print(json.dumps(events, indent=2, sort_keys=True))
        return 0
    if not events:
        print(f"no board events in the last {args.hours:g}h")
        return 0
    for record in events:
        where = f"{record.get('rig', '')}" + (f" gpu{record['gpu']}" if record.get("gpu") is not None else "")
        detail = record.get("reason") or record.get("session") or ""
        if record.get("event") == "reconcile":
            detail = "; ".join(record.get("changes", []))
        print(f"{record.get('at')}  {record.get('event'):<12} {where:<18} {detail}")
    return 0


def status(config: BoardConfig, args: argparse.Namespace) -> int:
    if args.reconcile:
        reconcile(config, quiet=True)
    data = snapshot(config)
    if args.json:
        print(json.dumps(data, indent=2, sort_keys=True))
        return 0
    summary = data["summary"]
    print(f"rig-board @ {data['generated_at']}  ({config.root})")
    line = (
        f"fleet: {summary['free']} free · {summary['held']} held · {summary['foreign']} foreign"
        f" · {summary['interrupted']} interrupted · {summary['unreachable_rigs']} unreachable rig(s)"
    )
    if summary.get("next_free"):
        nf = summary["next_free"]
        line += f" · next ETA {nf['rig']} gpu{nf['gpu']} ({nf['project']}) {nf['eta']}"
    print(line)
    for alert in summary.get("budget_alerts", []):
        print(f"budget alert: {alert}")
    for wave in data.get("supervised_waves", []):
        counts = wave.get("counts") or {}
        print(
            f"supervised: {wave['project']} wave {wave['wave_id']} · done {counts.get('done', '?')}/{wave.get('total', '?')}"
            f" · supervisor cycle {age(wave.get('cycled_at'))} ago · agent tick {age(wave.get('agent_ticked_at'))} ago"
            + (f" · {wave['open_questions']} open question(s)" if wave.get("open_questions") else "")
        )
    for rig in data["rigs"]:
        reach = "unreachable" if rig["reachable"] is False else ("never probed" if rig["reachable"] is None else f"probed {age(rig['at'])} ago")
        print(f"\n{rig['rig']}  [{reach}]" + ("  shared" if rig.get("shared") else ""))
        if rig["reachable"] and rig.get("gpu_probe_ok") is False:
            print("  nvidia-smi failed on the rig: GPU state unknown")
        gpus = {g["index"]: g for g in rig.get("gpus", [])}
        if not rig["lanes"] and not rig["foreign"] and not rig.get("free_gpus"):
            print("  free" if rig["reachable"] else "  no lanes on the board")
        for lane in rig["lanes"]:
            state = lane.get("observed", {}).get("state", "?")
            eta = lane.get("eta") or "unavailable"
            print(
                f"  gpu{lane['gpu']}  {state:<12} {lane['project']} / {lane['experiment']}  wave {lane['wave_id']}"
                f"  since {age(lane.get('claimed_at'))}  run {lane.get('active_run') or '?'}"
                f"  {lane.get('progress') or ''}  eta {eta}  updated {age(lane.get('updated_at'))} ago"
            )
            run = (lane.get("observed") or {}).get("run")
            total_runs, done_runs = lane.get("runs_total"), lane.get("runs_done")
            if total_runs and done_runs is not None:
                frac = 0.0
                if run and run.get("state") == "done":
                    frac = 1.0
                elif run and run.get("state") == "running" and isinstance(run.get("completed"), (int, float)) and run.get("total"):
                    frac = min(1.0, run["completed"] / run["total"])
                print(f"        overall: {100 * min(total_runs, done_runs + frac) / total_runs:.2f}% of the lane ({done_runs}/{total_runs} runs done + {100 * frac:.2f}% of the current run)")
            if run:
                pct = f" ({100 * run['completed'] / run['total']:.2f}%)" if isinstance(run.get("completed"), (int, float)) and run.get("total") else ""
                hb = f"heartbeat {humanize(run['heartbeat_age_s'])} ago" + (" STALE" if run.get("heartbeat_stale") else "") if run.get("heartbeat_age_s") is not None else "no heartbeat"
                print(
                    f"        live: {run['path']}  {run.get('state')}  {run.get('progress') or '?'}{pct}"
                    f"  elapsed {humanize(run.get('elapsed_s'))}  {hb}  run eta {run.get('eta') or 'unavailable'}"
                )
            tail = (lane.get("observed") or {}).get("last_output")
            if tail:
                print(f"        last output: {tail[:160]}")
        for index in rig["foreign"]:
            print(f"  gpu{index}  foreign      {describe_foreign(gpus.get(index))}")
        for index in rig.get("free_gpus", []):
            print(f"  gpu{index}  free         {describe_gpu(gpus.get(index))}")
        for session in rig.get("stray_sessions", []):
            print(f"  stray tmux session (not a lane): {session}")
    for cluster in data["slurm"]:
        if cluster["reachable"] is False:
            reach = f"unreachable: {cluster.get('reason') or '?'}; showing last known"
        elif cluster["reachable"] is None:
            reach = "never probed"
        else:
            reach = f"probed {age(cluster['at'])} ago"
        done = ", ".join(f"{cluster.get(kind, 0)} {kind}" for kind in FINISHED_KINDS if cluster.get(kind))
        print(
            f"\n{cluster['rig']}  [slurm, {reach}]  {cluster.get('running', 0)} running, {cluster.get('pending', 0)} pending"
            + (f"; last {cluster.get('finished_window', SACCT_WINDOW).replace('now-', '')}: {done}" if done else "")
            + (f"  account {', '.join(cluster['accounts'])}" if cluster.get("accounts") else "")
        )
        for b in cluster.get("budgets", []):
            if not b.get("in_use") and b.get("status") not in ("ok", "low", "expiring"):
                continue
            month = f"  month {b['month_consumed_h']:.0f}/{b['month_total_h']:.0f} h ({b['month_pct']:.2f}%)" if b.get("month_total_h") else ""
            flag = "" if b["status"] == "ok" else f"  [{b['status'].replace('_', ' ')}]"
            print(
                f"  budget {b['account']:<16} {b['consumed_h']:.0f}/{b['total_h']:.0f} h ({b['consumed_pct']:.2f}%)  remaining {b['remaining_h']:.0f} h"
                f"{month}  until {b['end']} ({b['days_left']} d){'  in use' if b.get('in_use') else ''}{flag}  (saldo, nightly)"
            )
        if not cluster["jobs"]:
            print("  no jobs in the queue")
        groups = cluster.get("groups") or group_jobs(list(cluster["jobs"]), list(cluster.get("finished", [])))
        for group in groups:
            if len(group["jobs"]) > 1 or group.get("finished_total"):
                tail = []
                total = group.get("total") or len(group["jobs"])
                if group["running"]:
                    tail.append(f"{group['running']} running (left {group['min_time_left']}…{group['max_time_left']})")
                if group["pending"]:
                    tail.append(f"{group['pending']} pending (earliest start {group['earliest_start'] or '?'}; {', '.join(group['reasons']) or '?'})")
                if group["other"]:
                    tail.append(f"{group['other']} other")
                for kind in FINISHED_KINDS:
                    n = (group.get("finished") or {}).get(kind, 0)
                    if n:
                        tail.append(f"{n} {kind} ({100 * n / total:.2f}%)")
                acct = f"  account {', '.join(group['accounts'])}" if group.get("accounts") else ""
                qos = f"  qos {', '.join(group['qos'])}" if group.get("qos") and group["qos"] != ["normal"] else ""
                print(f"  ── {group['key']}  {group.get('partition') or ''}{acct}{qos}  {total} total: {' · '.join(tail)}")
                if group.get("experiment") and group["experiment"] not in group["key"]:
                    print(f"     experiment: {group['experiment']}")
                if group.get("common"):
                    print(f"     common: {group['common']}")
        by_group: dict[str, list[dict]] = {}
        for job in cluster["jobs"]:
            by_group.setdefault(job.get("group", job["name"]), []).append(job)
        shown = 0
        for group in groups:
            members = by_group.get(group["key"], [])
            limit = len(members) if args.all or len(members) <= 3 else 3
            for job in members[:limit]:
                who = f"{job['project']} / {job['experiment']}  wave {job['wave_id']}" if job.get("wave_id") else (job.get("variant") or job["name"])
                tail = f"elapsed {job['elapsed']}  left {job['time_left']}" if job["state"] == "RUNNING" else f"reason {job.get('reason') or '?'}  start {job.get('start') or '?'}"
                print(f"  {job['job_id']:<10} {job['state']:<12} {who}  {job['partition']}  {job.get('nodes') or '-'}  {tail}")
                shown += 1
            if limit < len(members):
                print(f"  … {len(members) - limit} more in this group (status --all lists every job)")
    return 0


def free(config: BoardConfig, args: argparse.Namespace) -> int:
    rig = require_rig(config, args.rig)
    gpu = validate_gpu(args.gpu)
    if args.reconcile:
        reconcile(config, rigs=[rig.name], quiet=True)
    wanted = gpu_indices(gpu)
    for lane in list_lanes(config):
        if lane.get("rig") == rig.name and gpu_indices(str(lane.get("gpu"))) & wanted:
            eta = lane.get("eta") or "unavailable"
            print(
                f"[held] {rig.name} gpu{lane['gpu']}: {lane['project']} / {lane['experiment']} wave {lane['wave_id']}"
                f" run {lane.get('active_run') or '?'} eta {eta}"
            )
            return 1
    probe = read_json(config.rig_path(rig.name)) or {}
    foreign = set(probe.get("foreign", [])) & wanted
    if foreign:
        print(f"[foreign] {rig.name} gpu{','.join(map(str, sorted(foreign)))} busy without a lane on the board")
        return 1
    if probe.get("reachable") is False:
        print(f"[unreachable] {rig.name}: last probe failed; board says free but unverified")
        return 2
    print(f"[free] {rig.name} gpu{gpu}")
    return 0


# ───────────────────────────── viewer ─────────────────────────────

VIEWER_PATH = Path(__file__).resolve().parent.parent / "assets" / "viewer.html"
FALLBACK_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>rig-board</title></head>
<body><h1>rig-board</h1><p>viewer.html is missing next to board.py; the JSON is still at <a href="/api/board">/api/board</a>.</p></body></html>
"""


def viewer_page() -> bytes:
    """The viewer is a static file next to the script, read per request so edits show without a restart."""
    try:
        return VIEWER_PATH.read_bytes()
    except OSError:
        return FALLBACK_PAGE.encode()


COOKIE_NAME = "rig_board_token"
DENIED = b"rig-board: access token required. Open the page as /?token=<token> once; it is then remembered by a cookie.\n"


def presented_token(handler: BaseHTTPRequestHandler, url) -> tuple[str | None, bool]:
    """(token the client presented, whether it came from the query string)."""
    query = parse_qs(url.query).get("token")
    if query:
        return query[0], True
    header = handler.headers.get("X-Board-Token")
    if header:
        return header, False
    auth = handler.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip(), False
    cookie = SimpleCookie(handler.headers.get("Cookie", ""))
    if COOKIE_NAME in cookie:
        return cookie[COOKIE_NAME].value, False
    return None, False


def make_handler(config: BoardConfig, state: dict | None = None):
    serve_state = state if state is not None else {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: object) -> None:  # quiet
            pass

        def do_GET(self) -> None:  # noqa: N802
            url = urlparse(self.path)
            path = url.path
            if path == "/healthz":
                self._send(200, "text/plain; charset=utf-8", b"ok\n")
                return
            set_cookie = None
            if config.token is not None:
                token, from_query = presented_token(self, url)
                if token is None or not hmac.compare_digest(token, config.token):
                    self._send(401, "text/plain; charset=utf-8", DENIED)
                    return
                if from_query:
                    set_cookie = f"{COOKIE_NAME}={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age=31536000"
            if path == "/api/board":
                data = snapshot(config)
                data["serve"] = {
                    "reconcile_every_s": serve_state.get("reconcile_every"),
                    "last_reconcile_at": serve_state.get("last_reconcile_at"),
                    "last_reconcile_error": serve_state.get("last_reconcile_error"),
                    "reconciling": bool(serve_state.get("reconciling")),
                }
                body = json.dumps(data).encode()
                self._send(200, "application/json; charset=utf-8", body, set_cookie)
            elif path == "/api/history":
                query = parse_qs(url.query)
                try:
                    hours = float(query.get("hours", [HISTORY_DEFAULT_HOURS])[0])
                    limit = int(query.get("limit", [HISTORY_MAX_EVENTS])[0])
                except ValueError:
                    self._send(400, "text/plain; charset=utf-8", b"hours and limit must be numbers\n")
                    return
                include_refresh = query.get("refresh", ["0"])[0] in ("1", "true", "yes")
                hours = min(max(hours, 0.0), 24 * 30)
                limit = min(max(limit, 1), HISTORY_MAX_EVENTS)
                events = read_history(config, hours=hours, limit=limit, include_refresh=include_refresh)
                body = json.dumps({"generated_at": iso(now()), "hours": hours, "events": events}).encode()
                self._send(200, "application/json; charset=utf-8", body, set_cookie)
            elif path in ("/", "/index.html"):
                self._send(200, "text/html; charset=utf-8", viewer_page(), set_cookie)
            else:
                self._send(404, "text/plain; charset=utf-8", b"not found\n")

        def _send(self, code: int, ctype: str, body: bytes, set_cookie: str | None = None) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            if set_cookie:
                self.send_header("Set-Cookie", set_cookie)
            self.end_headers()
            self.wfile.write(body)

    return Handler


def serve(config: BoardConfig, args: argparse.Namespace) -> int:
    stop = threading.Event()
    every = config.reconcile_every if args.reconcile_every is None else args.reconcile_every
    check_reconcile_every(every, "--reconcile-every")
    state: dict = {"reconcile_every": every if every > 0 else None}

    def loop() -> None:
        while not stop.is_set():
            state["reconciling"] = True
            try:
                reconcile(config, quiet=True)
                state["last_reconcile_error"] = None
            except Exception as exc:  # keep serving even if a probe misbehaves
                state["last_reconcile_error"] = str(exc)
                print(f"[serve] reconcile failed: {exc}", file=sys.stderr)
            state["last_reconcile_at"] = iso(now())
            state["reconciling"] = False
            stop.wait(every)

    if every > 0:
        threading.Thread(target=loop, daemon=True).start()
    server = ThreadingHTTPServer((args.bind or config.bind, args.port or config.port), make_handler(config, state))
    guard = "token required" if config.token else "OPEN: no board.token in the registry"
    probing = f"probing rigs every {every} s" if every > 0 else "not probing (reconcile disabled)"
    print(f"[serve] rig-board on http://{server.server_address[0]}:{server.server_address[1]}  root={config.root}  ({guard}; {probing})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()
    return 0


# ───────────────────────────── CLI ─────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--registry", type=Path, help=f"rigsync user registry (default {DEFAULT_REGISTRY})")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("status", help="print the board; --reconcile probes the rigs first")
    p.add_argument("--json", action="store_true")
    p.add_argument("--reconcile", action="store_true")
    p.add_argument("--all", action="store_true", help="list every Slurm job instead of three per group")

    p = sub.add_parser("free", help="exit 0 if <rig> gpu<ids> is free, 1 if held/foreign, 2 if unverifiable")
    p.add_argument("rig")
    p.add_argument("gpu")
    p.add_argument("--reconcile", action="store_true")

    p = sub.add_parser("claim", help="record that a lane's tmux session now holds <rig> gpu<ids>")
    p.add_argument("--rig", required=True)
    p.add_argument("--gpu", required=True)
    p.add_argument("--project", required=True)
    p.add_argument("--experiment", required=True, help="NNN_experiment folder name")
    p.add_argument("--wave", required=True)
    p.add_argument("--project-root", required=True, help="absolute project root on the hub")
    p.add_argument("--runs-total", type=int, required=True)
    p.add_argument("--tmux-session", help="defaults to <project>_<experiment>_<wave>_<rig>_gpu<ids>")

    p = sub.add_parser("refresh", help="update progress/ETA for a held lane (orchestrator tick)")
    p.add_argument("--rig", required=True)
    p.add_argument("--gpu", required=True)
    p.add_argument("--wave", help="guard: refuse if the lane is held by a different wave")
    p.add_argument("--active-run")
    p.add_argument("--progress")
    p.add_argument("--eta", help="ISO timestamp, or '' to clear")
    p.add_argument("--eta-basis")
    p.add_argument("--runs-done", type=int)
    p.add_argument("--runs-total", type=int)

    p = sub.add_parser("release", help="drop a lane (terminal wave, or an explicit decision)")
    p.add_argument("--rig", required=True)
    p.add_argument("--gpu", required=True)
    p.add_argument("--reason", default="lane terminal")

    p = sub.add_parser("reconcile", help="probe the rigs and correct the board")
    p.add_argument("--rig", action="append", dest="rigs", help="limit to this rig or Slurm target (repeatable)")

    p = sub.add_parser("history", help="recent board events (claims, releases, adoptions, interruptions, reconcile changes)")
    p.add_argument("--hours", type=float, default=HISTORY_DEFAULT_HOURS)
    p.add_argument("--limit", type=int, default=HISTORY_MAX_EVENTS)
    p.add_argument("--refresh", action="store_true", help="include the ten-minute orchestrator refresh ticks")
    p.add_argument("--json", action="store_true")

    sub.add_parser("token", help="print a fresh random access token to put under [board] token in the registry")

    p = sub.add_parser("serve", help="read-only web viewer with periodic reconcile")
    p.add_argument("--port", type=int)
    p.add_argument("--bind")
    p.add_argument(
        "--reconcile-every", type=int, help=f"seconds between rig probes (default: [board] reconcile_every, else {DEFAULT_RECONCILE_EVERY_S}; 0 disables; minimum {MIN_RECONCILE_EVERY_S})"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    registry = (args.registry or Path(os.environ.get("RIGSYNC_REGISTRY", DEFAULT_REGISTRY))).expanduser().resolve()
    try:
        config = load_config(registry)
        if args.command == "status":
            return status(config, args)
        if args.command == "free":
            return free(config, args)
        if args.command == "claim":
            return claim(config, args)
        if args.command == "refresh":
            return refresh(config, args)
        if args.command == "release":
            return release(config, args)
        if args.command == "reconcile":
            return reconcile(config, rigs=args.rigs)
        if args.command == "serve":
            return serve(config, args)
        if args.command == "history":
            return history_cli(config, args)
        if args.command == "token":
            print(secrets.token_urlsafe(32))
            return 0
    except BoardError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    sys.exit(main())
