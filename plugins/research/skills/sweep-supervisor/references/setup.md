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
process, so nothing can expire, compact away, or block on a prompt. When `command` takes
`{session}` (an executable that accepts a preset session id), the supervisor passes a fresh UUID
per run and exports it as `SWEEP_SUPERVISOR_SESSION`, so `ask`, `say` and the ledger name the
session that wrote them.

### Install targets

Claude Code, headless, with the profile this marketplace's owner chose (Opus at high effort for
every agent):

```toml
[supervisor.agent]
model = "opus"
effort = "high"
command = ["claude", "-p", "{prompt}", "--session-id", "{session}", "--model", "{model}", "--effort", "{effort}", "--allowedTools", "Read,Edit,Write,Glob,Grep,Skill,Bash"]
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
command = ["codex", "exec", "--json", "<model and effort flags using {model} and {effort}>", "{prompt}"]
```

An unattended tick cannot answer a permission prompt, so the command must pre-authorize what a
tick uses: reading and editing inside the project, this plugin's scripts, `ssh` to the declared
rigs, `tmux`, `git`, and `date`. Prefer the narrowest allowlist the host supports over a blanket
bypass; a denied tool call fails that step of the tick, which the tick reports with `decide`.

## Notices

`notify_command` is an argv list run with `{text}` substituted (and `{context}`, when present, with
a JSON object: `kind`, `silent`, and for a wave's notice `project`, `root`, `wave`, `body` without
the wave prefix, the writing agent's `session`, and an `ask`'s `question`), for every `ask` and `say`,
every follow-up report, a finished wave, failed runs, a rig down, a lane that cannot start, a cluster submission
failure, and a certificate about to expire or expired. It is optional, and reaches the user with no
chat open. `ntfy` needs only the app and an unguessable topic name (anyone who
knows the topic reads it, so keep it private); any command that takes the text as an argument
works (`notify-send` for a desktop, a mail or chat CLI). Without it nothing is pushed: notices reach
a chat running `watch`, the printout, `followups` and the ledger; `check` prints `notices: none
declared`, and `ask` and `say` print `recorded, not pushed`. A command that fails is logged in the
ledger as `notify-failed`, and `ask` and `say` print that too.

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
mkdir -p ~/.local/share/sweep-supervisor && cp /absolute/path/to/sweep-supervisor/assets/launch_supervisor.py ~/.local/share/sweep-supervisor/launch_supervisor.py
sed -i "s|__LAUNCHER_OR_SUPERVISOR_PY__|$HOME/.local/share/sweep-supervisor/launch_supervisor.py|" ~/.config/systemd/user/sweep-supervisor.service
systemctl --user daemon-reload && systemctl --user enable --now sweep-supervisor.service
loginctl enable-linger "$USER"
systemctl --user status sweep-supervisor.service --no-pager
```

Point the unit at the launcher copied above, never at a versioned install
(`.../research/<X.Y.Z>/skills/...`): hosts delete old version directories, and a unit naming one
stops being able to start. The launcher starts the newest installed copy whose `check` passes. From
there the service moves itself onto a newer installed version between cycles, once that copy's
`check` passes, so a release never needs the unit re-pointed or restarted. To run a development
checkout, put its `scripts/supervisor.py` in the unit instead; that unit never moves. `rig-sync`,
`rig-board`, and `environment-sync` must sit next to the running copy, as installed. The unit's environment needs the agent executable on `PATH` and the same SSH
agent or keys an interactive shell uses. `python3 supervisor.py check --service` must pass as the
service user before the first wave; `sweep-dispatch` refuses to launch otherwise.

## State

| where | what |
|---|---|
| `<project>/.waves/_state/<wave_id>/queue.json` | the ordered queue, pool, constraints, per-run state |
| `…/ledger.jsonl` | append-only event log: every assignment, outcome, fault, steal, question, tick |
| `…/snapshot.json`, `…/table.md` | the last cycle's view; `table` re-renders it with a fresh written-time |
| `…/decisions.md` | one line per agent decision, stamped with model and effort |
| `…/questions.json` | non-blocking questions and their answers |
| `…/watch.json` | what `watch` already told a chat: warnings (a cleared one is forgotten), `say`s, reports, events, a fix round's steps |
| `…/watching.json` | a running `watch`'s pid and how long to believe it; the session-start facts read it |
| `…/agent-logs/` | one log per tick |
| `<rig repo_path>/.waves/_state/<wave_id>/lanes/gpu<ids>/{queue,running,finished}` | a lane's buffer; `drain` ends the lane, `../offload` makes exit 88 wait |
| `<board root>/supervisor/<project>__<wave_id>.json` | pointer that makes the service supervise the wave |
| `<board root>/supervisor/finished/<project>__<wave_id>.json` | a finished wave, kept 14 days for the rig-board viewer; never supervised |
| `<board root>/supervisor/followups/<id>.json` | one follow-up: kind, brief, state, log path, report, session |
| `<board root>/supervisor/service/heartbeat.json` | the running service's time, pid and host, each cycle; `check --service` reads it |

`.waves/` is already ignored by every supported project, and `_state` can never be a wave id, so
no project needs a change. `finish --confirm` moves the pointer to `finished/` when a wave is terminal
(`--abandon` only when the user gave it up); the state directory stays as the wave's record. The
rig-board viewer's Experiments tab reads `snapshot.json` through this script's `brief_view`, the
same data the brief printout renders, so the page and the chat never disagree.

## Watch

`supervisor.py --root <project> watch (--wave <id> | --all) [--poll 20] [--timeout 21600]` blocks,
reading only local files (the snapshot, the ledger, the questions, the queue, the follow-up records)
every `--poll` seconds, and returns as soon as there is something it has not told yet: any `⚠` line
of the printout (a stale, blocked, down or waiting lane, a failed run, an open question, an expiring
or expired certificate); a `say`; a follow-up report of any kind; a follow-up that failed or ended
without a report; an event a `notify_command` would get, also one that cleared between two watches
(a rig down, a failed run, a submission failure, a certificate); no supervisor cycle for ten minutes.
It also returns when the wave ends, once more for each follow-up it leads to, and when the timeout
passes. It prints one reason line per item (`watch: new ⚠ …`, `watch: say: …`, `watch: follow-up …
report: …`, `watch: wave finished`, `watch: timeout, nothing new; run watch again`) and then the
`table` printout, and exits 0; an unregistered wave exits 2. Each thing goes to one running watch, so
keep one per project.

`--all` follows every wave the service supervises under `--root`, one with a follow-up still to run,
and one a watch followed that it has not told finished, including a wave registered while it runs. It
returns on the first new thing in any of them with each line naming its wave, and says `watch: no
live wave …` when none remains. While it runs, either form keeps `watching.json` fresh in each wave's
state directory, which is how the session-start facts know whether a chat follows the wave. It is what a chat runs in the
background instead of a timer.
