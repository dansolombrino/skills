# Spec template (level 0 fills one per item)

Keep the angle-bracket placeholders out of the final spec. Delete sections that do not apply,
but never the LEVEL 1 paragraph, Method, or Report: Method is what authorizes the worker's own
subagents.

```markdown
# Task: <title>   (fleet group <group>, item <item>)

You are LEVEL 1 of a fleet. Load the `agent-fleet` skill and follow its level-1 rules
(references/level-1.md); they apply to this whole task.

Your branch: <prefix>/<group>-<item>   (rename to it before your first commit; omit this line when the
repository has no branch rule)
Base: <base ref>

## Goal
<what must be true when you are done>

## Context
<facts, decisions already taken by the user, links or paths to read>

## Your area
<files or directories you may edit; everything else is read-only>

## Constraints
<repository rules that matter here, machines or services not to touch, checks not to run>

## Method
Use your own subagents (level 2): start with read-only exploration or audit subagents for your
area, then split independent work across parallel subagents; you integrate, test, and commit.
No orchestrator workers, no new worktrees, and no orchestrator commands from your subagents.

## Done when
<observable evidence: tests that fail before and pass after, commands and their expected output>

## Report
One done message with: outcome, branch, commits, checks before and after, report path,
"Outside my area", open questions. Do not push or open pull requests.
```
