---
name: sweep-dispatch
description: Generate, launch, and continuously monitor experiment runs/sweeps across the GPU rigs (rig-4090, rig-3090-ti, rig-3080-ti, behemoth — also "4090", "3080 ti", "pro 6000", "bw", "blackwell", "server-pro-6000-bw"). Use when the user asks to run, launch, sweep, or dispatch experiments, receive timestamped ten-minute status/ETA updates, monitor or babysit running experiments, split runs across machines or GPUs, rerun failed runs, or recover/resume a wave after a rig crash or reboot.
---

# sweep-dispatch

Launch machinery for runs and sweeps. Canon: `../research-project-init/references/conventions.md`. Templates: [references/templates.md](references/templates.md). Dispatch always happens **from rig-4090** (the hub). GitHub distributes one tested, tagged commit through `rig-sync`; `environment-sync` establishes runtime parity; rsync is only for artifacts. If either gate fails, stop.

Read `program/00-execution-agreement.md` first and require the Research 2.0 surfaces. Stop on a
legacy layout; do not offer migration or recover it as-is. In engineering-manual mode, preview and
wait at the assignment/deployment/launch gate. In engineering-auto mode, choose and execute only
inside the approved repo, branch, rigs/GPUs, compute/budget, and destination envelope. Shared
`behemoth` cards beyond gpu0, new destinations, destructive actions, and envelope expansion remain
protected in every mode.

Vocabulary (canon): a **wave** is one dispatch decision, identified by `YYYYMMDD-HHMMSS`; a **slice** is the portion of a wave on one rig; a **lane** is the portion of a slice on one GPU set. Lanes run in parallel, runs within a lane run sequentially.

## Before anything launches — mandatory gates

1. **run_id coverage check** — every param varied in the sweep grid, AND every behavior-affecting config param added/changed since this experiment's last runs, must be in `RUN_ID_PARAMS`. If not, **halt** and route through the `experiment-design` skill's run_id evolution protocol (re-election + migration) before generating anything — otherwise new runs collide with old artifacts: overwritten, or silently skipped as "done". The artifact guard is only sound under this gate: `guard_run_config` catches collisions after Python starts, but an artifact-skipped run never enters Python.
1b. **helper safety check** — verify `code/common/run_id.py` provides the canonical percent-encoded
`run_id_path`/`run_id_flat` renderers and `hydra_override_arg`. Any older helper makes the project
unsupported; halt without upgrading it in place.
1c. **environment contract check** — require exact uv/Python pins, current `uv.lock`, the standard
`code/common/environment.py`, and the project GPU smoke. Use `environment-sync` to verify the hub
fingerprint before the experiment smoke. Dependency-contract changes must be committed and
verified before wave generation; read `[environment].name` from `sync.toml` and never let a
launch-time command repair or relock the configured environment.
1e. **rig declaration check** — resolve each assigned rig's project path with
`rig-sync repo-path --machine <rig>` and its storage floor with
`rig-sync storage-env --machine <rig>`, and substitute those values into the wave scripts and the
dispatch, monitor, and recovery commands. Never type a project path or a quota filesystem by
hand: both are declared once, and a second copy is what goes stale. A rig missing from `sync.toml`
or the machine registry is not assignable — stop rather than guessing its layout.
1d. **telemetry contract check** — require schema-v2 `code/common/status.py` from
`research-project-init` and structured progress call sites in every target training/eval entrypoint.
The helper must provide timezone-aware timestamps, live elapsed time, and its automatic
60-second heartbeat; entrypoints call
`heartbeat(completed=<done>, total=<total>, unit=<label>)`. Display-only telemetry makes the
project unsupported. Telemetry does not join `RUN_ID_PARAMS`.
2. **Resolve usable rigs and GPUs.** In manual mode, ask. In auto mode, use only the named rig/GPU
set in the envelope after checking current load; never infer availability from `nvidia-smi`. On
`behemoth`, gpu0 is the only default. Any additional named card requires an explicit per-wave user
grant regardless of mode and is never persisted.
3. **Build and authorize the assignment.** Balance disjoint lanes by the canonical per-GPU weights.
Present or record run → rig/GPU, exact smoke command, staged files, journal entry, branch/remote,
environment fingerprint, provisioning dry run, commit/tag/push, and fast-forward scope. Manual mode
waits for approval; auto mode records that every item is within the envelope. Restate any shared-GPU
grant verbatim.
4. **Mint the wave id** when the assignment is authorized: `date '+%Y%m%d-%H%M%S'` on rig-4090.
One id for the whole dispatch, shared by every rig and lane. Never use a semantic slug.

## Generate

One script kind. For each run in the wave, materialize:

```
scripts/NNN_exp/<run_id_flat>/wave_<wave_id>/
    README.md                     # what this wave is + this run's placement; written once, never updated
    wave_<rig>_gpu<ids>.sh        # this run's invocation in this wave
```

- The wave script is **self-contained** (full python command with explicit hydra overrides, each rendered through `hydra_override_arg`) and **self-guarded**: it exits early only if its expected final artifact is present. A `.status.json` that says `done` while the artifact is absent is inconsistent, so the script warns and re-executes. That guard is what makes lane dispatch idempotent — there is no launcher file holding it. Whether a re-execution resumes or restarts was fixed at experiment design time, never re-asked here.
- It exports `CUDA_VISIBLE_DEVICES`, `WAVE_ID`, and the approved `ENVIRONMENT_FINGERPRINT`.
  Before experiment Python it recomputes the fingerprint with the configured environment's
  `bin/python`, compares it, and runs the bounded project GPU smoke without syncing.
  Drift/compatibility failure exits `87`.
  On `behemoth` it retains the per-wave authorization guard before the smoke. On a quota'd rig it
  also checks storage headroom first, exiting `88` rather than dying mid-checkpoint and leaving a
  truncated artifact that looks real. It captures its own
  timestamped log under the mirror path in `logs/`.
- Folder names come from `run_id_flat` (via `code/common/run_id.py`) — identical strings to EXPERIMENTS.md rows and wandb run names. `ls scripts/NNN_exp/<run_id_flat>/` is that run's execution history.
- **The filesystem encodes the assignment**: a run is on that rig and those GPUs precisely because `wave_<rig>_gpu<ids>.sh` exists in its wave folder. No separate manifest to drift.
- Only `scripts/` and `logs/` are wave-scoped. `checkpoints/`, `evaluations/`, `plots/` stay run-scoped in path form — artifacts must keep a stable per-run path or the self-guard and `guard_run_config` both break.
- `scripts/` contains shell only. **Never put yaml under scripts/** — the grid lives in the generation conversation, the wave README, and EXPERIMENTS.md rows.

## Test, commit, and deploy one revision

After generation and before any tmux launch:

1. Stage only the approved code/config/scripts/tracking/JOURNAL files. Require every execution
   file to be tracked and no unstaged or non-ignored untracked file under `code/`, `config/`,
   `scripts/`, or a root dependency manifest. Record a hash of the staged patch.
2. Run the experiment's design-time smoke command on rig-4090. Missing smoke metadata makes the
   project unsupported. After the smoke, require the same staged-patch hash and a clean execution
   worktree; a test that rewrites source invalidates itself.
3. Commit as `dispatch(<NNN_exp>): wave <wave_id>`. Refuse an existing `wave--<wave_id>` ref, then
   create that annotated tag at `HEAD`. Push the configured branch and exact tag without force;
   verify GitHub resolves both to the full local commit SHA. A push failure blocks dispatch.
4. Run `rig-sync deploy-revision` first as a dry run, obtain authorization under the active
   engineering mode, then confirm it for **all assigned rigs together**. It may only fast-forward clean
   checkouts on the configured branch/remote and refuses a different revision on a rig with
   active project lanes or `running` statuses. Run `verify-revision` across the full assigned set.
   Then run the approved `environment-sync provision` when its dry run showed changes and
   `verify` the full rig set plus every assigned lane. Do not launch unless source, fingerprint,
   and GPU smoke all pass.

Later edits to EXPERIMENTS.md/JOURNAL.md are allowed on the hub because they are state/prose, not
execution source. A different dispatch revision may not be deployed onto a rig while an older
wave is active there.

## Launch & monitor — orchestrator + one subagent per rig

The chat where the launch is requested is the **orchestrator**. It performs the all-rig Git deployment gate but never launches or polls rigs itself. Only after that gate passes, use the host's parallel background-subagent capability to spawn **one background subagent per assigned rig in parallel**, then collect their reports. One subagent per *rig*, not per lane. A peer subagent drives one SSH target; when the assigned rig is the current local hub, its subagent uses direct bounded commands and never requires self-SSH. If the host cannot run background subagents in parallel, stop before launch and tell the user; do not silently collapse monitoring into the orchestrator.

Before launch, also require a recurring wait/monitor primitive that lets the orchestrator remain
active and responsive for the full wave. If it is unavailable, stop before launch. The
orchestrator must not send its final response while any run remains queued or running unless the
user explicitly asks it to stop monitoring. Use the recurring wait primitive between events;
never implement monitoring with shell `sleep`.

Each rig subagent:

1. Runs `rig-sync verify-revision` and read-only `environment-sync verify` for its rig/lane
   immediately before launch, then **dispatches each
   lane** as its own named tmux session — `<project>_<NNN_exp>_<wave_id>_<rig>_gpu<ids>` — running
   a glob loop over that lane's wave scripts (exact one-liner in
   [references/templates.md](references/templates.md)). Each queued script repeats the Git guard
   before Python. Source-drift `86`, environment-drift `87`, or insufficient storage `88` stops the lane; ordinary run failures continue the queue.
   Sequencing remains a shell loop so it survives subagent death, compaction, and multi-day queues.
2. **Monitors** its queues to completion, judging each run by the three signals (hierarchy in `conventions.md` — artifacts are golden). Batch all lanes/status files for that rig into bounded probes; in normal operation obtain at least one fresh snapshot every five minutes so a scheduled chat update never depends on an arbitrarily old poll:
   - **expected final artifact** on disk (final checkpoint / final eval output) ⇒ truly done;
   - **`.status.json`** (`schema_version`, `state`, `heartbeat`, display + numeric progress,
    timing, `wave_id`, `gpu`, source tag/SHA, environment fingerprint);
   - tail of the latest **`logs/NNN_exp/<run_id_flat>/wave_<wave_id>/wave_<rig>_gpu<ids>-<timestamp>.log`** — tracebacks/errors ⇒ failed; stale heartbeat + silent log ⇒ first rule out a machine fault (below), then suspect a hang and say so.
   Within the five-minute freshness cap, match poll intervals to expected run length (reference
   `elapsed` values in EXPERIMENTS.md help here) — don't hammer ssh.
3. **Reports back** snapshots with an observation timestamp and heartbeat age, per-run outcome
   (`queued`/`running`/`done`/`failed`/hung/`interrupted→relaunched`), lane, structured progress,
   timing, and queued order. Push terminal/fault/recovery transitions immediately; the
   orchestrator requests or uses the newest complete snapshot for each scheduled report.

**Single-writer rule: only the orchestrator edits EXPERIMENTS.md**, from the subagents' reports. Subagents never touch it.

Tell the user how to watch manually too: for a peer, `ssh <rig>` then
`tmux attach -t <project>_<NNN_exp>_<wave_id>_<rig>_gpu<ids>`; for the current local hub, run the
`tmux attach` command directly.

## Machine faults — reboot/crash detection & auto-resume

Each rig subagent is also its rig's watchdog. A machine-level failure (crash, reboot, power loss) kills every tmux session on it and freezes each running run's `.status.json` at `running` — those are **interrupted** runs (machine fault), never `failed` ones (code error). Handle it with this state machine:

- **Unreachable** — ssh fails/times out. Use noninteractive bounded probes (`ssh -o BatchMode=yes -o ConnectTimeout=10 <rig> true`). One failed poll may be a network blip: declare the rig *down* only after 2–3 consecutive failures. Then keep probing reachability every 1–2 minutes and report once to the orchestrator: "rig down since <time>". Never mark its runs failed while it's down.
- **Back up — diagnose before acting** (one ssh round-trip; exact command in [references/templates.md](references/templates.md)): boot time (`uptime -s`) newer than the dispatch time ⇒ the rig rebooted (tmux never survives a reboot); a lane's session missing from `tmux ls` ⇒ that lane is gone even without a reboot; session alive and heartbeat advancing ⇒ it was only a network blip — resume normal polling, touch nothing.
- **Recover, lane by lane** — first re-run `verify-revision` and `environment-sync verify` for
  the wave's original SHA/fingerprint and lane smoke. If either
  fails, report recovery blocked and do not alter the checkout. Otherwise re-issue that lane's
  dispatch one-liner **with the same wave id** (a relaunch is not a new dispatch: same paths,
  session names, tag, and commit). Completed runs skip; interrupted ones re-execute.
- **Recovery never re-decides GPU placement** — a relaunch re-executes the *same* wave script, so it inherits that wave's authorization (including its `# GPU auth:` header and guard) unchanged. Never widen a lane's GPU set during recovery, and never move a lane to a different card on `behemoth` to work around a busy GPU 0. If placement genuinely must change, that is a new wave, needing authorization under the active engineering mode and — for anything past gpu0 on `behemoth` — a fresh grant from the user.
- **Never double-launch** — immediately before relaunching a lane, check `tmux has-session -t <lane session>` again; if it exists, just monitor it.
- **Unsupported layouts** — any noncanonical script/status layout blocks recovery. Do not migrate
  or execute it.
- **Autonomous, not silent** — relaunching already-approved work is not a new dispatch decision: don't ask permission, recover and report — which runs were already done (skipped), which were interrupted and relaunched, in which lanes, and whether each resumes or restarts.

## Ten-minute status and ETA updates to the user

Anchor `next_update` to the confirmed launch time and advance it in exact 600-second increments;
collection or message latency must not drift later ticks. At every tick, even if nothing changed,
the orchestrator reconciles EXPERIMENTS.md from the newest per-rig snapshots and posts one compact
aggregate update. A completion, failure, suspected hang, rig outage, or recovery is reported as
soon as observed and does not reset `next_update`. When every run is terminal, post the final
summary immediately and stop the schedule.

Immediately before every scheduled, urgent, and final message, obtain a timezone-bearing local
timestamp (for example `date '+%Y-%m-%dT%H:%M:%S%:z'`) and open exactly with
`Status written <timestamp> —`. Do not reuse a status-file timestamp as the message-written time.
Include:

- wave counts: `done`, `running`, `queued`, `failed`, plus the estimated wave completion;
- one line per lane: active run, display progress, heartbeat age, estimated active-run
  completion, queued count/next run, and estimated lane completion;
- failures, stale telemetry, unreachable rigs, interruptions, and recovery actions;
- the basis for each estimate (`structured progress`, `exact-run history`, or
  `experiment median`), or `ETA unavailable: <reason>`.

All ETAs are estimates. Use `experiments-tracking` for the calculation hierarchy. A schema-v2
heartbeat older than three minutes is stale: diagnose rig/session/process health and mark its ETA
unavailable until liveness is resolved. Non-schema-v2 status makes the project unsupported.

## Tracking side-effects (same turn)

- Append `todo` rows to EXPERIMENTS.md for every generated run — **one row per (run, wave)**, carrying its wave id, rig and gpu. A run re-launched in a later wave gets a **new row**, never an in-place update.
- Flip launched rows to `inpr` and fill `started`; on completion reports flip to `done`/`failed` and fill `ended`/`elapsed`; keep `progress`/`eta` fresh while runs are in flight (format + single-writer rules: `experiments-tracking` skill).
- Put the exact launch entry in the assignment record (`research-journal` skill). Apply its
  mode-aware authorization before the dispatch commit; never bypass the journal hook. The entry names
  the wave, rationale, environment fingerprint, rig/GPU assignment, and any one-off behemoth
  authorization.
