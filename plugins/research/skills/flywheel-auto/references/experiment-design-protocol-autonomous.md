# Autonomous Experiment Design Gate

Use this gate for `flywheel-auto` runs that must continue autonomously under a
persisted control contract.

## Goal

Prevent wasted spend and ambiguous stop behavior while keeping the run
non-interactive after the control contract is coherent.

## Operating Rules

- Treat this gate as deterministic and non-interactive.
- Do not wait for user acceptance once required contract fields are coherent.
- Infer missing objective from graph state only when objective is missing and
  the user refuses to provide one; persist the inferred objective before
  execution.
- Derive missing terminal condition as `budget ceiling reached` when budget
  ceiling and budget unit are present.
- Persist every derivation in the control node `content` before compute
  request or acquisition.
- Require explicit stop recording before any termination.
- Treat newly authored plot communication as an exception to non-interactivity. Before creating or
  modifying plot-producing code, propose the exact title, visible metric meaning/direction text,
  units or relevant ranges, in-figure placement, and every complete project-relative `plots/` export
  path including its leaf filename, its arrangement across title lines, and the interaction
  affordances, then wait for explicit user acceptance before editing. Every plot is one
  self-contained interactive HTML file at `plots/<experiment_path>/<script_stem>/<leaf>.html`;
  run_id selection lives inside the file, so no plot path carries run_id segments and plot
  communication never identifies, applies, or reopens `RUN_ID_PATH_LAYOUT`. Ask how the selected
  params are arranged across title lines for this plot rather than reusing another plot's
  arrangement. Protect `plots/`, the complete experiment path, script
  stem, and leaf. Rewriting a leaf whole on rerun is expected and needs no fresh approval while the
  path is unchanged; reject any destination that collides with a different script's output. If
  runtime values prevent a concrete path before
  the edit, identify every placeholder in the complete path template. Before rendering, show every
  fully resolved concrete export path, including its leaf filename, and
  wait for explicit user approval. An approved template does not approve any concrete destination. A
  new or changed resolved path always reopens approval, even when it conforms to the approved
  template; do not render before that approval. Ground the proposal in the control contract or
  evaluation schema and stop rather than infer missing metric semantics. Persist the accepted
  specification, pinned layout, approved template, and approved matching concrete paths in
  control-node `content`. An unchanged-code rerender may reuse approval only when every resolved
  concrete export path is byte-for-byte identical to the previously explicitly approved concrete
  path.

## Required Gate Checks

The run is gate-ready only when all checks below pass.

1. Objective check
   - Objective is explicit in the control contract, or inferred and persisted.
2. Decision criterion check
   - Decision criterion is explicit and measurable enough to compare branches.
3. Budget contract check
   - Budget ceiling and budget unit are explicit.
   - If user-facing budget uses a non-credit unit, operational compute approval
     cap is derived and both values are persisted.
4. Terminal condition check
   - Terminal condition is explicit.
   - If missing while budget ceiling and unit are present, set terminal
     condition to `budget ceiling reached` and persist the derivation.
5. Continuation/stop recording check
   - Control contract records continuation rule.
   - Termination requires persisted `stop_reason` with one of:
     `budget_exhausted`, `objective_met`, `no_viable_branch`,
     `user_cancelled`, `runtime_error`.

## Failure Handling

- If any required check fails, repair the control contract from available graph
  state and previously supplied inputs.
- Ask only when a required field cannot be recovered from user instructions,
  conversation, or graph state.
- Once repaired, continue execution without adding a user-acceptance checkpoint.
- Do not apply that rule to the protected plot communication gate; the agent cannot approve its own
  proposal or derive acceptance from autonomous mode.

## Execution Handoff

When gate-ready, continue with the active `flywheel-auto` execution workflow:

- persist control node updates,
- execute or acquire compute within budget,
- refresh lookahead after each resolved node,
- stop only on explicit terminal conditions,
- persist stop reason before exit.
