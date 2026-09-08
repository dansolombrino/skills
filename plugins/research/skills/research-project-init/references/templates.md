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
(`RUN_ID_PATH_LAYOUT = "nested"|"collapsed-v1"|"hashed-v1"`).

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
  authoritative ordered run identity and `RUN_ID_PATH_LAYOUT` is the authoritative literal
  `nested` or `collapsed-v1` output-path pin.

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
     `RUN_ID_PATH_LAYOUT: nested`, `RUN_ID_PATH_LAYOUT: collapsed-v1`, or `RUN_ID_PATH_LAYOUT: hashed-v1`
     (the last also names `run_id map: evaluations/<experiment_path>/RUN_ID_MAP.json`).
     Run tables, one row per (run, wave):
     | <run_id params...> | wave | rig | gpu | status | started | progress | eta | ended | elapsed | notes |
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
  - Run-output path layout: at experiment design, first show the complete `nested` checkpoint and
    evaluation templates through their leaf names; `plots/` is not run-scoped and is out of scope
    for this choice. Immediately afterward,
    when at least two ordered RUN_ID_PARAMS are eligible, show exactly one separately labeled
    `collapsed-v1` alternative; show none otherwise. When an applicable fixed-param list has zero
    or one item, show its byte-identical `nested`/`collapsed-v1` path once, not as a separate
    alternative; label it as both layouts' rendering and state the experiment's pinned literal.
    That path is approvable under the pin. Show a `hashed-v1` alternative only when both other
    renderings overflow a filesystem limit or the user asks. Wait for explicit user layout selection;
    neither automatic mode may select `collapsed-v1` or `hashed-v1`. Record one experiment-wide RUN_ID_PATH_LAYOUT and use it
    for both artifact surfaces. Offer this choice only before any checkpoint, evaluation, or wave
    README/script exists. After that boundary, preserve the checked-in
    renderer and literal pin forever; never propose or perform an in-place layout change, including
    a byte-identical zero/one-param change. Another layout requires a new numbered sub-experiment.
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
root**. `rig-sync doctor` already requires each rig's `repo_path` to resolve under that rig's
declared `storage_root`, so a relative path lands on the large volume everywhere, without this
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
environment nor the project root provides, derive it from that machine's declared `storage_root`
(`rig-sync` → `references/configuration.md`), accept that the file is now rig-specific and must be
maintained per rig, and record why in the journal. `doctor` warns on every absolute value here.

## .gitignore

```gitignore
.env
{{ENVIRONMENT_NAME}}/
.rigsync_cache/
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
`repo_path` per intended rig — each on that rig's declared `storage_root`; do not put SSH aliases,
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
fingerprint algorithm per project. Keep `{{ENVIRONMENT_NAME}}/` and `.rigsync_cache/` ignored.

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
own path under `logs/`**. Full self-contained template with the self-guard, the
`CUDA_VISIBLE_DEVICES`/`WAVE_ID` exports and the `.status.json` failed-fallback:
sweep-dispatch skill, `references/templates.md`.

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../../../.." || exit 1  # to project root (depth varies with nesting)
LOGDIR="logs/NNN_experiment/<run_id_flat>/wave_<wave_id>"
mkdir -p "$LOGDIR"
HYDRA_ARGS=(<tokens produced by hydra_override_arg; one per override>)
{{ENVIRONMENT_NAME}}/bin/python code/NNN_experiment/script.py "${HYDRA_ARGS[@]}" 2>&1 \
  | tee "$LOGDIR/wave_<rig>_gpu<ids>-$(date +%Y%m%d-%H%M%S).log"
```

`pipefail` makes either the Python process or `tee` failure visible. The full wave template also
captures both pipeline statuses so Python remains authoritative when both fail.

## code/common/paths.py

```python
"""Resolve the repo-relative storage paths declared in .env.

.env keeps project-scoped storage relative (CACHE_DIR=storage/cache) so that one
file is correct on every rig: rig-sync already requires each rig's repo_path to
sit on that rig's large volume, so anything under the project root inherits that
guarantee without naming a mount point. Absolute values are left alone -- they
are a warned-about exception, not the shape to build against.

Use project_path() for every storage location an experiment writes to. Building
one from os.getcwd() works from the project root and silently writes somewhere
else the first time a script is launched from anywhere but there.
"""

import os
from pathlib import Path

_MARKER = "pyproject.toml"


def project_root() -> Path:
    """The directory holding pyproject.toml, found by walking up from this file."""
    for candidate in Path(__file__).resolve().parents:
        if (candidate / _MARKER).is_file():
            return candidate
    raise RuntimeError(f"no {_MARKER} above {__file__}: cannot locate the project root")


def project_path(value: str | os.PathLike) -> Path:
    """An absolute path: value as-is when absolute, else relative to the project root."""
    path = Path(value)
    return path if path.is_absolute() else project_root() / path


def storage_path(var: str, default: str) -> Path:
    """Resolve an .env storage variable, falling back to its documented default."""
    return project_path(os.environ.get(var) or default)
```

## code/common/run_id.py

```python
"""Shared run_id helpers.

Each experiment declares, in its own .py, the authoritative ordered list of config
params that uniquely identify a run and its approved experiment-wide path layout:

    RUN_ID_PARAMS = ["model", "lr", "seed"]
    RUN_ID_PATH_LAYOUT = "nested"

and uses these helpers for ALL artifact paths and run names. Never hand-build them.

Scripts must call guard_run_config() before writing ANY artifact (and before the
StatusWriter starts): it hard-fails on run_id collisions — same run_id, different
full config — which happen when a param was added to the config but not to
RUN_ID_PARAMS (schema evolution rules: conventions.md). One guard at the
evaluations/ run dir suffices: checkpoints/ shares the same run_id. plots/ is
not run-scoped and needs no guard.

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


def run_id_path(cfg, params, *, layout="nested") -> Path:
    """Run-output path in the experiment's authoritative layout.

    ``nested`` renders ``model=mlp/lr=0.001/seed=0``. ``collapsed-v1`` renders
    the exact same ordered, percent-encoded pairs in one component:
    ``model=mlp,lr=0.001,seed=0``. ``hashed-v1`` renders one short component
    ``rid-<16 hex>`` (sha256 of the collapsed-v1 string) for filesystems whose
    limits the other two overflow; guard_run_config() then records the two-way
    hash<->params map (.run_id.json per run, RUN_ID_MAP.json per experiment).
    Both non-nested layouts are opt-in and must be recorded as the experiment's
    RUN_ID_PATH_LAYOUT only after explicit user approval.

    With zero or one pair the two layouts are byte-identical (``.`` or the one
    ``key=value`` component). Presentation code shows that path once, labels it
    as both layouts' rendering plus the pinned literal.

    Use one layout consistently under checkpoints/ and evaluations/. plots/ does
    not use run_id paths at all. Do not truncate, hash outside hashed-v1, or
    otherwise rewrite pairs to evade a collision or filesystem limit.
    """
    pairs = _run_id_pairs(cfg, params)
    if layout == "nested":
        return Path(*pairs)
    if layout == "collapsed-v1":
        return Path(",".join(pairs))
    if layout == "hashed-v1":
        return Path(run_id_hash(cfg, params))
    raise ValueError(
        f"invalid run_id path layout {layout!r}; "
        "expected 'nested', 'collapsed-v1', or 'hashed-v1'"
    )


def run_id_hash(cfg, params) -> str:
    """The hashed-v1 component: ``rid-`` + first 16 hex of sha256(run_id_flat)."""
    digest = hashlib.sha256(run_id_flat(cfg, params).encode("utf-8")).hexdigest()
    return RUN_ID_HASH_PREFIX + digest[:RUN_ID_HASH_HEX]


def run_id_record(cfg, params) -> dict:
    """The .run_id.json payload: the full two-way mapping for one run."""
    return {
        "layout": "hashed-v1",
        "hash": run_id_hash(cfg, params),
        "run_id_flat": run_id_flat(cfg, params),
        "run_id": run_id_dict(cfg, params),
    }


def write_run_id_map(experiment_root: Path) -> Path:
    """Regenerate <experiment_root>/RUN_ID_MAP.json from every run's .run_id.json.

    Derived index only, safe to regenerate on any rig: ``by_hash`` maps
    hash -> {run_id_flat, run_id}; ``by_run_id_flat`` maps flat -> hash.
    """
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
    tmp = map_file.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True))
    os.replace(tmp, map_file)
    return map_file


def run_id_flat(cfg, params) -> str:
    """Flat form, e.g. 'model=mlp,lr=0.001,seed=0'.

    Used for scripts/ and logs/ run-folder names, wandb run names, EXPERIMENTS.md rows.
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


def guard_run_config(cfg, params, run_dir: Path, *, layout="nested") -> None:
    """Refuse to reuse a run dir whose config differs from the current one.

    Writes the full resolved config to <run_dir>/.run_config.json on first run.
    On later runs with the same run_id, hard-fails if ANY param differs — that is
    a run_id collision: a param changed that is not in RUN_ID_PARAMS. Same-config
    reruns (resume/retry) pass. Call BEFORE writing any artifact.

    Under ``hashed-v1`` it also writes <run_dir>/.run_id.json (hash <-> params),
    hard-fails if an existing record maps the hash to a different run_id_flat,
    and regenerates the experiment-wide RUN_ID_MAP.json next to the run dirs.
    """
    resolved = resolved_config(cfg)
    if layout == "hashed-v1":
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
