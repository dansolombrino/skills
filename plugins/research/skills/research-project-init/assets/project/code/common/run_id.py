"""Shared run_id helpers.

New experiments use the ``hashed-v2`` layout. The identifying params live in
the tracked source tree, ``config/<exp>/RUN_ID_VERSIONS.json``, not in
constants, and are read through source_path() so every wave worktree uses the
identity version it was deployed with (evaluations/ is gitignored and rig-local;
it holds only the derived RUN_ID_MAP.json):

    RUN_ID_PATH_LAYOUT = "hashed-v2"
    spec_root = source_path("config/<exp>")          # code/common/paths.py
    version, params = load_run_id_spec(spec_root)

    RUN_ID_VERSIONS.json  [{"version": 1, "params": ["model", "lr", "seed"],
                            "date": "2026-10-16", "reason": "..."}, ...]   # last = current
    RUN_ID_HISTORY.json   [{"from_version": 1, "to_version": 2, "date": "...",
                            "map": {"<old hash>": "<new hash>"},
                            "backfill": {"<param>": {"value": ..., "source": "..."}}}, ...]

Identity params are top-level config keys only (no dots); to identify a nested
value, elect its top-level subtree key. A run's directory is ``rid-<32 hex>``:
sha256 of the canonical JSON ``{"layout": "hashed-v2", "params": {param:
value-or-null}}`` (sorted keys, so param order never matters; a param missing
from the config hashes as null). run_id_label() gives the readable ``k=v,...``
shown beside the hash (run_id_name), which stays the identity.
Changing the identifying params means a new version plus a run_id migration
that renames every hash through RUN_ID_HISTORY.json.

``config/<exp>/PARAM_GLOSSARY.json`` (any layout) maps each param to the full
name and explanation plots show instead of the raw key; it is not identity:

    PARAM_GLOSSARY.json   {"lr": {"name": "Learning rate",
                                  "explanation": "Step size of each optimizer update."}}

    glossary = load_param_glossary(spec_root, params)   # hard-fails on a missing entry

Experiments already pinned to an older layout keep declaring, in their own .py,
the authoritative ordered list of config params that uniquely identify a run,
its experiment-wide path layout, and (for segments-v2) the user-approved
segment spec:

    RUN_ID_PARAMS = ["model", "lr", "wd", "beta1", "seed"]
    RUN_ID_PATH_LAYOUT = "segments-v2"
    RUN_ID_SEGMENTS = [                      # one tuple = one directory component
        ("model",),
        ({"optim_params": ("lr", "wd", "beta1")}, "seed"),
    ]

A segment item is either a param name (rendered explicitly as ``key=value``) or a
one-key dict naming a hash group (rendered as ``name=<16 hex>``). That renders
``model=mlp/optim_params=3f0c...,seed=0``. Under segments-v2 every rendered
key and value uses only ``A-Z a-z 0-9 . _ -``, so a path never needs URL or
shell escaping: a list renders its items joined with ``-`` (``[64, 128]`` ->
``64-128``), and a value with no such rendering (a space, ``/``, a dict, an
empty list, a list item containing ``-``, e.g. a negative number) is refused,
never escaped -- put its param in a hash group. The legacy literals
``segments-v2``, ``segments-v1`` (percent-encoded), ``nested``,
``collapsed-v1`` and ``hashed-v1`` stay supported, rendering unchanged, for
experiments already pinned to them and are never offered to new experiments.

Use these helpers for ALL artifact paths and run names. Never hand-build them.

Scripts must call guard_run_config() before writing ANY artifact (and before the
StatusWriter starts): it hard-fails on run_id collisions — same run_id, different
full config — which happen when a param was added to the config but not to
RUN_ID_PARAMS (schema evolution rules: conventions.md), and on hash collisions.
One guard at the evaluations/ run dir suffices: checkpoints/ shares the same
run_id. plots/ is not run-scoped and needs no guard. The guard's I/O is O(1)
per run: it never scans the experiment or writes RUN_ID_MAP.json. The map is
rebuilt once per wave with write_run_id_map(), and check_run_id_plan() rescans
every record before dispatch.
"""

import hashlib
import json
import os
import re
import shlex
import socket
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from urllib.parse import quote

RUN_ID_HASH_PREFIX = "rid-"
RUN_ID_HASH_HEX = 16
RUN_ID_MAP_NAME = "RUN_ID_MAP.json"
RUN_ID_VERSIONS_NAME = "RUN_ID_VERSIONS.json"
RUN_ID_HISTORY_NAME = "RUN_ID_HISTORY.json"
PARAM_GLOSSARY_NAME = "PARAM_GLOSSARY.json"
HASHED_V2_LAYOUT = "hashed-v2"
HASHED_V2_HEX = 32
SEGMENTS_LAYOUT = "segments-v2"
SEGMENTS_LAYOUTS = ("segments-v1", SEGMENTS_LAYOUT)
LEGACY_LAYOUTS = ("segments-v1", "nested", "collapsed-v1", "hashed-v1")
SAFE_RUN_ID_RE = re.compile(r"[A-Za-z0-9._-]+")
SAFE_RUN_ID_ITEM_RE = re.compile(r"[A-Za-z0-9._]+")


def _run_id_component(value) -> str:
    """Filesystem-safe, reversible rendering for a run_id key or value."""
    if isinstance(value, (dict, list, tuple)):
        value = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return quote(str(value), safe="-._~")


def _safe_run_id_component(value) -> str:
    """segments-v2 rendering: only ``A-Z a-z 0-9 . _ -``, never escaped.

    A scalar renders as ``str(value)``; a list renders its scalar items joined
    with ``-``. Anything else raises: the param belongs in a hash group.
    """
    def scalar(item):
        return isinstance(item, str) or not isinstance(item, (Mapping, Sequence, bytes))

    if scalar(value):
        if SAFE_RUN_ID_RE.fullmatch(str(value)):
            return str(value)
    elif isinstance(value, Sequence) and value and all(
        scalar(item) and SAFE_RUN_ID_ITEM_RE.fullmatch(str(item)) for item in value
    ):
        return "-".join(str(item) for item in value)
    raise ValueError(
        f"run_id value {value!r} has no safe path rendering (only A-Z a-z 0-9 . _ -; "
        "list items joined with '-' may not contain '-'); put its param in a hash group"
    )


def run_id_dict(cfg, params):
    """run_id as an ordered dict {param: value}."""
    return {p: cfg[p] for p in params}


def _run_id_pairs(cfg, params) -> list[str]:
    """Ordered, percent-encoded ``key=value`` pairs: the lossless identity form."""
    return [f"{_run_id_component(p)}={_run_id_component(cfg[p])}" for p in params]


def _safe_run_id_pairs(cfg, params) -> list[str]:
    """Ordered segments-v2 ``key=value`` pairs in the safe alphabet only."""
    return [f"{_safe_run_id_component(p)}={_safe_run_id_component(cfg[p])}" for p in params]


def _segment_items(segments):
    """Normalize a segment spec to [[("param", p) | ("group", name, params)]]."""
    normalized = []
    for segment in segments:
        items = []
        for item in segment:
            if isinstance(item, str):
                items.append(("param", item))
            elif isinstance(item, dict) and len(item) == 1:
                (name, group_params), = item.items()
                items.append(("group", name, tuple(group_params)))
            else:
                raise ValueError(
                    f"invalid run_id segment item {item!r}; expected a param name or "
                    "a one-key {group_name: (params, ...)} dict"
                )
        normalized.append(items)
    return normalized


def validate_run_id_segments(params, segments, *, layout=SEGMENTS_LAYOUT) -> None:
    """Hard-fail unless the segment spec renders RUN_ID_PARAMS losslessly.

    Every param appears exactly once, no unknown params, no empty segment or
    group, group names unique, filesystem-safe as-is, and never a param name.
    Order and placement are free: the user decides them at every election.
    """
    normalized = _segment_items(segments)
    if not normalized:
        raise ValueError("RUN_ID_SEGMENTS is empty")
    seen, groups = [], set()
    for items in normalized:
        if not items:
            raise ValueError("RUN_ID_SEGMENTS has an empty segment")
        for item in items:
            if item[0] == "param":
                seen.append(item[1])
                continue
            _, name, group_params = item
            if not group_params:
                raise ValueError(f"hash group {name!r} is empty")
            if name in groups or name in params:
                raise ValueError(
                    f"hash group name {name!r} is duplicated or shadows a param"
                )
            safe = (
                SAFE_RUN_ID_RE.fullmatch(name)
                if layout == SEGMENTS_LAYOUT
                else _run_id_component(name) == name
            )
            if not name or not safe:
                raise ValueError(
                    f"hash group name {name!r} must be non-empty and filesystem-safe"
                )
            groups.add(name)
            seen.extend(group_params)
    duplicated = sorted({p for p in seen if seen.count(p) > 1})
    missing = [p for p in params if p not in seen]
    unknown = sorted(set(seen) - set(params))
    if duplicated or missing or unknown:
        raise ValueError(
            "RUN_ID_SEGMENTS must place every RUN_ID_PARAMS entry exactly once: "
            f"duplicated={duplicated} missing={missing} unknown={unknown}"
        )


def run_id_group_hash(cfg, name, group_params, *, layout=SEGMENTS_LAYOUT) -> str:
    """16 hex of sha256 over the layout, the group name, and its ordered pairs.

    The hashed pairs are the lossless percent-encoded form under every layout,
    so a value with no safe rendering still hashes unambiguously.

    Only the group's own params enter the hash, so the same group values give
    the same hash in every run. Collisions are never assumed away: the
    per-directory record, RUN_ID_MAP.json, and check_run_id_plan() fail hard.
    """
    canonical = "\0".join(
        (layout, name, ",".join(_run_id_pairs(cfg, group_params)))
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:RUN_ID_HASH_HEX]


def run_id_groups(cfg, segments, *, layout=SEGMENTS_LAYOUT) -> dict:
    """{group_name: {"hash": ..., "params": {param: value}}} for one run."""
    return {
        item[1]: {
            "hash": run_id_group_hash(cfg, item[1], item[2], layout=layout),
            "params": run_id_dict(cfg, item[2]),
        }
        for items in _segment_items(segments)
        for item in items
        if item[0] == "group"
    }


def run_id_path(cfg, params, *, layout="nested", segments=None) -> Path:
    """Run-output path in the experiment's authoritative layout.

    ``hashed-v2`` (the layout for new experiments) renders one component
    ``rid-<32 hex>`` (run_id_hash_v2); ``params`` is the current entry of
    RUN_ID_VERSIONS.json (load_run_id_spec).

    Legacy ``segments-v2`` renders one directory
    per segment; items inside a segment are joined with ``,``; a param renders as
    its safe-alphabet ``key=value`` (lists joined with ``-``; anything else that
    needs escaping raises) and a hash group as ``name=<16 hex>``.

    Legacy pins, kept only for experiments that already recorded them:
    ``segments-v1`` renders the same structure with percent-encoded pairs;
    ``nested`` renders ``model=mlp/lr=0.001/seed=0``; ``collapsed-v1`` renders
    the same pairs in one component ``model=mlp,lr=0.001,seed=0``; ``hashed-v1``
    renders one component ``rid-<16 hex>`` (sha256 of the collapsed-v1 string).

    Use one layout consistently under checkpoints/ and evaluations/. plots/ does
    not use run_id paths at all. Never truncate, hash outside the recorded spec,
    or otherwise rewrite pairs to evade a collision or filesystem limit.
    """
    if layout in SEGMENTS_LAYOUTS:
        if segments is None:
            raise ValueError(f"{layout} requires the experiment's RUN_ID_SEGMENTS")
        validate_run_id_segments(params, segments, layout=layout)
        pairs = _safe_run_id_pairs if layout == SEGMENTS_LAYOUT else _run_id_pairs
        components = []
        for items in _segment_items(segments):
            rendered = []
            for item in items:
                if item[0] == "param":
                    rendered.extend(pairs(cfg, [item[1]]))
                else:
                    group_hash = run_id_group_hash(cfg, item[1], item[2], layout=layout)
                    rendered.append(f"{item[1]}={group_hash}")
            components.append(",".join(rendered))
        return Path(*components)
    if segments is not None:
        raise ValueError(f"RUN_ID_SEGMENTS only applies to segments-v2/v1, not {layout!r}")
    if layout == HASHED_V2_LAYOUT:
        return Path(run_id_hash_v2(cfg, params))
    pairs = _run_id_pairs(cfg, params)
    if layout == "nested":
        return Path(*pairs)
    if layout == "collapsed-v1":
        return Path(",".join(pairs))
    if layout == "hashed-v1":
        return Path(run_id_hash(cfg, params))
    raise ValueError(
        f"invalid run_id path layout {layout!r}; expected 'hashed-v2', "
        "'segments-v2', 'segments-v1', 'nested', 'collapsed-v1', or 'hashed-v1'"
    )


def run_id_name(cfg, params, *, layout="nested", segments=None) -> str:
    """The one string identity of a run.

    Under segments-v2 (and a legacy segments-v1 pin) it is the rendered run_id
    path in POSIX form, used for the EXPERIMENTS.md ``run_id`` column and the
    Slurm job-name suffix, so every surface matches the directories on disk.
    Under hashed-v2 it is the hash itself, while run_id_label is the readable
    label shown beside it. Other legacy layouts keep ``run_id_flat``.
    """
    if layout in SEGMENTS_LAYOUTS:
        return run_id_path(cfg, params, layout=layout, segments=segments).as_posix()
    if layout == HASHED_V2_LAYOUT:
        if segments is not None:
            raise ValueError("RUN_ID_SEGMENTS does not apply to hashed-v2")
        return run_id_hash_v2(cfg, params)
    return run_id_flat(cfg, params)


def run_id_hash(cfg, params) -> str:
    """The legacy hashed-v1 component: ``rid-`` + 16 hex of sha256(run_id_flat)."""
    digest = hashlib.sha256(run_id_flat(cfg, params).encode("utf-8")).hexdigest()
    return RUN_ID_HASH_PREFIX + digest[:RUN_ID_HASH_HEX]


def _canonical_json(payload) -> str:
    """The one canonical JSON form hashed-v2 hashes and indexes by."""
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    )


def run_id_params_v2(cfg, params) -> dict:
    """{param: value} for hashed-v2, read from resolved_config(cfg).

    Values are json-normalized exactly as .run_config.json stores them, so a
    hash computed from a stored snapshot equals the one computed at launch. A
    param missing from the config maps to None (hashed as null).
    """
    resolved = resolved_config(cfg)
    return {p: resolved.get(p) for p in params}


def run_id_hash_v2_from_values(values: Mapping) -> str:
    """``rid-`` + 32 hex of sha256 over the canonical hashed-v2 JSON of ``values``.

    Pure: ``values`` is a plain {param: value} dict (e.g. built from a stored
    .run_config.json). Key order never matters.
    """
    canonical = _canonical_json({"layout": HASHED_V2_LAYOUT, "params": dict(values)})
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return RUN_ID_HASH_PREFIX + digest[:HASHED_V2_HEX]


def run_id_hash_v2(cfg, params) -> str:
    """The hashed-v2 run_id (and directory name) of one run."""
    return run_id_hash_v2_from_values(run_id_params_v2(cfg, params))


def run_id_label(cfg, params) -> str:
    """Readable lossless ``k=v,...`` for a hashed-v2 run: a display label only.

    Never a path or an identity: the hash is the identity.
    """
    return run_id_flat(run_id_params_v2(cfg, params), params)


def _read_json_list(path: Path, what: str) -> list:
    if not path.is_file():
        raise FileNotFoundError(f"{path} is missing: the experiment has no {what}")
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError as error:
        raise ValueError(f"{path} is not valid JSON: {error}") from error
    if not isinstance(payload, list):
        raise ValueError(f"{path} must hold a JSON list of entries")
    return payload


def load_run_id_versions(spec_root: Path) -> list[dict]:
    """Every RUN_ID_VERSIONS entry, validated; the last one is current.

    ``spec_root`` is the tracked config/<exp>/ dir of the running revision
    (``source_path("config/<exp>")``). Hard-fails on a missing, empty, or
    malformed file, a non-increasing version, empty/duplicated params, or a
    dotted param (identity params are top-level config keys).
    """
    path = Path(spec_root) / RUN_ID_VERSIONS_NAME
    entries = _read_json_list(path, "run_id identity versions")
    if not entries:
        raise ValueError(f"{path} is empty: it needs at least one version entry")
    previous = None
    for position, entry in enumerate(entries):
        where = f"{path} entry {position}"
        if not isinstance(entry, dict):
            raise ValueError(f"{where} is not an object")
        missing = [k for k in ("version", "params", "date", "reason") if k not in entry]
        if missing:
            raise ValueError(f"{where} lacks {missing}")
        version, params = entry["version"], entry["params"]
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise ValueError(f"{where}: version must be a positive integer, not {version!r}")
        if previous is not None and version <= previous:
            raise ValueError(f"{where}: version {version} does not follow {previous}")
        previous = version
        if (
            not isinstance(params, list)
            or not params
            or not all(isinstance(p, str) and p for p in params)
        ):
            raise ValueError(f"{where}: params must be a non-empty list of param names")
        duplicated = sorted({p for p in params if params.count(p) > 1})
        if duplicated:
            raise ValueError(f"{where}: duplicated params {duplicated}")
        dotted = sorted(p for p in params if "." in p)
        if dotted:
            raise ValueError(
                f"{where}: params {dotted} are dotted; identity params are top-level "
                "config keys (elect the top-level subtree key instead)"
            )
    return entries


def load_run_id_spec(spec_root: Path) -> tuple[int, list[str]]:
    """(version, params) of the current hashed-v2 identity: the last RUN_ID_VERSIONS entry.

    ``spec_root`` is ``source_path("config/<exp>")``; see load_run_id_versions.
    """
    entries = load_run_id_versions(spec_root)
    return entries[-1]["version"], list(entries[-1]["params"])


def load_run_id_history(spec_root: Path) -> list[dict]:
    """Every RUN_ID_HISTORY entry under ``spec_root`` ([] before the first migration)."""
    path = Path(spec_root) / RUN_ID_HISTORY_NAME
    if not path.exists():
        return []
    entries = _read_json_list(path, "run_id history")
    for position, entry in enumerate(entries):
        mapping = entry.get("map") if isinstance(entry, dict) else None
        if not isinstance(mapping, dict):
            raise ValueError(f"{path} entry {position} has no 'map' object")
    return entries


def resolve_run_id_hash(spec_root: Path, run_hash: str) -> str:
    """The current hash of a run known by ``run_hash``, under any earlier version.

    Follows ``spec_root``/RUN_ID_HISTORY.json in order (a -> b -> c); a hash no
    migration renamed is returned unchanged. Exact hashes only: prefix lookup
    belongs to the CLI. No history file means no migration ever happened.
    """
    current = run_hash
    for entry in load_run_id_history(spec_root):
        current = entry["map"].get(current, current)
    return current


def load_param_glossary(spec_root: Path, params) -> dict[str, dict[str, str]]:
    """``{param: {"name", "explanation"}}`` for every param in ``params``.

    ``spec_root`` is ``source_path("config/<exp>")``. The agent writes every
    entry from the producer's config and code and reports it; plots show ``name`` instead of the raw key and ``explanation`` as its
    hover text. Hard-fails on a missing or malformed file, an entry without a
    non-empty name and explanation, or any of ``params`` lacking an entry (all
    named at once, so one write covers them).
    """
    path = Path(spec_root) / PARAM_GLOSSARY_NAME
    if not path.is_file():
        raise FileNotFoundError(
            f"{path} is missing: write a full name and explanation for {list(params)} "
            "from the producer's config and code, and report them"
        )
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError as error:
        raise ValueError(f"{path} is not valid JSON: {error}") from error
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must hold a JSON object keyed by param")
    for param, entry in payload.items():
        if not (
            isinstance(entry, dict)
            and all(isinstance(entry.get(k), str) and entry[k].strip() for k in ("name", "explanation"))
        ):
            raise ValueError(f"{path} entry {param!r} needs a non-empty 'name' and 'explanation'")
    missing = [p for p in params if p not in payload]
    if missing:
        raise ValueError(
            f"{path} has no entry for {missing}: write a full name and explanation for each "
            "from the producer's config and code, and report them"
        )
    return {p: {"name": payload[p]["name"], "explanation": payload[p]["explanation"]} for p in params}


def _require_spec_root(spec_root, what: str) -> Path:
    if spec_root is None:
        raise ValueError(
            f"hashed-v2 {what} requires spec_root=source_path('config/<exp>'), the tracked "
            "dir holding RUN_ID_VERSIONS.json"
        )
    return Path(spec_root)


def _check_spec_params(spec_root, params, version) -> int:
    """The current version; refuse params that are not the current RUN_ID_VERSIONS entry."""
    current, spec_params = load_run_id_spec(spec_root)
    if sorted(params) != sorted(spec_params):
        raise RuntimeError(
            f"run_id params {sorted(params)} are not the current "
            f"{RUN_ID_VERSIONS_NAME} v{current} params {sorted(spec_params)}"
        )
    if version is not None and version != current:
        raise RuntimeError(
            f"run_id version {version} is not the current {RUN_ID_VERSIONS_NAME} v{current}"
        )
    return current


def _segments_json(segments):
    return [
        [item[1] if item[0] == "param" else {item[1]: list(item[2])} for item in items]
        for items in _segment_items(segments)
    ]


def run_id_record(cfg, params, *, layout="hashed-v1", segments=None, version=None) -> dict:
    """The .run_id.json payload: the full two-way mapping for one run.

    hashed-v2 needs the identity ``version`` (load_run_id_spec) and records
    ``{layout, version, hash, params: {param: value}}``.
    """
    if layout == HASHED_V2_LAYOUT:
        if not isinstance(version, int) or isinstance(version, bool):
            raise ValueError("a hashed-v2 record needs the integer identity version")
        values = run_id_params_v2(cfg, params)
        return {
            "layout": HASHED_V2_LAYOUT,
            "version": version,
            "hash": run_id_hash_v2_from_values(values),
            "params": values,
        }
    if layout in SEGMENTS_LAYOUTS:
        return {
            "layout": layout,
            "segments": _segments_json(segments),
            "path": run_id_name(cfg, params, layout=layout, segments=segments),
            "run_id_flat": run_id_flat(cfg, params),
            "run_id": run_id_dict(cfg, params),
            "groups": run_id_groups(cfg, segments, layout=layout),
        }
    return {
        "layout": "hashed-v1",
        "hash": run_id_hash(cfg, params),
        "run_id_flat": run_id_flat(cfg, params),
        "run_id": run_id_dict(cfg, params),
    }


def _merge_segment_record(index, record, where) -> None:
    """Fold one segments record into a map index; raise on any collision."""
    known_flat = index["by_path"].get(record["path"])
    if known_flat is not None and known_flat != record["run_id_flat"]:
        raise RuntimeError(
            f"run_id path collision at {where}: {record['path']!r} maps to both "
            f"{known_flat!r} and {record['run_id_flat']!r}"
        )
    index["by_path"][record["path"]] = record["run_id_flat"]
    index["by_run_id_flat"][record["run_id_flat"]] = record["path"]
    for name, group in record["groups"].items():
        hashes = index["groups"].setdefault(name, {})
        known = hashes.get(group["hash"])
        if known is not None and known != group["params"]:
            raise RuntimeError(
                f"group hash collision at {where}: {name}={group['hash']} maps to both "
                f"{known!r} and {group['params']!r}. Stop; never reuse or rename the "
                "directory. Re-elect the segments in a new numbered sub-experiment."
            )
        hashes[group["hash"]] = group["params"]


def _merge_hashed_v2_record(index, record, where, *, status=None) -> None:
    """Fold one hashed-v2 record into a map index; raise on any collision."""
    run_hash, params = record["hash"], record["params"]
    known = index["by_hash"].get(run_hash)
    if known is not None and known["params"] != params:
        raise RuntimeError(
            f"run_id hash collision at {where}: {run_hash} maps to both "
            f"{known['params']!r} and {params!r}. Stop; never reuse or rename the "
            "directory. Propose the next run_id version and a run_id migration."
        )
    key = _canonical_json(params)
    claimed = index["by_params"].get(key)
    if claimed is not None and claimed != run_hash:
        raise RuntimeError(
            f"run_id params {params!r} at {where} are claimed by both {claimed} and {run_hash}"
        )
    status = status or {}
    index["by_hash"][run_hash] = {
        "params": params,
        "version": record["version"],
        "wave_id": status.get("wave_id"),
        "state": status.get("state"),
        "rig": status.get("rig"),
    }
    index["by_params"][key] = run_hash


def _read_optional_json(path: Path):
    """A display-only sidecar (.status.json, .run_config.json): None if absent or torn."""
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


def _write_json_atomic(path: Path, payload, **dumps_kwargs) -> None:
    """Write JSON so concurrent readers see the old file or the new one, never a torn one.

    The tmp name is hidden (never matches a ``.run_id.json`` glob) and unique
    across hosts sharing one filesystem, not just across local pids.
    """
    path = Path(path)
    tmp = path.with_name(
        f".{path.name}.tmp.{socket.gethostname()}.{os.getpid()}.{uuid.uuid4().hex[:8]}"
    )
    try:
        tmp.write_text(json.dumps(payload, **dumps_kwargs))
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def build_run_id_index(experiment_root: Path, *, layout="hashed-v1", spec_root=None) -> dict:
    """Rebuild the RUN_ID_MAP payload from every run's .run_id.json, in memory.

    O(runs) reads: call it once per wave (write_run_id_map, check_run_id_plan),
    never once per run. hashed-v2 (requires spec_root, the tracked config/<exp>/
    dir): ``{layout, version, versions, history, by_hash: {hash: {params,
    version, wave_id, state, rig}}, by_params: {canonical params JSON: hash},
    configs: {hash: .run_config.json content or None}}``, with wave_id/state/rig
    read from each run's .status.json (None when absent) and ``versions`` /
    ``history`` copies of the identity files, so a viewer needs only the map; a
    record whose directory, layout, or hash does not match its params raises. Legacy
    hashed-v1: ``by_hash`` maps hash -> {run_id_flat, run_id};
    ``by_run_id_flat`` maps flat -> hash. segments-v2/v1: ``by_path`` maps
    rendered path -> run_id_flat, ``by_run_id_flat`` the reverse, and ``groups``
    maps group -> hash -> params. Under every layout a hash or path claimed by
    two different identities raises.
    """
    experiment_root = Path(experiment_root)
    if layout == HASHED_V2_LAYOUT:
        spec_root = _require_spec_root(spec_root, "map")
        versions = load_run_id_versions(spec_root)
        payload = {
            "layout": HASHED_V2_LAYOUT,
            "version": versions[-1]["version"],
            "versions": versions,
            "history": load_run_id_history(spec_root),
            "by_hash": {},
            "by_params": {},
            "configs": {},
        }
        for record_file in sorted(experiment_root.glob("*/.run_id.json")):
            record = json.loads(record_file.read_text())
            run_dir = record_file.parent
            if record.get("layout") != HASHED_V2_LAYOUT or record.get("hash") != run_dir.name:
                raise RuntimeError(
                    f"{record_file} does not match hashed-v2 path {run_dir.name!r}; mixed or "
                    "moved run directories block the map"
                )
            if run_id_hash_v2_from_values(record["params"]) != record["hash"]:
                raise RuntimeError(
                    f"{record_file}: hash {record['hash']} does not match its params "
                    f"{record['params']!r}; never hand-edit a run_id record"
                )
            status = _read_optional_json(run_dir / ".status.json")
            _merge_hashed_v2_record(
                payload, record, record_file, status=status if isinstance(status, dict) else None
            )
            payload["configs"][record["hash"]] = _read_optional_json(run_dir / ".run_config.json")
        return payload
    if layout in SEGMENTS_LAYOUTS:
        payload = {"layout": layout, "by_path": {}, "by_run_id_flat": {}, "groups": {}}
        for record_file in sorted(experiment_root.glob("**/.run_id.json")):
            record = json.loads(record_file.read_text())
            rel = record_file.parent.relative_to(experiment_root).as_posix()
            if record.get("layout") != layout or record["path"] != rel:
                raise RuntimeError(
                    f"{record_file} does not match {layout} path {rel!r}; mixed or "
                    "moved run directories block the map"
                )
            _merge_segment_record(payload, record, record_file)
    else:
        by_hash, by_flat = {}, {}
        for record_file in sorted(experiment_root.glob("*/.run_id.json")):
            record = json.loads(record_file.read_text())
            known = by_hash.get(record["hash"])
            if known is not None and known["run_id_flat"] != record["run_id_flat"]:
                raise RuntimeError(
                    f"hashed-v1 hash collision at {record_file}: {record['hash']} maps to "
                    f"both {known['run_id_flat']!r} and {record['run_id_flat']!r}. Stop; "
                    "never reuse or rename the directory."
                )
            by_hash[record["hash"]] = {
                "run_id_flat": record["run_id_flat"],
                "run_id": record["run_id"],
            }
            by_flat[record["run_id_flat"]] = record["hash"]
        payload = {"layout": "hashed-v1", "by_hash": by_hash, "by_run_id_flat": by_flat}
    return payload


def write_run_id_map(experiment_root: Path, *, layout="hashed-v1", spec_root=None) -> Path:
    """Regenerate <experiment_root>/RUN_ID_MAP.json atomically, once per wave.

    Derived index only, safe to regenerate on any rig; hard-fails on any
    collision (build_run_id_index). Never call it per run: guard_run_config does
    not, and the wave's end rebuilds it once. hashed-v2 requires spec_root
    (``source_path("config/<exp>")``) for the version and the embedded
    versions/history copies.
    """
    map_file = Path(experiment_root) / RUN_ID_MAP_NAME
    payload = build_run_id_index(experiment_root, layout=layout, spec_root=spec_root)
    map_file.parent.mkdir(parents=True, exist_ok=True)
    _write_json_atomic(map_file, payload, indent=2, sort_keys=True)
    return map_file


def _path_limit(root: Path, name: str, fallback: int) -> int:
    probe = Path(root).absolute()
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        return os.pathconf(probe, name)
    except (OSError, ValueError, AttributeError):
        return fallback


def preflight_run_id_path(root: Path, rel_path: Path) -> dict:
    """Byte report for <root>/<rel_path>; raise if any filesystem limit overflows.

    Returns {"components": [(component, bytes), ...], "total_bytes": n,
    "name_max": ..., "path_max": ...}. The election shows this report for the
    all-explicit path first and again for every proposed rendering.
    """
    name_max = _path_limit(root, "PC_NAME_MAX", 255)
    path_max = _path_limit(root, "PC_PATH_MAX", 4096)
    components = [(part, len(part.encode("utf-8"))) for part in Path(rel_path).parts]
    total = len(str(Path(root).absolute() / rel_path).encode("utf-8"))
    report = {
        "components": components,
        "total_bytes": total,
        "name_max": name_max,
        "path_max": path_max,
    }
    over = [part for part, size in components if size > name_max]
    if over or total >= path_max:
        raise ValueError(
            f"run_id path overflows under {root}: components over {name_max} bytes: "
            f"{over}; full path {total} bytes (limit {path_max}). Re-elect the segments."
        )
    return report


def _config_diffs(old: Mapping, new: Mapping) -> dict:
    """{key: (old, new)} for every top-level key whose value differs or is missing."""
    missing = "<MISSING>"
    return {
        k: (old[k] if k in old else missing, new[k] if k in new else missing)
        for k in sorted(set(old) | set(new))
        if k not in old or k not in new or old[k] != new[k]
    }


def check_run_id_plan(
    cfgs,
    params,
    segments=None,
    experiment_root: Path = None,
    *,
    path_roots=(),
    layout=SEGMENTS_LAYOUT,
    spec_root=None,
) -> list[Path]:
    """Pre-dispatch gate for a hashed-v2 (or legacy segments-v2/v1) wave; returns the planned paths.

    hashed-v2 takes no segments (pass None), requires spec_root
    (``source_path("config/<exp>")``), and refuses params that are not the
    current RUN_ID_VERSIONS.json entry. Under segments-v2 a value with no safe
    rendering raises here, before any dispatch.

    Preflights every planned path under experiment_root and each extra root
    (e.g. the experiment's checkpoints/ dir), and fails on any hash or path
    collision within the plan or against every existing run's .run_id.json
    (rescanned here, so a stale RUN_ID_MAP.json never hides a collision), and on
    a run planned twice. A planned run whose path already holds a
    .run_config.json is allowed only when that config equals its
    resolved_config (resume/retry); any difference refuses, naming the keys.
    """
    if experiment_root is None:
        raise ValueError("check_run_id_plan requires experiment_root")
    experiment_root = Path(experiment_root)
    if layout == HASHED_V2_LAYOUT:
        if segments is not None:
            raise ValueError("RUN_ID_SEGMENTS does not apply to hashed-v2")
        spec_root = _require_spec_root(spec_root, "check_run_id_plan")
        version = _check_spec_params(spec_root, params, None)
        remedy = (
            "Propose the next run_id version in RUN_ID_VERSIONS.json plus a run_id "
            "migration; never reuse the directory."
        )
    elif layout in SEGMENTS_LAYOUTS:
        validate_run_id_segments(params, segments, layout=layout)
        version = None
        remedy = (
            "Re-elect the identifying params in a new numbered sub-experiment; never "
            "reuse the directory."
        )
    else:
        raise ValueError(
            f"check_run_id_plan needs hashed-v2 or a segments layout, not {layout!r}"
        )
    index = build_run_id_index(experiment_root, layout=layout, spec_root=spec_root)
    planned, paths = set(), []
    for cfg in cfgs:
        record = run_id_record(cfg, params, layout=layout, segments=segments, version=version)
        if layout == HASHED_V2_LAYOUT:
            identity, rel = record["hash"], Path(record["hash"])
            if identity in planned:
                raise RuntimeError(f"run {identity} ({run_id_label(cfg, params)}) is planned twice")
            _merge_hashed_v2_record(index, record, "the planned wave")
        else:
            identity, rel = record["run_id_flat"], Path(record["path"])
            if identity in planned:
                raise RuntimeError(f"run {identity!r} is planned twice")
            _merge_segment_record(index, record, "the planned wave")
        planned.add(identity)
        for root in (experiment_root, *path_roots):
            preflight_run_id_path(root, rel)
        snapshot_file = experiment_root / rel / ".run_config.json"
        if snapshot_file.exists():
            diffs = _config_diffs(json.loads(snapshot_file.read_text()), resolved_config(cfg))
            if diffs:
                raise RuntimeError(
                    f"planned run {rel.as_posix()} already exists with a different config; "
                    f"differing keys: {sorted(diffs)}. The identifying params do not "
                    f"capture what this wave varies. {remedy}"
                )
        paths.append(rel)
    return paths


def run_id_flat(cfg, params) -> str:
    """Flat form, e.g. 'model=mlp,lr=0.001,seed=0'.

    The complete, lossless identity string: recorded in .run_id.json and
    RUN_ID_MAP.json under every layout. Legacy non-segment layouts also use it
    for scripts/ and logs/ run-folder names and EXPERIMENTS.md
    rows; segments layouts use run_id_path()/run_id_name() there instead.
    """
    return ",".join(_run_id_pairs(cfg, params))


def hydra_override_arg(param, value) -> str:
    """One shell-safe `param=value` token for generated wave scripts."""
    return shlex.quote(f"{param}={value}")


def resolved_config(cfg) -> dict:
    """The run's ENTIRE config, resolved and json-normalized.

    Single source of truth for "the full config of this run", wherever it is
    recorded: the .run_config.json snapshot is built from this. Do not resolve
    a config anywhere else; do not record a hand-picked subset anywhere.
    """
    try:
        from omegaconf import OmegaConf
    except ImportError:  # plain-dict configs (tests, tooling) need no hydra
        OmegaConf = None

    # json round-trip so every consumer sees exactly what a stored snapshot stores
    raw = (
        OmegaConf.to_container(cfg, resolve=True)
        if OmegaConf is not None and OmegaConf.is_config(cfg)
        else cfg
    )
    return json.loads(json.dumps(raw, sort_keys=True, default=str))


def guard_run_config(
    cfg,
    params,
    run_dir: Path,
    *,
    layout="nested",
    segments=None,
    experiment_root=None,
    version=None,
    spec_root=None,
) -> None:
    """Refuse to reuse a run dir whose config differs from the current one.

    Writes the full resolved config to <run_dir>/.run_config.json on first run.
    On later runs with the same run_id, hard-fails if ANY param differs — that is
    a run_id collision: a param changed that is not in RUN_ID_PARAMS. Same-config
    reruns (resume/retry) pass. Call BEFORE writing any artifact.

    Under ``hashed-v2`` (requires experiment_root and spec_root, the tracked
    ``source_path("config/<exp>")`` of the running revision, so a rig needs no
    identity file in its evaluations/) it refuses params that are not the
    current RUN_ID_VERSIONS.json entry (one small read; ``version`` defaults to
    that entry), asserts that run_dir is experiment_root /
    run_id_hash_v2(...), hard-fails if an existing record there names different
    params, and writes <run_dir>/.run_id.json.

    Under ``segments-v2`` or a legacy ``segments-v1`` pin (requires segments
    and experiment_root) it asserts that
    run_dir is experiment_root / run_id_path(...), hard-fails if an existing
    record there names a different run_id_flat, and writes
    <run_dir>/.run_id.json. Legacy
    ``hashed-v1`` keeps its per-directory record check.

    Per-run I/O is O(1) and every write is atomic, so thousands of concurrent
    runs on a shared filesystem stay cheap and never read a torn file. The guard
    never reads or writes RUN_ID_MAP.json or scans other runs: cross-run
    group-hash collisions are caught by check_run_id_plan before dispatch (a
    rescan of every record) and by write_run_id_map, which rebuilds the map once
    per wave.
    """
    resolved = resolved_config(cfg)
    if layout == HASHED_V2_LAYOUT:
        if experiment_root is None:
            raise ValueError("hashed-v2 guard requires experiment_root")
        spec_root = _require_spec_root(spec_root, "guard")
        version = _check_spec_params(spec_root, params, version)
        record = run_id_record(cfg, params, layout=layout, version=version)
        expected = Path(experiment_root) / record["hash"]
        if Path(run_dir) != expected:
            raise RuntimeError(f"run dir {run_dir} is not the hashed-v2 path {expected}")
        record_file = expected / ".run_id.json"
        if record_file.exists():
            existing = json.loads(record_file.read_text())
            if existing.get("params") != record["params"]:
                raise RuntimeError(
                    f"hashed-v2 hash collision at {run_dir}: {record['hash']} already maps "
                    f"to {existing.get('params')!r}, not {record['params']!r}. Stop; propose "
                    "the next run_id version and a run_id migration."
                )
        else:
            expected.mkdir(parents=True, exist_ok=True)
            _write_json_atomic(record_file, record, indent=2, sort_keys=True)
    elif layout in SEGMENTS_LAYOUTS:
        if experiment_root is None:
            raise ValueError(f"{layout} guard requires experiment_root")
        expected = Path(experiment_root) / run_id_path(
            cfg, params, layout=layout, segments=segments
        )
        if Path(run_dir) != expected:
            raise RuntimeError(f"run dir {run_dir} is not the {layout} path {expected}")
        record = run_id_record(cfg, params, layout=layout, segments=segments)
        record_file = expected / ".run_id.json"
        if record_file.exists():
            existing = json.loads(record_file.read_text())
            if existing["run_id_flat"] != record["run_id_flat"]:
                raise RuntimeError(
                    f"{layout} hash collision at {run_dir}: {record['path']} already maps "
                    f"to {existing['run_id_flat']!r}, not {record['run_id_flat']!r}. Stop and "
                    "re-elect the segments in a new numbered sub-experiment."
                )
        else:
            expected.mkdir(parents=True, exist_ok=True)
            _write_json_atomic(record_file, record, indent=2, sort_keys=True)
    elif layout == "hashed-v1":
        record = run_id_record(cfg, params)
        record_file = run_dir / ".run_id.json"
        if record_file.exists():
            existing = json.loads(record_file.read_text())
            if existing["run_id_flat"] != record["run_id_flat"]:
                raise RuntimeError(
                    f"hashed-v1 hash collision at {run_dir}: {record['hash']} already maps to "
                    f"{existing['run_id_flat']!r}, not {record['run_id_flat']!r}. Stop and "
                    "re-elect the layout or params in a new numbered sub-experiment."
                )
        else:
            run_dir.mkdir(parents=True, exist_ok=True)
            _write_json_atomic(record_file, record, indent=2, sort_keys=True)
    snapshot_file = run_dir / ".run_config.json"
    if snapshot_file.exists():
        snapshot = json.loads(snapshot_file.read_text())
        diffs = _config_diffs(snapshot, resolved)
        if diffs:
            lines = "\n".join(f"  {k}: old={old!r} new={new!r}" for k, (old, new) in diffs.items())
            if layout == HASHED_V2_LAYOUT:
                raise RuntimeError(
                    f"run_id collision at {run_dir}: same run_id "
                    f"({run_id_label(cfg, params)}) but the config differs:\n{lines}\n"
                    "Propose the next run_id version in RUN_ID_VERSIONS.json plus a run_id "
                    "migration. See conventions.md, 'run_id schema evolution'."
                )
            raise RuntimeError(
                f"run_id collision at {run_dir}: same run_id "
                f"({run_id_flat(cfg, params)}) but the config differs:\n{lines}\n"
                "Re-elect RUN_ID_PARAMS only before any output or wave record exists; "
                "otherwise create a new numbered sub-experiment. See conventions.md, "
                "'run_id schema evolution'."
            )
    else:
        run_dir.mkdir(parents=True, exist_ok=True)
        _write_json_atomic(snapshot_file, resolved, indent=2, sort_keys=True, default=str)
