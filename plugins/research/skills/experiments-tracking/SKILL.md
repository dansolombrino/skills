---
name: experiments-tracking
description: Keep a Research 2.0 EXPERIMENTS.md true and derive active-run, lane, and wave ETAs from provenance-valid schema-v2 status plus artifacts. Use when runs are generated, launched, checked, reported, or discussed, when the user asks about experiment status or ETA, or at session start to reconcile factual run state; do not interpret or migrate legacy tracking layouts.
---

# experiments-tracking

EXPERIMENTS.md is the project's **state** (the story lives in JOURNAL.md). Canon: `../research-project-init/references/conventions.md`.

Require the Research 2.0 scaffold and schema-v2 status contract. If either is absent, stop as
unsupported; do not offer migration or interpret legacy state. Factual reconciliation is automatic
in every engineering mode and does not require a narrative or scientific decision.

## Hard rules

- **Single-writer: EXPERIMENTS.md is only ever edited on rig-4090.** On any other rig, report statuses verbally but never edit the file. During a launch session, only the orchestrator chat writes it — monitoring subagents never do.
- Ground truth is NOT the md — it is the three on-disk signals (hierarchy in `conventions.md`): the per-run status first proves the current wave's Git revision/tag and environment fingerprint; after that, the **expected final artifact** is golden for completion, followed by `.status.json` state/timing/progress and the latest run log. A missing `.status.json`/dir means the run never started (or its outputs were deleted ⇒ it is no longer done).
- **One (run, wave) = one table row = one line.** A run re-launched in a later wave gets a **new row**, never an in-place update — the table itself carries the execution history. One line per row keeps parallel-session git merges clean.
- A row's run identity is semantic: the ordered `RUN_ID_PARAMS` values, independent of whether
  `RUN_ID_PATH_LAYOUT` is `nested`, `collapsed-v1`, or `hashed-v1`. Read the experiment's recorded selection
  and consume the exact checkpoint, evaluation, status, and final-artifact paths materialized in
  the generated wave record. Never derive identity from path segment count, reconstruct an
  artifact path from the flat id, or treat the layouts as different runs. Scripts, logs, and
  wandb remain keyed by the unchanged flat rendering. Under `hashed-v1`, resolve a hash through
  the run's `.run_id.json` or the experiment's `evaluations/<experiment_path>/RUN_ID_MAP.json`;
  never recompute or guess it.
- **Every status report to the user opens with its message-written time** — obtain a local
  timezone-bearing timestamp immediately before sending (for example
  `date '+%Y-%m-%dT%H:%M:%S%:z'`) and lead exactly with
  `Status written <timestamp> —`. Never substitute the status heartbeat/observation time; report
  heartbeat age separately.

## Format

One section per `NNN_experiment`; its preamble restates both authoritative source decisions: the
ordered `RUN_ID_PARAMS` and the literal `RUN_ID_PATH_LAYOUT` (`nested`, `collapsed-v1`, or
`hashed-v1`). The table has **one column per run_id param** (dict form — never a single
flat-string column), then placement, status, and timing. Under `hashed-v1` the preamble adds
`run_id map: evaluations/<experiment_path>/RUN_ID_MAP.json` and the table adds a `hash` column
right after the param columns, so params and hash are readable side by side. The layout line is durable experiment metadata, not a run-row
identity field:

```markdown
## 000_grokking
run_id params: model, lr, seed   (mirrors RUN_ID_PARAMS in code/000_grokking/train.py)
run_id path layout: nested   (mirrors RUN_ID_PATH_LAYOUT in code/000_grokking/train.py)
smoke command: <environment.name>/bin/python code/000_grokking/train.py smoke=true steps=1 seed=smoke
smoke pass: exit 0 and evaluations/000_grokking/smoke/result.json says one step completed

| model | lr   | seed | wave            | rig                | gpu     | status | started     | progress   | eta         | ended       | elapsed | notes |
|-------|------|------|-----------------|--------------------|---------|--------|-------------|------------|-------------|-------------|---------|-------|
| mlp   | 1e-3 | 0    | 20260731-162043 | rig-4090           | 0       | failed | 07-31 16:20 | epoch 3/10 |             | 07-31 16:58 | 38m     | OOM   |
| mlp   | 1e-3 | 0    | 20260805-081200 | behemoth           | 0       | inpr   | 08-05 08:12 | epoch 7/10 | 08-05 09:40 |             |         |       |
| tr-xl | 1e-4 | 0    | 20260803-141000 | rig-3090-ti        | 0       | done   | 08-03 14:10 | 10/10      |             | 08-03 18:02 | 3h52m   |       |
```

- `wave` — the dispatch this execution belonged to (`YYYYMMDD-HHMMSS`, canon), whose annotated
  Git tag is `wave--<wave>`. `gpu` — the GPU set it occupied, opaque identity. Both are read from
  `.status.json`; before the run starts they come from the generated script's path. A run executed
as a Slurm job on `leonardo` has `rig=leonardo`, `gpu=1` (the count requested, since the card index
is Slurm's), and its job id(s) in `notes`.
- Statuses: `todo` → `inpr` → `done` | `failed`. `started`/`ended` use `MM-DD HH:MM` (year only if
  ambiguous), and `elapsed` is compact (`45m`, `1h45m`, `2d3h`) from `elapsed_s`. **Elapsed values
  are the project's reference runtimes** — use them to estimate wall-clock and split temporal
  budgets when planning future waves.
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
2. At zero/missing structured progress, estimate from `done` rows in **rig-independent cost
   units**: `cost = elapsed_s × weight(rig)`, using the fixed per-GPU speed weights in
   `../research-project-init/references/conventions.md` § Rig fleet, and predict on the target rig
   as `elapsed_pred = median(cost) / weight(target rig)`. Prefer prior `done` rows with the same
   run_id **on the same rig** — those need no normalization, basis `exact-run history`; then the
   same run_id on other rigs, basis `exact-run history (weight-normalized)`; then all
   provenance-valid `done` rows in the same experiment, basis `experiment median
   (weight-normalized)`. For an active run subtract its current elapsed and floor at zero. Never
   pool elapsed values across rigs unnormalized: a behemoth runtime applied to a queued
   `rig-3090-ti` run understates it fourfold. The weights are fixed nominal throughput priors, not
   measurements, so a normalized basis is always weaker than a same-rig one — report which it is.
3. A schema-v2 heartbeat older than three minutes, invalid numeric bounds, integrity mismatch,
   or unreachable rig makes the active ETA unavailable until resolved. State the reason.

For a lane, add its active run's remaining estimate to the estimated full runtimes of queued runs
in deterministic script/glob order, predicting every term for **that lane's own rig** with the
hierarchy above.
If any required term lacks a basis, report that lane ETA as unavailable. The wave ETA is the
latest available lane completion only when every nonterminal lane is estimable; otherwise report
the wave ETA as unavailable and name the blocking lane(s). Do not put queued-run estimates into
their `eta` table cells: lane/wave roll-ups belong in chat reports, while the table's `eta` stays
an active-run field.

`RUN_ID_PARAMS` may change only while no checkpoint, evaluation, or plot output and no wave README
or script exists. After the first such surface, any identity-schema change under `nested`,
`collapsed-v1`, or `hashed-v1` requires a new numbered sub-experiment; never backfill, rename, move, or rewrite the
established tree. The recorded `RUN_ID_PATH_LAYOUT` is immutable after the same boundary, and exact
wave paths remain authoritative. A layout mismatch or mixed checkpoint/evaluation tree blocks
reconciliation and routes changed work to a new numbered sub-experiment; never move, rename, or
rewrite old artifacts or wave records. The new sub-experiment gets a fresh EXPERIMENTS section with
no edits to historical rows.

## Fleet questions go to the board

EXPERIMENTS.md sees one project. "Are the GPUs free", "what is running on the 3090", "wait for
the rigs", or any question about another project's runs is answered from `rig-board`
(`status --reconcile`), which lists every held lane across projects with holder, wave, active
run, progress, ETA and basis, plus foreign cards and unreachable rigs. Report it with the same
`Status written <timestamp> —` opener and the probe age. Waiting uses the host's recurring
wait primitive with a board check per tick, never shell `sleep`. The board never changes a row
here: a lane on the board is not evidence that a run is done, failed, or even started.

## Who writes what (same turn as the action)

- Wave generation ⇒ append `todo` rows (one per generated run), pre-filled with `wave`, `rig`, `gpu` from the generated script paths.
- Launching ⇒ flip those rows to `inpr`, fill `started` (from `.status.json` once the run actually starts).
- Observing progress ⇒ refresh `progress` and recompute `eta`; during a launch chat do this before
  every fixed ten-minute report as well as on urgent transitions.
- Observing completion ⇒ flip to `done`/`failed` per the signals, fill `ended` + `elapsed`, clear `eta`.

## Reconciliation (mandatory)

Whenever a session on rig-4090 touches the project — and always when asked about statuses — sweep
the signals and fix any stale rows. Match a status to its row by **(semantic run identity,
`wave_id`)**, reading the exact status path from that wave's generated record; a run with several
rows only ever has one live one (its latest wave). Missing `wave_id` or schema-v2 provenance makes
the project unsupported. Resolve `wave--<wave_id>` and require the status's `source_tag` and
`source_revision` to match it, then require `environment_fingerprint` to match the value in that
wave's README/scripts. A missing or mismatched source/environment value is an integrity error: flag
it prominently and do not reconcile that row automatically or accept its artifact for comparisons.
Then: `inpr` whose status says `done`/`failed` **and** whose exact recorded artifacts agree ⇒ flip
and fill `ended`/`elapsed`; `done` in status but the exact recorded final artifact is missing ⇒
artifacts win, flag it to the user instead of marking done; `inpr` whose status is stuck at
`running` with a frozen heartbeat **because of a machine fault** — the rig's boot time (`uptime -s`)
postdates the heartbeat, or the lane's tmux session is gone — ⇒ the run is **interrupted**, not
failed-by-code: keep `inpr` if a relaunched lane is (or is about to be) re-executing it, otherwise
flip back to `todo` with a note; when reporting, always distinguish "failed (code error)" from
"interrupted (machine fault — safe to relaunch, wave scripts are self-guarded)"; rows whose exact
recorded evaluation directory vanished ⇒ back to `todo` (outputs were deleted, timing cleared). Any
path disagreement or mixed tree blocks instead of substituting different paths. Recompute `eta` for
every `inpr` row while you are here. Reconciliation is deterministic: md always converges to the
recorded signals without inferring slash depth.
