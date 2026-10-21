---
name: experiments-tracking
description: Keep a Research 2.0 EXPERIMENTS.md true, derive active-run, lane, and wave ETAs from provenance-valid status plus artifacts, and prune finished wave worktrees. Use when runs are generated, launched, checked, reported, or discussed, when the user asks about experiment status or ETA, or at session start to reconcile factual run state; do not interpret or migrate legacy tracking layouts.
---

# experiments-tracking

EXPERIMENTS.md is the project's **state** (the story lives in JOURNAL.md). It is derived from the
three on-disk signals in `../research-project-init/references/conventions.md` § Status ground
truth; when they disagree with the file, the file is wrong.

- Edit it only on the hub (`rig-4090`). While a wave is supervised, its `sweep-supervisor` agent
  writes that wave's rows.
- One row per (run, wave), one line each: a run relaunched in a later wave gets a new row. Rows
  are not rewritten; the exception is a run_id migration (`run-identity`), whose `apply` replaces
  hashes and whose `followups.json` drives the follow-up here: the preamble's `run_id version:`
  and params line, the new param columns, and each row's new values and `label`.
- A row's identity is its params' values; the `run_id` cell is `run_id_name` (`rid-<32 hex>`) and
  `label` is `run_id_label`. Resolve a hash through the run's `.run_id.json`, `RUN_ID_MAP.json`, or
  `run-identity`, never by hand. Use the exact paths recorded in the wave's README and scripts.
- A status answer opens with `Status written <YYYY-MM-DD at HH:MM> —` (local time taken right
  before sending, e.g. `Status written 2026-09-21 at 09:10 —`).

## Format

```markdown
## 000_grokking
run_id path layout: hashed-v2   (mirrors RUN_ID_PATH_LAYOUT in code/000_grokking/train.py)
run_id version: 1 — model, lr, wd, seed   (mirrors config/000_grokking/RUN_ID_VERSIONS.json)
run_id map: evaluations/000_grokking/RUN_ID_MAP.json
smoke command: <environment.name>/bin/python code/000_grokking/train.py smoke=true steps=1 seed=smoke
smoke pass: exit 0 and evaluations/000_grokking/smoke/result.json says one step completed

| model | lr   | wd   | seed | run_id                               | label                              | wave            | rig         | gpu | status | started     | progress   | eta         | ended       | elapsed | notes |
|-------|------|------|------|--------------------------------------|------------------------------------|-----------------|-------------|-----|--------|-------------|------------|-------------|-------------|---------|-------|
| mlp   | 1e-3 | 0.01 | 0    | rid-4c5ffc9409ae4575b2e0d1c3a7f69e18 | model=mlp,lr=1e-3,wd=0.01,seed=0   | 20260731-162043 | rig-4090    | 0   | failed | 07-31 16:20 | epoch 3/10 |             | 07-31 16:58 | 38m     | OOM   |
| mlp   | 1e-3 | 0.01 | 0    | rid-4c5ffc9409ae4575b2e0d1c3a7f69e18 | model=mlp,lr=1e-3,wd=0.01,seed=0   | 20260805-081200 | behemoth    | 0   | inpr   | 08-05 08:12 | epoch 7/10 | 08-05 09:40 |             |         |       |
| tr-xl | 1e-4 | 0.01 | 0    | rid-6961a90e5ffca50c93d7e2b14a08c5f6 | model=tr-xl,lr=1e-4,wd=0.01,seed=0 | 20260803-141000 | rig-3090-ti | 0   | done   | 08-03 14:10 | 10/10      |             | 08-03 18:02 | 3h52m   |       |
```

The preamble also carries the design-time records (`min_vram_mib`, `data needs`, final artifact,
per-run footprint). One column per identity param, then `run_id` and `label`.

- `wave` — the dispatch (`YYYYMMDD-HHMMSS`, tag `wave--<wave>`). `rig` and `gpu` — the lane that
  claimed the run, blank while `todo`, filled from the supervisor ledger's `run-assigned` event. A
  moved run keeps its row and says so in `notes`. A `leonardo` job is `rig=leonardo`, `gpu=1`,
  job id(s) in `notes`.
- Statuses: `todo` → `inpr` → `done` | `failed`, plus terminal `superseded` (notes name the
  replacing wave).
- `started`/`ended` as `MM-DD HH:MM`; `elapsed` compact (`45m`, `1h45m`, `2d3h`) from `elapsed_s`.
  Elapsed values are the project's reference runtimes.
- `progress` — `progress_unit progress_completed/progress_total`.
- `eta` — the only computed column: recomputed on every pass for `inpr` rows, blank otherwise or
  when there is no basis. Report it as an estimate with its basis.

## ETA

1. Active run with `0 < progress_completed < progress_total`:
   `remaining_s = elapsed_s * (progress_total - progress_completed) / progress_completed`
   (basis `structured progress`).
2. Otherwise, in order: the median runtime of this wave's own `done` runs on that rig (`measured
   this wave`); the same run_id on the same rig (`exact-run history`); the same run_id on other
   rigs, then the experiment median, both in rig-independent cost `cost = elapsed_s ×
   weight(rig)` predicted as `median(cost) / weight(target rig)` (`… (weight-normalized)`). Never
   pool runtimes across rigs unnormalized.
3. A heartbeat older than three minutes, invalid bounds, an integrity mismatch, or an unreachable
   rig makes the ETA unavailable; say why.

For a lane, add its active run's remaining estimate to the runtimes of the runs already in its
buffer. The wave ETA is the latest available lane completion only when every nonterminal lane is
estimable; otherwise it is unavailable, naming the blocking lanes. `sweep-supervisor` computes
exactly this for its table; use its numbers for supervised waves.

Questions about the fleet ("are the GPUs free", another project's runs) go to `rig-board`
(`status --reconcile`); the board never changes a row here.

## Reconciliation

On the hub, whenever the project is touched and always when asked about status:

1. Match each status to its row by (run identity, `wave_id`), reading the status path from that
   wave's record; `superseded` rows are skipped.
2. Require the status's `source_tag`/`source_revision` to match `wave--<wave_id>` and its
   `environment_fingerprint` to match the wave's README. A mismatch is an integrity error: flag it
   and leave the row.
3. Then: status `done`/`failed` with the recorded artifacts agreeing ⇒ flip and fill
   `ended`/`elapsed`. Status `done` with the final artifact (or its `.offloaded.json` receipt)
   missing ⇒ flag it, do not mark done. Frozen `running` from a machine fault (boot time after the
   heartbeat, or the lane's tmux session gone) ⇒ interrupted, not failed: keep `inpr` if a
   relaunch is re-executing it, else back to `todo` with a note. Evaluation directory gone ⇒ back
   to `todo`. Report code failures and interruptions as different things.
4. A wave launched before wave isolation (no `.waves/<wave_id>`) is reconciled but flagged as not
   recoverable.

Then prune: compare `rig-sync activity --machines <project machines>` with the rows; every wave
whose rows are all terminal but whose worktree remains, plus unused keyed environments, goes
through `rig-sync prune --machines <...> --waves <ids> --dry-run`, then `--confirm`. A wave with
failed runs may be pruned: recovery recreates its worktree from the tag.
