# Bounded subagent roles

Use these contracts when dedicated installed agent profiles are unavailable. Pass only the active
task, relevant repository paths, and required sources; do not leak the orchestrator's preferred
answer.

## Assignment packet

Give every worker:

- assignment id, objective, and success condition;
- agreement version, phase id, relevant decision ids, and only the constraints needed locally;
- exact input paths, allowed sources, and source-read-only or disjoint source write scope;
- required skill, sole report or artifact path, validation, and stop condition;
- permission and resource boundaries inherited from the active agreement.

Start the worker with fresh or minimal conversation context. Do not pass the main transcript.
Source-read-only workers may write only their assigned report path. Workers execute directly
inside the packet, do not mutate shared control records, and do not delegate again unless the
packet explicitly authorizes nested delegation.

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

Give the active agreement/phase and record paths. Require use of `research-housekeeping`, a
read-only audit, proposed factual corrections, no shared-record or `EXPERIMENTS.md` mutation, no
Flywheel publication, and no rewriting of append-only records.

## Required return envelope

Put detailed findings in the assigned artifact or report path. Return only a concise envelope to
the orchestrator: assignment id, conclusion, supporting evidence or artifact paths, files touched,
validation result, unresolved material risks, and whether the stop condition was reached. Do not
return raw logs, long excerpts, or a chronological tool narrative.
