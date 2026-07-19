---
name: experiments-tracking
description: Keep EXPERIMENTS.md true — run tables, status lifecycle (todo/inpr/done/failed), reconciliation from .status markers. Use in any session in a structured research project when runs are generated, launched, checked, or discussed, when the user asks about experiment status, or at session start to reconcile stale statuses.
---

# experiments-tracking

EXPERIMENTS.md is the project's **state** (the story lives in JOURNAL.md). Canon: `../research-project-init/references/conventions.md`.

## Hard rules

- **Single-writer: EXPERIMENTS.md is only ever edited on rig-4090.** On any other rig, report statuses verbally but never edit the file.
- Ground truth is NOT the md — it is the per-run markers `evaluations/NNN_exp/<run_id path>/.status` (`running`/`done`/`failed`), written by each `run.sh`. A missing marker/dir means the run never started (or its outputs were deleted ⇒ it is no longer done).
- One run = one table row = one line (keeps parallel-session git merges clean).

## Format

One section per `NNN_experiment`; header restates the run_id decision; table has **one column per run_id param** (dict form — never a single flat-string column):

```markdown
## 000_grokking
run_id params: model, lr, seed   (mirrors RUN_ID_PARAMS in code/000_grokking/train.py)

| model       | lr   | seed | rig                | status | launched   | notes |
|-------------|------|------|--------------------|--------|------------|-------|
| mlp         | 1e-3 | 0    | server-pro-6000-bw | done   | 2026-07-19 |       |
| transformer | 1e-4 | 1    | rig-3090ti         | inpr   | 2026-07-19 |       |
```

Statuses: `todo` → `inpr` → `done` | `failed`.

## Who writes what (same turn as the action)

- Sweep generation ⇒ append `todo` rows (one per generated run).
- Launching ⇒ flip those rows to `inpr`, fill rig + launched date.
- Observing completion ⇒ flip to `done`/`failed` per the markers.

## Reconciliation (mandatory)

Whenever a session on rig-4090 touches the project — and always when asked about statuses — sweep the `.status` markers and fix any stale rows: `inpr` with marker `done`/`failed` ⇒ flip; rows whose evaluations dir vanished ⇒ back to `todo` (outputs were deleted). Reconciliation is deterministic: md always converges to the markers.
