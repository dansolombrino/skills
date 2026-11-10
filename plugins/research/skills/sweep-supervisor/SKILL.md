---
name: sweep-supervisor
description: Supervise a dispatched Research 2.0 wave from the hub with no chat needed - a hub service feeds each GPU lane from one pull queue, relaunches lanes after a rig fault, offloads and collects, keeps the fleet board current, reports to a watching chat (and any `notify_command`), and wakes a fresh unattended agent on every event that needs judgment (a failure, a stale heartbeat, an idle lane at the tail) to diagnose, move runs, fetch data, or ask without blocking; it then runs the follow-ups a wave leads to (plots and journal when it finishes, fix-and-redispatch of a deterministic engineering error). Use when a wave is running or about to be launched, when the user asks for a wave's status, ETA, or warnings, to follow a wave from a chat, to rebalance GPUs, to recover after a crash or reboot, to answer a supervisor question, or when woken as the unattended supervisor agent for a tick. Do not use to design, generate, or authorize a wave (sweep-dispatch), or for fleet-wide GPU occupancy across projects (rig-board).
---

# sweep-supervisor

A running wave is watched by a **hub service**, not by a chat. The service does everything that
must keep happening while nobody is awake; a **fresh unattended agent**, woken by the service, does
everything that needs judgment; a **chat** shows status on request and relays the user. Any chat
may attach to a running wave, including a new one after the launching chat died. Install and
registry: [references/setup.md](references/setup.md). The unattended tick:
[references/tick.md](references/tick.md).

**Resolving this skill's own files.** `scripts/supervisor.py` means *this skill's installed
directory* — the folder holding the SKILL.md you are reading — not the research project; use its absolute path. It loads `rig-sync`, `rig-board`, and
`environment-sync` by sibling path, so install them from the same plugin.

```bash
# Absolute path to this skill's own scripts/supervisor.py; see "Resolving this skill's own files".
SUPERVISOR=/absolute/path/to/sweep-supervisor/scripts/supervisor.py
python3 "$SUPERVISOR" check                                   # agent profile, board root, cadence, notices
python3 "$SUPERVISOR" check --service                         # and proves the hub service runs
python3 "$SUPERVISOR" --root <project root> register --wave <wave_id> --manifest <file> --dry-run
python3 "$SUPERVISOR" --root <project root> table --wave <wave_id>          # status: ETA, one line per rig, warnings
python3 "$SUPERVISOR" --root <project root> table --wave <wave_id> --full   # the per-GPU table
python3 "$SUPERVISOR" --root <project root> watch --wave <wave_id>          # returns on anything new or the wave's end
python3 "$SUPERVISOR" --root <project root> watch --all                     # every live wave of the project
python3 "$SUPERVISOR" --root <project root> ledger --wave <wave_id>         # since the last good tick began
python3 "$SUPERVISOR" --root <project root> answer --wave <wave_id> --id q1 --text "<the user's answer>"
python3 "$SUPERVISOR" --root <project root> say --wave <wave_id> --text "<the agent's words to the user>"
python3 "$SUPERVISOR" --root <project root> lane add --wave <wave_id> --rig <rig> --gpu <ids> --reason "<why>"
python3 "$SUPERVISOR" --root <project root> followup --wave <wave_id> --kind fix --text "<diagnosis with evidence>"
python3 "$SUPERVISOR" waves                                   # every wave the service is supervising
python3 "$SUPERVISOR" followups                               # every follow-up, its state and report
```

## Who does what

| | hub service (`serve`, every cycle) | unattended agent (on events; a safety tick) | chat |
|---|---|---|---|
| lanes | start, feed, relaunch after a fault, drain | unblock, admit a new lane on one of our pool cards | — |
| runs | give each to the lane finishing it first; settle outcomes from the artifact | reorder, pin, exclude, requeue, steal, constrain | — |
| cluster jobs | submit under the caps, resubmit machine faults, resize a `TIMEOUT` from measured progress, pull a `PENDING` job to an idle lane | budget and walltime judgment | — |
| disk | `rig-sync offload` pass on offload rigs (every non-hub lane rig by default; Slurm targets such as `leonardo` excluded) | turn offload on or off for a rig | — |
| evaluations | `rig-sync collect` from every pool rig to the hub | settle a `collect-failed` identity mismatch with the user | — |
| tracking | fleet board claim/refresh/release, snapshot, status table | EXPERIMENTS.md (single writer during a wave), `decide` log, journal | status on request |
| after the wave | `finish`: prune every machine, queue the `analyze` follow-up; run follow-ups one per project | queue a `fix` follow-up for a deterministic engineering error | — |
| the user | notices through `notify_command`, if declared: wave finished, failures, rig down, blocked lane, certificate expiring | `ask` without blocking; `say` | relay `watch`; `answer` |

A lane holds one run in flight plus one buffered and takes a run only when no other live lane
finishes it sooner (rig weight, then measured rate); a buffered run another lane finishes clearly
sooner is taken back (`run-reclaimed`). A card the plan does not feed is released. The hub
reaches rigs; rigs never reach the hub.

The agent is woken at once by every event that needs judgment: a failed run or job, a lane stopped,
blocked or relaunched, a rig down or back, an answer, a collect/offload/submit failure, the wave
turning terminal, `heartbeat-stale` (a run's heartbeat older than three minutes) and `lane-idle-tail`
(a lane with nothing it can take while another still holds runs), each once per episode. The scheduled tick (`tick_every`, default 30 minutes) is only a safety net, skipped while
nothing needs it, with a real tick every `idle_tick_every` (6 hours); a failed tick is never idle.

- **Evaluations converge on the hub.** Every cycle runs `rigsync collect` for each reachable pool rig
  except the hub. A failure is one `collect-failed` event per new problem and never stops the cycle.
  A wave turning terminal collects once more from every rig; `SUP finish` collects a last time.
- **The fleet board decides first.** The service claims a card before it starts a lane there; a
  refused claim keeps the lane out (`waiting for board`, one `claim-refused` event) until the holder
  releases it. A lane whose card the board gives to another wave is not fed.

## In a chat

- **Status on request:** `table --wave <id>` (a few lines: ETA, progress, one line per rig, a `⚠`
  line per warning); `table --full` for the per-GPU table. Post it as printed. The rig-board
  viewer's **Experiments** tab shows every wave's progress.
- **Following a wave:** after `register --confirm`, and when attaching to a live wave, run `watch
  --wave <id>`, or one `watch --all` for every live wave of the project, through the host's
  background-command capability. It returns only on something not told yet (a warning, a question, a
  `say`, a follow-up report), the wave's end, or after six hours; when it does, post its output (plus
  at most one sentence) and run it again, until it says `wave finished` (`--all`: `no live wave`).
  What happened in between comes with the next run.
  On a host without background-command completion notices, say once that status is on request and in
  the dashboard.
- **A resumed chat** has lost its background watches and runs them again; session-start context
  names a live wave nothing follows.
- **Fix rounds:** watch returns once on a wave's `fix` follow-up (`… fix follow-up <id> …`)
  and once on its new wave (`continuing as wave <id> (fix round <k>)`; watch it next);
  a fix dispatching nothing ends it with its report.
- **A timed printout** only when the user asks for one: the host's scheduled wake-up at their
  interval, never a shell `sleep`.
- **Questions:** a `⚠ needs you [<id>]` line is a supervisor question (the status names only its
  id); record the user's answer with `answer` (the service wakes the agent on it). Waiting on the
  user never stops the wave; the stated status quo holds.
- **Direct requests** ("move X to behemoth", "stop using the 3080", "add gpu 3, Marco lent it") go
  through the commands the agent uses (`queue`, `lane`, `lane add`, `steal`) with
  `--reason "user: ..."`; a shared rig's card beyond ours takes the user's words with
  `--user-words`. A cluster's core-hour cap changes only when the user asks, through
  `pool --rig <cluster> --core-hours-cap <h> --reason "..."` (a raised cap resumes submission).

## Agent profile

The unattended agent's model and reasoning effort are declared once, in the registry
(`[supervisor.agent]`, [references/setup.md](references/setup.md)); they are never defaulted, and
every `decide` line and the table header name them. A helper agent a chat starts for supervision
work requests that same profile.

## Without a chat

- **Notices.** Only a declared `notify_command` pushes (`check` says); else they reach a chat running
  `watch`. One line for a finished wave, failed runs, a rig down, a lane that cannot start, a
  cluster submission failure (identical ones at most hourly, across waves), every `ask`, `say` and
  follow-up report; a failure is logged as `notify-failed`. Its `{context}` names the wave and the writing session, and marks failed runs and a rig down silent.
- **Certificates.** A rig's `ssh_cert` is watched: a notice an hour
  before it expires and one when it does, and a `⚠` line. A declared `cert_renew_command` runs first,
  once per certificate, its outcome in the ledger. Never an `ask`; the service resumes the rig when it answers.
- **Trouble ahead.** Two failed ticks in a row, a failing `health_command`, or a project checkout off
  its `sync.toml` branch (blocks relaunches and dispatch; follow-ups wait; nothing moves it: a person
  may be working there): a `⚠` line and a notice.
- **Releases.** Running from a versioned install, the service moves onto a newer installed version
  between cycles once that copy's `check` passes and no agent it started runs.
- **Held GPUs.** A lane `waiting for board` never makes the wave ETA unavailable while others run.
- **Cluster jobs.** Unstarted ones make the ETA a lower bound.

## Follow-ups

Work a wave leads to that needs no person but is too long for a tick runs as a **follow-up**: one
fresh headless agent with the same profile, started by the service, one at a time per project,
with `followup_timeout` (default three hours). Records live under `<board root>/supervisor/followups/`,
so they outlive the wave, and one interrupted by a service restart runs again.

- **`analyze`** — queued by `finish` for every wave that ends with at least one `done` run
  (`--no-analysis` skips it): re-render every plotter of the experiment (or the smallest set that
  answers its question) under the `visualizations` contract, append the `research-journal` entry,
  commit on the project's branch and push, report the plot URLs.
- **`fix`** — queued by the agent for a deterministic engineering error: a traceback, a wrong
  path, a generator or wave-script bug. It fixes the code on the hub's main checkout without
  changing what the experiment measures, reruns the smoke, commits, and dispatches a wave of
  exactly the runs the error broke, inside the same pool and standing authorization, carrying
  `fix_depth` + 1 and `fixes_wave` (the wave it replaces). The supervisor refuses a third automatic
  round in one chain; a fix that would change the science or anything reserved for the user becomes
  an `ask`.

Each follow-up ends with `followup-report --id <id> --text "<outcome and links>"`: recorded, told by
`watch`, pushed if `notify_command` is declared; one that fails or ends with no report, with its log.

## Authority

The bound is the wave README's **pool** (its rigs, GPU sets, cluster caps) and **standing
authorization**, granted at the dispatch gate. Inside it, without asking, the supervisor may
start, stop, move, requeue, and steal this wave's own runs; resubmit this wave's own jobs and cancel
by id the ones the cluster has not started (a `PENDING` job pulled to an idle lane); fetch data into
declared caches; let `rig-sync offload` free verified checkpoints and `rig-sync collect` bring
evaluation records to the hub; add a lane on one of our cards of a pool rig (`lane add`); and queue
the wave's `fix` and `analyze` follow-ups. A user-granted `behemoth` card is part of the pool for the
whole wave. **Leonardo:** core-hours are not a gate; there is no cap unless the user set one, and a
`budget-cap` event (only possible then) is reported, not asked.

**Reserved for the user**, asked with `ask` while the status quo holds (or granted in their own words): any rig, card, or budget
outside the pool; a `behemoth` card without a grant for this wave; anything of another project or
user; deleting anything `offload` and `prune` do not cover; cancelling or superseding runs outside
the standing authorization (another wave's runs, a supersede, a running Slurm job); a run_id
migration; a fix that changes the science; and settling an identity mismatch `collect` reports.

Before any kill, prove ownership: the lane is held on the fleet board by this wave's session, the
recorded pid belongs to this user and is this run's wave script, the card is not foreign. `steal`
enforces this and refuses otherwise; never work around a refusal with a hand-written `kill`.

## Stop conditions

- `check --service` fails (agent profile undeclared, service not running): a wave is not
  launched (`sweep-dispatch` gate; `register --confirm` reruns it) and a chat says so.
- A wave with no `.waves/_state/<wave_id>/queue.json` (launched before this contract) may be
  reconciled by `experiments-tracking`, never supervised or recovered here.
- Never edit `queue.json`, a lane buffer, or the board by hand; every change goes through the
  script so the ledger stays true.
