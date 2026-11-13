# Orca behaviour this adapter relies on

Checked against Orca 1.4.257. Orca changes quickly: when a command or field below no longer
matches, trust `ORCA skills get orchestration` and the command's `--help`, and fix this file.

## Sidebar nesting is per workspace

`worker-start --worktree new-child` makes the new worktree a child of the caller's workspace,
never of a chat. Several coordinator chats in one workspace therefore mix their workers under
one parent. That is why each group gets its own `l0-<group>` workspace, and why the coordinator
never runs in the repository's main checkout. A child can be moved later with
`ORCA worktree set --worktree <selector> --parent-worktree <selector>`.

## Runs are bound to a terminal pane

The first `worker-start` from a coordinator creates an orchestration Run bound to that pane.
Resuming the chat in the same pane keeps the binding. In a new tab or pane,
`orchestration run-current` returns no Run and `worker-start` is refused until
`orchestration run-use --id <run>` rebinds it; `resume-check` does this from the saved state.
Binding a Run to a pane unbinds any other Run that pane held, so one pane coordinates one group.

A resumed chat may move to the folder it was recorded in, while `ORCA_WORKTREE_ID` still names
the terminal's own workspace. `resume-check` compares that variable, not the folder, with the
saved workspace, and refuses on a mismatch.

## Nesting depth

Orca's Settings has Nested worker depth (default 4). Set to 1, a level-1 worker cannot start
helper workers and its brief no longer offers them: the attempt fails with
`nested_worker_depth_exceeded`. Orca enforces it only while the worker has an active task, so
the level-1 rules forbid starting workers after reporting as well. Only the user can change the
setting, in Orca's Settings window.

## The worker brief

Orca prefixes every spec with its own worker brief. It says to work without subagents or helper
workers "unless the task asks", which is why every fleet spec asks for level-2 subagents
explicitly. The brief also carries the worker's task and dispatch ids and the `ask` and done
commands it must use. A worker's subagents inherit its terminal identity, so an Orca command run
by one of them counts as the worker's own.

## Branch names

A worker's branch is Orca's prefix plus its workspace name. Orca's automatic rename touches only
names it generated itself, so a branch renamed by the worker, or the coordinator's
`--branch` name, stays as set.

## Prompts and specs

`worktree create --agent … --prompt …` submits the prompt to the new chat at once. Specs reach
workers pasted after Orca's brief and are size-limited, so specs live in files and the
`--spec` text only points to one.
