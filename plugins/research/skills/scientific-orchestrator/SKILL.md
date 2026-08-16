---
name: scientific-orchestrator
description: "Coordinate nontrivial scientific research through persisted control state and bounded subagents: frame the question, ground literature, challenge assumptions, hand approved steps to engineering skills, audit evidence, and choose the next branch. Use for multi-stage programs or autonomous scientific iteration, not a single specified experiment or routine sweep."
---

# Scientific Orchestrator

Own the scientific control loop. Let the engineering skills own concrete experiment design,
environment parity, deployment, dispatch, run state, and artifacts.

Read [references/control-contract.md](references/control-contract.md) before opening or resuming a
program. Read [references/subagent-roles.md](references/subagent-roles.md) before delegation. Use
[references/operational-fast-path.md](references/operational-fast-path.md) to classify failures.

## Admission gate

1. Require the Research 2.0 project surfaces: `program.md`,
   `program/00-execution-agreement.md`, `program/decision-register.md`, `EXPERIMENTS.md`,
   `JOURNAL.md`, `index.md`, `orchestration/` ignored by Git, and the standard artifact taxonomy.
2. If any required surface is absent or the project exposes a legacy status/script schema, stop.
   State that the repository is unsupported; do not offer or perform migration.
3. Reconcile factual run state with `experiments-tracking` before using prior run claims.
4. Require explicit `scientific_mode` and `engineering_mode`. Never infer either mode.
5. Require an approved scope, resource envelope, external destinations, and protected choices.
   A request for autonomy is not an unbounded authorization.

## Records

Use `research-housekeeping` to establish and reconcile the records. Keep:

- `program.md` as the one-screen current index, never a progress journal;
- `program/00-execution-agreement.md` as the current human-editable contract, under 200 lines;
- `program/decision-register.md` as the tracked decision history;
- tracked phase reports under `program/`;
- append-only local events and task traces under ignored `orchestration/`;
- `EXPERIMENTS.md` as the only run-state authority;
- `JOURNAL.md` as the chronological narrative;
- Flywheel as curated scientific lineage and `index.md` as its local mirror.

Link records with experiment name, run id, wave id, source revision, artifact path, and Flywheel
node id. Do not copy raw logs or factual run tables into program files.

## Main-thread control plane

Keep the main orchestrator focused on the user, control state, delegation, decisions, approvals,
and synthesis. Delegate every bounded work package that requires substantive repository
exploration, implementation, testing, external research, log analysis, monitoring, or independent
verification. The main orchestrator may directly read applicable skill contracts and canonical
control records, update those records, inspect concise worker returns, resolve cross-worker
conflicts, and perform small integration checks needed to choose the next assignment.

Before every delegation, update `program.md` and append a pending assignment to the active
`orchestration/<phase_id>/agent-trace.jsonl`. Immediately after the worker returns, persist its
status, evidence and artifact paths, material risks, and exact next action before delegating again
or reporting to the user. Treat these records, not the chat transcript, as the continuity source
after compaction, resume, or orchestrator replacement.

## Scientific loop

1. **Frame.** Record the question, active claim, hypothesis, plausible alternatives, evidence
   stage, evaluation boundary, decisive evidence, success/kill criteria, budget, and stop rule.
2. **Ground.** Delegate `science-literature-plan` for material external uncertainty. For a
   nontrivial question, delegate 4-5 bounded paper-explorer assignments in concurrency-safe waves,
   then reconcile them rather than voting.
3. **Challenge.** Delegate `assumption-breaker-plan` before an expensive, confirmatory, or
   irreversible branch and whenever the current explanation is weak or repeatedly failing.
4. **Choose.** Rank the smallest experiments that discriminate among live explanations. In
   scientific-manual mode, preview the recommendation and wait for approval. In scientific-auto
   mode, choose inside the approved scientific scope and record the rationale.
5. **Compile.** Write an engineering handoff with the fields in the control contract. Delegate its
   realization to `experiment-design`, then use `environment-sync`, `rig-sync`, and
   `sweep-dispatch` as required. Do not choose their technical representation here.
6. **Receive.** Require the engineering return packet. Treat `EXPERIMENTS.md`, provenance-valid
   status, and artifacts as evidence; do not accept a prose success claim without them.
7. **Audit.** Delegate `critical-scientific-audit` when evidence is decisive, confirmatory, final,
   claim-promoting, materially expensive, or validity is uncertain. Apply its allowed claim and
   minimum closure path.
8. **Decide.** Mark the hypothesis supported, falsified, narrowed, or unresolved; select the next
   branch or stop. Record the decision and update the narrative according to mode ownership.
9. **Dispose.** End every nontrivial loop with a Flywheel disposition in `program.md`: delegate
   publication to a logger subagent using `flywheel-log`, or record why the work is not
   scientifically loggable. The main orchestrator never publishes nodes directly.

Repeat only while the approved stop rule, budget, scope, and terminal condition permit it.

## Delegation and concurrency

Delegate with a fresh or minimal-context worker whenever the host supports it; do not copy or fork
the full main transcript. Give each worker the assignment packet in
[references/subagent-roles.md](references/subagent-roles.md), full freedom inside that packet, and
a disjoint write scope when it may edit. Workers must not update `program.md`, the decision
register, or shared control traces, and must not recursively delegate unless the assignment
explicitly authorizes it. The orchestrator remains their sole reconciler, synthesizer, and
decision-register writer.

Require detailed findings to land at stable artifact or report paths and require only the concise
return envelope in the main thread. Use source-read-only workers for exploration and verification,
granting each only its assigned report path outside project sources and shared control records.
Run overlapping source write scopes sequentially; parallelize only independent scopes.

Run at most `available concurrent subagents minus one` workers, reserving the remainder for the
orchestrator itself; never assume a fixed slot count, and never stop merely because concurrency is
limited. When the ceiling is unknown, treat it as the number the host actually starts in parallel.
Satisfy larger literature coverage in waves, and run explorers sequentially rather than skipping
them. Rig-monitoring subagents hold their slots for the whole wave, so when a wave is live, defer
literature explorers and say so instead of silently dropping them.

If no worker slot is available, wait for one or schedule a later wave. Do not silently execute the
substantive assignment in the main thread. Before completing the loop, delegate an independent
verification pass and reconcile its evidence into the canonical records.

## Mode behavior

- Scientific-manual: the user approves trajectory choices and Flywheel publication.
- Scientific-auto: choose and publish within the approved scientific scope and canonical root.
- Engineering-manual: engineering skills retain their explicit design, provisioning, and launch
  approvals.
- Engineering-auto: engineering skills may choose and execute non-destructively inside the
  approved envelope after their mandatory integrity gates pass.

System permission prompts and the protected boundaries in the control contract override every
mode. On a protected, ambiguous, or out-of-envelope decision, stop at the safe boundary and ask.

## Completion

Finish only when the current scientific question has a recorded disposition, engineering state is
reconciled, decisive claims have the required audit, program records agree, the journal policy was
applied, and the Flywheel disposition is explicit.
