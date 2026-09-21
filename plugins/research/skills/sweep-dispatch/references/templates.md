# Launch script templates

Placeholders in `<...>`. Artifact paths are relative to the project root, which is also the
working directory every script is invoked from on the target rig. Code, configuration, and the
wave scripts themselves are read from the wave's detached worktree `.waves/<wave_id>/` (the
wave-isolation contract in conventions); nothing is ever executed from the main checkout. Layout and vocabulary (wave, lane, GPU set):
`../../research-project-init/references/conventions.md`.

## wave.sh — one per (run, wave), self-contained, self-guarded, and placed at run time

Committed at `scripts/<NNN_exp>/<run_id folder>/wave_<wave_id>/wave.sh` and executed
as `.waves/<wave_id>/scripts/...` from the project root (the rendered
`run_id_path` directories under `segments-v1`; `<run_id_flat>` under a legacy pin). It is the only
per-run script kind. It names **no rig and no GPU**: the lane that claims it supplies `LANE_RIG`
and `LANE_GPUS` (and its rig's `MIN_FREE_KIB`/`QUOTA_FS`), which is what lets the wave's queue
hand any run to whichever lane is free first. The artifact guard travels with each run, which is
what keeps a lane relaunch idempotent.

```bash
#!/usr/bin/env bash
# run: <run_id_name>   experiment: <NNN_exp>
# run_id_flat: <flat run_id>
# wave: <wave_id>   placement: the claiming lane (LANE_RIG, LANE_GPUS)
set -uo pipefail
export WAVE_ID="<wave_id>"
# the lane loop, or sbatch --export, names the rig; a wave script is never started bare
: "${LANE_RIG:?started without LANE_RIG: run it through its lane loop or the guarded sbatch}"
LANE_GPUS="${LANE_GPUS:-}"
# project root = storage root = working directory; a Slurm job starts from its submit directory
PROJECT_ROOT="${SLURM_SUBMIT_DIR:-$PWD}"
cd "$PROJECT_ROOT" || exit 1
WAVE_TREE="$PROJECT_ROOT/.waves/$WAVE_ID"

RUN_ID_FLAT="<flat run_id>"
RUN_ID_NAME="<run_id_name: the rendered run_id under segments-v1; the flat run_id under a legacy pin>"
RUN_ID_PATH_LAYOUT="<segments-v1, or a legacy nested | collapsed-v1 | hashed-v1, copied from the experiment record>"
# RUN_ID_SEGMENTS: <segments-v1 only: the experiment's recorded spec, copied verbatim>
CHECKPOINT_DIR="<exact checkpoint directory resolved by canonical run_id_path>"
EVAL_DIR="<exact evaluation directory resolved by canonical run_id_path>"
STATUS_PATH="$EVAL_DIR/.status.json"
LOG_DIR="logs/<NNN_exp>/<run_id folder>/wave_<wave_id>"   # logs/ mirrors scripts/
ARTIFACT="<exact expected final artifact path under CHECKPOINT_DIR or EVAL_DIR>"

# self-guard (idempotency): the expected final artifact is the only completion signal
if [ -e "$ARTIFACT" ]; then
  echo "[skip] $RUN_ID_NAME already done (artifact present)"; exit 0
fi
# an offloaded artifact leaves a receipt: the hub holds the checksum-verified copy (rig-sync offload)
if [ -e "$ARTIFACT.offloaded.json" ]; then
  echo "[skip] $RUN_ID_NAME already done (artifact offloaded to the hub)"; exit 0
fi
if grep -q '"state": "done"' "$STATUS_PATH" 2>/dev/null; then
  echo "[warn] status says done but expected artifact is missing; re-executing $RUN_ID_NAME" >&2
fi

# exact Git revision guard: source drift blocks this lane with reserved exit 86
# the wave worktree must belong to this project root and sit exactly on the wave tag
WAVE_GIT_DIR=$(cd "$WAVE_TREE" 2>/dev/null && cd "$(git rev-parse --git-common-dir)" 2>/dev/null && pwd -P) || {
  echo "[source-drift] missing wave worktree $WAVE_TREE" >&2; exit 86;
}
if [ "$WAVE_GIT_DIR" != "$(cd "$PROJECT_ROOT/.git" 2>/dev/null && pwd -P)" ]; then
  echo "[source-drift] $WAVE_TREE is not a worktree of $PROJECT_ROOT" >&2; exit 86
fi
SOURCE_TAG="wave--$WAVE_ID"
SOURCE_REVISION=$(git -C "$WAVE_TREE" rev-parse "refs/tags/$SOURCE_TAG^{commit}" 2>/dev/null) || {
  echo "[source-drift] missing $SOURCE_TAG" >&2; exit 86;
}
ACTUAL_REVISION=$(git -C "$WAVE_TREE" rev-parse HEAD 2>/dev/null) || {
  echo "[source-drift] $WAVE_TREE is not a Git working tree" >&2; exit 86;
}
if [ "$ACTUAL_REVISION" != "$SOURCE_REVISION" ]; then
  echo "[source-drift] HEAD=$ACTUAL_REVISION expected=$SOURCE_REVISION ($SOURCE_TAG)" >&2
  exit 86
fi
SOURCE_PATHS=(code config scripts .python-version pyproject.toml uv.toml uv.lock sync.toml poetry.lock setup.cfg setup.py Pipfile Pipfile.lock requirements*.txt environment*.yml environment*.yaml Dockerfile*)
if ! git -C "$WAVE_TREE" diff --quiet -- "${SOURCE_PATHS[@]}" || \
   ! git -C "$WAVE_TREE" diff --cached --quiet -- "${SOURCE_PATHS[@]}" || \
   [ -n "$(git -C "$WAVE_TREE" ls-files --others --exclude-standard -- "${SOURCE_PATHS[@]}")" ]; then
  echo "[source-drift] execution files differ from $SOURCE_REVISION" >&2
  exit 86
fi
# storage resolves against the shared project root, never against the worktree
export SOURCE_REVISION SOURCE_TAG
export RESEARCH_PROJECT_ROOT="$PROJECT_ROOT"

# a Slurm allocation sets the card itself; everywhere else the lane's GPU set is the card
if [ -z "${SLURM_JOB_ID:-}" ]; then
  [ -n "$LANE_GPUS" ] || { echo "[abort] started without LANE_GPUS" >&2; exit 1; }
  export CUDA_VISIBLE_DEVICES="$LANE_GPUS"
fi

# ---- behemoth in the pool ONLY: shared machine, only GPU 0 is ours. Omit this block otherwise.
# GPU auth: <"default (gpu0 only)" | "user-granted <YYYY-MM-DD> for wave <wave_id>: gpu <ids>">
BEHEMOTH_AUTHORIZED_GPUS="<0 | the granted set>"
if [ "$LANE_RIG" = "behemoth" ]; then
  for d in ${CUDA_VISIBLE_DEVICES//,/ }; do
    case ",$BEHEMOTH_AUTHORIZED_GPUS," in
      *",$d,"*) ;;
      *) echo "[abort] gpu $d is not ours on behemoth (authorized: $BEHEMOTH_AUTHORIZED_GPUS)" >&2; exit 1 ;;
    esac
  done
fi
# ---- end behemoth block

# storage guard: a rig short on space stops this run with reserved exit 88 BEFORE any compute is
# spent. A quota is not free space -- df reports the filesystem, not the user's allowance -- so ask
# quota first and fall back to df only when absent. The rig's floor and quota filesystem are the
# lane's, not the script's: the lane loop exports MIN_FREE_KIB and QUOTA_FS from the machine
# registry (`rig-sync storage-env`), so the same script is right on whichever rig claims it. The
# run adds its own checkpoint footprint on top of the floor -- a run writing 350 MB every 30 s eats
# headroom far faster than the rig-level number suggests. On a rig with offload enabled the lane
# loop waits for the hub-ward copy to free space and retries instead of stopping.
RUN_FOOTPRINT_KIB=<this run's retained checkpoint footprint in KiB, from experiment-design>
MIN_FREE_KIB=$(( ${MIN_FREE_KIB:-0} + RUN_FOOTPRINT_KIB ))
QUOTA_FS="${QUOTA_FS:-}"
FREE_KIB=""
if [ -n "$QUOTA_FS" ] && command -v quota >/dev/null 2>&1; then
  FREE_KIB=$(quota -w 2>/dev/null | awk -v fs="$QUOTA_FS" '
    $1 == fs { used = $2; sub(/\*$/, "", used)
               lim = ($4 > 0 ? $4 : $3)          # hard limit, else soft; 0 means unlimited
               if (lim > 0) { d = lim - used; print (d > 0 ? d : 0) } }')
fi
if [ -z "$FREE_KIB" ]; then
  FREE_KIB=$(df -P . 2>/dev/null | awk 'NR == 2 { print $4 }')
fi
if [ -z "$FREE_KIB" ]; then
  echo "[storage] cannot determine free space" >&2; exit 88
fi
if [ "$FREE_KIB" -lt "$MIN_FREE_KIB" ]; then
  echo "[storage] only $((FREE_KIB / 1048576))G free, need $((MIN_FREE_KIB / 1048576))G" >&2
  exit 88
fi

# exact environment guard: runtime drift/incompatibility blocks this lane with reserved exit 87
ENVIRONMENT_DIR="<ENVIRONMENT_DIR printed by environment-sync verify: .envs/<env_key>>"
EXPECTED_ENVIRONMENT_FINGERPRINT="<64-char fingerprint verified on every assigned rig>"
ACTUAL_ENVIRONMENT_FINGERPRINT=$(
  "$ENVIRONMENT_DIR/bin/python" "$WAVE_TREE/code/common/environment.py" fingerprint --lock "$WAVE_TREE/uv.lock" 2>/dev/null
) || {
  echo "[environment-drift] cannot fingerprint $ENVIRONMENT_DIR" >&2; exit 87;
}
if [ "$ACTUAL_ENVIRONMENT_FINGERPRINT" != "$EXPECTED_ENVIRONMENT_FINGERPRINT" ]; then
  echo "[environment-drift] actual=$ACTUAL_ENVIRONMENT_FINGERPRINT expected=$EXPECTED_ENVIRONMENT_FINGERPRINT" >&2
  exit 87
fi
ENVIRONMENT_SMOKE=("$WAVE_TREE/<gpu_smoke script>" <shell-quoted remaining gpu_smoke tokens>)
if ! "$ENVIRONMENT_DIR/bin/python" "${ENVIRONMENT_SMOKE[@]}"; then
  echo "[environment-drift] GPU compatibility smoke failed on $CUDA_VISIBLE_DEVICES" >&2
  exit 87
fi
export ENVIRONMENT_FINGERPRINT="$ACTUAL_ENVIRONMENT_FINGERPRINT"

mkdir -p "$EVAL_DIR" "$LOG_DIR" || exit 1

# full terminal capture: everything the run writes to stdout/stderr lands in the run log
HYDRA_ARGS=(<tokens produced by hydra_override_arg; one per override>)
"$ENVIRONMENT_DIR/bin/python" "$WAVE_TREE/code/<NNN_exp>/<script>.py" "${HYDRA_ARGS[@]}" 2>&1 \
  | tee "$LOG_DIR/wave_${LANE_RIG}_gpu${LANE_GPUS:-job}-$(date +%Y%m%d-%H%M%S).log"
pipeline_rc=("${PIPESTATUS[@]}")
python_rc=${pipeline_rc[0]}
tee_rc=${pipeline_rc[1]}
rc=$python_rc
if [ "$tee_rc" -ne 0 ]; then
  echo "[error] tee failed with exit code $tee_rc; the required run log is incomplete" >&2
  if [ "$rc" -eq 0 ]; then rc=$tee_rc; fi
fi

# fallback: if python died before StatusWriter could finalize, mark the run failed
if [ $rc -ne 0 ] && [ ! -e "$ARTIFACT" ]; then
  "$ENVIRONMENT_DIR/bin/python" - "$STATUS_PATH" <<'EOF'
import json, os, sys, datetime, pathlib
p = pathlib.Path(sys.argv[1])
s = json.loads(p.read_text()) if p.exists() else {}
s.setdefault("schema_version", 2)
ended = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
s.update(state="failed", ended=ended, heartbeat=ended,
         wave_id=os.environ.get("WAVE_ID"), gpu=os.environ.get("CUDA_VISIBLE_DEVICES"),
         source_revision=os.environ.get("SOURCE_REVISION"),
         source_tag=os.environ.get("SOURCE_TAG"),
         environment_fingerprint=os.environ.get("ENVIRONMENT_FINGERPRINT"))
tmp = p.with_suffix(".json.tmp")
tmp.write_text(json.dumps(s))
tmp.replace(p)
EOF
fi
exit $rc
```

Notes:
- The overrides list is the FULL run_id (and any non-default fixed params) — explicit, so the
  script is meaningful standalone. Produce each array token with
  `code/common/run_id.py::hydra_override_arg`; never interpolate raw config values into shell.
- Read the experiment's literal `RUN_ID_PATH_LAYOUT` (`segments-v1` with its `RUN_ID_SEGMENTS`, or a
  legacy `nested`, `collapsed-v1`, or `hashed-v1`). After `check_run_id_plan` passed for the whole
  wave, produce the checkpoint and evaluation suffix once with canonical
  `run_id_path(cfg, params, layout=RUN_ID_PATH_LAYOUT, segments=RUN_ID_SEGMENTS)`, and materialize the resulting exact
  `CHECKPOINT_DIR`, `EVAL_DIR`, `STATUS_PATH`, and `ARTIFACT` in the generated script. The
  checkpoint and evaluation directories must use the same selected layout. Never hand-compose a
  path, infer slash depth, or reconstruct a path from `RUN_ID_FLAT`. Plot outputs are the
  exception: they are not run-scoped, carry no run_id segments, and never go through the helper.
- Produce `<flat run_id>` with canonical `run_id_flat` and `<run_id_name>` with canonical
  `run_id_name` at generation time. Under `segments-v1` the run folder in `scripts/` and `logs/` is
  the same suffix as `EVAL_DIR`, and wandb and Slurm names use `RUN_ID_NAME`; under a legacy pin
  scripts, logs, and wandb names remain flat and unchanged.
- The artifact guard runs **before** anything else, so relaunching a lane after
  a crash re-executes only runs whose declared completion artifact (or its offload receipt) is absent — resuming or
  restarting per the experiment's design-time resume decision. `.status.json` is inspected only
  to emit a useful inconsistency warning; it never overrides the golden artifact signal.
- The working directory is the project root (`PROJECT_ROOT`): every artifact path, the storage
  guard's `df -P .`, and the Slurm `--output` path resolve there, in the tree shared by all waves.
  Code is read only from `WAVE_TREE`. `RESEARCH_PROJECT_ROOT` makes the project's `paths.py`
  resolve storage and `.env` against the shared root although the imported code lives in the
  worktree. Never `cd` into `WAVE_TREE`.
- The Git guard requires `WAVE_TREE` to be a worktree of this project root, resolves
  `wave--<wave_id>`, requires it and the worktree `HEAD` to name the same commit, and rejects
  tracked/staged/non-ignored-untracked execution drift in the worktree before any status/log
  write. The main checkout is never inspected: it may sit on any commit, which is what lets waves
  of different revisions run side by side. Reserved exit `86` means deployment drift, not
  experiment failure; the lane loop stops on it.
- `LANE_RIG` and `LANE_GPUS` arrive from the lane loop (or `sbatch --export` on a cluster) and are
  the run's only placement input; the log file name carries them, so `ls logs/.../wave_<wave_id>/`
  shows where every attempt ran. `CUDA_VISIBLE_DEVICES`, `WAVE_ID`, `SOURCE_REVISION`, `SOURCE_TAG`, and
  `ENVIRONMENT_FINGERPRINT` are exported here and read
  by StatusWriter. They are env vars, **not** config params, so they never enter the
  `guard_run_config` snapshot — otherwise relaunch placement/provenance would trip the collision
  guard.
- The environment guard uses the wave's keyed environment `ENVIRONMENT_DIR` (`.envs/<env_key>`,
  exactly as `environment-sync verify` printed it for this wave — never the hub development
  environment named by `[environment].name`), runs the worktree's tracked fingerprint helper, and
  never invokes `uv sync`. Generate `ENVIRONMENT_SMOKE` from `sync.toml`'s argv after removing its
  leading `python`, with the script token prefixed by `$WAVE_TREE/`. Exit `87` means runtime drift or GPU
  incompatibility, not an experiment failure, and stops the lane.
- The **storage guard** sits *after* the artifact guard (a
  finished run must still skip cleanly) and *before* the environment guard, so a run that cannot
  possibly finish is stopped before it spends GPU time. Reserved exit `88` means insufficient
  storage, not an experiment failure. Size `RUN_FOOTPRINT_KIB` from **this run's**
  footprint — checkpoint size × retained checkpoints — on top of the rig floor the lane exports;
  a lane writing a 350 MB checkpoint every 30 s exhausts headroom far faster than
  a static floor suggests. Never type a floor or a quota filesystem into the script: they belong
  to the rig that claims the run. Running out of space mid-run is worse than a clean abort,
  because a partial `.pt` on disk looks like a real checkpoint. Low disk never removes a rig from
  the pool: `rig-sync offload` keeps freeing it, and its lane waits on `88` instead of stopping.
- The **behemoth GPU guard** is emitted when `behemoth` is in the wave's pool and acts only when
  `LANE_RIG` is `behemoth` — the other rigs are ours outright. It sits *after* the artifact guard (a finished run must still skip
  cleanly, not abort) and *before* python. `BEHEMOTH_AUTHORIZED_GPUS` is `0` by default and
  widened only by a user grant for that wave, which the `# GPU auth:` line records verbatim —
  so the wave folder is the audit trail of why a run was allowed on a given card. The guard
  replays the grant that existed at generation time: it catches drift *after* generation
  (recovery relaunch, hand-edits, a copied script) but cannot validate the grant itself. The
  pre-launch approval gate (`sweep-dispatch`, gate 2) remains the primary control.
- The `tee` target is the **mirror of this script's own path under `logs/`**, with a timestamp
  suffix (conventions): history is kept, logs are never overwritten, and a crash-recovery
  relaunch inside the same wave never clobbers the earlier attempt. Capture both `PIPESTATUS`
  entries immediately: a Python failure remains authoritative, while a `tee` failure makes an
  otherwise successful pipeline fail because a complete run log is required.
- Hydra projects must have job file logging disabled (`- override hydra/job_logging: none`) or
  pointed inside `logs/` — no `.log` may land in the project root.
- `.status.json` is owned by the python script (the canonical StatusWriter linked below); the
  wave script only writes the `failed` fallback, atomically, when the process died before python
  could finalize it.

### Leonardo variant

When `leonardo` is in the pool the same script carries an `#SBATCH` header (comments to bash on a
rig) and leaves `CUDA_VISIBLE_DEVICES` to Slurm; `--time` and `LANE_RIG` are given on the `sbatch`
command line by the supervisor, so one file serves a lane and a job alike. The header, the `sbatch` submit command guarded against double submission,
the `squeue`/`sacct` monitor, and the resubmit rule are in [cineca-slurm.md](cineca-slurm.md);
nothing else in this file changes for a job.

## README.md — one per (run, wave), written once at launch

Lives beside the wave script. It records **what was launched and why** — a launch-time statement
of intent. It is never updated afterwards: no results, no evaluations, no conclusions. Live
state belongs in EXPERIMENTS.md, which is reconciled from disk; if results leaked in here, a
wave would accumulate N stale copies of them.

```markdown
# wave 20260731-162043 — <one-line purpose>

Dispatched 2026-07-31 16:20 from rig-4090.

Source tag: `wave--20260731-162043` (the annotated tag and configured branch must resolve to the
same commit on every assigned rig).

Environment fingerprint: `<64-char sha256>` (verified with the project GPU smoke on every
assigned lane before launch).

Why this wave: <the reason these runs are being launched now — first probe of the grid, scale-up
after a promising probe, re-launch of what failed in <earlier wave id>, re-run after a code fix,
moving work to a freer rig, ...>

This run: `model=mlp/optim_params=b4c2e09f6dbe6b4b,seed=0` → rig-4090, gpu 0.
Full run_id: `model=mlp,lr=0.001,wd=0.01,beta1=0.9,seed=0`.

Run path layout: `segments-v1`, segments `[("model",), ({"optim_params": ("lr", "wd", "beta1")}, "seed")]`.

Checkpoint directory: `checkpoints/000_grokking/model=mlp/optim_params=b4c2e09f6dbe6b4b,seed=0`.
Evaluation directory: `evaluations/000_grokking/model=mlp/optim_params=b4c2e09f6dbe6b4b,seed=0`.
Status path: `evaluations/000_grokking/model=mlp/optim_params=b4c2e09f6dbe6b4b,seed=0/.status.json`.
Expected final artifact: `evaluations/000_grokking/model=mlp/optim_params=b4c2e09f6dbe6b4b,seed=0/result.json`.

Full wave: 4 runs — 3 on rig-4090 (gpu 0), 1 on behemoth (gpu 0).
```

The four run-path fields above are concrete launch records, not templates. Generate all of them
from the same selected `RUN_ID_PATH_LAYOUT` and `RUN_ID_SEGMENTS`; for legacy `nested`, `collapsed-v1`, and `hashed-v1` pins their concrete values differ.
Monitoring and recovery consume these recorded paths verbatim and never infer a layout from slash
depth. `RUN_ID_PARAMS` may change only while no checkpoint or evaluation and no wave
README or script exists. After the first such surface, any identity-schema or segment change under
`segments-v1`, `nested`, `collapsed-v1`, or `hashed-v1` requires a new numbered sub-experiment; never backfill, rename, move, or rewrite the
established tree. The experiment's layout is likewise immutable after that boundary. A mismatch or
mixed tree blocks the wave and routes changed work to a new numbered sub-experiment; never move,
rename, or rewrite the old README, script, log, or artifact paths.

When the user has granted extra cards on `behemoth` for this wave, the README says so, in the
same words as the script header — e.g. `Full wave: 6 runs — 3 on rig-4090 (gpu 0), 3 on
behemoth (gpu 0, gpu 3; gpu 3 user-authorized 2026-08-02 for this wave only).`

## StatusWriter — the python side of the signaling system

Use the single canonical `code/common/status.py` implementation in
[research-project-init templates](../../research-project-init/references/templates.md#codecommonstatuspy).
It writes schema-v2 numeric progress, timezone-aware timestamps, live elapsed time, and an
automatic 60-second heartbeat. Experiment code calls
`heartbeat(completed=<done>, total=<total>, unit=<label>)`; monitors compute ETA from those facts
and never persist ETA in `.status.json`. Do not copy a second implementation into this skill.

## lane.sh — one per wave, the loop every lane runs

Committed once per wave at `scripts/<NNN_exp>/_lanes/wave_<wave_id>/lane.sh`. A **lane** is one GPU
set on one rig; its tmux session runs this loop. The lane has a shallow local buffer under
`.waves/_state/<wave_id>/lanes/gpu<ids>/`: the hub's supervisor drops entries into `queue/` (each
a file holding one wave-script path, named `<sequence>__<token>` so plain sorting is the order),
and the loop claims the first one with an atomic `mv`. The supervisor retracts an unclaimed entry
with the same `mv`, so exactly one side wins and a run is never both moved and started.

```bash
#!/usr/bin/env bash
# lane loop for wave <wave_id>. Started by sweep-supervisor from the project root with
# LANE_RIG, LANE_GPUS, MIN_FREE_KIB and QUOTA_FS in the environment.
set -uo pipefail
WAVE_ID="<wave_id>"
: "${LANE_RIG:?}" "${LANE_GPUS:?}"
export LANE_RIG LANE_GPUS MIN_FREE_KIB="${MIN_FREE_KIB:-0}" QUOTA_FS="${QUOTA_FS:-}"
PROJECT_ROOT="$PWD"
STATE="$PROJECT_ROOT/.waves/_state/$WAVE_ID"
LANE="$STATE/lanes/gpu$LANE_GPUS"
IDLE_POLL_S="${LANE_IDLE_POLL_S:-15}"
STORAGE_WAIT_S="${LANE_STORAGE_WAIT_S:-120}"
STORAGE_WAITS_MAX="${LANE_STORAGE_WAITS_MAX:-30}"
mkdir -p "$LANE/queue" "$LANE/running" "$LANE/finished" || exit 1

# a relaunch after a machine fault: whatever was in flight goes back to the front of the buffer
for e in "$LANE"/running/*; do
  [ -f "$e" ] || continue
  case "$e" in *.pid) rm -f "$e" ;; *) mv "$e" "$LANE/queue/" ;; esac
done

storage_waits=0
while :; do
  entry=$(ls "$LANE/queue" 2>/dev/null | sort | head -n 1)
  if [ -z "$entry" ]; then
    [ -e "$LANE/drain" ] && exit 0      # the supervisor has nothing left for this lane
    sleep "$IDLE_POLL_S"; continue
  fi
  mv "$LANE/queue/$entry" "$LANE/running/$entry" 2>/dev/null || continue   # the supervisor retracted it first
  script=$(cat "$LANE/running/$entry")
  bash "$PROJECT_ROOT/.waves/$WAVE_ID/$script" &
  pid=$!
  echo "$pid" > "$LANE/running/$entry.pid"
  wait "$pid"; rc=$?
  rm -f "$LANE/running/$entry.pid"
  if [ "$rc" -eq 88 ] && [ -e "$STATE/offload" ] && [ "$storage_waits" -lt "$STORAGE_WAITS_MAX" ]; then
    # offload is freeing this rig: the run waits for room instead of costing the lane
    storage_waits=$((storage_waits + 1))
    mv "$LANE/running/$entry" "$LANE/queue/$entry"
    sleep "$STORAGE_WAIT_S"; continue
  fi
  storage_waits=0
  mv "$LANE/running/$entry" "$LANE/finished/$entry.rc$rc"
  # source drift, environment drift, insufficient storage: the lane is wrong, not the run
  { [ "$rc" -eq 86 ] || [ "$rc" -eq 87 ] || [ "$rc" -eq 88 ]; } && exit "$rc"
done
```

- Sequencing is a shell loop so it survives the death of any chat or agent, compaction, and
  multi-day queues. The lane `cd`s nowhere: its working directory is the project root, which
  rig-board's probe relies on.
- The loop stops on the complete reserved exit set `{86, 87, 88}`: source drift, environment
  drift, and insufficient storage are lane faults, never ordinary run failures that may fall
  through to the next entry. The supervisor requeues the innocent run elsewhere.
- `finished/<entry>.rc<rc>` is how the hub learns an outcome without parsing logs; the run's
  expected final artifact (or its offload receipt) still decides `done`.
- The `.pid` file names the wave-script process so a supervisor steal can stop exactly that run,
  after proving the pid is this user's and is this run's script, without touching the lane.
- Because every wave script self-guards and the loop returns in-flight entries to its buffer,
  starting the same session again is the entire recovery procedure. It is the **same wave id** — a
  relaunch is not a new dispatch, so paths and session names are unchanged.

The supervisor starts a lane, and restarts one after a fault, with one guarded command (the `||`
makes it a no-op when the session exists). `<repo_path on rig>` is the output of
`rig-sync repo-path --machine <rig>` and the storage values those of
`rig-sync storage-env --machine <rig>`; never
type either by hand. When `<rig>` is the current local hub the same command runs without SSH:

```bash
ssh -o BatchMode=yes -o ConnectTimeout=10 <rig> "tmux has-session -t <project>_<NNN_exp>_<wave_id>_<rig>_gpu<ids> 2>/dev/null || tmux new-session -d -s <project>_<NNN_exp>_<wave_id>_<rig>_gpu<ids> 'cd <repo_path on rig> && env LANE_RIG=<rig> LANE_GPUS=<ids> MIN_FREE_KIB=<MIN_FREE_KIB> QUOTA_FS=<QUOTA_FS> bash .waves/<wave_id>/scripts/<NNN_exp>/_lanes/wave_<wave_id>/lane.sh'"
```

Before any lane starts: `rig-sync deploy-revision` then `verify-revision` for the approved wave
commit across **every rig in the pool** plus the hub (this creates `.waves/<wave_id>` on each);
approved `environment-sync provision --wave <wave_id> --revision <sha>` as needed, then
`environment-sync verify --wave <wave_id> --revision <sha>` for the full set and every lane. The
supervisor repeats both read-only gates for a lane immediately before it starts or restarts it.
Remind the user how to watch: for a peer, `ssh <rig>` →
`tmux attach -t <project>_<NNN_exp>_<wave_id>_<rig>_gpu<ids>`; for the current local hub, run the
`tmux attach` command directly.

## Supervisor manifest — what `sweep-supervisor register` reads

Written by dispatch to the scratch area (never under `scripts/`: it is launch input, not an
execution file), one JSON object:

```json
{
  "wave_id": "<wave_id>",
  "experiment": "<NNN_exp>",
  "revision": "<40-char sha of the wave tag>",
  "lane_script": "scripts/<NNN_exp>/_lanes/wave_<wave_id>/lane.sh",
  "pool": {
    "rig-4090": {"lanes": ["0"]},
    "behemoth": {"lanes": ["0", "5"]},
    "leonardo": {"max_jobs": 8, "core_hours_cap": 400, "default_walltime_s": 7200}
  },
  "weights": {"rig-4090": 1.0, "behemoth": 2.0, "leonardo": 1.0},
  "offload": ["behemoth"],
  "runs": [
    {
      "id": "<run_id_name>",
      "script": "scripts/<NNN_exp>/<run_id folder>/wave_<wave_id>/wave.sh",
      "status_path": "<exact status path>",
      "artifact": "<exact expected final artifact>",
      "checkpoint_dir": "<exact checkpoint directory>",
      "eval_dir": "<exact evaluation directory>",
      "resumes": true,
      "cost_s": 3600,
      "min_vram_mib": 20000,
      "needs": ["hf:imagenet-1k"]
    }
  ]
}
```

`runs` order is the queue order (longest first, see below). `cost_s` is the run's estimate in
weight-1.0 seconds from the `experiments-tracking` hierarchy, omitted when there is no basis.
`min_vram_mib` and `needs` come from the experiment's design record. A `behemoth` lane beyond
gpu0 appears only under a user grant for this wave. Weights are copied from conventions § Rig
fleet at generation time and are only the prior: the supervisor replaces them with rates measured
in this wave as soon as runs finish.

## Pool and initial estimate

There is no static assignment. The wave has one ordered queue and a **pool** of lanes; each lane
takes the next run it is eligible for when it has room, so a fast card simply takes more runs and
a wrong estimate corrects itself once real runtimes arrive. What dispatch decides is the pool, the
queue order, and the estimate it shows the user.

1. **Pool.** Every authorized lane: rig and GPU set, from the fleet board and the envelope. Ask
   which rigs and GPUs are free; never infer it from `nvidia-smi`. Low disk never excludes a rig:
   list it under `offload` instead. `leonardo` joins with a job cap and a core-hour cap.
2. **Cost.** Estimate every run's cost in rig-independent units with the `experiments-tracking`
   hierarchy (`cost = elapsed_s × weight`, preferring same-rig history). With no history at all,
   omit `cost_s` — say so, and treat the first estimate as provisional.
3. **Order.** Sort runs by cost descending. Longest-first matters: a long run claimed last is what
   leaves one lane working alone at the end of the wave.
4. **Estimate.** Simulate the feeder (each run to the eligible lane predicted free first, runtime
   `cost / weight`) and report the predicted wave completion and each lane's share in the gate-3
   preview. A run pinned by VRAM or data to one rig is named as such.

Worked example — 12 equal-cost runs, one hour each on `rig-4090`, across `rig-4090` gpu0 (1.0),
`rig-3090-ti` gpu0 (0.5), and `behemoth` gpu0 (2.0). The feeder lands 3 / 2 / 7 and the wave takes
about 4.0 h; an even 4 / 4 / 4 split would take 8.0 h with behemoth idle for six of them. If
`rig-3090-ti` turns out slower than its weight, it simply claims fewer runs — nobody re-plans.

Do not duplicate rig names, weights, or authorization rules into a generated artifact or another
policy file; they are declared once in `conventions.md`.
