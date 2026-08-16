---
name: critical-scientific-audit
description: "Run a context-aware, proportionate audit of scientific validity and pipeline flow. Use when decisive evidence, evaluation integrity, measurement validity, reproducibility, material spend, external logging, or readiness work that may be bottlenecking or displacing the primary goal is at risk. Do not use for O0 startup failures or routine O1 runtime-smoke failures unless they may have invalidated scientific evidence."
---

# Critical Scientific Audit

This skill is the sole source of truth for scientific materiality, pipeline flow
status, finding dispositions, verdict semantics, and claim-limiting audit behavior.

Audit the evidence that matters to the current decision, not checklist completeness.

First read the repository guidance, active claim and plan, established baseline and protocol, prior comparable runs, and the artifacts needed to judge the current stage. If required context has not been inspected, inspect it or record a non-blocking question; do not invent a blocker from a generic concern.

Before auditing a failed run, require its classification under
[the canonical operational fast path](../scientific-orchestrator/references/operational-fast-path.md).
Do not produce a scientific audit for `O0`. Give routine `O1` failures only
targeted verification of the affected runtime contract; run a full audit only
when produced evidence may be invalid or the proposed fix changes a scientific
boundary. Audit `S1` through the normal path.

## Protect the Primary Objective

Before a broad audit, compare the active objective, next promotion gate, critical
path, elapsed budget, and current tasks. Every pending test, artifact, or review
must either reduce uncertainty that could change the next decision or protect a
named hard boundary. Diagnose flow from the existing plan, traces, timings, and
evidence; do not launch tests or demand new artifacts merely to prove a bottleneck.

Report one independent flow status:

- `on-track`: remaining work is necessary for the next gate and proportionate to its risk.
- `bottlenecked`: redundant, obsolete, overengineered, or unnecessarily serial work is delaying the decisive path.
- `goal-displacing`: readiness or audit work is consuming the budget without materially reducing uncertainty for the primary objective.

Treat repeated equivalent checks, tests made obsolete by a frozen design,
generic artifact production, independent work kept unnecessarily serial, and readiness that
costs more than the decisive run as concrete bottleneck evidence. For
`bottlenecked` or `goal-displacing`, return a minimum closure path: freeze the
scope, name only must-pass checks, stop or defer obsolete work, parallelize safe
independent tasks, prefer one consolidated patch and one material audit when required, then
advance to the decisive run. Do not create extra artifacts merely to document
the bottleneck. If no uninspected material threat remains for the named
transition, end the broad audit early. Time pressure never waives a material
scientific blocker.

## Set the Evidence Stage

Judge the claim by how the evidence will be used:

- **Exploratory:** choose a next step or debug a system. Labeled partial runs, descriptive statistics, one-seed screens, and an established repository baseline may be sufficient.
- **Confirmatory:** decide a predeclared claim. Require comparable baselines, controls, completion, and uncertainty only to the extent needed for that decision.
- **Final or publication:** support a final conclusion or external record. Require sealed evaluation, transparent exclusions, reproducible decisive evidence, and wording fully supported by the result.

Do not let an agent call evidence exploratory when it is being used to make a confirmatory or final claim.

## Apply the Materiality Test

Classify an issue as blocking only when inspected evidence shows that it could:

- change the current decision or invalidate the exact claim;
- contaminate the sealed evaluation or invalidate a decisive metric, prompt, scoring path, split, or comparator;
- make decisive evidence materially unverifiable or irreproducible;
- violate the execution agreement, permissions, budget, safety, or an external commitment; or
- make a final or published record materially false.

Cite the artifact and the causal consequence. Speculative confounds, optional robustness work, polish, and completeness improvements are not blockers. A checklist category is never blocking by itself.

Use these finding dispositions:

- `blocker`: invalidates the named claim, decision, or transition.
- `fix-forward`: safe local correction inside the approved scope; continue unaffected work and verify it at the next natural checkpoint.
- `claim-limiting`: evidence supports a narrower factual claim but not a broader one.
- `advisory`: worthwhile robustness or follow-up work with no current gate.

## Use Proportionate Verdicts

- `pass`: no material blocker. It may include fix-forward items, claim limits, and advisories; these do not hold the gate.
- `conditional pass`: a material local issue must be fixed before one named transition. Freeze only that transition and its dependents, not the whole pipeline.
- `fail`: the named claim or action is materially invalid and cannot be repaired locally without changing the plan, evaluation, scope, or success decision. Correct it and re-audit only the affected scope.
- `blocked by missing evidence`: decisive evidence is absent for the requested conclusion or promotion. Do not block a bounded task whose purpose is to collect that evidence.

No audit verdict grants execution permission. Leakage, contaminated evaluation, invalid decisive metrics, and scope or permission violations remain hard blockers.

## Treat Common Concerns Proportionately

- **Baselines and controls:** judge adequacy against the exact claim and repository standard. Extra baselines are advisory unless a missing or mismatched comparator could change the decision.
- **Partial runs and selection:** accept clearly labeled partial or selected evidence for debugging and screening. Block only when selective missingness, omitted failures, or incomplete coverage is used as representative evidence and could change the conclusion.
- **Preprocessing or implementation confounds:** require a concrete path from the suspected confound to the decisive metric. A bundle probe may support a bundle-level claim; it cannot support component attribution without isolation.
- **Statistics and effect size:** match the standard to the design. Smoke tests, deterministic checks, and exploratory screens do not require paper-grade inference. Block when uncertainty could reverse a confirmatory decision or a final generalization.
- **Claim strength:** correct wording immediately when narrowing only removes unsupported breadth and leaves the objective, success decision, evaluation boundary, and external commitment unchanged. Escalate only when the correction changes one of those boundaries. Claim narrowing cannot repair leakage or an invalid measurement.

## Check Hard Validity Where Relevant

- the human run contract at `program/00-execution-agreement.md` is under 200
  lines, preserves user-written fields, remains authoritative for material
  decisions, and clearly separates hard constraints, soft preferences,
  protected choices, and fields explicitly tagged `delegable`;
- the explicit scientific and engineering modes were honored, and every automatic
  decision remained inside its approved scope or envelope;
- every material evidence item is resolved before planning, mapped to decisive
  engineering evidence, knowingly accepted as risk by its owner, or explicitly deferred;
- the engineering handoff and return packet map claims and decision criteria to
  experiment, run, wave, provenance, and artifact identifiers without hidden scope;
- hard constraints from the human contract are honored; soft preferences are
  violated only with recorded rationale and no hidden scope expansion;
- train, validation, test, benchmark, or holdout contamination;
- adaptive tuning on nominal final evidence;
- canonical prompt rendering, generation prefix, scoring span, and special tokens for LLM or VLM work;
- mismatch between the command, config, data path, metric, and reported method;
- artifact overwrite, fabricated or inconsistent plots, silently skipped failures, or summary-number mismatch;
- for a newly agent-authored plot, require evidence that before the plotting-code edit the user
  explicitly approved either its complete project-relative `plots/` export path including its leaf
  filename or, when runtime values prevented a concrete destination, the complete project-relative
  `plots/` path template including its leaf-filename template and every identified placeholder.
  Only explicit user approval satisfies this gate; scientific-auto and engineering-auto cannot
  bypass it. Require evidence that every fully resolved concrete export path, including its leaf
  filename, received explicit user approval before rendering. An approved template does not approve
  any concrete destination. A new or changed resolved path always reopens approval, even when it
  conforms to the approved template; do not accept a render before that approval. Approval reused
  for unchanged code is valid only when every resolved concrete export path is byte-for-byte
  identical to the previously explicitly approved concrete path. Also reject a rendered figure that
  does not legibly match the accepted specification. Treat these as protected-contract violations
  rather than optional polish, without retroactively applying them to extracted, user-supplied, or
  historical figures;
- missing information needed to reproduce the decisive result;
- plan drift, stale versions, unauthorized choices, unresolved protected blanks,
  or execution outside allowed scope.

Require a rerun, recomputation, or full reproduction only when it is the smallest check that can resolve a material uncertainty. Prefer direct local fixes and targeted checks over a new approval or a full re-audit.

## Return

Keep the report compact. For `bottlenecked` or `goal-displacing`, do not restate
generic missing artifacts or optional checks; include only items that affect the
next gate or minimum closure path.

Inside a repository, write the report to `program/<phase_id>-scientific-audit.md` and keep
operational attempt details in the consolidated `orchestration/<phase_id>/operational-failures.md`.
Do not edit `EXPERIMENTS.md`, append the journal, publish to Flywheel, or create one audit per O0/O1
attempt.

1. Scientific verdict, evidence stage, and flow status.
2. Primary objective and critical path: bottleneck evidence, obsolete or redundant work, and minimum closure path.
3. Material blockers, smallest fixes, fix-forward items, claim limits, and prioritized advisories affecting the next gate.
4. Claims allowed, corrected, or deferred; required reruns or checks.
5. Decision-ownership, plan-drift, evaluation-boundary, reporting, and reproduction gaps that are material now.
6. Human-contract gaps: ungrounded questions, unresolved contradictions or
   planning facts, hard/soft constraint conflicts, protected or untagged blanks,
   unsafe delegation, or missing scientific-to-engineering traceability.
7. Trace: context and artifacts inspected, commands run, files touched, outputs, unresolved material risks.
