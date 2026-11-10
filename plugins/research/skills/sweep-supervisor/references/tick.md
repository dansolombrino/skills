# The unattended supervisor tick

You were started by the hub service as a fresh process for **one** tick of one wave, in the
project root. Nobody is watching and nobody can answer. Work from the files; act through the
scripts; write down what you decided; exit. Read the wave README first: everything you do stays
inside the **pool** and the **standing authorization** written there; anything beyond them is the
user's (the Authority section of [SKILL.md](../SKILL.md)) and goes through `ask`.

`SUP` below is `python3 "$SWEEP_SUPERVISOR_SCRIPT" --root . <command> --wave <wave_id>`; your prompt
spells out the path.

Any commit you make goes directly on the branch `sync.toml` names under `[git]`, whatever your host's
default about branching before a commit. Never create or switch to another branch or worktree here.

## 1. Read

- `SUP ledger` — every event since the last tick that exited cleanly began, so nothing that arrived
  during it or behind a failed tick is hidden, and something already handled can reappear (its `decide`
  line says). `SUP snapshot` — lanes, runs, ETAs.
- For each `run-failed` / `job-failed`: the tail of that run's newest log under
  `logs/<NNN_exp>/<run_id folder>/wave_<wave_id>/` **on the rig that ran it** (the ledger names
  the rig), and its `.status.json`. Use bounded, non-interactive SSH
  (`ssh -o BatchMode=yes -o ConnectTimeout=10 <rig> ...`); never open an interactive session.
- `questions.json` — answers that arrived.
- Any `collect-failed` event: the rig, the run, and whether it is a transient ssh failure or an
  identity mismatch.
- Every event that needs judgment wakes you at once. A `scheduled tick` (half-hourly by default) runs
  only when a person edited `EXPERIMENTS.md`, a `budget-cap` or `checkout-off-branch` arrived, the
  wave is terminal, or the last tick failed; routine progress never causes one. An `idle tick` (six
  hours of nothing, by default) is the safety net for what raises no event: one of our cards free and
  not yet a lane, a run stalled under a live heartbeat, drift in `EXPERIMENTS.md`. Check for those,
  record "nothing to do" if so, and exit.

## 2. Judge, then act

Classify before acting; one failure is a run problem, the same failure on every run of a config is
a code problem.

| finding | action |
|---|---|
| out of memory on a small card | `queue constrain --min-vram-mib <n>` for that run **and every queued run of the same shape**, then `queue requeue` |
| missing dataset or weights on that rig | verify the hub copy, `rig-sync sync-cache --var <declared cache> --subpath <rel> --to <rig>` (or the project's own fetch inside the declared cache), verify size or checksum on the rig, `data mark-ready --rig <rig> --tag <tag>`, `queue requeue`. A dataset that cannot fit leaves that rig ineligible for those runs only |
| deterministic engineering error (a traceback in the experiment code, a wrong path, a wave-script or generator bug) | do **not** requeue. `queue drop` the queued runs that would hit the same line only when the evidence is the same traceback. Then `followup --kind fix --text "<the error, file:line, the evidence, the runs it breaks>"`: the service fixes it and dispatches those runs again, unattended. Only when the fix would change what the experiment measures, or `followup` refuses (two automatic rounds already), `ask` with the diagnosis and the proposed patch |
| flaky or machine-caused failure | `queue requeue`, once; a second identical failure is a code error |
| `heartbeat-stale` (a run's heartbeat is over three minutes old), rig up, session alive | a hang: report it with evidence; steal only when the process is provably idle (no GPU utilisation, log silent past several heartbeats) |
| lane `blocked` (revision or environment verification failed) | re-run `rig-sync deploy-revision` / `environment-sync provision` for that wave on that pool rig, then `lane unblock`; if it still fails, leave it blocked and report |
| lane stopped on 88 | `lane offload-on --rig <rig>` and `lane unblock` if blocked; low disk never removes a rig |
| cluster `job-timeout` | the service already resized it from measured progress; confirm the number is sane, else `queue constrain --walltime-s`. A run that restarts from scratch and cannot fit 24 h goes to the user |
| `budget-cap` reached (only when the user set a cap) | `decide` reporting it; the cluster takes no new jobs until the user raises the cap with `pool --rig leonardo --core-hours-cap <n> --reason "user: ..."`; the lanes keep running |
| `collect-failed` | read the problem. A transient ssh failure needs nothing: the next cycle retries. An identity mismatch (`.run_id.json`/`.run_config.json` differ between rig and hub) → `ask`, and never overwrite either copy |
| `claim-refused` / `lane-yielded` (lane `waiting for board`) | nothing to do: the lane is in the pool and starts the cycle its holder releases it. The service asks for a held card only when its plan would use it, hands a card to a wave that goes first (higher `priority`, then clearly less work left) between runs, and gives back one of ours the plan no longer feeds. Never change a wave's `priority`: it is the user's. If a hold looks stale, `rig-board status --reconcile` evidence goes to the user, never a forced release |
| one of our cards on a pool rig is free and not yet a lane | `lane add --rig <rig> --gpu <ids> --reason "<why>"`; the service runs the revision and environment gates before it starts the lane. A shared rig's card beyond ours needs the user's grant: `ask` |
| `rig-down` while the printout shows the rig's SSH certificate expired | nothing to ask: the service already tried any declared `cert_renew_command` and issued its notice and warning, and it resumes the rig when it answers. `decide` what keeps running meanwhile |
| `checkout-off-branch` (`⚠ project checkout is on branch …`) | a person may be working there: never merge, switch or commit around it, and leave `deploy-revision` blocked. `say` what it blocks, once |

### Tail stealing

Near the end a fast lane idles while a slow lane works through its last runs; the service wakes
you with `lane-idle-tail` the cycle that happens. The service already keeps the last long runs off
slow lanes and takes back buffered runs another lane finishes clearly sooner, so what is left is
mostly in-flight runs:

- A **buffered** run (not started) the service left in place is free to move when evidence says
  its estimate is wrong: `steal --run <id> --reason "<why>" --front --confirm`.
- An **in-flight** run is stolen only when all hold: finishing it on the idle lane — from its
  newest checkpoint when the run resumes, from zero when it does not — beats leaving it by a clear
  margin (at least 20% of its remaining time and at least ten minutes); the work thrown away is
  small relative to that gain; and nothing else is queued for the idle lane. Otherwise leave it.
- A resuming run needs its checkpoint on the new rig first: `rig-sync restore --machine <rig>
  --wave <wave_id> --run <id>` (after an `offload` pass if the source rig is not the hub).
- `steal` proves ownership and refuses otherwise. Never bypass a refusal.

## 3. Record

- Reconcile `EXPERIMENTS.md` per `experiments-tracking` — during a wave the ticks are its only
  writers, and the service runs one tick at a time per project, so no other wave's tick edits or
  commits while you do: rows flip from the ledger and the on-disk signals, `rig`/`gpu` are filled
  from the lane that claimed the run, `eta` is recomputed. Rows may be several hours behind when you
  start (routine progress wakes nobody): bring every row of this wave up to date, not only the
  event's.
- One `SUP decide "<what, why, evidence>"` per decision, including "nothing to do" when an event
  needed no action. This log is the only decision record; the user reads it under the table.
- Journal a meaningful scientific or engineering event per `research-journal` (append; no
  prompt).

## 4. Ask without blocking

Anything outside the pool or the standing authorization:
`SUP ask --text "<one decision, with the numbers>" --meanwhile "<what stays in force>"`. Then carry
on with everything that does not depend on the answer. Never wait, never retry the question, and
never treat silence as consent. Something the user should know but need not decide goes out with
`SUP say --text "<one or two lines>"`, sparingly: a watching chat gets it, and `notify_command` if declared.

## 5. Finish

When the snapshot says every run is terminal, the service has already run a final
`rigsync collect` from every rig. Check `wave-terminal.collect_failed` and any `collect-failed`
events. Run `rigsync collect --check --wave <id>` and settle what it lists (an identity mismatch
needs the user) before you rebuild the run_id map. Then do the final reconcile, rebuild the run_id
map once per machine whose evaluations tree received runs
(`write_run_id_map(<evaluations experiment dir>, layout=RUN_ID_PATH_LAYOUT)`, plus
`spec_root=<root>/.waves/<wave_id>/config/<experiment_path>` under `hashed-v2` — that machine's
wave worktree, which carries the tracked identity files the wave ran with — never per run), add
a closing `decide` with counts and open problems, then `SUP finish --confirm`. `finish` prunes the
wave on every machine and queues its `analyze` follow-up (plots and the journal entry); never prune
or plot from a tick yourself.

Exit when done. The next tick starts from the files, not from your memory.
