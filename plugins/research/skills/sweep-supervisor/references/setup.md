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
tick_every = 1800            # seconds between scheduled agent ticks; events wake it at once
idle_tick_every = 21600      # with nothing new since the last good tick, a real tick only this often
agent_timeout = 900          # a tick that runs longer is stopped; the next one starts fresh
closing_timeout = 2700       # the same for a terminal wave's closing tick, which does the most work
followup_timeout = 10800     # a follow-up (post-wave plots, fix-and-redispatch) that runs longer is stopped
notify_command = ["curl", "-s", "-d", "{text}", "https://ntfy.sh/<private topic>"]   # optional; see Notices

[supervisor.agent]
model = "<model alias or id>"
effort = "<low|medium|high|xhigh|max>"
command = ["<headless agent executable>", "...", "{prompt}"]
health_command = ["<a command whose exit code says the agent can run>"]   # optional, no default; see Agent health
```

The agent is woken at once by every event that needs judgment — a failed run or job, a lane
stopped, blocked or relaunched, a rig down or back, a run whose heartbeat went stale
(`heartbeat-stale`, once per episode), a lane idle with nothing to take while another still holds
runs (`lane-idle-tail`, once per episode), a collect, offload or submission failure, an answer, the
wave turning terminal — so `tick_every` is only a safety net. An explicit value is respected.

A scheduled tick is skipped while nothing needs an agent: no `budget-cap` or `checkout-off-branch`
since the last tick that exited cleanly began, and no edit of `EXPERIMENTS.md` by a person since a
tick of this project last ended. Routine progress (runs assigned, started, done, jobs submitted) is
not news: the service settles it. A real tick (`idle tick`) still runs every `idle_tick_every`. A
failed tick is never idle: the next one runs at `tick_every`, and a terminal wave no tick has
finished keeps that period too. What a script cannot see waits for the next real tick, so it can
take up to `idle_tick_every`: one of our cards free and not yet a lane, a run stalled under a live
heartbeat, and the rows of `EXPERIMENTS.md` (reconciled by every tick that does run, and at the end).

Two waves of one project never tick at once (`<project>/.waves/_state/tick.lock`): each tick edits
`EXPERIMENTS.md` and commits in the same checkout. A tick held back by a sibling's runs the cycle
after it ends.

Nothing the service runs can hold it forever: a shell snippet on a rig is cut after two minutes
(the rig then counts as not answering), a `rig-sync collect` or `offload` pass and a lane's
revision/environment check after fifteen. A cut `offload` pass keeps its partial files and resumes
next cycle; a wave whose cycle fails or crashes is skipped that pass and the others go on.

## Several waves, one fleet

A wave may use only the cards its pool lists, and a card runs one wave's lane at a time. When
several live waves want the same card:

- The wave with the higher `priority` goes first. `priority` is an integer in the manifest (default
  0) and only the user changes it later: `priority --wave <id> --set <n> --reason "user: ..."`.
- At equal priority the wave with clearly less work left goes first (at least 20% less, counted in
  weight-1.0 seconds of its unfinished runs): a short wave is not stuck behind a long one, and the
  long one loses little. Waves closer than that leave each other's cards alone, so a card does not
  change hands every run.
- A hand-over happens between runs, never by stopping one: the wave that goes after lets its run in
  flight finish, takes back the run it had buffered, drains the lane and releases the card
  (`lane-yielded`, and the lane reads `waiting for board: yielded to …`). It asks for the card back
  and gets it when the other wave has nothing left for it.
- A free card is left alone by a wave while one that goes first asks for it.

A wave asks by keeping its standing (project, wave, priority, work left) in
`<board root>/supervisor/requests/<rig>__gpu<ids>.json`, renewed every cycle; a request not renewed
for ten minutes lapses, so a wave that ended, lost its rig or stopped wanting the card never keeps
one idle. A lane that could not start (`blocked`) asks for nothing. To keep a card away from every
wave (ad-hoc work, a colleague), `rig-board hold` it.

ETA notes: a lane `waiting for board` never makes the wave ETA unavailable while others run, and
cluster jobs not started make it a lower bound (`at the earliest: N cluster jobs not started`).

`command` is an argv list, never a shell string. `{prompt}`, `{model}`, and `{effort}` are
substituted per tick; `{prompt}` is required. The tick runs with the project root as its working
directory and `SWEEP_SUPERVISOR_MODEL`/`SWEEP_SUPERVISOR_EFFORT` in its environment, so every
`decide` line and the table header name the profile that produced them; `SWEEP_SUPERVISOR_SCRIPT`,
also in the prompt, is the absolute path of the running script. Every tick is a **fresh**
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
health_command = ["claude", "auth", "status"]
```

`{prompt}` goes right after `-p`: `--allowedTools` is variadic and would consume a prompt placed
after its value, leaving the tick with no prompt. The supervisor refuses that ordering whenever it
loads the registry, so `check` fails on it.

`claude auth status` exits 0 when logged in and 1 when not, so it fails once a login has ended, not
while one is about to.

A run started with `-p` and a text prompt stops a background command after 10 minutes by default, and
at 2 hours at most, while a follow-up may run `followup_timeout` (3 hours). Match them in the service's
environment, in milliseconds, then restart the service:

```ini
# ~/.config/systemd/user/sweep-supervisor.service.d/bash-limits.conf
[Service]
# the longest a command may be given: followup_timeout, 10800 s
Environment=BASH_MAX_TIMEOUT_MS=10800000
# what a command gets when it asks for nothing: 30 minutes, the interactive default
Environment=BASH_DEFAULT_TIMEOUT_MS=1800000
```

Raise `BASH_MAX_TIMEOUT_MS` with `followup_timeout`. The default also holds a foreground command that
long before it moves to the background, so a hung command can use up a tick's `agent_timeout` or hold
a follow-up longer; both limits still apply. Other hosts ignore these variables.

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

## Agent health

Two failed ticks in a row, timeouts included, raise a `⚠` line and an `agent-failing` notice, once per
streak; the next good tick clears it. A declared `health_command` is run by `check` and hourly by the
service. Only its exit code counts: its output is thrown away unread, since such a command reports on
credentials. A failure is a `⚠` on every wave and a notice when it starts. Plain `check` only prints
it, so a login cannot keep the service from starting, since it feeds and recovers lanes with no agent;
`check --service` and `register --confirm` refuse on it.

## Notices

`notify_command` is an argv list run with `{text}` substituted (and `{context}`, when present, with
a JSON object: `kind`, `silent`, and for a wave's notice `project`, `root`, `wave`, `body` without
the wave prefix, the writing agent's `session`, and an `ask`'s `question`), for every `ask` and `say`,
every follow-up report, a finished wave, failed runs, a rig down, a lane that cannot start, a cluster submission
failure, failing agent ticks, a project checkout off its branch, and a certificate about to expire, expired,
renewed, or whose renewal failed. It is optional, and reaches the user with no
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
cert_renew_command = "systemctl --user start cineca-cert-renew.service"   # optional
```

`cert_renew_command`, a string split like a shell line but run with no shell, or an argv list, runs
when the certificate enters that last hour (or is found expired) while a wave uses the rig: at most
once per certificate, across waves. It passes only by exiting 0 and leaving a later expiry; only its
exit code is kept, so run it by hand to see why it failed. The ledger records `cert-renewed` or
`cert-renew-failed`, and the notice says which. After a failure the `⚠` line and the expired notice go
out as they do with no command. `cineca-login` provides one for Leonardo; a scheduled renewal keeps
the certificate out of this window, so the command is its backstop.

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

`register --confirm` reruns what it can in seconds on the hub, and registers nothing on a failure:
`check --service` (the service, the agent profile, `health_command`) and whether the project checkout
is on its `sync.toml` branch. The other dispatch gates stay with `sweep-dispatch`: `collect --check`,
`rig-sync activity` and both `doctor`s ssh to every rig, and the run_id plan is a call into the
project's code over the planned configs.

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
| `<board root>/supervisor/service/agent-health.json` | the last hourly `health_command` result: its time and what failed, never its output |
| `<board root>/supervisor/service/cert-renewals.json` | per rig, the certificate expiry a renewal was last tried for, and how it went |

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
