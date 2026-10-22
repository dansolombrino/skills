# Scaffold templates

Substitute `{{PROJECT_NAME}}` and `{{ENVIRONMENT_NAME}}`. Put a `.gitkeep` in each otherwise-empty
taxonomy directory before the first commit; the `.gitignore` exceptions below keep them in ignored
trees.

## README.md

```markdown
# {{PROJECT_NAME}}

## Structure

| path | purpose |
|---|---|
| `checkpoints/` | checkpoints produced by trainings/finetunings |
| `code/` | experiment code (hydra-configured) |
| `config/` | hydra yaml configs for `code/`, plus each experiment's run_id and glossary files |
| `evaluations/` | data produced by running experiments (+ `.status.json` markers, `metrics.jsonl` curves) |
| `logs/` | run logs — tee'd stdout+stderr per run (gitignored, rig-local) |
| `plots/` | plots produced by `visualizations/` |
| `scripts/` | shell scripts that launch experiments/sweeps |
| `tests/` | optional pytest unit tests, mirroring `code/` and `visualizations/` inside itself |
| `visualizations/` | plotting code (argparse), reads `evaluations/`, writes `plots/` |
| `shitpads/` | temp scratch space (gitignored, rig-local) |
| `references/` | papers/codebases for reference (gitignored, rig-synced) |

Experiments are named `NNN_experiment_name` and mirrored across the folders above; tests for
`<root>/<path>/<stem>.py` live at `tests/<root>/<path>/<stem>/test_*.py`.
Each run is identified by its **run_id** `rid-<32 hex>`, a hash of the experiment's identity
params: the experiment's `.py` pins `RUN_ID_PATH_LAYOUT = "hashed-v2"`, the params are the current
entry of `config/<experiment>/RUN_ID_VERSIONS.json`, `RUN_ID_HISTORY.json` beside it maps old
hashes to new ones after a run_id migration, and `evaluations/<experiment>/RUN_ID_MAP.json`
indexes every run. `run_id_label` gives the readable `k=v,…` shown beside the hash, and
`config/<experiment>/PARAM_GLOSSARY.json` gives each param's full name for plots.

## Tracking

- **`EXPERIMENTS.md`** — state: which runs exist (todo/inpr/done/failed), in which wave, on
  which rig and GPU.
- **`JOURNAL.md`** — story: what was done, why, and what was learned.

## Setup

1. `cp .env.example .env` and fill in the secrets.
2. Use `environment-sync` to provision the exact `uv.lock` + `.python-version` environment and
   run the project GPU smoke.
3. Configure the GitHub remote, dispatch branch, and `sync.toml`, then use `rig-sync` and
   `environment-sync` to prepare and verify the rigs.

## Running

Runs are launched in **waves** (one dispatch decision, id `YYYYMMDD-HHMMSS`) via
`scripts/NNN_experiment_name/<run_id folder>/wave_<wave_id>/` — a `README.md` saying what the
wave is, plus one placement-free `wave.sh` per run that any GPU lane of the wave's pool may claim.
Each wave is smoke-tested, committed, and tagged `wave--<wave_id>`; every rig verifies that exact
commit and the wave's environment fingerprint before execution. `logs/` mirrors `scripts/`.
```

## AGENTS.md

```markdown
# {{PROJECT_NAME}} — project notes

<!-- Thin by design: generic research conventions come from the `research` skills, enabled by
     the `research` tag in `.project.toml`. Only project-specific facts live here. -->

## Where things are

- `EXPERIMENTS.md` — run state: what exists, what ran where.
- `JOURNAL.md` — the story: why things were done and what was learned.
- `README.md` — structure and setup.
- `config/NNN_*/RUN_ID_VERSIONS.json` — each experiment's run identity (`hashed-v2`);
  `run-identity` looks up a run hash and migrates an identity.

This project's own files and the `research` skills are the reference; don't copy layout or
defaults from other projects on disk.

## Project-specific quirks

<!-- datasets, unusual constraints, deviations from the standard structure -->
```

## CLAUDE.md

`AGENTS.md` is the canonical project doc; `CLAUDE.md` only redirects to it, so both hosts read
the same notes.

```markdown
# {{PROJECT_NAME}}

Project notes live in `AGENTS.md`. Read @AGENTS.md before doing anything.
```

## EXPERIMENTS.md (initial)

```markdown
# Experiments

<!-- state only; the story lives in JOURNAL.md. One section per NNN_experiment; its header
     mirrors `RUN_ID_PATH_LAYOUT: hashed-v2`, the current `run_id version` and params from
     `config/<experiment_path>/RUN_ID_VERSIONS.json`, and
     `run_id map: evaluations/<experiment_path>/RUN_ID_MAP.json`.
     One row per (run, wave):
     | <run_id params...> | run_id | label | wave | rig | gpu | status | started | progress | eta | ended | elapsed | notes |
     Format: experiments-tracking skill. -->
```

## JOURNAL.md (initial)

```markdown
# Journal

<!-- append-only prose, chronological; entries under `## YYYY-MM-DD, HH:MM — title`;
     only a run_id migration may replace old run hashes in past entries -->
```

## .env.example (committed key mirror)

```bash
# Hugging Face credentials (secrets; keep values only in .env)
HF_TOKEN=
HUGGING_FACE_HUB_TOKEN=

# Project-scoped storage (relative to the project root; resolved by code/common/paths.py)
OPENCLIP_CACHE_DIR=
CACHE_DIR=

# Machine-local runtime setting
TORCH_NUM_WORKERS=
```

Shared model and dataset caches (`HF_HOME`, `HF_HUB_CACHE`, `HF_DATASETS_CACHE`,
`HUGGINGFACE_HUB_CACHE`, `TORCH_HOME`, `UV_CACHE_DIR`, `TRITON_CACHE_DIR`, `XDG_CACHE_HOME`,
`TMPDIR`, `WANDB_CACHE_DIR`, `WANDB_DIR`) are **absent by design** — see below.

## .env (ignored machine profile)

Render this file directly. Keep secret values empty unless the user supplies them; never copy or
commit a token from another project's `.env`.

Shared cache paths stay out of `.env`: it loads after the shell environment and silently overrides
the rig's machine-level caches (declared in the registry, installed by `rig-sync provision-env`).
Project-scoped storage stays **relative to the project root**, which `rig-sync doctor` already
requires to sit on a declared storage root, so one file is right on every rig.

```bash
HF_TOKEN=
HUGGING_FACE_HUB_TOKEN=

OPENCLIP_CACHE_DIR=storage/openclip
CACHE_DIR=storage/cache

TORCH_NUM_WORKERS=16
```

Nothing here is rig-specific, so `rig-sync push-env` copies the one file to every peer.
`doctor` warns on any absolute value.

## .gitignore

```gitignore
.env
{{ENVIRONMENT_NAME}}/
.rigsync_cache/
# per-wave worktrees and lock-keyed wave environments, created by rig-sync and environment-sync
.waves/
.envs/
storage/
shitpads/*
!shitpads/.gitkeep
logs/*
!logs/.gitkeep
# Slurm job-id history written beside a wave script by sweep-dispatch; state, not source
scripts/**/leonardo.jobs
references/*
!references/.gitkeep
checkpoints/*
!checkpoints/.gitkeep
evaluations/*
!evaluations/.gitkeep
plots/*
!plots/.gitkeep
__pycache__/
*.pyc
.pytest_cache/
# hydra default run dir, if left enabled
outputs/
# editor state is machine-local, except the exclude maps, which are project policy
.vscode/*
!.vscode/settings.json
```

The user may want `evaluations/` or `plots/` committed instead. The run-identity files are
tracked source in `config/<experiment_path>/`; only the derived `RUN_ID_MAP.json` sits in
`evaluations/`. Comments go on their own lines: `.gitignore` has no inline comments, and a
trailing `# note` becomes part of the pattern.

## .vscode/settings.json

Committed project policy. The artifact trees hold far more files than the tracked source, and
`.gitignore` does not hide them from an editor; these exclude maps keep them visible in the
explorer but out of the watcher, search, and Python analysis.

```jsonc
{
  // Git tracks a few hundred files here; the working tree holds far more, because
  // the artifact trees below are large and gitignored. .gitignore does NOT hide
  // them from the editor -- only these three exclude maps do.
  //
  // Policy: artifact trees stay VISIBLE in the explorer (plots/ gets browsed by
  // hand) but are excluded from the watcher, from search, and from Python
  // analysis. Consequence: they are unwatched, so the explorer does NOT
  // auto-refresh when a run writes new files there -- hit Refresh to see them.

  "files.watcherExclude": {
    "**/.git/objects/**": true,
    "**/.git/subtree-cache/**": true,
    "checkpoints/**": true,
    ".waves/**": true,
    ".envs/**": true,
    "plots/**": true,
    "evaluations/**": true,
    "logs/**": true,
    "shitpads/**": true,
    "references/**": true,
    "outputs/**": true,
    "storage/**": true,
    "{{ENVIRONMENT_NAME}}/**": true,
    ".rigsync_cache/**": true,
    ".pytest_cache/**": true,
    "**/__pycache__/**": true
  },

  // logs/ can hold symlinks (latest-run, debug logs) that can walk the
  // watcher and search outside the workspace entirely.
  "files.followSymlinks": false,
  "search.followSymlinks": false,

  "search.useIgnoreFiles": true,
  "search.exclude": {
    "checkpoints/**": true,
    "plots/**": true,
    "evaluations/**": true,
    "logs/**": true,
    "shitpads/**": true,
    "references/**": true,
    "outputs/**": true,
    "storage/**": true,
    "{{ENVIRONMENT_NAME}}/**": true,
    ".rigsync_cache/**": true,
    ".pytest_cache/**": true,
    "**/__pycache__/**": true,
    "**/*.pyc": true,
    "uv.lock": true
  },

  // Artifact trees stay visible on purpose. Only genuine noise is hidden.
  "files.exclude": {
    "**/__pycache__": true,
    "**/*.pyc": true,
    "**/.pytest_cache": true,
    ".rigsync_cache": true,
    "{{ENVIRONMENT_NAME}}": true
  },

  // Without an explicit exclude list the language server indexes the whole
  // workspace, virtual environment included.
  "python.defaultInterpreterPath": "${workspaceFolder}/{{ENVIRONMENT_NAME}}/bin/python",
  "python.analysis.exclude": [
    "checkpoints/**",
    "plots/**",
    "evaluations/**",
    "logs/**",
    "shitpads/**",
    "references/**",
    "outputs/**",
    "storage/**",
    "{{ENVIRONMENT_NAME}}/**",
    ".rigsync_cache/**",
    "**/__pycache__/**"
  ],
  "python.analysis.diagnosticMode": "openFilesOnly",
  "python.analysis.indexing": true,

  // A timed network fetch stalls the editor for no benefit on a repo this small.
  "git.autofetch": false,
  "git.untrackedChanges": "separate",

  // An artifact json or csv opened by accident should not freeze the editor.
  "editor.largeFileOptimizations": true,

  "npm.autoDetect": "off",
  "task.autoDetect": "off",
  "typescript.disableAutomaticTypeAcquisition": true
}
```

Add a project's own heavy directories to all three maps.

## sync.toml

Create it from the `rig-sync` configuration reference: `[git]` with the remote and branch, the
standard artifact groups, and one absolute `repo_path` per rig the project will dispatch to, each
under one of that rig's declared storage roots (SSH aliases, users, and keys stay in the user
registry). Add `[environment]` from `environment-sync`: `manager = "uv"`,
`name = "{{ENVIRONMENT_NAME}}"` (`.venv` unless the user names another), and the tokenized GPU
smoke command.

## Python environment

`pyproject.toml`: project metadata, exact `[tool.uv].required-version = "==X.Y.Z"`,
`python-preference = "managed"`, `dependencies = ["torch"]` (the fixed framework; later packages
come with `uv add`), and `[dependency-groups] dev = ["pytest>=8"]` from the start, since adding it
later changes the environment fingerprint on every rig. No build system: the project is never
installed into its environment. Commit an exact `X.Y.Z` `.python-version`. `uv.lock` is produced
by `uv lock` during the environment gate, never
hand-written. Copy `assets/environment.py` from the `environment-sync` skill's installed directory
to `code/common/environment.py` unchanged.

## .githooks/pre-commit (journal guard)

```bash
#!/usr/bin/env bash
# Blocks commits that change experiment-relevant files without touching JOURNAL.md.
set -euo pipefail
staged=$(git diff --cached --name-only)
if echo "$staged" | grep -qE '^(code|config|scripts|evaluations|visualizations)/'; then
  if ! echo "$staged" | grep -qx 'JOURNAL.md'; then
    echo "COMMIT BLOCKED: changes touch code/config/scripts/evaluations/visualizations" >&2
    echo "but JOURNAL.md is not part of the commit. Journal what happened first." >&2
    exit 1
  fi
fi
```

Install with: `chmod +x .githooks/pre-commit && git config core.hooksPath .githooks`

## scripts/ wave script (log-capture core)

The log path mirrors the script's own path under `logs/`; the working directory is the project
root and code runs from the wave worktree. The full template is in the `sweep-dispatch` skill,
`references/templates.md`.

```bash
#!/usr/bin/env bash
set -euo pipefail
PROJECT_ROOT="${SLURM_SUBMIT_DIR:-$PWD}"
cd "$PROJECT_ROOT"
WAVE_TREE="$PROJECT_ROOT/.waves/<wave_id>"
export RESEARCH_PROJECT_ROOT="$PROJECT_ROOT"
LOGDIR="logs/NNN_experiment/<run_id folder>/wave_<wave_id>"
mkdir -p "$LOGDIR"
HYDRA_ARGS=(<tokens produced by hydra_override_arg; one per override>)
.envs/<env_key>/bin/python "$WAVE_TREE/code/NNN_experiment/script.py" "${HYDRA_ARGS[@]}" 2>&1 \
  | tee "$LOGDIR/wave_${LANE_RIG}_gpu${LANE_GPUS:-job}-$(date +%Y%m%d-%H%M%S).log"
```


## code/common/paths.py

```python
"""Resolve project storage and tracked inputs, for the hub checkout and for wave worktrees.

Two roots exist. The *source root* holds the code being imported: the hub checkout
during development, or a wave's detached worktree (<project>/.waves/<wave_id>) when a
wave runs. The *project root* holds everything that is not source: .env, storage/,
checkpoints/, evaluations/, logs/. A wave script exports RESEARCH_PROJECT_ROOT so the
two resolve differently; without it they coincide.

.env keeps project-scoped storage relative (CACHE_DIR=storage/cache) so that one
file is correct on every rig: rig-sync already requires each rig's repo_path to
sit on that rig's large volume, so anything under the project root inherits that
guarantee without naming a mount point. Absolute values are left alone -- they
are a warned-about exception, not the shape to build against.

Use project_path() for every storage location an experiment reads or writes, and
source_path() for tracked inputs (checked-in tables, prompts, fixtures) so a wave
reads the exact revision it runs. Never build a storage path from os.getcwd() or
Path(__file__): the first silently writes elsewhere when launched from another
directory, the second writes into a wave worktree that is pruned later.
"""

import os
from pathlib import Path

_MARKER = "pyproject.toml"
ROOT_ENV = "RESEARCH_PROJECT_ROOT"


def source_root() -> Path:
    """The directory holding pyproject.toml, found by walking up from this file."""
    for candidate in Path(__file__).resolve().parents:
        if (candidate / _MARKER).is_file():
            return candidate
    raise RuntimeError(f"no {_MARKER} above {__file__}: cannot locate the source root")


def project_root() -> Path:
    """The shared project root: $RESEARCH_PROJECT_ROOT inside a wave, else the source root."""
    value = os.environ.get(ROOT_ENV)
    if not value:
        return source_root()
    root = Path(value)
    if not root.is_absolute() or not (root / ".git").exists():
        raise RuntimeError(f"{ROOT_ENV}={value!r} is not an absolute project root with .git")
    return root


def project_path(value: str | os.PathLike) -> Path:
    """An absolute storage path: value as-is when absolute, else under the project root."""
    path = Path(value)
    return path if path.is_absolute() else project_root() / path


def source_path(value: str | os.PathLike) -> Path:
    """A tracked input of the running revision, relative to the source root."""
    path = Path(value)
    if path.is_absolute():
        raise ValueError(f"source_path takes a repo-relative path, got {value!r}")
    return source_root() / path


def storage_path(var: str, default: str) -> Path:
    """Resolve an .env storage variable, falling back to its documented default."""
    return project_path(os.environ.get(var) or default)
```

Load `.env` from `project_root() / ".env"`. `RESEARCH_PROJECT_ROOT` support is what
`sweep-dispatch` checks for the wave-isolation contract.

## code/common/run_id.py

```python
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
```

## code/common/environment_smoke.py

The default `gpu_smoke` target in `sync.toml`: a real device operation through the locked torch.

```python
"""Bounded GPU smoke: prove the locked runtime can actually compute on a device."""

import sys


def main() -> int:
    import torch

    if not torch.cuda.is_available():
        print("gpu smoke FAIL: no CUDA device visible", file=sys.stderr)
        return 1

    device = torch.device("cuda:0")
    name = torch.cuda.get_device_name(device)
    a = torch.randn(256, 256, device=device)
    b = torch.randn(256, 256, device=device)
    result = (a @ b).sum().item()
    torch.cuda.synchronize()

    if result != result:  # NaN guard
        print(f"gpu smoke FAIL: NaN result on {name}", file=sys.stderr)
        return 1

    print(f"gpu smoke OK: {name}, torch {torch.__version__}, cuda {torch.version.cuda}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

## code/common/status.py

```python
"""Atomic per-run lifecycle and progress signaling."""

import datetime
import json
import os
import threading
import time
from numbers import Real
from pathlib import Path


class StatusWriter:
    """Own evaluations/NNN_exp/<run_id path>/.status.json for one run.

    Schema v3 adds ``rig`` (from LANE_RIG, which the claiming lane exports);
    readers treat a missing field in a v2 file as None.
    """

    def __init__(self, eval_dir, heartbeat_interval_s=60):
        if heartbeat_interval_s <= 0:
            raise ValueError("heartbeat_interval_s must be positive")
        self.path = Path(eval_dir) / ".status.json"
        self.heartbeat_interval_s = heartbeat_interval_s
        self.t0 = None
        self.status = {}
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = None

    def _now(self):
        return datetime.datetime.now().astimezone().isoformat(timespec="seconds")

    def _elapsed(self):
        return round(time.monotonic() - self.t0, 1)

    def _write_locked(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.status))
        tmp.replace(self.path)

    def _refresh_liveness_locked(self):
        self.status["heartbeat"] = self._now()
        self.status["elapsed_s"] = self._elapsed()
        self._write_locked()

    def _heartbeat_loop(self):
        while not self._stop.wait(self.heartbeat_interval_s):
            with self._lock:
                if self.status.get("state") != "running":
                    return
                self._refresh_liveness_locked()

    def __enter__(self):
        self.t0 = time.monotonic()
        started = self._now()
        with self._lock:
            self.status = {
                "schema_version": 3,
                "state": "running",
                "started": started,
                "ended": None,
                "elapsed_s": 0.0,
                "heartbeat": started,
                "progress": None,
                "progress_completed": None,
                "progress_total": None,
                "progress_unit": None,
                "wave_id": os.environ.get("WAVE_ID"),
                "gpu": os.environ.get("CUDA_VISIBLE_DEVICES"),
                "rig": os.environ.get("LANE_RIG"),
                "source_revision": os.environ.get("SOURCE_REVISION"),
                "source_tag": os.environ.get("SOURCE_TAG"),
                "environment_fingerprint": os.environ.get("ENVIRONMENT_FINGERPRINT"),
            }
            self._write_locked()
        self._thread = threading.Thread(
            target=self._heartbeat_loop,
            name="run-status-heartbeat",
            daemon=True,
        )
        self._thread.start()
        print(
            f"[status] RUN START {self.status['started']} "
            f"wave={self.status['wave_id']} rig={self.status['rig']} gpu={self.status['gpu']} "
            f"source={self.status['source_revision']} "
            f"environment={self.status['environment_fingerprint']}",
            flush=True,
        )
        return self

    def heartbeat(self, *, completed=None, total=None, unit=None):
        """Refresh liveness and optionally record machine-readable progress."""
        structured = completed is not None or total is not None or unit is not None
        if structured:
            if not isinstance(completed, Real) or isinstance(completed, bool):
                raise ValueError("completed must be numeric")
            if not isinstance(total, Real) or isinstance(total, bool) or total <= 0:
                raise ValueError("total must be a positive number")
            if completed < 0 or completed > total:
                raise ValueError("completed must be between zero and total")
            if not isinstance(unit, str) or not unit.strip():
                raise ValueError("unit must be a non-empty string")

        with self._lock:
            if structured:
                self.status.update(
                    progress=f"{unit} {completed:g}/{total:g}",
                    progress_completed=completed,
                    progress_total=total,
                    progress_unit=unit,
                )
            self._refresh_liveness_locked()

    def __exit__(self, exc_type, exc, tb):
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
        with self._lock:
            ended = self._now()
            self.status.update(
                state="failed" if exc_type else "done",
                ended=ended,
                heartbeat=ended,
                elapsed_s=self._elapsed(),
            )
            self._write_locked()
        print(
            f"[status] RUN END {self.status['ended']} "
            f"state={self.status['state']} elapsed={self.status['elapsed_s']}s",
            flush=True,
        )
        return False
```

Call `heartbeat(completed=<done>, total=<total>, unit=<label>)` after every completed training or
evaluation unit. A daemon thread refreshes liveness and elapsed time every 60 seconds, so a long
unit still looks alive.

## code/common/metrics.py

```python
"""Append-only per-run metric log: every tracked scalar, kept offline."""

import datetime
import json
import os
from pathlib import Path


class MetricsWriter:
    """Own evaluations/NNN_exp/<run_id path>/metrics.jsonl for one run.

    One JSON object per line: {"step": <int>, "time": <iso>, <key>: <number>, ...}.
    A fresh run truncates the file; a run that restored its training state
    appends, so steps it repeats after the restored checkpoint are logged twice
    and read_metrics() keeps the last value of each (step, key).
    """

    def __init__(self, eval_dir, *, resume=False):
        self.path = Path(eval_dir) / "metrics.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("a" if resume else "w", encoding="utf-8")

    def log(self, metrics, *, step):
        """Record one step's scalars; values are anything float() accepts (tensors included)."""
        if not isinstance(step, int) or isinstance(step, bool) or step < 0:
            raise ValueError("step must be a non-negative int")
        if not metrics:
            raise ValueError("metrics must not be empty")
        record = {
            "step": step,
            "time": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        }
        for key, value in metrics.items():
            if not isinstance(key, str) or not key or key in record:
                raise ValueError(f"bad metric key {key!r}")
            if isinstance(value, bool):
                raise ValueError(f"metric {key} is a bool, not a number")
            record[key] = value if isinstance(value, int) else float(value)
        self._file.write(json.dumps(record) + "\n")
        self._file.flush()
        os.fsync(self._file.fileno())

    def close(self):
        self._file.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False


def read_metrics(eval_dir) -> dict:
    """{key: [(step, value), ...]} sorted by step; the last value of a repeated step wins.

    A torn last line (the run was killed mid-write) is skipped; NaN and inf are kept.
    """
    path = Path(eval_dir) / "metrics.jsonl"
    latest: dict = {}
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    for index, line in enumerate(lines):
        try:
            record = json.loads(line)
        except ValueError:
            if index == len(lines) - 1:
                break
            raise
        step = record["step"]
        for key, value in record.items():
            if key not in ("step", "time"):
                latest.setdefault(key, {})[step] = value
    return {key: sorted(values.items()) for key, values in latest.items()}
```

Open one `MetricsWriter(<eval run dir>, resume=<training state was restored>)` per entrypoint,
after `guard_run_config` and inside `StatusWriter`, and call `log({"train/loss": ..., ...},
step=<global step>)` wherever a metric is computed. Plotters read it with `read_metrics`.
