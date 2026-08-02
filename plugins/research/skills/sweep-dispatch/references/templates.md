# Launch script templates

Placeholders in `<...>`. All paths relative to the project root; scripts assume they are
invoked from the project root on the target rig. Layout and vocabulary (wave, lane, GPU set):
`../../research-project-init/references/conventions.md`.

## wave_<rig>_gpu<ids>.sh — one per (run, wave), self-contained and self-guarded

Lives at `scripts/<NNN_exp>/<run_id_flat>/wave_<wave_id>/wave_<rig>_gpu<ids>.sh`. This is the
**only** script kind — it replaced the old `run.sh` + `launch_<rig>.sh` pair. The done-guard
that used to sit in the per-rig launcher now travels with each run, which is what keeps lane
dispatch idempotent.

```bash
#!/usr/bin/env bash
# run: <flat run_id>   experiment: <NNN_exp>
# wave: <wave_id>   rig: <rig>   gpu: <ids>
set -uo pipefail
cd "$(dirname "$0")/../../../.."   # → project root (scripts/NNN_exp/<run_id_flat>/wave_<id>/ is 4 deep; adjust for sub-experiments)

RUN_ID_FLAT="<flat run_id>"
EVAL_DIR="evaluations/<NNN_exp>/<run_id path>"
LOG_DIR="logs/<NNN_exp>/$RUN_ID_FLAT/wave_<wave_id>"     # logs/ mirrors scripts/
ARTIFACT="<expected final artifact path>"

# self-guard (idempotency): artifact is golden, .status.json is the fallback
if [ -e "$ARTIFACT" ]; then
  echo "[skip] $RUN_ID_FLAT already done (artifact present)"; exit 0
fi
if grep -q '"state": "done"' "$EVAL_DIR/.status.json" 2>/dev/null; then
  echo "[skip] $RUN_ID_FLAT already done (.status.json)"; exit 0
fi

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

export WAVE_ID="<wave_id>"
mkdir -p "$EVAL_DIR" "$LOG_DIR"

# full terminal capture: everything the run writes to stdout/stderr lands in the run log
python code/<NNN_exp>/<script>.py <param>=<value> <param>=<value> ... 2>&1 \
  | tee "$LOG_DIR/wave_<rig>_gpu<ids>-$(date +%Y%m%d-%H%M%S).log"
rc=${PIPESTATUS[0]}

# fallback: if python died before StatusWriter could finalize, mark the run failed
if [ $rc -ne 0 ]; then
  python - "$EVAL_DIR/.status.json" <<'EOF'
import json, sys, datetime, pathlib
p = pathlib.Path(sys.argv[1])
s = json.loads(p.read_text()) if p.exists() else {}
if s.get("state") != "done":
    s.update(state="failed", ended=datetime.datetime.now().isoformat(timespec="seconds"))
    p.write_text(json.dumps(s))
EOF
fi
exit $rc
```

Notes:
- The overrides list is the FULL run_id (and any non-default fixed params) — explicit, so the
  script is meaningful standalone.
- `<run_id path>` / `<flat run_id>` must be produced with `code/common/run_id.py` at generation
  time — never hand-composed.
- The done-guard runs **before** anything else, so re-issuing a lane's dispatch command after a
  crash re-executes only interrupted runs — resuming or restarting per the experiment's
  design-time resume decision. The status fallback is a shell `grep`, deliberately not python:
  after a reboot the relaunch may run before any environment activation, and the guard must
  never silently degrade to "not done". The exact string `"state": "done"` is safe to match —
  StatusWriter is the file's single writer and always emits `json.dumps` with that formatting.
- `CUDA_VISIBLE_DEVICES` and `WAVE_ID` are exported here and read by StatusWriter. They are env
  vars, **not** config params, so they never enter the `guard_run_config` snapshot — otherwise
  re-launching in a new wave or on a different card would trip the run_id-collision hard error.
- The **behemoth GPU guard** is emitted on `behemoth` lanes only — the other rigs are ours
  outright and get no such block. It sits *after* the done-guard (a finished run must still skip
  cleanly, not abort) and *before* python. `BEHEMOTH_AUTHORIZED_GPUS` is `0` by default and
  widened only by a user grant for that wave, which the `# GPU auth:` line records verbatim —
  so the wave folder is the audit trail of why a run was allowed on a given card. The guard
  replays the grant that existed at generation time: it catches drift *after* generation
  (recovery relaunch, hand-edits, a copied script) but cannot validate the grant itself. The
  pre-launch approval gate (`sweep-dispatch`, gate 2) remains the primary control.
- The `tee` target is the **mirror of this script's own path under `logs/`**, with a timestamp
  suffix (conventions): history is kept, logs are never overwritten, and a crash-recovery
  relaunch inside the same wave never clobbers the earlier attempt. `${PIPESTATUS[0]}` keeps
  python's exit code authoritative despite the pipe.
- Hydra projects must have job file logging disabled (`- override hydra/job_logging: none`) or
  pointed inside `logs/` — no `.log` may land in the project root.
- `.status.json` is owned by the python script (StatusWriter, below); the wave script only
  writes the `failed` fallback when the process died before python could finalize it.

## README.md — one per (run, wave), written once at launch

Lives beside the wave script. It records **what was launched and why** — a launch-time statement
of intent. It is never updated afterwards: no results, no evaluations, no conclusions. Live
state belongs in EXPERIMENTS.md, which is reconciled from disk; if results leaked in here, a
wave would accumulate N stale copies of them.

```markdown
# wave 20260731-162043 — <one-line purpose>

Dispatched 2026-07-31 16:20 from rig-4090.

Why this wave: <the reason these runs are being launched now — first probe of the grid, scale-up
after a promising probe, re-launch of what failed in <earlier wave id>, re-run after a code fix,
moving work to a freer rig, ...>

This run: `model=mlp,lr=1e-3,seed=0` → rig-4090, gpu 0.

Full wave: 4 runs — 3 on rig-4090 (gpu 0), 1 on behemoth (gpu 0).
```

When the user has granted extra cards on `behemoth` for this wave, the README says so, in the
same words as the script header — e.g. `Full wave: 6 runs — 3 on rig-4090 (gpu 0), 3 on
behemoth (gpu 0, gpu 3; gpu 3 user-authorized 2026-08-02 for this wave only).`

## StatusWriter — the python side of the signaling system

Every training/eval script wraps its work in this pattern (stdlib-only; put it in
`code/common/status.py` and import it). It owns `.status.json` and prints start/end/elapsed to
stdout — which the wave script's tee lands in the run log, and which EXPERIMENTS.md records as
the run's reference runtime.

```python
import json, os, time, datetime
from pathlib import Path


class StatusWriter:
    """Owns evaluations/NNN_exp/<run_id path>/.status.json for one run."""

    def __init__(self, eval_dir):
        self.path = Path(eval_dir) / ".status.json"
        self.t0 = None
        self.status = {}

    def _now(self):
        return datetime.datetime.now().isoformat(timespec="seconds")

    def _write(self):
        tmp = self.path.with_suffix(".json.tmp")   # atomic: monitors never see partial json
        tmp.write_text(json.dumps(self.status))
        tmp.replace(self.path)

    def __enter__(self):
        self.t0 = time.monotonic()
        self.status = {"state": "running", "started": self._now(), "ended": None,
                       "elapsed_s": None, "heartbeat": self._now(), "progress": None,
                       # exported by the wave script; identifies WHICH dispatch this execution
                       # belongs to, so reconciliation can find the right EXPERIMENTS.md row
                       "wave_id": os.environ.get("WAVE_ID"),
                       "gpu": os.environ.get("CUDA_VISIBLE_DEVICES")}
        self._write()
        print(f"[status] RUN START {self.status['started']} "
              f"wave={self.status['wave_id']} gpu={self.status['gpu']}", flush=True)
        return self

    def heartbeat(self, progress=None):
        """Call periodically (e.g. once per epoch/eval step)."""
        self.status["heartbeat"] = self._now()
        if progress is not None:
            self.status["progress"] = progress
        self._write()

    def __exit__(self, exc_type, exc, tb):
        self.status.update(state="failed" if exc_type else "done", ended=self._now(),
                           elapsed_s=round(time.monotonic() - self.t0, 1))
        self._write()
        print(f"[status] RUN END {self.status['ended']} "
              f"state={self.status['state']} elapsed={self.status['elapsed_s']}s", flush=True)
        return False   # never swallow the exception


# usage in the experiment script:
#   with StatusWriter(eval_dir) as sw:
#       for epoch in range(cfg.epochs):
#           ...train...
#           sw.heartbeat(progress=f"epoch {epoch + 1}/{cfg.epochs}")
```

`progress` is what EXPERIMENTS.md's `progress` column shows and what its `eta` column is
extrapolated from — keep it a simple `<done>/<total>` shape so it can be parsed.

## Dispatch — one tmux session per lane (executed by each rig's subagent)

A **lane** is one GPU set on one rig. There is no launcher file: the lane's queue is the glob of
its wave scripts, walked sequentially by a shell loop. Lanes on the same rig run in parallel.

```bash
ssh <rig> "tmux new-session -d -s <project>_<NNN_exp>_<wave_id>_<rig>_gpu<ids> 'cd <project path on rig> && for s in scripts/<NNN_exp>/*/wave_<wave_id>/wave_<rig>_gpu<ids>.sh; do bash \"\$s\"; done'"
```

- Glob expansion sorts lexicographically ⇒ deterministic ordering.
- The glob **is** the assignment record: a run is in this lane precisely because
  `wave_<rig>_gpu<ids>.sh` exists in its wave folder. Nothing to keep in sync.
- Because every wave script self-guards, re-issuing this exact command is the entire recovery
  procedure.

Before dispatching: make sure the generated scripts have reached the rig (rig-sync), and remind
the user how to watch: `ssh <rig>` → `tmux attach -t <project>_<NNN_exp>_<wave_id>_<rig>_gpu<ids>`.

Monitoring one run via the three signals (artifacts / `.status.json` / latest run log):

```bash
ssh <rig> "cat <project>/evaluations/<NNN_exp>/<run_id path>/.status.json; tail -n 30 \$(ls -t <project>/logs/<NNN_exp>/<run_id_flat>/wave_<wave_id>/wave_<rig>_gpu<ids>-*.log | head -1)"
```

## Machine-fault check & recovery (rig crash/reboot — see SKILL.md state machine)

One round-trip to diagnose a rig that stopped answering or whose heartbeat froze — liveness of
every lane on that rig + boot time + run status in a single ssh:

```bash
ssh <rig> "tmux ls 2>/dev/null | grep <wave_id> || echo SESSIONS=gone; echo BOOT=\$(uptime -s); cat <project>/evaluations/<NNN_exp>/<run_id path>/.status.json"
```

Interpretation: `BOOT` newer than the dispatch time ⇒ the rig rebooted (tmux never survives a
reboot). A lane's session missing with its `.status.json` stuck at `running` ⇒ interrupted runs
(machine fault), not failed-by-code. Session alive with the heartbeat advancing ⇒ network blip
only — do nothing.

Recovery — **per lane**, guarded against double-launch (the `||` makes it a no-op if that lane's
session already exists); the self-guarded wave scripts then skip done runs and re-execute
interrupted ones:

```bash
ssh <rig> "tmux has-session -t <project>_<NNN_exp>_<wave_id>_<rig>_gpu<ids> 2>/dev/null || tmux new-session -d -s <project>_<NNN_exp>_<wave_id>_<rig>_gpu<ids> 'cd <project path on rig> && for s in scripts/<NNN_exp>/*/wave_<wave_id>/wave_<rig>_gpu<ids>.sh; do bash \"\$s\"; done'"
```

Note it is the **same wave id** — a relaunch is not a new dispatch, so paths and session names
are unchanged.

## Assignment math

`per-GPU weights = {behemoth: 2.0, rig-4090: 1.0, rig-3090-ti: 0.5, rig-3080-ti: 0.5}`.
Canonical names are the ssh aliases — they are what `ssh <rig>` must resolve.

1. Ask the user **which rigs and which GPUs on them are usable** — never assume, never hardcode
   the multi-GPU server's card count. On `behemoth` **only gpu0 is ours and only gpu0 may be
   proposed**; every other card belongs to someone else, and an idle card is not an available
   card (canon: `../../research-project-init/references/conventions.md` § Rig fleet). Wider use
   needs an explicit user grant, good for that one wave only. Check load with `nvidia-smi` as
   well as ownership.
2. Capacity of a lane = the rig's per-GPU weight × the number of GPUs in that lane's set —
   `behemoth` contributes `2.0 × 1` unless a grant for this wave says otherwise.
3. Split the run list across lanes proportionally to capacity (≈ equal wall-clock per lane).
4. Within one (wave, rig) the GPU sets must be **disjoint** — a run occupying every card makes
   that rig a single lane for the wave.
5. Present the split (run → rig, gpu), launch only after approval.
