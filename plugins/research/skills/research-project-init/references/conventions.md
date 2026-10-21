# Research-project conventions

Shared facts for every `research` skill. Projects without these Research 2.0 surfaces are
unsupported; moving one over is a separate change the user asks for.

## Taxonomy

```
[project root]
├── checkpoints/     # training checkpoints (gitignored)
├── code/            # experiment code, hydra-configured python
│   └── common/      # environment.py, environment_smoke.py, paths.py, run_id.py, status.py
├── config/          # hydra yaml; plus each experiment's run_id and glossary JSON
├── evaluations/     # experiment outputs and .status.json markers (gitignored)
├── logs/            # run logs, mirroring scripts/ (gitignored, rig-local)
├── plots/           # one self-contained HTML file per figure (gitignored)
├── scripts/         # shell only: wave scripts and lane loops
├── tests/           # optional pytest tests
├── visualizations/  # plotting code, argparse python
├── shitpads/        # scratch (gitignored, rig-local)
├── references/      # user-supplied papers/codebases (gitignored, rig-synced)
├── .env             # secrets and repo-relative project storage only (gitignored)
├── .env.example     # committed key mirror
├── .python-version, pyproject.toml, uv.lock   # exact Python, uv, and dependencies
├── sync.toml        # Git remote, artifact groups, per-rig repo_path, environment contract
├── README.md        # structure and setup
├── AGENTS.md        # project notes (canonical)
├── CLAUDE.md        # points at AGENTS.md
├── JOURNAL.md       # story
└── EXPERIMENTS.md   # state
```

Experiments are `NNN_name` (zero-padded), sub-experiments nest as `NNN_exp/NNN_sub`. The complete
numbered leaf hierarchy is `<experiment_path>`, mirrored across `checkpoints/ code/ config/
evaluations/ logs/ plots/ scripts/ visualizations/`.

- **Plots.** A single-producer plotter lives at `visualizations/<experiment_path>/<script_stem>.py`
  and writes only below `plots/<experiment_path>/<script_stem>/`, one HTML file per figure
  (`plots/000_grokking/plot_loss/loss_curve.html`). Run selection lives inside the file, so plot
  paths carry no run_id. A plot combining several producer experiments is placed by the user.
- **Tests** (optional) for `<root>/<path>/<stem>.py`, `<root>` being `code` or `visualizations`,
  live at `tests/<root>/<path>/<stem>/test_*.py`. They are not the pre-dispatch smoke.
- **Logs.** A wave script tees its stdout and stderr into the mirror of its own folder:
  `scripts/NNN_exp/<run folder>/wave_<wave_id>/wave.sh` logs to
  `logs/NNN_exp/<run folder>/wave_<wave_id>/wave_<rig>_gpu<ids>-<YYYYmmdd-HHMMSS>.log`. WandB
  writes under `logs/`; Hydra job file logging is off (`- override hydra/job_logging: none`).

## run_id

New experiments use the `hashed-v2` layout. An experiment pinned to an older layout: see
`run-identity` references/legacy-layouts.md.

- **Record.** The experiment's `.py` pins `RUN_ID_PATH_LAYOUT = "hashed-v2"`. The identity params
  are the last entry of the tracked `config/<experiment_path>/RUN_ID_VERSIONS.json`
  (`[{version, params, date, reason}]`), read with
  `load_run_id_spec(source_path("config/<experiment_path>"))`, so each wave worktree carries the
  version it was deployed with. `RUN_ID_HISTORY.json` beside it records every migration
  (`{from_version, to_version, date, map, backfill}`). `PARAM_GLOSSARY.json` there
  (`{param: {name, explanation}}`) gives plots each param's full name; it is not identity. Hydra
  yamls carry no run_id metadata.
- **Election.** Every param the grid varies plus every behavior-affecting param whose value
  differs from the config default; top-level config keys only (`optim`, not `optim.lr`).
- **Hash.** `rid-` plus the first 32 hex digits of sha256 over the canonical JSON
  `{"layout": "hashed-v2", "params": {param: value or null}}` with sorted keys.
- **Renderings** (`code/common/run_id.py`). `run_id_path` gives the run directory `rid-<32 hex>`
  under `checkpoints|evaluations|scripts|logs/<experiment_path>/`. `run_id_name` is the hash: the
  EXPERIMENTS.md `run_id` column, the WandB tag, the Slurm job-name suffix. `run_id_label` is the
  readable `k=v,…`: the WandB display name and the `label` column; it is never a path. WandB is
  `wandb.init(name=run_id_label(...), tags=[run_id_name(...)], config=wandb_config(cfg))`, where
  `wandb_config` is the whole resolved config plus a `provenance` namespace.
- **Guards.** `guard_run_config` runs before any artifact is written: it checks the params against
  the current version, atomically writes `.run_config.json` and `.run_id.json`, and refuses a
  different config at an existing hash; its I/O is O(1) per run. `check_run_id_plan` runs before
  dispatch: it rescans every record and refuses collisions, duplicates, path overflow, and config
  drift at an existing path. `write_run_id_map` rebuilds the derived
  `evaluations/<experiment_path>/RUN_ID_MAP.json` once per wave. Resolve a hash through
  `run-identity`, never by hand.

### run_id schema evolution

Adding, removing, or renaming a behavior-affecting config param changes what identifies a run.
Before any checkpoint, evaluation, plot, or wave record of the experiment exists, rewrite the
current version entry. After that, append the next version and run a run_id migration through
`run-identity`: it is the one operation that rewrites existing hashes (run directories, wave
READMEs and scripts, EXPERIMENTS.md, JOURNAL.md, WandB runs). `RUN_ID_PATH_LAYOUT` itself never
changes once outputs exist.

## Waves, lanes, and the pool

- **Wave** — one dispatch decision. Its id is `YYYYMMDD-HHMMSS`, minted once on the hub when the
  pool is fixed, shared by every rig, reused verbatim on crash recovery, and tagged
  `wave--<wave_id>` in Git. Meaning goes in the wave README, never in the id.
- **Lane** — one GPU set on one rig, written `gpu<ids>` (`gpu0`, `gpu0,1`) even on single-GPU
  rigs. Lanes run in parallel, runs within a lane sequentially; GPU sets within one (wave, rig)
  are disjoint.
- **Pool** — the lanes (and cluster job caps) a wave may use. A wave is one ordered queue: a run
  belongs to whichever pool lane claims it, so placement is recorded where it happens (the log
  name, the hub ledger `.waves/_state/<wave_id>/ledger.jsonl`, the EXPERIMENTS.md `rig`/`gpu`).
- **Slice** — the part of a wave one rig ends up running. **Run** — one experiment execution.
  **Job** — one Slurm allocation on `leonardo`, hosting exactly one run.

```
scripts/NNN_exp/<run folder>/wave_<wave_id>/README.md   # purpose, pool, standing authorization; written once
scripts/NNN_exp/<run folder>/wave_<wave_id>/wave.sh     # this run's invocation; names no rig or GPU
scripts/NNN_exp/_lanes/wave_<wave_id>/lane.sh           # the loop every lane runs
```

tmux session: `<project>_<NNN_exp>_<wave_id>_<rig>_gpu<ids>`. The wave README's pool and standing
authorization are what bound the unattended supervisor agent for that wave.

### Wave isolation

Each machine's `repo_path` holds one main checkout plus the shared storage. A wave runs from its
own detached worktree `.waves/<wave_id>/` at its tag, with a lock-keyed environment
`.envs/<env_key>/`, from the project root with `RESEARCH_PROJECT_ROOT` exported, so `paths.py`
resolves storage against the shared root and tracked inputs (`source_path()`) against the
worktree. Waves of different revisions run side by side. Inside every queued script, before Python, the
wave verifies its source (exit `86`), its environment fingerprint and GPU smoke (exit `87`), and
storage headroom (exit `88`); each stops the lane and is not an experiment failure.

## Rig fleet

The canonical name is the ssh alias.

| name | also called | GPUs | per-GPU weight | storage | role |
|---|---|---|---|---|---|
| `rig-4090` | 4090 | 1 | 1.0 | dedicated | hub: dispatch, EXPERIMENTS.md, plotting |
| `rig-3090-ti` | 3090, 3090 ti, `rig-3090ti` | 1 | 0.5 | dedicated | support |
| `rig-3080-ti` | 3080, 3080 ti, `rig-3080ti` | 1 | 0.5 | dedicated | support |
| `behemoth` | pro 6000, 6000, bw, blackwell, `rig-6000-pro-blackwell`, `server-pro-6000-bw` | 8, only GPU 0 is ours | 2.0 | quota'd, small home | support |
| `leonardo` | cineca, leonardo booster, slurm, hpc | 1 per job (A100 64 GB) | 1.0 per A100 | per-project quota, scratch auto-cleaned | Slurm target: `sweep-dispatch` → `references/cineca-slurm.md` |

- **Weights** are nominal per-GPU priors (capacity = weight × free GPUs), used to balance waves
  and to compare runtimes across rigs; measured same-rig history beats them.
- **behemoth** is shared. GPU 0 is the only card that is ours; another card needs the user's grant
  for that one wave, quoted verbatim in the manifest's `gpu_grants.behemoth` and the script's
  `# GPU auth:` header (`supervisor.py register` and `board.py claim` refuse without it). Its
  disk is quota'd per user: check headroom with `quota`, not `df`.
- **leonardo** is a batch cluster: one run per job, one GPU, 8 cores, 128 GB unless the user says
  a run needs more; it is outside lane balancing. Access is a short-lived SSH certificate only the
  user can renew (`phone-bridge`). The project
  account is the one in its declared `repo_path`; there is no core-hour cap unless the user sets
  one.
- **Machine registry.** Each rig's ssh alias, hostname, storage roots, quota filesystem, and shared
  caches live in the user registry `~/.config/rigsync/machines.toml` (`rig-sync` →
  `references/configuration.md`), declared, never guessed, and never defaulted to `$HOME` or
  `~/.cache`. It is a prerequisite for scaffolding: a rig without an entry cannot go in `sync.toml`.
  Shared caches are installed by `rig-sync provision-env` where non-interactive shells see them;
  a project `.env` never redeclares them, and its own storage paths are repo-relative.
- **Low disk** is a reason to offload, not to idle a rig: `rig-sync offload` moves a wave's
  finished checkpoints to the hub with checksum-verified receipts. Evaluations converge on the hub
  through `rig-sync collect`; `collect --check` passing is what "reconciled" means for the run_id
  gate and migrations.

## Status ground truth

Every run leaves three signals. First the run's `.status.json` source tag and revision must match
its wave tag; after that, **artifacts win**, then `.status.json`, then the log.

1. **Final artifact** — declared at design time, under the run's checkpoint or evaluation
   directory (or its `<artifact>.offloaded.json` receipt). Present means done.
2. **`.status.json`** in the run's evaluation directory, written by `code/common/status.py`
   ([templates](templates.md#codecommonstatuspy)):

   ```json
   {"schema_version": 3, "state": "running|done|failed", "started": "<ISO>",
    "ended": "<ISO>|null", "elapsed_s": 1234.5, "heartbeat": "<ISO>",
    "progress": "epoch 3/10", "progress_completed": 3, "progress_total": 10,
    "progress_unit": "epoch",
    "wave_id": "20260731-162043", "rig": "rig-4090", "gpu": "0",
    "source_revision": "<40-char-sha>", "source_tag": "wave--20260731-162043",
    "environment_fingerprint": "<64-char-sha256>"}
   ```

   Placement and provenance arrive as environment variables, never config params, so a relaunch
   elsewhere never trips `guard_run_config`. The heartbeat refreshes every 60 s; one older than
   three minutes is stale. ETA is derived, never stored.
3. **Run log** — the latest log in the run's wave log folder.

A run stuck at `running` is hung (rig and session up, heartbeat frozen) or interrupted by a
machine fault (rig rebooted after the heartbeat, or its tmux session gone); interrupted runs are
safe to relaunch because wave scripts skip runs whose artifact exists. EXPERIMENTS.md is edited only on the hub, and during a supervised wave only by its
supervisor agent.

## Running waves

`sweep-supervisor` owns a wave once it is registered: a hub service feeds lanes, recovers faults,
offloads, collects, and wakes an unattended agent for judgment calls inside the wave's pool and
standing authorization. Status in the chat, the web view, and warnings: see `sweep-supervisor`.

## Secrets

`.env` holds secrets and is never committed, echoed, logged, or pasted; refer to keys by name.
`rig-sync push-env` copies the hub's `.env` to a declared peer over ssh, lands it 0600, prints key
names only, and keeps a different peer file unless `--overwrite` is passed. Credentials the project
does not have yet (a token, a login) come from the user. Never copy a secret from another project.

## Root-file duties

- EXPERIMENTS.md is **state**: run tables reconciled from the three signals, including each run's
  timing, which is the project's reference runtime.
- JOURNAL.md is **story**: what was done, why, and what was learned; append-only, except that a
  run_id migration replaces old hashes.
- A new environment variable read by code goes into `.env.example` in the same change.
