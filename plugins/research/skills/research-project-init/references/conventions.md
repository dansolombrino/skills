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
├── evaluations/     # data produced by running experiments (+ .status markers)
├── logs/            # run logs (wandb dirs, stdout dumps); gitignored, dir tracked, rig-local, never synced
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
`checkpoints/ code/ config/ evaluations/ plots/ scripts/ visualizations/`.

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

## Status ground truth

- Each run writes `evaluations/NNN_exp/<run_id path>/.status` (`running` / `done` / `failed`).
  One file per run → conflict-free by construction; deleting a run's outputs resets its status.
- **EXPERIMENTS.md is single-writer: only ever edited on rig-4090.**

## Root-file duties

- EXPERIMENTS.md = **state** (tables, reconciled from markers). JOURNAL.md = **story**
  (prose: what, why, what was learned; append-only, never rewritten).
- New env var read by code → `.env.example` updated in the SAME turn.
