# Research guard

`guard.py` is a `PreToolUse` hook, for Claude Code only. It checks the agent's own `Bash`, `Edit`,
`Write` and `Read` calls in a project tagged `research` against the rules the skills write down.
This matters because ticks and chats on this machine run in `bypassPermissions`, where those rules
are otherwise only text. A hook's `deny` holds in every permission mode.

It catches accidents and is not a security boundary. A script, `python -c` or a variable holding a
path gets past it. Skills' own scripts run their subprocesses unseen, which is correct.

**It starts log-only: it never blocks anything until you turn enforcement on.**

## What it flags

| Rule | Flags |
| :- | :- |
| `git-push-force`, `git-reset-hard`, `git-clean`, `git-stash`, `git-commit-no-verify` | Those git commands. Read-only forms pass: `git clean -n`, `git stash list` and `show`. |
| `rm-mv-run-data` | `rm`, `mv`, `rmdir`, `unlink` or `find -delete` on `checkpoints/`, `evaluations/` or `rid-*`. A copy under `/tmp` or the scratchpad passes. |
| `remote-copy` | `scp` or `rsync` with a `host:` argument. |
| `runid-rollback-force` | `runid ... migrate rollback --force`. |
| `kill-by-hand` | `scancel`, `pkill`, `killall`, `kill`. `kill -0` passes. It cannot tell your own process from another project's job, so expect this rule to be the noisiest. |
| `read-env` | The `Read` tool, or a printing command such as `cat`, `grep` or `head`, on `.env` or `.env.*`. `.env.example`, `source .env` and `test -f .env` pass. |
| `run-record` | `Edit`, `Write`, or a shell redirect or `rm` on `.run_id.json`, `.run_config.json`, `RUN_ID_MAP.json`, `RUN_ID_HISTORY.json`, `*.offloaded.json`. |
| `wave-state`, `board-lane` | The same, on `.waves/_state/**` and the board's `lanes/<rig>/gpu*.json`. |
| `run-id-versions` | `Edit` or `Write` on `config/<exp>/RUN_ID_VERSIONS.json` once `checkpoints/`, `evaluations/`, `plots/` or `logs/` holds something for `<exp>`. |
| `experiments-during-wave` | `Edit` or `Write` on `EXPERIMENTS.md` while a wave is live, except from a supervisor agent. |
| `ssh:<rule>` | Any Bash rule above, inside `ssh <host> ...`. A plain `ssh rig nvidia-smi` passes. |

Commands are parsed, not searched: a commit message, an `echo` or a grep pattern holding these
words does not match.

## What it logs, and where

Each would-be refusal adds one JSON line to
`${XDG_STATE_HOME:-~/.local/state}/research-guard/guard.jsonl` (mode 0600; it moves to
`guard.jsonl.1` past 5 MB). The line holds the time, `logged` or `denied`, the rules, the tool, the
first 400 characters of the command or the file path (never a `Write`'s content), the working
directory, the session id, the permission mode, the agent type, and whether a supervisor agent made
the call. A hand-typed secret in a command would land there, hence the mode.

## How to read it

```
python3 plugins/research/hooks/guard.py report
python3 plugins/research/hooks/guard.py report --days 7 --rule kill-by-hand --examples 20
```

From an installed plugin the script is under
`~/.claude/plugins/cache/<marketplace>/research/<version>/hooks/guard.py`.

The report counts entries per rule, splits chats from supervisor agents and prints distinct
examples. Before enforcing, ask of each rule: was that call wrong, or would refusing it have got
in the way?

## How to turn enforcement on and off

```
python3 plugins/research/hooks/guard.py mode enforce   # deny, with the reason shown to the agent
python3 plugins/research/hooks/guard.py mode log       # back to log-only, the default
python3 plugins/research/hooks/guard.py mode off       # does nothing, logs nothing
python3 plugins/research/hooks/guard.py mode           # print the mode
```

This writes one word to `${XDG_CONFIG_HOME:-~/.config}/research-guard/mode`, read on each flagged
call, so it applies to running sessions and to supervisor ticks. `RESEARCH_GUARD=enforce|log|off`
in the environment of a session wins over the file, for that session only. To lift enforcement for
one call you need yourself, run it in your own terminal: the guard sees only the agent's calls.

## Limits

- Any error lets the call proceed (a bad payload, an unreadable tag, a missing script: the hook
  command ends in `|| true`). Only `deny` blocks; the guard never uses `ask`, which an unattended
  tick could not answer.
- It applies when the working directory, the session's project directory, or the edited file's
  directory has a `.project.toml` tagged `research`.
- `.env`: the guard adds the `Bash` reads and a log. A `Read(./.env)` deny rule in project settings
  is separate and stronger for the `Read` tool: it holds in every mode, whatever the guard's mode.
  With both in place and the guard log-only, the log can show a `Read` that the rule then refused.
