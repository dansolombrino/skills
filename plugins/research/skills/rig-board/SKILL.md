---
name: rig-board
description: Read and maintain the fleet-wide GPU lane board shared by every Research 2.0 project on the hub, so any chat knows which rig GPUs are held, by which project and wave, since when, and with what ETA, plus the read-only Slurm queue of a cluster such as Leonardo. Use when the user asks whether the GPUs or a rig are free or busy, says to wait for GPUs or for another project's runs to finish, asks what is running or queued across projects or on the cluster, or when sweep-dispatch claims, refreshes, or releases a lane; also to run or install the read-only web viewer. Do not use for per-run status, to submit or cancel jobs, or to decide cross-project priority.
---

# rig-board

The board is the one place every project and every chat on the hub looks to learn what the
rigs are doing. It is **fleet state**, never run state: a run's truth stays in its expected
final artifact and schema-v2 `.status.json` (`experiments-tracking`). The board only answers
"is `<rig>` gpu`<ids>` held, by whom, since when, ETA", so a chat told "wait for the GPUs" can
stop guessing. Canon for rigs, lanes and waves: `../research-project-init/references/conventions.md`.

Run this skill's bundled `scripts/board.py`. Configuration and the viewer service:
[references/configuration.md](references/configuration.md). A Slurm cluster such as `leonardo`
is shown read-only (its `squeue --me` queue: running and pending jobs, reasons, time left) and
is never a lane: Slurm is its own coordination center and the cluster does not run out of cards.

## Model

- `[board] root` in the rigsync user registry (`~/.config/rigsync/machines.toml`) names a
  directory outside every project tree. `lanes/<rig>/gpu<ids>.json` exists exactly while that
  GPU set is held; absent means free. `rigs/<rig>.json` is the last probe. `history.jsonl` is
  the append-only event log.
- **Availability is "no lane file after reconcile."** `nvidia-smi` never grants availability;
  it only reveals *foreign* usage (a busy card nobody on the board holds, normally another
  user on `behemoth`), which is shown and never claimable.
- **Reconstructable from the machines.** Lane tmux sessions are named
  `<project>_<NNN_exp>_<wave_id>_<rig>_gpu<ids>`, so `reconcile` adopts any live session that
  has no lane file, releases a lane whose session is gone, and marks a lane *interrupted* when
  the rig rebooted after the claim. A dead chat therefore never leaves a phantom hold, and a
  live lane never goes unseen.
- **Single writer per lane.** Only the chat that launched a wave (the orchestrator of
  `sweep-dispatch`, on the hub) claims, refreshes and releases its lanes. Monitoring subagents
  never touch the board. `reconcile` may run from anywhere and only corrects provenance.
- Board ETAs are copies of the orchestrator's own estimates (`experiments-tracking` hierarchy),
  refreshed at its ten-minute tick. An adopted lane has no ETA until an orchestrator reports.

## Commands

```bash
python3 scripts/board.py status [--json] [--reconcile] [--all]   # whole fleet; --all lists every Slurm job, not three per group
python3 scripts/board.py free <rig> <gpu> [--reconcile]  # exit 0 free, 1 held/foreign, 2 unverifiable
python3 scripts/board.py reconcile [--rig <rig|cluster>]...  # probe rigs, correct the board (repeatable)
python3 scripts/board.py claim --rig <rig> --gpu <ids> --project <name> --experiment <NNN_exp> --wave <wave_id> --project-root <abs path> --runs-total <n> [--tmux-session <name>]
python3 scripts/board.py refresh --rig <rig> --gpu <ids> --wave <wave_id> --active-run <run_id_flat> --progress "<display>" --eta <ISO|''> --eta-basis "<basis>" --runs-done <n> [--runs-total <n>]
python3 scripts/board.py release --rig <rig> --gpu <ids> --reason "<why>"
python3 scripts/board.py history [--hours 24] [--limit 500] [--refresh] [--json]   # what happened on the board
python3 scripts/board.py serve [--port 8765] [--bind 0.0.0.0] [--reconcile-every 120]
python3 scripts/board.py token                            # fresh access token for [board] token
```

Set `RIGSYNC_REGISTRY` or pass `--registry` to use another registry.

## Answering "are the GPUs free?" / "wait for the GPUs"

1. Run `status --reconcile` (or `free <rig> <gpu> --reconcile` for one lane). Report what the
   board says with its probe age: the fleet line (free / held / foreign / interrupted counts and
   the soonest ETA), then per rig the holder project, experiment, wave, active run, progress,
   ETA and basis; for a free card its model, VRAM and current use; for a foreign card the
   owning user, process and memory (and "idle, holding memory" when it holds VRAM at ~0% util);
   stray experiment-looking tmux sessions that are not lanes; unreachable rigs and rigs whose
   `nvidia-smi` failed (GPU state unknown, never "free"); and for each Slurm target its running
   and pending jobs, folded into sweep groups with the varying configuration per job and the
   group's completed / failed / cancelled / timeout counts (from `sacct`, last three days) as a
   share of its total, and the account budgets from `saldo` (hours used and left, this month's
   allowance, expiry, flagged when exhausted, used up for the month or expiring; nightly data,
   so today's jobs are not yet counted). Open the
   report with a message-written timestamp as `experiments-tracking` requires. If a cluster is
   unreachable because SSH authentication failed, say that the certificate needs renewing
   (`sweep-dispatch` → `references/cineca-slurm.md`). `history` answers "what happened while
   nobody was watching" (claims, releases, adoptions, interruptions, queue changes).
2. To wait, use the host's recurring wait/monitor primitive and re-run the check each tick;
   never busy-loop with shell `sleep`. Report when the lane frees up, then continue the
   original request. If the ETA is unavailable, say so and why (adopted lane, silent
   orchestrator, unreachable rig) instead of inventing one.
3. Never release another project's lane to make room, and never claim over a foreign card.
   Ordering between projects is the user's decision: present the holder and ETA and let them
   choose to wait or move to another rig.

## Dispatch hooks (owned by `sweep-dispatch`)

- Gate 2 reads `status --reconcile` before proposing an assignment; held and foreign lanes are
  not free capacity.
- `claim` immediately after each lane's tmux session is confirmed running. A claim on a held
  lane exits 3 and blocks the launch. A same-session claim is a re-claim (recovery relaunch).
- `refresh` at every ten-minute tick, in the same turn EXPERIMENTS.md is reconciled.
- `release` when the lane is terminal, in the final summary turn. A machine fault leaves the
  lane on the board as interrupted until recovery re-claims it or the user releases it.

## Viewer

`serve` is a stdlib HTTP server. `/` is the read-only page (`assets/viewer.html`): a fleet
strip (free / held / foreign / issues / silent / Slurm counts, each a click-to-filter), the
soonest lane ETA, probe age and next-probe countdown, then one card per rig with one row per
GPU (free with model, VRAM and utilization gauges; running / claimed / silent / interrupted /
unreachable lanes with holder, wave, run, progress, ETA, held-since, orchestrator age and
copy buttons for `tmux attach` and the project path; foreign cards with the owning user,
process, memory and age; a collapsed list of stray sessions), a Slurm card that folds sweep
jobs into groups (running / pending / completed / failed counts with their share of everything
submitted in the accounting window, a stacked bar, end-time window, earliest pending start,
shared configuration, per job the varying part of the name) with the account budgets above the
queue and footnotes for the nightly lag and the accounting unit, a filter box, state chips, free-first sort, light/dark
theme, optional browser notifications on lane changes, and a "history" panel of the last 24 h.
The tab title and favicon carry the free count. Polling pauses while the pointer is over a
card or text is selected, and a lost server shows the last good read as stale. `/api/board`
(schema `api_version` 2, additive over v1: `summary`, `serve`, per-rig `free_gpus`,
`stray_sessions`, `gpu_probe_ok`, `load`, per-GPU `name`, `memory_total_mib`, `temperature_c`,
`power_w`, `processes`; per-cluster `groups`) and `/api/history?hours=&limit=&refresh=` are the
JSON behind it. It reconciles on its own timer so the page stays honest when no chat is alive.
It takes no write actions. Install it as a user service on the hub per the configuration
reference; the URL is `http://<hub address>:<port>`. With `[board] token` set, the page and
the API require that token (query once, then a cookie); require it before exposing the port
beyond the LAN.

## Hard limits

- Never edit lane files by hand; every change goes through the script so the history stays true.
- Never treat the board as proof a run is done or failed. Read the run's own signals.
- No queueing or greenlighting: the board reports, the user decides.
