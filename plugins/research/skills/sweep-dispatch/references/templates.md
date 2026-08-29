# Launch script templates

Placeholders in `<...>`. All paths relative to the project root; scripts assume they are
invoked from the project root on the target rig. Layout and vocabulary (wave, lane, GPU set):
`../../research-project-init/references/conventions.md`.

## wave_<rig>_gpu<ids>.sh — one per (run, wave), self-contained and self-guarded

Lives at `scripts/<NNN_exp>/<run_id_flat>/wave_<wave_id>/wave_<rig>_gpu<ids>.sh`. This is the
**only** script kind — it replaced the old `run.sh` + `launch_<rig>.sh` pair. The artifact guard
that used to sit in the per-rig launcher now travels with each run, which is what keeps lane
dispatch idempotent.

```bash
#!/usr/bin/env bash
# run: <flat run_id>   experiment: <NNN_exp>
# wave: <wave_id>   rig: <rig>   gpu: <ids>
set -uo pipefail
cd "$(dirname "$0")/../../../.." || exit 1  # → project root; adjust for sub-experiments

RUN_ID_FLAT="<flat run_id>"
RUN_ID_PATH_LAYOUT="<nested | collapsed-v1, copied from the experiment record>"
CHECKPOINT_DIR="<exact checkpoint directory resolved by canonical run_id_path>"
EVAL_DIR="<exact evaluation directory resolved by canonical run_id_path>"
STATUS_PATH="$EVAL_DIR/.status.json"
LOG_DIR="logs/<NNN_exp>/$RUN_ID_FLAT/wave_<wave_id>"     # logs/ mirrors scripts/
ARTIFACT="<exact expected final artifact path under CHECKPOINT_DIR or EVAL_DIR>"
export WAVE_ID="<wave_id>"

# self-guard (idempotency): the expected final artifact is the only completion signal
if [ -e "$ARTIFACT" ]; then
  echo "[skip] $RUN_ID_FLAT already done (artifact present)"; exit 0
fi
if grep -q '"state": "done"' "$STATUS_PATH" 2>/dev/null; then
  echo "[warn] status says done but expected artifact is missing; re-executing $RUN_ID_FLAT" >&2
fi

# exact Git revision guard: source drift blocks this lane with reserved exit 86
SOURCE_TAG="wave--$WAVE_ID"
SOURCE_REVISION=$(git rev-parse "refs/tags/$SOURCE_TAG^{commit}" 2>/dev/null) || {
  echo "[source-drift] missing $SOURCE_TAG" >&2; exit 86;
}
ACTUAL_REVISION=$(git rev-parse HEAD 2>/dev/null) || {
  echo "[source-drift] project is not a Git working tree" >&2; exit 86;
}
if [ "$ACTUAL_REVISION" != "$SOURCE_REVISION" ]; then
  echo "[source-drift] HEAD=$ACTUAL_REVISION expected=$SOURCE_REVISION ($SOURCE_TAG)" >&2
  exit 86
fi
SOURCE_PATHS=(code config scripts .python-version pyproject.toml uv.toml uv.lock sync.toml poetry.lock setup.cfg setup.py Pipfile Pipfile.lock requirements*.txt environment*.yml environment*.yaml Dockerfile*)
if ! git diff --quiet -- "${SOURCE_PATHS[@]}" || \
   ! git diff --cached --quiet -- "${SOURCE_PATHS[@]}" || \
   [ -n "$(git ls-files --others --exclude-standard -- "${SOURCE_PATHS[@]}")" ]; then
  echo "[source-drift] execution files differ from $SOURCE_REVISION" >&2
  exit 86
fi
export SOURCE_REVISION SOURCE_TAG

export CUDA_VISIBLE_DEVICES="<ids>"

# ---- behemoth lanes ONLY: shared machine, only GPU 0 is ours. Omit this block on every other rig.
# GPU auth: <"default (gpu0 only)" | "user-granted <YYYY-MM-DD> for wave <wave_id>: gpu <ids>">
BEHEMOTH_AUTHORIZED_GPUS="<0 | the granted set>"
for d in ${CUDA_VISIBLE_DEVICES//,/ }; do
  case ",$BEHEMOTH_AUTHORIZED_GPUS," in
    *",$d,"*) ;;
    *) echo "[abort] gpu $d is not ours on behemoth (authorized: $BEHEMOTH_AUTHORIZED_GPUS)" >&2; exit 1 ;;
  esac
done
# ---- end behemoth block

# storage guard: a quota'd rig blocks this lane with reserved exit 88 BEFORE any compute is spent.
# Omit only on rigs with a single volume and no quotas. A quota is not free space -- df reports the
# filesystem, not the user's allowance -- so ask quota first and fall back to df only when absent.
# Both values come from `rig-sync storage-env --machine <rig>`, which reads the machine registry.
# Never retype them: QUOTA_FS must name the filesystem backing that rig's storage_root, and a
# hand-typed one produces a headroom number for a disk nothing is being written to. MIN_FREE_KIB
# may be RAISED above the printed floor to cover this wave's checkpoint footprint -- a run writing
# 350 MB every 30 s eats headroom far faster than the rig-level number suggests -- but never
# lowered below it, or this guard passes work that `rig-sync doctor` would have stopped.
MIN_FREE_KIB=<MIN_FREE_KIB from storage-env, raised to this wave's checkpoint footprint>
QUOTA_FS="<QUOTA_FS from storage-env; empty means the rig has no quota and df is used>"
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
ENVIRONMENT_DIR="<environment.name from sync.toml>"
EXPECTED_ENVIRONMENT_FINGERPRINT="<64-char fingerprint verified on every assigned rig>"
ACTUAL_ENVIRONMENT_FINGERPRINT=$(
  "$ENVIRONMENT_DIR/bin/python" code/common/environment.py fingerprint --lock uv.lock 2>/dev/null
) || {
  echo "[environment-drift] cannot fingerprint $ENVIRONMENT_DIR" >&2; exit 87;
}
if [ "$ACTUAL_ENVIRONMENT_FINGERPRINT" != "$EXPECTED_ENVIRONMENT_FINGERPRINT" ]; then
  echo "[environment-drift] actual=$ACTUAL_ENVIRONMENT_FINGERPRINT expected=$EXPECTED_ENVIRONMENT_FINGERPRINT" >&2
  exit 87
fi
ENVIRONMENT_SMOKE=(<shell-quoted gpu_smoke tokens after the leading python>)
if ! "$ENVIRONMENT_DIR/bin/python" "${ENVIRONMENT_SMOKE[@]}"; then
  echo "[environment-drift] GPU compatibility smoke failed on $CUDA_VISIBLE_DEVICES" >&2
  exit 87
fi
export ENVIRONMENT_FINGERPRINT="$ACTUAL_ENVIRONMENT_FINGERPRINT"

mkdir -p "$EVAL_DIR" "$LOG_DIR" || exit 1

# full terminal capture: everything the run writes to stdout/stderr lands in the run log
HYDRA_ARGS=(<tokens produced by hydra_override_arg; one per override>)
"$ENVIRONMENT_DIR/bin/python" code/<NNN_exp>/<script>.py "${HYDRA_ARGS[@]}" 2>&1 \
  | tee "$LOG_DIR/wave_<rig>_gpu<ids>-$(date +%Y%m%d-%H%M%S).log"
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
- Read the experiment's literal `RUN_ID_PATH_LAYOUT` (`nested` or `collapsed-v1`). Produce the
  checkpoint and evaluation suffix once with canonical
  `run_id_path(cfg, params, layout=RUN_ID_PATH_LAYOUT)`, and materialize the resulting exact
  `CHECKPOINT_DIR`, `EVAL_DIR`, `STATUS_PATH`, and `ARTIFACT` in the generated script. The
  checkpoint and evaluation directories must use the same selected layout. Never hand-compose a
  path, infer slash depth, or reconstruct a path from `RUN_ID_FLAT`. Plot outputs are the
  exception: they are not run-scoped, carry no run_id segments, and never go through the helper.
- Produce `<flat run_id>` with canonical `run_id_flat` at generation time. Scripts, logs, and
  wandb names remain flat and unchanged for both path layouts.
- The artifact guard runs **before** anything else, so re-issuing a lane's dispatch command after
  a crash re-executes only runs whose declared completion artifact is absent — resuming or
  restarting per the experiment's design-time resume decision. `.status.json` is inspected only
  to emit a useful inconsistency warning; it never overrides the golden artifact signal.
- The Git guard resolves `wave--<wave_id>`, requires it and `HEAD` to name the same commit, and
  rejects tracked/staged/non-ignored-untracked execution drift before any status/log write.
  Reserved exit `86` means deployment drift, not experiment failure; the lane loop stops on it.
- `CUDA_VISIBLE_DEVICES`, `WAVE_ID`, `SOURCE_REVISION`, `SOURCE_TAG`, and
  `ENVIRONMENT_FINGERPRINT` are exported here and read
  by StatusWriter. They are env vars, **not** config params, so they never enter the
  `guard_run_config` snapshot — otherwise relaunch placement/provenance would trip the collision
  guard.
- The environment guard reads `ENVIRONMENT_DIR` from `[environment].name`, uses its `bin/python`
  with the tracked fingerprint helper, and never invokes `uv sync`. Generate `ENVIRONMENT_SMOKE`
  from `sync.toml`'s argv after removing its leading `python`. Exit `87` means runtime drift or GPU
  incompatibility, not an experiment failure, and stops the lane.
- The **storage guard** is emitted on quota'd rigs only, and sits *after* the artifact guard (a
  finished run must still skip cleanly) and *before* the environment guard, so a lane that cannot
  possibly finish is stopped before it spends GPU time. Reserved exit `88` means insufficient
  storage, not an experiment failure, and stops the lane. Size `MIN_FREE_KIB` from **this wave's**
  footprint — checkpoint size × retained checkpoints × runs sharing the volume — not from a fixed
  per-rig number; a lane writing a 350 MB checkpoint every 30 s exhausts headroom far faster than
  a static floor suggests. Set `QUOTA_FS` to the filesystem the lane actually writes to; leave it
  empty only where no quota applies. Running out of space mid-run is worse than a clean abort,
  because a partial `.pt` on disk looks like a real checkpoint.
- The **behemoth GPU guard** is emitted on `behemoth` lanes only — the other rigs are ours
  outright and get no such block. It sits *after* the artifact guard (a finished run must still skip
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

This run: `model=mlp,lr=1e-3,seed=0` → rig-4090, gpu 0.

Run path layout: `nested`.

Checkpoint directory: `checkpoints/000_grokking/model=mlp/lr=1e-3/seed=0`.
Evaluation directory: `evaluations/000_grokking/model=mlp/lr=1e-3/seed=0`.
Status path: `evaluations/000_grokking/model=mlp/lr=1e-3/seed=0/.status.json`.
Expected final artifact: `evaluations/000_grokking/model=mlp/lr=1e-3/seed=0/result.json`.

Full wave: 4 runs — 3 on rig-4090 (gpu 0), 1 on behemoth (gpu 0).
```

The four run-path fields above are concrete launch records, not templates. Generate all of them
from the same selected `RUN_ID_PATH_LAYOUT`; for `collapsed-v1` their concrete values will differ.
Monitoring and recovery consume these recorded paths verbatim and never infer a layout from slash
depth. `RUN_ID_PARAMS` may change only while no checkpoint or evaluation and no wave
README or script exists. After the first such surface, any identity-schema change under `nested` or
`collapsed-v1` requires a new numbered sub-experiment; never backfill, rename, move, or rewrite the
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

## Dispatch — one tmux session per lane (executed by each rig's subagent)

A **lane** is one GPU set on one rig. There is no launcher file: the lane's queue is the glob of
its wave scripts, walked sequentially by a shell loop. Lanes on the same rig run in parallel.

Every `<repo_path on rig>` below is the output of `rig-sync repo-path --machine <rig>`, which
reads the project's `sync.toml`. Resolve it once per rig and substitute it; never type a project
path into a dispatch, monitor, or recovery command. A path spelled by hand is a second copy of a
declared fact, and the copy is what goes stale when a rig's checkout moves.

```bash
ssh -o BatchMode=yes -o ConnectTimeout=10 <rig> "tmux new-session -d -s <project>_<NNN_exp>_<wave_id>_<rig>_gpu<ids> 'cd <repo_path on rig> && for s in scripts/<NNN_exp>/*/wave_<wave_id>/wave_<rig>_gpu<ids>.sh; do bash \"\$s\"; rc=\$?; { [ \"\$rc\" -eq 86 ] || [ \"\$rc\" -eq 87 ] || [ \"\$rc\" -eq 88 ]; } && exit \"\$rc\"; done'"
```

When `<rig>` is the current local hub, the hub subagent runs the inner command directly instead
of self-SSH:

```bash
tmux new-session -d -s <project>_<NNN_exp>_<wave_id>_rig-4090_gpu<ids> \
  'cd <repo_path on rig-4090> && for s in scripts/<NNN_exp>/*/wave_<wave_id>/wave_rig-4090_gpu<ids>.sh; do bash "$s"; rc=$?; { [ "$rc" -eq 86 ] || [ "$rc" -eq 87 ] || [ "$rc" -eq 88 ]; } && exit "$rc"; done'
```

- Glob expansion sorts lexicographically ⇒ deterministic ordering.
- The glob **is** the assignment record: a run is in this lane precisely because
  `wave_<rig>_gpu<ids>.sh` exists in its wave folder. Nothing to keep in sync.
- Every launch and recovery loop stops on the complete reserved exit set `{86, 87, 88}`. These
  mean source drift, environment drift, and insufficient storage respectively; none is an
  ordinary run failure that may fall through to the next queued script.
- Because every wave script self-guards, re-issuing this exact command is the entire recovery
  procedure.

Before dispatching: use `rig-sync deploy-revision` then `verify-revision` for the approved wave
commit across the complete assigned rig set; run approved `environment-sync provision` as
needed, then verify the full set and every lane. Each rig subagent repeats both read-only gates
for its target immediately before tmux. Then remind
the user how to watch: for a peer, `ssh <rig>` →
`tmux attach -t <project>_<NNN_exp>_<wave_id>_<rig>_gpu<ids>`; for the current local hub, run the
`tmux attach` command directly.

Monitoring one run via the three signals (artifacts / `.status.json` / latest run log):

```bash
ssh -o BatchMode=yes -o ConnectTimeout=10 <rig> "test -e <repo_path on rig>/<exact expected final artifact from wave record> && echo ARTIFACT=present || echo ARTIFACT=absent; cat <repo_path on rig>/<exact status path from wave record>; tail -n 30 \$(ls -t <repo_path on rig>/logs/<NNN_exp>/<run_id_flat>/wave_<wave_id>/wave_<rig>_gpu<ids>-*.log | head -1)"
```

For the current local hub, run the quoted `cat ...; tail ...` portion directly from the project
root; do not self-SSH.

## Machine-fault check & recovery (rig crash/reboot — see SKILL.md state machine)

One round-trip to diagnose a rig that stopped answering or whose heartbeat froze — liveness of
every lane on that rig + boot time + run status in a single ssh:

```bash
ssh -o BatchMode=yes -o ConnectTimeout=10 <rig> "tmux ls 2>/dev/null | grep <wave_id> || echo SESSIONS=gone; echo BOOT=\$(uptime -s); test -e <repo_path on rig>/<exact expected final artifact from wave record> && echo ARTIFACT=present || echo ARTIFACT=absent; cat <repo_path on rig>/<exact status path from wave record>"
```

For the current local hub, run the quoted diagnostic portion directly. The same boot-time,
session, heartbeat, and artifact rules apply.

Interpretation: `BOOT` newer than the dispatch time ⇒ the rig rebooted (tmux never survives a
reboot). A lane's session missing with its `.status.json` stuck at `running` ⇒ interrupted runs
(machine fault), not failed-by-code. Session alive with the heartbeat advancing ⇒ network blip
only — do nothing.

Recovery — **per lane**, guarded against double-launch (the `||` makes it a no-op if that lane's
session already exists); the self-guarded wave scripts then skip done runs and re-execute
interrupted ones:

```bash
ssh -o BatchMode=yes -o ConnectTimeout=10 <rig> "tmux has-session -t <project>_<NNN_exp>_<wave_id>_<rig>_gpu<ids> 2>/dev/null || tmux new-session -d -s <project>_<NNN_exp>_<wave_id>_<rig>_gpu<ids> 'cd <repo_path on rig> && for s in scripts/<NNN_exp>/*/wave_<wave_id>/wave_<rig>_gpu<ids>.sh; do bash \"\$s\"; rc=\$?; { [ \"\$rc\" -eq 86 ] || [ \"\$rc\" -eq 87 ] || [ \"\$rc\" -eq 88 ]; } && exit \"\$rc\"; done'"
```

For a local hub lane, run the same `tmux has-session ... || tmux new-session ...` command directly
from the hub subagent; do not wrap it in SSH.

Note it is the **same wave id** — a relaunch is not a new dispatch, so paths and session names
are unchanged.

## Assignment math

Objective: **minimize wave completion time** — the wave ends when its slowest lane ends, so lanes
should be predicted to finish together. Equal run counts do not achieve this on a heterogeneous
fleet.

Procedure, run once before generation:

1. **Capacity.** For each authorized lane, `capacity = weight × free GPUs in that lane`. Weights
   and ownership limits: `../../research-project-init/references/conventions.md` § Rig fleet. Ask
   which rigs and GPUs are free; never infer it from `nvidia-smi`.
2. **Cost.** Estimate every run's cost in rig-independent units with the `experiments-tracking`
   hierarchy (`cost = elapsed_s × weight`, preferring same-rig history). A run's predicted runtime
   in a lane is `cost / capacity(lane)`. With no history at all, every run costs the same — say so,
   and treat the resulting split as provisional.
3. **Assign.** Sort runs by cost descending; place each into the lane with the smallest running
   predicted finish. Longest-first matters: placing the biggest runs last is what strands one
   oversized job on an already-full lane.
4. **Check.** Compute each lane's predicted finish and the spread across lanes. Spread above 20% of
   the wave ETA is rebalanced, or explained as indivisible (fewer runs than weighted capacity, one
   dominant run, or a run pinned to a rig by VRAM). Report both figures in the gate-3 preview.

Worked example — 12 equal-cost runs, one hour each on `rig-4090`, across `rig-4090` gpu0 (1.0),
`rig-3090-ti` gpu0 (0.5), and `behemoth` gpu0 (2.0). Total capacity 3.5. Proportional shares are
3.43 / 1.71 / 6.86, so the greedy pass lands 3 / 2 / 7, predicting 3.0 h / 4.0 h / 3.5 h — a 4.0 h
wave. An even 4 / 4 / 4 split predicts 4.0 h / 8.0 h / 2.0 h: a 8.0 h wave, twice as long, with
behemoth idle for six of them. The residual 1.0 h spread in the balanced case is indivisibility at
one-hour granularity, not a balancing failure.

Do not duplicate rig names, weights, or authorization rules into a generated artifact or another
policy file; they are declared once in `conventions.md`.
