---
name: experiment-design
description: Compile a requested scientific step into a reproducible Research 2.0 experiment - NNN naming, hashed-v2 run_id election, checkpoints, schema-v3 status telemetry, smoke criteria, and WandB setup. Use when creating a new experiment or sub-experiment, writing training, finetuning, or evaluation code under code/NNN_..., or adding its Hydra configuration; do not use to migrate legacy experiment layouts.
---

# experiment-design

Turns a requested step into an experiment that `sweep-dispatch` can launch. Project facts:
`../research-project-init/references/conventions.md`.

## 1. Name and structure

- Take the next free `NNN_name`; sub-experiments nest as `NNN_exp/NNN_sub`. The complete numbered
  leaf hierarchy is `<experiment_path>`; preserve it unchanged in `code/`, `config/`, and later in
  `evaluations/`, `visualizations/`, and `plots/`.
- Unit tests are optional, at `tests/code/<experiment_path>/<script_stem>/test_*.py`; they never
  substitute for the smoke command (§ 6).

## 2. run_id

- Elect every param the grid varies plus every behavior-affecting param whose value differs from
  the config default; top-level config keys only (`optim`, never `optim.lr`). `run-identity`
  owns the election rules, lookup, and migration.
- Pin `RUN_ID_PATH_LAYOUT = "hashed-v2"` in the experiment's `.py` and write
  `config/<experiment_path>/RUN_ID_VERSIONS.json` as
  `[{"version": 1, "params": [...], "date": "<YYYY-MM-DD>", "reason": "<why>"}]`. Code reads it
  with `version, params = load_run_id_spec(source_path("config/<experiment_path>"))`.
- Every run-scoped path and name goes through `code/common/run_id.py`: `run_id_path(cfg, params,
  layout=RUN_ID_PATH_LAYOUT)` for checkpoints, evaluations, and the `scripts/`/`logs/` run
  folders; `run_id_name` (the hash) and `run_id_label` (readable `k=v,…`) for EXPERIMENTS.md and
  WandB.
- Call `guard_run_config(cfg, params, <eval run dir>, layout=RUN_ID_PATH_LAYOUT,
  experiment_root=<evaluations experiment dir>, spec_root=source_path("config/<experiment_path>"))`
  in every training/eval script before the StatusWriter starts and before any artifact is written.
- Mirror the pin, the version and its params, and
  `run_id map: evaluations/<experiment_path>/RUN_ID_MAP.json` in the experiment's EXPERIMENTS.md
  section header.

An experiment pinned to an older layout: see `run-identity` references/legacy-layouts.md.

### Changing an existing experiment's config

Adding, removing, or renaming a behavior-affecting param changes the identity: decide whether it
joins (a param that cannot change results may stay out, with the reason recorded). If no
checkpoint, evaluation, plot, or wave record exists yet, rewrite the current version entry and the
section header. Otherwise append the next version and run a run_id migration through
`run-identity`; the experiment stays one experiment.

## 3. Config

One hydra yaml tree under `config/<experiment_path>/` with every parameter and its default. No
run_id metadata and no secrets in yaml; a new environment variable goes into `.env.example`.

## 4. Checkpoints

- Filenames, contents (full training state for exact resume vs weights only), and whether the
  script resumes. Unattended crash recovery relaunches interrupted runs: resumable ones continue,
  others restart from scratch, so expensive runs want full state.
- Retention, sized: `checkpoint size × checkpoints retained × runs`, against the rig's and the
  hub's headroom (offload moves a wave's checkpoints to the hub). Record the **per-run retained
  footprint in KiB**; each wave script adds it to its lane's storage floor.
- Write every checkpoint atomically: a `.tmp` name in the same directory, then `os.replace`.
- The **expected final artifact** proves completion. Prefer a small evaluation output (an
  offloaded checkpoint is only a receipt on the rig). Record it in the section header.

## 5. Placement and telemetry

- Record in the section header what limits where a run can go: `min_vram_mib` (per config shape
  when the grid varies model or batch size) and `data needs` tags (`hf:<repo>`, `torch:<name>`,
  `file:<cache-relative path>`) with their cache variable and size.
- Wrap each entrypoint in `StatusWriter` (`code/common/status.py`, schema v3) and call
  `heartbeat(completed=<done>, total=<total>, unit=<label>)` after every epoch or major step.
  Wave id, rig, GPU, source revision, and environment fingerprint come from environment variables
  the wave exports, never config params.
- Print start, end, and elapsed to stdout; the wave script captures the log.

## 6. Smoke command

One repeatable hub command that runs the real entrypoint on the smallest safe workload, with the
interpreter written as `<env>/bin/python` and an output that cannot collide with a production run,
plus its pass criterion (exit 0 and a named output proving one unit of work). Record both in the
section header; `sweep-dispatch` runs it before tagging each wave.

## 7. WandB

- The whole resolved config: `wandb.init(config=wandb_config(cfg), ...)`. A finished run's config
  cannot be changed retroactively (a run_id migration renames runs, it never refills a config),
  so a subset is permanent loss; `wandb_config` adds a `provenance` namespace.
- project = the research project, group = `NNN_experiment`,
  `wandb.init(name=run_id_label(...), tags=[run_id_name(...)], config=wandb_config(cfg))`, online
  mode, `WANDB_API_KEY` from `.env`, `dir=project_path("logs")`.

## 8. Paths in code

Waves run code from a worktree while storage stays in the shared project root, so every storage
location (`.env`, checkpoints, evaluations, logs, caches, earlier experiments' outputs) goes
through `paths.py` `project_path()`, and tracked inputs through `source_path()`; never derive a
storage path from `Path(__file__)` or `os.getcwd()`.

Finish with the `research-journal` entry and a commit; the next step is usually `sweep-dispatch`.
