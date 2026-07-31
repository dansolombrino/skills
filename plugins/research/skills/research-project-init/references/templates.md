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
| `evaluations/` | data produced by running experiments (+ `.status.json` markers) |
| `logs/` | run logs — tee'd stdout+stderr per run, wandb files (gitignored, rig-local) |
| `plots/` | plots produced by `visualizations/` |
| `scripts/` | shell scripts that launch experiments/sweeps |
| `visualizations/` | plotting code (argparse), reads `evaluations/`, writes `plots/` |
| `shitpads/` | temp scratch space (gitignored, rig-local) |
| `references/` | papers/codebases for reference (gitignored, rig-synced) |

Experiments are named `NNN_experiment_name` and mirrored across the folders above.
Each run is identified by its **run_id** (an ordered subset of config params, declared
per experiment in its `.py` as `RUN_ID_PARAMS`).

## Tracking

- **`EXPERIMENTS.md`** — state: which runs exist (todo/inpr/done/failed), in which wave, on
  which rig and GPU.
- **`JOURNAL.md`** — story: prose log of what was done, why, and what was learned.

## Setup

1. `cp .env.example .env` and fill in the secrets/paths.
2. Create the environment (see below).
3. Cross-machine sync is handled by rig-sync.

## Running

Runs are launched in **waves** (one dispatch decision, id `YYYYMMDD-HHMMSS`) via
`scripts/NNN_experiment_name/<flat run_id>/wave_<wave_id>/` — a `README.md` saying what the
wave is, plus one self-contained `wave_<rig>_gpu<ids>.sh` per run. `logs/` mirrors that tree.
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

<!-- state only; the story lives in JOURNAL.md. One section per NNN_experiment.
     Run tables, one row per (run, wave):
     | <run_id params...> | wave | rig | gpu | status | started | progress | eta | ended | elapsed | notes |
     (schema: experiments-tracking skill) -->
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
`.status.json` markers live inside evaluations/ and follow whatever is decided for it.

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
cd "$(dirname "$0")/../../../.."         # to project root (depth varies with nesting)
LOGDIR="logs/NNN_experiment/<run_id_flat>/wave_<wave_id>"
mkdir -p "$LOGDIR"
python code/NNN_experiment/script.py <overrides> 2>&1 \
  | tee "$LOGDIR/wave_<rig>_gpu<ids>-$(date +%Y%m%d-%H%M%S).log"
```

`pipefail` keeps python's exit code authoritative despite the `tee` pipe.

## code/common/run_id.py

```python
"""Shared run_id helpers.

Each experiment declares, in its own .py, the authoritative ordered list of config
params that uniquely identify a run (elected WITH the user):

    RUN_ID_PARAMS = ["model", "lr", "seed"]

and uses these helpers for ALL artifact paths and run names. Never hand-build them.

Scripts must call guard_run_config() before writing ANY artifact (and before the
StatusWriter starts): it hard-fails on run_id collisions — same run_id, different
full config — which happen when a param was added to the config but not to
RUN_ID_PARAMS (schema evolution rules: conventions.md). One guard at the
evaluations/ run dir suffices: checkpoints/ and plots/ share the same run_id.
"""

import json
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

    Used for scripts/ and logs/ run-folder names, wandb run names, EXPERIMENTS.md rows.
    """
    return ",".join(f"{p}={cfg[p]}" for p in params)


def guard_run_config(cfg, params, run_dir: Path) -> None:
    """Refuse to reuse a run dir whose config differs from the current one.

    Writes the full resolved config to <run_dir>/.run_config.json on first run.
    On later runs with the same run_id, hard-fails if ANY param differs — that is
    a run_id collision: a param changed that is not in RUN_ID_PARAMS. Same-config
    reruns (resume/retry) pass. Call BEFORE writing any artifact.
    """
    from omegaconf import OmegaConf  # hydra projects; plain-dict cfgs skip this

    # json round-trip so comparison sees exactly what a stored snapshot stores
    resolved = json.loads(json.dumps(OmegaConf.to_container(cfg, resolve=True),
                                     sort_keys=True, default=str))
    snapshot_file = run_dir / ".run_config.json"
    if snapshot_file.exists():
        snapshot = json.loads(snapshot_file.read_text())
        diffs = {
            k: (snapshot.get(k), resolved.get(k))
            for k in sorted(set(snapshot) | set(resolved))
            if snapshot.get(k) != resolved.get(k)
        }
        if diffs:
            lines = "\n".join(f"  {k}: old={old!r} new={new!r}" for k, (old, new) in diffs.items())
            raise RuntimeError(
                f"run_id collision at {run_dir}: same run_id "
                f"({run_id_flat(cfg, params)}) but the config differs:\n{lines}\n"
                "Add the offending param(s) to RUN_ID_PARAMS (re-elect with the "
                "user) or migrate existing artifacts. See conventions.md, "
                "'run_id schema evolution'."
            )
    else:
        run_dir.mkdir(parents=True, exist_ok=True)
        snapshot_file.write_text(json.dumps(resolved, indent=2, sort_keys=True, default=str))
```
