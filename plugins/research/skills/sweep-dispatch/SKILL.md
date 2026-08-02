---
name: sweep-dispatch
description: Generate and launch experiment runs/sweeps across the GPU rigs (rig-4090, rig-3090-ti, rig-3080-ti, behemoth — also "4090", "3080 ti", "pro 6000", "bw", "blackwell", "server-pro-6000-bw"). Use when the user asks to run, launch, sweep, or dispatch experiments, monitor or babysit running experiments, split runs across machines or GPUs, rerun failed runs, or recover/resume a wave after a rig crash or reboot.
---

# sweep-dispatch

Launch machinery for runs and sweeps. Canon: `../research-project-init/references/conventions.md`. Templates: [references/templates.md](references/templates.md). Dispatch always happens **from rig-4090** (the hub); source reaches assigned peers through `$rig-sync`, then launches via passwordless ssh. Use the skill's bundled `rigsync.py doctor` and `push-source --dry-run` before dispatch. If either fails, stop; never substitute ad-hoc copying.

Vocabulary (canon): a **wave** is one dispatch decision, identified by `YYYYMMDD-HHMMSS`; a **slice** is the portion of a wave on one rig; a **lane** is the portion of a slice on one GPU set. Lanes run in parallel, runs within a lane run sequentially.

## Before anything launches — mandatory gates

1. **run_id coverage check** — every param varied in the sweep grid, AND every behavior-affecting config param added/changed since this experiment's last runs, must be in `RUN_ID_PARAMS`. If not, **halt** and route through the `experiment-design` skill's run_id evolution protocol (re-election + migration) before generating anything — otherwise new runs collide with old artifacts: overwritten, or silently skipped as "done". The artifact guard is only sound under this gate: `guard_run_config` catches collisions after Python starts, but an artifact-skipped run never enters Python.
1b. **helper safety check** — verify `code/common/run_id.py` provides the current percent-encoded `run_id_path`/`run_id_flat` renderers and `hydra_override_arg`. For a pre-1.0 project, inspect existing run values before upgrading the helper. If every existing component uses only unreserved characters (`A-Z a-z 0-9 - . _ ~`), the rendered paths are unchanged and the helper can be upgraded directly. If any existing component needs encoding, **halt and ask the user** to migrate those artifact/script/log paths or freeze the old experiment and start a new sub-experiment; never silently change run identity.
2. **Ask which rigs AND which GPUs on them are usable** — never assume availability, never hardcode the multi-GPU server's card count. **On `behemoth`, gpu0 is the only answer you may propose**: it is a shared machine and GPU 0 is the only card that is ours (canon: conventions.md § Rig fleet). Anything wider requires the user to explicitly state they got authorization for named cards — never inferred from an idle `nvidia-smi`, never fished for; and that grant covers this wave only, is never persisted, and must be re-given next time. Confirm current load with `nvidia-smi` too — a card at high utilization will steal SM time from whatever is already running and skew its `elapsed`, which the project relies on as a reference runtime.
3. **Propose the assignment, get approval.** Capacity of a lane = the rig's **per-GPU** weight (`behemoth` 2.0, `rig-4090` 1.0, `rig-3090-ti`/`rig-3080-ti` 0.5) × the GPUs in that lane, so every lane finishes in roughly equal wall-clock. `behemoth` contributes `2.0 × 1` unless a grant for this wave widens it. Within one (wave, rig) the GPU sets must be **disjoint**. Present run → rig, gpu; the user approves or amends. If a grant is in play, restate it verbatim in the proposal ("using gpu 0,3 on behemoth per your authorization of <date>") so it is re-confirmed at approval time.
4. **Mint the wave id** the moment the assignment is approved: `date '+%Y%m%d-%H%M%S'` on rig-4090. One id for the whole dispatch, shared by every rig and lane — it goes into the paths, so it must exist before generation. Never a semantic slug; the wave README carries the meaning.

## Generate

One script kind. For each run in the wave, materialize:

```
scripts/NNN_exp/<run_id_flat>/wave_<wave_id>/
    README.md                     # what this wave is + this run's placement; written once, never updated
    wave_<rig>_gpu<ids>.sh        # this run's invocation in this wave
```

- The wave script is **self-contained** (full python command with explicit hydra overrides, each rendered through `hydra_override_arg`) and **self-guarded**: it exits early only if its expected final artifact is present. A `.status.json` that says `done` while the artifact is absent is inconsistent, so the script warns and re-executes. That guard is what makes lane dispatch idempotent — there is no launcher file holding it. Whether a re-execution resumes or restarts was fixed at experiment design time, never re-asked here.
- It **exports `CUDA_VISIBLE_DEVICES` and `WAVE_ID`**. On `behemoth` lanes it additionally carries a `# GPU auth:` provenance header and a `BEHEMOTH_AUTHORIZED_GPUS` variable, and **aborts before python** if `CUDA_VISIBLE_DEVICES` is not a subset of it — the second line of defence behind gate 2. It **captures its own log** into the mirror of its own path under `logs/`: `logs/NNN_exp/<run_id_flat>/wave_<wave_id>/wave_<rig>_gpu<ids>-<timestamp>.log`. Timestamped per launch, history kept, never overwritten.
- Folder names come from `run_id_flat` (via `code/common/run_id.py`) — identical strings to EXPERIMENTS.md rows and wandb run names. `ls scripts/NNN_exp/<run_id_flat>/` is that run's execution history.
- **The filesystem encodes the assignment**: a run is on that rig and those GPUs precisely because `wave_<rig>_gpu<ids>.sh` exists in its wave folder. No separate manifest to drift.
- Only `scripts/` and `logs/` are wave-scoped. `checkpoints/`, `evaluations/`, `plots/` stay run-scoped in path form — artifacts must keep a stable per-run path or the self-guard and `guard_run_config` both break.
- `scripts/` contains shell only. **Never put yaml under scripts/** — the grid lives in the generation conversation, the wave README, and EXPERIMENTS.md rows.

## Launch & monitor — orchestrator + one Codex subagent per rig

The chat where the launch is requested is the **orchestrator**. It never launches or polls rigs itself. After the assignment is approved, use Codex's collaboration tools to spawn **one background subagent per assigned rig in parallel**, then collect their reports. One subagent per *rig*, not per lane. A peer subagent drives one SSH target; when the assigned rig is the current local hub, its subagent uses direct bounded commands and never requires self-SSH. If collaboration tools are unavailable, stop before launch and tell the user; do not silently collapse monitoring into the orchestrator.

Each rig subagent:

1. **Dispatches each of its lanes** as its own named tmux session — `<project>_<NNN_exp>_<wave_id>_<rig>_gpu<ids>` — running a glob loop over that lane's wave scripts (exact one-liner in [references/templates.md](references/templates.md)). Sequencing is a dumb shell loop on purpose: it must survive subagent death, context compaction and multi-day queues.
2. **Monitors** its queues to completion, judging each run by the three signals (hierarchy in `conventions.md` — artifacts are golden):
   - **expected final artifact** on disk (final checkpoint / final eval output) ⇒ truly done;
   - **`.status.json`** (`state`, `heartbeat`, `progress`, timing, `wave_id`, `gpu`);
   - tail of the latest **`logs/NNN_exp/<run_id_flat>/wave_<wave_id>/wave_<rig>_gpu<ids>-<timestamp>.log`** — tracebacks/errors ⇒ failed; stale heartbeat + silent log ⇒ first rule out a machine fault (below), then suspect a hang and say so.
   Poll at intervals matched to expected run length (reference `elapsed` values in EXPERIMENTS.md help here) — don't hammer ssh.
3. **Reports back** per run: outcome (`done`/`failed`/hung/`interrupted→relaunched`), which lane it ran in, plus `started`/`progress`/`ended`/`elapsed` read from `.status.json`.

**Single-writer rule: only the orchestrator edits EXPERIMENTS.md**, from the subagents' reports. Subagents never touch it.

Tell the user how to watch manually too: for a peer, `ssh <rig>` then
`tmux attach -t <project>_<NNN_exp>_<wave_id>_<rig>_gpu<ids>`; for the current local hub, run the
`tmux attach` command directly.

## Machine faults — reboot/crash detection & auto-resume

Each rig subagent is also its rig's watchdog. A machine-level failure (crash, reboot, power loss) kills every tmux session on it and freezes each running run's `.status.json` at `running` — those are **interrupted** runs (machine fault), never `failed` ones (code error). Handle it with this state machine:

- **Unreachable** — ssh fails/times out. Use noninteractive bounded probes (`ssh -o BatchMode=yes -o ConnectTimeout=10 <rig> true`). One failed poll may be a network blip: declare the rig *down* only after 2–3 consecutive failures. Then keep probing reachability every 1–2 minutes and report once to the orchestrator: "rig down since <time>". Never mark its runs failed while it's down.
- **Back up — diagnose before acting** (one ssh round-trip; exact command in [references/templates.md](references/templates.md)): boot time (`uptime -s`) newer than the dispatch time ⇒ the rig rebooted (tmux never survives a reboot); a lane's session missing from `tmux ls` ⇒ that lane is gone even without a reboot; session alive and heartbeat advancing ⇒ it was only a network blip — resume normal polling, touch nothing.
- **Recover, lane by lane** — for each dead lane, re-issue that lane's dispatch one-liner **with the same wave id** (a relaunch is not a new dispatch: same paths, same session names). This is safe because every wave script is **self-guarded**: completed runs are skipped, interrupted ones re-execute — resuming or restarting per the design-time resume decision.
- **Recovery never re-decides GPU placement** — a relaunch re-executes the *same* wave script, so it inherits that wave's authorization (including its `# GPU auth:` header and guard) unchanged. Never widen a lane's GPU set during recovery, and never move a lane to a different card on `behemoth` to work around a busy GPU 0. If placement genuinely must change, that is a new wave, needing a new assignment approval and — for anything past gpu0 on `behemoth` — a fresh grant from the user.
- **Never double-launch** — immediately before relaunching a lane, check `tmux has-session -t <lane session>` again; if it exists, just monitor it.
- **Legacy layouts** — a project still on the old `run.sh` + `launch_<rig>.sh` scheme has no per-run self-guard (and pre-idempotent launchers would redo finished runs on relaunch). Check before recovering, and offer to migrate the experiment to the wave layout — but **never migrate while runs are in flight**: renaming script and log trees under a live run breaks the paths it is writing into. If anything is `inpr`/`running`, say so and defer; recover the legacy slice as-is for now (`experiments-tracking`, migration rules).
- **Autonomous, not silent** — relaunching already-approved work is not a new dispatch decision: don't ask permission, recover and report — which runs were already done (skipped), which were interrupted and relaunched, in which lanes, and whether each resumes or restarts.

## Status updates to the user

Every status update **opens with the current date and time** — run `date '+%Y-%m-%d %H:%M'` first and lead with it ("Status as of 2026-07-20 16:45 — ..."). Applies to interim reports, subagent-relay summaries, and the final wrap-up.

## Tracking side-effects (same turn)

- Append `todo` rows to EXPERIMENTS.md for every generated run — **one row per (run, wave)**, carrying its wave id, rig and gpu. A run re-launched in a later wave gets a **new row**, never an in-place update.
- Flip launched rows to `inpr` and fill `started`; on completion reports flip to `done`/`failed` and fill `ended`/`elapsed`; keep `progress`/`eta` fresh while runs are in flight (format + single-writer rules: `experiments-tracking` skill).
- Suggest a JOURNAL.md entry for the launch, naming the wave id (`research-journal` skill).
