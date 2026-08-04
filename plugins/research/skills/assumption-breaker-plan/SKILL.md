---
name: assumption-breaker-plan
description: "Generate adversarial scientific planning critiques that break hidden assumptions instead of accepting the initial architecture or hypothesis. Use when Codex should identify fallacious premises, distinguish already-explored dead ends from worth-retrying variants, propose false-hypothesis alternatives, and design new experimental strategies."
---

# Assumption Breaker Plan

Treat the initial architecture, hypothesis, metric, and plan as unproven. Do not accept them as true because the prompt presents them as the starting point.

Read the Research 2.0 execution agreement and decision register. Use `$scientific-orchestrator`'s modes, allowed scope, and ownership rules without redefining them here. Stop as unsupported when those records are absent; do not create a legacy compatibility path.

List the core assumptions first. For each assumption, classify it as supported, untested, weakly supported, contradicted, confounded, or unfalsifiable.

Identify fallacies and brittle reasoning:
- circular validation
- metric substitution
- leakage or contamination dependence
- prompt-template or harness dependence
- overfitting to a known failure case
- architecture-first reasoning
- assuming negative results transfer across changed conditions

Separate prior paths into:
- already explored and still likely dead
- already explored but worth retrying because conditions changed
- superficially similar but scientifically distinct
- not actually tested

Generate alternatives that would make the original hypothesis false. Include null models, simpler baselines, reversed causal stories, data artifacts, evaluation artifacts, and mechanisms outside the proposed architecture.

Propose decision-ready experimental trajectories that can discriminate between competing explanations, not an unranked brainstorm. Prefer small, falsifiable tests before expensive runs.

For each proposed strategy, include:
- recommendation and why it outranks the alternatives
- question answered
- competing explanation it discriminates against
- minimal intervention
- required data or code path
- expected signal if the original hypothesis is true
- expected signal if an alternative is true
- kill criterion
- information value and what later decision it unlocks
- estimated compute, time, and monetary cost
- main scientific and engineering risks
- reversibility and recovery cost
- decision class, decision owner, and approval state
- logging or audit requirement

Return ranked, decision-ready recommendations to `$scientific-orchestrator`.
The router owns mode behavior, approval, and protected boundaries; assumption
breaking must not expand the agreement, budget, evaluation boundary, or
publication rights.

Inside a repository, write the self-contained analysis to
`program/<phase_id>-assumption-analysis.md` and record only assignment/output facts under
`orchestration/<phase_id>/`. Link evidence rather than duplicating run tables or raw logs. Do not
edit `EXPERIMENTS.md`, append the journal, or publish to Flywheel from this skill.

End with a ranked adversarial plan:
- highest-leverage assumption to attack
- fastest disconfirming experiment
- most important control
- path that should not be retried yet
- unresolved ambiguity that would change the next step
