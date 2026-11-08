# {{PROJECT_NAME}}

## Structure

| path | purpose |
|---|---|
| `checkpoints/` | checkpoints produced by trainings/finetunings |
| `code/` | experiment code (hydra-configured) |
| `config/` | hydra yaml configs for `code/`, plus each experiment's run_id and glossary files |
| `evaluations/` | data produced by running experiments (+ `.status.json` markers, `metrics.jsonl` curves) |
| `logs/` | run logs — tee'd stdout+stderr per run (gitignored, rig-local) |
| `plots/` | plots produced by `visualizations/` |
| `scripts/` | shell scripts that launch experiments/sweeps |
| `tests/` | optional pytest unit tests, mirroring `code/` and `visualizations/` inside itself |
| `visualizations/` | plotting code (argparse), reads `evaluations/`, writes `plots/` |
| `shitpads/` | temp scratch space (gitignored, rig-local) |
| `references/` | papers/codebases for reference (gitignored, rig-synced) |

Experiments are named `NNN_experiment_name` and mirrored across the folders above; tests for
`<root>/<path>/<stem>.py` live at `tests/<root>/<path>/<stem>/test_*.py`.
Each run is identified by its **run_id** `rid-<32 hex>`, a hash of the experiment's identity
params: the experiment's `.py` pins `RUN_ID_PATH_LAYOUT = "hashed-v2"`, the params are the current
entry of `config/<experiment>/RUN_ID_VERSIONS.json`, `RUN_ID_HISTORY.json` beside it maps old
hashes to new ones after a run_id migration, and `evaluations/<experiment>/RUN_ID_MAP.json`
indexes every run. `run_id_label` gives the readable `k=v,…` shown beside the hash, and
`config/<experiment>/PARAM_GLOSSARY.json` gives each param's full name for plots.

## Tracking

- **`EXPERIMENTS.md`** — state: which runs exist (todo/inpr/done/failed), in which wave, on
  which rig and GPU.
- **`JOURNAL.md`** — story: what was done, why, and what was learned.

## Setup

1. `cp .env.example .env` and fill in the secrets.
2. Use `environment-sync` to provision the exact `uv.lock` + `.python-version` environment and
   run the project GPU smoke.
3. Configure the GitHub remote, dispatch branch, and `sync.toml`, then use `rig-sync` and
   `environment-sync` to prepare and verify the rigs.

## Running

Runs are launched in **waves** (one dispatch decision, id `YYYYMMDD-HHMMSS`) via
`scripts/NNN_experiment_name/<run_id folder>/wave_<wave_id>/` — a `README.md` saying what the
wave is, plus one placement-free `wave.sh` per run that any GPU lane of the wave's pool may claim.
Each wave is smoke-tested, committed, and tagged `wave--<wave_id>`; every rig verifies that exact
commit and the wave's environment fingerprint before execution. `logs/` mirrors `scripts/`.
