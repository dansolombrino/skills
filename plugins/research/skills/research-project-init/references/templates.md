# Scaffold templates

The real files live in `assets/project/`, and `scripts/scaffold.py` renders them: never retype one.
In that tree a path component starting `dot_` becomes `.` (`dot_githooks/pre-commit` is
`.githooks/pre-commit`) and a `.template` suffix is dropped. `{{PROJECT_NAME}}`,
`{{ENVIRONMENT_NAME}}`, `{{PYTHON_VERSION}}`, `{{UV_VERSION}}`, `{{REMOTE}}`, and `{{BRANCH}}` are
substituted everywhere except under `code/`, which is copied byte for byte. This page holds what
the files themselves don't say.

## CLAUDE.md

`AGENTS.md` is the canonical project doc. `CLAUDE.md` only redirects to it
("Project notes live in `AGENTS.md`"), so both hosts read the same notes.

## .env (ignored machine profile)

`scaffold.py` writes it with mode 0600 from `dot_env`, since a host permission rule may stop an
agent reading or writing `.env`. Secret values stay empty unless the user supplies them; never copy
or commit a token from another project's `.env`.

Shared model and dataset caches (`HF_HOME`, `HF_HUB_CACHE`, `HF_DATASETS_CACHE`,
`HUGGINGFACE_HUB_CACHE`, `TORCH_HOME`, `UV_CACHE_DIR`, `TRITON_CACHE_DIR`, `XDG_CACHE_HOME`,
`TMPDIR`, `WANDB_CACHE_DIR`, `WANDB_DIR`) are **absent by design** from it and from `.env.example`:
`code/common/paths.py` loads `.env` on import, after the shell environment, and its non-empty values
replace the shell's, so a cache path here would silently override the rig's machine-level caches
(declared in the registry, installed by `rig-sync provision-env`). Project-scoped storage stays
**relative to the project root**, which `rig-sync doctor` already requires to sit on a declared
storage root, so one file is right on every rig: `rig-sync push-env` copies it to every peer, and
`doctor` warns on any absolute value.

## .gitignore

The user may want `evaluations/` or `plots/` committed instead. The run-identity files are tracked
source in `config/<experiment_path>/`; only the derived `RUN_ID_MAP.json` sits in `evaluations/`.
Comments go on their own lines: `.gitignore` has no inline comments, and a trailing `# note` becomes
part of the pattern.

## .vscode/settings.json

Committed project policy: artifact trees stay visible in the explorer but out of the watcher, search,
and Python analysis. Add a project's own heavy directories to all three maps.

## sync.toml

`scaffold.py` writes `[git]`, `[environment]` (`manager = "uv"`, the environment name, the tokenized
GPU smoke command), and the standard artifact groups. The agent then appends one
`[machines.<rig>]` table with an absolute `repo_path` per rig the project will dispatch to, the hub
included, each under one of that rig's declared storage roots (SSH aliases, users, and keys stay in
the user registry). Tools refuse a `sync.toml` with no machines.

## Python environment

`pyproject.toml` declares the standard dependency set and `[dependency-groups] dev` from the start,
since adding either later changes the environment fingerprint on every rig; project-specific
packages come with `uv add`. No build system: the project is never installed into its environment.
`uv.lock` is produced by `uv lock` before the first commit, never
hand-written. `code/common/environment.py` is copied unchanged from the `environment-sync` skill's
`assets/environment.py`.

## .githooks/pre-commit (journal guard)

Rules: `research-journal`. `git config core.hooksPath` is per clone, so a fresh clone runs no guard
until that is set again; `scaffold.py` sets it to `.githooks` here. The hook checks the commit only:
journal entry present, no removed JOURNAL.md line once `rid-<32 hex>` is normalized, and no added
`Path(__file__)`, `Path.cwd()`, or `os.getcwd()` in `code/` or `visualizations/` (`code/common/paths.py`
excepted). A `sys.path` line passes, as does a line marked `# paths-lint: ok` when it builds no
storage path. Atomic checkpoints and the `guard_run_config` call are not linted: neither can be told
from a helper that wraps them without false alarms.

## Experiment entrypoint

`code/` shares its name with a standard-library module, so `import code.common` finds the wrong
thing, and `NNN_name` is no valid identifier. There is one way in, for `code/` and `visualizations/`
at any depth, in the hub checkout and in a wave worktree: put the project's `code/` on `sys.path`,
then import `common.<module>`, with `common.paths` first because importing it loads `.env`. Never
add `PYTHONPATH` or a relative import. Tests get the same imports from `pyproject.toml`.

```python
import sys
from pathlib import Path

sys.path.insert(0, str(next(p / "code" for p in Path(__file__).resolve().parents if (p / "code/common").is_dir())))

from common.paths import output_root, reset_smoke, source_path
from common.run_id import guard_run_config, load_run_id_spec, run_id_path
from common.status import StatusWriter
```

## Smoke outputs

A smoke run keeps the code path and moves the outputs: pass `smoke=cfg.smoke` to `output_root`, so
they land in `storage/smoke/{evaluations,checkpoints}/<experiment_path>/<rid>/`. `guard_run_config`
works there unchanged with that root as `experiment_root`. Nothing that reads real runs looks there:
not `check_run_id_plan`, `RUN_ID_MAP.json`, a migration, `collect`, or a plotter. Call
`reset_smoke(<experiment_path>)` first, so a repeat smoke with a changed default doesn't trip the
guard.

```python
if cfg.smoke:
    reset_smoke(EXPERIMENT)
eval_root = output_root("evaluations", EXPERIMENT, smoke=cfg.smoke)
run_dir = eval_root / run_id_path(cfg, params, layout=RUN_ID_PATH_LAYOUT)
guard_run_config(cfg, params, run_dir, layout=RUN_ID_PATH_LAYOUT, experiment_root=eval_root, spec_root=source_path(f"config/{EXPERIMENT}"))
```

The recorded pass criterion names `storage/smoke/evaluations/<experiment_path>/*/<output>`.

## scripts/ wave script

The log path mirrors the script's own path under `logs/`; the working directory is the project root
and code runs from the wave worktree. The template is in the `sweep-dispatch` skill,
`references/templates.md`.

## code/common/paths.py

`RESEARCH_PROJECT_ROOT` support is what `sweep-dispatch` checks for the wave-isolation contract.

## code/common/run_id.py

`run-identity` mirrors its hashed-v2 functions in `scripts/runid_agent.py`; a test holds the two equal.

## code/common/environment_smoke.py

The default `gpu_smoke` target in `sync.toml`: a real device operation through the locked torch.

## code/common/status.py

Call `heartbeat(completed=<done>, total=<total>, unit=<label>)` after every completed training or
evaluation unit. A daemon thread refreshes liveness and elapsed time every 60 seconds, so a long
unit still looks alive.

## code/common/metrics.py

Open one `MetricsWriter(<eval run dir>, resume=<training state was restored>)` per entrypoint,
after `guard_run_config` and inside `StatusWriter`, and call `log({"train/loss": ..., ...},
step=<global step>)` wherever a metric is computed. Plotters read it with `read_metrics`.
