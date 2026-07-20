---
name: experiments-tracking
description: Keep EXPERIMENTS.md true — run tables, status lifecycle (todo/inpr/done/failed), run timing (started/ended/elapsed), reconciliation from .status.json markers + artifacts. Use in any session in a structured research project when runs are generated, launched, checked, or discussed, when the user asks about experiment status, or at session start to reconcile stale statuses.
---

# experiments-tracking

EXPERIMENTS.md is the project's **state** (the story lives in JOURNAL.md). Canon: `../research-project-init/references/conventions.md`.

## Hard rules

- **Single-writer: EXPERIMENTS.md is only ever edited on rig-4090.** On any other rig, report statuses verbally but never edit the file. During a launch session, only the orchestrator chat writes it — monitoring subagents never do.
- Ground truth is NOT the md — it is the three on-disk signals (hierarchy in `conventions.md`): the run's **expected final artifact** (golden — present ⇒ truly done, absent ⇒ not done), the per-run `evaluations/NNN_exp/<run_id path>/.status.json` (state + started/ended/elapsed_s/heartbeat/progress, written by the python script), and the latest `logs/NNN_exp/<run_id path>/run-<timestamp>.log`. A missing `.status.json`/dir means the run never started (or its outputs were deleted ⇒ it is no longer done).
- One run = one table row = one line (keeps parallel-session git merges clean).
- **Every status report to the user opens with the current date and time** — run `date '+%Y-%m-%d %H:%M'` and lead with it ("Status as of 2026-07-20 16:45 — ...").

## Format

One section per `NNN_experiment`; header restates the run_id decision; table has **one column per run_id param** (dict form — never a single flat-string column):

```markdown
## 000_grokking
run_id params: model, lr, seed   (mirrors RUN_ID_PARAMS in code/000_grokking/train.py)

| model       | lr   | seed | rig                | status | started     | ended       | elapsed | notes |
|-------------|------|------|--------------------|--------|-------------|-------------|---------|-------|
| mlp         | 1e-3 | 0    | server-pro-6000-bw | done   | 07-19 14:02 | 07-19 15:47 | 1h45m   |       |
| transformer | 1e-4 | 1    | rig-3090ti         | inpr   | 07-19 14:05 |             |         |       |
```

Statuses: `todo` → `inpr` → `done` | `failed`. Timing columns come from `.status.json`:
`started`/`ended` as `MM-DD HH:MM` (year only if ambiguous), `elapsed` compact (`45m`, `1h45m`, `2d3h`) from `elapsed_s`. **Elapsed values are the project's reference runtimes** — use them to estimate wall-clock and split temporal budgets when planning future sweeps.

## Who writes what (same turn as the action)

- Sweep generation ⇒ append `todo` rows (one per generated run).
- Launching ⇒ flip those rows to `inpr`, fill rig + `started` (from `.status.json` once the run actually starts).
- Observing completion ⇒ flip to `done`/`failed` per the signals, fill `ended` + `elapsed`.

## Reconciliation (mandatory)

Whenever a session on rig-4090 touches the project — and always when asked about statuses — sweep the signals and fix any stale rows: `inpr` whose `.status.json` says `done`/`failed` **and** whose artifacts agree ⇒ flip and fill `ended`/`elapsed`; `done` in `.status.json` but expected final artifact missing ⇒ artifacts win, flag it to the user instead of marking done; `inpr` whose `.status.json` is stuck at `running` with a frozen heartbeat **because of a machine fault** — the rig's boot time (`uptime -s`) postdates the heartbeat, or the sweep's tmux session is gone — ⇒ the run is **interrupted**, not failed-by-code: keep `inpr` if a relaunched session is (or is about to be) re-executing it, otherwise flip back to `todo` with a note; when reporting, always distinguish "failed (code error)" from "interrupted (machine fault — safe to relaunch, `launch_<rig>.sh` is idempotent)"; rows whose evaluations dir vanished ⇒ back to `todo` (outputs were deleted, `started`/`ended`/`elapsed` cleared). Reconciliation is deterministic: md always converges to the signals.

Old projects may still have plain `.status` markers and a `launched` column — when working in one, offer to migrate its table/scripts to the current schema opportunistically.
