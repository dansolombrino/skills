---
name: experiments-tracking
description: Keep a Research 2.0 EXPERIMENTS.md true and derive active-run, lane, and wave ETAs from provenance-valid schema-v2 status plus artifacts. Use when runs are generated, launched, checked, reported, or discussed, when the user asks about experiment status or ETA, or at session start to reconcile factual run state; do not interpret or migrate legacy tracking layouts.
---

# experiments-tracking

EXPERIMENTS.md is the project's **state** (the story lives in JOURNAL.md). Canon: `../research-project-init/references/conventions.md`.

Require the Research 2.0 scaffold and schema-v2 status contract. If either is absent, stop as
unsupported; do not offer migration or interpret legacy state. Factual reconciliation is automatic
in every scientific/engineering mode and does not require a narrative or scientific decision.

## Hard rules

- **Single-writer: EXPERIMENTS.md is only ever edited on rig-4090.** On any other rig, report statuses verbally but never edit the file. During a launch session, only the orchestrator chat writes it — monitoring subagents never do.
- Ground truth is NOT the md — it is the three on-disk signals (hierarchy in `conventions.md`): the per-run status first proves the current wave's Git revision/tag and environment fingerprint; after that, the **expected final artifact** is golden for completion, followed by `.status.json` state/timing/progress and the latest run log. A missing `.status.json`/dir means the run never started (or its outputs were deleted ⇒ it is no longer done).
- **One (run, wave) = one table row = one line.** A run re-launched in a later wave gets a **new row**, never an in-place update — the table itself carries the execution history. One line per row keeps parallel-session git merges clean.
- **Every status report to the user opens with its message-written time** — obtain a local
  timezone-bearing timestamp immediately before sending (for example
  `date '+%Y-%m-%dT%H:%M:%S%:z'`) and lead exactly with
  `Status written <timestamp> —`. Never substitute the status heartbeat/observation time; report
  heartbeat age separately.

## Format

One section per `NNN_experiment`; header restates the run_id decision; table has **one column per run_id param** (dict form — never a single flat-string column), then placement, status, and timing:

```markdown
## 000_grokking
run_id params: model, lr, seed   (mirrors RUN_ID_PARAMS in code/000_grokking/train.py)
smoke command: .venv/bin/python code/000_grokking/train.py smoke=true steps=1 seed=smoke
smoke pass: exit 0 and evaluations/000_grokking/smoke/result.json says one step completed

| model | lr   | seed | wave            | rig                | gpu     | status | started     | progress   | eta         | ended       | elapsed | notes |
|-------|------|------|-----------------|--------------------|---------|--------|-------------|------------|-------------|-------------|---------|-------|
| mlp   | 1e-3 | 0    | 20260731-162043 | rig-4090           | 0       | failed | 07-31 16:20 | epoch 3/10 |             | 07-31 16:58 | 38m     | OOM   |
| mlp   | 1e-3 | 0    | 20260805-081200 | behemoth           | 0       | inpr   | 08-05 08:12 | epoch 7/10 | 08-05 09:40 |             |         |       |
| tr-xl | 1e-4 | 0    | 20260803-141000 | rig-3090-ti        | 0       | done   | 08-03 14:10 | 10/10      |             | 08-03 18:02 | 3h52m   |       |
```

- `wave` — the dispatch this execution belonged to (`YYYYMMDD-HHMMSS`, canon), whose annotated
  Git tag is `wave--<wave>`. `gpu` — the GPU set it occupied, opaque identity. Both are read from
  `.status.json`; before the run starts they come from the generated script's path.
- Statuses: `todo` → `inpr` → `done` | `failed`. `started`/`ended` as `MM-DD HH:MM` (year only if ambiguous), `elapsed` compact (`45m`, `1h45m`, `2d3h`) from `elapsed_s`. **Elapsed values are the project's reference runtimes** — use them to estimate wall-clock and split temporal budgets when planning future waves.
- `progress` — render schema-v2 `progress_unit progress_completed/progress_total`.
- `eta` — the **only computed column**. Recompute it on **every** reconciliation pass, and leave
  it blank for `todo`/`done`/`failed` rows and whenever there is no sound basis. Never write ETA
  into `.status.json` or present it as measured fact — say `estimated` and state the basis when
  reporting it.

## ETA calculation and wave roll-up

Use this deterministic hierarchy:

1. For an active schema-v2 run with `0 < progress_completed < progress_total`, calculate
   `remaining_s = elapsed_s * (progress_total - progress_completed) / progress_completed`.
   Its estimated completion is the observation time plus `remaining_s`; label the basis
   `structured progress`.
2. At zero/missing structured progress, use the median `elapsed_s` of prior `done` rows with the
   same run_id, minus current elapsed, floored at zero (`exact-run history`). If none exist, use
   the median of all provenance-valid `done` rows in the same experiment (`experiment median`).
3. A schema-v2 heartbeat older than three minutes, invalid numeric bounds, integrity mismatch,
   or unreachable rig makes the active ETA unavailable until resolved. State the reason.

For a lane, add its active run's remaining estimate to the estimated full runtimes of queued runs
in deterministic script/glob order, using the same exact-run then experiment-median hierarchy.
If any required term lacks a basis, report that lane ETA as unavailable. The wave ETA is the
latest available lane completion only when every nonterminal lane is estimable; otherwise report
the wave ETA as unavailable and name the blocking lane(s). Do not put queued-run estimates into
their `eta` table cells: lane/wave roll-ups belong in chat reports, while the table's `eta` stays
an active-run field.

On a run_id re-election (a param joins `RUN_ID_PARAMS` — `experiment-design` skill, "run_id evolution"): update the section header **and** add the new column to existing rows, backfilled with the old implicit value — same turn as the artifact migration, so rows and on-disk paths never disagree.

## Who writes what (same turn as the action)

- Wave generation ⇒ append `todo` rows (one per generated run), pre-filled with `wave`, `rig`, `gpu` from the generated script paths.
- Launching ⇒ flip those rows to `inpr`, fill `started` (from `.status.json` once the run actually starts).
- Observing progress ⇒ refresh `progress` and recompute `eta`; during a launch chat do this before
  every fixed ten-minute report as well as on urgent transitions.
- Observing completion ⇒ flip to `done`/`failed` per the signals, fill `ended` + `elapsed`, clear `eta`.

## Reconciliation (mandatory)

Whenever a session on rig-4090 touches the project — and always when asked about statuses — sweep the signals and fix any stale rows. Match a `.status.json` to its row by **(run_id, `wave_id`)**; a run with several rows only ever has one live one (its latest wave). Missing `wave_id` or schema-v2 provenance makes the project unsupported. Resolve `wave--<wave_id>` and require `.status.json`'s `source_tag` and `source_revision` to match it, then require `environment_fingerprint` to match the value in that wave's README/scripts. A missing or mismatched source/environment value is an integrity error: flag it prominently and do not reconcile that row automatically or accept its artifact for comparisons. Then: `inpr` whose `.status.json` says `done`/`failed` **and** whose artifacts agree ⇒ flip and fill `ended`/`elapsed`; `done` in `.status.json` but expected final artifact missing ⇒ artifacts win, flag it to the user instead of marking done; `inpr` whose `.status.json` is stuck at `running` with a frozen heartbeat **because of a machine fault** — the rig's boot time (`uptime -s`) postdates the heartbeat, or the lane's tmux session is gone — ⇒ the run is **interrupted**, not failed-by-code: keep `inpr` if a relaunched lane is (or is about to be) re-executing it, otherwise flip back to `todo` with a note; when reporting, always distinguish "failed (code error)" from "interrupted (machine fault — safe to relaunch, wave scripts are self-guarded)"; rows whose evaluations dir vanished ⇒ back to `todo` (outputs were deleted, timing cleared). Recompute `eta` for every `inpr` row while you are here. Reconciliation is deterministic: md always converges to the signals.
