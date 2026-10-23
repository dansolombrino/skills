---
name: sweep-dispatch
description: Generate and launch experiment runs/sweeps across the GPU rigs (rig-4090, rig-3090-ti, rig-3080-ti, behemoth — also "4090", "3080 ti", "pro 6000", "bw", "blackwell", "server-pro-6000-bw") and the CINECA Leonardo Slurm cluster ("leonardo", "cineca", "slurm", "sbatch", "HPC") as one pull queue over a pool of GPU lanes, then hand the wave to sweep-supervisor. Use when the user asks to run, launch, sweep, dispatch, or submit experiments or Slurm jobs, split runs across machines, GPUs, or the cluster, use a rig that is short on disk, rerun failed runs, supersede in-flight runs with a newer wave, run several waves of one project at once, or fetch results from the cluster. Monitoring, status, rebalancing, and recovery belong to sweep-supervisor.
---

# sweep-dispatch

Turns a set of planned runs into a **wave**: gates, generated scripts, one tested and tagged
commit deployed as a worktree on every pool rig, then registration with `sweep-supervisor`, which
runs the wave from there. Dispatch happens on the hub, `rig-4090`. Vocabulary (wave, lane, pool,
slice, run, job) and the fleet: `../research-project-init/references/conventions.md`. Script
templates, the manifest, and the queue math: [references/templates.md](references/templates.md).
When a wave assigns runs to `leonardo`, read [references/cineca-slurm.md](references/cineca-slurm.md)
first and follow it where it differs; do not load it otherwise.

## Gates before generation

Each gate names what enforces it; a failure stops the wave and is reported as is.

1. **Hub reconciled** — `rigsync collect --check --wave <id>` passes for every earlier wave of the
   experiment (`rig-sync`), so the hub holds every rig's run records.
2. **run_id plan** — `check_run_id_plan(<planned cfgs>, params, None, <evaluations experiment dir>,
   path_roots=(<checkpoints dir>, <scripts dir>, <logs dir>), layout="hashed-v2",
   spec_root=source_path("config/<experiment_path>"))` over the whole wave refuses collisions,
   duplicates, path overflow, and a planned run whose path holds a different `.run_config.json`.
   A config difference means the identity must grow: the next version plus a run_id migration
   (`run-identity`). An experiment pinned to an older layout: see `run-identity`
   references/legacy-layouts.md.
3. **Project contract** — the current `code/common/` helpers (`run_id.py` with `hashed-v2` and
   `hydra_override_arg`; schema-v3 `status.py` with numeric `heartbeat` call
   sites in the entrypoints), `paths.py` honouring `RESEARCH_PROJECT_ROOT`, and `.waves/` and
   `.envs/` ignored. A project without them is unsupported: name what is missing rather than
   upgrading it here. `rig-sync deploy-revision` also refuses a hub that does not ignore `.waves/`.
4. **No overlap** — a run with a live (`todo`/`inpr`) row in another wave stays out of this one;
   `rig-sync activity --machines <pool>` shows live lanes, statuses, and Slurm jobs per wave.
5. **Rigs** — `rig-sync doctor` and `environment-sync doctor` pass for every pool rig; each rig's
   path and storage floor come from `rig-sync repo-path`/`storage-env`, never typed by hand.
6. **Supervisor** — `sweep-supervisor check` passes: its hub service is active with a declared
   agent profile. A wave nobody supervises is not launched.

## Pool and queue

- Read the fleet board first: `rig-board status --reconcile`. The pool is every lane of ours on the
  project's rigs (`sync.toml`), free or held; a held lane waits in the pool and starts when its
  holder releases it. Availability never comes from `nvidia-smi`.
- `behemoth` contributes gpu0. Another card needs the user's grant for this wave, written into
  the manifest as `gpu_grants.behemoth` with the user's words verbatim (`register` refuses
  otherwise).
- `leonardo`: pass the certificate precondition in `references/cineca-slurm.md`, check the account
  with `saldo -b`, set a job cap; one GPU, 8 cores, 128 GB per job unless the user says a run
  needs more; no core-hour cap unless the user sets one. Its queue wait keeps `leonardo` outside
  the balancing; estimate its budget as `runs × walltime_h × 8` core-hours.
- Order the queue longest-first by cost from the `experiments-tracking` hierarchy, and list every
  non-hub lane rig under `offload` (a rig short on disk is kept busy through offload, never left
  out).
- Mint the wave id on the hub when the pool is fixed: `date '+%Y%m%d-%H%M%S'`.

## Generate

```
scripts/NNN_exp/<run_id folder>/wave_<wave_id>/README.md   # purpose, pool, standing authorization; written once
scripts/NNN_exp/<run_id folder>/wave_<wave_id>/wave.sh     # this run's invocation
scripts/NNN_exp/_lanes/wave_<wave_id>/lane.sh              # the loop every lane runs
```

- `wave.sh` names no rig and no GPU: the claiming lane supplies `LANE_RIG`, `LANE_GPUS`, and its
  storage floor; on `leonardo` the same file carries an `#SBATCH` header.
- It is self-contained (overrides rendered with `hydra_override_arg`) and self-guarded: it skips
  itself when the final artifact or its offload receipt exists. Its guards stop the lane with exit
  `86` (source drift), `87` (environment drift), and `88` (storage).
- The run folder is `run_id_path(...)`, the same `rid-<32 hex>` directory as in `evaluations/`.
  Materialize each run's exact checkpoint, evaluation, status, and final-artifact paths into its
  script and into the supervisor manifest.
- The README records the wave's purpose, its pool and offload rigs, any `behemoth` grant verbatim,
  and its **standing authorization**: inside the pool the supervisor may start, move, requeue, and
  steal this wave's own runs, resubmit or cancel its own not-yet-started jobs, fetch data into
  declared caches, and free checksum-verified checkpoints on offload rigs. That record is what
  bounds the unattended supervisor agent.
- `scripts/` holds shell only; the manifest goes to the scratch area.

## Test, commit, deploy

1. Stage only this wave's code, config, scripts, EXPERIMENTS.md rows, and journal entry; every
   execution file is tracked and clean.
2. Run the experiment's recorded smoke command on the hub in the staged lock's environment
   (`environment-sync provision --staged` then `verify --staged --machines rig-4090`, which prints
   `ENVIRONMENT_DIR`). The smoke must leave the staged patch unchanged.
3. Commit `dispatch(<NNN_exp>): wave <wave_id>`, create the annotated tag `wave--<wave_id>`, and
   push branch and tag without force.
4. `rig-sync deploy-revision --wave <wave_id>` (dry run, then `--confirm`) and `verify-revision`
   for every pool rig plus the hub; then `environment-sync provision`/`verify --wave <wave_id>
   --revision <sha>` for the same set and every lane.

Later hub commits never affect a dispatched wave: it reads code from its own worktree.

## Launch

Write the manifest and run `sweep-supervisor register --wave <wave_id> --manifest <file>
--dry-run`, then `--confirm`. The service verifies and starts each lane, claims it on `rig-board`,
feeds the queue, and submits `leonardo` jobs. Status in the chat and on the web: see
`sweep-supervisor`. To watch a lane by hand: `ssh <rig>` then
`tmux attach -t <project>_<NNN_exp>_<wave_id>_<rig>_gpu<ids>` (directly on the hub);
`ssh leonardo squeue --me` for jobs.

Recovery after a fault belongs to `sweep-supervisor`: same wave id, paths, and tag; it may
re-place a run within the pool, never widen it. Waves launched before the pull queue (scripts
named `wave_<rig>_gpu<ids>.sh`) can be monitored and reconciled, not relaunched.

## Supersede

When the user wants a newer wave to replace in-flight placements of the same runs:

- Retire each overlapping run of the old wave with `sweep-supervisor supersede --wave <old> --run
  <id> --by <new> --dry-run`, then `--confirm`: it ends `excluded` and is never requeued or
  resubmitted into the old wave. A Slurm job is cancelled by id; a lane run is first taken back
  with `steal --reason "superseded by wave <new>"`. Never `scancel` by hand: the supervisor
  resubmits a cancelled job it was not told about.
- Flip the old rows to `superseded`, naming the new wave (`experiments-tracking`).
- A superseded run's partial checkpoint is kept for resume when the source revision is the same,
  otherwise renamed to `<checkpoint dir>.superseded-<old_wave_id>`.

## Tracking

- Append one `todo` row per (run, wave) with its wave id and blank `rig`/`gpu`; after `register`,
  the supervisor agent writes those rows (`experiments-tracking`).
- Append the launch entry with `research-journal` before the dispatch commit: the wave, why,
  the environment fingerprint, the pool and offload rigs, and any `behemoth` grant.
- `sweep-supervisor finish` prunes a closed wave; `experiments-tracking` reconciliation prunes
  what it missed (`rig-sync prune --dry-run`, then `--confirm`).
