# Scaffold templates

Substitute `{{PROJECT_NAME}}` / `{{PROJECT_DESCRIPTION}}` and adjust with the user.
Create `.gitkeep` in each otherwise-empty taxonomy directory before the initial
commit; the `.gitignore` exceptions below preserve placeholders in ignored trees.

## README.md

```markdown
# {{PROJECT_NAME}}

{{PROJECT_DESCRIPTION}}

## Structure

| path | purpose |
|---|---|
| `checkpoints/` | checkpoints produced by trainings/finetunings |
| `code/` | experiment code (hydra-configured) |
| `config/` | hydra yaml configs for `code/` |
| `evaluations/` | data produced by running experiments (+ `.status.json` markers) |
| `logs/` | run logs — tee'd stdout+stderr per run, wandb files (gitignored, rig-local) |
| `plots/` | plots produced by `visualizations/` |
| `scripts/` | shell scripts that launch experiments/sweeps |
| `tests/` | optional pytest unit tests, mirroring `code/` and `visualizations/` inside itself |
| `visualizations/` | plotting code (argparse), reads `evaluations/`, writes `plots/` |
| `shitpads/` | temp scratch space (gitignored, rig-local) |
| `references/` | papers/codebases for reference (gitignored, rig-synced) |
| `program/` | the engineering execution agreement |

Experiments are named `NNN_experiment_name` and mirrored across the folders above — except
`tests/`, which mirrors the source root first: tests for `<root>/<path>/<stem>.py` live at
`tests/<root>/<path>/<stem>/test_*.py`, and are optional per script.
Each run is identified by its **run_id**. The experiment's `.py` is authoritative for both its
ordered identity params (`RUN_ID_PARAMS`) and its literal output-path layout pin
(`RUN_ID_PATH_LAYOUT = "segments-v1"` with its user-approved `RUN_ID_SEGMENTS`; legacy
experiments keep `"nested"|"collapsed-v1"|"hashed-v1"`).

## Tracking

- **`program/00-execution-agreement.md`** — explicit engineering mode, envelope, destinations,
  and protected choices.
- **`EXPERIMENTS.md`** — state: which runs exist (todo/inpr/done/failed), in which wave, on
  which rig and GPU.
- **`JOURNAL.md`** — story: prose log of what was done, why, and what was learned.

## Setup

1. `cp .env.example .env` and fill in the secrets/paths.
2. Use `environment-sync` to provision the exact `uv.lock` + `.python-version` environment and
   run the project GPU smoke.
3. Configure the approved GitHub remote/dispatch branch and `sync.toml`, then use `rig-sync` and
   `environment-sync` to prepare and verify approved rig roots.

## Running

Runs are launched in **waves** (one dispatch decision, id `YYYYMMDD-HHMMSS`) via
`scripts/NNN_experiment_name/<flat run_id>/wave_<wave_id>/` — a `README.md` saying what the
wave is, plus one self-contained `wave_<rig>_gpu<ids>.sh` per run. Each wave is smoke-tested,
committed, and tagged `wave--<wave_id>`; every rig verifies that exact commit before execution.
Every run also verifies and records the wave's approved environment fingerprint. `logs/` mirrors
the scripts tree.
```

## AGENTS.md

```markdown
# {{PROJECT_NAME}} — project notes

<!-- Thin by design: generic research conventions come from the installed `research`
     skill bundle. Only project-specific facts live here. -->

## What this project is

{{PROJECT_DESCRIPTION}}

## Conventions come from this project and the skills

Follow this project's own files and the `research` skill directives. Never list, read, or copy
from another project on disk to decide layout, file contents, dependencies, or defaults, and
never treat a similar-looking project as a template or precedent. Where a convention is not
stated, ask rather than imitate. External source code enters only through
`integrate-reference-code`, from a reference the user identifies explicitly or places in
`references/`.

## Awareness map

- `EXPERIMENTS.md` — run tracking (state). Read it to know what exists/ran.
- `JOURNAL.md` — prose history (story). Read it to know why things were done.
- `program/00-execution-agreement.md` — authoritative manual/auto mode and approval envelope.
- `README.md` — structure + setup.
- Deepen further by reading `config/NNN_*/` and the experiment's `.py`: `RUN_ID_PARAMS` is the
  authoritative ordered run identity, `RUN_ID_PATH_LAYOUT` is the authoritative literal
  output-path pin (`segments-v1`; legacy `nested`, `collapsed-v1`, or `hashed-v1`), and
  `RUN_ID_SEGMENTS` is the approved split into explicit params and named hash groups.

## Project-specific quirks

<!-- datasets, unusual constraints, deviations from the standard structure -->
```

## CLAUDE.md

`AGENTS.md` is the canonical project doc. `CLAUDE.md` only redirects to it, so Claude Code loads
the same notes without a second copy that can drift. Scaffold both; never duplicate the content.

```markdown
# {{PROJECT_NAME}}

Project notes live in `AGENTS.md`. Read @AGENTS.md before doing anything.
```

## EXPERIMENTS.md (initial)

```markdown
# Experiments

<!-- state only; the story lives in JOURNAL.md. One section per NNN_experiment; each section
     header mirrors the experiment .py's ordered RUN_ID_PARAMS and exact literal pin,
     `RUN_ID_PATH_LAYOUT: segments-v1` (plus `RUN_ID_SEGMENTS` and
     `run_id map: evaluations/<experiment_path>/RUN_ID_MAP.json`), or a legacy
     `RUN_ID_PATH_LAYOUT: nested`, `RUN_ID_PATH_LAYOUT: collapsed-v1`, or `RUN_ID_PATH_LAYOUT: hashed-v1`
     (the last also names the map).
     Run tables, one row per (run, wave); segments-v1 adds `run_id` and one column per hash group:
     | <run_id params...> | run_id | <group hashes...> | wave | rig | gpu | status | started | progress | eta | ended | elapsed | notes |
     (schema: experiments-tracking skill) -->
```

## JOURNAL.md (initial)

```markdown
# Journal

<!-- append-only prose, chronological; entries under `## YYYY-MM-DD, HH:MM — title` -->
```

## program/00-execution-agreement.md (initial)

```markdown
# Execution Agreement

agreement_version: a1
approved: no
engineering_mode: <manual|auto — required>

## Engineering envelope

- Active request:
- Repository / branch / approved remotes:
- Rigs / GPUs:
- Compute / time / monetary ceilings:
- Approved tracking and publication destinations:

## Decision ownership

- Hard constraints:
- Soft preferences:
- Delegable fields:
- Protected choices:
  - Run-output path layout: at every run_id election, run the `segments-v1` rendering dialogue
    with the user: show the complete all-explicit checkpoint and evaluation templates with byte
    counts and overflows; ask which params are hashed; propose named hash groups following the
    Hydra config tree for the user to accept, rename, or regroup; ask where every item goes and
    which items share a directory; show the rendered paths and preflight them again. `plots/` is
    not run-scoped and is out of scope for this choice. Neither automatic mode may decide any step.
    Record one experiment-wide RUN_ID_PATH_LAYOUT plus RUN_ID_SEGMENTS and use them for every
    run-scoped surface. Legacy `nested`, `collapsed-v1`, and `hashed-v1` pins are never offered.
    Offer this choice only before any checkpoint, evaluation, or wave README/script exists. After
    that boundary, preserve the checked-in renderer and literal pin forever, with its segment spec;
    never propose or perform an in-place layout change, including a regrouping or a move from a
    legacy pin. Another layout requires a new numbered sub-experiment.
  - Plot communication: approve each exact title, its arrangement across title lines, the visible
    metric explanation, its in-figure placement, the interaction affordances, and the complete
    project-relative `plots/` export path including its leaf filename before every plotting-code
    edit. Every plot is one self-contained interactive HTML file at
    `plots/<experiment_path>/<script_stem>/<leaf>.html`; run_id selection lives inside the file, so
    no plot path carries run_id segments and plot communication never touches RUN_ID_PATH_LAYOUT.
    The title states every selected RUN_ID param; how those params are arranged across title lines
    is a per-plot question the user answers each time, never chosen automatically and never reused
    from another plot's arrangement. Only the user may approve these protected path choices;
    engineering-auto cannot bypass them. Rewriting a leaf whole on rerun is expected and needs no
    fresh approval while the path is unchanged; reject any destination that collides with a
    different script's output. If runtime values require a path template, the user
    must explicitly approve before the plotting-code edit the complete project-relative `plots/`
    path template, including its leaf-filename template and every identified placeholder. Before
    rendering, show every fully resolved concrete export path, including its leaf filename, and
    wait for explicit user approval. An approved template does not approve any concrete
    destination. A new or changed resolved path always reopens approval, even when it conforms to
    the approved template; do not render before that approval. An unchanged-code rerender may
    reuse approval only when every resolved concrete export path is byte-for-byte identical to the
    previously explicitly approved concrete path.
  - Additional project-specific choices:

## Approval

- Owner:
- Approved scope:
- Latest material revision:
```

## .env.example (committed key mirror)

```bash
WANDB_API_KEY=

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

Render this file directly; do not obtain it by copying `.env.example`. Keep secret values
empty unless the user explicitly supplies them through an authorized secret source;
never copy or commit a token from another project's `.env`.

**Never put shared cache paths in `.env`.** A project `.env` is loaded *after* the shell
environment and silently overrides it, so a single `HF_HOME=` line defeats a correctly configured
rig — and it does so invisibly, with the download landing on whatever volume that line names. The
rig's caches are machine-level configuration and belong in the machine's environment, set where
**every** shell sees it (a login-independent file such as `~/.zshenv`, not an interactive-only
`~/.bashrc`/`~/.zshrc`, because dispatch runs under `nohup`, `ssh <cmd>`, and generated scripts).

Two further reasons never to hardcode them here: the absolute path is **rig-specific**, so a value
that is right on one machine points at a nonexistent mount on the next; and the natural repair when
it does not exist is to retarget it at `$HOME`, which on a quota'd rig is the small volume. That
sequence is exactly how a wave dies mid-checkpoint with `Disk quota exceeded`.

Keep only what is genuinely per-project, and keep project-scoped storage **relative to the project
root**. `rig-sync doctor` already requires each rig's `repo_path` to resolve under one of that
rig's declared storage roots, so a relative path lands on the project's large volume everywhere, without this
file naming a mount point that is true on exactly one machine.

```bash
WANDB_API_KEY=

HF_TOKEN=
HUGGING_FACE_HUB_TOKEN=

OPENCLIP_CACHE_DIR=storage/openclip
CACHE_DIR=storage/cache

TORCH_NUM_WORKERS=16
```

Written this way the file contains nothing rig-specific, which is the point: `rig-sync push-env`
copies this one file to every peer rather than rendering a different one per machine, and adding a
rig later needs no edit here at all.

Do not reach for an absolute path. If a project genuinely needs one that neither the machine
environment nor the project root provides, derive it from the project's storage root on that machine
(`rig-sync` → `references/configuration.md`), accept that the file is now rig-specific and must be
maintained per rig, and record why in the journal. `doctor` warns on every absolute value here.

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

Adjust with the user: they may want evaluations/ (small jsons) or plots/ committed.
`.status.json` markers live inside evaluations/ and follow whatever is decided for it.

Every comment above sits on its own line, and must. `.gitignore` has no inline comments: a
trailing `#` note becomes part of the pattern, so `outputs/  # hydra run dir` silently stops
ignoring `outputs/` and matches a path literally named that instead. Never annotate a pattern
inline, here or in a scaffolded project.

## .vscode/settings.json

Committed, not machine-local. The taxonomy guarantees that a working project's artifact trees
dwarf its source: `plots/`, `checkpoints/`, `evaluations/`, `logs/`, `shitpads/`, `references/`,
and `{{ENVIRONMENT_NAME}}/` routinely hold five to six figures of files against a few hundred
tracked ones. `.gitignore` does not hide any of that from an editor. Only the exclude maps below
do, so without this file every checkout of every Research 2.0 project installs recursive file
watchers over the whole artifact tree, full-text-searches it, and language-server-indexes the
virtual environment. Scaffold it at init; retrofitting it later is a step nothing prompts for.

Keep artifact trees **visible** in the file explorer — they are browsed by hand, `plots/` most of
all — and exclude them from the watcher, from search, and from Python analysis. State the
consequence in the file: unwatched directories do not auto-refresh, so newly written artifacts
appear only after an explicit explorer refresh. That is the deliberate trade, and a scaffold that
hides it produces a user who thinks a run wrote nothing.

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
    "**/__pycache__/**": true,
    "**/wandb/**": true
  },

  // logs/wandb/ holds symlinks (latest-run, debug logs) that can walk the
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

Substitute `{{ENVIRONMENT_NAME}}` everywhere above, including inside
`python.defaultInterpreterPath`. Add a project's own heavy directories to all three maps when the
user declares any beyond the taxonomy; never drop a taxonomy directory from them.

## sync.toml

Create this from the `rig-sync` skill's configuration reference. The user registry
(`~/.config/rigsync/machines.toml`) is a prerequisite: a rig with no entry there cannot be declared
here at all.

Declare `[git]` with the approved remote/branch, the standard artifact groups, and one absolute
`repo_path` per intended rig — each under one of that rig's declared storage roots; do not put SSH aliases,
ports, users, or keys here. Nothing derives `repo_path`, so declare every rig the project will ever
dispatch to now: adding one later is a hand edit that no gate asks for. Run `rig-sync check-paths`
before any remote step — it is the only check that catches a path off the rig's large volume before
something has been cloned into it. Add `[environment]` from
`environment-sync` with `manager = "uv"`, the user-chosen single-directory
`name = "{{ENVIRONMENT_NAME}}"`, and the approved tokenized GPU smoke command. Ask for this name
before creating the environment; suggest `.venv` only as an option and never infer a default. Run
both skills' doctor checks before remote dispatch.

## Python environment

Create `pyproject.toml` with the project metadata, dependencies, exact
`[tool.uv].required-version = "==X.Y.Z"`, and `python-preference = "managed"`. Include pytest as a
dev dependency (`[dependency-groups] dev = ["pytest>=8"]`) so the optional `tests/` tree is
runnable from the start. Add it now, not later: it participates in the installed-environment
fingerprint that `environment-sync` uses as its cross-rig parity gate, so introducing it
mid-project forces a re-provision on every rig. Commit an exact
`X.Y.Z` `.python-version`. `uv.lock` is produced by `uv lock` during the environment gate, never
hand-written; commit it once that gate has run. Copy
`assets/environment.py` from the environment-sync skill's own installed directory to
`code/common/environment.py`; do not rewrite its
fingerprint algorithm per project. Keep `{{ENVIRONMENT_NAME}}/`, `.rigsync_cache/`, `.waves/`, and
`.envs/` ignored. Do not declare a build system: the project is never installed into its
environment, so one lock-keyed wave environment can serve every worktree with the same lock.

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

Canonical pattern every launch script follows — the log path is the **mirror of the script's
own path under `logs/`**; the working directory is the project root, and code runs from the
wave's worktree `.waves/<wave_id>/` with the wave's keyed environment. Full self-contained template with the self-guard, the
`CUDA_VISIBLE_DEVICES`/`WAVE_ID` exports and the `.status.json` failed-fallback:
sweep-dispatch skill, `references/templates.md`.

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
  | tee "$LOGDIR/wave_<rig>_gpu<ids>-$(date +%Y%m%d-%H%M%S).log"
```

`pipefail` makes either the Python process or `tee` failure visible. The full wave template also
captures both pipeline statuses so Python remains authoritative when both fail.

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

`.env` is loaded from `project_root() / ".env"`, so a wave reads the machine's real `.env` rather
than looking for one inside its worktree. `RESEARCH_PROJECT_ROOT` support is the marker of the
**wave-isolation contract** that `sweep-dispatch` checks before any dispatch.

## code/common/run_id.py

```python
"""Shared run_id helpers.

Each experiment declares, in its own .py, the authoritative ordered list of config
params that uniquely identify a run, its experiment-wide path layout, and (for
segments-v1, the only layout offered to new experiments) the user-approved
segment spec:

    RUN_ID_PARAMS = ["model", "lr", "wd", "beta1", "seed"]
    RUN_ID_PATH_LAYOUT = "segments-v1"
    RUN_ID_SEGMENTS = [                      # one tuple = one directory component
        ("model",),
        ({"optim_params": ("lr", "wd", "beta1")}, "seed"),
    ]

A segment item is either a param name (rendered explicitly as ``key=value``) or a
one-key dict naming a hash group (rendered as ``name=<16 hex>``). That renders
``model=mlp/optim_params=3f0c...,seed=0``. The legacy literals ``nested``,
``collapsed-v1`` and ``hashed-v1`` stay supported for experiments already pinned
to them and are never offered to new experiments.

Use these helpers for ALL artifact paths and run names. Never hand-build them.

Scripts must call guard_run_config() before writing ANY artifact (and before the
StatusWriter starts): it hard-fails on run_id collisions — same run_id, different
full config — which happen when a param was added to the config but not to
RUN_ID_PARAMS (schema evolution rules: conventions.md), and on hash collisions.
One guard at the evaluations/ run dir suffices: checkpoints/ shares the same
run_id. plots/ is not run-scoped and needs no guard.

Scripts that use wandb must call wandb.init(config=wandb_config(cfg)) — the whole
resolved config, never a subset.
"""

import hashlib
import json
import os
import shlex
from pathlib import Path
from urllib.parse import quote

RUN_ID_HASH_PREFIX = "rid-"
RUN_ID_HASH_HEX = 16
RUN_ID_MAP_NAME = "RUN_ID_MAP.json"
SEGMENTS_LAYOUT = "segments-v1"
LEGACY_LAYOUTS = ("nested", "collapsed-v1", "hashed-v1")


def _run_id_component(value) -> str:
    """Filesystem-safe, reversible rendering for a run_id key or value."""
    if isinstance(value, (dict, list, tuple)):
        value = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return quote(str(value), safe="-._~")


def run_id_dict(cfg, params):
    """run_id as an ordered dict {param: value}."""
    return {p: cfg[p] for p in params}


def _run_id_pairs(cfg, params) -> list[str]:
    """Ordered, percent-encoded ``key=value`` pairs shared by every rendering."""
    return [f"{_run_id_component(p)}={_run_id_component(cfg[p])}" for p in params]


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


def validate_run_id_segments(params, segments) -> None:
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
            if not name or _run_id_component(name) != name:
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


def run_id_group_hash(cfg, name, group_params) -> str:
    """16 hex of sha256 over the scheme, the group name, and its ordered pairs.

    Only the group's own params enter the hash, so the same group values give
    the same hash in every run. Collisions are never assumed away: the
    per-directory record, RUN_ID_MAP.json, and check_run_id_plan() fail hard.
    """
    canonical = "\0".join(
        (SEGMENTS_LAYOUT, name, ",".join(_run_id_pairs(cfg, group_params)))
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:RUN_ID_HASH_HEX]


def run_id_groups(cfg, segments) -> dict:
    """{group_name: {"hash": ..., "params": {param: value}}} for one run."""
    return {
        item[1]: {
            "hash": run_id_group_hash(cfg, item[1], item[2]),
            "params": run_id_dict(cfg, item[2]),
        }
        for items in _segment_items(segments)
        for item in items
        if item[0] == "group"
    }


def run_id_path(cfg, params, *, layout="nested", segments=None) -> Path:
    """Run-output path in the experiment's authoritative layout.

    ``segments-v1`` (the only layout for new experiments) renders one directory
    per segment; items inside a segment are joined with ``,``; a param renders as
    its percent-encoded ``key=value`` and a hash group as ``name=<16 hex>``.

    Legacy pins, kept only for experiments that already recorded them:
    ``nested`` renders ``model=mlp/lr=0.001/seed=0``; ``collapsed-v1`` renders
    the same pairs in one component ``model=mlp,lr=0.001,seed=0``; ``hashed-v1``
    renders one component ``rid-<16 hex>`` (sha256 of the collapsed-v1 string).

    Use one layout consistently under checkpoints/ and evaluations/. plots/ does
    not use run_id paths at all. Never truncate, hash outside the recorded spec,
    or otherwise rewrite pairs to evade a collision or filesystem limit.
    """
    if layout == SEGMENTS_LAYOUT:
        if segments is None:
            raise ValueError("segments-v1 requires the experiment's RUN_ID_SEGMENTS")
        validate_run_id_segments(params, segments)
        components = []
        for items in _segment_items(segments):
            rendered = []
            for item in items:
                if item[0] == "param":
                    rendered.extend(_run_id_pairs(cfg, [item[1]]))
                else:
                    rendered.append(f"{item[1]}={run_id_group_hash(cfg, item[1], item[2])}")
            components.append(",".join(rendered))
        return Path(*components)
    if segments is not None:
        raise ValueError(f"RUN_ID_SEGMENTS only applies to segments-v1, not {layout!r}")
    pairs = _run_id_pairs(cfg, params)
    if layout == "nested":
        return Path(*pairs)
    if layout == "collapsed-v1":
        return Path(",".join(pairs))
    if layout == "hashed-v1":
        return Path(run_id_hash(cfg, params))
    raise ValueError(
        f"invalid run_id path layout {layout!r}; "
        "expected 'segments-v1', 'nested', 'collapsed-v1', or 'hashed-v1'"
    )


def run_id_name(cfg, params, *, layout="nested", segments=None) -> str:
    """The one string identity of a run.

    Under segments-v1 it is the rendered run_id path in POSIX form, used for the
    wandb run name, the EXPERIMENTS.md ``run_id`` column, and the Slurm job-name
    suffix, so every surface matches the directories on disk. Legacy layouts
    keep ``run_id_flat``.
    """
    if layout == SEGMENTS_LAYOUT:
        return run_id_path(cfg, params, layout=layout, segments=segments).as_posix()
    return run_id_flat(cfg, params)


def run_id_hash(cfg, params) -> str:
    """The legacy hashed-v1 component: ``rid-`` + 16 hex of sha256(run_id_flat)."""
    digest = hashlib.sha256(run_id_flat(cfg, params).encode("utf-8")).hexdigest()
    return RUN_ID_HASH_PREFIX + digest[:RUN_ID_HASH_HEX]


def _segments_json(segments):
    return [
        [item[1] if item[0] == "param" else {item[1]: list(item[2])} for item in items]
        for items in _segment_items(segments)
    ]


def run_id_record(cfg, params, *, layout="hashed-v1", segments=None) -> dict:
    """The .run_id.json payload: the full two-way mapping for one run."""
    if layout == SEGMENTS_LAYOUT:
        return {
            "layout": SEGMENTS_LAYOUT,
            "segments": _segments_json(segments),
            "path": run_id_name(cfg, params, layout=layout, segments=segments),
            "run_id_flat": run_id_flat(cfg, params),
            "run_id": run_id_dict(cfg, params),
            "groups": run_id_groups(cfg, segments),
        }
    return {
        "layout": "hashed-v1",
        "hash": run_id_hash(cfg, params),
        "run_id_flat": run_id_flat(cfg, params),
        "run_id": run_id_dict(cfg, params),
    }


def _merge_segment_record(index, record, where) -> None:
    """Fold one segments-v1 record into a map index; raise on any collision."""
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


def _read_run_id_map(experiment_root: Path) -> dict:
    map_file = experiment_root / RUN_ID_MAP_NAME
    if map_file.exists():
        payload = json.loads(map_file.read_text())
        if payload.get("layout") == SEGMENTS_LAYOUT:
            return payload
    return {"layout": SEGMENTS_LAYOUT, "by_path": {}, "by_run_id_flat": {}, "groups": {}}


def write_run_id_map(experiment_root: Path, *, layout="hashed-v1") -> Path:
    """Regenerate <experiment_root>/RUN_ID_MAP.json from every run's .run_id.json.

    Derived index only, safe to regenerate on any rig. Legacy hashed-v1:
    ``by_hash`` maps hash -> {run_id_flat, run_id}; ``by_run_id_flat`` maps
    flat -> hash. segments-v1: ``by_path`` maps rendered path -> run_id_flat,
    ``by_run_id_flat`` the reverse, and ``groups`` maps group -> hash -> params;
    a hash or path claimed by two different identities raises.
    """
    if layout == SEGMENTS_LAYOUT:
        payload = {"layout": SEGMENTS_LAYOUT, "by_path": {}, "by_run_id_flat": {}, "groups": {}}
        for record_file in sorted(experiment_root.glob("**/.run_id.json")):
            record = json.loads(record_file.read_text())
            rel = record_file.parent.relative_to(experiment_root).as_posix()
            if record.get("layout") != SEGMENTS_LAYOUT or record["path"] != rel:
                raise RuntimeError(
                    f"{record_file} does not match segments-v1 path {rel!r}; mixed or "
                    "moved run directories block the map"
                )
            _merge_segment_record(payload, record, record_file)
    else:
        by_hash, by_flat = {}, {}
        for record_file in sorted(experiment_root.glob("*/.run_id.json")):
            record = json.loads(record_file.read_text())
            by_hash[record["hash"]] = {
                "run_id_flat": record["run_id_flat"],
                "run_id": record["run_id"],
            }
            by_flat[record["run_id_flat"]] = record["hash"]
        payload = {"layout": "hashed-v1", "by_hash": by_hash, "by_run_id_flat": by_flat}
    map_file = experiment_root / RUN_ID_MAP_NAME
    tmp = map_file.with_suffix(f".json.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True))
    os.replace(tmp, map_file)
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


def check_run_id_plan(cfgs, params, segments, experiment_root: Path, *, path_roots=()) -> list[Path]:
    """Pre-dispatch gate for a segments-v1 wave; returns the planned run paths.

    Preflights every planned path under experiment_root and each extra root
    (e.g. the experiment's checkpoints/ dir), and fails on any group-hash or
    path collision within the plan or against the existing RUN_ID_MAP.json, and
    on a run planned twice.
    """
    validate_run_id_segments(params, segments)
    index = _read_run_id_map(experiment_root)
    planned, paths = set(), []
    for cfg in cfgs:
        record = run_id_record(cfg, params, layout=SEGMENTS_LAYOUT, segments=segments)
        if record["run_id_flat"] in planned:
            raise RuntimeError(f"run {record['run_id_flat']!r} is planned twice")
        planned.add(record["run_id_flat"])
        _merge_segment_record(index, record, "the planned wave")
        rel = Path(record["path"])
        for root in (experiment_root, *path_roots):
            preflight_run_id_path(root, rel)
        paths.append(rel)
    return paths


def run_id_flat(cfg, params) -> str:
    """Flat form, e.g. 'model=mlp,lr=0.001,seed=0'.

    The complete, lossless identity string: recorded in .run_id.json and
    RUN_ID_MAP.json under every layout. Legacy layouts also use it for scripts/
    and logs/ run-folder names, wandb run names, and EXPERIMENTS.md rows;
    segments-v1 uses run_id_path()/run_id_name() there instead.
    """
    return ",".join(_run_id_pairs(cfg, params))


def hydra_override_arg(param, value) -> str:
    """One shell-safe `param=value` token for generated wave scripts."""
    return shlex.quote(f"{param}={value}")


def resolved_config(cfg) -> dict:
    """The run's ENTIRE config, resolved and json-normalized.

    Single source of truth for "the full config of this run", wherever it is
    recorded: the .run_config.json snapshot AND the wandb run config are both
    built from this, so the two can never disagree. Do not resolve a config
    anywhere else; do not record a hand-picked subset anywhere.
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


def wandb_config(cfg) -> dict:
    """Payload for wandb.init(config=...): whole config + wave provenance.

    Provenance sits in its own "provenance" namespace and is read from the env
    vars the wave script exports (same set StatusWriter records), NOT from config
    params: they must stay out of resolved_config()/.run_config.json, or every
    relaunch in a new wave or on another GPU would trip the run_id-collision hard
    error. In wandb they are pure gain — they make runs filterable by tested
    commit, rig/GPU, and environment fingerprint, not just hyperparameters.
    """
    resolved = resolved_config(cfg)
    if "provenance" in resolved:
        raise RuntimeError(
            "config has a top-level 'provenance' key, which would be shadowed by "
            "wave provenance in the wandb run config — rename the config key."
        )
    resolved["provenance"] = {
        "wave_id": os.environ.get("WAVE_ID"),
        "gpu": os.environ.get("CUDA_VISIBLE_DEVICES"),
        "source_revision": os.environ.get("SOURCE_REVISION"),
        "source_tag": os.environ.get("SOURCE_TAG"),
        "environment_fingerprint": os.environ.get("ENVIRONMENT_FINGERPRINT"),
    }
    return resolved


def guard_run_config(
    cfg, params, run_dir: Path, *, layout="nested", segments=None, experiment_root=None
) -> None:
    """Refuse to reuse a run dir whose config differs from the current one.

    Writes the full resolved config to <run_dir>/.run_config.json on first run.
    On later runs with the same run_id, hard-fails if ANY param differs — that is
    a run_id collision: a param changed that is not in RUN_ID_PARAMS. Same-config
    reruns (resume/retry) pass. Call BEFORE writing any artifact.

    Under ``segments-v1`` (requires segments and experiment_root) it asserts that
    run_dir is experiment_root / run_id_path(...), writes <run_dir>/.run_id.json,
    hard-fails if an existing record there names a different run_id_flat, and
    regenerates RUN_ID_MAP.json, which hard-fails on any group-hash collision.
    Legacy ``hashed-v1`` keeps its record/map behavior next to the run dirs.
    """
    resolved = resolved_config(cfg)
    if layout == SEGMENTS_LAYOUT:
        if experiment_root is None:
            raise ValueError("segments-v1 guard requires experiment_root")
        expected = Path(experiment_root) / run_id_path(
            cfg, params, layout=layout, segments=segments
        )
        if Path(run_dir) != expected:
            raise RuntimeError(f"run dir {run_dir} is not the segments-v1 path {expected}")
        record = run_id_record(cfg, params, layout=layout, segments=segments)
        record_file = expected / ".run_id.json"
        if record_file.exists():
            existing = json.loads(record_file.read_text())
            if existing["run_id_flat"] != record["run_id_flat"]:
                raise RuntimeError(
                    f"segments-v1 hash collision at {run_dir}: {record['path']} already maps "
                    f"to {existing['run_id_flat']!r}, not {record['run_id_flat']!r}. Stop and "
                    "re-elect the segments in a new numbered sub-experiment."
                )
        else:
            index = _read_run_id_map(Path(experiment_root))
            _merge_segment_record(index, record, run_dir)
            expected.mkdir(parents=True, exist_ok=True)
            record_file.write_text(json.dumps(record, indent=2, sort_keys=True))
        write_run_id_map(Path(experiment_root), layout=layout)
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
            record_file.write_text(json.dumps(record, indent=2, sort_keys=True))
        write_run_id_map(run_dir.parent)
    snapshot_file = run_dir / ".run_config.json"
    if snapshot_file.exists():
        snapshot = json.loads(snapshot_file.read_text())
        missing = "<MISSING>"
        diffs = {
            k: (
                snapshot[k] if k in snapshot else missing,
                resolved[k] if k in resolved else missing,
            )
            for k in sorted(set(snapshot) | set(resolved))
            if k not in snapshot or k not in resolved or snapshot[k] != resolved[k]
        }
        if diffs:
            lines = "\n".join(f"  {k}: old={old!r} new={new!r}" for k, (old, new) in diffs.items())
            raise RuntimeError(
                f"run_id collision at {run_dir}: same run_id "
                f"({run_id_flat(cfg, params)}) but the config differs:\n{lines}\n"
                "Re-elect RUN_ID_PARAMS only before any output or wave record exists; "
                "otherwise create a new numbered sub-experiment. See conventions.md, "
                "'run_id schema evolution'."
            )
    else:
        run_dir.mkdir(parents=True, exist_ok=True)
        snapshot_file.write_text(json.dumps(resolved, indent=2, sort_keys=True, default=str))
```

## code/common/environment_smoke.py

This is the default `gpu_smoke` target in `sync.toml`. It must perform a real device operation
through the project's locked framework, so a rig that imports but cannot compute fails the gate.
Adapt the framework call when the project is not torch-based, but keep it bounded and non-zero on
failure.

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
    """Own evaluations/NNN_exp/<run_id path>/.status.json for one run."""

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
                "schema_version": 2,
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
            f"wave={self.status['wave_id']} gpu={self.status['gpu']} "
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
evaluation unit. The helper also refreshes liveness and live elapsed time every 60 seconds in a
daemon thread, so a unit that lasts longer than the launch chat's reporting cadence is still
distinguishable from a dead process. Display-only progress is unsupported.
