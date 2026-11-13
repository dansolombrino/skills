---
name: agent-fleet-orca
description: Orca adapter for agent-fleet: start, resume and supervise one Orca worker per fleet item. Use only together with the agent-fleet method, when it directs you here or a fleet coordinator chat runs inside an Orca terminal and must create its coordinator workspace, start or message workers, or reattach after a resume. Not for a single hand-off to another agent, one new worktree, or Orca terminal, browser or worktree chores (orca-cli), nor for Orca workers outside a fleet (orchestration).
---

# agent-fleet-orca

## First

1. If the `agent-fleet` skill is not loaded in this chat, load it now and follow it. This file
   only says how its operations run in Orca; the levels, roles, and rules live there.
2. If the request is not several work items each run by its own agent (it is one hand-off, one
   new worktree, or one review), stop using this skill and use Orca's own `orca-cli` or
   `orchestration` skill instead.

## Setup

- **Orca CLI.** Use the value of `ORCA_CLI_COMMAND` when set. Otherwise, on Linux use
  `orca-ide`, never bare `orca` (outside Orca it is the GNOME screen reader); elsewhere `orca`.
  Below, `ORCA` stands for that executable.
- **Orca's own guide.** Run `ORCA skills get orchestration` once per chat for command details;
  it matches the installed Orca. Where it and this file differ, the stricter rule wins.
- **Helper script.** `scripts/orca_fleet.py` lives in this skill's installed directory, the
  folder holding this SKILL.md, not in the project. Call it by absolute path as `FLEET`; every
  subcommand prints one JSON object, and a refusal carries `error`, `message` and `next`: do what
  `next` says.
- **Defaults** (agent ids, names, state location): [defaults.md](references/defaults.md).
- **Why the rules below exist** (Run binding, resume, nesting depth, branch names):
  [orca-notes.md](references/orca-notes.md).

## Operations

### where-am-i
`python3 FLEET detect`. `role_hint` is `outside-orca` (no Orca adapter here: say so and stop),
`main-checkout` or `other-workspace` (bootstrap), or `l0-workspace` (run resume-check).

### bootstrap-coordinator
1. Write the brief to a temporary file outside the repository; bootstrap copies it into the
   group's state directory.
2. `python3 FLEET bootstrap --group <group> --brief <file> --agent <your own agent id> [--branch-prefix <prefix>] [--base-branch <ref>]`.
   Pass `--branch-prefix` only when the repository states a branch naming rule. Add `--dry-run`
   first when unsure what it will do.
3. Orca creates the top-level workspace `l0-<group>` and starts a new coordinator chat there,
   already prompted with the brief. Tell the user to switch to `l0-<group>` in Orca's sidebar
   and continue there; then stop in this chat.

A workspace the user made by hand and named `l0-<group>`: `python3 FLEET adopt --group <group>`.

### resume-check
`python3 FLEET resume-check`, first thing whenever the coordinator chat starts or is resumed. It
reattaches the chat to the group's Orca Run when the chat now runs in a different terminal pane,
and refuses when the chat runs in a terminal of another workspace. On `wrong-terminal`, tell the
user to open a terminal tab in `l0-<group>` and resume the chat there; start nothing meanwhile.

### start-worker
1. Save the spec as `<state_dir>/specs/<item>.md` (`state_dir` comes from resume-check).
2. Start it:
   ```text
   ORCA orchestration worker-start --spec "Read <absolute spec path> and follow it. Load the agent-fleet skill as level 1. Use your own subagents (level 2); start no workers." --task-title "<group>: <item>" --worktree new-child --name <group>-<item> --base-branch <base ref> --agent <agent id> --json
   ```
   Add `--model` and `--effort` only when the user named them. Never `--worktree current` or
   `new-top-level`: a level-1 worker gets its own worktree nested under `l0-<group>`.
3. After the first start, `python3 FLEET record-run`. After each start,
   `python3 FLEET record-worker --item <item> --dispatch <dispatch id> --workspace <workspace> --agent <agent id> --status started`.
4. A failed start is never rerun blindly: read `failedStage` and follow Orca's guide on
   recovery.

### message-worker / answer-question
- Follow-up, fix, or new work for an item:
  `ORCA orchestration send --to dispatch:<dispatch id> --subject "<topic>" --body "<message>"`.
  Put anything longer than a few lines in a file under `<state_dir>/specs/` and point to it.
- A worker's question: `ORCA orchestration reply --id <message id> --body "<answer>"`.
- Update the item with `record-worker` (always with its current `--dispatch`) when its dispatch
  or status changes.

### wait
End your turn. Orca wakes you: in a chat each message arrives whole; in a terminal the wake names
a `check` command, and you run exactly that one. Never run `check`, `worker-list`, or
`worker-read` in a loop, and never sleep: a plain `check` takes the mail that would have woken
you.

### close-out
- Release settled workers nobody will use again: `ORCA orchestration worker-release` (see
  `--help`). Workers in their own worktrees stay open until released.
- Tell the user the group can be removed in one step: right-click `l0-<group>` in Orca's sidebar,
  then Delete with Descendants. Orca keeps any branch with unmerged commits for review. Never
  delete workspaces or branches yourself without the user's explicit go.
