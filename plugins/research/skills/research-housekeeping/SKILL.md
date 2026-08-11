---
name: research-housekeeping
description: Maintain Research 2.0 scientific program records and local orchestration hygiene without owning experiment state or publishing results. Use when initializing, reconciling, compacting, or checking program.md, program phase reports, decision history, subagent traces, failure notes, and Flywheel logger handoffs during a nontrivial research workflow.
---

# Research Housekeeping

Create or update the local research housekeeping layer before scientific execution becomes complex. This skill keeps the repository workspace orderly; it does not publish research records.

## Housekeeping Layout

Use these roles:

- `program.md`: one-screen forward-looking execution index with the active request, current phase, the compact pipeline state shown below, subagent plan, checklist, short backlog, and links to phase files. Never turn it into a decision or progress journal.
- `program/`: the authoritative human rendering of current constraints,
  evidence dispositions, material decisions, and delegations at
  `program/00-execution-agreement.md`, expanded decision history at
  `program/decision-register.md`, and phase plans such as literature synthesis,
  assumption-breaking reports, engineering handoffs, and critical audits.
- `orchestration/`: local-only execution traces for orchestrators and subagents, plus append-only decision, progress, and deviation history under `orchestration/control/events.jsonl`. Never stage, commit, or push this directory. Add `orchestration/` to the project `.gitignore` when allowed. Correct a bad event with a later superseding entry rather than rewriting history.
- `orchestration/<phase_id>/operational-failures.md`: one consolidated record for related `O0` and `O1` attempts when operational failures occur; never create one scientific audit file per attempt.
- The standard `checkpoints/`, `evaluations/`, `logs/`, `plots/`, and `visualizations/` trees:
  execution artifacts keyed by the elected run identity. For a single-producer visualization,
  preserve the producer's complete numbered `<experiment_path>` before the script-stem and run-id
  layers. Never introduce a generic `outputs/` tree.
- `index.md`: backward-looking Flywheel mirror. Do not replace it with local narrative.
- `EXPERIMENTS.md`: sole factual run-state authority. Never edit it from this skill.
- `JOURNAL.md`: append-only narrative owned by `research-journal`; do not duplicate it here.

Keep the compact `program.md` block to: active request, current phase, agreement version,
scientific/engineering modes, scientific question, next decision, active engineering handoff,
subagent plan, short checklist/backlog, Flywheel disposition, and links to detailed records.

## Start Or Update

- Read existing `program.md`, the execution agreement and decision log under `program/`, local `orchestration/control/` state, `index.md`, `.flywheel.json`, `.env`, and repository guidance if present.
- Preserve existing run artifacts and prior phase records.
- Append material decisions to `program/decision-register.md`; correct an old decision with a
  later superseding entry rather than deleting or rewriting history.
- Preserve user-written fields in `program/00-execution-agreement.md`. Agents may
  add source-backed evidence rows, decision rows, recommended defaults, and
  refinement notes, but must not overwrite user constraints, keep decisions only
  in hidden state, or turn the agreement into a progress journal.
- Keep `program/00-execution-agreement.md` under 200 lines. Move detailed plans,
  traces, audit findings, and subagent reports into other `program/` or
  `orchestration/` files and link them.
- If `program.md` is stale, rewrite it as the current execution index rather than appending a journal.
- If phase detail would make `program.md` long, move it into `program/<phase_id>-<slug>.md` and link it from `program.md`.
- If parallel agents or long-running runs are planned, create or update `orchestration/<phase_id>/summary.md` and `orchestration/<phase_id>/agent-trace.jsonl`.
- Before marking a task launchable, verify that its agreement and plan versions are current, blocking decisions are resolved or delegated, and that exact plan version has execution permission. Record mismatches as blockers; do not silently repair ownership or permission.
- Treat only blank fields explicitly tagged `delegable` in an approved contract, or choices owned
  by the applicable auto mode inside its approved scope, as delegation. Treat blank
  required, protected, or untagged fields as unresolved blockers.

## Periodic Housekeeper Check

For active scientific automation lasting more than three hours, use a supported recurring
automation every three hours when the user has authorized it. Otherwise run this check at each
phase boundary and record that fallback in `orchestration/<phase_id>/summary.md`.

Each check should verify:

- `program.md` is current, short, and not used as a progress journal.
- `program/00-execution-agreement.md` is current, under 200 lines, and preserves
  user edits.
- The contract evidence map has no unresolved material contradiction after agreement approval,
  and every remaining uncertainty maps to engineering evidence, accepted risk, or explicit deferral.
- The compact pipeline state matches the execution agreement and decision log, and every executing task cites the current agreement and plan versions.
- The engineering handoff and active tasks honor hard constraints from the human contract;
  soft-preference violations have recorded rationale.
- The handoff/return trace maps material evidence and decision ids to experiment, run, wave,
  provenance, artifact, or explicit non-action identifiers.
- Execution permission covers the active phase and actions; unresolved gates, expired permission, or plan-version drift are visible as blockers.
- Every active experiment has a visible claim, hypothesis, decision criterion, and metric/evidence before launch.
- `program/` and `orchestration/` contain the active phase plans, traces, agent assignments, and logger handoff notes.
- Elected run paths are unique and no artifacts from older runs were overwritten.
- Metrics, plots, reports, summaries, scheduler logs, and failure traces use the standard taxonomy.
- Every single-producer visualization and plot root preserves the producer's complete numbered
  experiment/sub-experiment hierarchy. Record and route any flattened or mismatched path; do not
  move code or artifacts from this skill.
- Every failed run is preserved and classified exactly once. Operational recovery of the same
  scientific configuration retains its elected run id and wave recovery semantics; material
  code/config changes require a newly approved wave and applicable run-id evolution checks.
- Logger handoff material is complete enough for a `flywheel-log` subagent to publish without
  reading the chat transcript.

The housekeeper must not launch experiments, mutate Flywheel, edit `EXPERIMENTS.md`, delete
artifacts, rewrite scientific conclusions, change mode, resolve scientific decisions, grant
execution permission, or act as the user-facing progress reporter. Record discrepancies and route
them to `scientific-orchestrator`; only that orchestrator consolidates user notifications.

## Required Checklist

Ensure `program.md` contains:

```md
- [ ] Execution agreement and current plan version recorded
- [ ] Material evidence items resolved, mapped to engineering evidence, accepted, or deferred
- [ ] Scientific-to-engineering traceability complete
- [ ] Exact plan version approved for the next execution scope
- [ ] Subagent delegation plan written before launch
- [ ] Claim, hypothesis, decision criterion, and metric/evidence recorded before launch
- [ ] Planning artifacts complete
- [ ] Verification disposition recorded: full audit for a material gate, or targeted verification with reason
- [ ] Objective completed
- [ ] Flywheel disposition recorded; logging delegated when the result is loggable
```

Each item must include enough detail to verify it later: agent/thread name or direct-execution exception, evidence paths, output paths, and unresolved blockers.

## Orchestration Trace

For each delegated chunk, record:

```json
{"agent":"<role/name>","objective":"<task>","agreement_version":"<version>","phase_id":"<phase>","decision_ids":[],"write_scope":"<paths or read-only>","commands":[],"files_touched":[],"outputs":[],"status":"pending|completed|failed|blocked","risks":[]}
```

Keep traces small and factual. Do not duplicate full logs; point to stable output paths and summarize decisions. Append decision, progress, and deviation events to `orchestration/control/events.jsonl` with the applicable agreement version, phase id, triggering fact, action taken, and whether the orchestrator must notify the user; never rewrite prior control events.

For an operational incident, record the tier, causal failure, failed run ids,
affected runtime contract, smallest fix, targeted checks, preflight outcome, and
retry run id. Consolidate recurrences of the same cause into that record.

## Logger Handoff

Before calling a logger agent, prepare a concise handoff in `orchestration/<phase_id>/logger-handoff.md` with:

- objective and claim
- run ids, wave ids, and standard artifact paths
- commands and configs used
- metrics, plots, reports, and failure traces
- verification disposition, material blockers, open fix-forward items, and unresolved caveats
- agreement version, engineering handoff id, approved execution scope, and material deviations
- Flywheel root or destination, if known

Do not log to Flywheel from this skill. If logging is needed, prepare the handoff and delegate a
bounded logger subagent using `flywheel-log`.
