---
name: experiment-design
description: Compile an approved scientific step into a reproducible Research 2.0 experiment with NNN naming, run-id election, checkpoints, schema-v2 status telemetry, smoke criteria, and WandB decisions. Use when creating a new experiment or sub-experiment, writing training, finetuning, or evaluation code under code/NNN_..., or adding its Hydra configuration; do not use to migrate legacy experiment layouts.
---

# experiment-design

Protocol for compiling an approved scientific step into a new experiment. Read
`../research-project-init/references/conventions.md` and the active
`program/00-execution-agreement.md` first. Stop as unsupported when Research 2.0 surfaces are
absent; do not offer migration.

Directives are closed (see the canon section of that name). Derive naming, structure, config
shape, and defaults from these directives and this project alone. Never read another project on
disk to copy how an experiment was laid out there; where a directive is missing, ask the user.

Decision ownership follows `engineering_mode`. In manual mode, propose each technical choice and
wait for the user. In auto mode, choose and record it inside the approved engineering envelope.
Integrity gates and protected choices never become delegable.

## 1. Name & mirrored structure

- Propose the next `NNN_[experiment_name]` (zero-padded, next free number; sub-experiments nest as `NNN_exp/NNN_sub_exp`).
- Treat the complete numbered leaf hierarchy as `<experiment_path>` and preserve it unchanged in
  every engineering handoff. Once authorized by the active mode, create the matching dirs in
  `code/` and `config/`; later `evaluations/`, `visualizations/`, and `plots/` paths must retain the
  same hierarchy as their artifacts appear. Apply `visualizations` for plotting-code placement.
- Unit tests for an experiment script are optional and create-on-demand. When one is written, it
  belongs at `tests/code/<experiment_path>/<script_stem>/test_*.py` (pytest). This is not the
  pre-dispatch smoke test of section 4c and never substitutes for it. Do not scaffold the tree and
  do not ask whether tests are wanted.

## 2. run_id election

1. List the experiment's config params and propose which subset (and order) uniquely identifies a run, with reasoning.
2. In manual mode, iterate until the user approves. In auto mode, elect the smallest ordered subset
   that prevents collisions, record the reasoning in the handoff/decision register, and proceed.
3. Construct and show the complete current canonical `nested` path templates first for
   checkpoints, evaluations, and every run-ID-derived plot. Immediately afterward, and separately,
   show exactly one `collapsed-v1` alternative that collapses all eligible ordered
   `RUN_ID_PARAMS` into one component using the exact existing percent-encoded `run_id_flat`
   pairs. Do not construct or show an alternative when fewer than two params are eligible. When it
   is eligible, showing exactly one alternative is mandatory. The
   user must explicitly approve `collapsed-v1`; otherwise `nested` is the default regardless of
   engineering mode. Preflight exact component and full-path limits and collisions; never silently
   hash, truncate, drop params, fall back, or accept an ambiguous mapping.
   For any checkpoint, evaluation, or run-ID-derived plot path with zero or one applicable fixed
   param, `nested` and `collapsed-v1` are byte-identical: show the path once, not as a separate
   alternative; label it as both layouts' rendering and state the experiment's selected or existing
   pinned literal. That one path is approvable under the pin. This does not relax
   the exactly-one alternative rule for any path with two or more applicable fixed params.
4. Record `RUN_ID_PARAMS = [...]` and the one authoritative experiment-wide
   `RUN_ID_PATH_LAYOUT = "nested"|"collapsed-v1"` beside it in the experiment's `.py`; mirror
   both in the experiment's EXPERIMENTS.md section header. Make ALL artifact paths go through
   `code/common/run_id.py`: checkpoints, evaluations, and run-ID-derived plots call
   `run_id_path(..., layout=RUN_ID_PATH_LAYOUT)` with their applicable ordered params, while
   scripts/logs/WandB and EXPERIMENTS.md row identity keep using `run_id_flat` unchanged. Never
   mix layouts across the authoritative artifact surfaces.
5. Wire `guard_run_config(cfg, RUN_ID_PARAMS, <eval run dir>)` into every training/eval script,
   **before the StatusWriter starts and before any artifact is written** — it snapshots the full
   config to `.run_config.json` and hard-fails on run_id collisions (same run_id, different
   config).

## 2b. run_id evolution — config changes to an EXISTING experiment

Any change that adds/removes/renames a behavior-affecting config param (integration, new feature, refactor) **invalidates the election** — new runs would collide with old artifacts at the same `<run_id path>` (overwritten, or silently skipped as "done" by a self-guarded wave script). Full rules: `conventions.md`, "run_id schema evolution". Protocol:

1. **STOP before any new run launches** and re-run the election: does the new param join
   `RUN_ID_PARAMS`? The active engineering-mode owner may keep it out only by explicitly recording
   why it is non-identifying.
2. Inspect checkpoints, evaluations, plots, and wave records for this experiment.
   If none exists, update `RUN_ID_PARAMS` and the EXPERIMENTS.md section header normally before the
   first launch.
3. Once any checkpoint, evaluation, or plot output or wave README/script exists,
   `RUN_ID_PARAMS` is immutable under both `nested` and `collapsed-v1`. Never change the identity
   schema in place. Create a new numbered sub-experiment with the re-elected params and its own
   pinned layout; leave the old experiment and all of its records untouched.

## 2c. layout immutability for an EXISTING experiment

Inspect checkpoints, evaluations, plots, and wave records before offering a layout
choice. If none exists, present `nested` then the eligible `collapsed-v1` alternative under section
2 and record the chosen literal. Once any output or wave record exists, preserve the checked-in
renderer and `RUN_ID_PATH_LAYOUT` forever. Never propose or perform an in-place layout change, even
when zero/one-param renderings are byte-identical. To use another layout, create a new numbered
sub-experiment and leave the old experiment untouched.

## 3. Config

- One hydra yaml tree under `config/NNN_exp/` holding ALL parameters with defaults. Pure params — no run_id metadata, no secrets (those are `.env`'s; new env var ⇒ update `.env.example` same turn).

## 4. Checkpoint checklist (must resolve)

Resolve every item with the applicable engineering-mode owner; do not leave implicit defaults.

1. Checkpoint filenames? (no repo-wide default — per-case)
2. Contents? Present trade-offs: full training state (model+optimizer+scheduler+RNG+step+config snapshot; exact resume) vs weights-only (small; cannot truly resume).
3. Resume support in this script, yes/no? — decided NOW, before coding; sweep-rerun behavior later inherits this silently — **including unattended crash recovery**: after a machine crash/reboot the sweep machinery auto-relaunches interrupted runs, and resumable ones continue from checkpoint while non-resumable ones restart from scratch, so choose full training state for anything expensive.
4. Which checkpoints to produce / retention? **Compute the footprint before answering**, and
   state it in the decision: `checkpoint size × checkpoints retained × runs in the sweep`. Full
   training state (item 2) is typically several times the model size, and "keep every step" is a
   retention policy — usually an unexamined one. Multiply it out against the rig's declared
   headroom (`rig-sync` → `references/configuration.md`): a sweep whose total exceeds the
   allowance cannot be placed anywhere, and no dispatch-time guard can rescue it. If the total is
   uncomfortable, the lever is here — keep last-N plus the final, checkpoint on a coarser cadence,
   or store weights-only for the intermediates and full state only where resume must work.
5. **Expected final artifact** — which file (final checkpoint and/or final eval output) proves the run truly finished? This is the **golden completion signal** monitors check first (`conventions.md`); record the choice in the experiment's EXPERIMENTS.md section header.

## 4b. Run signaling & timing (mandatory, no user decision needed)

Every training/eval script wraps its work in the **StatusWriter** pattern
(`code/common/status.py`; canonical implementation in
`research-project-init` references/templates.md):

- owns `evaluations/NNN_exp/<run_id path>/.status.json` schema v2: state, timezone-aware
  lifecycle timestamps, live `elapsed_s`, automatic 60-second heartbeat, display progress, and
  numeric `progress_completed`/`progress_total`/`progress_unit`, plus `wave_id`, `gpu`,
  `source_revision`, `source_tag`, and `environment_fingerprint`, read from environment exported
  by the source/environment-verified wave script — env vars, never config params, so they stay
  out of the `guard_run_config` snapshot;
- calls `heartbeat(completed=<done>, total=<total>, unit=<label>)` after every completed
  epoch/major step. The helper supplies liveness between calls; experiment code supplies the
  facts needed for ETA. Do not write an ETA into `.status.json`;
- prints start time, end time, and elapsed to stdout — the wave script tees all stdout+stderr to the mirror of its own path under `logs/` (`logs/NNN_exp/<run_id_flat>/wave_<wave_id>/wave_<rig>_gpu<ids>-<timestamp>.log`), so scripts need no separate file logging, and the elapsed lands in EXPERIMENTS.md as the run's reference runtime.

Before dispatching, require the canonical schema-v2 helper and numeric call sites. A legacy
display-only status contract makes the repository unsupported; do not upgrade it in place.

## 4c. Pre-dispatch smoke test (must resolve)

1. Resolve one repeatable hub command that exercises the real entrypoint with the smallest safe
   workload and cannot collide with a production run_id or final artifact.
2. Resolve its pass criterion: exit zero plus the named lightweight output/assertion that proves
   initialization and one meaningful unit of work completed.
3. Record both in the experiment's EXPERIMENTS.md section header. `sweep-dispatch` reruns this
   exact command after staging the wave and before committing/tagging its Git revision; any source
   rewrite or failed criterion blocks the launch.

## 5. WandB checklist (must resolve)

1. Use wandb for this experiment?
2. **The ENTIRE resolved config is logged** — `wandb.init(config=wandb_config(cfg), ...)` from
   `code/common/run_id.py`, which returns the same full payload as the `.run_config.json` snapshot
   plus a `provenance` namespace (`wave_id`, `gpu`, `source_revision`, `source_tag`,
   `environment_fingerprint`, from wave env vars). Never a hand-picked subset, never `RUN_ID_PARAMS`
   only: a run's config, like its run name, **cannot be changed retroactively**, so a partial config
   leaves the sweep permanently unfilterable on exactly the params nobody thought to log — and
   provenance is what lets you filter by tested commit, rig/GPU, and environment. Not a mode
   decision; no owner may narrow it.
3. Resolve metrics and their names/keys with the applicable engineering-mode owner.
4. Use project = research project, group = `NNN_experiment`, run name = flat run_id as the proposal.
   Manual mode waits for approval; auto mode may adopt or safely refine it inside the envelope.
5. Online mode on all rigs; `WANDB_API_KEY` from `.env`.
6. wandb files go under `logs/` — `wandb.init(dir=<project_root>/"logs")` — never the project root.

## 6. Wrap up

- Apply `research-journal` according to the event-owning layer's mode, then return the engineering
  handoff fields, elected run identity, checkpoint/smoke/WandB decisions, and expected artifacts to
  `scientific-orchestrator` when it initiated the work.
