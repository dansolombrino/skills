#!/usr/bin/env python3
"""run-identity: look up hashed-v2 run_ids and migrate an experiment's identity to a new version.

`lookup` resolves a hash or a unique prefix (following RUN_ID_HISTORY.json from an old hash to the
current one) or lists the hashes whose params match `--where k=v`. Exit 0 found, 1 nothing matched,
2 an error (ambiguous prefix, bad input, refused operation).

The identity files RUN_ID_VERSIONS.json and RUN_ID_HISTORY.json are tracked source:
`config/<exp>/` of the hub checkout. A migration updates them there only (its commit carries
them); rigs pick the new revision up with the next wave deploy. RUN_ID_MAP.json is derived and
lives in `evaluations/<exp>/` on every machine, embedding copies of both identity files. `migrate plan` computes the
next identity version for an experiment and inventories every machine that holds its runs;
`migrate apply` executes an approved plan with a rollback manifest written first; `migrate
rollback` reverses an applied migration from that manifest.

Every machine, the hub included, is worked on by `runid_agent.py`, piped over stdin to
`python3 -` through rig-sync's transport, so a rig needs nothing installed.
"""

from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import os
import re
import subprocess
import sys
import tomllib
from datetime import datetime
from pathlib import Path, PurePosixPath


def _load_sibling(skill: str, script: str, alias: str):
    path = Path(__file__).resolve().parents[2] / skill / "scripts" / script
    return _load_path(path, alias)


def _load_path(path: Path, alias: str):
    spec = importlib.util.spec_from_file_location(alias, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load helper: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


AGENT_PATH = Path(__file__).resolve().parent / "runid_agent.py"
agent = _load_path(AGENT_PATH, "run_identity_agent")
rigsync = _load_sibling("rig-sync", "rigsync.py", "run_identity_rigsync")
board = _load_sibling("rig-board", "board.py", "run_identity_board")
RIGSYNC_SCRIPT = Path(rigsync.__file__)


class RunIdError(RuntimeError):
    pass


PLAN_SCHEMA = "run-identity-migration/1"
ROLLBACK_SCHEMA = "run-identity-rollback/1"
AREAS = ("checkpoints", "evaluations", "scripts", "logs")
PROJECT_FILES = ("EXPERIMENTS.md", "JOURNAL.md")
DEFAULT_REGISTRY = "~/.config/rigsync/machines.toml"
EXPERIMENT_RE = re.compile(r"^\d{3}_[^/]+(?:/\d{3}_[^/]+)*$")
PARAM_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")  # top-level config keys only: no dots
ABSENT = "absent"


def now() -> datetime:
    return datetime.now().astimezone()


def read_json(path: Path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError:
        return default
    except (OSError, ValueError) as exc:
        raise RunIdError(f"{path}: unreadable JSON: {exc}") from exc


def dumps_list(entries: list) -> bytes:
    return (json.dumps(entries, indent=2, ensure_ascii=False) + "\n").encode()


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


# ───────────────────────────── context ─────────────────────────────


class Context:
    def __init__(self, args: argparse.Namespace) -> None:
        self.project = Path(args.project).resolve()
        self.registry = Path(
            args.registry or os.environ.get("RIGSYNC_REGISTRY", DEFAULT_REGISTRY)
        ).expanduser().resolve()
        self.config_path = Path(args.config).resolve() if args.config else self.project / "sync.toml"
        self.experiment = args.experiment
        if self.experiment is not None:
            if not EXPERIMENT_RE.fullmatch(self.experiment) or ".." in PurePosixPath(self.experiment).parts:
                raise RunIdError(f"--experiment must be an experiment path such as 000_name; got {self.experiment!r}")
        self._config = None

    @property
    def exp_root(self) -> Path:
        if self.experiment is None:
            raise RunIdError("--experiment is required here")
        return self.project / "evaluations" / self.experiment

    @property
    def spec_root(self) -> Path:
        """The tracked config/<exp>/ dir holding RUN_ID_VERSIONS.json and RUN_ID_HISTORY.json."""
        if self.experiment is None:
            raise RunIdError("--experiment is required here")
        return self.project / "config" / self.experiment

    def environment_python(self) -> str:
        """`<[environment].name>/bin/python` from sync.toml, or a placeholder when unreadable."""
        try:
            with open(self.config_path, "rb") as handle:
                name = (tomllib.load(handle).get("environment") or {}).get("name")
        except (OSError, tomllib.TOMLDecodeError):
            name = None
        return f"{name}/bin/python" if isinstance(name, str) and name else "<environment.name>/bin/python"

    def config(self):
        if self._config is None:
            try:
                self._config = rigsync.load_config(self.project, self.config_path, self.registry)
            except rigsync.RigSyncError as exc:
                raise RunIdError(f"cannot load the machine configuration: {exc}") from exc
        return self._config

    def hub(self):
        try:
            return rigsync.hub_machine(self.config())
        except rigsync.RigSyncError as exc:
            raise RunIdError(str(exc)) from exc


# ───────────────────────────── identity files ─────────────────────────────


def load_versions(spec_root: Path) -> list[dict]:
    """RUN_ID_VERSIONS.json of the tracked config/<exp>/ dir."""
    path = spec_root / agent.VERSIONS_NAME
    entries = read_json(path)
    if entries is None:
        raise RunIdError(
            f"{path} is missing: this experiment is not on the hashed-v2 layout. run_id migration "
            "supports hashed-v2 only; a legacy-pinned experiment keeps its rules"
        )
    if not isinstance(entries, list) or not entries:
        raise RunIdError(f"{path} must be a non-empty JSON list")
    previous = 0
    for position, entry in enumerate(entries):
        if not isinstance(entry, dict) or not isinstance(entry.get("version"), int) or not isinstance(entry.get("params"), list):
            raise RunIdError(f"{path} entry {position} is malformed")
        if entry["version"] <= previous:
            raise RunIdError(f"{path} entry {position}: version {entry['version']} does not follow {previous}")
        previous = entry["version"]
    return entries


def load_history(spec_root: Path) -> list[dict]:
    """RUN_ID_HISTORY.json of the tracked config/<exp>/ dir ([] before the first migration)."""
    path = spec_root / agent.HISTORY_NAME
    entries = read_json(path, [])
    if not isinstance(entries, list) or not all(isinstance(e, dict) and isinstance(e.get("map"), dict) for e in entries):
        raise RunIdError(f"{path} must be a JSON list of entries with a 'map' object")
    return entries


def resolve_chain(history: list[dict], run_hash: str) -> list[tuple[str, int | None]]:
    """[(hash, version it was renamed into)], applying every history entry in order (template rule)."""
    chain = [(run_hash, None)]
    current = run_hash
    for entry in history:
        renamed = entry["map"].get(current)
        if renamed is not None and renamed != current:
            current = renamed
            chain.append((current, entry.get("to_version")))
    return chain


def hub_runs(exp_root: Path) -> dict[str, dict]:
    """Every run record on the hub: hash -> {record, config, status}. Refuses a non-hashed-v2 record."""
    runs: dict[str, dict] = {}
    for record_file in sorted(exp_root.glob("*/.run_id.json")):
        record = read_json(record_file)
        run_dir = record_file.parent
        if not isinstance(record, dict) or record.get("layout") != agent.HASHED_V2:
            raise RunIdError(f"{record_file} is not a hashed-v2 record: run_id migration supports hashed-v2 only")
        if record.get("hash") != run_dir.name or agent.hash_from_values(record.get("params") or {}) != record["hash"]:
            raise RunIdError(f"{record_file}: its hash does not match its directory or params; settle that first")
        config = read_json(run_dir / ".run_config.json")
        status = read_json(run_dir / ".status.json")
        runs[record["hash"]] = {
            "record": record,
            "config": config if isinstance(config, dict) else None,
            "status": status if isinstance(status, dict) else {},
        }
    return runs


# ───────────────────────────── lookup ─────────────────────────────


def find_experiments(project: Path) -> list[str]:
    """evaluations/ dirs holding RUN_ID_MAP.json, united with config/ dirs holding RUN_ID_VERSIONS.json."""
    found: set[str] = set()
    for area, marker in (("evaluations", agent.MAP_NAME), ("config", agent.VERSIONS_NAME)):
        base = project / area
        for directory, dirs, files in os.walk(base):
            dirs[:] = sorted(d for d in dirs if not agent.RUN_DIR_RE.match(d) and d != agent.MIGRATIONS_DIR)
            if marker in files:
                rel = Path(directory).relative_to(base).as_posix()
                if EXPERIMENT_RE.fullmatch(rel):
                    found.add(rel)
    return sorted(found)


def experiment_history(project: Path, exp: str, index_payload: dict | None = None) -> list[dict]:
    """config/<exp>/RUN_ID_HISTORY.json; without that file, the copy embedded in RUN_ID_MAP.json."""
    spec_root = project / "config" / exp
    if (spec_root / agent.HISTORY_NAME).is_file() or (spec_root / agent.VERSIONS_NAME).is_file():
        return load_history(spec_root)
    if index_payload is None:
        index_payload = read_json(project / "evaluations" / exp / agent.MAP_NAME, {}) or {}
    embedded = index_payload.get("history") or []
    if not isinstance(embedded, list) or not all(isinstance(e, dict) and isinstance(e.get("map"), dict) for e in embedded):
        raise RunIdError(f"{project / 'evaluations' / exp / agent.MAP_NAME}: its embedded history is malformed")
    return embedded


def experiment_index(exp_root: Path) -> dict:
    """by_hash and configs: RUN_ID_MAP.json, completed by any run dir the map does not list."""
    index = read_json(exp_root / agent.MAP_NAME, {}) or {}
    by_hash = dict(index.get("by_hash") or {})
    configs = dict(index.get("configs") or {})
    for record_file in sorted(exp_root.glob("*/.run_id.json")):
        name = record_file.parent.name
        if name in by_hash:
            continue
        record = read_json(record_file, {}) or {}
        status = read_json(record_file.parent / ".status.json", {}) or {}
        by_hash[name] = {
            "params": record.get("params"),
            "version": record.get("version"),
            "wave_id": status.get("wave_id") if isinstance(status, dict) else None,
            "state": status.get("state") if isinstance(status, dict) else None,
            "rig": status.get("rig") if isinstance(status, dict) else None,
        }
        configs[name] = read_json(record_file.parent / ".run_config.json")
    return {"by_hash": by_hash, "configs": configs}


def parse_cli_value(raw: str):
    """A `--where` value: JSON when it parses (1e-4, true, null, [64,128]), else the bare string."""
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def value_matches(stored, raw: str) -> bool:
    parsed = parse_cli_value(raw)
    if _is_number(stored) and _is_number(parsed):
        return float(stored) == float(parsed)
    if agent.canonical_json(stored) == agent.canonical_json(parsed):
        return True
    return isinstance(stored, str) and stored == raw


def _lookup_key(entry: dict, config, key: str):
    params = entry.get("params") or {}
    if key in params:
        return True, params[key]
    node = config
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return False, None
        node = node[part]
    return True, node


def lookup(ctx: Context, token: str | None, where: list[str], as_json: bool) -> int:
    experiments = [ctx.experiment] if ctx.experiment else find_experiments(ctx.project)
    if not experiments:
        raise RunIdError(f"no hashed-v2 experiment under {ctx.project / 'config'} or {ctx.project / 'evaluations'}")
    if where:
        conditions = []
        for item in where:
            key, sep, value = item.partition("=")
            if not sep or not key:
                raise RunIdError(f"--where takes key=value; got {item!r}")
            conditions.append((key, value))
        rows = []
        for exp in experiments:
            index = experiment_index(ctx.project / "evaluations" / exp)
            for run_hash, entry in sorted(index["by_hash"].items()):
                config = index["configs"].get(run_hash)
                if all((hit := _lookup_key(entry, config, k))[0] and value_matches(hit[1], v) for k, v in conditions):
                    rows.append({"experiment": exp, "hash": run_hash, **entry})
        if as_json:
            print(json.dumps(rows, indent=2, sort_keys=True))
        else:
            for row in rows:
                params = ",".join(f"{k}={json.dumps(v)}" for k, v in (row.get("params") or {}).items())
                print(f"{row['hash']}  v{row.get('version')}  {row['experiment']}  {params}  wave={row.get('wave_id')} state={row.get('state')} rig={row.get('rig')}")
            print(f"{len(rows)} run(s) match")
        return 0 if rows else 1
    if not token:
        raise RunIdError("lookup needs a hash, a hash prefix, or --where k=v")
    needle = token if token.startswith(agent.HASH_PREFIX) else agent.HASH_PREFIX + token
    known: set[tuple[str, str]] = set()
    contexts = {}
    for exp in experiments:
        exp_root = ctx.project / "evaluations" / exp
        history = experiment_history(ctx.project, exp)
        index = experiment_index(exp_root)
        contexts[exp] = (history, index)
        hashes = set(index["by_hash"])
        for entry in history:
            hashes.update(entry["map"].keys())
            hashes.update(entry["map"].values())
        known.update((exp, run_hash) for run_hash in hashes)
    exact = sorted(key for key in known if key[1] == needle)
    matches = exact or sorted(key for key in known if key[1].startswith(needle))
    if not matches:
        print(f"runid: no run_id matches {token!r} in {', '.join(experiments)}", file=sys.stderr)
        return 1
    if len(matches) > 1:
        listing = "\n".join(f"  {exp}: {h}" for exp, h in matches[:20])
        raise RunIdError(f"{token!r} is ambiguous ({len(matches)} matches); give more characters:\n{listing}")
    exp, run_hash = matches[0]
    history, index = contexts[exp]
    chain = resolve_chain(history, run_hash)
    current = chain[-1][0]
    entry = index["by_hash"].get(current)
    result = {
        "experiment": exp,
        "query": token,
        "hash": current,
        "resolved_from": run_hash if run_hash != current else None,
        "chain": [h for h, _ in chain],
        "found": entry is not None,
        **(entry or {}),
    }
    if as_json:
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if entry else 1
    if result["resolved_from"]:
        print(f"{run_hash} -> {current}  (renamed by run_id migration: {' -> '.join(result['chain'])})")
    print(f"hash:       {current}")
    print(f"experiment: {exp}")
    if entry is None:
        print("record:     none on this machine (known only from RUN_ID_HISTORY.json)")
        return 1
    print(f"version:    v{entry.get('version')}")
    print(f"wave:       {entry.get('wave_id')}   state: {entry.get('state')}   rig: {entry.get('rig')}")
    for key, value in (entry.get("params") or {}).items():
        print(f"  {key} = {json.dumps(value)}")
    return 0


# ───────────────────────────── machines and transport ─────────────────────────────


def transport(machine, script: str) -> subprocess.CompletedProcess:
    """Run a python program on a machine (locally for the hub), the program piped over stdin."""
    return rigsync.remote(machine, ["python3", "-"], check=False, input_bytes=script.encode())


def machine_call(machine, op: str, payload: dict) -> dict:
    request = b64(json.dumps({"op": op, **payload}).encode())
    script = AGENT_PATH.read_text() + f"\n\nserve({request!r})\n"
    try:
        result = transport(machine, script)
    except rigsync.RigSyncError as exc:
        raise RunIdError(f"{machine.name}: unreachable: {exc}") from exc
    stdout = result.stdout.decode(errors="replace") if isinstance(result.stdout, bytes) else (result.stdout or "")
    stderr = result.stderr.decode(errors="replace") if isinstance(result.stderr, bytes) else (result.stderr or "")
    lines = [line for line in stdout.splitlines() if line.startswith(agent.RESULT_MARKER)]
    if not lines:
        raise RunIdError(f"{machine.name}: {op} failed (exit {result.returncode}): {stderr.strip()[-2000:] or 'no output'}")
    reply = json.loads(lines[-1][len(agent.RESULT_MARKER):])
    if not reply.get("ok"):
        undone = " (its own partial changes were undone)" if reply.get("undone") else ""
        raise RunIdError(f"{machine.name}: {op} failed{undone}: {reply.get('error')}")
    return reply


def machine_root(ctx: Context, machine) -> str:
    return str(ctx.project if machine.local else machine.repo_path)


def experiment_waves(ctx: Context) -> list[tuple[str, dict]]:
    """Every supervisor-registered wave of this experiment on the hub, oldest first."""
    state = ctx.project / ".waves" / "_state"
    waves = []
    if state.is_dir():
        for path in sorted(state.glob("*/queue.json")):
            if not rigsync.WAVE_RE.fullmatch(path.parent.name):
                continue
            queue = read_json(path, {}) or {}
            if queue.get("experiment") == ctx.experiment:
                waves.append((path.parent.name, queue))
    return waves


def wave_machine_names(ctx: Context, waves: list[tuple[str, dict]]) -> set[str]:
    names: set[str] = set()
    for wave_id, queue in waves:
        names.update((queue.get("pool") or {}).keys())
        ledger = ctx.project / ".waves" / "_state" / wave_id / "ledger.jsonl"
        for line in ledger.read_text().splitlines() if ledger.is_file() else []:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if isinstance(record, dict) and isinstance(record.get("rig"), str):
                names.add(record["rig"])
    return names


def run_collect_check(ctx: Context, wave_id: str) -> subprocess.CompletedProcess:
    """`rigsync collect --wave <id> --check` as a subprocess, so its output and exit code are its own."""
    command = [
        sys.executable, str(RIGSYNC_SCRIPT), "--root", str(ctx.project), "--config", str(ctx.config_path),
        "--registry", str(ctx.registry), "collect", "--wave", wave_id, "--check",
    ]
    return subprocess.run(command, capture_output=True, text=True, check=False)


def check_preconditions(ctx: Context, extra_machines: list[str], *, reconcile: bool = True):
    """(waves, machines): refuses a live wave and, with `reconcile`, an unreconciled rig."""
    waves = experiment_waves(ctx)
    live = [wave_id for wave_id, queue in waves if not queue.get("finished_at")]
    if live:
        raise RunIdError(
            f"wave(s) {', '.join(live)} of {ctx.experiment} are still live (no finished_at in their supervisor queue); "
            "a run_id migration waits until every wave of the experiment is finished"
        )
    config = ctx.config()
    hub = ctx.hub()
    names = wave_machine_names(ctx, waves) | set(extra_machines)
    unknown = sorted(name for name in names if name not in config.machines)
    if unknown:
        raise RunIdError(f"machine(s) {', '.join(unknown)} ran this experiment but sync.toml does not declare them")
    machines = [hub] + [config.machines[name] for name in sorted(names) if name != hub.name and not config.machines[name].local]
    if reconcile:
        failures = []
        for wave_id, _queue in waves:
            result = run_collect_check(ctx, wave_id)
            if result.returncode != 0:
                failures.append(f"wave {wave_id}:\n{(result.stdout or '').strip()}\n{(result.stderr or '').strip()}".rstrip())
        if failures:
            raise RunIdError(
                "evaluation records are not reconciled with the hub; run `rigsync collect --wave <id> --confirm` "
                "for each wave below (a differing record needs a person), then plan again:\n" + "\n".join(failures)
            )
    return waves, machines


def board_files(ctx: Context) -> tuple[object | None, list[Path], str | None]:
    try:
        config = board.load_config(ctx.registry)
    except Exception as exc:  # noqa: BLE001 -- the board is optional for a migration
        return None, [], f"rig-board not configured ({exc}); its history is not rewritten"
    files = [config.history_path] if config.history_path.is_file() else []
    if config.lanes_dir.is_dir():
        files.extend(sorted(config.lanes_dir.glob("*/*.json")))
    return config, files, None


# ───────────────────────────── migrate plan ─────────────────────────────


def split_csv(value: str | None) -> list[str]:
    return [part.strip() for part in (value or "").split(",") if part.strip()]


def load_backfill(path: Path | None, new_params: list[str]) -> dict:
    """{param: {"value", "source", "runs": {old_hash: {"value", "source"}}}}, validated."""
    if path is None:
        return {}
    raw = read_json(path)
    if not isinstance(raw, dict):
        raise RunIdError(f"{path}: the backfill file must be a JSON object keyed by param")

    def entry(item, where: str) -> dict:
        if not isinstance(item, dict) or "value" not in item or not isinstance(item.get("source"), str) or not item["source"]:
            raise RunIdError(f"{path}: {where} needs \"value\" and a non-empty \"source\" (file:line@sha, or \"absent\")")
        if item["source"] == ABSENT and item["value"] is not None:
            raise RunIdError(f"{path}: {where} has source \"absent\" but a non-null value")
        return {"value": item["value"], "source": item["source"]}

    backfill = {}
    for param, spec in raw.items():
        if param not in new_params:
            raise RunIdError(f"{path}: {param!r} is not an identity param of the new version")
        if not isinstance(spec, dict):
            raise RunIdError(f"{path}: {param} must be an object")
        result: dict = {"runs": {}}
        if "value" in spec or "source" in spec:
            result.update(entry(spec, param))
        for old, item in (spec.get("runs") or {}).items():
            result["runs"][old] = entry(item, f"{param}.runs.{old}")
        backfill[param] = result
    return backfill


def _backfill_for(backfill: dict, param: str, old: str) -> dict | None:
    spec = backfill.get(param)
    if not spec:
        return None
    if old in spec["runs"]:
        return spec["runs"][old]
    if "value" in spec:
        return {"value": spec["value"], "source": spec["source"]}
    return None


def wandb_script(plan: dict, python: str = "<environment.name>/bin/python") -> str:
    renames = json.dumps(plan["wandb"]["renames"], indent=1, sort_keys=True)
    paths = json.dumps(plan["wandb"]["paths"])
    return f'''#!/usr/bin/env python3
"""Rename WandB runs after run_id migration v{plan["from_version"]} -> v{plan["to_version"]} of {plan["experiment"]}.

Generated by run-identity. Run it with the project environment (it needs wandb):
    {python} wandb_rename.py [--path entity/project ...]            # prints what would change
    {python} wandb_rename.py [--path entity/project ...] --apply    # renames
Every hashed-v2 run is wandb.init(name=run_id_label(...), tags=[run_id_name(...)], ...): the display
name is the readable label and the hash is a tag. A run matches by its old-hash tag; its name becomes
the new label and the old-hash tag is replaced by the new hash. A WandB run *id* never carries the
hash and is never changed.
"""
import argparse
import json

import wandb

RENAMES = json.loads(r"""{renames}""")
DEFAULT_PATHS = {paths}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", action="append", default=None, help="entity/project (repeatable)")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    paths = args.path or DEFAULT_PATHS
    if not paths:
        parser.error("no WandB project recorded in the run configs; pass --path entity/project")
    by_old_hash = {{item["old_hash"]: item for item in RENAMES}}
    api = wandb.Api()
    changed = 0
    for path in paths:
        for run in api.runs(path):
            hits = [by_old_hash[tag] for tag in run.tags if tag in by_old_hash]
            if not hits:
                continue
            if len(hits) > 1:
                print(f"{{path}}/{{run.id}}: tagged with {{len(hits)}} old hashes; skipped, settle it by hand")
                continue
            item = hits[0]
            tags = [item["new_hash"] if tag == item["old_hash"] else tag for tag in run.tags]
            name = item["new_label"]
            if name == run.name and tags == list(run.tags):
                continue
            changed += 1
            print(f"{{path}}/{{run.id}}: name {{run.name}} -> {{name}}; tag {{item['old_hash']}} -> {{item['new_hash']}}")
            if args.apply:
                run.name = name
                run.tags = tags
                run.update()
    print(f"{{changed}} run(s) {{'renamed' if args.apply else 'would be renamed'}}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


def identity_rels(experiment: str, hub: bool) -> list[str]:
    """Identity files a machine's inventory tracks: the map everywhere, the tracked spec on the hub."""
    rels = [f"evaluations/{experiment}/{agent.MAP_NAME}"]
    if hub:
        rels = [f"config/{experiment}/{agent.VERSIONS_NAME}", f"config/{experiment}/{agent.HISTORY_NAME}"] + rels
    return rels


def plan_migration(ctx: Context, args: argparse.Namespace) -> int:
    exp_root = ctx.exp_root
    versions = load_versions(ctx.spec_root)
    history = load_history(ctx.spec_root)
    current = versions[-1]
    from_version, params_before = current["version"], list(current["params"])
    add, remove = split_csv(args.add), split_csv(args.remove)
    if not add and not remove:
        raise RunIdError("a run_id migration needs --add and/or --remove")
    for param in add:
        if "." in param:
            raise RunIdError(
                f"--add {param!r} is dotted: identity params are top-level config keys; to identify a "
                "nested value, elect its top-level subtree key"
            )
        if not PARAM_RE.fullmatch(param):
            raise RunIdError(f"--add {param!r} is not a param name")
        if param in params_before:
            raise RunIdError(f"--add {param!r} is already an identity param of v{from_version}")
    for param in remove:
        if param not in params_before:
            raise RunIdError(f"--remove {param!r} is not an identity param of v{from_version}")
    if len(set(add)) != len(add):
        raise RunIdError("--add names a param twice")
    params_after = [p for p in params_before if p not in remove] + add
    if not params_after:
        raise RunIdError("the new version would have no identity params")
    to_version = from_version + 1
    if not args.reason:
        raise RunIdError("--reason is required: it becomes the RUN_ID_VERSIONS.json entry's reason")

    runs = hub_runs(exp_root)
    if not runs:
        raise RunIdError(f"{exp_root} holds no run records; with nothing to rename, just add the version entry")
    waves, machines = check_preconditions(ctx, split_csv(args.machines))
    backfill = load_backfill(Path(args.backfill_file) if args.backfill_file else None, params_after)

    needs: list[dict] = []
    per_run: dict[str, dict] = {}
    for old, run in sorted(runs.items()):
        config = run["config"] or {}
        values = {}
        filled = {}
        missing = []
        for param in params_after:
            if param in params_before:
                values[param] = run["record"]["params"].get(param)
            elif param in config:
                values[param] = config[param]
                if backfill.get(param, {}).get("runs", {}).get(old):
                    raise RunIdError(f"backfill for {param} on {old}: the run's .run_config.json already records it")
            else:
                item = _backfill_for(backfill, param, old)
                if item is None:
                    missing.append(param)
                else:
                    values[param] = item["value"]
                    filled[param] = item
        status = run["status"]
        if missing:
            needs.append({
                "hash": old, "missing": missing, "wave_id": status.get("wave_id"),
                "source_revision": status.get("source_revision"), "source_tag": status.get("source_tag"),
                "label": agent.label(run["record"]["params"], params_before),
            })
            continue
        per_run[old] = {"values": values, "backfilled": filled, "status": status, "config": run["config"]}
    if needs:
        print(f"{len(needs)} run(s) need a backfill: their stored .run_config.json lacks a new identity param.")
        print("Read the code at each run's source revision (wave tag wave--<wave_id>) and record the value it ran with,")
        print("as file:line@sha, or null with source \"absent\" when the param did not exist yet; then plan again.")
        for item in needs:
            print(f"  {item['hash']}  missing {','.join(item['missing'])}  wave {item['wave_id']}  "
                  f"rev {item['source_revision']}  tag {item['source_tag']}  [{item['label']}]")
        skeleton = {}
        for item in needs:
            for param in item["missing"]:
                skeleton.setdefault(param, {"value": None, "source": "<file:line@sha | absent>", "runs": {}})
        print("Backfill file skeleton (a default per param; add \"runs\": {old_hash: {value, source}} for exceptions):")
        print(json.dumps(skeleton, indent=2))
        raise RunIdError(f"{len(needs)} run(s) still need backfill values")

    mapping: dict[str, str] = {}
    claimed: dict[str, str] = {}
    for old, item in per_run.items():
        new = agent.hash_from_values(item["values"])
        if new in claimed:
            raise RunIdError(
                f"collision: {claimed[new]} and {old} would both become {new} under params {params_after}; "
                "the new version loses an axis that tells them apart"
            )
        claimed[new] = old
        mapping[old] = new
    overlap = sorted(set(mapping.values()) & set(runs))
    if overlap:
        raise RunIdError(f"new hash(es) {', '.join(overlap)} equal existing run hashes")

    date = now().date().isoformat()
    versions_entry = {"version": to_version, "params": params_after, "date": date, "reason": args.reason}
    history_backfill = {}
    for param, spec in backfill.items():
        out = {k: spec[k] for k in ("value", "source") if k in spec}
        used = {old: v for old, v in spec["runs"].items() if old in per_run}
        if used:
            out["runs"] = used
        history_backfill[param] = out
    history_entry = {"from_version": from_version, "to_version": to_version, "date": date, "map": mapping, "backfill": history_backfill}
    new_versions_bytes = dumps_list(versions + [versions_entry])
    new_history_bytes = dumps_list(history + [history_entry])

    # new run records, byte-for-byte what the template's guard would have written
    records: dict[str, bytes] = {}
    configs: dict[str, bytes] = {}
    runs_out: dict[str, dict] = {}
    for old, item in per_run.items():
        new = mapping[old]
        rel = f"evaluations/{ctx.experiment}/{old}"
        records[f"{rel}/.run_id.json"] = agent.record_bytes({"layout": agent.HASHED_V2, "version": to_version, "hash": new, "params": item["values"]})
        if item["backfilled"] and item["config"] is not None:
            config = dict(item["config"])
            config.update({param: fill["value"] for param, fill in item["backfilled"].items()})
            configs[f"{rel}/.run_config.json"] = agent.config_bytes(config)
        runs_out[old] = {
            "new": new,
            "params_before": runs[old]["record"]["params"],
            "params_after": item["values"],
            "label_before": agent.label(runs[old]["record"]["params"], params_before),
            "label_after": agent.label(item["values"], params_after),
            "backfilled": item["backfilled"],
            "wave_id": item["status"].get("wave_id"),
            "source_revision": item["status"].get("source_revision"),
            "source_tag": item["status"].get("source_tag"),
        }

    wave_ids = [wave_id for wave_id, _ in waves]
    plan_machines: dict[str, dict] = {}
    for machine in machines:
        inventory = machine_call(machine, "inventory", {
            "root": machine_root(ctx, machine), "experiment": ctx.experiment, "areas": list(AREAS),
            "map": mapping, "waves": wave_ids, "extras": list(PROJECT_FILES) if machine.local else [],
            "plots": bool(machine.local), "identity": identity_rels(ctx.experiment, bool(machine.local)),
        })
        if inventory["collisions"]:
            raise RunIdError(f"{machine.name}: new hash collides with an existing dir: " + "; ".join(inventory["collisions"]))
        if inventory["unmapped"]:
            raise RunIdError(
                f"{machine.name}: run dirs whose hash the hub has no record of (collect them, or settle them first): "
                + ", ".join(inventory["unmapped"][:20])
            )
        files: dict[str, dict] = {f["path"]: {"path": f["path"], "sha256": f["sha256"], "action": "replace", "hits": f["hits"]} for f in inventory["files"]}
        for path, sha in inventory["records"].items():
            name = PurePosixPath(path).name
            if name == ".status.json" or sha is None:
                continue
            hub_sha = agent.sha256_file(ctx.project / path)
            if sha != hub_sha:
                raise RunIdError(f"{machine.name}: {path} differs from the hub's copy; reconcile with rigsync collect first")
            content = records.get(path) if name == ".run_id.json" else configs.get(path)
            if content is not None:
                files[path] = {"path": path, "sha256": sha, "action": "content", "content_b64": b64(content)}
        identity = inventory["identity"]
        map_rel = f"evaluations/{ctx.experiment}/{agent.MAP_NAME}"
        notes = []
        if machine.local:
            # the tracked identity files change on the hub only; rigs get them with the next wave deploy
            versions_rel, history_rel = identity_rels(ctx.experiment, True)[:2]
            files[versions_rel] = {"path": versions_rel, "sha256": identity[versions_rel], "action": "content", "content_b64": b64(new_versions_bytes)}
            files[history_rel] = {"path": history_rel, "sha256": identity[history_rel], "action": "content", "content_b64": b64(new_history_bytes)}
        rebuild_map = machine.local or identity[map_rel] is not None
        present = sorted(
            agent.RUN_DIR_RE.match(PurePosixPath(r["from"]).name).group(1)
            for r in inventory["renames"]
            if r["from"].startswith(f"evaluations/{ctx.experiment}/") and agent.RUN_DIR_RE.match(PurePosixPath(r["from"]).name).group(2) is None
        )
        plan_machines[machine.name] = {
            "root": machine_root(ctx, machine),
            "hub": bool(machine.local),
            "renames": inventory["renames"],
            "files": sorted(files.values(), key=lambda f: f["path"]),
            "map_file": {"path": map_rel, "sha256": identity[map_rel], "rebuild": rebuild_map},
            "runs_present": present,
            "plots": inventory["plots"],
            "wandb_dirs": inventory["wandb_dirs"],
            "notes": notes,
            "inventory": {k: inventory[k] for k in ("renames", "files", "records", "identity")},
        }

    board_config, board_paths, board_note = board_files(ctx)
    volatile = []
    for path in board_paths:
        hits, _ = agent._text_hits(path, mapping)
        if hits:
            volatile.append({"path": str(path), "hits": hits})

    wandb_paths = sorted({
        f"{cfg['wandb'].get('entity')}/{cfg['wandb'].get('project')}" if cfg["wandb"].get("entity") else str(cfg["wandb"].get("project"))
        for item in per_run.values()
        if isinstance(cfg := item["config"], dict) and isinstance(cfg.get("wandb"), dict) and cfg["wandb"].get("project")
    })
    wandb_detected = bool(wandb_paths) or any(m["wandb_dirs"] for m in plan_machines.values())
    notes = [note for m in plan_machines.values() for note in m["notes"]]
    if board_note:
        notes.append(board_note)
    plan = {
        "schema": PLAN_SCHEMA,
        "created_at": now().isoformat(timespec="seconds"),
        "project": str(ctx.project),
        "experiment": ctx.experiment,
        "layout": agent.HASHED_V2,
        "from_version": from_version,
        "to_version": to_version,
        "params_before": params_before,
        "params_after": params_after,
        "added": add,
        "removed": remove,
        "reason": args.reason,
        "waves": wave_ids,
        "versions_entry": versions_entry,
        "history_entry": history_entry,
        "map": mapping,
        "runs": runs_out,
        "new_index": {new: {"params": per_run[old]["values"], "version": to_version} for old, new in mapping.items()},
        "machines": plan_machines,
        "board": {"files": volatile},
        "plots": sorted({p for m in plan_machines.values() for p in m["plots"]}),
        "wandb": {
            "detected": wandb_detected,
            "paths": wandb_paths,
            "renames": [
                {"old_hash": old, "new_hash": item["new"], "old_label": item["label_before"], "new_label": item["label_after"]}
                for old, item in sorted(runs_out.items())
            ] if wandb_detected else [],
        },
        "notes": notes,
    }
    out = Path(args.out) if args.out else exp_root / agent.MIGRATIONS_DIR / f"plan-v{from_version}-v{to_version}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n")
    if wandb_detected:
        (out.parent / "wandb_rename.py").write_text(wandb_script(plan, ctx.environment_python()))
    print_plan_summary(plan, out)
    return 0


def print_plan_summary(plan: dict, out: Path) -> None:
    print(f"run_id migration plan: {plan['experiment']} v{plan['from_version']} -> v{plan['to_version']}")
    print(f"  params: {plan['params_before']} -> {plan['params_after']}  (reason: {plan['reason']})")
    print(f"  waves: {', '.join(plan['waves']) or 'none registered'}")
    for name, machine in plan["machines"].items():
        hits = sum(f.get("hits", 0) for f in machine["files"] if f["action"] == "replace")
        records = sum(1 for f in machine["files"] if f["action"] == "content")
        print(f"  {name}{' (hub)' if machine['hub'] else ''}: {len(machine['renames'])} dir rename(s), "
              f"{len(machine['files'])} file(s) rewritten ({records} structurally, {hits} hash occurrence(s) replaced), "
              f"map {'rebuilt' if machine['map_file']['rebuild'] else 'not present'}")
    if plan["board"]["files"]:
        print(f"  rig-board: {len(plan['board']['files'])} file(s) mention an old hash (rewritten under the board lock)")
    print("  map (old -> new):")
    for old, item in sorted(plan["runs"].items()):
        print(f"    {old} -> {item['new']}  [{item['label_after']}]")
        for param, fill in item["backfilled"].items():
            print(f"        backfill {param}={json.dumps(fill['value'])}  source {fill['source']}")
    if plan["plots"]:
        print(f"  plots to re-render: {len(plan['plots'])}")
        for path in plan["plots"]:
            print(f"    {path}")
    if plan["wandb"]["detected"]:
        print(f"  WandB: {len(plan['wandb']['renames'])} run name(s)/tag(s) to rename with {out.parent / 'wandb_rename.py'}")
    for note in plan["notes"]:
        print(f"  note: {note}")
    print(f"plan written: {out}")
    print("Log this summary to program/DECISIONS.md, then apply (agent-decided; rollback undoes it):")
    print(f"  runid.py --project {plan['project']} --experiment {plan['experiment']} migrate apply --plan {out} --confirm")


# ───────────────────────────── migrate apply ─────────────────────────────


def load_plan(ctx: Context, path: Path) -> dict:
    plan = read_json(path)
    if not isinstance(plan, dict) or plan.get("schema") != PLAN_SCHEMA:
        raise RunIdError(f"{path} is not a run_id migration plan")
    if Path(plan["project"]) != ctx.project or plan["experiment"] != ctx.experiment:
        raise RunIdError(f"{path} plans {plan['project']} {plan['experiment']}, not {ctx.project} {ctx.experiment}")
    return plan


def stale_reasons(planned: dict, fresh: dict) -> list[str]:
    reasons = []
    for key in ("renames", "records", "identity"):
        if planned[key] != fresh[key]:
            reasons.append(f"{key} changed")
    before = {f["path"]: f["sha256"] for f in planned["files"]}
    after = {f["path"]: f["sha256"] for f in fresh["files"]}
    for path in sorted(set(before) | set(after)):
        if before.get(path) != after.get(path):
            reasons.append(f"{path} {'appeared' if path not in before else 'vanished' if path not in after else 'changed'}")
    return reasons


def manifest_path(ctx: Context, ts: str) -> Path:
    return ctx.exp_root / agent.MIGRATIONS_DIR / ts / "rollback.json"


def write_manifest(path: Path, manifest: dict) -> None:
    agent.write_atomic(path, (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode())


def rewrite_board(paths: list[str], mapping: dict, board_config) -> int:
    changed = 0
    with board.BoardLock(board_config):
        for raw in paths:
            path = Path(raw)
            if not path.is_file():
                continue
            data = path.read_bytes()
            new = agent.replace_hashes(data, mapping)
            if new != data:
                agent.write_atomic(path, new)
                changed += 1
    return changed


def verify_index(name: str, found: dict, plan: dict, present: list[str] | None) -> None:
    """A machine's rebuilt index must equal the planned map: all of it on the hub, its own runs on a rig."""
    expected = plan["new_index"] if present is None else {plan["map"][old]: plan["new_index"][plan["map"][old]] for old in present}
    if found == expected:
        return
    extra = sorted(set(found) - set(expected))
    lacking = sorted(set(expected) - set(found))
    differ = sorted(h for h in set(expected) & set(found) if expected[h] != found[h])
    raise RunIdError(f"{name}: rebuilt index does not equal the planned map (extra {extra[:5]}, missing {lacking[:5]}, differing {differ[:5]})")


def rollback_machines(manifest: dict, machines: dict, *, force: bool = False) -> list[str]:
    """Roll back every applied machine of a manifest, newest first; returns the failures."""
    failures = []
    for name in reversed(list(manifest["machines"])):
        entry = manifest["machines"][name]
        if entry["state"] != "applied":
            continue
        try:
            machine_call(machines[name], "rollback", {
                "root": entry["root"], "backup_dir": entry["backup_dir"], "files": entry["files"],
                "renames": entry["renames"], "force": force,
            })
            entry["state"] = "rolled-back"
        except RunIdError as exc:
            failures.append(str(exc))
    return failures


def followups(plan: dict) -> dict:
    """What the agent still updates by hand after an apply: EXPERIMENTS.md labels, params, backfills."""
    return {
        "schema": "run-identity-followups/1",
        "experiment": plan["experiment"],
        "from_version": plan["from_version"],
        "to_version": plan["to_version"],
        "params_before": plan["params_before"],
        "params_after": plan["params_after"],
        "experiments_md": (
            f"in the {plan['experiment']} section set the preamble's `run_id version:` to "
            f"v{plan['to_version']} and its params line to {plan['params_after']}, add the param "
            "columns of the new params, and per run below fill those columns and replace the label cell"
        ),
        "runs": [
            {
                "old_hash": old, "new_hash": item["new"], "label_before": item["label_before"],
                "label_after": item["label_after"], "params_after": item["params_after"],
                "backfilled": item["backfilled"], "wave_id": item["wave_id"],
            }
            for old, item in sorted(plan["runs"].items())
        ],
    }


def apply_migration(ctx: Context, args: argparse.Namespace) -> int:
    if args.dry_run == args.confirm:
        raise RunIdError("pass exactly one of --dry-run or --confirm (apply after reporting the plan summary)")
    plan = load_plan(ctx, Path(args.plan))
    versions = load_versions(ctx.spec_root)
    history = load_history(ctx.spec_root)
    if versions[-1]["version"] != plan["from_version"]:
        raise RunIdError(
            f"the experiment is at v{versions[-1]['version']}, the plan migrates v{plan['from_version']}: "
            "it was already applied or another migration ran; plan again"
        )
    _waves, machines = check_preconditions(ctx, [n for n in plan["machines"] if n != ctx.hub().name])
    by_name = {m.name: m for m in machines}
    missing = sorted(set(plan["machines"]) - set(by_name))
    if missing or set(by_name) != set(plan["machines"]):
        raise RunIdError(f"the machines holding this experiment changed since the plan ({sorted(by_name)} vs {sorted(plan['machines'])}); plan again")
    mapping = plan["map"]
    stale = []
    for name, entry in plan["machines"].items():
        fresh = machine_call(by_name[name], "inventory", {
            "root": entry["root"], "experiment": ctx.experiment, "areas": list(AREAS), "map": mapping,
            "waves": plan["waves"], "extras": list(PROJECT_FILES) if entry["hub"] else [], "plots": False,
            "identity": identity_rels(ctx.experiment, bool(entry["hub"])),
        })
        stale.extend(f"{name}: {reason}" for reason in stale_reasons(entry["inventory"], fresh))
    if stale:
        raise RunIdError("the plan is stale; nothing was changed. Plan again:\n  " + "\n  ".join(stale[:40]))
    if args.dry_run:
        print(f"[dry-run] {ctx.experiment} v{plan['from_version']} -> v{plan['to_version']}: the plan is current")
        for name, entry in plan["machines"].items():
            print(f"[dry-run] {name}: rename {len(entry['renames'])} dir(s), rewrite {len(entry['files'])} file(s)")
        return 0

    new_versions = versions + [plan["versions_entry"]]
    new_history = history + [plan["history_entry"]]
    ts = now().strftime("%Y%m%d-%H%M%S")
    while manifest_path(ctx, ts).parent.exists():
        ts += "x"
    mpath = manifest_path(ctx, ts)
    manifest = {
        "schema": ROLLBACK_SCHEMA, "ts": ts, "status": "prepared", "experiment": ctx.experiment,
        "from_version": plan["from_version"], "to_version": plan["to_version"], "map": mapping,
        "plan_sha256": agent.sha256_file(Path(args.plan)), "board": {"files": [], "rewritten": False},
        "machines": {},
    }
    for name, entry in plan["machines"].items():
        files = [{"path": f["path"], "sha_before": f["sha256"], "sha_after": None} for f in entry["files"]]
        if entry["map_file"]["rebuild"]:
            files.append({"path": entry["map_file"]["path"], "sha_before": entry["map_file"]["sha256"], "sha_after": None})
        manifest["machines"][name] = {
            "root": entry["root"],
            "backup_dir": str(Path(entry["root"]) / "evaluations" / ctx.experiment / agent.MIGRATIONS_DIR / ts / "backup"),
            "renames": entry["renames"], "files": files, "state": "pending",
        }
    mpath.parent.mkdir(parents=True, exist_ok=True)
    (mpath.parent / "plan.json").write_bytes(Path(args.plan).read_bytes())
    write_manifest(mpath, manifest)  # before anything changes

    order = [n for n in plan["machines"] if not plan["machines"][n]["hub"]] + [n for n in plan["machines"] if plan["machines"][n]["hub"]]
    try:
        for name in order:
            entry = manifest["machines"][name]
            machine_call(by_name[name], "backup", {
                "root": entry["root"], "backup_dir": entry["backup_dir"],
                "files": [{"path": f["path"], "sha256": f["sha_before"]} for f in entry["files"]],
            })
        manifest["status"] = "applying"
        write_manifest(mpath, manifest)
        for name in order:
            entry = manifest["machines"][name]
            planned = plan["machines"][name]
            reply = machine_call(by_name[name], "apply", {
                "root": entry["root"], "backup_dir": entry["backup_dir"], "map": mapping,
                "files": planned["files"], "renames": planned["renames"],
            })
            for item in entry["files"]:
                if item["path"] in reply["after"]:
                    item["sha_after"] = reply["after"][item["path"]]
            entry["state"] = "applied"
            write_manifest(mpath, manifest)
            index = machine_call(by_name[name], "index", {
                "root": entry["root"], "experiment": ctx.experiment, "versions": new_versions,
                "history": new_history, "write": planned["map_file"]["rebuild"],
            })
            if planned["map_file"]["rebuild"]:
                for item in entry["files"]:
                    if item["path"] == planned["map_file"]["path"]:
                        item["sha_after"] = index["map_sha"]
                write_manifest(mpath, manifest)
            verify_index(name, index["by_hash"], plan, None if planned["hub"] else planned["runs_present"])
        board_config, _paths, _note = board_files(ctx)
        board_paths = [item["path"] for item in plan["board"]["files"]]
        if board_config is not None and board_paths:
            manifest["board"]["files"] = board_paths
            rewrite_board(board_paths, mapping, board_config)
            manifest["board"]["rewritten"] = True
        manifest["status"] = "applied"
        write_manifest(mpath, manifest)
    except Exception as exc:  # noqa: BLE001 -- any failure rolls back what was applied, then reports
        failures = rollback_machines(manifest, by_name)
        manifest["status"] = "failed-rolled-back" if not failures else "failed-rollback-incomplete"
        write_manifest(mpath, manifest)
        detail = "" if not failures else "\nrollback also failed (manifest kept for a person):\n  " + "\n  ".join(failures)
        raise RunIdError(f"migration failed and was rolled back: {exc}{detail}\nmanifest: {mpath}") from exc

    todo = followups(plan)
    followups_path = mpath.parent / "followups.json"
    agent.write_atomic(followups_path, (json.dumps(todo, indent=2, sort_keys=True) + "\n").encode())
    print(f"[apply] {ctx.experiment}: v{plan['from_version']} -> v{plan['to_version']}, {len(mapping)} run(s) renamed on "
          f"{', '.join(order)}; every machine's index equals the planned map")
    print(f"[apply] rollback manifest: {mpath}   (undo: migrate rollback {ts} --confirm)")
    print(f"[apply] follow-ups: {followups_path}")
    print("Follow-ups:")
    if plan["wandb"]["detected"]:
        print(f"  - rename the WandB runs: {ctx.environment_python()} {Path(args.plan).parent / 'wandb_rename.py'} (then --apply)")
    if plan["plots"]:
        print(f"  - re-render {len(plan['plots'])} plot(s) that name an old hash: " + ", ".join(plan["plots"][:10]))
    print(f"  - update EXPERIMENTS.md ({todo['experiments_md']}):")
    for run in todo["runs"]:
        filled = ", ".join(f"{p}={json.dumps(f['value'])} ({f['source']})" for p, f in run["backfilled"].items())
        print(f"      {run['new_hash']}  label {run['label_after']}" + (f"  backfilled {filled}" if filled else ""))
    print("  - append a JOURNAL.md entry (old -> new map, backfill sources, reason)")
    print(f"  - commit config/{ctx.experiment}/RUN_ID_VERSIONS.json, config/{ctx.experiment}/RUN_ID_HISTORY.json "
          "and the rewritten tracked files in one commit; the next wave deploys that revision to the rigs")
    return 0


# ───────────────────────────── migrate rollback ─────────────────────────────


def rollback_migration(ctx: Context, args: argparse.Namespace) -> int:
    if args.dry_run == args.confirm:
        raise RunIdError("pass exactly one of --dry-run or --confirm")
    mpath = manifest_path(ctx, args.ts)
    manifest = read_json(mpath)
    if not isinstance(manifest, dict) or manifest.get("schema") != ROLLBACK_SCHEMA:
        raise RunIdError(f"no rollback manifest at {mpath}")
    if manifest["status"] != "applied":
        raise RunIdError(f"migration {args.ts} is {manifest['status']}, not applied; nothing to roll back")
    versions = load_versions(ctx.spec_root)
    if versions[-1]["version"] != manifest["to_version"]:
        raise RunIdError(f"the experiment is at v{versions[-1]['version']}; only the latest migration (to v{manifest['to_version']}) can be rolled back")
    waves = experiment_waves(ctx)
    live = [wave_id for wave_id, queue in waves if not queue.get("finished_at")]
    if live:
        raise RunIdError(f"wave(s) {', '.join(live)} are live; roll back once they finish")
    config = ctx.config()
    machines = {}
    for name in manifest["machines"]:
        if name not in config.machines:
            raise RunIdError(f"sync.toml no longer declares {name}")
        machines[name] = config.machines[name]
    if args.dry_run:
        for name, entry in manifest["machines"].items():
            print(f"[dry-run] {name}: rename back {len(entry['renames'])} dir(s), restore {len(entry['files'])} file(s)")
        return 0
    failures = rollback_machines(manifest, machines, force=args.force)
    if manifest["board"].get("rewritten"):
        board_config, _paths, _note = board_files(ctx)
        if board_config is not None:
            reverse = {new: old for old, new in manifest["map"].items()}
            rewrite_board(manifest["board"]["files"], reverse, board_config)
            manifest["board"]["rewritten"] = False
    manifest["status"] = "rolled-back" if not failures else "rollback-incomplete"
    # the manifest lives under the restored tree; keep it as the audit record
    write_manifest(mpath, manifest)
    if failures:
        raise RunIdError("rollback incomplete:\n  " + "\n  ".join(failures))
    print(f"[rollback] {ctx.experiment}: back to v{manifest['from_version']} on {', '.join(manifest['machines'])}")
    print("  - append a JOURNAL.md entry saying the migration was rolled back, and revert or drop its commit")
    return 0


# ───────────────────────────── CLI ─────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", default=os.getcwd(), help="project root (default: the current directory)")
    parser.add_argument("--experiment", help="experiment path under config/ and evaluations/, e.g. 000_name")
    parser.add_argument("--registry", help=f"machines registry (default: $RIGSYNC_REGISTRY or {DEFAULT_REGISTRY})")
    parser.add_argument("--config", help="project sync.toml (default: <project>/sync.toml)")
    sub = parser.add_subparsers(dest="command", required=True)
    look = sub.add_parser("lookup", help="hash or unique prefix -> params (follows RUN_ID_HISTORY.json); or --where k=v. Exit 1: no match")
    look.add_argument("hash", nargs="?")
    look.add_argument("--where", nargs="+", default=[], metavar="K=V", help="values parse as JSON when they can, so lr=1e-4 matches 0.0001")
    look.add_argument("--json", action="store_true")
    migrate = sub.add_parser("migrate", help="evolve the experiment's identity: plan, apply, rollback")
    msub = migrate.add_subparsers(dest="migrate_command", required=True)
    plan = msub.add_parser("plan")
    plan.add_argument("--add", help="comma list of top-level config keys to add to the identity (no dotted names)")
    plan.add_argument("--remove", help="comma list of params to drop from the identity")
    plan.add_argument("--backfill-file", help="JSON {param: {value, source, runs: {old_hash: {value, source}}}}")
    plan.add_argument("--reason", help="why the identity changes (RUN_ID_VERSIONS.json reason)")
    plan.add_argument("--machines", help="comma list of extra machines holding this experiment's runs")
    plan.add_argument("--out", help="plan file (default: evaluations/<exp>/.run_id_migrations/plan-v<N>-v<N+1>.json)")
    apply_p = msub.add_parser("apply")
    apply_p.add_argument("--plan", required=True)
    apply_p.add_argument("--dry-run", action="store_true")
    apply_p.add_argument("--confirm", action="store_true")
    rollback = msub.add_parser("rollback")
    rollback.add_argument("ts", help="the migration's timestamp under evaluations/<exp>/.run_id_migrations/")
    rollback.add_argument("--dry-run", action="store_true")
    rollback.add_argument("--confirm", action="store_true")
    rollback.add_argument("--force", action="store_true", help="restore even files that changed after the migration")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        ctx = Context(args)
        if args.command == "lookup":
            return lookup(ctx, args.hash, args.where, args.json)
        if ctx.experiment is None:
            raise RunIdError("migrate needs --experiment")
        if args.migrate_command == "plan":
            return plan_migration(ctx, args)
        if args.migrate_command == "apply":
            return apply_migration(ctx, args)
        return rollback_migration(ctx, args)
    except RunIdError as exc:
        print(f"runid: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
