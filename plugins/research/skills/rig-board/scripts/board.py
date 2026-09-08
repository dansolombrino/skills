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
FOREIGN_MEMORY_MIB = 1024
ORCHESTRATOR_SILENT_S = 30 * 60
SSH_TIMEOUT_S = 25

SESSION_RE = re.compile(
    r"^(?P<prefix>.+)_(?P<wave>\d{8}-\d{6})_(?P<rig>[A-Za-z0-9][A-Za-z0-9-]*)_gpu(?P<gpu>\d+(?:,\d+)*)$"
)
EXPERIMENT_SPLIT_RE = re.compile(r"_(?=\d{3}_)")
GPU_RE = re.compile(r"^\d+(?:,\d+)*$")
WAVE_RE = re.compile(r"^\d{8}-\d{6}$")


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
    return BoardConfig(root=root, rigs=rigs, slurm=slurm, port=port, bind=bind, registry_path=registry_path, token=token)


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
            "eta_basis": existing.get("eta_basis") if existing else None,
            "updated_at": stamp,
            "observed": (existing or {}).get("observed", {"at": None, "session_alive": None, "state": "claimed"}),
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

PROBE_SCRIPT = (
    "tmux ls -F '#{session_name}' 2>/dev/null; echo __GPUS__; "
    "nvidia-smi --query-gpu=index,memory.used,utilization.gpu --format=csv,noheader,nounits 2>/dev/null; "
    "echo __BOOT__; uptime -s 2>/dev/null"
)


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


def parse_probe(rig: Rig, output: str) -> dict:
    sessions_part, _, rest = output.partition("__GPUS__")
    gpus_part, _, boot_part = rest.partition("__BOOT__")
    sessions = [line.strip() for line in sessions_part.splitlines() if line.strip()]
    gpus: list[dict] = []
    for line in gpus_part.splitlines():
        cells = [cell.strip() for cell in line.split(",")]
        if len(cells) < 3 or not cells[0].isdigit():
            continue
        try:
            gpus.append(
                {"index": int(cells[0]), "memory_used_mib": int(float(cells[1])), "utilization": int(float(cells[2]))}
            )
        except ValueError:
            continue
    boot = boot_part.strip() or None
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
        "last_reachable_at": (previous or {}).get("at") if (previous or {}).get("reachable") else (previous or {}).get("last_reachable_at"),
    }


SQUEUE_FIELDS = ["job_id", "name", "state", "reason", "partition", "nodes", "elapsed", "time_left", "start", "submitted"]
SQUEUE_FORMAT = "%i|%j|%T|%r|%P|%N|%M|%L|%S|%V"
SQUEUE_SCRIPT = f"squeue --me --noheader --format='{SQUEUE_FORMAT}'; echo __SQUEUE_OK__"


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


def parse_slurm_probe(target: SlurmTarget, output: str) -> dict:
    jobs: list[dict] = []
    for line in output.splitlines():
        if line.strip() == "__SQUEUE_OK__" or "|" not in line:
            continue
        cells = line.split("|")
        if len(cells) != len(SQUEUE_FIELDS):
            continue
        job = dict(zip(SQUEUE_FIELDS, (cell.strip() for cell in cells)))
        parsed = parse_session_name(job["name"]) or {}
        job["project"] = parsed.get("project")
        job["experiment"] = parsed.get("experiment")
        job["wave_id"] = parsed.get("wave_id")
        for key in ("reason", "nodes", "start"):
            if job[key] in ("None", "N/A", "(null)", ""):
                job[key] = None
        jobs.append(job)
    order = {"RUNNING": 0, "COMPLETING": 1, "CONFIGURING": 2, "PENDING": 3}
    jobs.sort(key=lambda j: (order.get(j["state"], 9), j["job_id"]))
    return {
        "rig": target.name,
        "kind": "slurm",
        "at": iso(now()),
        "reachable": True,
        "jobs": jobs,
        "running": sum(1 for j in jobs if j["state"] == "RUNNING"),
        "pending": sum(1 for j in jobs if j["state"] == "PENDING"),
    }


def unreachable_slurm_probe(target: SlurmTarget, previous: dict | None, reason: str) -> dict:
    previous = previous or {}
    return {
        "rig": target.name,
        "kind": "slurm",
        "at": iso(now()),
        "reachable": False,
        "reason": reason,
        "jobs": previous.get("jobs", []),
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
            lane["observed"] = {
                "at": stamp,
                "session_alive": True,
                "state": "running",
                "orchestrator_silent_s": int(silent) if silent is not None else None,
            }
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
            "observed": {"at": stamp, "session_alive": True, "state": "running", "adopted": True},
        }
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
    write_json(config.rig_path(rig.name), probe)
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
                for job_id in sorted(after.keys() - before.keys()):
                    changes.append(f"{target.name}: job {job_id} appeared ({after[job_id]})")
                for job_id in sorted(before.keys() - after.keys()):
                    changes.append(f"{target.name}: job {job_id} left the queue (was {before[job_id]})")
                for job_id in sorted(before.keys() & after.keys()):
                    if before[job_id] != after[job_id]:
                        changes.append(f"{target.name}: job {job_id} {before[job_id]} → {after[job_id]}")
            write_json(config.rig_path(target.name), probe)
        append_history(config, "reconcile", rigs=[t.name for t in targets] + [t.name for t in slurm_targets], changes=changes)
    if not quiet:
        for line in changes:
            print(f"[reconcile] {line}")
        if not changes:
            print("[reconcile] board already matched the rigs")
    return 0


# ───────────────────────────── status ─────────────────────────────


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
            "running": 0,
            "pending": 0,
        }
        slurm.append(probe)
    return {"generated_at": iso(now()), "board_root": str(config.root), "rigs": rigs, "slurm": slurm}


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


def status(config: BoardConfig, args: argparse.Namespace) -> int:
    if args.reconcile:
        reconcile(config, quiet=True)
    data = snapshot(config)
    if args.json:
        print(json.dumps(data, indent=2, sort_keys=True))
        return 0
    print(f"rig-board @ {data['generated_at']}  ({config.root})")
    for rig in data["rigs"]:
        reach = "unreachable" if rig["reachable"] is False else ("never probed" if rig["reachable"] is None else f"probed {age(rig['at'])} ago")
        print(f"\n{rig['rig']}  [{reach}]" + ("  shared" if rig.get("shared") else ""))
        if not rig["lanes"] and not rig["foreign"]:
            print("  free" if rig["reachable"] else "  no lanes on the board")
        for lane in rig["lanes"]:
            state = lane.get("observed", {}).get("state", "?")
            eta = lane.get("eta") or "unavailable"
            print(
                f"  gpu{lane['gpu']}  {state:<12} {lane['project']} / {lane['experiment']}  wave {lane['wave_id']}"
                f"  since {age(lane.get('claimed_at'))}  run {lane.get('active_run') or '?'}"
                f"  {lane.get('progress') or ''}  eta {eta}  updated {age(lane.get('updated_at'))} ago"
            )
        for index in rig["foreign"]:
            print(f"  gpu{index}  foreign      busy without a lane on the board")
    for cluster in data["slurm"]:
        if cluster["reachable"] is False:
            reach = f"unreachable: {cluster.get('reason') or '?'}; showing last known"
        elif cluster["reachable"] is None:
            reach = "never probed"
        else:
            reach = f"probed {age(cluster['at'])} ago"
        print(f"\n{cluster['rig']}  [slurm, {reach}]  {cluster.get('running', 0)} running, {cluster.get('pending', 0)} pending")
        if not cluster["jobs"]:
            print("  no jobs in the queue")
        for job in cluster["jobs"]:
            who = f"{job['project']} / {job['experiment']}  wave {job['wave_id']}" if job.get("wave_id") else job["name"]
            tail = f"elapsed {job['elapsed']}  left {job['time_left']}" if job["state"] == "RUNNING" else f"reason {job.get('reason') or '?'}  start {job.get('start') or '?'}"
            print(f"  {job['job_id']:<10} {job['state']:<12} {who}  {job['partition']}  {job.get('nodes') or '-'}  {tail}")
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

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>rig-board</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--ink:#1c1f24;--muted:#6b7280;--line:#e5e7eb;--free:#15803d;--busy:#b45309;--bad:#b91c1c;--foreign:#6d28d9;--freebg:#ecfdf5;--busybg:#fffbeb;--badbg:#fef2f2;--foreignbg:#f5f3ff}
@media(prefers-color-scheme:dark){:root{--bg:#0f1115;--card:#171a21;--ink:#e6e8ee;--muted:#9aa3b2;--line:#2a2f3a;--freebg:#0f2a1c;--busybg:#2d2208;--badbg:#2e1212;--foreignbg:#1f1836;--free:#4ade80;--busy:#fbbf24;--bad:#f87171;--foreign:#c4b5fd}}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
header{display:flex;align-items:baseline;gap:16px;padding:16px 24px;border-bottom:1px solid var(--line)}
h1{font-size:18px;margin:0}small{color:var(--muted)}
main{display:grid;gap:16px;padding:24px;grid-template-columns:repeat(auto-fill,minmax(420px,1fr))}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.card h2{font-size:15px;margin:0 0 6px;display:flex;justify-content:space-between;align-items:baseline}
.pill{font-size:11px;padding:2px 8px;border-radius:999px;border:1px solid var(--line);color:var(--muted)}
.lane{border-radius:8px;padding:8px 10px;margin:8px 0;border:1px solid var(--line)}
.lane.free{background:var(--freebg);border-color:transparent}.lane.running{background:var(--busybg);border-color:transparent}
.lane.interrupted,.lane.unreachable{background:var(--badbg);border-color:transparent}.lane.foreign,.lane.pending{background:var(--foreignbg);border-color:transparent}
.lane .top{display:flex;justify-content:space-between;font-weight:600}
.state.free{color:var(--free)}.state.running{color:var(--busy)}.state.interrupted,.state.unreachable{color:var(--bad)}.state.foreign,.state.pending{color:var(--foreign)}
.meta{color:var(--muted);font-size:12px;margin-top:4px;word-break:break-all}
.bar{height:5px;background:var(--line);border-radius:3px;margin-top:6px;overflow:hidden}.bar i{display:block;height:100%;background:var(--busy)}
code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px}
.err{color:var(--bad);padding:8px 24px}
</style></head><body>
<header><h1>rig-board</h1><small id="stamp">loading…</small><small id="root"></small></header>
<div class="err" id="err" hidden></div>
<main id="main"></main>
<script>
const fmtAge=s=>{if(s==null)return "?";s=Math.max(0,Math.floor(s));if(s<90)return s+"s";const m=Math.floor(s/60);if(m<90)return m+"m";const h=Math.floor(m/60);return h<48?h+"h"+String(m%60).padStart(2,"0")+"m":Math.floor(h/24)+"d"+(h%24)+"h"};
const ago=(iso,nowMs)=>iso?fmtAge((nowMs-Date.parse(iso))/1000)+" ago":"?";
const until=(iso,nowMs)=>{if(!iso)return "unavailable";const d=(Date.parse(iso)-nowMs)/1000;return isNaN(d)?iso:(d<0?"overdue "+fmtAge(-d):"in "+fmtAge(d)+" ("+new Date(iso).toLocaleTimeString([], {hour:"2-digit",minute:"2-digit"})+")")};
const esc=s=>String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
function render(d){const nowMs=Date.parse(d.generated_at);document.getElementById("stamp").textContent="board read "+new Date(d.generated_at).toLocaleTimeString();document.getElementById("root").textContent=d.board_root;
const main=document.getElementById("main");main.innerHTML="";
for(const rig of d.rigs){const card=document.createElement("section");card.className="card";
const reach=rig.reachable===false?"unreachable · last "+ago(rig.last_reachable_at,nowMs):rig.reachable==null?"never probed":"probed "+ago(rig.at,nowMs);
card.innerHTML=`<h2><span>${esc(rig.rig)}${rig.shared?' <span class="pill">shared</span>':''}</span><span class="pill">${esc(reach)}${rig.boot_at?' · up since '+new Date(rig.boot_at).toLocaleString():''}</span></h2>`;
const held=new Map();for(const l of rig.lanes)held.set(l.gpu,l);
const gpuIdx=rig.gpus.length?rig.gpus.map(g=>g.index):[];const seen=new Set();
for(const l of rig.lanes){for(const i of l.gpu.split(","))seen.add(+i);const st=(l.observed&&l.observed.state)||"claimed";const cls=st==="running"||st==="claimed"?"running":st;
const tot=l.runs_total,done=l.runs_done;const pct=tot&&done!=null?Math.round(100*done/tot):null;
card.insertAdjacentHTML("beforeend",`<div class="lane ${cls}"><div class="top"><span>gpu${esc(l.gpu)} · ${esc(l.project)} / ${esc(l.experiment)}</span><span class="state ${cls}">${esc(st)}</span></div>
<div>wave ${esc(l.wave_id)} · run ${esc(l.active_run||"?")} ${esc(l.progress||"")}</div>
<div>ETA ${esc(until(l.eta,nowMs))}${l.eta_basis?' <small>· '+esc(l.eta_basis)+'</small>':''}${tot?' · '+done+'/'+tot+' runs':''}</div>
${pct!=null?`<div class="bar"><i style="width:${pct}%"></i></div>`:""}
<div class="meta">held ${ago(l.claimed_at,nowMs)} · orchestrator update ${ago(l.updated_at,nowMs)}${l.observed&&l.observed.orchestrator_silent_s>1800?' <b>(silent)</b>':''} · <code>tmux attach -t ${esc(l.tmux_session)}</code></div></div>`)}
for(const i of rig.foreign||[]){seen.add(i);const g=rig.gpus.find(x=>x.index===i);card.insertAdjacentHTML("beforeend",`<div class="lane foreign"><div class="top"><span>gpu${i}</span><span class="state foreign">foreign</span></div><div class="meta">busy without a lane on the board${g?" · "+g.memory_used_mib+" MiB · "+g.utilization+"% util":""}</div></div>`)}
const freeIdx=gpuIdx.filter(i=>!seen.has(i));
if(freeIdx.length)card.insertAdjacentHTML("beforeend",`<div class="lane free"><div class="top"><span>gpu${freeIdx.join(", gpu")}</span><span class="state free">free</span></div></div>`);
else if(!rig.lanes.length&&!(rig.foreign||[]).length)card.insertAdjacentHTML("beforeend",`<div class="lane free"><div class="top"><span>no lanes</span><span class="state free">free</span></div></div>`);
main.appendChild(card)}
for(const c of d.slurm||[]){const card=document.createElement("section");card.className="card";
const reach=c.reachable===false?"unreachable · "+(c.reason||"?")+" · last "+ago(c.last_reachable_at,nowMs):c.reachable==null?"never probed":"probed "+ago(c.at,nowMs);
card.innerHTML=`<h2><span>${esc(c.rig)} <span class="pill">slurm</span></span><span class="pill">${esc(reach)}</span></h2><div class="meta">${c.running||0} running · ${c.pending||0} pending</div>`;
if(!c.jobs.length)card.insertAdjacentHTML("beforeend",`<div class="lane free"><div class="top"><span>queue empty</span><span class="state free">idle</span></div></div>`);
for(const j of c.jobs){const run=j.state==="RUNNING";const cls=run?"running":(j.state==="PENDING"?"pending":"interrupted");const who=j.wave_id?`${esc(j.project)} / ${esc(j.experiment)} · wave ${esc(j.wave_id)}`:esc(j.name);
card.insertAdjacentHTML("beforeend",`<div class="lane ${cls}"><div class="top"><span>${esc(j.job_id)} · ${who}</span><span class="state ${cls}">${esc(j.state)}</span></div>
<div>${run?`elapsed ${esc(j.elapsed)} · left ${esc(j.time_left)} · ${esc(j.nodes||"?")}`:`reason ${esc(j.reason||"?")} · start ${esc(j.start||"unknown")} · submitted ${esc(j.submitted||"?")}`}</div>
<div class="meta">${esc(j.partition)} · <code>${esc(j.name)}</code></div></div>`)}
main.appendChild(card)}}
async function tick(){try{const q=new URLSearchParams(location.search).get("token");const r=await fetch("/api/board"+(q?"?token="+encodeURIComponent(q):""),{cache:"no-store"});if(!r.ok)throw new Error(r.status+" "+r.statusText);render(await r.json());document.getElementById("err").hidden=true}catch(e){const el=document.getElementById("err");el.hidden=false;el.textContent="board unavailable: "+e.message}}
tick();setInterval(tick,5000);
</script></body></html>
"""


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


def make_handler(config: BoardConfig):
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
                body = json.dumps(snapshot(config)).encode()
                self._send(200, "application/json; charset=utf-8", body, set_cookie)
            elif path in ("/", "/index.html"):
                self._send(200, "text/html; charset=utf-8", PAGE.encode(), set_cookie)
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

    def loop() -> None:
        while not stop.is_set():
            try:
                reconcile(config, quiet=True)
            except Exception as exc:  # keep serving even if a probe misbehaves
                print(f"[serve] reconcile failed: {exc}", file=sys.stderr)
            stop.wait(args.reconcile_every)

    if args.reconcile_every > 0:
        threading.Thread(target=loop, daemon=True).start()
    server = ThreadingHTTPServer((args.bind or config.bind, args.port or config.port), make_handler(config))
    guard = "token required" if config.token else "OPEN: no board.token in the registry"
    print(f"[serve] rig-board on http://{server.server_address[0]}:{server.server_address[1]}  root={config.root}  ({guard})")
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

    sub.add_parser("token", help="print a fresh random access token to put under [board] token in the registry")

    p = sub.add_parser("serve", help="read-only web viewer with periodic reconcile")
    p.add_argument("--port", type=int)
    p.add_argument("--bind")
    p.add_argument("--reconcile-every", type=int, default=120, help="seconds; 0 disables")
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
        if args.command == "token":
            print(secrets.token_urlsafe(32))
            return 0
    except BoardError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    sys.exit(main())
