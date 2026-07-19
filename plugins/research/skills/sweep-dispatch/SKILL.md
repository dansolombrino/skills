---
name: sweep-dispatch
description: Generate and launch experiment runs/sweeps across the GPU rigs (rig-4090, rig-3090ti, rig-3080ti, server-pro-6000-bw — also "4090", "3080 ti", "pro 6000", "bw", "blackwell"). Use when the user asks to run, launch, sweep, or dispatch experiments, split runs across machines, or rerun failed runs.
---

# sweep-dispatch

Launch machinery for runs and sweeps. Canon: `../research-project-init/references/conventions.md`. Templates: [references/templates.md](references/templates.md). Dispatch always happens **from rig-4090** (the hub); code reaches the rigs via rig-sync, launching via passwordless ssh.

## Before anything launches — two mandatory gates

1. **Ask which rigs are currently free** — never assume availability.
2. **Propose the assignment, get approval.** Weight by speed (`server-pro-6000-bw` 2.0, `rig-4090` 1.0, `rig-3090ti`/`rig-3080ti` 0.5 each) so each rig finishes its slice in roughly equal wall-clock. The user approves or amends before launch.

## Generate

For each run in the sweep grid, materialize under `scripts/NNN_exp/`:

```
scripts/NNN_exp/<flat run_id>/run.sh     # one folder per run, named by run_id_flat
scripts/NNN_exp/launch_<rig>.sh          # per-rig launcher: its slice, sequential
```

- `run.sh` is **self-contained**: the full python command with explicit hydra overrides, plus `.status` marker writes (template). Rerunning a failed run = rerunning its `run.sh` — whether that resumes or restarts was fixed at experiment design time (resume decision), never re-asked here.
- Folder names come from `run_id_flat` — identical strings to EXPERIMENTS.md rows and wandb run names.
- `scripts/` contains shell only. **Never put yaml under scripts/** — the grid lives in the launcher generation conversation and in EXPERIMENTS.md rows.

## Launch

- Per rig: one **named tmux session** (`NNN_exp`), running its `launch_<rig>.sh`, which executes the slice **sequentially** (one run at a time).
- `ssh <rig> tmux new-session -d -s <NNN_exp> 'cd <project> && bash scripts/NNN_exp/launch_<rig>.sh'`
- Tell the user how to watch: `ssh <rig>` then `tmux attach -t <NNN_exp>`.

## Tracking side-effects (same turn)

- Append `todo` rows to EXPERIMENTS.md for every generated run; flip launched ones to `inpr` (format + single-writer rules: `experiments-tracking` skill).
- Suggest a JOURNAL.md entry for the launch (`research-journal` skill).
