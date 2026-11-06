#!/usr/bin/env python3
"""Reconcile EXPERIMENTS.md against the signals on this machine; flag what takes judgment.

    python3 reconcile.py [--root <project>] [--wave <wave_id>] [--dry-run | --confirm] [--json]

Reads the project's EXPERIMENTS.md, each wave's supervisor record (.waves/_state/<wave>/), the runs'
local .status.json and artifacts, the wave READMEs, the wave tags and the run-identity files. It
never contacts another machine and never changes anything but EXPERIMENTS.md, so a rig that has not
reported to the hub yet shows up as a flag rather than as a change.

The default is a dry run. It prints the proposed row changes, integrity errors, judgment cases,
computed ETAs and the finished waves whose worktree remains. --confirm applies the proposed changes,
which are only the unambiguous ones: a provenance-valid terminal status with its artifact agreeing,
a live run's progress and placement, the eta column. Anything else is flagged and the row left alone:
a frozen `running` (interrupted, hung or failed), a moved run, a provenance mismatch, a missing
artifact, a gone evaluation directory.

A supervised wave's rows have one writer. While a wave is live --confirm leaves its rows, unless the
caller is a supervisor agent (SWEEP_SUPERVISOR_MODEL is set) naming that wave with --wave.

ETAs come from the supervisor's own `remaining_active` and, for a live wave, `brief_view`, so the
skill's hierarchy has one implementation. The history steps the supervisor has no data for (the same
run_id in other waves, the experiment median) use this file's `elapsed` column.

Exit status: 0 nothing flagged, 1 at least one flag (changes, if confirmed, were still applied),
2 could not run (no EXPERIMENTS.md, --confirm from a linked worktree, the file changed meanwhile).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import re
import statistics
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
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


sup = _load_sibling("sweep-supervisor", "supervisor.py", "reconcile_supervisor")  # read-only uses only

EXPERIMENTS = "EXPERIMENTS.md"
LAYOUT = "hashed-v2"
RID_RE = re.compile(r"^rid-[0-9a-f]{32}$")
STATUSES = ("todo", "inpr", "done", "failed", "superseded")
TERMINAL = ("done", "failed", "superseded")
REQUIRED_COLUMNS = ("run_id", "label", "wave", "rig", "gpu", "status", "started", "progress", "eta", "ended", "elapsed", "notes")
FINGERPRINT_RE = re.compile(r"Environment fingerprint:\s*`?([0-9a-f]{64})`?")
VERSION_RE = re.compile(r"^run_id version:\s*(\d+)\s*[—-]\s*([^(\n]*?)\s*(?:\(mirrors ([^)]+)\))?\s*$", re.MULTILINE)
LAYOUT_RE = re.compile(r"^run_id path layout:\s*(\S+)\s*(?:\(mirrors RUN_ID_PATH_LAYOUT in ([^)]+)\))?\s*$", re.MULTILINE)
MAP_RE = re.compile(r"^run_id map:\s*(\S+)\s*$", re.MULTILINE)
CODE_LAYOUT_RE = re.compile(r"^RUN_ID_PATH_LAYOUT\s*=\s*[\"']([^\"']+)[\"']", re.MULTILINE)
ELAPSED_RE = re.compile(r"^(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?$")


# ─────────────────────────── EXPERIMENTS.md ───────────────────────────

def split_cells(line: str) -> list[str]:
    """The raw cells of a table line, padding kept. An escaped pipe stays inside its cell."""
    body = line.strip()
    body = body[1:] if body.startswith("|") else body
    body = body[:-1] if body.endswith("|") and not body.endswith("\\|") else body
    return re.split(r"(?<!\\)\|", body)


@dataclass
class Row:
    section: "Section"
    line: int  # index into Document.lines
    raw: list[str]
    cells: dict[str, str]

    @property
    def run_id(self) -> str:
        return self.cells["run_id"]

    @property
    def wave(self) -> str:
        return self.cells["wave"]

    @property
    def status(self) -> str:
        return self.cells["status"]

    def params(self) -> list[str]:
        return self.section.columns[: self.section.columns.index("run_id")]


@dataclass
class Section:
    name: str
    line: int
    preamble: str = ""
    columns: list[str] = field(default_factory=list)
    rows: list[Row] = field(default_factory=list)


@dataclass
class Document:
    lines: list[str]
    sections: list[Section]
    shape_errors: list[dict]


def parse(text: str) -> Document:
    lines = text.splitlines(keepends=True)
    sections: list[Section] = []
    shape: list[dict] = []
    section: Section | None = None
    in_table = False
    fenced = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            continue
        if line.startswith("## "):
            section = Section(stripped[3:].strip(), index)
            sections.append(section)
            in_table = False
            continue
        if section is None:
            continue
        if not stripped.startswith("|"):
            in_table = False
            if not section.columns:
                section.preamble += line
            continue
        cells = split_cells(line)
        names = [cell.strip() for cell in cells]
        if all(re.fullmatch(r":?-+:?", name) for name in names):
            continue
        if not in_table:
            in_table = True
            if {"run_id", "wave", "status"} <= set(names):
                section.columns = names
                missing = [c for c in REQUIRED_COLUMNS if c not in names]
                if missing:
                    shape.append({"section": section.name, "line": index + 1, "msg": f"table has no column {', '.join(missing)}"})
                    section.columns = []
            continue
        if not section.columns:
            continue
        if len(cells) != len(section.columns):
            shape.append({"section": section.name, "line": index + 1,
                          "msg": f"row has {len(cells)} cells under {len(section.columns)} columns (a shifted or broken row)"})
            continue
        section.rows.append(Row(section, index, cells, dict(zip(section.columns, names))))
    return Document(lines, sections, shape)


def render_row(row: Row, changes: dict[str, str]) -> str:
    """The row's line with only the changed cells replaced; a value keeps the cell's width if it fits."""
    raw = list(row.raw)
    for column, value in changes.items():
        position = row.section.columns.index(column)
        width = len(raw[position])
        raw[position] = f" {value}".ljust(width - 1) + " " if len(value) + 2 <= width else f" {value} "
    return "|" + "|".join(raw) + "|"


def apply_changes(path: Path, original: str, document: Document, proposals: list[dict]) -> None:
    lines = list(document.lines)
    for proposal in proposals:
        row = proposal["row"]
        ending = lines[row.line][len(lines[row.line].rstrip("\r\n")):]
        lines[row.line] = render_row(row, proposal["changes"]) + ending
    if path.read_text() != original:
        raise ReconcileError(f"{path} changed while reconciling; nothing written, run it again")
    handle, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    with os.fdopen(handle, "w") as out:
        out.write("".join(lines))
    os.chmod(tmp, path.stat().st_mode & 0o7777)
    os.replace(tmp, path)


class ReconcileError(RuntimeError):
    pass


# ─────────────────────────── values ───────────────────────────

def compact(seconds: object) -> str:
    """`45m`, `1h45m`, `2d3h`: the `elapsed` cell."""
    if not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or seconds < 0:
        return ""
    total = int(seconds)
    if total < 60:
        return f"{total}s"
    minutes = total // 60
    if minutes < 60:
        return f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h{minutes}m" if minutes else f"{hours}h"
    days, hours = divmod(hours, 24)
    return f"{days}d{hours}h" if hours else f"{days}d"


def parse_elapsed(cell: str) -> float | None:
    match = ELAPSED_RE.fullmatch(cell.strip())
    if not cell.strip() or not match:
        return None
    days, hours, minutes, seconds = (int(part or 0) for part in match.groups())
    return float(days * 86400 + hours * 3600 + minutes * 60 + seconds) or None


def stamp(value: object) -> str:
    """`MM-DD HH:MM`, local time."""
    parsed = value if isinstance(value, datetime) else sup.parse_iso(value)
    return parsed.astimezone().strftime("%m-%d %H:%M") if parsed else ""


def progress_cell(signal: dict) -> str:
    done, total = signal.get("progress_completed"), signal.get("progress_total")
    if isinstance(done, (int, float)) and isinstance(total, (int, float)):
        unit = signal.get("progress_unit")
        return f"{unit} {done}/{total}" if unit else f"{done}/{total}"
    return str(signal.get("progress") or "")


def same_value(cell: str, value: object) -> bool:
    if value is None:
        return cell.lower() in ("", "null", "none")
    if isinstance(value, bool):
        return cell.lower() == str(value).lower()
    if isinstance(value, (int, float)):
        try:
            return math.isclose(float(cell), float(value), rel_tol=1e-12)
        except ValueError:
            return False
    if isinstance(value, str):
        return cell == value
    return cell in (json.dumps(value), json.dumps(value, sort_keys=True), str(value))


def label_values(label: str, params: list[str]) -> dict[str, str] | None:
    """`k=v,...` in param order, values cut at the next known key; None if it is not that shape."""
    values, rest = {}, label
    for position, param in enumerate(params):
        if not rest.startswith(f"{param}="):
            return None
        rest = rest[len(param) + 1:]
        following = f",{params[position + 1]}=" if position + 1 < len(params) else None
        cut = rest.find(following) if following else len(rest)
        if cut < 0:
            return None
        values[param] = rest[:cut]
        rest = rest[cut + (1 if following else 0):]
    return values if not rest else None


def read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


# ─────────────────────────── the project on disk ───────────────────────────

class Project:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._queues: dict[str, dict | None] = {}
        self._tags: dict[str, str] | None = None
        self._readmes: dict[Path, str | None] = {}

    def queue(self, wave: str) -> dict | None:
        if wave not in self._queues:
            queue = read_json(sup.Wave(self.root, wave).queue_path)
            self._queues[wave] = queue if isinstance(queue, dict) and isinstance(queue.get("runs"), list) else None
        return self._queues[wave]

    def queue_run(self, wave: str, run_id: str) -> dict | None:
        queue = self.queue(wave)
        return next((run for run in queue["runs"] if run.get("id") == run_id), None) if queue else None

    def live(self, wave: str) -> bool:
        queue = self.queue(wave)
        return queue is not None and not queue.get("finished_at")

    def tags(self) -> dict[str, str]:
        """wave tag -> commit, peeled; one git call for all of them."""
        if self._tags is None:
            self._tags = {}
            env = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE")}
            try:
                out = subprocess.run(
                    ["git", "-C", str(self.root), "for-each-ref", "--format=%(refname:short) %(objectname) %(*objectname)", "refs/tags/wave--*"],
                    capture_output=True, text=True, timeout=20, env=env, check=False,
                ).stdout
            except (OSError, subprocess.TimeoutExpired):
                out = ""
            for line in out.splitlines():
                parts = line.split()
                if len(parts) >= 2:
                    self._tags[parts[0]] = parts[-1]
        return self._tags

    def revision(self, wave: str) -> str | None:
        queue = self.queue(wave)
        return (queue or {}).get("revision") or self.tags().get(f"wave--{wave}")

    def readme_fingerprint(self, path: Path) -> str | None:
        if path not in self._readmes:
            try:
                match = FINGERPRINT_RE.search(path.read_text())
            except OSError:
                match = None
            self._readmes[path] = match.group(1) if match else None
        return self._readmes[path]

    def weights(self) -> dict[str, float]:
        """Rig weights as the wave records give them, later waves winning; the registry is not read."""
        merged: dict[str, float] = {}
        for path in sorted((self.root / ".waves" / sup.STATE_DIR).glob("*/queue.json")):
            queue = read_json(path)
            if isinstance(queue, dict) and isinstance(queue.get("weights"), dict):
                merged.update({rig: float(w) for rig, w in queue["weights"].items() if isinstance(w, (int, float)) and w > 0})
        return merged


def run_paths(section: Section, row: Row, run: dict | None) -> dict[str, Path | None]:
    """Exact paths from the supervisor's record; under hashed-v2 the layout gives the rest."""
    hashed = RID_RE.fullmatch(row.run_id) is not None
    run = run or {}
    default_eval = Path("evaluations") / section.name / row.run_id if hashed else None
    eval_dir = Path(run["eval_dir"]) if run.get("eval_dir") else default_eval
    status = Path(run["status_path"]) if run.get("status_path") else (default_eval / ".status.json" if default_eval else None)
    if run.get("script"):
        readme = Path(run["script"]).with_name("README.md")
    else:
        readme = Path("scripts") / section.name / row.run_id / f"wave_{row.wave}" / "README.md" if hashed else None
    return {"eval": eval_dir, "status": status, "readme": readme, "artifact": Path(run["artifact"]) if run.get("artifact") else None}


# ─────────────────────────── flags ───────────────────────────

class Flags:
    """Integrity errors say a record is wrong; judgment cases say a person or the model must decide."""

    def __init__(self) -> None:
        self.integrity: list[dict] = []
        self.judgment: list[dict] = []

    def add(self, bucket: list[dict], kind: str, msg: str, row: Row | None = None, **extra: object) -> None:
        item: dict = {"kind": kind, "msg": msg, **extra}
        if row is not None:
            item.update(section=row.section.name, run_id=row.run_id, wave=row.wave, line=row.line + 1)
        bucket.append(item)

    def error(self, kind: str, msg: str, row: Row | None = None, **extra: object) -> None:
        self.add(self.integrity, kind, msg, row, **extra)

    def judge(self, kind: str, msg: str, row: Row | None = None, **extra: object) -> None:
        self.add(self.judgment, kind, msg, row, **extra)


def check_section_header(project: Project, section: Section, flags: Flags) -> str | None:
    """The preamble against what it mirrors. Returns the section's path layout."""
    name = section.name
    layout = LAYOUT_RE.search(section.preamble)
    if not layout:
        flags.error("header-mirror", f"{name}: no `run_id path layout:` line", section=name)
    elif layout.group(2):
        source = project.root / layout.group(2)
        try:
            pinned = CODE_LAYOUT_RE.search(source.read_text())
        except OSError:
            flags.error("header-mirror", f"{name}: the layout line mirrors {layout.group(2)}, which does not exist", section=name)
        else:
            if pinned and pinned.group(1) != layout.group(1):
                flags.error("header-mirror", f"{name}: header says layout {layout.group(1)}, {layout.group(2)} pins {pinned.group(1)}", section=name)
    version_line = VERSION_RE.search(section.preamble)
    versions_path = project.root / "config" / name / "RUN_ID_VERSIONS.json"
    versions = read_json(versions_path)
    current = versions[-1] if isinstance(versions, list) and versions and isinstance(versions[-1], dict) else None
    if current is None:
        flags.error("header-mirror", f"{name}: no readable config/{name}/RUN_ID_VERSIONS.json to mirror", section=name)
    elif not version_line:
        flags.error("header-mirror", f"{name}: no `run_id version:` line", section=name)
    else:
        params = [p.strip() for p in version_line.group(2).split(",") if p.strip()]
        if int(version_line.group(1)) != current.get("version") or params != current.get("params"):
            flags.error("header-mirror", f"{name}: header says version {version_line.group(1)} ({', '.join(params)}), "
                                         f"RUN_ID_VERSIONS.json says {current.get('version')} ({', '.join(map(str, current.get('params') or []))})", section=name)
    if current is not None and section.columns:
        columns = section.columns[: section.columns.index("run_id")]
        if columns != current.get("params"):
            flags.error("header-mirror", f"{name}: param columns {columns} are not the current identity params {current.get('params')}", section=name)
    map_line = MAP_RE.search(section.preamble)
    expected_map = f"evaluations/{name}/RUN_ID_MAP.json"
    if map_line and map_line.group(1) != expected_map:
        flags.error("header-mirror", f"{name}: header names map {map_line.group(1)}, expected {expected_map}", section=name)
    return layout.group(1) if layout else None


def check_row_identity(project: Project, row: Row, layout: str | None, run_map: dict | None, history: list, seen: set, flags: Flags) -> bool:
    """A row's cells against the identity records. False when the row cannot be reconciled at all."""
    if row.status not in STATUSES:
        flags.error("bad-status", f"status {row.status!r} is not one of {', '.join(STATUSES)}", row)
        return False
    if not sup.rigsync.WAVE_RE.fullmatch(row.wave):
        flags.error("bad-wave", f"wave {row.wave!r} is not YYYYMMDD-HHMMSS", row)
        return False
    key = (row.section.name, row.run_id, row.wave)
    if key in seen:
        flags.error("duplicate-row", "a second row for the same (run, wave)", row)
    seen.add(key)
    if layout == LAYOUT and not RID_RE.fullmatch(row.run_id):
        flags.error("bad-run-id", f"run_id {row.run_id!r} is not rid-<32 hex> under {LAYOUT}", row)
        return False
    params = row.params()
    values = label_values(row.cells["label"], params) if layout == LAYOUT else {param: row.cells[param] for param in params}
    if values is None:
        flags.error("label-mismatch", f"label {row.cells['label']!r} is not `k=v,…` over {', '.join(params)}", row)
    else:
        for param in params:
            if values[param] != row.cells[param] and not (_number(values[param]) is not None and _number(values[param]) == _number(row.cells[param])):
                flags.error("label-mismatch", f"label says {param}={values[param]}, the column says {row.cells[param]}", row)
    if run_map is not None and row.status != "todo":
        entry = (run_map.get("by_hash") or {}).get(row.run_id)
        if entry is None:
            migrated = any(row.run_id in (item.get("map") or {}) for item in history if isinstance(item, dict))
            flags.error("stale-hash" if migrated else "unknown-hash",
                        "this hash was migrated away (RUN_ID_HISTORY.json)" if migrated else "this hash is not in RUN_ID_MAP.json", row)
        else:
            for param, value in (entry.get("params") or {}).items():
                if param in row.cells and not same_value(row.cells[param], value):
                    flags.error("params-mismatch", f"RUN_ID_MAP.json says {param}={value}, the column says {row.cells[param]}", row)
    return True


def _number(text: str) -> float | None:
    try:
        return float(text)
    except ValueError:
        return None


def provenance_problems(project: Project, row: Row, status: dict, readme: Path | None) -> list[str]:
    problems = []
    tag = f"wave--{row.wave}"
    if status.get("source_tag") != tag:
        problems.append(f"source_tag {status.get('source_tag')!r}, expected {tag}")
    revision = project.revision(row.wave)
    if revision is None:
        problems.append(f"no {tag} tag or supervisor record to check source_revision against")
    elif status.get("source_revision") != revision:
        problems.append(f"source_revision {status.get('source_revision')!r}, expected {revision}")
    fingerprint = project.readme_fingerprint(project.root / readme) if readme else None
    if fingerprint is None:
        problems.append("the wave README records no environment fingerprint to check against")
    elif status.get("environment_fingerprint") != fingerprint:
        problems.append(f"environment_fingerprint {status.get('environment_fingerprint')!r}, the README records {fingerprint}")
    return problems


# ─────────────────────────── ETA ───────────────────────────

def bad_bounds(signal: dict) -> bool:
    done, total = signal.get("progress_completed"), signal.get("progress_total")
    if done is None and total is None:
        return False
    return not (isinstance(done, (int, float)) and isinstance(total, (int, float)) and 0 <= done <= total and total > 0)


def history_estimate(rows: list[Row], row: Row, rig: str, weights: dict[str, float]) -> tuple[float | None, str]:
    """The hierarchy's steps past this wave, from this section's `done` rows."""
    done = [(r, parse_elapsed(r.cells["elapsed"])) for r in rows if r.status == "done" and r.wave != row.wave]
    done = [(r, value) for r, value in done if value]
    exact = [value for r, value in done if r.run_id == row.run_id and r.cells["rig"] == rig]
    if exact:
        return statistics.median(exact), "exact-run history"
    target = weights.get(rig)
    if not target:
        return None, "no basis yet"
    same = [value * weights[r.cells["rig"]] for r, value in done if r.run_id == row.run_id and r.cells["rig"] in weights]
    if same:
        return statistics.median(same) / target, "exact-run history (weight-normalized)"
    others = [value * weights[r.cells["rig"]] for r, value in done if r.cells["rig"] in weights]
    if others:
        return statistics.median(others) / target, "experiment median (weight-normalized)"
    return None, "no basis yet"


def estimate(project: Project, row: Row, signal: dict, queue_run: dict | None, rig: str, at: datetime, weights: dict[str, float]) -> dict:
    """{remaining_s, at, basis} or {unavailable: why}: the supervisor's numbers first."""
    if not rig:
        return {"unavailable": "no rig known"}
    if bad_bounds(signal):
        return {"unavailable": "invalid progress bounds"}
    queue = project.queue(row.wave)
    if queue is not None and queue_run is not None and rig in (queue.get("weights") or {}):
        source, run = queue, {**queue_run, "observed": signal}
    else:
        known = weights if rig in weights else {}
        peers = [
            {"state": "done", "lane": {"rig": r.cells["rig"], "gpu": r.cells["gpu"]}, "cost_s": None, "observed": {"elapsed_s": parse_elapsed(r.cells["elapsed"])}}
            for r in row.section.rows if r.wave == row.wave and r.status == "done"
        ]
        source, run = {"weights": known, "runs": peers}, {"cost_s": None, "observed": signal}
    remaining, basis, stale = sup.remaining_active(source, run, rig, at)
    if stale:
        return {"unavailable": "heartbeat older than three minutes"}
    if remaining is None:
        full, basis = history_estimate(row.section.rows, row, rig, weights)
        if full is None:
            return {"unavailable": basis}
        elapsed = signal.get("elapsed_s")
        remaining = max(0.0, full - (float(elapsed) if isinstance(elapsed, (int, float)) else 0.0))
    return {"remaining_s": round(remaining), "at": sup.iso(at + timedelta(seconds=remaining)), "basis": basis}


def wave_eta(project: Project, wave: str, rows: list[Row], etas: dict[int, dict], at: datetime) -> dict:
    """A live wave's table is the supervisor's; otherwise the latest lane, if every lane is estimable."""
    if project.live(wave):
        snapshot = read_json(sup.Wave(project.root, wave).snapshot_path)
        if isinstance(snapshot, dict):
            try:
                view = sup.brief_view(snapshot, at)
            except (KeyError, TypeError, AttributeError):
                view = None
            if view:
                eta = view["eta"]
                return {"kind": eta["kind"], "at": eta["at"], "basis": eta["basis"], "source": "supervisor"}
    live_rows = [row for row in rows if row.status not in TERMINAL]
    if not live_rows:
        return {"kind": "finished", "at": None, "basis": None}
    blockers = [f"{sum(row.status == 'todo' for row in live_rows)} todo rows have no lane"] if any(row.status == "todo" for row in live_rows) else []
    latest: datetime | None = None
    for row in live_rows:
        if row.status != "inpr":
            continue
        eta = etas.get(row.line) or {"unavailable": "no estimate"}
        if "at" in eta:
            moment = sup.parse_iso(eta["at"])
            latest = max(latest, moment) if latest else moment
        else:
            blockers.append(f"{row.cells['rig'] or '?'} gpu{row.cells['gpu'] or '?'}: {eta['unavailable']}")
    if blockers or latest is None:
        return {"kind": "unavailable", "at": None, "basis": "; ".join(blockers) or "nothing in progress"}
    return {"kind": "eta", "at": sup.iso(latest), "basis": "latest lane completion"}


# ─────────────────────────── one row ───────────────────────────

def changes_for(row: Row, wanted: dict[str, str]) -> dict[str, str]:
    return {column: value for column, value in wanted.items() if row.cells.get(column) != value}


def placement(row: Row, signal: dict, lane: dict | None) -> dict[str, str]:
    """rig and gpu, filled only while blank: a moved run keeps its row and says so in its notes."""
    out = {}
    for column, value in (("rig", signal.get("rig") or (lane or {}).get("rig")), ("gpu", signal.get("gpu") or (lane or {}).get("gpu"))):
        if value is not None and not row.cells[column]:
            out[column] = str(value)
    return out


def running_changes(row: Row, signal: dict, eta: dict) -> dict[str, str]:
    return {
        "status": "inpr",
        "started": row.cells["started"] or stamp(signal.get("started")),
        "progress": progress_cell(signal) or row.cells["progress"],
        "eta": stamp(eta.get("at")) if "at" in eta else "",
    }


def fresher(status: dict, observed: dict) -> dict:
    """The newer of a local status copy and what the supervisor last read on the rig."""
    local, remote = sup.parse_iso(status.get("heartbeat")), sup.parse_iso(observed.get("heartbeat"))
    return observed if remote and (local is None or remote > local) else status


def artifact_present(project: Project, paths: dict[str, Path | None], queue_run: dict | None) -> bool | None:
    """True on a local artifact or receipt, or when the supervisor saw it on the rig; None when no record names it."""
    artifact = paths["artifact"]
    if artifact is None:
        return None
    local = project.root / artifact
    if local.exists() or local.with_name(local.name + ".offloaded.json").exists():
        return True
    return bool(queue_run and queue_run.get("state") == "done")


def reconcile_row(project: Project, row: Row, at: datetime, weights: dict[str, float], flags: Flags) -> tuple[dict[str, str], dict | None]:
    """(changes, eta) for one row. Changes only when the signals leave one reading; otherwise a flag."""
    state = row.status
    if state == "superseded":
        return {}, None
    queue_run = project.queue_run(row.wave, row.run_id)
    paths = run_paths(row.section, row, queue_run)
    status_file = project.root / paths["status"] if paths["status"] else None
    status = read_json(status_file) if status_file and status_file.exists() else None
    if status_file and status_file.exists() and not isinstance(status, dict):
        flags.judge("status-unreadable", f"{paths['status']} does not parse", row)
        status = None
    own = status if status and status.get("wave_id") == row.wave else None
    queue_state = (queue_run or {}).get("state")

    if state in ("done", "failed"):
        if own and own.get("state") != state:
            flags.judge("terminal-disagrees", f"the row says {state}, this wave's status says {own.get('state')}", row)
        elif queue_state in ("done", "failed") and queue_state != state:
            flags.judge("terminal-disagrees", f"the row says {state}, the supervisor says {queue_state}", row)
        return {}, None

    if own is None:
        return _without_local_status(project, row, status, queue_run, paths, at, weights, flags)

    problems = provenance_problems(project, row, own, paths["readme"])
    if problems:
        flags.error("provenance", "; ".join(problems), row)
        return (changes_for(row, {"eta": ""}) if state == "inpr" else {}), {"unavailable": "integrity mismatch"}

    rig = str(own.get("rig") or "")
    if row.cells["rig"] and rig and row.cells["rig"] != rig and rig not in row.cells["notes"]:
        flags.judge("moved-run", f"the row names {row.cells['rig']}, the status {rig}, and the notes do not say it moved", row)
        return {}, None

    verdict = own.get("state")
    if verdict in ("done", "failed") and queue_state in ("assigned", "running"):
        flags.judge("queue-disagrees", f"the status says {verdict}, the supervisor has this run {queue_state} (a relaunch in this wave?)", row)
        return {}, None
    closing = {"ended": stamp(own.get("ended")), "elapsed": compact(own.get("elapsed_s")), "eta": "",
               "started": row.cells["started"] or stamp(own.get("started")), "progress": progress_cell(own) or row.cells["progress"]}
    if verdict == "running" and queue_state in ("done", "failed"):
        flags.judge("no-local-status", f"the supervisor says {queue_state}; the status copy on this machine still says running, so the end's provenance cannot be checked here", row)
        return {}, None
    if verdict == "done":
        present = artifact_present(project, paths, queue_run)
        if present is None:
            flags.judge("artifact-unverified", "status done, but no supervisor record names the final artifact to check", row)
            return {}, None
        if not present:
            flags.judge("artifact-missing", f"status done, but {paths['artifact']} and its .offloaded.json receipt are missing", row)
            return {}, None
        return changes_for(row, {"status": "done", **closing, **placement(row, own, None)}), None
    if verdict == "failed":
        if artifact_present(project, paths, queue_run):
            flags.judge("failed-with-artifact", "status failed, but the final artifact exists: artifacts win, look at the log", row)
            return {}, None
        return changes_for(row, {"status": "failed", **closing, **placement(row, own, None)}), None
    if verdict == "running":
        rig = row.cells["rig"] or rig
        signal = fresher(own, (queue_run or {}).get("observed") or {})
        eta = estimate(project, row, signal, queue_run, rig, at, weights)
        if eta.get("unavailable", "").startswith("heartbeat"):
            flags.judge("frozen-running", f"status running, heartbeat {signal.get('heartbeat')}: hung or interrupted? check boot time and the lane's tmux session", row)
            return (changes_for(row, {"eta": ""}) if state == "inpr" else {}), eta
        return changes_for(row, {**running_changes(row, signal, eta), **placement(row, own, (queue_run or {}).get("lane"))}), eta
    flags.judge("status-unknown", f"status state {verdict!r}", row)
    return {}, None


def _without_local_status(project: Project, row: Row, status: dict | None, queue_run: dict | None, paths: dict, at: datetime, weights: dict[str, float], flags: Flags) -> tuple[dict[str, str], dict | None]:
    """No status of this wave on this machine: the supervisor's record is what is left."""
    state, queue_state = row.status, (queue_run or {}).get("state")
    if status is not None and state == "inpr":
        flags.judge("status-other-wave", f"the status file belongs to wave {status.get('wave_id')} (relaunched or moved?)", row)
    if queue_state in ("done", "failed"):
        flags.judge("no-local-status", f"the supervisor says {queue_state}; no status of this wave on this machine, so provenance cannot be checked here (collect it, or read it on {((queue_run or {}).get('lane') or {}).get('rig') or 'the rig'})", row)
        return {}, None
    if queue_state == "excluded":
        flags.judge("queue-excluded", f"the supervisor excluded this run: {queue_run.get('note') or 'no note'}", row)
        return {}, None
    if queue_state in ("assigned", "running"):
        lane = queue_run.get("lane")
        signal = queue_run.get("observed") or {}
        if queue_state == "assigned":
            return changes_for(row, placement(row, {}, lane)), None
        rig = row.cells["rig"] or (lane or {}).get("rig") or ""
        if row.cells["rig"] and (lane or {}).get("rig") not in (None, row.cells["rig"]) and lane["rig"] not in row.cells["notes"]:
            flags.judge("moved-run", f"the row names {row.cells['rig']}, the supervisor's lane {lane['rig']}, and the notes do not say it moved", row)
            return {}, None
        eta = estimate(project, row, signal, queue_run, rig, at, weights)
        if eta.get("unavailable", "").startswith("heartbeat"):
            flags.judge("frozen-running", f"the supervisor has it running, heartbeat {signal.get('heartbeat')}: hung or interrupted? check boot time and the lane's tmux session", row)
            return (changes_for(row, {"eta": ""}) if state == "inpr" else {}), eta
        return changes_for(row, {**running_changes(row, signal, eta), **placement(row, {}, lane)}), eta
    if state == "inpr":
        eval_dir = paths["eval"]
        if eval_dir is not None and not (project.root / eval_dir).exists():
            flags.judge("evaluation-gone", f"{eval_dir} is not on this machine: back to todo, or still on {row.cells['rig'] or 'its rig'}?", row)
        elif status is None:
            flags.judge("no-signal", "in progress with no status here and no supervisor record", row)
        return changes_for(row, {"eta": ""}), {"unavailable": "no signal on this machine"}
    return {}, None


# ─────────────────────────── the pass ───────────────────────────

def worktree_main(root: Path) -> Path | None:
    return sup.rigsync.linked_worktree_main(root)


def find_root(start: Path) -> Path:
    for directory in (start, *start.parents):
        if (directory / EXPERIMENTS).is_file():
            return directory
    return start


def reconcile(root: Path, only_wave: str | None, at: datetime, agent: bool) -> tuple[dict, Document, str, list[dict]]:
    path = root / EXPERIMENTS
    try:
        text = path.read_text()
    except OSError as exc:
        raise ReconcileError(f"no readable {path}: {exc}") from exc
    document = parse(text)
    project = Project(root)
    flags = Flags()
    weights = project.weights()
    for item in document.shape_errors:
        flags.error("row-shape", item["msg"], section=item["section"], line=item["line"])
    proposals: list[dict] = []
    etas: dict[int, dict] = {}
    by_wave: dict[str, list[Row]] = {}
    seen: set = set()
    for section in document.sections:
        if not section.columns:
            continue
        layout = check_section_header(project, section, flags)
        map_data = read_json(project.root / "evaluations" / section.name / "RUN_ID_MAP.json")
        history = read_json(project.root / "config" / section.name / "RUN_ID_HISTORY.json")
        for row in section.rows:
            if not check_row_identity(project, row, layout, map_data if isinstance(map_data, dict) else None,
                                      history if isinstance(history, list) else [], seen, flags):
                continue
            by_wave.setdefault(row.wave, []).append(row)
            if only_wave and row.wave != only_wave:
                continue
            changes, eta = reconcile_row(project, row, at, weights, flags)
            if eta is not None:
                etas[row.line] = eta
            if changes:
                held = project.live(row.wave) and not (agent and only_wave == row.wave)
                proposals.append({"row": row, "changes": changes, "held": held})
    waves: dict[str, dict] = {}
    prunable = []
    for wave, rows in sorted(by_wave.items()):
        if only_wave and wave != only_wave:
            continue
        waves[wave] = {"live": project.live(wave), **wave_eta(project, wave, rows, etas, at)}
        worktree = (root / ".waves" / wave).is_dir()
        if not project.live(wave) and worktree and all(row.status in TERMINAL for row in rows):
            prunable.append(wave)
        recorded = worktree or sup.Wave(root, wave).dir.is_dir() or f"wave--{wave}" in project.tags()
        if not recorded and any(row.status != "todo" for row in rows):
            flags.judge("not-recoverable", f"wave {wave} has no worktree, supervisor record or tag: launched before wave isolation; it cannot be recreated", wave=wave)
    report = {
        "root": str(root),
        "proposed": [_public(item) for item in proposals if not item["held"]],
        "held_for_supervisor": [_public(item) for item in proposals if item["held"]],
        "integrity_errors": flags.integrity,
        "judgment": flags.judgment,
        "etas": [
            {"section": row.section.name, "run_id": row.run_id, "wave": row.wave, **etas[row.line]}
            for rows in by_wave.values() for row in rows if row.line in etas
        ],
        "waves": waves,
        "prunable_worktrees": prunable,
    }
    return report, document, text, [item for item in proposals if not item["held"]]


def _public(item: dict) -> dict:
    row = item["row"]
    return {"section": row.section.name, "run_id": row.run_id, "wave": row.wave, "line": row.line + 1,
            "changes": {column: {"from": row.cells.get(column, ""), "to": value} for column, value in item["changes"].items()}}


def print_report(report: dict, confirmed: bool) -> None:
    print(f"{EXPERIMENTS} in {report['root']}: {'applied' if confirmed else 'dry run, nothing written'}")
    title = "Applied" if confirmed else "Proposed"
    groups = ((title, report["proposed"]), ("Held: a live wave's supervisor agent writes these", report["held_for_supervisor"]))
    for heading, items in groups:
        if items:
            print(f"\n{heading} ({len(items)}):")
        for item in items:
            moves = ", ".join(f"{column} {c['from'] or '∅'} → {c['to'] or '∅'}" for column, c in item["changes"].items())
            print(f"  {item['section']} {item['run_id']} wave {item['wave']}: {moves}")
    for heading, items in (("Integrity errors, left as they are", report["integrity_errors"]), ("For judgment, left as they are", report["judgment"])):
        if items:
            print(f"\n{heading} ({len(items)}):")
        for item in items:
            where = f"{item.get('run_id')} wave {item.get('wave')}: " if item.get("run_id") else ""
            print(f"  [{item['kind']}] {where}{item['msg']}")
    if report["etas"]:
        print("\nETAs:")
    for eta in report["etas"]:
        told = f"{stamp(eta['at'])} ({eta['basis']})" if "at" in eta else f"unavailable: {eta['unavailable']}"
        print(f"  {eta['run_id']} wave {eta['wave']}: {told}")
    if report["waves"]:
        print("\nWaves:")
    for wave, info in report["waves"].items():
        told = {"eta": lambda: f"ETA {stamp(info['at'])} ({info['basis']})", "finished": lambda: "finished",
                "unavailable": lambda: f"ETA unavailable: {info['basis']}"}[info["kind"]]()
        print(f"  {wave}{' (live, supervised)' if info['live'] else ''}: {told}")
    if report["prunable_worktrees"]:
        print(f"\nFinished waves whose worktree remains here: {', '.join(report['prunable_worktrees'])}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--root", type=Path, help="the project's main checkout (default: the nearest folder holding EXPERIMENTS.md)")
    parser.add_argument("--wave", help="only this wave's rows")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="print what would change (the default)")
    mode.add_argument("--confirm", action="store_true", help="apply the unambiguous changes")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    root = (args.root or find_root(Path.cwd())).resolve()
    main_checkout = worktree_main(root)
    if main_checkout is not None:
        if args.confirm:
            print(f"reconcile: {root} is a linked worktree; run --confirm from the main checkout {main_checkout}", file=sys.stderr)
            return 2
        print(f"reconcile: {root} is a linked worktree; reading the main checkout {main_checkout}", file=sys.stderr)
        root = main_checkout
    if args.wave and not sup.rigsync.WAVE_RE.fullmatch(args.wave):
        print(f"reconcile: --wave must be YYYYMMDD-HHMMSS; got {args.wave!r}", file=sys.stderr)
        return 2
    try:
        report, document, text, to_apply = reconcile(root, args.wave, sup.now(), bool(os.environ.get("SWEEP_SUPERVISOR_MODEL")))
        if args.confirm and to_apply:
            apply_changes(root / EXPERIMENTS, text, document, to_apply)
    except ReconcileError as exc:
        print(f"reconcile: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps({**report, "applied": bool(args.confirm)}, indent=1))
    else:
        print_report(report, bool(args.confirm))
    return 1 if report["integrity_errors"] or report["judgment"] else 0


if __name__ == "__main__":
    sys.exit(main())
