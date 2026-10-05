#!/usr/bin/env python3
"""ReSirch Killalot: one cross-project list of work, owned by the user, fed by agents.

Every tagged project (a directory holding `.project.toml`) under the configured roots gets its
items: tasks, decisions, approvals, questions and reminders. Agents may only *propose*; the owner
alone accepts, rejects, answers and approves. Everything is one SQLite store outside every
project tree, written only through this script, with an append-only event log.

Layout under `[store] root` from the config (`~/.config/resirch-killalot/config.toml`):

    killalot.db          the store (WAL); never edited by hand
    backups/             daily copies made by `serve`
    app/<version>/       the deployed release the service runs from; app/current points at it

Surfaces: this CLI (agents and the owner), the `hook session-start` handler both hosts run at
session start, and `serve` — a web app reached only through `tailscale serve`, gated by the
Tailscale identity *and* a per-device paired cookie.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import tomllib
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

SCHEMA_VERSION = 1
DEFAULT_CONFIG = "~/.config/resirch-killalot/config.toml"
DEFAULT_PORT = 49147  # the hub's fixed Killalot port; see references/setup.md § Port
DEFAULT_MAX_DEPTH = 5
DEFAULT_STALE_DAYS = 14
DEFAULT_APPROVAL_TTL_H = 24
PAIRING_TTL_S = 10 * 60
SCAN_EVERY_S = 10 * 60
WORKER_TICK_S = 60
BACKUPS_KEPT = 14
APP_VERSIONS_KEPT = 3
HOOK_CONTEXT_MAX = 600
EVIDENCE_TEXT_MAX = 1_000_000
SERVICE_NAME = "resirch-killalot.service"
COOKIE_NAME = "killalot_device"
CSRF_HEADER = "X-Killalot"
TAILSCALE_LOGIN_HEADER = "Tailscale-User-Login"

SKILL_DIR = Path(__file__).resolve().parent.parent
ASSETS = SKILL_DIR / "assets"

KINDS = ("task", "decision", "approval", "question", "reminder")
OWNERS = ("me", "agent")
OPEN_STATES = ("proposed", "accepted", "in_progress", "waiting", "snoozed")
CLOSED_STATES = ("done", "rejected", "dropped")
STATES = OPEN_STATES + CLOSED_STATES

# Directories never worth descending into while looking for tagged projects: artifact trees and
# environments outnumber source by orders of magnitude.
SCAN_SKIP = {
    ".git", ".venv", "venv", ".envs", ".waves", "node_modules", "__pycache__", "checkpoints",
    "evaluations", "logs", "plots", "wandb", "datasets", "shitpads", "references", ".cache",
}

VIA_RE = re.compile(r"^(session:\S+|terminal)$")


class KillalotError(Exception):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(value: datetime) -> str:
    """UTC, second precision: lexicographic order is time order, so SQL can compare strings."""
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed.astimezone(timezone.utc)


RELATIVE_RE = re.compile(r"^(\d+)\s*([mhdw])$")


def parse_when(value: str, base: datetime | None = None) -> datetime:
    """`30m`, `2h`, `3d`, `1w`, `YYYY-MM-DD` (09:00 local), `YYYY-MM-DD HH:MM`, or full ISO."""
    base = base or utcnow()
    text = value.strip()
    match = RELATIVE_RE.match(text)
    if match:
        amount, unit = int(match.group(1)), match.group(2)
        delta = {"m": timedelta(minutes=amount), "h": timedelta(hours=amount), "d": timedelta(days=amount), "w": timedelta(weeks=amount)}[unit]
        return base + delta
    if re.match(r"^\d{4}-\d{2}-\d{2}$", text):
        text += " 09:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise KillalotError(f"cannot read time {value!r}: use 30m/2h/3d/1w, YYYY-MM-DD, or YYYY-MM-DD HH:MM") from exc
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed.astimezone(timezone.utc)


def local(value: str | None) -> str:
    parsed = parse_iso(value)
    return parsed.astimezone().strftime("%Y-%m-%d %H:%M") if parsed else "—"


# ───────────────────────────── config ─────────────────────────────


@dataclass(frozen=True)
class Config:
    root: Path
    project_roots: tuple[Path, ...]
    max_depth: int = DEFAULT_MAX_DEPTH
    stale_days: int = DEFAULT_STALE_DAYS
    bind: str = "127.0.0.1"
    port: int = DEFAULT_PORT
    owner_login: str | None = None
    public_base: str | None = None
    approval_ttl_h: int = DEFAULT_APPROVAL_TTL_H
    config_path: Path = field(default_factory=lambda: Path(DEFAULT_CONFIG).expanduser())

    @property
    def db_path(self) -> Path:
        return self.root / "killalot.db"

    @property
    def backups_dir(self) -> Path:
        return self.root / "backups"

    @property
    def app_dir(self) -> Path:
        return self.root / "app"


def config_path_from_env(explicit: str | Path | None = None) -> Path:
    raw = explicit or os.environ.get("KILLALOT_CONFIG") or DEFAULT_CONFIG
    return Path(raw).expanduser().resolve()


def load_config(path: Path) -> Config:
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except FileNotFoundError as exc:
        raise KillalotError(f"no config at {path}; see references/setup.md") from exc
    except tomllib.TOMLDecodeError as exc:
        raise KillalotError(f"{path}: invalid TOML: {exc}") from exc
    store = data.get("store") or {}
    projects = data.get("projects") or {}
    web = data.get("web") or {}
    approvals = data.get("approvals") or {}
    root = store.get("root")
    if not isinstance(root, str) or not root:
        raise KillalotError(f"{path}: [store] root is required")
    root_path = Path(root).expanduser()
    if not root_path.is_absolute():
        raise KillalotError(f"{path}: [store] root must be absolute")
    roots = projects.get("roots")
    if not isinstance(roots, list) or not roots or not all(isinstance(r, str) for r in roots):
        raise KillalotError(f"{path}: [projects] roots must be a non-empty list of paths")
    port = web.get("port", DEFAULT_PORT)
    if not isinstance(port, int) or not 0 < port < 65536:
        raise KillalotError(f"{path}: [web] port must be an integer port")
    owner = web.get("owner_login")
    if owner is not None and (not isinstance(owner, str) or "@" not in owner):
        raise KillalotError(f"{path}: [web] owner_login must be the Tailscale login, like name@github")
    return Config(
        root=root_path,
        project_roots=tuple(Path(r).expanduser() for r in roots),
        max_depth=int(projects.get("max_depth", DEFAULT_MAX_DEPTH)),
        stale_days=int(projects.get("stale_days", DEFAULT_STALE_DAYS)),
        bind=str(web.get("bind", "127.0.0.1")),
        port=port,
        owner_login=owner,
        public_base=(web.get("public_base") or None),
        approval_ttl_h=int(approvals.get("ttl_hours", DEFAULT_APPROVAL_TTL_H)),
        config_path=path,
    )


# ───────────────────────────── store ─────────────────────────────

MIGRATIONS: list[str] = [
    # 1 — initial schema
    """
    CREATE TABLE projects (
        path TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        categories TEXT NOT NULL DEFAULT '[]',
        first_seen TEXT NOT NULL,
        last_seen TEXT NOT NULL,
        missing INTEGER NOT NULL DEFAULT 0,
        last_activity TEXT,
        parked_at TEXT,
        park_reason TEXT,
        revisit_item INTEGER,
        unpark_suggested INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project TEXT NOT NULL REFERENCES projects(path),
        kind TEXT NOT NULL,
        title TEXT NOT NULL,
        body TEXT NOT NULL DEFAULT '',
        owner TEXT NOT NULL,
        state TEXT NOT NULL,
        origin TEXT NOT NULL,
        evidence TEXT NOT NULL DEFAULT '[]',
        evidence_text TEXT,
        fingerprint TEXT,
        command TEXT,
        due_at TEXT,
        snooze_until TEXT,
        snoozed_from TEXT,
        source_key TEXT UNIQUE,
        dedupe_key TEXT NOT NULL,
        reject_reason TEXT,
        wait_reason TEXT,
        answer TEXT,
        approved_at TEXT,
        approved_until TEXT,
        approved_by TEXT,
        notified_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX items_project_state ON items(project, state);
    CREATE INDEX items_dedupe ON items(project, dedupe_key);
    CREATE TABLE events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        at TEXT NOT NULL,
        item INTEGER,
        project TEXT,
        actor TEXT NOT NULL,
        via TEXT,
        action TEXT NOT NULL,
        from_state TEXT,
        to_state TEXT,
        payload TEXT NOT NULL DEFAULT '{}'
    );
    CREATE INDEX events_item ON events(item);
    CREATE INDEX events_at ON events(at);
    CREATE TRIGGER events_no_update BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
    CREATE TRIGGER events_no_delete BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
    CREATE TABLE devices (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        token_sha256 TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL,
        last_used_at TEXT,
        revoked_at TEXT
    );
    CREATE TABLE pairings (
        code_sha256 TEXT PRIMARY KEY,
        device_name TEXT NOT NULL,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        used_at TEXT
    );
    CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    """,
]


def connect(config: Config, readonly: bool = False, timeout: float = 10.0) -> sqlite3.Connection:
    if readonly:
        if not config.db_path.exists():
            raise KillalotError(f"no store at {config.db_path}")
        conn = sqlite3.connect(f"file:{config.db_path}?mode=ro", uri=True, timeout=timeout, isolation_level=None)
    else:
        config.root.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(config.db_path, timeout=timeout, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout={int(timeout * 1000)}")
    conn.execute("PRAGMA foreign_keys=ON")
    if not readonly:
        migrate(conn)
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    conn.execute("BEGIN IMMEDIATE")
    try:
        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'").fetchone()
        current = 0
        if exists:
            row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            current = int(row["value"]) if row else 0
        if current > len(MIGRATIONS):
            raise KillalotError(f"store schema {current} is newer than this script ({len(MIGRATIONS)}); deploy the current release")
        for number in range(current + 1, len(MIGRATIONS) + 1):
            for statement in split_sql(MIGRATIONS[number - 1]):
                conn.execute(statement)
            conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', ?)", (str(number),))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def split_sql(script: str) -> list[str]:
    """Split a migration on statement ends, keeping trigger bodies (BEGIN … END;) whole."""
    statements, buffer = [], []
    for line in script.strip().splitlines():
        buffer.append(line)
        chunk = "\n".join(buffer).strip()
        if chunk.endswith(";") and sqlite3.complete_statement(chunk):
            statements.append(chunk)
            buffer = []
    if "\n".join(buffer).strip():
        statements.append("\n".join(buffer).strip())
    return statements


class Tx:
    """BEGIN IMMEDIATE … COMMIT: one write, one transaction, its event row included."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def __enter__(self) -> sqlite3.Connection:
        self.conn.execute("BEGIN IMMEDIATE")
        return self.conn

    def __exit__(self, exc_type, exc, tb) -> None:
        self.conn.execute("ROLLBACK" if exc_type else "COMMIT")


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, value))


def record(conn, *, actor: str, via: str | None, action: str, item: int | None = None, project: str | None = None,
           from_state: str | None = None, to_state: str | None = None, **payload) -> None:
    conn.execute(
        "INSERT INTO events(at, item, project, actor, via, action, from_state, to_state, payload) VALUES (?,?,?,?,?,?,?,?,?)",
        (iso(utcnow()), item, project, actor, via, action, from_state, to_state, json.dumps(payload, sort_keys=True)),
    )


def item_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    data = dict(row)
    data["evidence"] = json.loads(data.get("evidence") or "[]")
    return data


def get_item(conn, item_id: int) -> dict:
    data = item_dict(conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone())
    if data is None:
        raise KillalotError(f"no item #{item_id}")
    return data


# ───────────────────────────── projects ─────────────────────────────


def find_project_root(start: Path) -> Path | None:
    current = start.expanduser().resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".project.toml").is_file():
            return candidate
    return None


def read_categories(project: Path) -> list[str]:
    try:
        with (project / ".project.toml").open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return []
    cats = data.get("categories")
    return [c for c in cats if isinstance(c, str)] if isinstance(cats, list) else []


def discover(config: Config) -> list[Path]:
    found: list[Path] = []
    for root in config.project_roots:
        if not root.is_dir():
            continue
        base_depth = len(root.resolve().parts)
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            here = Path(dirpath)
            if ".project.toml" in filenames:
                found.append(here.resolve())
                dirnames[:] = []  # projects are never nested
                continue
            if len(here.resolve().parts) - base_depth >= config.max_depth:
                dirnames[:] = []
                continue
            dirnames[:] = sorted(d for d in dirnames if d not in SCAN_SKIP and not d.startswith(".rigsync"))
    return sorted(set(found))


def display_names(paths: list[Path]) -> dict[Path, str]:
    names: dict[Path, str] = {}
    by_leaf: dict[str, list[Path]] = {}
    for path in paths:
        by_leaf.setdefault(path.name, []).append(path)
    for leaf, group in by_leaf.items():
        for path in group:
            names[path] = leaf if len(group) == 1 else f"{path.parent.name}/{leaf}"
    return names


def last_activity(project: Path) -> datetime | None:
    stamps: list[float] = []
    try:
        out = subprocess.run(["git", "-C", str(project), "log", "-1", "--format=%ct"], capture_output=True, text=True, timeout=5)
        if out.returncode == 0 and out.stdout.strip().isdigit():
            stamps.append(float(out.stdout.strip()))
    except (OSError, subprocess.TimeoutExpired):
        pass
    for path in (project / "JOURNAL.md", project / "EXPERIMENTS.md"):
        try:
            stamps.append(path.stat().st_mtime)
        except OSError:
            pass
    state_dir = project / ".waves" / "_state"
    try:
        for wave in state_dir.iterdir():
            stamps.append(wave.stat().st_mtime)
    except OSError:
        pass
    if not stamps:
        return None
    return datetime.fromtimestamp(max(stamps), tz=timezone.utc).replace(microsecond=0)


def register_project(conn, path: Path, name: str | None = None) -> None:
    stamp = iso(utcnow())
    cats = json.dumps(read_categories(path))
    row = conn.execute("SELECT name FROM projects WHERE path=?", (str(path),)).fetchone()
    if row:
        conn.execute("UPDATE projects SET last_seen=?, missing=0, categories=? WHERE path=?", (stamp, cats, str(path)))
        if name and name != row["name"]:
            conn.execute("UPDATE projects SET name=? WHERE path=?", (name, str(path)))
        return
    conn.execute(
        "INSERT INTO projects(path, name, categories, first_seen, last_seen) VALUES (?,?,?,?,?)",
        (str(path), name or path.name, cats, stamp, stamp),
    )
    record(conn, actor="system", via="scan", action="project-found", project=str(path))


def scan(conn, config: Config) -> dict:
    paths = discover(config)
    known = {Path(r["path"]) for r in conn.execute("SELECT path FROM projects")}
    names = display_names(sorted(set(paths) | {p for p in known if p not in paths}))
    activity = {path: last_activity(path) for path in paths}
    with Tx(conn):
        for path in paths:
            register_project(conn, path, names[path])
            seen = activity[path]
            row = conn.execute("SELECT parked_at, last_activity, unpark_suggested FROM projects WHERE path=?", (str(path),)).fetchone()
            updates = {"last_activity": iso(seen) if seen else row["last_activity"]}
            parked = parse_iso(row["parked_at"])
            if parked and seen and seen > parked and not row["unpark_suggested"]:
                updates["unpark_suggested"] = 1
                record(conn, actor="system", via="scan", action="unpark-suggested", project=str(path), activity=iso(seen))
            assignments = ", ".join(f"{k}=?" for k in updates)
            conn.execute(f"UPDATE projects SET {assignments} WHERE path=?", (*updates.values(), str(path)))
        roots = [r.resolve() for r in config.project_roots]
        for path in known - set(paths):
            if any(path.is_relative_to(r) for r in roots):
                row = conn.execute("SELECT missing FROM projects WHERE path=?", (str(path),)).fetchone()
                if row and not row["missing"]:
                    conn.execute("UPDATE projects SET missing=1 WHERE path=?", (str(path),))
                    record(conn, actor="system", via="scan", action="project-missing", project=str(path))
        set_meta(conn, "last_scan_at", iso(utcnow()))
    return {"projects": len(paths), "missing": len(known - set(paths))}


def resolve_project(conn, value: str, cwd: Path | None = None) -> str:
    """`.` or a path → the tagged project containing it; otherwise a project name."""
    if value == "." or "/" in value or value.startswith("~"):
        start = (cwd or Path.cwd()) if value == "." else Path(value).expanduser()
        root = find_project_root(start)
        if root is None:
            raise KillalotError(f"{start} is not inside a tagged project (no .project.toml); tag it with project-init first")
        if not conn.execute("SELECT 1 FROM projects WHERE path=?", (str(root),)).fetchone():
            with Tx(conn):
                register_project(conn, root)
        return str(root)
    rows = conn.execute("SELECT path FROM projects WHERE name=? AND missing=0", (value,)).fetchall()
    if len(rows) == 1:
        return rows[0]["path"]
    if not rows:
        raise KillalotError(f"no project named {value!r}; run `killalot projects` (or `scan`) to see them")
    raise KillalotError(f"{value!r} names several projects; pass the path instead")


def project_rows(conn, config: Config) -> list[dict]:
    wake_snoozed(conn)
    horizon = utcnow() - timedelta(days=config.stale_days)
    rows = []
    for project in conn.execute("SELECT * FROM projects WHERE missing=0 ORDER BY name"):
        counts = {s: 0 for s in OPEN_STATES}
        for r in conn.execute("SELECT state, COUNT(*) AS n FROM items WHERE project=? AND state IN (%s) GROUP BY state" % ",".join("?" * len(OPEN_STATES)), (project["path"], *OPEN_STATES)):
            counts[r["state"]] = r["n"]
        open_total = sum(counts.values())
        last_item = conn.execute("SELECT MAX(at) AS at FROM events WHERE project=? AND item IS NOT NULL", (project["path"],)).fetchone()["at"]
        activity = max(filter(None, [parse_iso(project["last_activity"]), parse_iso(last_item)]), default=None)
        nxt = conn.execute(
            "SELECT id, title FROM items WHERE project=? AND state IN ('in_progress','accepted') ORDER BY state='in_progress' DESC, created_at LIMIT 1",
            (project["path"],),
        ).fetchone()
        parked = bool(project["parked_at"])
        rows.append({
            "path": project["path"],
            "name": project["name"],
            "categories": json.loads(project["categories"]),
            "last_activity": iso(activity) if activity else None,
            "open": open_total,
            "counts": counts,
            "next": {"id": nxt["id"], "title": nxt["title"]} if nxt else None,
            "parked": parked,
            "parked_at": project["parked_at"],
            "park_reason": project["park_reason"],
            "unpark_suggested": bool(project["unpark_suggested"]) and parked,
            "stale": (not parked) and open_total > 0 and (activity is None or activity < horizon),
        })
    return rows


def park(conn, project: str, *, reason: str | None, revisit: datetime | None, via: str) -> dict:
    with Tx(conn):
        row = conn.execute("SELECT * FROM projects WHERE path=?", (project,)).fetchone()
        if row["parked_at"]:
            raise KillalotError(f"{row['name']} is already parked")
        revisit_id = None
        if revisit:
            revisit_id = insert_item(conn, project=project, kind="reminder", title=f"Revisit parked project {row['name']}",
                                     owner="me", state="accepted", origin="me", via=via, actor="me", due_at=iso(revisit))
        conn.execute("UPDATE projects SET parked_at=?, park_reason=?, revisit_item=?, unpark_suggested=0 WHERE path=?",
                     (iso(utcnow()), reason, revisit_id, project))
        record(conn, actor="me", via=via, action="park", project=project, reason=reason, revisit=iso(revisit) if revisit else None)
    return {"project": project, "revisit_item": revisit_id}


def unpark(conn, project: str, *, via: str) -> None:
    with Tx(conn):
        row = conn.execute("SELECT * FROM projects WHERE path=?", (project,)).fetchone()
        if not row["parked_at"]:
            raise KillalotError(f"{row['name']} is not parked")
        conn.execute("UPDATE projects SET parked_at=NULL, park_reason=NULL, revisit_item=NULL, unpark_suggested=0 WHERE path=?", (project,))
        record(conn, actor="me", via=via, action="unpark", project=project)
        if row["revisit_item"]:
            item = conn.execute("SELECT state FROM items WHERE id=?", (row["revisit_item"],)).fetchone()
            if item and item["state"] in OPEN_STATES:
                _set_state(conn, row["revisit_item"], item["state"], "dropped", actor="me", via=via, action="drop", reject_reason="project unparked")


# ───────────────────────────── items and rules ─────────────────────────────


def dedupe_key(kind: str, title: str) -> str:
    norm = re.sub(r"[^\w\s]", "", title.lower())
    return f"{kind}:{' '.join(norm.split())}"


def fingerprint(text: str) -> str:
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    return "sha256:" + hashlib.sha256("\n".join(lines).encode()).hexdigest()


def insert_item(conn, *, project: str, kind: str, title: str, owner: str, state: str, origin: str, via: str | None,
                actor: str, body: str = "", evidence: list[str] | None = None, due_at: str | None = None,
                source_key: str | None = None, evidence_text: str | None = None, command: str | None = None) -> int:
    if kind not in KINDS:
        raise KillalotError(f"kind must be one of {', '.join(KINDS)}")
    if owner not in OWNERS:
        raise KillalotError("owner must be me or agent")
    title = " ".join(title.split())
    if not title:
        raise KillalotError("a title is required")
    stamp = iso(utcnow())
    fp = fingerprint(evidence_text) if evidence_text is not None else None
    cur = conn.execute(
        """INSERT INTO items(project, kind, title, body, owner, state, origin, evidence, evidence_text, fingerprint, command,
               due_at, source_key, dedupe_key, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (project, kind, title, body or "", owner, state, origin, json.dumps(evidence or []), evidence_text, fp, command,
         due_at, source_key, dedupe_key(kind, title), stamp, stamp),
    )
    item_id = cur.lastrowid
    record(conn, actor=actor, via=via, action="create", item=item_id, project=project, to_state=state, kind=kind, title=title, fingerprint=fp)
    return item_id


def check_duplicate(conn, project: str, kind: str, title: str) -> None:
    row = conn.execute(
        "SELECT id, state, reject_reason FROM items WHERE project=? AND dedupe_key=? AND state IN (%s) ORDER BY id DESC LIMIT 1"
        % ",".join("?" * (len(OPEN_STATES) + 1)),
        (project, dedupe_key(kind, title), *OPEN_STATES, "rejected"),
    ).fetchone()
    if row is None:
        return
    if row["state"] == "rejected":
        raise KillalotError(f"the owner already rejected this as #{row['id']}: {row['reject_reason'] or 'no reason given'} — do not propose it again")
    raise KillalotError(f"already tracked as #{row['id']} ({row['state']})")


def propose(conn, *, project: str, kind: str, title: str, body: str = "", evidence: list[str] | None = None,
            owner: str = "agent", due_at: str | None = None, session: str | None = None) -> int:
    if not evidence:
        raise KillalotError("a proposal must carry evidence: say why it exists and where it came from (--evidence)")
    if kind == "approval":
        raise KillalotError("approvals are filed with request-approval, which binds them to their dry-run")
    via = f"session:{session}" if session else None
    with Tx(conn):
        check_duplicate(conn, project, kind, title)
        return insert_item(conn, project=project, kind=kind, title=title, owner=owner, state="proposed",
                           origin=f"agent:{session}" if session else "agent", via=via, actor="agent",
                           body=body, evidence=evidence, due_at=due_at)


def add(conn, *, project: str, kind: str, title: str, via: str, body: str = "", owner: str = "me",
        evidence: list[str] | None = None, due_at: str | None = None) -> int:
    if kind == "approval":
        raise KillalotError("approvals are filed with request-approval")
    with Tx(conn):
        check_duplicate(conn, project, kind, title)
        return insert_item(conn, project=project, kind=kind, title=title, owner=owner, state="accepted", origin="me",
                           via=via, actor="me", body=body, evidence=evidence, due_at=due_at)


def request_approval(conn, *, project: str, title: str, evidence_text: str, command: str | None, body: str = "",
                     evidence: list[str] | None = None, session: str | None = None) -> tuple[int, bool]:
    if len(evidence_text.encode()) > EVIDENCE_TEXT_MAX:
        raise KillalotError("the evidence is over 1 MB; an approval must be shown in full, so narrow the dry-run")
    if not evidence_text.strip():
        raise KillalotError("the evidence file is empty; an approval binds to the dry-run it shows")
    fp = fingerprint(evidence_text)
    with Tx(conn):
        row = conn.execute("SELECT id FROM items WHERE project=? AND fingerprint=? AND state IN ('proposed','accepted','in_progress')",
                           (project, fp)).fetchone()
        if row:
            return row["id"], False
        item_id = insert_item(conn, project=project, kind="approval", title=title, owner="agent", state="proposed",
                              origin=f"agent:{session}" if session else "agent", via=f"session:{session}" if session else None,
                              actor="agent", body=body, evidence=evidence, evidence_text=evidence_text, command=command)
        return item_id, True


def check_approval(conn, item_id: int, evidence_text: str) -> tuple[bool, str]:
    item = get_item(conn, item_id)
    if item["kind"] != "approval":
        return False, f"#{item_id} is a {item['kind']}, not an approval"
    if item["state"] not in ("accepted", "in_progress"):
        return False, f"#{item_id} is {item['state']}, not approved"
    until = parse_iso(item["approved_until"])
    if until is None or until <= utcnow():
        return False, f"the approval of #{item_id} expired at {local(item['approved_until'])}; ask the owner again"
    if fingerprint(evidence_text) != item["fingerprint"]:
        return False, f"the dry-run changed since the owner approved #{item_id}; file a new approval with the current one"
    return True, f"#{item_id} approved by {item['approved_by']} until {local(item['approved_until'])}"


def _set_state(conn, item_id: int, from_state: str, to_state: str, *, actor: str, via: str | None, action: str, **fields) -> None:
    assignments = {"state": to_state, "updated_at": iso(utcnow()), **fields}
    sql = ", ".join(f"{k}=?" for k in assignments)
    conn.execute(f"UPDATE items SET {sql} WHERE id=?", (*assignments.values(), item_id))
    project = conn.execute("SELECT project FROM items WHERE id=?", (item_id,)).fetchone()["project"]
    payload = {k: v for k, v in fields.items() if k not in ("updated_at",)}
    record(conn, actor=actor, via=via, action=action, item=item_id, project=project, from_state=from_state, to_state=to_state, **payload)


OWNER_ACTIONS = {
    "accept": ({"proposed"}, "accepted"),
    "reject": ({"proposed"}, "rejected"),
    "drop": ({"accepted", "in_progress", "waiting", "snoozed"}, "dropped"),
    "snooze": ({"proposed", "accepted", "waiting"}, "snoozed"),
}
WORK_ACTIONS = {
    "start": ({"accepted"}, "in_progress"),
    "wait": ({"accepted", "in_progress"}, "waiting"),
    "done": ({"accepted", "in_progress"}, "done"),
}


def check_via(actor: str, via: str | None) -> None:
    if actor != "me":
        return
    if not via or not (VIA_RE.match(via) or via.startswith("device:")):
        raise KillalotError("an owner action needs --via session:<host>/<session id> (on the owner's words) or --via terminal")


def act(conn, item_id: int, action: str, *, actor: str, via: str | None, reason: str | None = None,
        until: datetime | None = None, evidence: list[str] | None = None, answer: str | None = None,
        edits: dict | None = None, presented_fingerprint: str | None = None, ttl_h: int = DEFAULT_APPROVAL_TTL_H) -> dict:
    """The one gate every surface goes through. `actor` is `me` (owner, with a `via`) or `agent`."""
    check_via(actor, via)
    with Tx(conn):
        item = get_item(conn, item_id)
        state = item["state"]
        if action in OWNER_ACTIONS:
            allowed, target = OWNER_ACTIONS[action]
            if actor != "me":
                raise KillalotError(f"only the owner can {action}; propose and let the owner decide")
            if state not in allowed:
                raise KillalotError(f"#{item_id} is {state}; {action} needs {' or '.join(sorted(allowed))}")
            if action == "accept" and item["kind"] == "approval":
                raise KillalotError(f"#{item_id} is an approval: the owner approves it from a paired device, after seeing the full dry-run")
            fields: dict = {}
            if action == "reject":
                if not reason:
                    raise KillalotError("a rejection keeps its reason so the proposal never comes back: pass --reason")
                fields["reject_reason"] = reason
            if action == "snooze":
                if until is None or until <= utcnow():
                    raise KillalotError("snooze needs a future time (--until 1h/1d/YYYY-MM-DD)")
                fields.update(snooze_until=iso(until), snoozed_from=state)
            if action == "drop" and reason:
                fields["reject_reason"] = reason
            _set_state(conn, item_id, state, target, actor=actor, via=via, action=action, **fields)
        elif action == "approve":
            if actor != "me" or not (via or "").startswith("device:"):
                raise KillalotError("approvals are given only from a paired device (the web app), never by an agent or the CLI")
            if item["kind"] != "approval" or state != "proposed":
                raise KillalotError(f"#{item_id} is not an approval waiting for the owner")
            if not presented_fingerprint or not hmac.compare_digest(presented_fingerprint, item["fingerprint"] or ""):
                raise KillalotError("the approval must name the fingerprint of the dry-run the owner saw")
            stamp = utcnow()
            _set_state(conn, item_id, state, "accepted", actor=actor, via=via, action="approve", approved_at=iso(stamp),
                       approved_until=iso(stamp + timedelta(hours=ttl_h)), approved_by=via, fingerprint=item["fingerprint"])
        elif action in WORK_ACTIONS:
            allowed, target = WORK_ACTIONS[action]
            if actor == "agent" and item["owner"] != "agent":
                raise KillalotError(f"#{item_id} is the owner's own item; an agent only works items owned by agent")
            if state not in allowed:
                raise KillalotError(f"#{item_id} is {state}; {action} needs {' or '.join(sorted(allowed))}")
            if action == "start" and item["kind"] == "approval":
                ok, why = check_approval_row(item)
                if not ok:
                    raise KillalotError(why)
            fields = {}
            if action == "wait":
                if not reason:
                    raise KillalotError("say what the item is waiting for (--reason); the owner sees it as a question")
                fields["wait_reason"] = reason
            if evidence:
                fields["evidence"] = json.dumps(item["evidence"] + evidence)
            _set_state(conn, item_id, state, target, actor=actor, via=via, action=action, **fields)
        elif action == "answer":
            if actor != "me":
                raise KillalotError("only the owner answers")
            if not answer:
                raise KillalotError("an answer needs text")
            if state == "waiting":
                _set_state(conn, item_id, state, "accepted", actor=actor, via=via, action="answer", answer=answer, wait_reason=None)
            elif item["kind"] in ("decision", "question") and state in ("proposed", "accepted", "in_progress"):
                _set_state(conn, item_id, state, "done", actor=actor, via=via, action="answer", answer=answer)
            else:
                raise KillalotError(f"#{item_id} ({item['kind']}, {state}) has nothing to answer")
        elif action == "edit":
            if actor != "me":
                raise KillalotError("only the owner edits; an agent proposes a new item instead")
            if state not in OPEN_STATES:
                raise KillalotError(f"#{item_id} is closed ({state})")
            changes = {k: v for k, v in (edits or {}).items() if v is not None}
            if not changes:
                raise KillalotError("nothing to edit")
            if "kind" in changes and (changes["kind"] not in KINDS or changes["kind"] == "approval" or item["kind"] == "approval"):
                raise KillalotError("an approval's kind is fixed, and an item cannot become one")
            if "owner" in changes and changes["owner"] not in OWNERS:
                raise KillalotError("owner must be me or agent")
            if "title" in changes:
                changes["title"] = " ".join(changes["title"].split())
            if item["kind"] == "approval" and set(changes) - {"due_at"}:
                raise KillalotError("an approval is bound to its dry-run; reject it and file a new one instead")
            changes["dedupe_key"] = dedupe_key(changes.get("kind", item["kind"]), changes.get("title", item["title"]))
            _set_state(conn, item_id, state, state, actor=actor, via=via, action="edit", **changes)
        else:
            raise KillalotError(f"unknown action {action!r}")
        return get_item(conn, item_id)


def check_approval_row(item: dict) -> tuple[bool, str]:
    until = parse_iso(item.get("approved_until"))
    if until is None or until <= utcnow():
        return False, f"the approval of #{item['id']} has expired or was never given"
    return True, "ok"


def wake_snoozed(conn) -> int:
    rows = conn.execute("SELECT id, snoozed_from FROM items WHERE state='snoozed' AND snooze_until <= ?", (iso(utcnow()),)).fetchall()
    if not rows:
        return 0
    try:
        with Tx(conn):
            for row in rows:
                current = conn.execute("SELECT state FROM items WHERE id=?", (row["id"],)).fetchone()
                if current["state"] == "snoozed":
                    _set_state(conn, row["id"], "snoozed", row["snoozed_from"] or "accepted", actor="system", via="clock",
                               action="unsnooze", snooze_until=None, snoozed_from=None)
    except sqlite3.OperationalError:
        return 0  # read-only connection or busy: the next writer wakes them
    return len(rows)


# ───────────────────────────── views ─────────────────────────────

BUCKETS = (
    ("blocked", "Blocked on you"),
    ("decisions", "Decisions"),
    ("proposals", "Proposals"),
    ("due", "Due"),
)


def bucket_of(item: dict, now_iso: str) -> str | None:
    state, kind = item["state"], item["kind"]
    if state == "waiting" or (kind == "approval" and state == "proposed") or (kind == "question" and state == "accepted" and item["owner"] == "me"):
        return "blocked"
    if kind == "decision" and state == "accepted" and item["owner"] == "me":
        return "decisions"
    if state == "proposed":
        return "proposals"
    if state in ("accepted", "in_progress") and item["due_at"] and item["due_at"] <= now_iso:
        return "due"
    return None


def list_items(conn, project: str | None = None, states: list[str] | None = None) -> list[dict]:
    wake_snoozed(conn)
    sql = "SELECT items.*, projects.name AS project_name FROM items JOIN projects ON projects.path = items.project"
    params: list = []
    clauses = []
    if project:
        clauses.append("items.project=?")
        params.append(project)
    states = states or list(OPEN_STATES)
    clauses.append("items.state IN (%s)" % ",".join("?" * len(states)))
    params.extend(states)
    sql += " WHERE " + " AND ".join(clauses) + " ORDER BY items.created_at, items.id"
    return [item_dict(r) for r in conn.execute(sql, params)]


def inbox(conn) -> dict[str, list[dict]]:
    now_iso = iso(utcnow())
    out: dict[str, list[dict]] = {key: [] for key, _ in BUCKETS}
    for item in list_items(conn):
        bucket = bucket_of(item, now_iso)
        if bucket:
            out[bucket].append(item)
    return out


def digest(conn, config: Config, mark: bool) -> dict:
    since = get_meta(conn, "last_digest_at") or "1970-01-01T00:00:00+00:00"
    now_iso = iso(utcnow())
    events = [dict(r) for r in conn.execute(
        "SELECT events.*, items.title AS title, projects.name AS project_name FROM events "
        "LEFT JOIN items ON items.id = events.item LEFT JOIN projects ON projects.path = events.project "
        "WHERE events.at > ? ORDER BY events.id", (since,))]
    created = [e for e in events if e["action"] == "create" and e["to_state"] == "proposed"]
    finished = [e for e in events if e["to_state"] in ("done",)]
    projects = project_rows(conn, config)
    result = {
        "since": since,
        "generated_at": now_iso,
        "inbox": inbox(conn),
        "new_proposals": created,
        "finished": finished,
        "stale": [p for p in projects if p["stale"]],
        "unpark_suggested": [p for p in projects if p["unpark_suggested"]],
        "events": len(events),
    }
    if mark:
        with Tx(conn):
            set_meta(conn, "last_digest_at", now_iso)
            record(conn, actor="me", via=None, action="digest", since=since)
    return result


# ───────────────────────────── text rendering ─────────────────────────────


def line(item: dict) -> str:
    project = item.get("project_name") or Path(item["project"]).name
    extra = []
    if item["owner"] == "agent":
        extra.append("agent")
    if item.get("due_at"):
        extra.append(f"due {local(item['due_at'])}")
    if item["state"] == "snoozed":
        extra.append(f"until {local(item['snooze_until'])}")
    if item["state"] == "waiting" and item.get("wait_reason"):
        extra.append(f"waiting: {item['wait_reason']}")
    tail = f"  ({'; '.join(extra)})" if extra else ""
    return f"#{item['id']:<4} {item['state']:<11} {item['kind']:<9} {project:<22} {item['title']}{tail}"


def print_items(items: list[dict]) -> None:
    if not items:
        print("  (none)")
    for item in items:
        print("  " + line(item))


# ───────────────────────────── session-start hook ─────────────────────────────


def hook_context(config: Config, cwd: Path) -> str | None:
    root = find_project_root(cwd)
    if root is None:
        return None
    conn = connect(config, readonly=True, timeout=0.5)
    try:
        row = conn.execute("SELECT name FROM projects WHERE path=?", (str(root),)).fetchone()
        if row is None:
            return None
        rows = [item_dict(r) for r in conn.execute(
            "SELECT * FROM items WHERE project=? AND state IN ('proposed','accepted','in_progress','waiting') ORDER BY state='in_progress' DESC, state='waiting' DESC, created_at",
            (str(root),))]
    finally:
        conn.close()
    if not rows:
        return None
    counts: dict[str, int] = {}
    for item in rows:
        counts[item["state"]] = counts.get(item["state"], 0) + 1
    summary = ", ".join(f"{n} {s.replace('_', ' ')}" for s, n in counts.items())
    head = (f"ReSirch Killalot — {row['name']}: {summary}. Use the resirch-killalot skill (`killalot list --project .`). "
            "Start an item only on the owner's yes; proposals wait for the owner.")
    lines = [head]
    for item in [i for i in rows if i["state"] != "proposed"][:5]:
        lines.append(f"#{item['id']} [{item['state']}] {item['title']}")
    text = "\n".join(lines)
    return text if len(text) <= HOOK_CONTEXT_MAX else text[: HOOK_CONTEXT_MAX - 1] + "…"


def hook_session_start(config_path: Path, stdin_text: str) -> str:
    """Always returns valid hook JSON. Any failure is silence (`{}`): core runs in every project."""
    try:
        data = json.loads(stdin_text) if stdin_text.strip() else {}
        cwd = Path(data.get("cwd") or os.getcwd())
        config = load_config(config_path)
        context = hook_context(config, cwd)
    except Exception:  # noqa: BLE001 - a session must never break because of this hook
        return "{}"
    if not context:
        return "{}"
    return json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": context}})


# ───────────────────────────── devices ─────────────────────────────


def sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def create_pairing(conn, name: str) -> str:
    code = secrets.token_urlsafe(18)
    stamp = utcnow()
    with Tx(conn):
        conn.execute("INSERT INTO pairings(code_sha256, device_name, created_at, expires_at) VALUES (?,?,?,?)",
                     (sha(code), name, iso(stamp), iso(stamp + timedelta(seconds=PAIRING_TTL_S))))
        record(conn, actor="me", via="terminal", action="pairing-created", device=name)
    return code


def redeem_pairing(conn, code: str) -> tuple[int, str] | None:
    with Tx(conn):
        row = conn.execute("SELECT * FROM pairings WHERE code_sha256=?", (sha(code),)).fetchone()
        if row is None or row["used_at"] or row["expires_at"] <= iso(utcnow()):
            return None
        token = secrets.token_urlsafe(32)
        cur = conn.execute("INSERT INTO devices(name, token_sha256, created_at) VALUES (?,?,?)", (row["device_name"], sha(token), iso(utcnow())))
        conn.execute("UPDATE pairings SET used_at=? WHERE code_sha256=?", (iso(utcnow()), sha(code)))
        record(conn, actor="me", via=f"device:{cur.lastrowid}", action="device-paired", device=row["device_name"])
        return cur.lastrowid, token


def device_for(conn, token: str | None) -> dict | None:
    if not token:
        return None
    row = conn.execute("SELECT * FROM devices WHERE token_sha256=? AND revoked_at IS NULL", (sha(token),)).fetchone()
    return dict(row) if row else None


def revoke_device(conn, device_id: int, via: str) -> None:
    with Tx(conn):
        row = conn.execute("SELECT * FROM devices WHERE id=?", (device_id,)).fetchone()
        if row is None:
            raise KillalotError(f"no device {device_id}")
        if row["revoked_at"]:
            return
        conn.execute("UPDATE devices SET revoked_at=? WHERE id=?", (iso(utcnow()), device_id))
        record(conn, actor="me", via=via, action="device-revoked", device=row["name"], device_id=device_id)


# ───────────────────────────── web ─────────────────────────────

DENIED_PAGE = """<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>ReSirch Killalot</title><body style="font:16px system-ui;padding:24px;max-width:36em">
<h1>ReSirch Killalot</h1><p>This device is not paired. On the hub, run <code>killalot device add "&lt;name&gt;"</code>
and open the link it prints on this device within ten minutes.</p></body>"""


def asset(name: str) -> bytes | None:
    try:
        return (ASSETS / name).read_bytes()
    except OSError:
        return None


def json_ready(value):
    return json.loads(json.dumps(value, default=str))


def make_handler(config: Config, state: dict | None = None):
    serve_state = state if state is not None else {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: object) -> None:  # quiet
            pass

        # ── auth ──
        def _identity_ok(self) -> bool:
            login = self.headers.get(TAILSCALE_LOGIN_HEADER)
            return bool(config.owner_login) and login is not None and hmac.compare_digest(login, config.owner_login)

        def _device(self, conn) -> dict | None:
            cookie = SimpleCookie(self.headers.get("Cookie", ""))
            token = cookie[COOKIE_NAME].value if COOKIE_NAME in cookie else None
            device = device_for(conn, token)
            if device:
                try:
                    conn.execute("UPDATE devices SET last_used_at=? WHERE id=?", (iso(utcnow()), device["id"]))
                except sqlite3.OperationalError:
                    pass
            return device

        def _conn(self):
            return connect(config)

        def do_GET(self) -> None:  # noqa: N802
            url = urlparse(self.path)
            path = url.path
            if path == "/healthz":
                self._send(200, "text/plain; charset=utf-8", b"ok\n")
                return
            if not self._identity_ok():
                self._send(401, "text/plain; charset=utf-8", b"ReSirch Killalot: reach this through tailscale serve, signed in as the owner.\n")
                return
            if path in ("/manifest.webmanifest", "/icon.svg"):
                body = asset(path.lstrip("/"))
                ctype = "application/manifest+json" if path.endswith("webmanifest") else "image/svg+xml"
                self._send(200 if body else 404, ctype, body or b"not found\n")
                return
            conn = self._conn()
            try:
                if path == "/pair":
                    code = parse_qs(url.query).get("code", [""])[0]
                    paired = redeem_pairing(conn, code) if code else None
                    if not paired:
                        self._send(403, "text/plain; charset=utf-8", b"This pairing link is invalid, used, or expired. Run `killalot device add` again.\n")
                        return
                    _, token = paired
                    cookie = f"{COOKIE_NAME}={token}; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=31536000"
                    self.send_response(303)
                    self.send_header("Location", "/")
                    self.send_header("Set-Cookie", cookie)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                device = self._device(conn)
                if device is None:
                    self._send(401, "text/html; charset=utf-8", DENIED_PAGE.encode())
                    return
                if path in ("/", "/index.html"):
                    self._send(200, "text/html; charset=utf-8", asset("app.html") or DENIED_PAGE.encode())
                elif path == "/api/inbox":
                    self._json({"inbox": inbox(conn), "buckets": BUCKETS, "device": device["name"]})
                elif path == "/api/projects":
                    self._json({"projects": project_rows(conn, config), "stale_days": config.stale_days})
                elif path == "/api/project":
                    project = parse_qs(url.query).get("path", [""])[0]
                    states = list(STATES) if parse_qs(url.query).get("all") else None
                    self._json({"project": project, "items": list_items(conn, project, states)})
                elif path == "/api/item":
                    item_id = int(parse_qs(url.query).get("id", ["0"])[0])
                    item = get_item(conn, item_id)
                    events = [dict(r) for r in conn.execute("SELECT * FROM events WHERE item=? ORDER BY id", (item_id,))]
                    self._json({"item": item, "events": events})
                elif path == "/api/digest":
                    self._json(digest(conn, config, mark=False))
                elif path == "/api/devices":
                    rows = [dict(r) | {"token_sha256": None} for r in conn.execute("SELECT * FROM devices ORDER BY id")]
                    self._json({"devices": rows, "current": device["id"]})
                else:
                    self._send(404, "text/plain; charset=utf-8", b"not found\n")
            except KillalotError as exc:
                self._json({"error": str(exc)}, 400)
            except ValueError:
                self._json({"error": "bad request"}, 400)
            finally:
                conn.close()

        def do_POST(self) -> None:  # noqa: N802
            url = urlparse(self.path)
            if not self._identity_ok():
                self._send(401, "text/plain; charset=utf-8", b"unauthorized\n")
                return
            if self.headers.get(CSRF_HEADER) != "1" or not self.headers.get("Content-Type", "").startswith("application/json"):
                self._json({"error": "missing CSRF header"}, 403)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(min(length, 1_000_000)) or b"{}")
            except (ValueError, json.JSONDecodeError):
                self._json({"error": "bad json"}, 400)
                return
            conn = self._conn()
            try:
                device = self._device(conn)
                if device is None:
                    self._json({"error": "device not paired or revoked"}, 401)
                    return
                via = f"device:{device['id']}"
                if url.path == "/api/action":
                    until = parse_when(body["until"]) if body.get("until") else None
                    item = act(conn, int(body["id"]), str(body["action"]), actor="me", via=via, reason=body.get("reason"),
                               until=until, answer=body.get("answer"), edits=body.get("edits"),
                               presented_fingerprint=body.get("fingerprint") if body.get("confirm") is True else None,
                               ttl_h=config.approval_ttl_h)
                    self._json({"item": item})
                elif url.path == "/api/add":
                    due = iso(parse_when(body["due"])) if body.get("due") else None
                    item_id = add(conn, project=str(body["project"]), kind=str(body.get("kind", "task")), title=str(body["title"]),
                                  via=via, body=str(body.get("body", "")), owner=str(body.get("owner", "me")), due_at=due)
                    self._json({"item": get_item(conn, item_id)})
                elif url.path == "/api/park":
                    revisit = parse_when(body["revisit"]) if body.get("revisit") else None
                    self._json(park(conn, str(body["project"]), reason=body.get("reason") or None, revisit=revisit, via=via))
                elif url.path == "/api/unpark":
                    unpark(conn, str(body["project"]), via=via)
                    self._json({"ok": True})
                elif url.path == "/api/digest":
                    self._json(digest(conn, config, mark=True))
                elif url.path == "/api/devices/revoke":
                    revoke_device(conn, int(body["id"]), via)
                    self._json({"ok": True})
                else:
                    self._json({"error": "not found"}, 404)
            except KillalotError as exc:
                self._json({"error": str(exc)}, 400)
            except (KeyError, ValueError, TypeError) as exc:
                self._json({"error": f"bad request: {exc}"}, 400)
            finally:
                conn.close()

        def _json(self, payload, code: int = 200) -> None:
            self._send(code, "application/json; charset=utf-8", json.dumps(json_ready(payload)).encode())

        def _send(self, code: int, ctype: str, body: bytes, set_cookie: str | None = None) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            if set_cookie:
                self.send_header("Set-Cookie", set_cookie)
            self.end_headers()
            self.wfile.write(body)

    return Handler


def backup(config: Config) -> Path | None:
    config.backups_dir.mkdir(parents=True, exist_ok=True)
    target = config.backups_dir / f"killalot-{datetime.now().strftime('%Y%m%d')}.db"
    if target.exists():
        return None
    src = connect(config)
    try:
        dst = sqlite3.connect(target)
        with dst:
            src.backup(dst)
        dst.close()
    finally:
        src.close()
    for old in sorted(config.backups_dir.glob("killalot-*.db"))[:-BACKUPS_KEPT]:
        old.unlink(missing_ok=True)
    return target


def worker_tick(config: Config, serve_state: dict) -> None:
    conn = connect(config)
    try:
        wake_snoozed(conn)
        last = parse_iso(get_meta(conn, "last_scan_at"))
        if last is None or (utcnow() - last).total_seconds() >= SCAN_EVERY_S:
            serve_state["last_scan"] = scan(conn, config)
    finally:
        conn.close()
    backup(config)


def serve(config: Config, args: argparse.Namespace) -> int:
    if not config.owner_login:
        raise KillalotError("[web] owner_login is required: the web app only answers the owner's Tailscale identity")
    bind = args.bind or config.bind
    port = args.port or config.port
    stop = threading.Event()
    serve_state: dict = {}

    def loop() -> None:
        while not stop.is_set():
            try:
                worker_tick(config, serve_state)
                serve_state["last_tick_error"] = None
            except Exception as exc:  # noqa: BLE001 - the server must outlive a bad tick
                serve_state["last_tick_error"] = repr(exc)
            stop.wait(WORKER_TICK_S)

    connect(config).close()  # migrate before the first request
    threading.Thread(target=loop, daemon=True).start()
    server = ThreadingHTTPServer((bind, port), make_handler(config, serve_state))
    print(f"resirch-killalot serving on http://{bind}:{port} for {config.owner_login}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()
    return 0


# ───────────────────────────── deploy ─────────────────────────────


def plugin_version() -> str:
    manifest = SKILL_DIR.parent.parent / ".claude-plugin" / "plugin.json"
    try:
        return json.loads(manifest.read_text())["version"]
    except (OSError, KeyError, json.JSONDecodeError):
        pass
    try:
        return (SKILL_DIR / "VERSION").read_text().strip()
    except OSError as exc:
        raise KillalotError("cannot tell which release this is (no plugin manifest and no VERSION file)") from exc


def deploy(config: Config, *, start_service: bool = True, home: Path | None = None) -> dict:
    """Copy this release under <store>/app/<version>, point app/current at it, install the wrapper
    and the user unit, and (re)start the service. Run it from the *installed* plugin copy."""
    home = home or Path.home()
    version = plugin_version()
    target = config.app_dir / version
    staging = config.app_dir / f".{version}.staging"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    shutil.copytree(SKILL_DIR / "scripts", staging / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(SKILL_DIR / "assets", staging / "assets")
    (staging / "VERSION").write_text(version + "\n")
    shutil.rmtree(target, ignore_errors=True)
    staging.rename(target)
    current = config.app_dir / "current"
    link_tmp = config.app_dir / ".current.tmp"
    link_tmp.unlink(missing_ok=True)
    link_tmp.symlink_to(version)
    os.replace(link_tmp, current)
    script = current / "scripts" / "killalot.py"

    wrapper = home / ".local" / "bin" / "killalot"
    wrapper.parent.mkdir(parents=True, exist_ok=True)
    wrapper.write_text(f'#!/bin/sh\nKILLALOT_CONFIG="{config.config_path}" exec python3 "{script}" "$@"\n')
    wrapper.chmod(0o755)

    unit_dir = home / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    template = (SKILL_DIR / "assets" / SERVICE_NAME).read_text()
    (unit_dir / SERVICE_NAME).write_text(template.replace("__KILLALOT_PY__", str(script)).replace("__KILLALOT_CONFIG__", str(config.config_path)))

    versions = sorted((p for p in config.app_dir.iterdir() if p.is_dir() and not p.name.startswith(".") and p.name != "current"),
                      key=lambda p: p.stat().st_mtime)
    for old in versions[:-APP_VERSIONS_KEPT]:
        if old.name != version:
            shutil.rmtree(old, ignore_errors=True)

    healthy = None
    if start_service:
        for cmd in (["systemctl", "--user", "daemon-reload"], ["systemctl", "--user", "enable", SERVICE_NAME], ["systemctl", "--user", "restart", SERVICE_NAME]):
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                raise KillalotError(f"{' '.join(cmd)} failed: {result.stderr.strip()}")
        healthy = wait_healthy(config)
    return {"version": version, "app": str(target), "wrapper": str(wrapper), "unit": str(unit_dir / SERVICE_NAME), "healthy": healthy}


def wait_healthy(config: Config, attempts: int = 40) -> bool:
    url = f"http://127.0.0.1:{config.port}/healthz"
    for _ in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return True
        except OSError:
            pass
        time.sleep(0.25)
    return False


def doctor(config: Config) -> list[tuple[bool, str]]:
    checks: list[tuple[bool, str]] = []
    try:
        conn = connect(config)
        ok = conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        checks.append((ok, f"store {config.db_path} (schema {get_meta(conn, 'schema_version')}) integrity {'ok' if ok else 'FAILED'}"))
        n = conn.execute("SELECT COUNT(*) FROM projects WHERE missing=0").fetchone()[0]
        checks.append((n > 0, f"{n} tagged projects known (run `killalot scan` if 0)"))
        conn.close()
    except (KillalotError, sqlite3.Error) as exc:
        checks.append((False, f"store: {exc}"))
    for root in config.project_roots:
        checks.append((root.is_dir(), f"project root {root}"))
    checks.append((bool(config.owner_login), f"[web] owner_login = {config.owner_login or 'unset'}"))
    on_path = shutil.which("killalot")
    checks.append((on_path is not None, f"killalot on PATH: {on_path or 'no — run `killalot.py deploy`'}"))
    active = subprocess.run(["systemctl", "--user", "is-active", SERVICE_NAME], capture_output=True, text=True).stdout.strip()
    checks.append((active == "active", f"{SERVICE_NAME}: {active or 'unknown'}"))
    checks.append((wait_healthy(config, attempts=2), f"http://127.0.0.1:{config.port}/healthz"))
    try:
        ts = subprocess.run(["tailscale", "serve", "status"], capture_output=True, text=True, timeout=5).stdout
        checks.append((f":{config.port}" in ts, f"tailscale serve proxies 127.0.0.1:{config.port}"))
    except (OSError, subprocess.TimeoutExpired):
        checks.append((False, "tailscale not available"))
    return checks


# ───────────────────────────── CLI ─────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="killalot", description="ReSirch Killalot — the owner's cross-project list of work.")
    parser.add_argument("--config", help=f"config path (default: $KILLALOT_CONFIG, else {DEFAULT_CONFIG})")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("scan", help="discover tagged projects under the configured roots and refresh their activity")
    sub.add_parser("projects", help="one line per project: open items, next action, last activity, stale/parked")

    p = sub.add_parser("list", help="items of one project, or of all")
    p.add_argument("--project", help="'.', a path, or a project name (default: all projects)")
    p.add_argument("--state", help="comma list of states (default: every open state)")

    sub.add_parser("inbox", help="everything waiting on the owner, most costly delay first")

    p = sub.add_parser("show", help="one item with its evidence and history")
    p.add_argument("id", type=int)

    p = sub.add_parser("propose", help="(agent) propose an item; only the owner can accept it")
    p.add_argument("--project", default=".")
    p.add_argument("--kind", choices=[k for k in KINDS if k != "approval"], default="task")
    p.add_argument("--title", required=True)
    p.add_argument("--body", default="")
    p.add_argument("--evidence", action="append", default=[], help="why it exists / where it came from (repeatable, required)")
    p.add_argument("--owner", choices=OWNERS, default="agent", help="who would do it once accepted")
    p.add_argument("--due")
    p.add_argument("--session", help="<host>/<session id> of the proposing session")

    p = sub.add_parser("request-approval", help="(agent) file a dry-run for the owner to approve from a paired device")
    p.add_argument("--project", default=".")
    p.add_argument("--title", required=True)
    p.add_argument("--evidence-file", required=True, help="the complete dry-run output; the approval binds to its fingerprint")
    p.add_argument("--command", dest="confirm_command", help="the exact command that runs once approved")
    p.add_argument("--body", default="")
    p.add_argument("--evidence", action="append", default=[])
    p.add_argument("--session")

    p = sub.add_parser("check-approval", help="(executor) exit 0 only if approved, unexpired, and the dry-run is unchanged")
    p.add_argument("--item", type=int, required=True)
    p.add_argument("--evidence-file", required=True, help="a fresh run of the same dry-run")

    for name, help_text in (("start", "begin work on an accepted item"), ("wait", "the item is blocked; the reason becomes a question to the owner"),
                            ("done", "finish an item, with evidence")):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("id", type=int)
        p.add_argument("--evidence", action="append", default=[])
        p.add_argument("--reason")
        p.add_argument("--via", help="set when the owner does it: session:<host>/<id> or terminal")

    p = sub.add_parser("add", help="(owner) add an accepted item directly")
    p.add_argument("--project", default=".")
    p.add_argument("--kind", choices=[k for k in KINDS if k != "approval"], default="task")
    p.add_argument("--title", required=True)
    p.add_argument("--body", default="")
    p.add_argument("--owner", choices=OWNERS, default="me")
    p.add_argument("--due")
    p.add_argument("--via", required=True)

    for name in ("accept", "reject", "drop", "snooze", "answer", "edit"):
        p = sub.add_parser(name, help=f"(owner) {name} an item — agents relay this only on the owner's explicit words")
        p.add_argument("id", type=int)
        p.add_argument("--via", required=True, help="session:<host>/<session id> when relayed by an agent, or terminal")
        if name in ("reject", "drop"):
            p.add_argument("--reason", required=(name == "reject"))
        if name == "snooze":
            p.add_argument("--until", required=True)
        if name == "answer":
            p.add_argument("--text", required=True)
        if name == "edit":
            p.add_argument("--title")
            p.add_argument("--body")
            p.add_argument("--kind", choices=[k for k in KINDS if k != "approval"])
            p.add_argument("--owner", choices=OWNERS)
            p.add_argument("--due")
            p.add_argument("--accept", action="store_true", help="edit, then accept the proposal")

    p = sub.add_parser("approve", help="refused: approvals are given only from a paired device")
    p.add_argument("id", type=int, nargs="?")

    p = sub.add_parser("park", help="(owner) park a project on purpose: never stale while parked")
    p.add_argument("project")
    p.add_argument("--reason")
    p.add_argument("--revisit", help="when to look again; becomes a reminder")
    p.add_argument("--via", required=True)

    p = sub.add_parser("unpark", help="(owner) unpark a project")
    p.add_argument("project")
    p.add_argument("--via", required=True)

    p = sub.add_parser("digest", help="everything since the last digest the owner asked for")
    p.add_argument("--peek", action="store_true", help="show it without moving the 'last digest' mark")

    p = sub.add_parser("device", help="pair, list, or revoke the owner's devices")
    dsub = p.add_subparsers(dest="device_command", required=True)
    q = dsub.add_parser("add")
    q.add_argument("name")
    dsub.add_parser("list")
    q = dsub.add_parser("revoke")
    q.add_argument("id", type=int)

    p = sub.add_parser("hook", help="host hook handlers")
    hsub = p.add_subparsers(dest="hook_command", required=True)
    hsub.add_parser("session-start", help="print session-start context for the project in the hook's cwd")

    p = sub.add_parser("serve", help="the web app (bind to localhost; expose with tailscale serve)")
    p.add_argument("--port", type=int)
    p.add_argument("--bind")

    p = sub.add_parser("deploy", help="install this release as the running service (run from the installed plugin)")
    p.add_argument("--no-service", action="store_true", help="copy and install files without touching systemd")

    sub.add_parser("doctor", help="check config, store, PATH, service and tailscale serve")
    return parser


def read_evidence_file(path: str) -> str:
    try:
        return Path(path).expanduser().read_text()
    except OSError as exc:
        raise KillalotError(f"cannot read {path}: {exc}") from exc


def emit(args, payload, text_fn) -> None:
    if args.json:
        print(json.dumps(json_ready(payload), indent=2))
    else:
        text_fn()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config_path = config_path_from_env(args.config)
    if args.command == "hook":
        print(hook_session_start(config_path, sys.stdin.read() if not sys.stdin.isatty() else ""))
        return 0
    try:
        if args.command == "approve":
            raise KillalotError("approvals are given only from a paired device: open the item in the web app, read the full dry-run, approve there")
        config = load_config(config_path)
        if args.command == "serve":
            return serve(config, args)
        if args.command == "deploy":
            result = deploy(config, start_service=not args.no_service)
            emit(args, result, lambda: print(f"deployed {result['version']} → {result['app']}\nwrapper {result['wrapper']}\nunit {result['unit']}"
                                             + ("" if result["healthy"] is None else f"\nhealthy: {result['healthy']}")))
            return 0 if result["healthy"] in (None, True) else 1
        if args.command == "doctor":
            checks = doctor(config)
            emit(args, [{"ok": ok, "check": text} for ok, text in checks], lambda: [print(("ok   " if ok else "FAIL ") + text) for ok, text in checks])
            return 0 if all(ok for ok, _ in checks) else 1
        conn = connect(config)
        try:
            return run_command(conn, config, args)
        finally:
            conn.close()
    except KillalotError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def run_command(conn, config: Config, args) -> int:
    cmd = args.command
    if cmd == "scan":
        result = scan(conn, config)
        emit(args, result, lambda: print(f"{result['projects']} tagged projects; {result['missing']} known but not found"))
        return 0
    if cmd == "projects":
        if get_meta(conn, "last_scan_at") is None:
            scan(conn, config)
        rows = project_rows(conn, config)

        def show():
            for p in rows:
                marks = " PARKED" if p["parked"] else (" STALE" if p["stale"] else "")
                marks += " (activity since parking: unpark?)" if p["unpark_suggested"] else ""
                nxt = f"next #{p['next']['id']} {p['next']['title']}" if p["next"] else "no next action"
                print(f"{p['name']:<34} {p['open']:>3} open  last {local(p['last_activity'])}  {nxt}{marks}")
        emit(args, rows, show)
        return 0
    if cmd == "list":
        project = resolve_project(conn, args.project) if args.project else None
        states = [s.strip() for s in args.state.split(",")] if args.state else None
        if states and set(states) - set(STATES):
            raise KillalotError(f"states are {', '.join(STATES)}")
        items = list_items(conn, project, states)
        emit(args, items, lambda: print_items(items))
        return 0
    if cmd == "inbox":
        box = inbox(conn)

        def show():
            for key, label in BUCKETS:
                print(f"{label} ({len(box[key])})")
                print_items(box[key])
        emit(args, box, show)
        return 0
    if cmd == "show":
        item = get_item(conn, args.id)
        events = [dict(r) for r in conn.execute("SELECT * FROM events WHERE item=? ORDER BY id", (args.id,))]

        def show():
            print(line(item | {"project_name": None}))
            print(f"project  {item['project']}\norigin   {item['origin']}\nowner    {item['owner']}")
            if item["body"]:
                print(f"\n{item['body']}")
            for ev in item["evidence"]:
                print(f"evidence {ev}")
            for key in ("reject_reason", "wait_reason", "answer", "command", "fingerprint", "approved_until"):
                if item.get(key):
                    print(f"{key:<8} {item[key]}")
            if item.get("evidence_text"):
                print("\n--- dry-run ---\n" + item["evidence_text"].rstrip() + "\n--- end ---")
            print("\nhistory")
            for ev in events:
                print(f"  {local(ev['at'])}  {ev['actor']:<6} {ev['via'] or '':<24} {ev['action']:<8} {ev['from_state'] or ''} → {ev['to_state'] or ''}")
        emit(args, {"item": item, "events": events}, show)
        return 0
    if cmd == "propose":
        project = resolve_project(conn, args.project)
        due = iso(parse_when(args.due)) if args.due else None
        item_id = propose(conn, project=project, kind=args.kind, title=args.title, body=args.body, evidence=args.evidence,
                          owner=args.owner, due_at=due, session=args.session)
        emit(args, {"id": item_id}, lambda: print(f"proposed #{item_id}; it waits for the owner"))
        return 0
    if cmd == "request-approval":
        project = resolve_project(conn, args.project)
        item_id, new = request_approval(conn, project=project, title=args.title, evidence_text=read_evidence_file(args.evidence_file),
                                        command=args.confirm_command, body=args.body, evidence=args.evidence, session=args.session)
        emit(args, {"id": item_id, "new": new}, lambda: print(f"{'filed' if new else 'already filed'} approval #{item_id}; the owner approves it from a paired device"))
        return 0
    if cmd == "check-approval":
        ok, why = check_approval(conn, args.item, read_evidence_file(args.evidence_file))
        emit(args, {"approved": ok, "reason": why}, lambda: print(("approved: " if ok else "NOT approved: ") + why))
        return 0 if ok else 1
    if cmd in ("start", "wait", "done"):
        actor = "me" if args.via else "agent"
        item = act(conn, args.id, cmd, actor=actor, via=args.via, reason=args.reason, evidence=args.evidence)
        emit(args, item, lambda: print(line(item)))
        return 0
    if cmd == "add":
        project = resolve_project(conn, args.project)
        due = iso(parse_when(args.due)) if args.due else None
        check_via("me", args.via)
        item_id = add(conn, project=project, kind=args.kind, title=args.title, via=args.via, body=args.body, owner=args.owner, due_at=due)
        emit(args, {"id": item_id}, lambda: print(f"added #{item_id}"))
        return 0
    if cmd in ("accept", "reject", "drop", "snooze", "answer", "edit"):
        kwargs: dict = {"actor": "me", "via": args.via}
        if cmd in ("reject", "drop"):
            kwargs["reason"] = args.reason
        if cmd == "snooze":
            kwargs["until"] = parse_when(args.until)
        if cmd == "answer":
            kwargs["answer"] = args.text
        if cmd == "edit":
            kwargs["edits"] = {"title": args.title, "body": args.body, "kind": args.kind, "owner": args.owner,
                               "due_at": iso(parse_when(args.due)) if args.due else None}
        item = act(conn, args.id, cmd, **kwargs)
        if cmd == "edit" and args.accept:
            item = act(conn, args.id, "accept", actor="me", via=args.via)
        emit(args, item, lambda: print(line(item)))
        return 0
    if cmd in ("park", "unpark"):
        check_via("me", args.via)
        project = resolve_project(conn, args.project)
        if cmd == "park":
            result = park(conn, project, reason=args.reason, revisit=parse_when(args.revisit) if args.revisit else None, via=args.via)
            emit(args, result, lambda: print(f"parked {project}" + (f"; revisit reminder #{result['revisit_item']}" if result["revisit_item"] else "")))
        else:
            unpark(conn, project, via=args.via)
            emit(args, {"project": project}, lambda: print(f"unparked {project}"))
        return 0
    if cmd == "digest":
        result = digest(conn, config, mark=not args.peek)

        def show():
            print(f"Digest since {local(result['since'])}")
            for key, label in BUCKETS:
                if result["inbox"][key]:
                    print(f"\n{label} ({len(result['inbox'][key])})")
                    print_items(result["inbox"][key])
            for title, rows in (("New proposals", result["new_proposals"]), ("Finished", result["finished"])):
                if rows:
                    print(f"\n{title} ({len(rows)})")
                    for ev in rows:
                        print(f"  #{ev['item']:<4} {ev['project_name'] or ''}: {ev['title']}")
            if result["stale"]:
                print(f"\nStale (no activity for {config.stale_days}+ days, items open)")
                for p in result["stale"]:
                    print(f"  {p['name']}: last {local(p['last_activity'])}, {p['open']} open")
            if result["unpark_suggested"]:
                print("\nParked, but active again (unpark?)")
                for p in result["unpark_suggested"]:
                    print(f"  {p['name']}")
        emit(args, result, show)
        return 0
    if cmd == "device":
        if args.device_command == "add":
            code = create_pairing(conn, args.name)
            base = (config.public_base or f"http://127.0.0.1:{config.port}").rstrip("/")
            link = f"{base}/pair?code={code}"
            emit(args, {"link": link, "expires_in_s": PAIRING_TTL_S}, lambda: print(f"open on the device within 10 minutes:\n{link}"))
            return 0
        if args.device_command == "list":
            rows = [dict(r) | {"token_sha256": None} for r in conn.execute("SELECT * FROM devices ORDER BY id")]
            emit(args, rows, lambda: [print(f"{r['id']:<3} {r['name']:<20} paired {local(r['created_at'])}  last used {local(r['last_used_at'])}"
                                            + ("  REVOKED" if r["revoked_at"] else "")) for r in rows])
            return 0
        if args.device_command == "revoke":
            revoke_device(conn, args.id, "terminal")
            emit(args, {"revoked": args.id}, lambda: print(f"revoked device {args.id}"))
            return 0
    raise KillalotError(f"unknown command {cmd}")


if __name__ == "__main__":
    sys.exit(main())
