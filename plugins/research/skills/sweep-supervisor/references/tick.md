# The unattended supervisor tick

You were started by the hub service as a fresh process for **one** tick of one wave, in the
project root. Nobody is watching and nobody can answer. Work from the files; act through the
scripts; write down what you decided; exit. Read `program/00-execution-agreement.md` and the wave
README first: the wave's **pool** and its standing authorization are your envelope.

`SUP` below is `python3 <this skill's scripts/supervisor.py> --root . <command> --wave <wave_id>`.

## 1. Read

- `SUP ledger` — every event since the previous tick. `SUP snapshot` — lanes, runs, ETAs.
- For each `run-failed` / `job-failed`: the tail of that run's newest log under
  `logs/<NNN_exp>/<run_id folder>/wave_<wave_id>/` **on the rig that ran it** (the ledger names
  the rig), and its `.status.json`. Use bounded, non-interactive SSH
  (`ssh -o BatchMode=yes -o ConnectTimeout=10 <rig> ...`); never open an interactive session.
- `questions.json` — answers that arrived.

## 2. Judge, then act

Classify before acting; one failure is a run problem, the same failure on every run of a config is
a code problem.

| finding | action |
|---|---|
| out of memory on a small card | `queue constrain --min-vram-mib <n>` for that run **and every queued run of the same shape**, then `queue requeue` |
| missing dataset or weights on that rig | verify the hub copy, `rig-sync sync-cache --var <declared cache> --subpath <rel> --to <rig>` (or the project's own fetch inside the declared cache), verify size or checksum on the rig, `data mark-ready --rig <rig> --tag <tag>`, `queue requeue`. A dataset that cannot fit leaves that rig ineligible for those runs only |
| deterministic code error | do **not** requeue. `queue drop` the queued runs that would hit the same line only when the evidence is the same traceback; otherwise leave them. Report |
| flaky or machine-caused failure | `queue requeue`, once; a second identical failure is a code error |
| stale heartbeat, rig up, session alive | a hang: report it with evidence; steal only when the process is provably idle (no GPU utilisation, log silent past several heartbeats) |
| lane `blocked` (revision or environment verification failed) | re-run `rig-sync deploy-revision` / `environment-sync provision` for that wave inside the envelope, then `lane unblock`; if it still fails, leave it blocked and report |
| lane stopped on 88 | `lane offload-on --rig <rig>` and `lane unblock` if blocked; low disk never removes a rig |
| cluster `job-timeout` | the service already resized it from measured progress; confirm the number is sane, else `queue constrain --walltime-s`. A run that restarts from scratch and cannot fit 24 h goes to the user |
| `budget-cap` reached | `ask`; meanwhile the cluster takes no new jobs |
| a card in the envelope became free on the fleet board | run the full `rig-sync` and `environment-sync` gates for it; admitting a lane that was not in the pool is a pool change: `ask` |

### Tail stealing

Near the end a fast lane idles while a slow lane works through its last runs. For each idle lane,
consider the in-flight and buffered runs of slower lanes:

- A **buffered** run (not started) is free to move: `steal --run <id> --reason "<why>" --front --confirm`.
- An **in-flight** run is stolen only when all hold: finishing it on the idle lane — from its
  newest checkpoint when the run resumes, from zero when it does not — beats leaving it by a clear
  margin (at least 20% of its remaining time and at least ten minutes); the work thrown away is
  small relative to that gain; and nothing else is queued for the idle lane. Otherwise leave it.
- A resuming run needs its checkpoint on the new rig first: `rig-sync restore --machine <rig>
  --wave <wave_id> --run <id>` (after an `offload` pass if the source rig is not the hub).
- `steal` proves ownership and refuses otherwise. Never bypass a refusal.

## 3. Record

- Reconcile `EXPERIMENTS.md` per `experiments-tracking` — during a wave this tick is its single
  writer: rows flip from the ledger and the on-disk signals, `rig`/`gpu` are filled from the lane
  that claimed the run, `eta` is recomputed.
- One `SUP decide "<what, why, evidence>"` per decision, including "nothing to do" when an event
  needed no action. The user reads these under the table.
- Journal a meaningful scientific or engineering event per `research-journal`.

## 4. Ask without blocking

Anything outside the pool or the standing authorization:
`SUP ask --text "<one decision, with the numbers>" --meanwhile "<what stays in force>"`. Then carry
on with everything that does not depend on the answer. Never wait, never retry the question, and
never treat silence as consent.

## 5. Finish

When the snapshot says every run is terminal: final reconcile, rebuild the run_id map once per
machine whose evaluations tree received runs
(`write_run_id_map(<evaluations experiment dir>, layout=RUN_ID_PATH_LAYOUT)`, never per run), a
closing `decide` with counts and open problems, then `SUP finish --confirm`. Pruning stays a
proposal for the user; never prune from a tick.

Exit when done. The next tick starts from the files, not from your memory.
