---
name: progress-updates
description: Make a long-running task measurable and report it when something changes - file transfers (rsync, scp, cloud copies), downloads, single training or evaluation runs, builds, or any background command expected to take more than a few minutes. Use when such a task is started or is already running and being watched, or when the user asks for its status, an ETA, progress, or "updates every N minutes"; also to change or stop those updates. Do not use for waves launched through sweep-dispatch (sweep-supervisor reports those), or for fleet-wide GPU occupancy (rig-board).
---

# progress-updates

A long task is launched so it can be measured, put on a board, and then watched by a blocking
script that returns only when something changes. Quiet stretches cost no chat turns. Numbers
come from `scripts/progress.py`, not from reading logs by eye.

`scripts/progress.py` lives in this skill's installed directory, the folder holding this
SKILL.md, not in the project. Use its absolute path.

```bash
PROGRESS=/absolute/path/to/progress-updates/scripts/progress.py
python3 "$PROGRESS" start                                 # prints the board id
python3 "$PROGRESS" add --board <id> --name "<label>" --kind rsync --log <file> [--exit-file <file>] [--host <ssh host>]
python3 "$PROGRESS" add --board <id> --name "<label>" --kind log   --log <file> [--pattern '(?P<done>\d+) of (?P<total>\d+)'] [--exit-file <file>] [--pid <n>]
python3 "$PROGRESS" add --board <id> --name "<label>" --kind size  --path <dest> (--total 62G | --total-of <src> [--total-of-host <h>])
python3 "$PROGRESS" watch --board <id> [--poll 30] [--timeout 21600]   # blocks until something changes
python3 "$PROGRESS" table --board <id>                    # the status table, now
python3 "$PROGRESS" remove --board <id> --name "<label>"  # stop watching one task
python3 "$PROGRESS" stop --board <id>                     # delete the board
```

## Scope

- Watch every long-running task this chat starts, and any already-running task the user points
  at. A task that will plausibly finish within about two minutes needs no board.
- A wave dispatched through `sweep-dispatch` belongs to `sweep-supervisor`, which has its own
  watcher; never put the wave's runs on a board as well. A single experiment or an eval started
  by hand goes on the board.
- Fleet-wide GPU occupancy is `rig-board`'s job.

## Make the task measurable at launch

Do this when starting the command; afterwards it is usually too late.

1. Send output to a log file and record the exit code next to it:
   `( <command> ; echo $? > <log>.exit ) > <log> 2>&1`, run through the host's background-command
   capability or `nohup … &`. Pass `--exit-file <log>.exit`, so done or failed is read from the
   exit code instead of guessed.
2. For rsync, add `--info=progress2 --no-inc-recursive` so the log carries one whole-transfer
   percentage and rsync's own ETA (`--kind rsync`).
3. A `tqdm` bar or an `epoch a/b` / `step a/b` line is parsed automatically (`--kind log`). If
   the log prints neither, give `--pattern` with named groups `done` and `total`.
4. For a copy that prints nothing useful, such as `scp` or a cloud CLI, compare the destination
   size to the source (`--kind size`).
5. Something on another machine goes in with `--host <ssh alias>`; reads use non-interactive ssh.

For a task already running without a log, use what it exposes: its existing log, its
destination size, or `--pid` for liveness only. Say once which reading the ETA rests on.

## Watching

1. Run `start`, `add` each task, and post the first `table`. A task started later in the chat
   goes on the same board.
2. Run `watch --board <id>` through the host's background-command capability, so its completion
   brings you back. It samples every 30 seconds and returns when a task is done, failed, or
   ended, newly stalled, shows an error in its log, stays unreadable, or after 6 hours. Its first
   line is `watch: <reason>`, followed by the table.
3. When it returns, post the table as printed, plus at most two sentences when something needs
   the user. On a failure, also show the last 20 log lines and ask before retrying. If the header
   does not say `all tasks finished`, run `watch` again; on `timeout`, re-run it without comment.
4. When the header says `all tasks finished`, post that table and run `stop`. "Stop the updates"
   from the user means `stop` too; the tasks keep running. A `watch` still running then returns
   `board removed`, which needs no reply.
5. Never use shell `sleep` or a foreground wait to pace reports. If the host has no
   background-command completion notification, say once that the table is available on request.

The user can ask for the table at any time: run `table` and post it.

## Timed updates

Only when the user asks for them ("updates every N minutes"): arm the host's scheduled wake-up
primitive at their N and post `table` at each wake-up, alongside `watch`. There is no default
cadence. Passing `start --every N` puts the next update time in the header. A new N re-arms the
wake-up; "stop the updates" cancels it.

## Reading the table

- `progress` is percent plus done/total: bytes for transfers and sizes, counts for runs.
- `ETA (basis)` is a clock time. `linear, Nm` comes from progress over the last N minutes;
  `rsync` or `tqdm` is the tool's own estimate, used until two samples exist. Otherwise the cell
  says why there is none. Never replace it with an estimate of your own.
- `state` is `ok`, `stalled Nm` (no progress for 10 minutes, or `--stall <min>`),
  `error in log`, `done`, `failed (exit N)`, `ended (no exit code)`, or an unreadable log or path.
  `done`, `failed`, and `ended` are terminal and freeze the row at its final values.
