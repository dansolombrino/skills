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
tick_every = 1800            # safety-net seconds between agent ticks when nothing happens; events wake it at once
agent_timeout = 900          # a tick that runs longer is stopped; the next one starts fresh
followup_timeout = 10800     # a follow-up (post-wave plots, fix-and-redispatch) that runs longer is stopped
notify_command = ["curl", "-s", "-d", "{text}", "https://ntfy.sh/<private topic>"]   # optional; see Notices

[supervisor.agent]
model = "<model alias or id>"
effort = "<low|medium|high|xhigh|max>"
command = ["<headless agent executable>", "...", "{prompt}"]
```

The agent is woken at once by every event that needs judgment — a failed run or job, a lane
stopped, blocked or relaunched, a rig down or back, a run whose heartbeat went stale
(`heartbeat-stale`, once per episode), a lane idle with nothing to take while another still holds
runs (`lane-idle-tail`, once per episode), a collect, offload or submission failure, an answer, the
wave turning terminal — so `tick_every` is only a safety net. An explicit value is respected.

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

## Notices

`notify_command` is an argv list run with `{text}` substituted, for every `ask`, every follow-up
report, a finished wave, failed runs, a rig down, a lane that cannot start, a cluster submission
failure, and a certificate about to expire or expired. It is what lets the user stay away from
chats: point it at a phone. `ntfy` needs only the app and an unguessable topic name (anyone who
knows the topic reads it, so keep it private); the `phone-bridge` skill's Telegram bot
(`["python3", "<phone-bridge>/scripts/bridge.py", "send", "{text}"]`) also renews a cluster's SSH
certificate from the phone; any command that takes the text as an argument
works (`notify-send` for a desktop, a mail or chat CLI). Without it, notices exist only in the
printout and `followups`.

## Certificates

A rig whose SSH access is a short-lived certificate declares it in its registry entry, and the
supervisor warns an hour before it expires and when it does:

```toml
[machines.leonardo]
ssh_cert = "~/.ssh/id_leonardo-cert.pub"
```

## Service

```bash
mkdir -p ~/.config/systemd/user && cp /absolute/path/to/sweep-supervisor/assets/sweep-supervisor.service ~/.config/systemd/user/sweep-supervisor.service
sed -i "s|__SUPERVISOR_PY__|/absolute/path/to/sweep-supervisor/scripts/supervisor.py|" ~/.config/systemd/user/sweep-supervisor.service
systemctl --user daemon-reload && systemctl --user enable --now sweep-supervisor.service
loginctl enable-linger "$USER"
systemctl --user status sweep-supervisor.service --no-pager
```

Point the unit at the installed skill path. From a versioned install
(`.../research/<X.Y.Z>/skills/...`) the service moves itself onto a newer installed version
between cycles, once that copy's `check` passes, so a release never needs the unit re-pointed or
restarted; a unit pointing at a development checkout never moves. `rig-sync`, `rig-board`, and
`environment-sync` must sit next to it, as installed. The unit's environment needs the agent executable on `PATH` and the same SSH
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
| `…/watch.json` | the warnings `watch` already announced to a chat (a cleared one is forgotten) |
| `…/agent-logs/` | one log per tick |
| `<rig repo_path>/.waves/_state/<wave_id>/lanes/gpu<ids>/{queue,running,finished}` | a lane's buffer; `drain` ends the lane, `../offload` makes exit 88 wait |
| `<board root>/supervisor/<project>__<wave_id>.json` | pointer that makes the service supervise the wave |
| `<board root>/supervisor/finished/<project>__<wave_id>.json` | a finished wave, kept 14 days for the rig-board viewer; never supervised |
| `<board root>/supervisor/followups/<id>.json` | one follow-up: kind, brief, state, log path, report |

`.waves/` is already ignored by every supported project, and `_state` can never be a wave id, so
no project needs a change. `finish --confirm` moves the pointer to `finished/` when a wave is terminal
(`--abandon` only when the user gave it up); the state directory stays as the wave's record. The
rig-board viewer's Experiments tab reads `snapshot.json` through this script's `brief_view`, the
same data the brief printout renders, so the page and the chat never disagree.

## Watch

`supervisor.py --root <project> watch --wave <id> [--poll 20] [--timeout 21600]` blocks, reading
only local files (the snapshot, the queue, the follow-up records) every `--poll` seconds, and
returns as soon as there is a warning it has not announced yet — any `⚠` line of the printout (a
stale, blocked, down or waiting lane, a failed run, an open question, an expiring or expired
certificate), a follow-up that failed or ended without a report, or no supervisor cycle for ten
minutes — or the wave is terminal, or the timeout passes. It prints one reason line
(`watch: new ⚠ …`, `watch: wave finished`, `watch: timeout, nothing new; run watch again`) and
then the `table` printout, and exits 0; an unregistered wave exits 2. It is what a chat runs in the
background instead of a timer.
