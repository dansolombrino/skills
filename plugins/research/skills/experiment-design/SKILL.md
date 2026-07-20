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
3. Record it as `RUN_ID_PARAMS = [...]` in the experiment's `.py` — and make ALL artifact paths go through `code/common/run_id.py` helpers (`run_id_path` for checkpoints/evaluations/plots, `run_id_flat` for scripts/wandb). Agree with the user on the path-form segment formatting.
4. Mirror the decision in the experiment's EXPERIMENTS.md section header (see the `experiments-tracking` skill).

## 3. Config

- One hydra yaml tree under `config/NNN_exp/` holding ALL parameters with defaults. Pure params — no run_id metadata, no secrets (those are `.env`'s; new env var ⇒ update `.env.example` same turn).

## 4. Checkpoint checklist (MUST ask; no defaults)

1. Checkpoint filenames? (no repo-wide default — per-case)
2. Contents? Present trade-offs: full training state (model+optimizer+scheduler+RNG+step+config snapshot; exact resume) vs weights-only (small; cannot truly resume).
3. Resume support in this script, yes/no? — decided NOW, before coding; sweep-rerun behavior later inherits this silently.
4. Which checkpoints to produce / retention?
5. **Expected final artifact** — which file (final checkpoint and/or final eval output) proves the run truly finished? This is the **golden completion signal** monitors check first (`conventions.md`); record the choice in the experiment's EXPERIMENTS.md section header.

## 4b. Run signaling & timing (mandatory, no user decision needed)

Every training/eval script wraps its work in the **StatusWriter** pattern (`code/common/status.py`; template in `sweep-dispatch` references/templates.md):

- owns `evaluations/NNN_exp/<run_id path>/.status.json` (state running/done/failed, started, ended, elapsed_s, heartbeat, progress);
- calls `heartbeat(progress=...)` at least once per epoch/major step;
- prints start time, end time, and elapsed to stdout — `run.sh` tees all stdout+stderr to `logs/NNN_exp/<run_id path>/run-<timestamp>.log`, so scripts need no separate file logging, and the elapsed lands in EXPERIMENTS.md as the run's reference runtime.

## 5. wandb checklist (MUST ask)

1. Use wandb for this experiment?
2. If yes: which metrics, their names/keys — the user controls the dashboard layout.
3. Propose the default mapping — project = research project, group = `NNN_experiment`, run name = flat run_id — and get explicit approval (user may override).
4. Online mode on all rigs; `WANDB_API_KEY` from `.env`.
5. wandb files go under `logs/` — `wandb.init(dir=<project_root>/"logs")` — never the project root.

## 6. Wrap up

- Suggest a JOURNAL.md entry for the design decisions (see `research-journal` skill).
