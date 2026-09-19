---
name: sweep-dispatch
description: Generate, launch, and continuously monitor experiment runs/sweeps across the GPU rigs (rig-4090, rig-3090-ti, rig-3080-ti, behemoth — also "4090", "3080 ti", "pro 6000", "bw", "blackwell", "server-pro-6000-bw") and the CINECA Leonardo Slurm cluster ("leonardo", "cineca", "slurm", "sbatch", "HPC"). Use when the user asks to run, launch, sweep, dispatch, or submit experiments or Slurm jobs, receive timestamped ten-minute status/ETA updates, monitor or babysit running experiments or queued jobs, split runs across machines, GPUs, or the cluster, rerun failed runs, supersede in-flight runs with a newer wave, run several waves of one project at once, resubmit jobs, fetch results from the cluster, or recover/resume a wave after a rig crash, reboot, or job interruption.
---

# sweep-dispatch

Launch machinery for runs and sweeps. Canon: `../research-project-init/references/conventions.md`. Templates: [references/templates.md](references/templates.md). CINECA Leonardo is one more target: when a wave assigns any run to `leonardo`, read [references/cineca-slurm.md](references/cineca-slurm.md) before gate 2 and follow it wherever it differs from this file; do not load it otherwise. Dispatch always happens **from rig-4090** (the hub). GitHub distributes one tested, tagged commit through `rig-sync`, which materializes it as the wave's own detached worktree `.waves/<wave_id>/` on every assigned machine; `environment-sync` establishes runtime parity in the wave's lock-keyed environment `.envs/<env_key>/`; rsync is only for artifacts. If either gate fails, stop. Because every wave executes from its own worktree and environment, waves of different revisions may run on the same machine at the same time; GPU exclusivity stays `rig-board`'s job.

Read `program/00-execution-agreement.md` first and require the Research 2.0 surfaces. Stop on a
legacy layout; do not offer migration or recover it as-is. In engineering-manual mode, preview and
wait at the assignment/deployment/launch gate. In engineering-auto mode, choose and execute only
inside the approved repo, branch, rigs/GPUs, compute/budget, and destination envelope. Shared
`behemoth` cards beyond gpu0, new destinations, destructive actions, and envelope expansion remain
protected in every mode.

Vocabulary (canon): a **wave** is one dispatch decision, identified by `YYYYMMDD-HHMMSS`; a **slice** is the portion of a wave on one rig; a **lane** is the portion of a slice on one GPU set. Lanes run in parallel, runs within a lane run sequentially. A **run** is one experiment execution; a **job** is one Slurm allocation on `leonardo`, which hosts exactly one run — the cluster has jobs, not lanes. Use both words strictly and never call a job a run or a lane.

## Before anything launches — mandatory gates

1. **run_id coverage check** — every param varied in the sweep grid, AND every behavior-affecting
config param added/changed since this experiment's last runs, must be in `RUN_ID_PARAMS`. If not,
**halt** and route through `experiment-design` before generating anything — otherwise new runs
collide with old artifacts: overwritten, or silently skipped as "done". `RUN_ID_PARAMS` may change
only while no checkpoint, evaluation, or plot output and no wave README or script exists. After the
first such surface, any identity-schema change under `segments-v1`, `nested`, `collapsed-v1`, or `hashed-v1`
(including any change to `RUN_ID_SEGMENTS`) requires a new numbered sub-experiment; never backfill, rename, move, or rewrite the established tree. The artifact
guard is only sound under this gate: `guard_run_config` catches collisions after Python starts, but
an artifact-skipped run never enters Python.
1b. **helper safety check** — verify `code/common/run_id.py` provides the canonical percent-encoded
`run_id_path(cfg, params, *, layout='nested', segments=None)`/`run_id_name`/`run_id_flat`
renderers with `segments-v1` support, `check_run_id_plan`, `preflight_run_id_path`, the
`.run_id.json`/`RUN_ID_MAP.json` writer (plus legacy `hashed-v1` support when the experiment pins
that layout) with atomic identity writes and a `guard_run_config` that never scans other runs or
rewrites the map (a per-run map rebuild is quadratic in the wave and cannot finish at scale), `hydra_override_arg`, and
the single config resolver
`resolved_config`/`wandb_config` (a wandb run whose config is anything less than the whole
resolved config is unfixable after the fact). Any older helper makes the project
unsupported; halt without upgrading it in place.
1c. **run-path layout check** — read the experiment's literal `RUN_ID_PATH_LAYOUT`; it must be
`segments-v1` (with the experiment's recorded `RUN_ID_SEGMENTS`) or a legacy `nested`,
`collapsed-v1`, or `hashed-v1` pin. Treat that record as pinned for the project, pass it to the canonical
`run_id_path` helper, and resolve the checkpoint and evaluation run directories before generating
the wave. They must use the same selected layout. `plots/` is not run-scoped. Under `segments-v1`,
run `check_run_id_plan(<planned cfgs>, RUN_ID_PARAMS, RUN_ID_SEGMENTS, <evaluations experiment dir>,
path_roots=(<checkpoints experiment dir>, <scripts experiment dir>, <logs experiment dir>))` over
the whole wave on the hub before generating anything; any overflow, duplicate, path collision, or
group-hash collision blocks dispatch and routes back to `experiment-design`. Never infer the layout from directory depth, reconstruct it from a
flat id, or silently adopt a different layout. A missing, unsupported, or mixed layout blocks
dispatch. Once any checkpoint, evaluation, or plot output or any wave README/script exists, its
recorded `RUN_ID_PATH_LAYOUT` is immutable. Any disagreement or mixed tree blocks dispatch and
routes new work to a new numbered sub-experiment with a fresh layout/schema decision; never move,
rename, or rewrite the existing artifacts, README files, or scripts.
1d. **environment contract check** — require exact uv/Python pins, current `uv.lock`, the standard
`code/common/environment.py`, and the project GPU smoke. Waves run only in the lock-keyed
environment `environment-sync` builds under `.envs/`; the `[environment].name` directory is the
user's hub development environment, which no wave, smoke, or launch command uses or modifies.
Never let a launch-time command repair or relock an environment.
1g. **wave-isolation contract check** — require the project's `code/common/paths.py` to honour
`RESEARCH_PROJECT_ROOT` (storage and `.env` resolve against the shared project root; tracked
inputs through `source_path()`), the project `.gitignore` to ignore `.waves/` and `.envs/`, and
every wave script to follow the current template (`WAVE_TREE`, project-root working directory).
A project without it is unsupported: stop and name exactly which of these is missing. Do not
migrate it here. A wave launched before this contract (it has no `.waves/<wave_id>` worktree) may
be monitored and reconciled, never recovered or relaunched by this skill.
1h. **overlap refusal** — after the mandatory `experiments-tracking` reconciliation, refuse every
planned run whose semantic identity already has a `todo` or `inpr` row in another wave, and
cross-check live evidence with `rig-sync activity --machines <assigned set>` (tmux lanes, running
statuses, queued or running Slurm jobs, grouped by wave). Two waves on one run would write the same
status and artifact paths. Never silently drop or duplicate the run: offer the user either to
leave it out of this wave or to supersede the old placement (§ Supersede).
1e. **rig declaration check** — resolve each assigned rig's project path with
`rig-sync repo-path --machine <rig>` and its storage floor with
`rig-sync storage-env --machine <rig>`, and substitute those values into the wave scripts and the
dispatch, monitor, and recovery commands. Never type a project path or a quota filesystem by
hand: both are declared once, and a second copy is what goes stale. A rig missing from `sync.toml`
or the machine registry is not assignable — stop rather than guessing its layout.
1f. **telemetry contract check** — require schema-v2 `code/common/status.py` from
`research-project-init` and structured progress call sites in every target training/eval entrypoint.
The helper must provide timezone-aware timestamps, live elapsed time, and its automatic
60-second heartbeat; entrypoints call
`heartbeat(completed=<done>, total=<total>, unit=<label>)`. Display-only telemetry makes the
project unsupported. Telemetry does not join `RUN_ID_PARAMS`.
2. **Resolve usable rigs and GPUs.** First read the fleet board: `rig-board` `status --reconcile`
shows every lane held by any project on the hub, foreign cards, and unreachable rigs. Held and
foreign lanes are not free capacity in any mode. In manual mode, present the board and ask. In
auto mode, use only the named rig/GPU set in the envelope that the board shows free; if the
envelope's lane is held, stop and report the holder and its ETA rather than waiting silently or
moving elsewhere. Never infer availability from `nvidia-smi`. On
`behemoth`, gpu0 is the only default. Any additional named card requires an explicit per-wave user
grant regardless of mode and is never persisted. For `leonardo`, first pass the certificate
precondition in `references/cineca-slurm.md` (print the login commands and wait when it fails),
then require the project account for this wave and check its remaining budget with `saldo -b`;
every job is one GPU, 8 cores, 128 GB unless the user states that a run needs more.
3. **Build and authorize the assignment.** The objective is to **minimize wave completion by
equalizing predicted lane finish times**, never to hand each lane an equal run count: a lane's
capacity is `weight × free GPUs` and the fleet spans a fourfold spread, so an even split makes the
slowest card the wave's tail while the fastest idles. Estimate each run's cost on its candidate rig
with the `experiments-tracking` hierarchy, then assign runs **longest-first to the disjoint lane
with the earliest predicted finish**. Per-run costs are what let a grid of unequal runs balance
itself. In-lane order stays lexicographic — the glob is the manifest, and order never changes a
lane's total, only which run is in flight at a snapshot. Present or record run → rig/GPU, **each
lane's predicted finish plus the spread across lanes**, exact smoke command, staged files, journal
entry, branch/remote, environment key and fingerprint, provisioning dry run, commit/tag/push,
worktree deployment scope, and any supersede. A spread wider than 20% of the wave ETA is either rebalanced or justified in
the same breath — normally indivisibility: fewer runs than weighted capacity, or one dominant run.
Below that, run granularity is coarser than the gain. `leonardo` is outside the balancing: its
queue wait is not predictable, so the user decides how many runs go there, each becomes one job,
and its slice ETA is reported as scheduler estimate plus run estimate. Preview for that slice the
account, partition/QOS, resource line, per-job walltime and its basis, and the budget estimate
(`runs × walltime_h × 8` core-hours). Manual mode
waits for approval; auto mode records that every item is within the envelope. Restate any shared-GPU
grant verbatim.
4. **Mint the wave id** when the assignment is authorized: `date '+%Y%m%d-%H%M%S'` on rig-4090.
One id for the whole dispatch, shared by every rig and lane. Never use a semantic slug.

## Generate

One script kind. For each run in the wave, materialize:

```
scripts/NNN_exp/<run_id folder>/wave_<wave_id>/
    README.md                     # what this wave is + this run's placement; written once, never updated
    wave_<rig>_gpu<ids>.sh        # this run's invocation in this wave
```

- On `leonardo` the wave script is the same file with an `#SBATCH` header (`references/cineca-slurm.md`) and is submitted with `sbatch`; the guards below are unchanged and run inside the job.
- The wave script is **self-contained** (full python command with explicit hydra overrides, each rendered through `hydra_override_arg`) and **self-guarded**: it exits early only if its expected final artifact is present. A `.status.json` that says `done` while the artifact is absent is inconsistent, so the script warns and re-executes. That guard is what makes lane dispatch idempotent — there is no launcher file holding it. Whether a re-execution resumes or restarts was fixed at experiment design time, never re-asked here.
- It exports `CUDA_VISIBLE_DEVICES`, `WAVE_ID`, and the approved `ENVIRONMENT_FINGERPRINT`.
  Before experiment Python it recomputes the fingerprint with the configured environment's
  `bin/python`, compares it, and runs the bounded project GPU smoke without syncing.
  Drift/compatibility failure exits `87`.
  On `behemoth` it retains the per-wave authorization guard before the smoke. On a quota'd rig it
  also checks storage headroom first, exiting `88` rather than dying mid-checkpoint and leaving a
  truncated artifact that looks real. It captures its own
  timestamped log under the mirror path in `logs/`.
- The run folder comes from `code/common/run_id.py`. Under `segments-v1` it is the same
  `run_id_path(..., segments=RUN_ID_SEGMENTS)` directories as `evaluations/`, and the wandb run
  name, EXPERIMENTS.md `run_id` column, and Slurm job-name suffix are `run_id_name(...)`, so every
  surface shows one identity. Under a legacy pin the selected `RUN_ID_PATH_LAYOUT` never changes
  scripts, logs, or wandb naming: those remain flat `run_id_flat`.
  `ls scripts/NNN_exp/<run_id folder>/` is that run's execution history.
- **The filesystem encodes the assignment**: a run is on that rig and those GPUs precisely because
  `wave_<rig>_gpu<ids>.sh` exists in its wave folder. No separate assignment record may drift.
- Only `scripts/` and `logs/` are wave-scoped. `checkpoints/` and `evaluations/` stay
  run-scoped at paths produced by the canonical helper for the experiment's recorded layout;
  `plots/` is not run-scoped and never uses the helper.
  Materialize the exact checkpoint directory, evaluation directory, status path, and expected
  final artifact path into each generated wave script; never leave a monitor or recovery step to
  infer how many path segments the run id occupies. Artifacts must keep a stable per-run path or
  the self-guard and `guard_run_config` both break.
- `scripts/` contains shell only. **Never put yaml under scripts/** — the grid lives in the generation conversation, the wave README, and EXPERIMENTS.md rows.

## Test, commit, and deploy one revision

After generation and before any tmux launch:

1. Stage only the approved code/config/scripts/tracking/JOURNAL files. Require every execution
   file to be tracked and no unstaged or non-ignored untracked file under `code/`, `config/`,
   `scripts/`, or a root dependency manifest. Record a hash of the staged patch.
2. Run the experiment's design-time smoke command on rig-4090 with the environment for the
   **staged** lock: `environment-sync provision --staged --machines rig-4090` (a new key is built
   only if absent; authorize it like any provisioning), then `verify --staged --machines rig-4090`
   prints `ENVIRONMENT_DIR`; substitute it for the smoke command's `<env>` placeholder. Missing smoke
   metadata makes the project unsupported. After the smoke, require the same staged-patch hash and
   a clean execution tree; a test that rewrites source invalidates itself. The committed wave has
   the same environment key, so the hub's smoke environment is also its wave environment.
3. Commit as `dispatch(<NNN_exp>): wave <wave_id>`. Refuse an existing `wave--<wave_id>` ref, then
   create that annotated tag at `HEAD`. Push the configured branch and exact tag without force;
   verify GitHub resolves both to the full local commit SHA. A push failure blocks dispatch.
4. Run `rig-sync deploy-revision --wave <wave_id>` first as a dry run, obtain authorization under
   the active engineering mode, then confirm it for **all assigned rigs plus the hub together**. It
   adds the detached worktree `.waves/<wave_id>` at the tagged commit on each machine, only
   verifies one that already exists, and never moves any main checkout — so other waves keep
   running untouched. Run `verify-revision` across the same set. Then run the approved
   `environment-sync provision --wave <wave_id> --revision <sha>` when its dry run showed changes
   and `verify --wave <wave_id> --revision <sha>` for the full set plus every assigned lane; it
   prints the `ENVIRONMENT_DIR` and fingerprint the wave scripts carry. Do not launch unless
   source, fingerprint, and GPU smoke all pass.

Later commits on the hub (EXPERIMENTS.md, JOURNAL.md, the next wave) never affect a dispatched
wave: its code is read from its own worktree and its environment is never re-synced while it runs.

## Launch & monitor — orchestrator + one subagent per rig

The chat where the launch is requested is the **orchestrator**. It performs the all-rig Git deployment gate but never launches or polls rigs itself. Only after that gate passes, use the host's parallel background-subagent capability to spawn **one background subagent per assigned rig in parallel**, then collect their reports. One subagent per *rig*, not per lane. A peer subagent drives one SSH target; when the assigned rig is the current local hub, its subagent uses direct bounded commands and never requires self-SSH. If the host cannot run background subagents in parallel, stop before launch and tell the user; do not silently collapse monitoring into the orchestrator.

Before launch, also require a recurring wait/monitor primitive that lets the orchestrator remain
active and responsive for the full wave. If it is unavailable, stop before launch. The
orchestrator must not send its final response while any run remains queued or running unless the
user explicitly asks it to stop monitoring. Use the recurring wait primitive between events;
never implement monitoring with shell `sleep`.

Each rig subagent:

1. Runs `rig-sync verify-revision` and read-only `environment-sync verify --wave <wave_id>
   --revision <sha>` for its rig/lane immediately before launch, then **dispatches each
   lane** as its own named tmux session — except the `leonardo` subagent, which instead reruns the
   certificate precondition and **submits each job** with the guarded `sbatch` command in
   `references/cineca-slurm.md`, records every job id, and monitors with `squeue`/`sacct` plus the
   three signals; its recovery is resubmission under the same wave id, mapped from the Slurm state
   table there — — `<project>_<NNN_exp>_<wave_id>_<rig>_gpu<ids>` — running
   a glob loop over that lane's wave scripts (exact one-liner in
   [references/templates.md](references/templates.md)). Each queued script repeats the Git guard
   before Python. Every launch and recovery loop treats the complete reserved set as lane-stopping:
   source-drift `86`, environment-drift `87`, and insufficient storage `88`; ordinary run failures
   continue the queue.
   Sequencing remains a shell loop so it survives subagent death, compaction, and multi-day queues.
   Report each confirmed session back at once; the **orchestrator** then runs `rig-board` `claim`
   for that lane (rig, GPU set, project, experiment, wave id, hub project root, run count). A
   claim that exits 3 means another project holds the lane: kill nothing, report, and stop that
   lane. Subagents never write the board.
2. **Monitors** its queues to completion, judging each run by the three signals (hierarchy in
`conventions.md` — artifacts are golden). Read each run's exact status and artifact paths from its
generated wave record; do not recreate paths from the flat id or slash depth. Batch all lanes/status
files for that rig into bounded probes; in normal operation obtain at least one fresh snapshot every
five minutes so a scheduled chat update never depends on an arbitrarily old poll:
   - **expected final artifact** on disk (final checkpoint / final eval output) ⇒ truly done;
   - **`.status.json`** (`schema_version`, `state`, `heartbeat`, display + numeric progress,
    timing, `wave_id`, `gpu`, source tag/SHA, environment fingerprint);
   - tail of the latest **`logs/NNN_exp/<run_id folder>/wave_<wave_id>/wave_<rig>_gpu<ids>-<timestamp>.log`** — tracebacks/errors ⇒ failed; stale heartbeat + silent log ⇒ first rule out a machine fault (below), then suspect a hang and say so.
   Within the five-minute freshness cap, match poll intervals to expected run length (reference
   `elapsed` values in EXPERIMENTS.md help here) — don't hammer ssh.
3. **Reports back** snapshots with an observation timestamp and heartbeat age, per-run outcome
   (`queued`/`running`/`done`/`failed`/hung/`interrupted→relaunched`), lane, structured progress,
   timing, and queued order. Push terminal/fault/recovery transitions immediately; the
   orchestrator requests or uses the newest complete snapshot for each scheduled report.

**Single-writer rule: only the orchestrator edits EXPERIMENTS.md**, from the subagents' reports. Subagents never touch it.

Tell the user how to watch manually too: for a peer, `ssh <rig>` then
`tmux attach -t <project>_<NNN_exp>_<wave_id>_<rig>_gpu<ids>`; for the current local hub, run the
`tmux attach` command directly; for `leonardo`, `ssh leonardo squeue --me`.

## Machine faults — reboot/crash detection & auto-resume

Each rig subagent is also its rig's watchdog. A machine-level failure (crash, reboot, power loss) kills every tmux session on it and freezes each running run's `.status.json` at `running` — those are **interrupted** runs (machine fault), never `failed` ones (code error). Handle it with this state machine:

- **Unreachable** — ssh fails/times out. Use noninteractive bounded probes (`ssh -o BatchMode=yes -o ConnectTimeout=10 <rig> true`). One failed poll may be a network blip: declare the rig *down* only after 2–3 consecutive failures. Then keep probing reachability every 1–2 minutes and report once to the orchestrator: "rig down since <time>". Never mark its runs failed while it's down.
- **Back up — diagnose before acting** (one ssh round-trip; exact command in [references/templates.md](references/templates.md)): boot time (`uptime -s`) newer than the dispatch time ⇒ the rig rebooted (tmux never survives a reboot); a lane's session missing from `tmux ls` ⇒ that lane is gone even without a reboot; session alive and heartbeat advancing ⇒ it was only a network blip — resume normal polling, touch nothing.
- **Recover, lane by lane** — first re-run `rig-sync deploy-revision` for the wave (it verifies the
  existing worktree, or recreates a pruned one from the same tag, never touching another wave),
  then `verify-revision` and `environment-sync verify` for the wave's original SHA, environment
  key, fingerprint, and lane smoke; provision the key again only if it was pruned. If any step
  fails, report recovery blocked and do not alter the worktree. Otherwise re-issue that lane's
  dispatch one-liner **with the same wave id** (a relaunch is not a new dispatch: same paths,
  session names, tag, and commit). Completed runs skip; interrupted ones re-execute.
- **Recovery never re-decides GPU placement** — a relaunch re-executes the *same* wave script, so it inherits that wave's authorization (including its `# GPU auth:` header and guard) unchanged. Never widen a lane's GPU set during recovery, and never move a lane to a different card on `behemoth` to work around a busy GPU 0. If placement genuinely must change, that is a new wave, needing authorization under the active engineering mode and — for anything past gpu0 on `behemoth` — a fresh grant from the user.
- **Never double-launch** — immediately before relaunching a lane, check `tmux has-session -t <lane session>` again; if it exists, just monitor it.
- **Board across a fault** — `rig-board` `reconcile` marks the lane *interrupted* after a reboot and keeps it held, so no other project takes the card mid-recovery. After the relaunch, the orchestrator re-runs `claim` with the same session name (a re-claim, not a new hold). If recovery is blocked and the user abandons the lane, release it with that reason.
- **Unsupported layouts** — any noncanonical or mixed checkpoint/evaluation layout, or any wave
  whose recorded status/artifact paths disagree with the experiment's pinned `RUN_ID_PATH_LAYOUT`,
  blocks recovery. Never infer slash depth or substitute different paths. Route changed work to a
  new numbered sub-experiment; otherwise ordinary recovery continues only from the unchanged wave
  README/script, pin, tag, and revision.
- **Slurm jobs** — a `leonardo` job never sees a rig reboot: `NODE_FAIL`, `PREEMPTED`, and
  system cancellations are its machine faults, `TIMEOUT` is interrupted-by-walltime, `FAILED` and
  `OUT_OF_MEMORY` are code failures, and an ssh failure to the login node is first a certificate
  question. The mapping and the guarded resubmit command live in `references/cineca-slurm.md`.
- **Autonomous, not silent** — relaunching already-approved work is not a new dispatch decision:
  don't ask permission, recover and report — which runs were already done (skipped), which were
  interrupted and relaunched, in which lanes, and whether each resumes or restarts.

## Supersede — replacing in-flight placements of the same runs

Only after the user chose it at gate 1h, and with an explicit user authorization per action in
every engineering mode (cancelling work is destructive). Show the exact list first.

- **Slurm:** `scancel` exactly the overlapping jobs, by id (from `leonardo.jobs` and the
  `<old_wave_id>__<run_id_name>` job-name match); never by user, partition, or pattern.
- **tmux lane:** a lane cannot drop one queued script, so it is stopped whole
  (`tmux kill-session -t <lane session>`), and only when every non-terminal run in that lane is
  in the new wave; propose adding the missing ones instead of stranding them. Release the lane on
  `rig-board` with reason `superseded by wave <new_wave_id>`.
- **Rows:** flip the old wave's rows for those runs to `superseded` with a note naming the new
  wave (`experiments-tracking`). A superseded row is terminal and never recovered.
- **Partial state:** a superseded run may have left a checkpoint that the new wave would resume
  from, possibly across a code change. Ask the user per supersede: keep (resume) or quarantine.
  Quarantine is a rename to `<checkpoint dir>.superseded-<old_wave_id>`, never a deletion; the
  status file is simply overwritten by the new wave.
- Then generate and dispatch the new wave as usual; gate 1h now passes.

## Pruning finished waves

A terminal wave's worktree, and any keyed environment no remaining worktree uses, is disk that
nothing needs — but a failed run may still be recovered, and recovery recreates what was pruned.
So pruning is always **proposed by this skill and approved by the user**, never silent and never
skipped: when the final summary of a wave is posted, run
`rig-sync prune --machines <wave's machines plus the hub> --waves <wave_id> --dry-run`, show its
output, and ask. On approval run the same command with `--confirm`. `prune` refuses active or
dirty worktrees by itself; still list only waves whose rows are all terminal.

## Ten-minute status and ETA updates to the user

Anchor `next_update` to the confirmed launch time and advance it in exact 600-second increments;
collection or message latency must not drift later ticks. At every tick, even if nothing changed,
the orchestrator reconciles EXPERIMENTS.md from the newest per-rig snapshots, runs `rig-board`
`refresh` for each of its lanes (active run, progress, ETA and basis, runs done), and posts one
compact aggregate update. A completion, failure, suspected hang, rig outage, or recovery is reported as
soon as observed and does not reset `next_update`. When every run is terminal, `rig-board`
`release` every lane of the wave, rebuild the run_id map once with
`write_run_id_map(<evaluations experiment dir>, layout=RUN_ID_PATH_LAYOUT)` on each machine whose
evaluations tree received the wave's runs (never per run; a collision it raises is a blocker in
the summary), post the final summary immediately with the prune proposal
(§ Pruning finished waves), and stop the schedule. A
lane that stops early on a reserved exit (`86`/`87`/`88`) or is deliberately abandoned is
released in the same turn with that reason.

Immediately before every scheduled, urgent, and final message, obtain a timezone-bearing local
timestamp (for example `date '+%Y-%m-%dT%H:%M:%S%:z'`) and open exactly with
`Status written <timestamp> —`. Do not reuse a status-file timestamp as the message-written time.
Include:

- wave counts: `done`, `running`, `queued`, `failed`, plus the estimated wave completion;
- one line per lane: active run, display progress, heartbeat age, estimated active-run
  completion, queued count/next run, and estimated lane completion;
- failures, stale telemetry, unreachable rigs, interruptions, and recovery actions; for
  `leonardo` also each job's id, Slurm state and pending reason, and remaining walltime;
- the basis for each estimate (`structured progress`, `exact-run history`, or
  `experiment median`), or `ETA unavailable: <reason>`.

All ETAs are estimates. Use `experiments-tracking` for the calculation hierarchy. A schema-v2
heartbeat older than three minutes is stale: diagnose rig/session/process health and mark its ETA
unavailable until liveness is resolved. Non-schema-v2 status makes the project unsupported.

## Tracking side-effects (same turn)

- Append `todo` rows to EXPERIMENTS.md for every generated run — **one row per (run, wave)**, carrying its wave id, rig and gpu. A run re-launched in a later wave gets a **new row**, never an in-place update.
- Board: `claim` per confirmed lane session, `refresh` per tick, `release` per terminal lane (`rig-board`); the board is fleet state and never replaces any signal below.
- Flip launched rows to `inpr` and fill `started`; on completion reports flip to `done`/`failed` and fill `ended`/`elapsed`; a supersede flips the replaced rows to `superseded`; keep `progress`/`eta` fresh while runs are in flight (format + single-writer rules: `experiments-tracking` skill).
- Put the exact launch entry in the assignment record (`research-journal` skill). Apply its
  mode-aware authorization before the dispatch commit; never bypass the journal hook. The entry names
  the wave, rationale, environment fingerprint, rig/GPU assignment, and any one-off behemoth
  authorization.
