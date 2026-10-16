#!/usr/bin/env python3
"""Per-machine worker for `runid.py`: inventory, backup, apply, rollback, and index of one checkout.

Stdlib only and self-contained: `runid.py` pipes this whole file over stdin to `python3 -` on
each machine (the hub included) followed by one `serve("<base64 request>")` call, so a rig needs
nothing installed. The worker prints exactly one result line, `__RUNID__ <json>`.

It also holds the hashed-v2 identity functions (`hash_from_values`, `label`, `build_index`),
which must stay byte-identical to `code/common/run_id.py` in the project template; the test suite
asserts that parity.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import socket
import sys
import uuid
from pathlib import Path
from urllib.parse import quote

HASH_PREFIX = "rid-"
HASHED_V2 = "hashed-v2"
HASHED_V2_HEX = 32
MAP_NAME = "RUN_ID_MAP.json"
VERSIONS_NAME = "RUN_ID_VERSIONS.json"
HISTORY_NAME = "RUN_ID_HISTORY.json"
IDENTITY_NAMES = (MAP_NAME, VERSIONS_NAME, HISTORY_NAME)
MIGRATIONS_DIR = ".run_id_migrations"
RESULT_MARKER = "__RUNID__ "
HASH_TEXT_RE = re.compile(r"rid-[0-9a-f]{32}")
HASH_BYTES_RE = re.compile(rb"rid-[0-9a-f]{32}")
RUN_DIR_RE = re.compile(r"^(rid-[0-9a-f]{32})(\..+)?$")
SUB_EXPERIMENT_RE = re.compile(r"^\d{3}_")
TEXT_MAX_BYTES = 4 * 1024 * 1024
PLOT_MAX_BYTES = 64 * 1024 * 1024
SKIP_NAMES = frozenset({".lock"})


# ───────────────────────────── identity (template parity) ─────────────────────────────


def canonical_json(payload) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def hash_from_values(values) -> str:
    canonical = canonical_json({"layout": HASHED_V2, "params": dict(values)})
    return HASH_PREFIX + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:HASHED_V2_HEX]


def _component(value) -> str:
    if isinstance(value, (dict, list, tuple)):
        value = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return quote(str(value), safe="-._~")


def label(values, params) -> str:
    """The readable `k=v,...` display label (WandB name and tags), in identity param order."""
    return ",".join(f"{_component(p)}={_component(values[p])}" for p in params)


def record_bytes(record: dict) -> bytes:
    """A `.run_id.json` exactly as the template's guard writes it."""
    return json.dumps(record, indent=2, sort_keys=True).encode()


def config_bytes(config: dict) -> bytes:
    """A `.run_config.json` exactly as the template's guard writes it."""
    return json.dumps(config, indent=2, sort_keys=True, default=str).encode()


def map_bytes(payload: dict) -> bytes:
    return json.dumps(payload, indent=2, sort_keys=True).encode()


def _optional_json(path: Path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


def build_index(experiment_root: Path, versions: list, history: list) -> dict:
    """The hashed-v2 RUN_ID_MAP payload, built as the template's build_run_id_index builds it.

    ``versions`` / ``history`` are the RUN_ID_VERSIONS / RUN_ID_HISTORY lists of the tracked
    config/<exp>/ dir; the map embeds copies of both, and its version is the last entry's.
    """
    experiment_root = Path(experiment_root)
    payload = {
        "layout": HASHED_V2, "version": versions[-1]["version"], "versions": versions, "history": history,
        "by_hash": {}, "by_params": {}, "configs": {},
    }
    for record_file in sorted(experiment_root.glob("*/.run_id.json")):
        record = json.loads(record_file.read_text())
        run_dir = record_file.parent
        if record.get("layout") != HASHED_V2 or record.get("hash") != run_dir.name:
            raise RuntimeError(
                f"{record_file} does not match hashed-v2 path {run_dir.name!r}; mixed or "
                "moved run directories block the map"
            )
        if hash_from_values(record["params"]) != record["hash"]:
            raise RuntimeError(
                f"{record_file}: hash {record['hash']} does not match its params "
                f"{record['params']!r}; never hand-edit a run_id record"
            )
        status = _optional_json(run_dir / ".status.json")
        status = status if isinstance(status, dict) else {}
        run_hash, params = record["hash"], record["params"]
        known = payload["by_hash"].get(run_hash)
        if known is not None and known["params"] != params:
            raise RuntimeError(f"run_id hash collision at {record_file}: {run_hash}")
        key = canonical_json(params)
        claimed = payload["by_params"].get(key)
        if claimed is not None and claimed != run_hash:
            raise RuntimeError(f"run_id params {params!r} at {record_file} are claimed by both {claimed} and {run_hash}")
        payload["by_hash"][run_hash] = {
            "params": params,
            "version": record["version"],
            "wave_id": status.get("wave_id"),
            "state": status.get("state"),
            "rig": status.get("rig"),
        }
        payload["by_params"][key] = run_hash
        payload["configs"][run_hash] = _optional_json(run_dir / ".run_config.json")
    return payload


# ───────────────────────────── file helpers ─────────────────────────────


def sha256_file(path: Path) -> str | None:
    try:
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
        return None


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_atomic(path: Path, data: bytes) -> None:
    """Replace `path` with `data` so a reader sees the old bytes or the new, keeping its mode."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp.{socket.gethostname()}.{os.getpid()}.{uuid.uuid4().hex[:8]}")
    try:
        tmp.write_bytes(data)
        if path.exists():
            shutil.copymode(path, tmp)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def replace_hashes(data: bytes, mapping: dict) -> bytes:
    return HASH_BYTES_RE.sub(lambda m: mapping.get(m.group(0).decode(), m.group(0).decode()).encode(), data)


def _text_hits(path: Path, mapping: dict, limit: int = TEXT_MAX_BYTES) -> tuple[int, str | None]:
    """(old-hash occurrences, sha256) of a small text file; (0, None) for anything else."""
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
            return 0, None
        data = path.read_bytes()
    except OSError:
        return 0, None
    if b"\x00" in data:
        return 0, None
    hits = sum(1 for match in HASH_BYTES_RE.finditer(data) if match.group(0).decode() in mapping)
    return hits, sha256_bytes(data)


def _walk_files(base: Path):
    for directory, dirs, files in os.walk(base):
        dirs[:] = sorted(d for d in dirs if d != MIGRATIONS_DIR)
        for name in sorted(files):
            if name in SKIP_NAMES or ".tmp." in name or name.endswith(".tmp"):
                continue
            yield Path(directory) / name


def _rel(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


# ───────────────────────────── operations ─────────────────────────────


def op_inventory(req: dict) -> dict:
    """What this checkout holds of the experiment: dirs to rename, text files naming an old hash."""
    root = Path(req["root"])
    exp = req["experiment"]
    mapping: dict = req["map"]
    new_hashes = set(mapping.values())
    out = {
        "renames": [],
        "files": [],
        "collisions": [],
        "unmapped": [],
        "records": {},
        "identity": {},
        "plots": [],
        "wandb_dirs": 0,
        "has_experiment": False,
    }
    seen_files: set[str] = set()

    def consider(path: Path) -> None:
        rel = _rel(root, path)
        if rel in seen_files:
            return
        hits, digest = _text_hits(path, mapping)
        if hits:
            seen_files.add(rel)
            out["files"].append({"path": rel, "sha256": digest, "size": path.stat().st_size, "hits": hits})

    for area in req["areas"]:
        base = root / area / exp
        if not base.is_dir():
            continue
        out["has_experiment"] = True
        for child in sorted(base.iterdir()):
            if child.name == MIGRATIONS_DIR:
                continue
            if area == "evaluations" and child.name in IDENTITY_NAMES:
                continue
            if child.is_dir() and SUB_EXPERIMENT_RE.match(child.name):
                continue  # a nested sub-experiment is its own experiment
            match = RUN_DIR_RE.match(child.name)
            if match and child.is_dir():
                old = match.group(1)
                suffix = match.group(2) or ""
                if old in mapping:
                    target = base / (mapping[old] + suffix)
                    entry = {"from": _rel(root, child), "to": _rel(root, target)}
                    out["renames"].append(entry)
                    if target.exists():
                        out["collisions"].append(f"{entry['to']} already exists")
                elif old in new_hashes:
                    out["collisions"].append(f"{_rel(root, child)} already carries a planned new hash")
                else:
                    out["unmapped"].append(_rel(root, child))
                if area == "evaluations" and not suffix and old in mapping:
                    for name in (".run_id.json", ".run_config.json", ".status.json"):
                        out["records"][f"{_rel(root, child)}/{name}"] = sha256_file(child / name)
            if child.is_dir():
                if area == "logs":
                    out["wandb_dirs"] += sum(1 for _ in child.rglob("wandb-metadata.json"))
                for path in _walk_files(child):
                    consider(path)
            elif child.is_file():
                consider(child)
    for rel in req.get("identity", [f"evaluations/{exp}/{MAP_NAME}"]):
        out["identity"][rel] = sha256_file(root / rel)
    for wave in req.get("waves", []):
        state = root / ".waves" / "_state" / wave
        if state.is_dir():
            for path in _walk_files(state):
                consider(path)
    for rel in req.get("extras", []):
        path = root / rel
        if path.is_file():
            consider(path)
    if req.get("plots"):
        plots = root / "plots"
        if plots.is_dir():
            for path in _walk_files(plots):
                rel = _rel(root, path)
                named = any(found in mapping for found in HASH_TEXT_RE.findall(rel))
                hits, _ = (0, None) if named else _text_hits(path, mapping, PLOT_MAX_BYTES)
                if named or hits:
                    out["plots"].append(rel)
    return out


def op_backup(req: dict) -> dict:
    root = Path(req["root"])
    backup = Path(req["backup_dir"])
    for item in req["files"]:
        if item["sha256"] is None:
            continue
        source = root / item["path"]
        if sha256_file(source) != item["sha256"]:
            raise RuntimeError(f"{item['path']} changed since the plan; nothing was modified")
        target = backup / item["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        if sha256_file(target) != item["sha256"]:
            raise RuntimeError(f"backup of {item['path']} does not verify")
    return {"backed_up": sum(1 for item in req["files"] if item["sha256"] is not None)}


def _moved(path: str, renames: list[dict]) -> str:
    """Where `path` (named before the renames) lives after them."""
    for rename in renames:
        prefix = rename["from"]
        if path == prefix or path.startswith(prefix + "/"):
            return rename["to"] + path[len(prefix):]
    return path


def _restore(root: Path, backup: Path, files: list[dict], renames: list[dict]) -> None:
    """Undo renames (newest first), then put each file's original bytes back."""
    for rename in reversed(renames):
        source, target = root / rename["to"], root / rename["from"]
        if source.exists() and not target.exists():
            os.rename(source, target)
    for item in reversed(files):
        path = root / item["path"]
        if item["sha_before"] is None:
            if path.exists():
                path.unlink()
            continue
        write_atomic(path, (backup / item["path"]).read_bytes())


def op_apply(req: dict) -> dict:
    """Rewrite the planned files, then rename the planned dirs; undo everything on any failure."""
    root = Path(req["root"])
    backup = Path(req["backup_dir"])
    mapping = req["map"]
    done_files: list[dict] = []
    done_renames: list[dict] = []
    after: dict[str, str] = {}
    try:
        for item in req["files"]:
            path = root / item["path"]
            current = sha256_file(path)
            if current != item["sha256"]:
                raise RuntimeError(f"{item['path']} changed since the plan (sha {current} != {item['sha256']})")
            if item["action"] == "content":
                data = base64.b64decode(item["content_b64"])
            else:
                data = replace_hashes(path.read_bytes(), mapping)
            write_atomic(path, data)
            done_files.append({"path": item["path"], "sha_before": item["sha256"]})
            after[item["path"]] = sha256_bytes(data)
        if req.get("fail_after_files"):  # test hook: a failure between the two phases
            raise RuntimeError("injected failure")
        for rename in req["renames"]:
            source, target = root / rename["from"], root / rename["to"]
            if target.exists():
                raise RuntimeError(f"{rename['to']} already exists")
            if not source.exists():
                raise RuntimeError(f"{rename['from']} is missing")
            os.rename(source, target)
            done_renames.append(rename)
    except Exception as exc:  # noqa: BLE001 -- every failure is undone, then reported
        try:
            _restore(root, backup, done_files, done_renames)
            undone = True
        except Exception as undo_exc:  # noqa: BLE001
            return {"ok": False, "error": f"{exc}; undo also failed: {undo_exc}", "undone": False}
        return {"ok": False, "error": str(exc), "undone": True}
    return {"ok": True, "after": after}


def op_rollback(req: dict) -> dict:
    """Reverse one machine's applied migration from its backups, refusing if anything moved on."""
    root = Path(req["root"])
    backup = Path(req["backup_dir"])
    renames = req["renames"]
    problems = []
    for rename in renames:
        if not (root / rename["to"]).exists():
            problems.append(f"{rename['to']} is missing")
        if (root / rename["from"]).exists():
            problems.append(f"{rename['from']} exists again")
    for item in req["files"]:
        where = _moved(item["path"], renames)
        current = sha256_file(root / where)
        if current != item["sha_after"]:
            problems.append(f"{where} changed after the migration")
        if item["sha_before"] is not None and sha256_file(backup / item["path"]) != item["sha_before"]:
            problems.append(f"backup of {item['path']} is missing or altered")
    if problems and not req.get("force"):
        raise RuntimeError("refusing to roll back: " + "; ".join(problems))
    _restore(root, backup, req["files"], renames)
    return {"restored": len(req["files"]), "renamed_back": len(renames)}


def op_index(req: dict) -> dict:
    root = Path(req["root"])
    experiment_root = root / "evaluations" / req["experiment"]
    payload = build_index(experiment_root, req["versions"], req["history"])
    map_file = experiment_root / MAP_NAME
    if req.get("write"):
        write_atomic(map_file, map_bytes(payload))
    by_hash = {h: {"params": e["params"], "version": e["version"]} for h, e in payload["by_hash"].items()}
    return {"by_hash": by_hash, "map_sha": sha256_file(map_file)}


OPS = {
    "inventory": op_inventory,
    "backup": op_backup,
    "apply": op_apply,
    "rollback": op_rollback,
    "index": op_index,
    "ping": lambda req: {"host": socket.gethostname()},
}


def serve(request_b64: str) -> None:
    request = json.loads(base64.b64decode(request_b64))
    try:
        result = OPS[request["op"]](request)
        if "ok" not in result:
            result["ok"] = True
    except Exception as exc:  # noqa: BLE001 -- reported to the orchestrator, never swallowed
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    sys.stdout.write(RESULT_MARKER + json.dumps(result) + "\n")
