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
| `visualizations/` | plotting code (argparse), reads `evaluations/`, writes `plots/` |
| `shitpads/` | temp scratch space (gitignored, rig-local) |
| `references/` | papers/codebases for reference (gitignored, rig-synced) |
| `program/` | scientific agreement, decision history, and phase reports |
| `orchestration/` | ignored local control events and subagent traces |

Experiments are named `NNN_experiment_name` and mirrored across the folders above.
Each run is identified by its **run_id** (an ordered subset of config params, declared
per experiment in its `.py` as `RUN_ID_PARAMS`).

## Tracking

- **`program.md`** — current scientific question, phase, next decision, and record links.
- **`program/00-execution-agreement.md`** — explicit scientific/engineering modes, scope,
  envelope, destinations, and protected choices.
- **`EXPERIMENTS.md`** — state: which runs exist (todo/inpr/done/failed), in which wave, on
  which rig and GPU.
- **`JOURNAL.md`** — story: prose log of what was done, why, and what was learned.
- **Flywheel / `index.md`** — authoritative curated scientific lineage and its local mirror.

## Setup

1. `cp .env.example .env` and fill in the secrets/paths.
2. Use `$environment-sync` to provision the exact `uv.lock` + `.python-version` environment and
   run the project GPU smoke.
3. Configure the approved GitHub remote/dispatch branch and `sync.toml`, then use `$rig-sync` and
   `$environment-sync` to prepare and verify approved rig roots.
4. Configure one canonical Flywheel root in `.flywheel.json` or `.env` before publishing nodes.

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
# {{PROJECT_NAME}} — project notes for Codex

<!-- Thin by design: generic research conventions come from the installed `research`
     skill bundle. Only project-specific facts live here. -->

## What this project is

{{PROJECT_DESCRIPTION}}

## Awareness map

- `EXPERIMENTS.md` — run tracking (state). Read it to know what exists/ran.
- `JOURNAL.md` — prose history (story). Read it to know why things were done.
- `program.md` — one-screen current scientific state and links.
- `program/00-execution-agreement.md` — authoritative manual/auto modes and approval envelope.
- `program/decision-register.md` — tracked material scientific decisions.
- `index.md` — local Flywheel mirror; Flywheel remains authoritative.
- `orchestration/` — ignored local traces; never stage or treat them as scientific results.
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

## program.md (initial)

```markdown
# Research Program

Active request: <not set>
Current phase: setup
Agreement: a1, approved: no (`program/00-execution-agreement.md`)
Scientific mode: <manual|auto — required>
Engineering mode: <manual|auto — required>
Scientific question: <not set>
Next decision: approve the initial agreement
Engineering handoff: none
Subagent plan: none
Flywheel disposition: setup incomplete

## Checklist

- [ ] Execution agreement approved
- [ ] Scientific question and decision criterion recorded
- [ ] Engineering envelope and protected choices recorded
- [ ] Required evidence or engineering handoff identified
- [ ] Flywheel disposition recorded

Decision register: `program/decision-register.md`
Run state: `EXPERIMENTS.md`
Narrative: `JOURNAL.md`
Flywheel mirror: `index.md`
```

## program/00-execution-agreement.md (initial)

```markdown
# Execution Agreement

agreement_version: a1
approved: no
scientific_mode: <manual|auto — required>
engineering_mode: <manual|auto — required>

## Scientific contract

- Active request:
- Question / claim / hypothesis:
- Live alternatives:
- Evidence stage: exploratory
- Decisive evidence and decision criterion:
- Evaluation boundary and stop rule:
- Included and excluded scientific scope:

## Engineering envelope

- Repository / branch / approved remotes:
- Rigs / GPUs:
- Compute / time / monetary ceilings:
- Approved tracking and publication destinations:
- Canonical Flywheel root id / title:

## Decision ownership

- Hard constraints:
- Soft preferences:
- Delegable fields:
- Protected choices:
  - Plot titles: approve the exact template and fixed RUN_ID_PARAMS before every plotting-code edit.
  - Additional project-specific choices:

## Approval

- Owner:
- Approved scope:
- Latest material revision:
```

## program/decision-register.md (initial)

```markdown
# Decision Register

<!-- Append material scientific/control decisions. Keep factual run state in EXPERIMENTS.md. -->
```

## index.md (initial)

```markdown
# Flywheel Node Index — {{PROJECT_NAME}}

Local mirror of the Flywheel graph. The authoritative record lives in Flywheel.
```

## .env.example (committed key mirror)

```bash
WANDB_API_KEY=
FLYWHEEL_ROOT_NODE_ID=
FLYWHEEL_ROOT_NODE_SLUG=
FLYWHEEL_ROOT_NODE_TITLE=

# Hugging Face credentials (secrets; keep values only in .env)
HF_TOKEN=
HUGGING_FACE_HUB_TOKEN=

# Machine-varying cache paths (the scaffold writes concrete absolute values to .env)
HF_HOME=
HF_DATASETS_CACHE=
HF_HUB_CACHE=
OPENCLIP_CACHE_DIR=
CACHE_DIR=

# Machine-local runtime setting
TORCH_NUM_WORKERS=
```

## .env (ignored machine profile)

Render this file directly; do not obtain it by copying `.env.example`. These are the shared cache
paths used by the research rigs and must be reproduced verbatim so model and dataset downloads are
reused across projects. Keep secret values empty unless the user explicitly supplies them through
an authorized secret source; never copy or commit a token from another project's `.env`.

```bash
WANDB_API_KEY=
FLYWHEEL_ROOT_NODE_ID=
FLYWHEEL_ROOT_NODE_SLUG=
FLYWHEEL_ROOT_NODE_TITLE=

HF_TOKEN=
HUGGING_FACE_HUB_TOKEN=

HF_HOME=/mnt/KS_2TB/cache/huggingface
HF_DATASETS_CACHE=/mnt/KS_2TB/cache/huggingface/datasets
HF_HUB_CACHE=/mnt/KS_2TB/cache/huggingface/models
OPENCLIP_CACHE_DIR=/mnt/KS_2TB/PARA/Projects/quantization/qat-transfer/storage/openclip
CACHE_DIR=/mnt/KS_2TB/PARA/Projects/quantization/qat-transfer/storage/cache

TORCH_NUM_WORKERS=16
```

## .flywheel.json (only after root verification)

```json
{
  "rootNodeId": "{{VERIFIED_FLYWHEEL_ROOT_NODE_ID}}",
  "rootNodeTitle": "{{VERIFIED_FLYWHEEL_ROOT_NODE_TITLE}}"
}
```

Do not create this file with placeholders. If no root is verified, leave it absent and keep the
Flywheel disposition as setup-incomplete.

## .gitignore

```gitignore
.env
{{ENVIRONMENT_NAME}}/
.rigsync_cache/
orchestration/
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

## sync.toml

Create this from the `$rig-sync` skill's configuration reference. Declare `[git]` with the
approved remote/branch, the standard artifact groups, and one absolute `repo_path` per intended
rig; do not put SSH aliases, ports, users, or keys here. Add `[environment]` from
`$environment-sync` with `manager = "uv"`, the user-chosen single-directory
`name = "{{ENVIRONMENT_NAME}}"`, and the approved tokenized GPU smoke command. Ask for this name
before creating the environment; suggest `.venv` only as an option and never infer a default. Run
both skills' doctor checks before remote dispatch.

## Python environment

Create `pyproject.toml` with the project metadata, dependencies, exact
`[tool.uv].required-version = "==X.Y.Z"`, and `python-preference = "managed"`. Commit an exact
`X.Y.Z` `.python-version` and the generated `uv.lock`. Copy
`$environment-sync/assets/environment.py` to `code/common/environment.py`; do not rewrite its
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

## code/common/run_id.py

```python
"""Shared run_id helpers.

Each experiment declares, in its own .py, the authoritative ordered list of config
params that uniquely identify a run (elected by the active engineering-mode owner):

    RUN_ID_PARAMS = ["model", "lr", "seed"]

and uses these helpers for ALL artifact paths and run names. Never hand-build them.

Scripts must call guard_run_config() before writing ANY artifact (and before the
StatusWriter starts): it hard-fails on run_id collisions — same run_id, different
full config — which happen when a param was added to the config but not to
RUN_ID_PARAMS (schema evolution rules: conventions.md). One guard at the
evaluations/ run dir suffices: checkpoints/ and plots/ share the same run_id.
"""

import json
import shlex
from pathlib import Path
from urllib.parse import quote


def _run_id_component(value) -> str:
    """Filesystem-safe, reversible rendering for a run_id key or value."""
    if isinstance(value, (dict, list, tuple)):
        value = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return quote(str(value), safe="-._~")


def run_id_dict(cfg, params):
    """run_id as an ordered dict {param: value}."""
    return {p: cfg[p] for p in params}


def run_id_path(cfg, params) -> Path:
    """Nested path form, e.g. model=mlp/lr=0.001/seed=0.

    Used under checkpoints/, evaluations/, plots/. Segment formatting (e.g. bare
    values for some params) may be customized per experiment under the active engineering mode.
    """
    return Path(*[f"{_run_id_component(p)}={_run_id_component(cfg[p])}" for p in params])


def run_id_flat(cfg, params) -> str:
    """Flat form, e.g. 'model=mlp,lr=0.001,seed=0'.

    Used for scripts/ and logs/ run-folder names, wandb run names, EXPERIMENTS.md rows.
    """
    return ",".join(
        f"{_run_id_component(p)}={_run_id_component(cfg[p])}" for p in params
    )


def hydra_override_arg(param, value) -> str:
    """One shell-safe `param=value` token for generated wave scripts."""
    return shlex.quote(f"{param}={value}")


def guard_run_config(cfg, params, run_dir: Path) -> None:
    """Refuse to reuse a run dir whose config differs from the current one.

    Writes the full resolved config to <run_dir>/.run_config.json on first run.
    On later runs with the same run_id, hard-fails if ANY param differs — that is
    a run_id collision: a param changed that is not in RUN_ID_PARAMS. Same-config
    reruns (resume/retry) pass. Call BEFORE writing any artifact.
    """
    from omegaconf import OmegaConf

    # json round-trip so comparison sees exactly what a stored snapshot stores
    raw = OmegaConf.to_container(cfg, resolve=True) if OmegaConf.is_config(cfg) else cfg
    resolved = json.loads(json.dumps(raw,
                                     sort_keys=True, default=str))
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
                "Add the offending param(s) to RUN_ID_PARAMS (re-elect under the "
                "active engineering mode) or migrate supported-project artifacts. See conventions.md, "
                "'run_id schema evolution'."
            )
    else:
        run_dir.mkdir(parents=True, exist_ok=True)
        snapshot_file.write_text(json.dumps(resolved, indent=2, sort_keys=True, default=str))
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
