---
name: experiment-design
description: Design a new experiment in a structured research project — NNN naming, run_id election, checkpoint and wandb checklists. Use when creating a new experiment or sub-experiment, writing new training/finetuning/eval code under code/NNN_..., or adding hydra configs for a new experiment.
---

# experiment-design

Protocol for creating a new experiment. Conventions canon: `../research-project-init/references/conventions.md` (read it if not already loaded). **Every decision below is proposed by you and DECIDED by the user.**

## 1. Name & mirrored structure

- Propose the next `NNN_[experiment_name]` (zero-padded, next free number; sub-experiments nest as `NNN_exp/NNN_sub_exp`).
- On approval, create the matching dirs in `code/`, `config/` (others appear as artifacts are produced).

## 2. run_id election — user has final say, ALWAYS

1. List the experiment's config params and propose which subset (and order) uniquely identifies a run, with reasoning.
2. Iterate until the user approves. **Never proceed on your own choice.**
3. Record it as `RUN_ID_PARAMS = [...]` in the experiment's `.py` — and make ALL artifact paths go through `code/common/run_id.py` helpers (`run_id_path` for checkpoints/evaluations/plots, `run_id_flat` for scripts/logs/wandb). The helpers percent-encode unsafe components by default. Agree with the user on any path-form formatting deviation.
4. Wire `guard_run_config(cfg, RUN_ID_PARAMS, <eval run dir>)` into every training/eval script, **before the StatusWriter starts and before any artifact is written** — it snapshots the full config to `.run_config.json` and hard-fails on run_id collisions (same run_id, different config).
5. Mirror the decision in the experiment's EXPERIMENTS.md section header (see the `experiments-tracking` skill).

## 2b. run_id evolution — config changes to an EXISTING experiment

Any change that adds/removes/renames a behavior-affecting config param (integration, new feature, refactor) **invalidates the election** — new runs would collide with old artifacts at the same `<run_id path>` (overwritten, or silently skipped as "done" by a self-guarded wave script). Full rules: `conventions.md`, "run_id schema evolution". Protocol:

1. **STOP before any new run launches** and re-run the election check with the user: does the new param join `RUN_ID_PARAMS`? It may stay out only if the user explicitly rules it non-identifying.
2. If it joins: propose the **backfill-rename** migration (insert `param=<old implicit value>` into existing artifact dirs at its elected position) — the user may instead choose to freeze the old tree and start a new sub-experiment. Execute only on approval.
3. Same turn: update the `RUN_ID_PARAMS` constant, the EXPERIMENTS.md section header + rows (`experiments-tracking` skill), and suggest a JOURNAL.md entry for the schema change.

## 3. Config

- One hydra yaml tree under `config/NNN_exp/` holding ALL parameters with defaults. Pure params — no run_id metadata, no secrets (those are `.env`'s; new env var ⇒ update `.env.example` same turn).

## 4. Checkpoint checklist (MUST ask; no defaults)

1. Checkpoint filenames? (no repo-wide default — per-case)
2. Contents? Present trade-offs: full training state (model+optimizer+scheduler+RNG+step+config snapshot; exact resume) vs weights-only (small; cannot truly resume).
3. Resume support in this script, yes/no? — decided NOW, before coding; sweep-rerun behavior later inherits this silently — **including unattended crash recovery**: after a machine crash/reboot the sweep machinery auto-relaunches interrupted runs, and resumable ones continue from checkpoint while non-resumable ones restart from scratch, so choose full training state for anything expensive.
4. Which checkpoints to produce / retention?
5. **Expected final artifact** — which file (final checkpoint and/or final eval output) proves the run truly finished? This is the **golden completion signal** monitors check first (`conventions.md`); record the choice in the experiment's EXPERIMENTS.md section header.

## 4b. Run signaling & timing (mandatory, no user decision needed)

Every training/eval script wraps its work in the **StatusWriter** pattern (`code/common/status.py`; template in `sweep-dispatch` references/templates.md):

- owns `evaluations/NNN_exp/<run_id path>/.status.json` (state running/done/failed, started, ended, elapsed_s, heartbeat, progress) plus `wave_id`, `gpu`, `source_revision`, and `source_tag`, read from environment exported by the revision-verified wave script — env vars, never config params, so they stay out of the `guard_run_config` snapshot;
- calls `heartbeat(progress=...)` at least once per epoch/major step — keep `progress` a simple `<done>/<total>` shape, EXPERIMENTS.md extrapolates its `eta` column from it;
- prints start time, end time, and elapsed to stdout — the wave script tees all stdout+stderr to the mirror of its own path under `logs/` (`logs/NNN_exp/<run_id_flat>/wave_<wave_id>/wave_<rig>_gpu<ids>-<timestamp>.log`), so scripts need no separate file logging, and the elapsed lands in EXPERIMENTS.md as the run's reference runtime.

## 4c. Pre-dispatch smoke test (MUST decide)

1. Agree on one repeatable hub command that exercises the real entrypoint with the smallest safe
   workload and cannot collide with a production run_id or final artifact.
2. Agree on its pass criterion: exit zero plus the named lightweight output/assertion that proves
   initialization and one meaningful unit of work completed.
3. Record both in the experiment's EXPERIMENTS.md section header. `$sweep-dispatch` reruns this
   exact command after staging the wave and before committing/tagging its Git revision; any source
   rewrite or failed criterion blocks the launch.

## 5. wandb checklist (MUST ask)

1. Use wandb for this experiment?
2. If yes: which metrics, their names/keys — the user controls the dashboard layout.
3. Propose the default mapping — project = research project, group = `NNN_experiment`, run name = flat run_id — and get explicit approval (user may override).
4. Online mode on all rigs; `WANDB_API_KEY` from `.env`.
5. wandb files go under `logs/` — `wandb.init(dir=<project_root>/"logs")` — never the project root.

## 6. Wrap up

- Suggest a JOURNAL.md entry for the design decisions (see `research-journal` skill).
