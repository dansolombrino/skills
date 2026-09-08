---
name: rig-board
description: Read and maintain the fleet-wide GPU lane board shared by every Research 2.0 project on the hub, so any chat knows which rig GPUs are held, by which project and wave, since when, and with what ETA. Use when the user asks whether the GPUs or a rig are free or busy, says to wait for GPUs or for another project's runs to finish, asks what is running across projects, or when sweep-dispatch claims, refreshes, or releases a lane; also to run or install the read-only web viewer. Do not use for Slurm clusters, for per-run status, or to decide cross-project priority.
---

# rig-board

The board is the one place every project and every chat on the hub looks to learn what the
rigs are doing. It is **fleet state**, never run state: a run's truth stays in its expected
final artifact and schema-v2 `.status.json` (`experiments-tracking`). The board only answers
"is `<rig>` gpu`<ids>` held, by whom, since when, ETA", so a chat told "wait for the GPUs" can
stop guessing. Canon for rigs, lanes and waves: `../research-project-init/references/conventions.md`.

Run this skill's bundled `scripts/board.py`. Configuration and the viewer service:
[references/configuration.md](references/configuration.md). `leonardo` is out of scope: Slurm
is its own coordination center and the cluster does not run out of cards.

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
python3 scripts/board.py status [--json] [--reconcile]   # whole fleet
python3 scripts/board.py free <rig> <gpu> [--reconcile]  # exit 0 free, 1 held/foreign, 2 unverifiable
python3 scripts/board.py reconcile [--rig <rig>]         # probe rigs, correct the board
python3 scripts/board.py claim --rig <rig> --gpu <ids> --project <name> --experiment <NNN_exp> --wave <wave_id> --project-root <abs path> --runs-total <n>
python3 scripts/board.py refresh --rig <rig> --gpu <ids> --wave <wave_id> --active-run <run_id_flat> --progress "<display>" --eta <ISO|''> --eta-basis "<basis>" --runs-done <n>
python3 scripts/board.py release --rig <rig> --gpu <ids> --reason "<why>"
python3 scripts/board.py serve [--port 8765] [--reconcile-every 120]
```

Set `RIGSYNC_REGISTRY` or pass `--registry` to use another registry.

## Answering "are the GPUs free?" / "wait for the GPUs"

1. Run `status --reconcile` (or `free <rig> <gpu> --reconcile` for one lane). Report what the
   board says with its probe age: holder project, experiment, wave, active run, progress, ETA
   and basis, plus foreign cards and unreachable rigs. Open the report with a message-written
   timestamp as `experiments-tracking` requires.
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

`serve` is a stdlib HTTP server: `/` is a read-only auto-refreshing page (one card per rig,
one row per GPU: free, running, interrupted, foreign, unreachable, with holder, progress, ETA,
held-since, orchestrator silence and the `tmux attach` command), `/api/board` is the JSON
behind it. It reconciles on its own timer so the page stays honest when no chat is alive. It
takes no write actions. Install it as a user service on the hub per the configuration
reference; the URL is `http://<hub address>:<port>`.

## Hard limits

- Never edit lane files by hand; every change goes through the script so the history stays true.
- Never treat the board as proof a run is done or failed. Read the run's own signals.
- No queueing or greenlighting: the board reports, the user decides.
