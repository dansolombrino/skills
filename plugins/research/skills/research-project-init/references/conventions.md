# Research-project conventions (shared canon)

The single source of truth for the conventions all `research` bundle skills enforce.
**Overarching invariant: Claude proposes, the user has final say** — on run_id election, rig
assignment, checkpoint choices, wandb layout, journal entries. Never decide these alone.

## Taxonomy

```
[project root]
├── checkpoints/     # checkpoints from trainings/finetunings
├── code/            # experiment code (hydra-configured python)
│   └── common/run_id.py   # shared run_id helper (see below)
├── config/          # hydra yaml configs for code/
├── evaluations/     # data produced by running experiments (+ .status.json markers)
├── logs/            # run logs (tee'd stdout+stderr of each run, wandb dirs); gitignored, dir tracked, rig-local, never synced
├── plots/           # plots produced by visualizations/ (one subfolder per plotting script)
├── scripts/         # shell scripts that LAUNCH things (shell only — never yaml)
├── visualizations/  # plotting code (argparse python), sibling of code/
├── shitpads/        # temp space; gitignored, dir tracked, rig-local, never synced
├── references/      # papers/codebases to reference; gitignored, dir tracked, rig-synced
├── .env             # secrets + machine-varying paths ONLY (gitignored)
├── .env.example     # committed mirror of .env keys with placeholders
├── README.md        # static scaffold (structure, setup, how-to)
├── CLAUDE.md        # thin: project specifics + light awareness map
├── JOURNAL.md       # story: prose log of what/why/learned (append-only)
└── EXPERIMENTS.md   # state: run tracking tables
```

Run-log-producing tools must write into `logs/` — e.g. `wandb.init(dir=<project_root>/"logs")` —
never the project root.

Every `scripts/` `run.sh` captures its own stdout/stderr: the python invocation is piped
through `tee` into `logs/NNN_experiment/<run_id path>/run-<YYYYmmdd-HHMMSS>.log` (nested
run_id path form, exactly mirroring `checkpoints/` and `evaluations/`). Timestamped per
launch — history is kept, logs are never overwritten. Hydra projects must disable hydra's
job file logging (defaults entry `- override hydra/job_logging: none`) or explicitly point
it inside `logs/` — a stray `<job_name>.log` must never appear in the project root.

Experiments are named `NNN_[experiment_name]` (three-digit zero-padded), optionally nested
(`NNN_exp/NNN_sub_exp/...`). The same `NNN_...` hierarchy is mirrored across
`checkpoints/ code/ config/ evaluations/ logs/ plots/ scripts/ visualizations/`.

## run_id

The per-experiment **ordered** set of config params that uniquely identifies a run.

- **Elected WITH the user. Claude NEVER decides it alone — the user always has the final say.**
- Authoritative record: a constant in the experiment's `.py` (e.g. `RUN_ID_PARAMS`) that the
  path-building code actually uses; mirrored in the experiment's EXPERIMENTS.md section header.
- Config yamls stay pure hydra params — **no run_id metadata in yaml**.
- Two renderings via `code/common/run_id.py`:
  - **path form** — nested subfolders, e.g. `model=mlp/lr=1e-3/seed=0/` → used under
    `checkpoints/`, `evaluations/`, `plots/`. `param=value` segments are the default; any
    shortening (e.g. bare `mlp/`) is a per-experiment deviation decided with the user.
  - **flat form** — e.g. `model=mlp,lr=1e-3,seed=0` → used for `scripts/` run-folder names,
    wandb run names, and log lines. Flat name ≡ EXPERIMENTS.md row ≡ wandb run, 1:1.

## Rig fleet

| canonical name       | sloppy names to recognize      | speed weight (vs 4090) | role |
|----------------------|--------------------------------|------------------------|------|
| `rig-4090`           | 4090                           | 1.0                    | main/hub: projects start here, dispatch + EXPERIMENTS.md writes happen here |
| `rig-3090ti`         | 3090, 3090 ti                  | 0.5                    | support |
| `rig-3080ti`         | 3080, 3080 ti                  | 0.5                    | support |
| `server-pro-6000-bw` | pro 6000, 6000, bw, blackwell  | 2.0                    | support |

Passwordless ssh between all rigs. Cross-machine file movement (references/, checkpoints, code)
is **rig-sync's job** — never invent ad-hoc sync.

## Status ground truth — three signals, artifacts golden

Every run leaves three on-disk signals. When they disagree, **artifacts win** (golden signal),
then `.status.json`, then the log.

1. **Artifacts (golden)** — the run's expected final artifact (final checkpoint under
   `checkpoints/NNN_exp/<run_id path>/` and/or final eval output under
   `evaluations/NNN_exp/<run_id path>/`), declared at experiment design time. Present ⇒ the
   run really finished; absent ⇒ not done, whatever the status file says.
2. **`.status.json`** — `evaluations/NNN_exp/<run_id path>/.status.json`, written by the
   python script itself (via the StatusWriter pattern — see `sweep-dispatch` templates):

   ```json
   {"state": "running|done|failed", "started": "<ISO>", "ended": "<ISO>|null",
    "elapsed_s": 1234.5, "heartbeat": "<ISO>", "progress": "epoch 3/10"}
   ```

   Single writer: the python script owns it; `run.sh` only overwrites it with `failed` if the
   process died before python could. One file per run → conflict-free by construction;
   deleting a run's outputs resets its status.
3. **Run log** — the latest `logs/NNN_exp/<run_id path>/run-<YYYYmmdd-HHMMSS>.log`: the run's
   **entire stdout+stderr** (tee'd by `run.sh`, terminal-redirection style; timestamped per
   launch, history kept). Tracebacks/errors near the tail ⇒ failed; a stale heartbeat plus a
   silent log ⇒ suspect a hang — after ruling out a machine fault (below).

A `.status.json` frozen at `running` has three readings: **healthy** (heartbeat advancing),
**process hang** (rig up, tmux session alive, heartbeat frozen — report, don't auto-kill), or
**machine fault** (rig unreachable/rebooted — boot time via `uptime -s` postdates the
heartbeat — or the sweep's tmux session is gone). A machine-fault run is **interrupted**, not
failed-by-code, and is safe to relaunch: `launch_<rig>.sh` is **idempotent** (runs whose final
artifact is present, or whose `.status.json` says `done`, are skipped), so re-running a slice
never redoes finished work — interrupted runs re-execute, resuming or restarting per the
experiment's design-time resume decision. Detection + recovery protocol: `sweep-dispatch`.

- **EXPERIMENTS.md is single-writer: only ever edited on rig-4090** — and within a launch
  session, only by the orchestrator chat, never by monitoring subagents.

## Root-file duties

- EXPERIMENTS.md = **state** (tables, reconciled from the three signals above; includes each
  run's `started`/`ended`/`elapsed` — reference runtimes for estimating future temporal
  budgets). JOURNAL.md = **story** (prose: what, why, what was learned; append-only, never
  rewritten).
- New env var read by code → `.env.example` updated in the SAME turn.
