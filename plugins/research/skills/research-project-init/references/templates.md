# Scaffold templates

Substitute `{{PROJECT_NAME}}` / `{{PROJECT_DESCRIPTION}}` and adjust with the user.

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
| `evaluations/` | data produced by running experiments |
| `logs/` | run logs — wandb files etc. (gitignored, rig-local) |
| `plots/` | plots produced by `visualizations/` |
| `scripts/` | shell scripts that launch experiments/sweeps |
| `visualizations/` | plotting code (argparse), reads `evaluations/`, writes `plots/` |
| `shitpads/` | temp scratch space (gitignored, rig-local) |
| `references/` | papers/codebases for reference (gitignored, rig-synced) |

Experiments are named `NNN_experiment_name` and mirrored across the folders above.
Each run is identified by its **run_id** (an ordered subset of config params, declared
per experiment in its `.py` as `RUN_ID_PARAMS`).

## Tracking

- **`EXPERIMENTS.md`** — state: which runs exist (todo/inpr/done/failed), where they ran.
- **`JOURNAL.md`** — story: prose log of what was done, why, and what was learned.

## Setup

1. `cp .env.example .env` and fill in the secrets/paths.
2. Create the environment (see below).
3. Cross-machine sync is handled by rig-sync.

## Running

Single runs and sweeps are launched via `scripts/NNN_experiment_name/` (one folder per
run, named by the flat run_id, each with a self-contained `run.sh`).
```

## CLAUDE.md

```markdown
# {{PROJECT_NAME}} — project notes for Claude

<!-- Thin by design: generic research conventions come from the installed `research`
     skill bundle. Only project-specific facts live here. -->

## What this project is

{{PROJECT_DESCRIPTION}}

## Awareness map

- `EXPERIMENTS.md` — run tracking (state). Read it to know what exists/ran.
- `JOURNAL.md` — prose history (story). Read it to know why things were done.
- `README.md` — structure + setup.
- Deepen further by reading `config/NNN_*/` and the experiment's `.py` (its
  `RUN_ID_PARAMS` is the authoritative run identity).

## Project-specific quirks

<!-- datasets, unusual constraints, deviations from the standard structure -->
```

## EXPERIMENTS.md (initial)

```markdown
# Experiments

<!-- state only; the story lives in JOURNAL.md. One section per NNN_experiment. -->
```

## JOURNAL.md (initial)

```markdown
# Journal

<!-- append-only prose, chronological; entries under `## YYYY-MM-DD, HH:MM — title` -->
```

## .env.example (seed)

```bash
WANDB_API_KEY=
# machine-varying paths (data roots etc.) — add per project; mirror every new key here
```

## .gitignore

```gitignore
.env
shitpads/*
!shitpads/.gitkeep
logs/*
!logs/.gitkeep
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
outputs/          # hydra default run dir, if left enabled
```

Adjust with the user: they may want evaluations/ (small jsons) or plots/ committed.
`.status` markers live inside evaluations/ and follow whatever is decided for it.

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

## scripts/ run.sh (log-capture core)

Canonical pattern every launch script follows (full self-contained template with `.status`
markers: sweep-dispatch skill, `references/templates.md`):

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../../.."            # to project root (depth varies with nesting)
LOGDIR="logs/NNN_experiment/<run_id path>"
mkdir -p "$LOGDIR"
python code/NNN_experiment/script.py <overrides> 2>&1 | tee "$LOGDIR/run-$(date +%Y%m%d-%H%M%S).log"
```

`pipefail` keeps python's exit code authoritative despite the `tee` pipe.

## code/common/run_id.py

```python
"""Shared run_id helpers.

Each experiment declares, in its own .py, the authoritative ordered list of config
params that uniquely identify a run (elected WITH the user):

    RUN_ID_PARAMS = ["model", "lr", "seed"]

and uses these helpers for ALL artifact paths and run names. Never hand-build them.
"""

from pathlib import Path


def run_id_dict(cfg, params):
    """run_id as an ordered dict {param: value}."""
    return {p: cfg[p] for p in params}


def run_id_path(cfg, params) -> Path:
    """Nested path form, e.g. model=mlp/lr=0.001/seed=0.

    Used under checkpoints/, evaluations/, plots/. Segment formatting (e.g. bare
    values for some params) may be customized per experiment — with the user.
    """
    return Path(*[f"{p}={cfg[p]}" for p in params])


def run_id_flat(cfg, params) -> str:
    """Flat form, e.g. 'model=mlp,lr=0.001,seed=0'.

    Used for scripts/ run-folder names, wandb run names, EXPERIMENTS.md rows.
    """
    return ",".join(f"{p}={cfg[p]}" for p in params)
```
