---
name: sweep-supervisor
description: Supervise a dispatched Research 2.0 wave from the hub without depending on any open chat - a hub service feeds each GPU lane from one pull queue so load balances itself, relaunches lanes after a rig fault, offloads disk-constrained rigs, keeps the fleet board current, renders a per-GPU status and ETA table, and wakes a fresh unattended agent every ten minutes and on every failure to judge what rules cannot (diagnose failures, move or steal runs across GPUs and rigs, fetch datasets, fix walltimes, ask the user without blocking). Use when a wave is running or about to be launched, when the user asks for status, ETA, a ten-minute update, to monitor or babysit a sweep, to rebalance GPUs, to recover after a crash or reboot, to answer a supervisor question, or when woken as the unattended supervisor agent for a tick. Do not use to design, generate, or authorize a wave (sweep-dispatch), or for fleet-wide GPU occupancy across projects (rig-board).
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
python3 "$SUPERVISOR" --root <project root> table --wave <wave_id>          # the status report
python3 "$SUPERVISOR" --root <project root> ledger --wave <wave_id>         # what happened since the last tick
python3 "$SUPERVISOR" --root <project root> answer --wave <wave_id> --id q1 --text "<the user's answer>"
python3 "$SUPERVISOR" waves                                   # every wave the service is supervising
```

## Who does what

| | hub service (`serve`, every cycle) | unattended agent (every tick, and on events) | chat |
|---|---|---|---|
| lanes | start, feed, relaunch after a fault, drain | unblock, admit a new in-envelope lane | — |
| runs | assign in queue order to the lane free first; settle outcomes from the artifact | reorder, pin, exclude, requeue, steal, constrain | — |
| cluster jobs | submit under the caps, resubmit machine faults, resize a `TIMEOUT` from measured progress, pull a `PENDING` job to an idle lane | budget and walltime judgment | — |
| disk | `rig-sync offload` pass on declared rigs | turn offload on or off for a rig | — |
| tracking | fleet board claim/refresh/release, snapshot, table | EXPERIMENTS.md (single writer during a wave), `decide` log, journal | paste the table |
| the user | — | `ask` without blocking | relay `answer` |

The queue is ordered once by dispatch. A lane holds at most one run in flight plus one buffered,
so a fast card simply takes more runs: a wrong initial estimate corrects itself, and an unstarted
run can still be re-routed. The hub reaches rigs; rigs never reach the hub.

## In a chat

1. **Never block while a wave is non-terminal.** Do not use a blocking-question tool and do not
   end the turn waiting on the user: a blocked chat cannot print, and an unanswered question at
   02:00 must cost nothing. Monitoring does not depend on the chat, so a stalled chat loses only
   the printout.
2. Arm the host's scheduled wake-up primitive for exact 600-second ticks anchored to the launch
   time; collection or message latency must not drift later ticks, and an urgent report does not
   reset `next_update`. Never implement the cadence with shell `sleep`. If the host has no such
   primitive, say so once: the table stays available from the command above, in
   `.waves/_state/<wave_id>/table.md`, and in the fleet viewer.
3. At every tick, even if nothing changed, run `table --wave <wave_id>` and **paste its output
   verbatim** — it already opens with `Status written <timestamp> —` (`YYYY-MM-DD at HH:MM`, hub local time), one row per GPU. Add at most
   two sentences of your own. Do not re-derive, re-poll, or restyle it, and never write a probe
   script of your own.
4. A `Needs you` line is a supervisor question. When the user answers in chat, record it with
   `answer`; the service wakes the agent on it. Until then the stated status quo holds.
5. When the header says the wave finished, post the final table, the prune proposal
   (`rig-sync prune --dry-run`, user-approved as always), and stop the schedule.
6. A direct request ("move X to behemoth", "stop using the 3080") goes through the same commands
   the agent uses (`queue`, `lane`, `steal`), with `--reason "user: ..."`.

## Agent profile

The unattended agent's model and reasoning effort are declared once, in the registry
(`[supervisor.agent]`, [references/setup.md](references/setup.md)); they are never defaulted, and
every `decide` line and the table header name them. If a chat ever starts a helper agent for
supervision work, request that same declared profile rather than the host's default.

## Authority

Standing, per wave, granted at the dispatch gate and recorded in the wave README: inside the
wave's **pool** (its rigs, GPU sets, cluster caps) the supervisor may start, stop, move, requeue,
and steal this wave's own runs, resubmit and cancel this wave's own jobs by id, fetch data into
declared caches, and let `rig-sync offload` free verified checkpoints — without asking. A
user-granted `behemoth` card is part of the pool for the whole wave.

Always the user's, asked with `ask` while the status quo holds: any rig, card, or budget outside
the pool; a `behemoth` card without a grant for this wave; anything belonging to another project
or another user; deleting anything `offload` and `prune` do not cover; superseding another wave.

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
