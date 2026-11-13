---
name: agent-fleet
description: Run several work items in parallel: one main agent per item, each on its own branch and worktree. Use when the user wants a list of tasks, findings, fixes or features handled side by side by separate agents that report back to this chat as coordinator, however it is worded - one agent per item with its own helpers, split these across agents, fix all of these in parallel, orchestrate this like last time with you as the main chat. Also use when a task brief says to load this skill as level 1. Not for a single task, ordinary subagent fan-out inside one task, or handing one task to another agent with nothing coming back.
---

# agent-fleet

A fleet runs a group of work items through three levels. This skill is the method. How workers
and worktrees are actually created belongs to an orchestrator adapter (see Adapter).

| Level | Who | Where | Does |
|---|---|---|---|
| 0 | One coordinator chat per group | Its own coordinator workspace, never the repository's main checkout | Plans, writes specs, starts workers, answers them, brings decisions to the user, reports |
| 1 | One main agent per item | Its own new worktree and branch, nested under the coordinator workspace | Does the item end to end and reports once |
| 2 | Any number of helpers per item | Inside the level-1 worktree | Explore, implement slices, review, using the host's own subagent capability |

## Which level am I?

- Your task brief says you are level 1 of a fleet (it was written from
  [spec-template.md](references/spec-template.md)): follow [level-1.md](references/level-1.md)
  and the Rules for every level at the end of this file; skip the rest.
- Otherwise you are level 0: continue below, then follow [level-0.md](references/level-0.md).

## Adapter

Level 0 never creates worktrees or workers by hand. It uses exactly one orchestrator adapter:

| Adapter | Use when |
|---|---|
| `agent-fleet-orca` | This chat runs in an Orca terminal: `ORCA_TERMINAL_HANDLE` and `ORCA_WORKTREE_ID` are set |

Load the matching adapter skill now (its SKILL.md is in the sibling folder:
[../agent-fleet-orca/SKILL.md](../agent-fleet-orca/SKILL.md)). If none matches, tell the user that
no supported orchestrator was detected and stop. Never fall back to the host's own subagents or
to `git worktree add` for level-1 work: that collapses levels 1 and 2 and hides the workers from
the user.

Every adapter provides the same operations, and level-0.md uses only these names:

| Operation | Result |
|---|---|
| `where-am-i` | Inside the orchestrator or not; in a coordinator workspace or not; the group and its saved state |
| `bootstrap-coordinator` | A new coordinator workspace for the group, with a fresh level-0 chat started there on the brief |
| `resume-check` | The coordinator is reattached to its group and workers after a new or resumed chat, or refuses with the reason |
| `start-worker` | One level-1 agent on one spec, in a new worktree nested under the coordinator workspace |
| `message-worker` / `answer-question` | A follow-up, fix, or answer delivered to one worker |
| `wait` | The coordinator ends its turn and is woken by the orchestrator, never by polling |
| `close-out` | On the user's go, the group's worktrees removed (workers first, the coordinator workspace last, which ends the coordinator chat) or only the named items'; branches not proven merged are kept |

A new orchestrator means a new sibling skill, `agent-fleet-<orchestrator>`, providing the same
operations; this file only gains a row in the Adapter table.

## Rules for every level

- The target repository's own rules win: read its agent instruction files (`AGENTS.md` and
  similar) before planning. They decide branch names, gates, and what may be pushed.
- Branches: when the repository states a branch naming rule, follow it (the coordinator
  workspace branch is `<prefix>/l0-<group>`; each worker renames its own to
  `<prefix>/<group>-<item>`). Otherwise keep the orchestrator's default branch names.
- Nothing is pushed, merged, or opened or changed as a pull request without the user's explicit
  go for that item. When the go comes, the owning level-1 agent does it.
- Name things by item name. Never invent labels the user has not seen.
