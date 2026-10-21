---
name: sweep-supervisor
description: Supervise a dispatched Research 2.0 wave from the hub with no chat needed - a hub service feeds each GPU lane from one pull queue, relaunches lanes after a rig fault, offloads and collects, keeps the fleet board current, renders a short ETA printout, pushes notices to the user's phone, and wakes a fresh unattended agent every two minutes and on every failure to judge what rules cannot (diagnose, move runs, fetch data, ask without blocking); it then runs the follow-ups a wave leads to (plots and journal when it finishes, fix-and-redispatch of a deterministic engineering error). Use when a wave is running or about to be launched, when the user asks for status, ETA, periodic updates or their interval, to monitor or babysit a sweep, to rebalance GPUs, to recover after a crash or reboot, to answer a supervisor question, or when woken as the unattended supervisor agent for a tick. Do not use to design, generate, or authorize a wave (sweep-dispatch), or for fleet-wide GPU occupancy across projects (rig-board).
---

# sweep-supervisor

A running wave is watched by a **hub service**, not by a chat. The service does everything that
must keep happening while nobody is awake; a **fresh unattended agent**, woken by the service, does
everything that needs judgment; a **chat** only shows the table and relays the user. Any chat may
attach to a running wave, including a new one after the launching chat died. Canon:
`../research-project-init/references/conventions.md`. Install and registry: [references/setup.md](references/setup.md).
The unattended tick: [references/tick.md](references/tick.md).

**Resolving this skill's own files.** `scripts/supervisor.py` means *this skill's installed
directory* — the folder holding the SKILL.md you are reading — not the research project. Use that
directory's absolute path. It loads `rig-sync`, `rig-board`, and `environment-sync` by sibling path,
so install them from the same plugin.

```bash
# Absolute path to this skill's own scripts/supervisor.py; see "Resolving this skill's own files".
SUPERVISOR=/absolute/path/to/sweep-supervisor/scripts/supervisor.py
python3 "$SUPERVISOR" check                                   # agent profile, board root, cadence
python3 "$SUPERVISOR" --root <project root> register --wave <wave_id> --manifest <file> --dry-run
python3 "$SUPERVISOR" --root <project root> table --wave <wave_id>          # the chat printout: ETA, one line per rig
python3 "$SUPERVISOR" --root <project root> table --wave <wave_id> --full   # the per-GPU table
python3 "$SUPERVISOR" --root <project root> ledger --wave <wave_id>         # what happened since the last tick
python3 "$SUPERVISOR" --root <project root> answer --wave <wave_id> --id q1 --text "<the user's answer>"
python3 "$SUPERVISOR" --root <project root> lane add --wave <wave_id> --rig <rig> --gpu <ids> --reason "<why>"
python3 "$SUPERVISOR" --root <project root> followup --wave <wave_id> --kind fix --text "<diagnosis with evidence>"
python3 "$SUPERVISOR" waves                                   # every wave the service is supervising
python3 "$SUPERVISOR" followups                               # every follow-up, its state and report
```

## Who does what

| | hub service (`serve`, every cycle) | unattended agent (every tick, and on events) | chat |
|---|---|---|---|
| lanes | start, feed, relaunch after a fault, drain | unblock, admit a new in-envelope lane | — |
| runs | assign in queue order to the lane free first; settle outcomes from the artifact | reorder, pin, exclude, requeue, steal, constrain | — |
| cluster jobs | submit under the caps, resubmit machine faults, resize a `TIMEOUT` from measured progress, pull a `PENDING` job to an idle lane | budget and walltime judgment | — |
| disk | `rig-sync offload` pass on offload rigs (every non-hub lane rig by default; Slurm targets such as `leonardo` excluded, and `supervisor.py` rejects offload entries that are not lane rigs) | turn offload on or off for a rig | — |
| evaluations | `rig-sync collect` from every pool rig to the hub | settle a `collect-failed` identity mismatch with the user | — |
| tracking | fleet board claim/refresh/release, snapshot, printout and table | EXPERIMENTS.md (single writer during a wave), `decide` log, journal | paste the printout |
| after the wave | `finish`: prune every machine, queue the `analyze` follow-up; run follow-ups one per project | queue a `fix` follow-up for a deterministic engineering error | — |
| the user | push notices (`notify_command`): wave finished, failures, rig down, blocked lane, certificate expiring | `ask` without blocking | relay `answer` (optional) |

The queue is ordered once by dispatch. A lane holds at most one run in flight plus one buffered,
so a fast card simply takes more runs: a wrong initial estimate corrects itself, and an unstarted
run can still be re-routed. The hub reaches rigs; rigs never reach the hub.

- **Evaluations converge on the hub.** Every cycle the supervisor runs
  `rigsync collect --wave <id> --machine <rig> --confirm` for each reachable pool rig except the
  hub, recording the result in `collect_last` (snapshot `collect`). A failure is one
  `collect-failed` ledger event per new problem (urgent: it wakes the agent) and never stops the
  cycle. When every run is terminal it collects once more, from every rig not already collected
  successfully in that cycle, before marking the wave terminal; `wave-terminal` carries `collect_failed` with any rig that still failed. `SUP finish`
  collects one last time before closing the wave.

The fleet board decides first: the service claims a card before it starts a lane there, and a
refused claim keeps the lane out (state `waiting for board`, one `claim-refused` event per refusal)
until the holder releases it. A lane whose card the board gives to another wave is not fed.

## In a chat

The printout starts by itself: right after `register --confirm`, the command prints a `NEXT:` line
and the plugin's post-register hook tells the chat to post the first table and arm the wake-up in
the same turn, without asking. The same hook reminds every session in this project of each live
wave (at session start, and on prompts at most every five minutes): when the reminder names a
wave, resume its printout and re-arm the wake-up if this chat has none.

1. **Never block while a wave is non-terminal.** Do not use a blocking-question tool and do not
   end the turn waiting on the user: a blocked chat cannot print, and an unanswered question at
   02:00 must cost nothing. Monitoring does not depend on the chat, so a stalled chat loses only
   the printout.
2. **Update interval: the user's N minutes, else 1.** Take N from the user's request or one
   already given in this chat; with the default, say once that updates come every minute and
   "every N minutes" changes it, which re-arms. This interval is the chat's alone; the unattended
   agent keeps the registry's `tick_every`.
   Arm the host's scheduled wake-up primitive for exact N-minute ticks anchored to the launch
   time; collection or message latency must not drift later ticks, and an urgent report does not
   reset `next_update`. Never implement the cadence with shell `sleep`. If the host has no such
   primitive, say so once: the table stays available from the command above, in
   `.waves/_state/<wave_id>/table.md`, and in the fleet viewer.
3. At every tick, even if nothing changed, run `table --wave <wave_id>` and **paste its output
   verbatim**. It is a few lines: `Status written <timestamp> —` (`YYYY-MM-DD at HH:MM`, hub
   local time), the wave ETA with its countdown and the done count; a progress bar of emoji squares (red under a third, yellow under two thirds, green beyond); one line per
   rig (GPUs or Slurm jobs, runs done, when it falls free or, for Slurm, jobs running and waiting,
   `← last to finish`) only when the pool has several rigs; and a `⚠` line only for a stale, blocked, or down lane, a failed run, or a question. Add
   nothing when there is no `⚠` line, and at most one sentence when there is. Do not re-derive,
   re-poll, or restyle it, and never write a probe script of your own. The per-GPU table is
   `table --full` (also `.waves/_state/<wave_id>/table.md`); post it only when the user asks.
4. A `⚠ needs you [<id>]` line is a supervisor question. When the user answers in chat, record it with
   `answer`; the service wakes the agent on it. Until then the stated status quo holds.
5. When the printout says `finished`, post it and stop the schedule. `finish` already pruned the
   wave and queued its `analyze` follow-up, whose report (plot links included) reaches the user as
   a notice; `followups` shows it too.
6. A direct request ("move X to behemoth", "stop using the 3080", "add gpu 3, Marco lent it")
   goes through the same commands the agent uses (`queue`, `lane`, `lane add`, `steal`), with
   `--reason "user: ..."`; a shared rig's card beyond ours takes the user's words with
   `--user-words`. A change of a cluster's
   tracked core-hour cap goes through `pool --rig <cluster> --core-hours-cap <h> --reason "..."`
   (ledger event `pool-cap`; a raised cap resumes submission), only when the user asks for one.

## Agent profile

The unattended agent's model and reasoning effort are declared once, in the registry
(`[supervisor.agent]`, [references/setup.md](references/setup.md)); they are never defaulted, and
every `decide` line and the table header name them. If a chat ever starts a helper agent for
supervision work, request that same declared profile rather than the host's default.

## Without a chat

A wave needs no chat from dispatch to its plotted results. What used to bring the user back:

- **Notices, not questions.** With `notify_command` declared ([references/setup.md](references/setup.md)),
  the service pushes one line for a finished wave, failed runs, a rig down, a lane that cannot
  start, a cluster submission failure, every `ask`, and every follow-up report — identical notices
  at most once an hour, across waves.
- **Certificates.** A rig's `ssh_cert` (a cluster login certificate, such as Leonardo's 12-hour
  one) is watched: a notice an hour before it expires and one when it does, and a `⚠` line in the
  printout. Renewing it is the user's 2FA, so it is never an `ask`, and nothing waits on an answer:
  the service resumes the rig the cycle it answers again. Jobs on the cluster keep running meanwhile.
- **Releases.** Running from a versioned plugin install, the service re-executes itself onto a newer
  installed version between cycles once that copy's `check` passes and no agent it started is
  running, so a release never leaves it on old code.
- **Held GPUs.** A pool lane held by another wave or project waits (`waiting for board`) and starts
  when released; it never makes the wave ETA unavailable while another lane can run.
- **Cluster jobs.** The wave ETA also waits for the last running cluster job, from that job's own
  progress; a running job with no estimate makes it unavailable, and a pending job's start is the
  scheduler's call, so it is not counted.

## Follow-ups

Work a wave leads to that needs no person but is too long for a tick runs as a **follow-up**: one
fresh headless agent with the same profile, started by the service, one at a time per project,
with `followup_timeout` (default three hours). Records live under `<board root>/supervisor/followups/`,
so they outlive the wave, and one interrupted by a service restart runs again.

- **`analyze`** — queued by `finish` for every wave that ends with at least one `done` run (`--no-analysis`
  skips it). It re-renders every plotter of the experiment (or creates the smallest set that answers
  the experiment's question) under the complete `visualizations` contract, appends the
  `research-journal` entry, commits, and reports the plot URLs.
- **`fix`** — queued by the unattended agent (`followup --kind fix --text "<diagnosis>"`) for a
  deterministic engineering error: a traceback, a wrong path, a generator or wave-script bug. The
  follow-up fixes the code on the hub's main checkout without changing what the experiment
  measures, reruns the smoke, commits, and dispatches a wave of exactly the runs the error broke,
  inside the same envelope, carrying `fix_depth` + 1. The supervisor refuses a third automatic
  round in one chain; a fix that would change the science or anything on the **Always ask** list
  becomes an `ask` instead.

Each follow-up ends with `followup-report --id <id> --text "<outcome and links>"`, which is
recorded and pushed to the user; a follow-up that fails or ends without a report is pushed with
its log path.

## Authority

Standing, per wave, granted at the dispatch gate and recorded in the wave README: inside the
wave's **pool** (its rigs, GPU sets, cluster caps) the supervisor may start, stop, move, requeue,
and steal this wave's own runs, resubmit this wave's own jobs and cancel the ones the cluster has
not started yet (a `PENDING` job pulled to an idle lane) by id, fetch data into
declared caches, let `rig-sync offload` free verified checkpoints, `rig-sync collect`
evaluation records to the hub, add a lane on one of our cards of a pool rig inside the envelope
(`lane add`), and queue the wave's `fix` and `analyze` follow-ups — without asking. A user-granted `behemoth` card is part of the pool
for the whole wave. **Leonardo:** core-hours are not a gate; the wave runs with no core-hour cap
unless the user set one, and a `budget-cap` event (only possible then) is reported, not asked.

Always the user's, asked with `ask` while the status quo holds: any rig, card, or budget outside
the pool; a `behemoth` card without a grant for this wave; anything belonging to another project
or another user; deleting anything `offload` and `prune` do not cover; cancelling or superseding
runs outside this standing authorization (another wave's runs, a supersede, a running Slurm job);
a run_id migration (`run-identity` refuses one while the wave is live anyway); and settling an identity mismatch `collect` reports between a
rig and the hub.

Before any kill, prove ownership: the lane is held on the fleet board by this wave's session, the
recorded pid belongs to this user and is this run's wave script, the card is not foreign. `steal`
enforces this and refuses otherwise; never work around a refusal with a hand-written `kill`.

## Stop conditions

- `check` fails, the agent profile is undeclared, or the service is not running: a wave is not
  launched (`sweep-dispatch` gate) and an attached chat says so instead of promising updates.
- A wave with no `.waves/_state/<wave_id>/queue.json` (launched before this contract) may be
  reconciled by `experiments-tracking`, never supervised or recovered here.
- Never edit `queue.json`, a lane buffer, or the board by hand; every change goes through the
  script so the ledger stays true.
