---
name: sweep-dispatch
description: Generate and launch experiment runs/sweeps across the GPU rigs (rig-4090, rig-3090ti, rig-3080ti, server-pro-6000-bw — also "4090", "3080 ti", "pro 6000", "bw", "blackwell"). Use when the user asks to run, launch, sweep, or dispatch experiments, monitor or babysit running experiments, split runs across machines, or rerun failed runs.
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

- `run.sh` is **self-contained**: the full python command with explicit hydra overrides, with a `failed` fallback for `.status.json` (template). The `.status.json` itself is written by the python script (StatusWriter pattern, `experiment-design` skill). Rerunning a failed run = rerunning its `run.sh` — whether that resumes or restarts was fixed at experiment design time (resume decision), never re-asked here.
- `run.sh` **captures its own log**: the python invocation is piped through `tee` into `logs/NNN_exp/<run_id path>/run-<timestamp>.log` (conventions; pattern in the template). Timestamped per launch, history kept, never overwritten.
- Folder names come from `run_id_flat` — identical strings to EXPERIMENTS.md rows and wandb run names.
- `scripts/` contains shell only. **Never put yaml under scripts/** — the grid lives in the launcher generation conversation and in EXPERIMENTS.md rows.

## Launch & monitor — orchestrator + one subagent per rig

The chat where the launch is requested is the **orchestrator**. It never launches or polls rigs itself — after the assignment is approved, it spawns **one subagent per assigned rig** (single message, parallel Agent calls, run in background), then collects their reports.

Each rig subagent:

1. **Dispatches** its rig's slice: one **named tmux session** (`NNN_exp`) running `launch_<rig>.sh`, which executes the slice **sequentially** (one run at a time):
   `ssh <rig> "tmux new-session -d -s <NNN_exp> 'cd <project> && bash scripts/NNN_exp/launch_<rig>.sh'"`
2. **Monitors** its queue to completion, judging each run by the three signals (hierarchy in `conventions.md` — artifacts are golden):
   - **expected final artifact** on disk (final checkpoint / final eval output) ⇒ truly done;
   - **`.status.json`** (`state`, `heartbeat`, `progress`, timing);
   - tail of the latest **`logs/NNN_exp/<run_id path>/run-<timestamp>.log`** — tracebacks/errors ⇒ failed; stale heartbeat + silent log ⇒ suspect hang and say so.
   Poll at intervals matched to expected run length (reference `elapsed` values in EXPERIMENTS.md help here) — don't hammer ssh.
3. **Reports back** per run: outcome (`done`/`failed`/hung) plus `started`/`ended`/`elapsed` read from `.status.json`.

**Single-writer rule: only the orchestrator edits EXPERIMENTS.md**, from the subagents' reports. Subagents never touch it.

Tell the user how to watch manually too: `ssh <rig>` then `tmux attach -t <NNN_exp>`.

## Status updates to the user

Every status update **opens with the current date and time** — run `date '+%Y-%m-%d %H:%M'` first and lead with it ("Status as of 2026-07-20 16:45 — ..."). Applies to interim reports, subagent-relay summaries, and the final wrap-up.

## Tracking side-effects (same turn)

- Append `todo` rows to EXPERIMENTS.md for every generated run; flip launched ones to `inpr` and fill `started`; on completion reports, flip to `done`/`failed` and fill `ended`/`elapsed` (format + single-writer rules: `experiments-tracking` skill).
- Suggest a JOURNAL.md entry for the launch (`research-journal` skill).
