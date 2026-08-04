---
name: science-literature-plan
description: Turn a vague scientific idea, research hypothesis, experiment direction, or benchmark proposal into a literature-grounded research plan. Use when Codex needs paper explorer subagents, common-practice extraction, evidence tables, methods, datasets, metrics, baselines, caveats, and unresolved uncertainties before implementation or experiments.
---

# Science Literature Plan

Convert the idea into a verifiable research plan before proposing implementation. Read the active execution agreement and decision log when present; follow `$scientific-orchestrator` for modes, decision categories, plan versions, and approval points. Read [references/paper-explorer.md](references/paper-explorer.md) before delegation.

## Start

- Restate the scientific idea, target claim, and intended decision.
- Name assumptions explicitly and return material decision candidates to the router
  with a recommendation and evidence; do not redefine mode or ownership rules here.
- Define success criteria as observable evidence, not broad intent.
- Identify high-impact choices about claim, evidence boundary, dataset, metric, baseline, compute, and research direction. Return them to the router as decision candidates with a recommendation, alternatives, evidence, trade-offs, and downstream consequences; classify them under the active agreement rather than redefining decision categories here.

## Literature Search

- Prefer primary sources: papers, official benchmark pages, official code, dataset cards, and maintainer documentation.
- Use paper-focused tools when available and verify important details against the paper or official repository.
- Browse live sources for recent or fast-moving claims, benchmarks, library APIs, model releases, or leaderboard status.
- Use a deep multi-agent review for every non-trivial scientific question. Spawn at least 4-5 paper-explorer subagents by default; use more when the field is broad, the claim is high-impact, or the literature splits into distinct method families. Keep one slot for the orchestrator and run explorers in waves when concurrency is limited.
- Limit each `paper-explorer` to at most 3-4 highly relevant papers or source artifacts. Do not ask one agent to survey a large field alone; this increases hallucination risk, shallow reading, and paper/code imprecision.
- Assign each explorer a distinct slice such as method family, benchmark family, dataset/evaluation protocol, theoretical framing, implementation/code lineage, or negative/failed approaches.
- Require the main agent to reconcile explorer outputs, deduplicate sources, resolve contradictions, and downgrade claims that are not independently supported.

Subagent return format:

```md
Scope assigned:
Papers/sources inspected, max 3-4:
Why these sources are relevant:
Main method or claim:
Evaluation setup:
Metrics and baselines:
Code or reproducibility status:
Caveats and failure modes:
What this source does not establish:
Follow-up sources worth assigning to another explorer:
```

## Extract Common Practice

- Identify recurring problem formulations, model families, training regimes, ablations, evaluation protocols, and reporting conventions.
- Separate what is standard practice from what is contested, newly proposed, or under-specified.
- Track dataset details that change conclusions: split names, leakage risks, preprocessing, prompt templates, filtering, and evaluation harnesses.
- Track metric definitions closely enough that a future agent can implement them without guessing.

## Evidence Table

Build a compact evidence table with these columns:

```md
| Source | Relevance | Method | Dataset / Setting | Metric | Baseline | Result | Caveat | Code / Reproducibility |
```

Keep the table factual. Do not average incomparable numbers or treat leaderboard claims as paper-verified results without saying so.

## Required Local Artifacts

Write the literature review as local artifacts that future agents can reread without chat context.

Required:

- `program/<phase_id>-literature-plan.md`: the primary self-contained Markdown synthesis.

Recommended for non-trivial reviews:

- `program/<phase_id>-literature-evidence.csv`: source-by-source evidence table in machine-readable form.
- `orchestration/<phase_id>-literature-review/summary.md`: short coordinator note describing search scope, subagent assignments, and reconciliation decisions.
- `orchestration/<phase_id>-literature-review/agent-trace.jsonl`: one factual record per paper-explorer assignment with status, sources inspected, outputs, and unresolved risks.

Elect a stable, unique phase id for each distinct review. Update the same files when
refining that phase; elect a new phase id for a later review so prior evidence is
never overwritten.

The Markdown synthesis must be readable on its own. Include:

```md
# Literature Plan: <topic>

## Research Question
## TL;DR
## Search Scope
## Evidence Table
## Common Practices
## Method Families
## Failure Modes And Caveats
## Implications For Our Pipeline
## Open Questions
## Recommended Next Step
```

If a CSV is produced, keep its columns aligned with the Markdown evidence table so downstream engineering-handoff, audit, and logging agents can parse it directly.

## Plan Output

Deliver `program/<phase_id>-literature-plan.md` with:

- Research question and concrete hypothesis.
- Literature-grounded rationale.
- Recommended methods, datasets, metrics, and baselines.
- Minimal experiment sequence ordered by information value.
- Required artifacts: config, metrics, plots, summaries, and reproducibility notes.
- Risks, confounds, and unresolved uncertainties.
- Explicit no-run gate: state what must be confirmed before launching training, evaluation, or broad data processing.
- Agreement and plan versions plus the unresolved or delegated decisions that control the recommended next step.

In the chat response, summarize the artifact paths and the main decision. Do not substitute a chat-only summary for the Markdown artifact when working inside a repository.

The required `program/` and `orchestration/` records above are the only repository
mutations owned by this skill. Do not launch experiments, write implementation or
configuration code, mutate run artifacts, or publish results. Execution permission
and approval remain owned by `$scientific-orchestrator`; this literature-planning
skill does not perform the gated action.
