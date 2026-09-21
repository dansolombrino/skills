# sweep-supervisor setup

One service per hub supervises every registered wave of every project. It needs three things in
the user's machine registry (`~/.config/rigsync/machines.toml`, the same file `rig-sync` and
`rig-board` read): the fleet board, a supervisor table, and an **agent profile**. Nothing here is
defaulted: `check` and `register` refuse an undeclared profile.

## Registry

```toml
[board]                      # see rig-board; the supervisor indexes its waves under <root>/supervisor/
root = "/absolute/path/outside/every/project/rig_board"
rigs = ["rig-4090", "rig-3090-ti", "rig-3080-ti", "behemoth"]
slurm = ["leonardo"]

[supervisor]
cycle_every = 60             # seconds between service passes (probe, feed, recover, offload, table); >= 10
tick_every = 600             # seconds between scheduled agent ticks; events wake it at once
agent_timeout = 900          # a tick that runs longer is stopped; the next one starts fresh
notify_command = ["notify-send", "sweep-supervisor", "{text}"]   # optional; run for every `ask`

[supervisor.agent]
model = "<model alias or id>"
effort = "<low|medium|high|xhigh|max>"
command = ["<headless agent executable>", "...", "{prompt}"]
```

`command` is an argv list, never a shell string. `{prompt}`, `{model}`, and `{effort}` are
substituted per tick; `{prompt}` is required. The tick runs with the project root as its working
directory and `SWEEP_SUPERVISOR_MODEL`/`SWEEP_SUPERVISOR_EFFORT` in its environment, so every
`decide` line and the table header name the profile that produced them. Every tick is a **fresh**
process: no conversation is resumed, so nothing can expire, compact away, or block on a prompt.

### Install targets

Claude Code, headless, with the profile this marketplace's owner chose (Opus at high effort for
every agent):

```toml
[supervisor.agent]
model = "opus"
effort = "high"
command = ["claude", "-p", "{prompt}", "--model", "{model}", "--effort", "{effort}", "--allowedTools", "Read,Edit,Write,Glob,Grep,Skill,Bash"]
```

`{prompt}` goes right after `-p`: `--allowedTools` is variadic and would consume a prompt placed
after its value, leaving the tick with no prompt. The supervisor refuses that ordering whenever it
loads the registry, so `check` fails on it.

Codex, headless — fill in the executable's own flags for model and reasoning effort from its
`--help`; the supervisor only requires the three placeholders it substitutes:

```toml
[supervisor.agent]
model = "<model>"
effort = "high"
command = ["codex", "exec", "<model and effort flags using {model} and {effort}>", "{prompt}"]
```

An unattended tick cannot answer a permission prompt, so the command must pre-authorize what a
tick uses: reading and editing inside the project, this plugin's scripts, `ssh` to the declared
rigs, `tmux`, `git`, and `date`. Prefer the narrowest allowlist the host supports over a blanket
bypass; a denied tool call fails that step of the tick, which the tick reports with `decide`.

## Service

```bash
mkdir -p ~/.config/systemd/user && cp /absolute/path/to/sweep-supervisor/assets/sweep-supervisor.service ~/.config/systemd/user/sweep-supervisor.service
sed -i "s|__SUPERVISOR_PY__|/absolute/path/to/sweep-supervisor/scripts/supervisor.py|" ~/.config/systemd/user/sweep-supervisor.service
systemctl --user daemon-reload && systemctl --user enable --now sweep-supervisor.service
loginctl enable-linger "$USER"
systemctl --user status sweep-supervisor.service --no-pager
```

Point the unit at a **stable** copy of `supervisor.py` (the installed skill path) and re-point it
after a plugin release that moves it; `rig-sync`, `rig-board`, and `environment-sync` must sit next
to it, as installed. The unit's environment needs the agent executable on `PATH` and the same SSH
agent or keys an interactive shell uses. `python3 supervisor.py check` must pass as the service
user before the first wave; `sweep-dispatch` refuses to launch otherwise.

## State

| where | what |
|---|---|
| `<project>/.waves/_state/<wave_id>/queue.json` | the ordered queue, pool, constraints, per-run state |
| `…/ledger.jsonl` | append-only event log: every assignment, outcome, fault, steal, question, tick |
| `…/snapshot.json`, `…/table.md` | the last cycle's view; `table` re-renders it with a fresh written-time |
| `…/decisions.md` | one line per agent decision, stamped with model and effort |
| `…/questions.json` | non-blocking questions and their answers |
| `…/agent-logs/` | one log per tick |
| `<rig repo_path>/.waves/_state/<wave_id>/lanes/gpu<ids>/{queue,running,finished}` | a lane's buffer; `drain` ends the lane, `../offload` makes exit 88 wait |
| `<board root>/supervisor/<project>__<wave_id>.json` | pointer that makes the service supervise the wave |

`.waves/` is already ignored by every supported project, and `_state` can never be a wave id, so
no project needs a change. `finish --confirm` removes the pointer when a wave is terminal
(`--abandon` only when the user gave it up); the state directory stays as the wave's record.
