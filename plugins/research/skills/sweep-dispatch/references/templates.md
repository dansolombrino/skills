# Launch script templates

Placeholders in `<...>`. All paths relative to the project root; scripts assume they are
invoked from the project root on the target rig.

## run.sh — one per run, self-contained

```bash
#!/usr/bin/env bash
# run: <flat run_id>   experiment: <NNN_exp>   assigned: <rig>
set -uo pipefail
cd "$(dirname "$0")/../../.."   # → project root (scripts/NNN_exp/<run_id>/ is 3 deep; adjust for sub-experiments)

EVAL_DIR="evaluations/<NNN_exp>/<run_id path>"
LOG_DIR="logs/<NNN_exp>/<run_id path>"
mkdir -p "$EVAL_DIR" "$LOG_DIR"

# full terminal capture: everything the run writes to stdout/stderr lands in the run log
python code/<NNN_exp>/<script>.py <param>=<value> <param>=<value> ... 2>&1 | tee "$LOG_DIR/run-$(date +%Y%m%d-%H%M%S).log"
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
- The `tee` into `logs/<NNN_exp>/<run_id path>/run-<timestamp>.log` is mandatory (conventions):
  timestamped per launch, history kept, never overwritten. `${PIPESTATUS[0]}` keeps python's
  exit code authoritative despite the pipe, so `rc` reflects python, not `tee`.
- Hydra projects must have job file logging disabled (`- override hydra/job_logging: none`) or
  pointed inside `logs/` — no `.log` may land in the project root.
- `.status.json` is owned by the python script (StatusWriter, below); `run.sh` only writes the
  `failed` fallback when the process died before python could finalize it.

## StatusWriter — the python side of the signaling system

Every training/eval script wraps its work in this pattern (stdlib-only; put it in
`code/common/status.py` and import it). It owns `.status.json` and prints start/end/elapsed to
stdout — which `run.sh`'s tee lands in the run log, and which EXPERIMENTS.md records as the
run's reference runtime.

```python
import json, time, datetime
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
                       "elapsed_s": None, "heartbeat": self._now(), "progress": None}
        self._write()
        print(f"[status] RUN START {self.status['started']}", flush=True)
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

## launch_<rig>.sh — per-rig sequential slice

Idempotent by construction: completed runs are skipped, so relaunching a slice (e.g. after a
rig crash/reboot) never redoes finished work.

```bash
#!/usr/bin/env bash
# rig: <rig>   experiment: <NNN_exp>   sweep slice: <k> runs
set -uo pipefail
cd "$(dirname "$0")/../.."   # → project root

run() {  # run <flat run_id> <run_id path> <expected final artifact>
  if [ -e "$3" ]; then echo "[skip] $1 already done (artifact present)"; return 0; fi
  if grep -q '"state": "done"' "evaluations/<NNN_exp>/$2/.status.json" 2>/dev/null; then
    echo "[skip] $1 already done (.status.json)"; return 0
  fi
  bash "scripts/<NNN_exp>/$1/run.sh"
}

run "<flat run_id 1>" "<run_id path 1>" "<expected final artifact path 1>"
run "<flat run_id 2>" "<run_id path 2>" "<expected final artifact path 2>"
# ... sequential, one run at a time
```

Notes:
- The done-guard mirrors the signal hierarchy (conventions): the **expected final artifact**
  (declared at experiment design time) is the primary check, `.status.json` `state == "done"`
  the fallback. A run interrupted mid-flight matches neither, so it re-executes on relaunch —
  resuming or restarting per its `run.sh` (design-time resume decision).
- The status fallback is a shell `grep`, deliberately not python: after a reboot the relaunch
  may run before any environment activation, and the guard must never silently degrade to
  "not done". The exact string `"state": "done"` is safe to match — StatusWriter is the file's
  single writer and always emits `json.dumps` with that formatting.

## Dispatch (from rig-4090, executed by each rig's subagent)

```bash
# one tmux session per rig, named after the experiment
ssh <rig> "tmux new-session -d -s <NNN_exp> 'cd <project path on rig> && bash scripts/<NNN_exp>/launch_<rig>.sh'"
```

Before dispatching: make sure the generated scripts have reached the rig (rig-sync), and remind
the user: watch with `ssh <rig>` → `tmux attach -t <NNN_exp>`. The subagent then monitors via
the three signals (artifacts / `.status.json` / latest run log — see SKILL.md), e.g.
`ssh <rig> "cat <project>/evaluations/<NNN_exp>/<run_id path>/.status.json; tail -n 30 \$(ls -t <project>/logs/<NNN_exp>/<run_id path>/run-*.log | head -1)"`.

## Machine-fault check & recovery (rig crash/reboot — see SKILL.md state machine)

One round-trip to diagnose a rig that stopped answering or whose heartbeat froze — session
liveness + boot time + run status in a single ssh:

```bash
ssh <rig> "tmux has-session -t <NNN_exp> 2>/dev/null && echo SESSION=alive || echo SESSION=gone; echo BOOT=\$(uptime -s); cat <project>/evaluations/<NNN_exp>/<run_id path>/.status.json"
```

Interpretation: `BOOT` newer than the dispatch time ⇒ the rig rebooted (tmux never survives a
reboot). `SESSION=gone` with `.status.json` stuck at `running` ⇒ interrupted runs (machine
fault), not failed-by-code. `SESSION=alive` with the heartbeat advancing ⇒ network blip only —
do nothing.

Recovery — relaunch guarded against double-launch (the `||` makes it a no-op if a session
already exists); the idempotent launcher then skips done runs and re-executes interrupted ones:

```bash
ssh <rig> "tmux has-session -t <NNN_exp> 2>/dev/null || tmux new-session -d -s <NNN_exp> 'cd <project path on rig> && bash scripts/<NNN_exp>/launch_<rig>.sh'"
```

## Assignment math

`weights = {server-pro-6000-bw: 2.0, rig-4090: 1.0, rig-3090ti: 0.5, rig-3080ti: 0.5}` —
restrict to the rigs the user said are free, split the run list proportionally to weights
(≈ equal wall-clock per rig), present the split, launch only after approval.
