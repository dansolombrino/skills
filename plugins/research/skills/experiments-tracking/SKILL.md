---
name: experiments-tracking
description: Keep EXPERIMENTS.md true — run tables, wave/rig/gpu placement, status lifecycle (todo/inpr/done/failed), run timing (started/progress/eta/ended/elapsed), reconciliation from .status.json markers + artifacts. Use in any session in a structured research project when runs are generated, launched, checked, or discussed, when the user asks about experiment status, or at session start to reconcile stale statuses.
---

# experiments-tracking

EXPERIMENTS.md is the project's **state** (the story lives in JOURNAL.md). Canon: `../research-project-init/references/conventions.md`.

## Hard rules

- **Single-writer: EXPERIMENTS.md is only ever edited on rig-4090.** On any other rig, report statuses verbally but never edit the file. During a launch session, only the orchestrator chat writes it — monitoring subagents never do.
- Ground truth is NOT the md — it is the three on-disk signals (hierarchy in `conventions.md`): the run's **expected final artifact** (golden — present ⇒ truly done, absent ⇒ not done), the per-run `evaluations/NNN_exp/<run_id path>/.status.json` (state + started/ended/elapsed_s/heartbeat/progress/wave_id/gpu, written by the python script), and the latest `logs/NNN_exp/<run_id_flat>/wave_<wave_id>/wave_<rig>_gpu<ids>-<timestamp>.log`. A missing `.status.json`/dir means the run never started (or its outputs were deleted ⇒ it is no longer done).
- **One (run, wave) = one table row = one line.** A run re-launched in a later wave gets a **new row**, never an in-place update — the table itself carries the execution history. One line per row keeps parallel-session git merges clean.
- **Every status report to the user opens with the current date and time** — run `date '+%Y-%m-%d %H:%M'` and lead with it ("Status as of 2026-07-20 16:45 — ...").

## Format

One section per `NNN_experiment`; header restates the run_id decision; table has **one column per run_id param** (dict form — never a single flat-string column), then placement, status, and timing:

```markdown
## 000_grokking
run_id params: model, lr, seed   (mirrors RUN_ID_PARAMS in code/000_grokking/train.py)

| model | lr   | seed | wave            | rig                | gpu     | status | started     | progress   | eta         | ended       | elapsed | notes |
|-------|------|------|-----------------|--------------------|---------|--------|-------------|------------|-------------|-------------|---------|-------|
| mlp   | 1e-3 | 0    | 20260731-162043 | rig-4090           | 0       | failed | 07-31 16:20 | epoch 3/10 |             | 07-31 16:58 | 38m     | OOM   |
| mlp   | 1e-3 | 0    | 20260805-081200 | behemoth           | 2       | inpr   | 08-05 08:12 | epoch 7/10 | 08-05 09:40 |             |         |       |
| tr-xl | 1e-4 | 0    | 20260803-141000 | behemoth           | 0,1,2,3 | done   | 08-03 14:10 | 10/10      |             | 08-03 18:02 | 3h52m   |       |
```

- `wave` — the dispatch this execution belonged to (`YYYYMMDD-HHMMSS`, canon). `gpu` — the GPU set it occupied, opaque identity. Both are read from `.status.json`; before the run starts they come from the generated script's path.
- Statuses: `todo` → `inpr` → `done` | `failed`. `started`/`ended` as `MM-DD HH:MM` (year only if ambiguous), `elapsed` compact (`45m`, `1h45m`, `2d3h`) from `elapsed_s`. **Elapsed values are the project's reference runtimes** — use them to estimate wall-clock and split temporal budgets when planning future waves.
- `progress` — copied verbatim from `.status.json`.
- `eta` — the **only computed column**: extrapolate linearly from `progress` plus elapsed-so-far; when `progress` is unavailable, fall back to the reference runtimes of prior completed runs in the same experiment. Recompute it on **every** reconciliation pass, and leave it blank for `todo`/`done`/`failed` rows and whenever there is no basis to estimate. Never present it as measured fact — say "estimated" when reporting it.

On a run_id re-election (a param joins `RUN_ID_PARAMS` — `experiment-design` skill, "run_id evolution"): update the section header **and** add the new column to existing rows, backfilled with the old implicit value — same turn as the artifact migration, so rows and on-disk paths never disagree.

## Who writes what (same turn as the action)

- Wave generation ⇒ append `todo` rows (one per generated run), pre-filled with `wave`, `rig`, `gpu` from the generated script paths.
- Launching ⇒ flip those rows to `inpr`, fill `started` (from `.status.json` once the run actually starts).
- Observing progress ⇒ refresh `progress` and recompute `eta`.
- Observing completion ⇒ flip to `done`/`failed` per the signals, fill `ended` + `elapsed`, clear `eta`.

## Reconciliation (mandatory)

Whenever a session on rig-4090 touches the project — and always when asked about statuses — sweep the signals and fix any stale rows. Match a `.status.json` to its row by **(run_id, `wave_id`)**; a run with several rows only ever has one live one (its latest wave). **Legacy fallback: a `.status.json` written before the wave schema has no `wave_id` field at all — match those by `run_id` alone and treat the row as pre-wave.** Without this, reconciliation silently finds nothing on exactly the old projects where it matters most. Then: `inpr` whose `.status.json` says `done`/`failed` **and** whose artifacts agree ⇒ flip and fill `ended`/`elapsed`; `done` in `.status.json` but expected final artifact missing ⇒ artifacts win, flag it to the user instead of marking done; `inpr` whose `.status.json` is stuck at `running` with a frozen heartbeat **because of a machine fault** — the rig's boot time (`uptime -s`) postdates the heartbeat, or the lane's tmux session is gone — ⇒ the run is **interrupted**, not failed-by-code: keep `inpr` if a relaunched lane is (or is about to be) re-executing it, otherwise flip back to `todo` with a note; when reporting, always distinguish "failed (code error)" from "interrupted (machine fault — safe to relaunch, wave scripts are self-guarded)"; rows whose evaluations dir vanished ⇒ back to `todo` (outputs were deleted, timing cleared). Recompute `eta` for every `inpr` row while you are here. Reconciliation is deterministic: md always converges to the signals.

Old projects may still have plain `.status` markers, a `launched` column, or the pre-wave `run.sh` + `launch_<rig>.sh` script layout with path-form log dirs — when working in one, offer to migrate its table/scripts/logs to the current wave schema opportunistically.

**Never migrate while runs are in flight.** Migration renames script and log trees and rewrites table columns; doing that under a live run breaks the paths it is writing into and desyncs the table from disk mid-flight. Check first — if any run is `inpr` (or any `.status.json` says `running` with an advancing heartbeat), **say so and defer the offer** until the project is quiet. Migration is never urgent; a corrupted in-flight run is unrecoverable.
