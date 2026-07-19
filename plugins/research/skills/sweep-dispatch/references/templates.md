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
echo running > "$EVAL_DIR/.status"

python code/<NNN_exp>/<script>.py <param>=<value> <param>=<value> ... 2>&1 | tee "$LOG_DIR/run-$(date +%Y%m%d-%H%M%S).log"
rc=$?

if [ $rc -eq 0 ]; then echo done > "$EVAL_DIR/.status"; else echo failed > "$EVAL_DIR/.status"; fi
exit $rc
```

Notes:
- The overrides list is the FULL run_id (and any non-default fixed params) — explicit, so the
  script is meaningful standalone.
- `<run_id path>` / `<flat run_id>` must be produced with `code/common/run_id.py` at generation
  time — never hand-composed.
- The `tee` into `logs/<NNN_exp>/<run_id path>/run-<timestamp>.log` is mandatory (conventions):
  timestamped per launch, history kept, never overwritten. `pipefail` keeps python's exit code
  authoritative despite the pipe, so `rc` reflects python, not `tee`.
- Hydra projects must have job file logging disabled (`- override hydra/job_logging: none`) or
  pointed inside `logs/` — no `.log` may land in the project root.

## launch_<rig>.sh — per-rig sequential slice

```bash
#!/usr/bin/env bash
# rig: <rig>   experiment: <NNN_exp>   sweep slice: <k> runs
set -uo pipefail
cd "$(dirname "$0")/../.."   # → project root

bash "scripts/<NNN_exp>/<flat run_id 1>/run.sh"
bash "scripts/<NNN_exp>/<flat run_id 2>/run.sh"
# ... sequential, one run at a time
```

## Dispatch (from rig-4090)

```bash
# one tmux session per rig, named after the experiment
ssh <rig> "tmux new-session -d -s <NNN_exp> 'cd <project path on rig> && bash scripts/<NNN_exp>/launch_<rig>.sh'"
```

Before dispatching: make sure the generated scripts have reached the rig (rig-sync), and remind
the user: watch with `ssh <rig>` → `tmux attach -t <NNN_exp>`.

## Assignment math

`weights = {server-pro-6000-bw: 2.0, rig-4090: 1.0, rig-3090ti: 0.5, rig-3080ti: 0.5}` —
restrict to the rigs the user said are free, split the run list proportionally to weights
(≈ equal wall-clock per rig), present the split, launch only after approval.
