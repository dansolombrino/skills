---
name: sweep-dispatch
description: Generate, authorize, and launch experiment runs/sweeps across the GPU rigs (rig-4090, rig-3090-ti, rig-3080-ti, behemoth — also "4090", "3080 ti", "pro 6000", "bw", "blackwell", "server-pro-6000-bw") and the CINECA Leonardo Slurm cluster ("leonardo", "cineca", "slurm", "sbatch", "HPC") as one pull queue over a pool of GPU lanes, then hand the wave to sweep-supervisor. Use when the user asks to run, launch, sweep, dispatch, or submit experiments or Slurm jobs, split runs across machines, GPUs, or the cluster, use a rig that is short on disk, rerun failed runs, supersede in-flight runs with a newer wave, run several waves of one project at once, or fetch results from the cluster. Monitoring, periodic status/ETA updates, rebalancing, and recovery after a rig crash or job interruption belong to sweep-supervisor.
---

# sweep-dispatch

Launch machinery for runs and sweeps: gates, generation, one tested revision, launch. Once lanes
are running the wave belongs to `sweep-supervisor`, a hub service plus an unattended agent that
feed, rebalance, recover, offload, and report it; this skill never polls a rig. Canon: `../research-project-init/references/conventions.md`. Templates: [references/templates.md](references/templates.md). CINECA Leonardo is one more target: when a wave assigns any run to `leonardo`, read [references/cineca-slurm.md](references/cineca-slurm.md) before gate 2 and follow it wherever it differs from this file; do not load it otherwise. Dispatch always happens **from rig-4090** (the hub). GitHub distributes one tested, tagged commit through `rig-sync`, which materializes it as the wave's own detached worktree `.waves/<wave_id>/` on every assigned machine; `environment-sync` establishes runtime parity in the wave's lock-keyed environment `.envs/<env_key>/`; rsync is only for artifacts. If either gate fails, stop. Because every wave executes from its own worktree and environment, waves of different revisions may run on the same machine at the same time; GPU exclusivity stays `rig-board`'s job.

Read `program/00-execution-agreement.md` first and require the Research 2.0 surfaces. Stop on a
legacy layout; do not offer migration or recover it as-is. Dispatch is **agent-decided**
(conventions, "Autonomy"): inside the envelope's repo, branch, rigs/GPUs, compute/budget, and
destinations, build the pool, commit, deploy, and launch without a prompt, logging each gate's
preview to `program/DECISIONS.md` instead of printing it. Shared `behemoth` cards beyond gpu0
(which the supervisor also refuses without `gpu_grants`), new destinations, destructive actions,
cancelling or superseding runs outside the wave's standing authorization (another wave's runs, a
supersede, a running Slurm job), and envelope expansion always ask.

Vocabulary (canon): a **wave** is one dispatch decision, identified by `YYYYMMDD-HHMMSS`; its **pool** is the set of lanes and cluster caps it may use; a **lane** is one GPU set on one rig; a **slice** is whatever part of the wave a rig ends up running. Lanes run in parallel, runs within a lane run sequentially, and a run belongs to the lane that claims it from the wave's queue — never to a lane chosen in advance. A **run** is one experiment execution; a **job** is one Slurm allocation on `leonardo`, which hosts exactly one run — the cluster has jobs, not lanes. Use both words strictly and never call a job a run or a lane.

## Before anything launches — mandatory gates

1. **run_id coverage check** — every param varied in the sweep grid, AND every behavior-affecting
config param added/changed since this experiment's last runs, must be in the identity (the current
`config/<experiment_path>/RUN_ID_VERSIONS.json` entry under `hashed-v2`; `RUN_ID_PARAMS` under a
legacy pin). The check is mechanical: first require `rigsync collect --check --wave <id>` to pass
for every earlier wave of the experiment (the hub must hold every rig's run records, or a
comparison against it proves nothing). It needs every rig of those waves reachable; when one is
not (for example an expired `leonardo` certificate), restore access (renew the certificate) and
rerun — there is no bypass; then `check_run_id_plan` (gate 1c) compares every planned run's resolved config with the
`.run_config.json` already at its path. Identical passes (resume/retry); any difference **halts**
before anything is generated and names the differing keys — otherwise new runs collide with old
artifacts: overwritten, or silently skipped as "done". Under `hashed-v2`, the route is the next
identity version plus a run_id migration through `run-identity` (agent-decided, logged; `experiment-design`
§ 2b). Under a legacy `segments-v2`, `segments-v1`, `nested`, `collapsed-v1`, or `hashed-v1` pin,
`RUN_ID_PARAMS` may change only while no checkpoint, evaluation, or plot output and no wave README
or script exists; after the first such surface, any identity-schema change (including any change to
`RUN_ID_SEGMENTS`) requires a new numbered sub-experiment; never backfill, rename, move, or rewrite
the established tree outside a run_id migration. The artifact guard is only sound under this gate:
`guard_run_config` catches collisions after Python starts, but an artifact-skipped run never enters
Python.
1b. **helper safety check** — verify `code/common/run_id.py` provides the canonical
`run_id_path(cfg, params, *, layout='nested', segments=None)`/`run_id_name`/`run_id_flat`
renderers with `hashed-v2` support (`load_run_id_spec`, `run_id_hash_v2`, `run_id_label`,
`resolve_run_id_hash`) and the legacy renderings the experiment's pin needs (`segments-v2`: lists
joined with `-`, unsafe values refused, never escaped), `check_run_id_plan` with its config
comparison, `preflight_run_id_path`, the `.run_id.json`/`RUN_ID_MAP.json` writer that hard-fails
on a hash mapping to two param sets, atomic identity writes, and a `guard_run_config` that never scans other runs or
rewrites the map (a per-run map rebuild is quadratic in the wave and cannot finish at scale), `hydra_override_arg`, and
the single config resolver
`resolved_config`/`wandb_config` (a wandb run whose config is anything less than the whole
resolved config is unfixable after the fact). Any older helper makes the project
unsupported; halt without upgrading it in place.
1c. **run-path layout check** — read the experiment's literal `RUN_ID_PATH_LAYOUT`; it must be
`hashed-v2` (with its tracked `config/<experiment_path>/RUN_ID_VERSIONS.json`) or a legacy `segments-v2`/`segments-v1` (with the
recorded `RUN_ID_SEGMENTS`), `nested`, `collapsed-v1`, or `hashed-v1` pin. Treat that record as
pinned for the project, pass it to the canonical `run_id_path` helper, and resolve the checkpoint
and evaluation run directories before generating the wave. They must use the same selected layout.
`plots/` is not run-scoped. Under `hashed-v2`, run `check_run_id_plan(<planned cfgs>, params, None,
<evaluations experiment dir>, path_roots=(<checkpoints experiment dir>, <scripts experiment dir>,
<logs experiment dir>), layout="hashed-v2", spec_root=source_path("config/<experiment_path>"))`
with `params` from `load_run_id_spec(spec_root)`; under a legacy
segments pin, `check_run_id_plan(<planned cfgs>, RUN_ID_PARAMS, RUN_ID_SEGMENTS, <evaluations
experiment dir>, path_roots=(...), layout=RUN_ID_PATH_LAYOUT)`. Run it over the whole wave on the
hub before generating anything; any overflow, duplicate, path or hash collision, or config
difference against an existing `.run_config.json` blocks dispatch and routes back to
`experiment-design`. Never infer the layout from directory depth, reconstruct it from a flat id,
or silently adopt a different layout. A missing, unsupported, or mixed layout blocks dispatch.
Once any checkpoint, evaluation, or plot output or any wave README/script exists, its recorded
`RUN_ID_PATH_LAYOUT` is immutable. Any disagreement or mixed tree blocks dispatch; under a legacy
pin it routes new work to a new numbered sub-experiment with a fresh `hashed-v2` identity. Never
move, rename, or rewrite the existing artifacts, README files, or scripts, except through an
approved run_id migration.
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
status and artifact paths. Leave such a run out of this wave and log it — the old placement keeps
it; superseding the old placement (§ Supersede) happens only when the user asks for it.
1e. **rig declaration check** — resolve each assigned rig's project path with
`rig-sync repo-path --machine <rig>` and its storage floor with
`rig-sync storage-env --machine <rig>`; the supervisor exports them to each lane, so they are
never written into a wave script. Never type a project path or a quota filesystem by
hand: both are declared once, and a second copy is what goes stale. A rig missing from `sync.toml`
or the machine registry is not assignable — stop rather than guessing its layout.
1f. **telemetry contract check** — require schema-v2 `code/common/status.py` from
`research-project-init` and structured progress call sites in every target training/eval entrypoint.
The helper must provide timezone-aware timestamps, live elapsed time, and its automatic
60-second heartbeat; entrypoints call
`heartbeat(completed=<done>, total=<total>, unit=<label>)`. Display-only telemetry makes the
project unsupported. Telemetry does not join `RUN_ID_PARAMS`.
1i. **supervisor check** — run `sweep-supervisor` `check` and require its service to be active on
the hub with a declared agent profile (model and effort are never defaulted). If it fails, stop
before launch and say exactly what is missing: a wave nobody supervises is not launched, and no
chat promises updates it cannot deliver.
2. **Resolve usable rigs and GPUs.** First read the fleet board: `rig-board` `status --reconcile`
shows every lane held by any project on the hub, foreign cards, and unreachable rigs. Held and
foreign lanes are not free capacity. The pool is **every lane the board shows free on the
envelope's rigs and GPUs**: take it and log it, never ask which rigs are free. Held lanes stay out
and are logged with their holder and ETA; when no envelope lane is free, say so in the chat and do
not widen the pool, which always asks. Never infer availability from `nvidia-smi`. On `behemoth`,
gpu0 is the only default. Any additional named card requires an explicit per-wave user grant, is
never persisted, and goes into the manifest as `gpu_grants.behemoth` with the user's words
verbatim — `register` refuses the lane without it. For
`leonardo`, first pass the certificate precondition in `references/cineca-slurm.md` (print the
login commands and wait when it fails), then require the project account declared in the envelope
and check its remaining budget with `saldo -b` (log it; never ask about core-hours);
every job is one GPU, 8 cores, 128 GB unless the user states that a run needs more.
3. **Build and authorize the pool.** There is no static assignment: the wave is one ordered
queue, and every lane in the pool takes the next run it is eligible for when it has room, so a
fast card takes more runs and a wrong estimate corrects itself as real runtimes arrive. The
objective stays to **minimize wave completion**, never to hand each lane an equal run count: a
lane's capacity is `weight × free GPUs` and the fleet spans a fourfold spread. Estimate each run's
cost with the `experiments-tracking` hierarchy and order the queue **longest-first** (math in
[references/templates.md](references/templates.md)). A rig short on disk is **never** left out
for that reason: list it under `offload`, and `rig-sync offload` keeps freeing it while its lane
waits instead of stopping; every non-hub lane rig in the pool is listed under `offload` by
default (Slurm targets such as `leonardo` excluded; `supervisor.py` rejects offload entries that
are not lane rigs).
Log — never print or wait on — the pool (rig, GPU set, per lane), the queue order, the simulated
wave completion and each lane's expected share, runs pinned to a rig by VRAM or data, offload
rigs, exact smoke command, staged files, branch/remote, environment key and fingerprint,
provisioning dry run, commit/tag/push, worktree deployment scope, and any supersede. The chat
gets one line at launch: wave id, run count, lanes per rig, simulated completion, update interval
in minutes (the user's number, else 1).
`leonardo` is outside the balancing: its queue wait is not predictable, so dispatch sets its job
cap (no core-hour cap unless the user asks for one), each run it takes becomes one job, and its
ETA is reported as scheduler estimate plus run estimate. Log for it the account,
partition/QOS, resource line, walltime basis, and the budget estimate (`runs × walltime_h × 8`
core-hours per job) beside the account's remaining hours. Log the
**standing authorization** the wave carries for its life: inside the pool,
`sweep-supervisor` may start, move, requeue, and steal this wave's own runs, resubmit its own
jobs and cancel its own jobs the cluster has not started yet, fetch data into declared caches, and free checksum-verified checkpoints on offload
rigs, without asking; everything outside the pool comes back to the user. Check every item is
within the envelope and carry on. Anything outside it — a rig, card, or budget beyond the
envelope — is an always-ask question. Record any shared-GPU grant verbatim in the manifest and the
journal; a granted `behemoth` card is part of the pool for this wave only.
4. **Mint the wave id** when the pool is fixed: `date '+%Y%m%d-%H%M%S'` on rig-4090.
One id for the whole dispatch, shared by every rig and lane. Never use a semantic slug.

## Generate

For each run in the wave, materialize one placement-free script; once per wave, one lane loop:

```
scripts/NNN_exp/<run_id folder>/wave_<wave_id>/
    README.md      # what this wave is + the pool it may run in; written once, never updated
    wave.sh        # this run's invocation in this wave
scripts/NNN_exp/_lanes/wave_<wave_id>/
    lane.sh        # the loop every lane of this wave runs
```

- `wave.sh` names **no rig and no GPU**. The lane that claims it supplies `LANE_RIG`, `LANE_GPUS`,
  and that rig's storage floor; on `leonardo` the same file carries an `#SBATCH` header
  (`references/cineca-slurm.md`) and the supervisor passes `--time` and `LANE_RIG` to `sbatch`.
- The wave script is **self-contained** (full python command with explicit hydra overrides, each rendered through `hydra_override_arg`) and **self-guarded**: it exits early only if its expected final artifact, or the offload receipt `rig-sync` leaves in its place, is present. A `.status.json` that says `done` while the artifact is absent is inconsistent, so the script warns and re-executes. That guard is what makes a lane relaunch idempotent. Whether a re-execution resumes or restarts was fixed at experiment design time, never re-asked here.
- It exports `WAVE_ID` and the approved `ENVIRONMENT_FINGERPRINT`.
  Before experiment Python it recomputes the fingerprint with the configured environment's
  `bin/python`, compares it, and runs the bounded project GPU smoke without syncing.
  Drift/compatibility failure exits `87`.
  With `behemoth` in the pool it retains the per-wave authorization guard before the smoke. It
  checks storage headroom first (the lane's floor plus the run's own checkpoint footprint), exiting
  `88` rather than dying mid-checkpoint and leaving a truncated artifact that looks real. It
  captures its own timestamped log under the mirror path in `logs/`, named after the lane that
  ran it.
- The run folder comes from `code/common/run_id.py`. Under `hashed-v2` it is the same
  `rid-<32 hex>` directory as `evaluations/`, and the EXPERIMENTS.md `run_id` column, Slurm
  job-name suffix, and WandB hash tag are `run_id_name(...)` — the hash — with `run_id_label(...)`
  as the readable label and WandB display name (`wandb.init(name=run_id_label(...),
  tags=[run_id_name(...)], config=wandb_config(cfg))`), so every surface carries one identity. Under a legacy `segments-v2` or
  `segments-v1` pin the same holds with the `run_id_path(..., segments=RUN_ID_SEGMENTS)`
  directories; under the other legacy pins the selected `RUN_ID_PATH_LAYOUT` never changes scripts, logs, or wandb naming:
  those remain flat `run_id_flat`.
  `ls scripts/NNN_exp/<run_id folder>/` is that run's execution history.
- **Placement is recorded where it happens**: the supervisor's ledger records which lane claimed
  each run and when, the run's log file is named after that lane, and EXPERIMENTS.md gets `rig`
  and `gpu` at claim time. No file name pre-assigns a run.
- Only `scripts/` and `logs/` are wave-scoped. `checkpoints/` and `evaluations/` stay
  run-scoped at paths produced by the canonical helper for the experiment's recorded layout;
  `plots/` is not run-scoped and never uses the helper.
  Materialize the exact checkpoint directory, evaluation directory, status path, and expected
  final artifact path into each generated wave script **and into the supervisor manifest**; never
  leave a monitor or recovery step to infer how many path segments the run id occupies. Artifacts
  must keep a stable per-run path or the self-guard and `guard_run_config` both break.
- `scripts/` contains shell only. **Never put yaml under scripts/** — the grid lives in the generation conversation, the wave README, and EXPERIMENTS.md rows. The supervisor manifest (template in `references/templates.md`) is launch input and is written to the scratch area, never under `scripts/`.

## Test, commit, and deploy one revision

After generation and before any tmux launch:

1. Stage only this wave's code/config/scripts/tracking/JOURNAL files. Require every execution
   file to be tracked and no unstaged or non-ignored untracked file under `code/`, `config/`,
   `scripts/`, or a root dependency manifest. Record a hash of the staged patch.
2. Run the experiment's design-time smoke command on rig-4090 with the environment for the
   **staged** lock: `environment-sync provision --staged --machines rig-4090` (a new key is built
   only if absent; agent-decided, like any provisioning), then `verify --staged --machines rig-4090`
   prints `ENVIRONMENT_DIR`; substitute it for the smoke command's `<env>` placeholder. Missing smoke
   metadata makes the project unsupported. After the smoke, require the same staged-patch hash and
   a clean execution tree; a test that rewrites source invalidates itself. The committed wave has
   the same environment key, so the hub's smoke environment is also its wave environment.
3. Commit as `dispatch(<NNN_exp>): wave <wave_id>`. Refuse an existing `wave--<wave_id>` ref, then
   create that annotated tag at `HEAD`. Push the configured branch and exact tag without force;
   verify GitHub resolves both to the full local commit SHA. A push failure blocks dispatch.
4. Run `rig-sync deploy-revision --wave <wave_id>` first as a dry run, then confirm it (no
   prompt) for **every rig in the pool plus the hub together**
(any lane may claim any run, so every pool rig needs the worktree and the environment). It
   adds the detached worktree `.waves/<wave_id>` at the tagged commit on each machine, only
   verifies one that already exists, and never moves any main checkout — so other waves keep
   running untouched. Run `verify-revision` across the same set. Then run
   `environment-sync provision --wave <wave_id> --revision <sha>` when its dry run showed changes
   and `verify --wave <wave_id> --revision <sha>` for the full set plus every pool lane; it
   prints the `ENVIRONMENT_DIR` and fingerprint the wave scripts carry. Do not launch unless
   source, fingerprint, and GPU smoke all pass.

Later commits on the hub (EXPERIMENTS.md, JOURNAL.md, the next wave) never affect a dispatched
wave: its code is read from its own worktree and its environment is never re-synced while it runs.

## Launch — hand the wave to the supervisor

The chat never launches a lane, polls a rig, or writes the fleet board. After the all-rig Git and
environment gates pass:

1. Write the supervisor manifest and run `sweep-supervisor` `register --wave <wave_id> --manifest
   <file> --dry-run`, then `--confirm`, and log what was registered.
2. The supervisor service does the rest on its next cycle: it repeats `rig-sync verify-revision`
   and read-only `environment-sync verify` for a lane immediately before starting it (when the
   lane is on the current local hub it runs the same commands without self-SSH), starts each lane
   as its own named tmux session `<project>_<NNN_exp>_<wave_id>_<rig>_gpu<ids>` running the wave's
   `lane.sh`, claims it on `rig-board`, feeds its buffer, and submits `leonardo` jobs under the
   caps with the guarded `sbatch` in `references/cineca-slurm.md`. A claim that exits 3 means
   another project holds the lane: nothing is killed, the lane stays out, and it is reported.
3. **Start monitoring in the same turn, without asking**: post the first table (`sweep-supervisor`
   `table --wave <wave_id> --cycle --full`), which shows every pool lane started or names why not,
   then arm the periodic printout at the chat update interval (`sweep-supervisor` § In a chat).
   `register` prints a `NEXT:` line and the plugin's post-register hook says the same, so this
   never waits for a prompt. A chat that stalls loses only the printout.

Log how to watch manually (the user reads it from `program/DECISIONS.md` when wanted): for a peer, `ssh <rig>` then
`tmux attach -t <project>_<NNN_exp>_<wave_id>_<rig>_gpu<ids>`; for the current local hub, run the
`tmux attach` command directly; for `leonardo`, `ssh leonardo squeue --me`.

## Machine faults and recovery

Owned by `sweep-supervisor`: its service detects an unreachable rig, a reboot, or a missing lane
session and relaunches the lane **with the same wave id** (a relaunch is not a new dispatch: same
paths, session names, tag, and commit) after the same revision and environment gates; completed
runs skip, interrupted ones re-execute, and anything it cannot verify is left blocked for its
agent. What stays true here:

- **Recovery may re-place a run, never widen the pool.** A relaunched or requeued run may be
  claimed by any pool lane. A card outside the pool — above all a `behemoth` card without a grant
  for this wave — is a pool change and needs the user.
- **Unsupported layouts** — any noncanonical or mixed checkpoint/evaluation layout, or any wave
  whose recorded status/artifact paths disagree with the experiment's pinned `RUN_ID_PATH_LAYOUT`,
  blocks recovery. Never infer slash depth or substitute different paths. Route changed work to a
  new numbered sub-experiment (legacy pin) or a run_id migration (`hashed-v2`); otherwise ordinary
  recovery continues only from the unchanged wave README/script, pin, tag, and revision. After a
  run_id migration the comparison is against the post-migration commit: the migration rewrote the
  README and script to the new hash, and that commit, not the wave tag's text, is "unchanged".
- **Statically assigned waves** (launched before the pull queue: `wave_<rig>_gpu<ids>` scripts, no
  `.waves/_state/<wave_id>`) may be monitored and reconciled, never recovered or relaunched.
- **Slurm jobs** — state mapping and the guarded resubmit live in `references/cineca-slurm.md`.

## Supersede — replacing in-flight placements of the same runs

Only when the user asks for it, with an explicit user authorization (cancelling work is
destructive). Show the exact list in that one question.

- **Slurm:** `scancel` exactly the overlapping jobs, by id (from `leonardo.jobs` and the
  `<old_wave_id>__<run_id_name>` job-name match); never by user, partition, or pattern.
- **Supervised lane:** take exactly the overlapping runs out of the old wave with
  `sweep-supervisor` `steal --run <id> --reason "superseded by wave <new_wave_id>"` (it proves the
  process is that wave's own before stopping it) followed by `queue drop`; the old lane keeps
  working through the rest of its queue. A statically assigned wave cannot drop one script, so its
  lane is stopped whole (`tmux kill-session -t <lane session>`), and only when every non-terminal
  run in that lane is in the new wave; then release it on `rig-board` with reason
  `superseded by wave <new_wave_id>`.
- **Rows:** flip the old wave's rows for those runs to `superseded` with a note naming the new
  wave (`experiments-tracking`). A superseded row is terminal and never recovered.
- **Partial state:** a superseded run may have left a checkpoint that the new wave would resume
  from, possibly across a code change. Decide it and log it: keep (resume) when the new wave's
  source revision equals the old wave's, otherwise quarantine.
  Quarantine is a rename to `<checkpoint dir>.superseded-<old_wave_id>`, never a deletion; the
  status file is simply overwritten by the new wave.
- Then generate and dispatch the new wave as usual; gate 1h now passes.

## Pruning finished waves

A terminal wave's worktree, and any keyed environment no remaining worktree uses, is disk that
nothing needs — but a failed run may still be recovered, and recovery recreates what was pruned.
So pruning is **done by this skill and logged**, never skipped: when the final summary of a wave
is posted, run
`rig-sync prune --machines <wave's machines plus the hub> --waves <wave_id> --dry-run`, then the
same command with `--confirm`, and log what was removed. `prune` refuses active or
dirty worktrees by itself; still list only waves whose rows are all terminal.

## Status and ETA updates

The periodic table, the urgent reports, the final summary, board refresh and release, and the
once-per-wave `write_run_id_map(<evaluations experiment dir>, layout=RUN_ID_PATH_LAYOUT)` rebuild
(`spec_root=` the tracked `config/<experiment_path>` under `hashed-v2`)
(never per run) belong to `sweep-supervisor`. When its final table is posted, run the prune
(§ Pruning finished waves).

## Tracking side-effects (same turn)

- Append `todo` rows to EXPERIMENTS.md for every generated run — **one row per (run, wave)**, carrying its wave id, with `rig` and `gpu` blank until a lane claims it. A run re-launched in a later wave gets a **new row**, never an in-place update.
- After `register`, the supervisor's agent is the single writer of those rows for the life of the wave (format and rules: `experiments-tracking`); the board is written by the supervisor service.
- Append the exact launch entry with `research-journal` (written, not echoed) before the
  dispatch commit; never bypass the journal hook. The entry names
  the wave, rationale, environment fingerprint, the pool and offload rigs, the standing
  authorization, and any one-off behemoth authorization.
