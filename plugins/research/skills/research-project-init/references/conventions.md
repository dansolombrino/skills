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
├── logs/            # run logs (tee'd stdout+stderr of each run, wandb dirs); mirrors the scripts/ tree exactly; gitignored, dir tracked, rig-local, never synced
├── plots/           # plots produced by visualizations/ (one subfolder per plotting script)
├── scripts/         # shell scripts that LAUNCH things (shell only — never yaml); wave-scoped, see "Waves and GPU lanes"
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

Every `scripts/` wave script captures its own stdout/stderr: the python invocation is piped
through `tee` into the **mirror of its own path under `logs/`** — same tree, `logs/` as the
leading folder, `-<YYYYmmdd-HHMMSS>` appended to the filename:

```
scripts/NNN_exp/<run_id_flat>/wave_<wave_id>/wave_<rig>_gpu<ids>.sh
logs/NNN_exp/<run_id_flat>/wave_<wave_id>/wave_<rig>_gpu<ids>-<YYYYmmdd-HHMMSS>.log
```

So `logs/` mirrors `scripts/` (flat run_id form, wave-scoped) — you reach a log from its
script — while `checkpoints/`, `evaluations/` and `plots/` mirror each other in the nested
run_id **path** form and stay run-scoped. Logs are timestamped per launch: history is kept,
logs are never overwritten, and a crash-recovery relaunch inside the same wave never clobbers
the earlier attempt. Hydra projects must disable hydra's job file logging (defaults entry
`- override hydra/job_logging: none`) or explicitly point it inside `logs/` — a stray
`<job_name>.log` must never appear in the project root.

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
  - **flat form** — e.g. `model=mlp,lr=1e-3,seed=0` → used for `scripts/` and `logs/`
    run-folder names, wandb run names, and log lines. Flat name ≡ wandb run, 1:1; it maps to
    one EXPERIMENTS.md row **per wave** the run took part in (see below).

### run_id schema evolution

A run_id election is only valid for the config schema it was elected against. Any change that
**adds, removes, or renames a behavior-affecting config param** (integration, refactor, new
feature) invalidates the current election ⇒ **mandatory re-election check with the user before
any new run launches**. A param may stay out of `RUN_ID_PARAMS` only if the user explicitly
rules it non-identifying (e.g. a pure logging knob). Otherwise, new runs varying the new param
would collide with old artifacts at the same `<run_id path>` — silently overwriting them, or
worse, being skipped by a self-guarded wave script as "already done".

- **If the param joins `RUN_ID_PARAMS`** — a migration decision is required for existing
  artifacts. Default proposal: **backfill-rename** — insert the new `param=<old implicit
  value>` segment at its elected position in existing dirs under
  `checkpoints/ evaluations/ plots/` (path form) and `logs/ scripts/` (flat form), and add the
  column to EXPERIMENTS.md rows
  (wandb run names cannot be backfilled — note the schema change in JOURNAL.md instead).
  Alternative (user's call): freeze the old tree and start a new sub-experiment for the new
  code path.
- **Runtime guard (canon)** — every run writes its **full resolved config snapshot** to
  `evaluations/NNN_exp/<run_id path>/.run_config.json` via `guard_run_config` in
  `code/common/run_id.py`, called before any artifact is written. If a snapshot already exists
  and differs on any param while the run_id is identical ⇒ **hard error** naming the differing
  params ("run_id collision — re-elect run_id or migrate"); never proceed. Same-config reruns
  (resume/retry) pass.
- Corollary: the wave script's "artifact present ⇒ skip" idempotency is only sound while the
  snapshot matches — the guard is what keeps the skip logic honest.

## Waves and GPU lanes

A **wave** is one dispatch decision: the set of runs launched together, however it is split
across machines. A run_id's grid is rarely launched in one go — you launch a subset now and
more later, re-launch what failed, re-run after a code fix, move work to a freer rig — and each
of those is its own wave. Waves are the unit of launch, of on-disk execution history, and of
EXPERIMENTS.md rows.

- **wave id** — `YYYYMMDD-HHMMSS` (e.g. `20260731-162043`). Minted **once per dispatch**, on
  rig-4090 in local time, at the moment the assignment is approved (the id is in the paths, so
  it must exist before generation). **Shared by every rig and lane in that dispatch** — one
  decision, one id — which is what makes `grep -r <wave_id> scripts/` return the whole dispatch.
- **Reused verbatim on crash recovery.** A post-reboot relaunch is not a new dispatch: same id,
  same paths, same tmux session names. A new id is minted only for a new dispatch decision.
- The id is a **pure timestamp — never a semantic slug.** It is replicated into `.status.json`,
  EXPERIMENTS.md rows, tmux session names and one script path per run, so it must never need
  renaming. Human meaning goes in the wave `README.md`, which is editable.

Two axes say where a run executed: the **rig** and the **GPU set** on it. A **lane** is the
portion of a wave assigned to one GPU set on one rig.

- The GPU field is **opaque identity, not semantics**: record which card(s) a run occupied,
  never reason about *why* it uses more than one (DDP, sharding, anything else is the
  experiment code's business).
- Written `gpu<ids>` — `gpu0`, or `gpu0,1,2,3` for a multi-GPU lane (comma style as in
  `run_id_flat`). **Always present, including on single-GPU rigs** (`wave_rig-4090_gpu0.sh`) —
  one filename shape everywhere, uniform globs.
- Lanes on a rig run **in parallel** (one tmux session each); runs within a lane run
  **sequentially**. Within one (wave, rig) the GPU sets must be **disjoint** — overlapping lanes
  would fight over a card. A run occupying every GPU therefore makes that rig a single lane for
  that wave.

Layout — one script kind, self-guarded, replacing the old `run.sh` + `launch_<rig>.sh` pair:

```
scripts/NNN_exp/<run_id_flat>/wave_<wave_id>/
    README.md                     # what this wave is; written once at launch, never updated
    wave_<rig>_gpu<ids>.sh        # this run's invocation in this wave
```

`ls scripts/NNN_exp/<run_id_flat>/` is that run's execution history — which waves it took part
in, on which rig, on which cards. **The filesystem encodes the assignment**: a run is on
rig-4090 GPU 0 in this wave precisely because `wave_rig-4090_gpu0.sh` exists in its wave folder,
so there is no separate manifest to drift. Only `scripts/` and `logs/` are wave-scoped;
`checkpoints/`, `evaluations/` and `plots/` stay **run-scoped in path form** — artifacts must
keep a stable per-run path or both the launcher's done-guard and `guard_run_config` break.

tmux session name: `<project>_<NNN_exp>_<wave_id>_<rig>_gpu<ids>` — e.g.
`grokmod_000_grokking_20260731-162043_behemoth_gpu0`. The project prefix matters
because tmux sessions are global per rig across projects; the wave id lets successive waves
coexist; the GPU field keeps parallel lanes apart. Generation, dispatch and recovery protocol:
`sweep-dispatch`.

## Rig fleet

**The canonical name is the ssh alias** — dispatch runs `ssh <rig>`, and the name also lands in
script filenames and tmux session names, so it must be the string that actually resolves.

| canonical name | sloppy names to recognize                                           | GPUs     | per-GPU speed weight | role |
|----------------|---------------------------------------------------------------------|----------|----------------------|------|
| `rig-4090`     | 4090                                                                | 1        | 1.0                  | main/hub: projects start here, dispatch + EXPERIMENTS.md writes happen here |
| `rig-3090-ti`  | 3090, 3090 ti, `rig-3090ti`                                         | 1        | 0.5                  | support |
| `rig-3080-ti`  | 3080, 3080 ti, `rig-3080ti`                                         | 1        | 0.5                  | support |
| `behemoth`     | pro 6000, 6000, bw, blackwell, `rig-6000-pro-blackwell`, `server-pro-6000-bw` | multiple | 2.0      | support |

**Not every GPU on a shared machine is ours.** `behemoth` has 8 cards but only some belong to
this fleet — never assume an idle GPU is available. Ask which cards are usable, every time.

The weight is **per GPU**, so a rig's capacity in the assignment math is
`weight × (number of free GPUs)`. Never hardcode the server's card count — it can change, and
the number of GPUs actually free varies per dispatch: **ask the user which rigs AND which GPUs
are free** before proposing an assignment (`sweep-dispatch`, pre-launch gates).

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
    "elapsed_s": 1234.5, "heartbeat": "<ISO>", "progress": "epoch 3/10",
    "wave_id": "20260731-162043", "gpu": "0"}
   ```

   Single writer: the python script owns it; the wave script only overwrites it with `failed`
   if the process died before python could. One file per run → conflict-free by construction;
   deleting a run's outputs resets its status. `wave_id` is what lets reconciliation match the
   file to the right EXPERIMENTS.md row (rows are per (run, wave)); `gpu` rides along for
   awareness. Both reach python as **environment variables** exported by the wave script
   (`WAVE_ID`, `CUDA_VISIBLE_DEVICES`), deliberately **not** as config params — they must stay
   out of the `guard_run_config` snapshot, or every re-launch in a new wave or on a different
   card would trip the run_id-collision hard error.
3. **Run log** — the latest
   `logs/NNN_exp/<run_id_flat>/wave_<wave_id>/wave_<rig>_gpu<ids>-<YYYYmmdd-HHMMSS>.log`
   (the mirror of the wave script's own path): the run's **entire stdout+stderr** (tee'd by the
   wave script, terminal-redirection style; timestamped per launch, history kept).
   Tracebacks/errors near the tail ⇒ failed; a stale heartbeat plus a silent log ⇒ suspect a
   hang — after ruling out a machine fault (below).

A `.status.json` frozen at `running` has three readings: **healthy** (heartbeat advancing),
**process hang** (rig up, tmux session alive, heartbeat frozen — report, don't auto-kill), or
**machine fault** (rig unreachable/rebooted — boot time via `uptime -s` postdates the
heartbeat — or the lane's tmux session is gone). A machine-fault run is **interrupted**, not
failed-by-code, and is safe to relaunch: every `wave_<rig>_gpu<ids>.sh` is **self-guarded**
(it skips itself if its final artifact is present, or its `.status.json` says `done`), so
re-issuing a lane's dispatch command never redoes finished work — interrupted runs re-execute,
resuming or restarting per the experiment's design-time resume decision. Detection + recovery
protocol: `sweep-dispatch`.

- **EXPERIMENTS.md is single-writer: only ever edited on rig-4090** — and within a launch
  session, only by the orchestrator chat, never by monitoring subagents.

## Root-file duties

- EXPERIMENTS.md = **state** (tables, reconciled from the three signals above; includes each
  run's `started`/`ended`/`elapsed` — reference runtimes for estimating future temporal
  budgets). JOURNAL.md = **story** (prose: what, why, what was learned; append-only, never
  rewritten).
- New env var read by code → `.env.example` updated in the SAME turn.
