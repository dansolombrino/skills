# Defaults

Change these here; the rest of the adapter reads them from this table.

| Setting | Value |
|---|---|
| Level-0 agent (bootstrap `--agent`) | The agent CLI running this chat, by its Orca agent id: `omp`, `claude`, or `codex` |
| Level-1 agent (worker-start `--agent`) | `omp`, unless the user or the item asks for another, for example `codex` as an independent auditor |
| Model and effort | Not passed unless the user named them; Orca then uses each agent's own configuration |
| Coordinator workspace | `l0-<group>`, top level (no parent) |
| Worker workspace | `<group>-<item>`, nested under `l0-<group>` |
| Coordinator branch | `<prefix>/l0-<group>` when the repository has a branch rule, else Orca's default; never pushed |
| Worker branch | `<prefix>/<group>-<item>` when the repository has a branch rule (the worker renames to it), else Orca's default for the workspace name |
| Group state | `${XDG_STATE_HOME:-~/.local/state}/agent-fleet/<repo id>/<group>/` holding `brief.md`, `state.json`, `specs/`; `AGENT_FLEET_STATE_DIR` overrides the root |

## Agent CLIs launched by hand

Starting a chat with Orca's new-tab button and typing the agent CLI in an Orca terminal (for
example `omp`, `omp --model …`, `omp --resume …`, `omp --continue`) are equivalent here. Orca's
shell wrapper adds its own integration to a typed `omp`, `claude`, or `codex`, and every Orca
terminal carries `ORCA_TERMINAL_HANDLE`, `ORCA_WORKTREE_ID`, and `ORCA_CLI_COMMAND`, which is all
this adapter needs. Calling a binary by absolute path skips only Orca's sidebar status. A chat
started outside Orca (tmux, a plain ssh session) has none of these, and `detect` reports
`outside-orca`.
