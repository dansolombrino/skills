---
name: progress-updates
description: Post a fixed status and ETA table in the chat every few minutes (default 5) for any long-running task without being asked - file transfers (rsync, scp, cloud copies), downloads, single training or evaluation runs, builds, or any background command expected to take more than a few minutes. Use whenever such a task is started or is already running and being watched, or when the user asks for status, an ETA, progress, or "updates every N minutes"; also to change the cadence or stop the updates. Do not use for waves launched through sweep-dispatch (sweep-supervisor reports those), or for fleet-wide GPU occupancy (rig-board).
---

# progress-updates

While anything long-running is in flight, the chat shows the same table on a fixed cadence until
it ends. The user never has to ask for it. Numbers come from `scripts/progress.py`, not from
reading logs by eye.

**Resolving this skill's own files.** `scripts/progress.py` means *this skill's installed
directory* — the folder holding the SKILL.md you are reading — not the project. Use that
directory's absolute path.

```bash
# Absolute path to this skill's own scripts/progress.py; see "Resolving this skill's own files".
PROGRESS=/absolute/path/to/progress-updates/scripts/progress.py
python3 "$PROGRESS" start --every 5                       # prints the board id; one board per chat
python3 "$PROGRESS" add --board <id> --name "<label>" --kind rsync --log <file> [--exit-file <file>] [--host <ssh host>]
python3 "$PROGRESS" add --board <id> --name "<label>" --kind log   --log <file> [--pattern '(?P<done>\d+) of (?P<total>\d+)'] [--exit-file <file>] [--pid <n>]
python3 "$PROGRESS" add --board <id> --name "<label>" --kind size  --path <dest> (--total 62G | --total-of <src> [--total-of-host <h>])
python3 "$PROGRESS" table --board <id>                    # the status report
python3 "$PROGRESS" remove --board <id> --name "<label>"  # stop watching one task
python3 "$PROGRESS" stop --board <id>                     # delete the board
```

## Scope

- Watch every long-running task this chat starts, and any already-running task the user points
  at. A task that will plausibly finish within about two minutes needs no board.
- A wave dispatched through `sweep-dispatch` belongs to `sweep-supervisor`: paste its table
  instead, and never put the wave's runs on a board as well. Other runs, such as a single
  experiment or an eval started by hand, go on the board.

## Make the task measurable at launch

Do this when starting the command; afterwards it is usually too late.

1. Send output to a log file, and record the exit code next to it:
   `( <command> ; echo $? > <log>.exit ) > <log> 2>&1`, run through the host's background-command
   capability or `nohup … &`. Pass `--exit-file <log>.exit`, so done or failed is read from the
   exit code instead of guessed.
2. For rsync, add `--info=progress2 --no-inc-recursive` so the log carries one whole-transfer
   percentage and rsync's own ETA (`--kind rsync`).
3. For a training or eval loop, a `tqdm` bar or an `epoch a/b`/`step a/b` line is parsed
   automatically (`--kind log`). If the log prints neither, give `--pattern` with named groups
   `done` and `total`.
4. For a copy that prints nothing useful, such as `scp` or a cloud CLI, compare the destination
   size to the source (`--kind size`).
5. Something on another machine goes in with `--host <ssh alias>`. Every read goes through
   non-interactive ssh.

For a task already running without a log, use whatever it exposes: its existing log, its
destination size, or `--pid` for liveness only. Say once which reading the ETA rests on.

## Cadence

1. Run `start --every <N>`, with N from the user's request or else 5, and `add` every task, then
   post the first table right away.
2. Arm the host's scheduled wake-up primitive for ticks every N minutes, anchored to the start;
   the header's `next` time comes from the same anchor. Never implement the cadence with shell
   `sleep` or a blocking wait. If the host has no such primitive, say so once and offer the table
   on request instead.
3. On every tick, even when nothing changed, run `table` and **paste its output verbatim**. Do not
   restyle it, re-derive numbers, or add columns. After it, add at most two sentences, and only
   when something needs the user: `stalled`, `error in log`, `failed`, `ended (no exit code)`,
   an `unavailable` ETA, or an ETA that moved a lot.
4. A new long-running task started later in the chat goes on the same board (`add`), not a
   second board. "every N minutes" from the user changes the cadence: run `stop`, then `start` a
   new board with the new `--every` and re-`add` the tasks, and re-arm the wake-up.
5. When a task fails, post the table, show the last 20 log lines, and ask before retrying.
   Keep reporting the other tasks.
6. When the header says `all tasks finished — stop the updates`, post that final table, cancel
   the scheduled wake-up, and run `stop`. "stop the updates" from the user does the same at any
   time; the tasks keep running.

## Reading the table

- `progress` is percent plus done/total: bytes for transfers and sizes, counts for runs.
- `ETA (basis)` is a clock time. `linear, Nm` comes from progress over the last N minutes;
  `rsync` or `tqdm` is the tool's own estimate, used until two samples exist. Otherwise the cell
  says why there is none. Never replace it with an estimate of your own.
- `state` is `ok`, `stalled Nm` (no progress for two ticks, or `table --stall <min>`),
  `error in log`, `done`, `failed (exit N)`, `ended (no exit code)`, or an unreadable log or path.
  `done`, `failed`, and `ended` are terminal and freeze the row at its final values.
