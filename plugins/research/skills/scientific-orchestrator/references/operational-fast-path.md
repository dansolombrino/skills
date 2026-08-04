# Operational Fast Path

This reference is the sole source of truth for classifying failed runs and
recovering from operational failures without unnecessary scientific bureaucracy.
Read it immediately after any failed run, before requesting an audit, reopening a
plan, or launching diagnostic retries.

## Classify the First Causal Failure

Assign every failed run to exactly one tier:

- `O0 startup failure`: execution failed before model construction, data
  iteration, optimizer creation, or scientific metric computation. Examples:
  missing executable, broken `PATH` or `PYTHONPATH`, import error, malformed
  launcher, missing environment variable, or unresolved configuration.
- `O1 runtime smoke failure`: bounded execution started, but the failure is in an
  operational mechanism such as device placement, distributed startup,
  checkpoint I/O, logging, artifact finalization, scheduler integration, or CUDA
  accounting rather than in the scientific method or decision metric.
- `S1 scientific failure`: the failure concerns the model, loss, data semantics,
  metric, causal or evaluation boundary, or preregistered decision criterion.

Classify by the earliest causal failure, not the last stack frame. If an
operational issue may have corrupted evidence, or its fix changes the scientific
method or measurement path, stop treating it as routine and replan it.

## Apply the Proportionate Response

- `O0`: routine fix-forward. Preserve the failed run and append one concise
  operational event. Do not create a scientific audit report, run the full test
  suite, reopen the plan, or request a user checkpoint.
- `O1`: verify only the affected runtime contract; replan if already-produced
  evidence may be invalid or the fix changes a scientific boundary.
- `S1`: replan and verify the affected scientific boundary. Audit only if prior
  evidence may already be invalid or at the next claim-promotion gate.

## Recover O0 and Routine O1 Failures

1. Inspect the preserved run and logs before launching another diagnostic run.
2. Reproduce the exact failure once with the intended launcher, environment
   variables, paths, and hardware class. On CINECA or Paradigma, use the required
   interactive GPU allocation; login-node reproduction is not equivalent.
3. Implement the smallest fix. Do not rebuild the environment, rewrite launchers,
   or change dependencies speculatively.
4. Run syntax, static, import, or unit checks targeted to the failure. Run
   `uv sync` only when project metadata or the lockfile is relevant. Never run the
   full suite for `O0`; for routine `O1`, run it only when the fix has broad impact.
5. Run one bounded end-to-end preflight that reaches the affected runtime boundary
   under compute-node-equivalent conditions.
6. Retry the intended work under the same elected run ID when the scientific
   configuration is unchanged. Record a new wave or attempt identifier and use a
   fresh attempt-scoped output directory where needed. If the fix changes
   behavior-affecting code, scientific configuration, or the run-identity schema,
   evolve the run ID and obtain authorization for a new wave before execution.

If the same causal failure recurs, continue the same incident instead of creating
new reports. If a different failure appears, classify it separately. Consolidate
related `O0` and `O1` attempts into one operational-failure record under
`orchestration/<phase_id>/` plus the normal append-only control events; do not add
one `program/*.md` audit per attempt. Review the accumulated operational changes
once at the next scientific promotion gate.

The consolidated record and event must name the tier, causal failure, failed run
ids, affected runtime contract, smallest fix, targeted checks, preflight outcome,
and retry run id plus wave or attempt identifier.
