# Bounded subagent roles

Use these contracts when dedicated installed agent profiles are unavailable. Pass only the active
task, relevant repository paths, and required sources; do not leak the orchestrator's preferred
answer.

## Paper explorer

Assign at most 3-4 primary sources or one narrow method family. Require: source identity and links,
author claims separated from measured evidence and interpretation, method and evaluation details,
limitations, reproduction blockers, implementation-relevant facts, unresolved questions, and a
trace of sources/tools. Do not let one explorer broaden its slice; schedule another assignment.

## Scientific critic

Give the active claim, evidence, alternatives, evaluation boundary, and decision criterion. Require
classified assumptions, concrete confounds, smallest discriminating checks, claim limits, and the
evidence that makes each concern material. Do not ask it to implement or launch.

## Research logger

Give the approved canonical Flywheel root, scientific disposition, engineering return packet,
decisive artifacts, source revision, and allowed node relationship. Require use of `flywheel-log`.
It must not run experiments, change code, fabricate evidence, or publish incomplete work.

## Housekeeper

Give the active agreement/phase and record paths. Require use of `research-housekeeping`, factual
referential updates only, no `EXPERIMENTS.md` mutation, no Flywheel publication, and no rewriting of
append-only records.

## Required return envelope

Every worker returns: assignment, conclusion, supporting evidence/paths, assumptions, unresolved
risks, files touched, tools/commands used, and whether its stop condition was reached.
