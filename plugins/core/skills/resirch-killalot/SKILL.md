---
name: resirch-killalot
description: Keep the owner's single cross-project list of work (ReSirch Killalot) for every project tagged with .project.toml - tasks, decisions, approvals, questions and reminders that agents only propose and the owner alone accepts, rejects, answers or approves. Use when the user asks what is open, pending, waiting on them, or on their plate in this project or across projects, asks for an inbox, digest, todo, reminder, or project overview, wants to park or unpark a project, says to do, start, finish, accept, reject or answer an item, when a session ends with work left undone, when a journal entry says something is not done, when a decision is left open, or when a prune or other risky action needs the owner's approval. Do not use for run status or GPU occupancy.
---

# resirch-killalot

ReSirch Killalot is the owner's one list of work across every tagged project (a directory holding
`.project.toml`; untagged projects are out of scope). It lives in one store on the hub, outside
every project, and is changed **only through the `killalot` command** — never by editing the
store. Setup, deployment and device pairing: [references/setup.md](references/setup.md).

**Resolving this skill's own files.** Run `killalot` from `PATH`. If it is missing (the service
was never deployed), use `scripts/killalot.py` from this skill's own directory — the folder
holding the SKILL.md you are reading — and tell the owner to deploy (setup reference). If the
command reports no config or no store, say so and stop; do not invent one.

## The model

- **Kinds:** `task`, `decision`, `approval`, `question`, `reminder`. **Owner** of the work:
  `me` (the owner) or `agent`.
- **States:** `proposed → accepted → in_progress → done`, with `waiting`, `snoozed`, and the
  closed `rejected` / `dropped`. Every change is an event; history is never rewritten.
- **Agents only propose.** Only the owner accepts, rejects, edits, drops, snoozes, answers,
  parks or unparks. Accepting means "it is on the list" — never that the work may start.
- **Approvals bind to evidence.** An approval stores the full dry-run and its fingerprint. The
  owner approves it only from a paired device, after seeing the whole dry-run; it expires; and
  an executor must re-run the dry-run and pass `check-approval` before acting.

## At session start

The host's session-start hook prints this project's accepted, in-progress and waiting items, if
any. When it does, mention them briefly. **Start an item only on the owner's yes** in this
session. To look yourself: `killalot list --project .` (add `--json` before the subcommand for
machine output: `killalot --json list --project .`).

## Proposing

Propose when there is real, owed work and it is not already tracked:

- the session ends with something undone that someone must pick up;
- a journal entry records something as not done;
- a decision is left open in a program, plan, or decision-register file;
- the owner mentions a todo or a reminder in passing.

```bash
killalot propose --project . --kind task --title "<one line>" --evidence "<why it exists, where it came from>" --session "<host>/<session id>"
```

Rules:

- **Evidence is required**: the file and line, journal entry, wave id, or the owner's words.
- **No duplicates.** `propose` refuses a title already open in the project. It also refuses one
  the owner **rejected**, and prints the reason: never propose it again in other words.
- `--owner me` for work only the owner can do (read, decide, write); the default `agent` is work
  an agent would do once accepted.
- One item per owed outcome; no item for trivia or for work finished in this session.

## Executing an accepted item

1. Only on the owner's yes in this session: `killalot start <id>`.
2. The item says **what** to do, never that the **how** is approved. The project's engineering
   mode, plot approval, dry-runs, journal rules and every other skill's gates still apply in full.
3. Finish with evidence: `killalot done <id> --evidence "<commit, journal entry, wave, file>"`.
4. Blocked on the owner: `killalot wait <id> --reason "<the question>"` — it becomes a question
   in the owner's inbox. Do not block the session waiting for the answer.

An agent works only items owned by `agent`; the owner's own items are for the owner to close.

## Relaying the owner's decisions

When the owner says in this session to accept, reject, edit, drop, snooze, answer, park or unpark,
run it on their behalf with `--via session:<host>/<session id>`:

```bash
killalot accept 12 --via session:<host>/<id>
killalot reject 13 --reason "<the owner's reason>" --via session:<host>/<id>
killalot answer 14 --text "<the owner's answer>" --via session:<host>/<id>
killalot park <project> --reason "<why>" --revisit 2w --via session:<host>/<id>
```

Only on the owner's explicit words in the current turn — never because it seems obvious. A
rejection always carries the owner's reason. **Never approve**: approvals are given only from a
paired device, so send the owner to the item in the web app. The CLI refuses `approve` outright.

## Risky actions (prune and other deletions)

1. Produce the complete dry-run and save its output to a file.
2. `killalot request-approval --project . --title "<what>" --evidence-file <file> --command "<the exact confirm command>"`.
3. Tell the owner the item number; they approve it in the web app.
4. In the session that executes it, re-run the same dry-run into a new file and run
   `killalot check-approval --item <id> --evidence-file <new file>`. Proceed only on exit 0. A
   changed dry-run, an expired approval, or no approval means stop and file a new request.
5. `killalot start <id>`, run the command, then `killalot done <id> --evidence "<output>"`.

## Overview, inbox and digest

- `killalot projects` — one line per project: open items, next action, last activity, `STALE`
  (no activity for the configured days while items are open) or `PARKED`.
- `killalot inbox` — what waits on the owner, in order: blocked on you, decisions, proposals, due.
- `killalot digest` — everything since the last digest the owner asked for. It moves the mark;
  use `--peek` to look without moving it. Run it only when the owner asks for a digest.
- `killalot show <id>` — one item with its evidence and history.

Staleness is never pushed; it shows in the overview and the digest. Parking is the owner's
deliberate choice; activity in a parked project only suggests unparking.

## Seeding (once, on the owner's request)

Turn the work already buried in the projects into proposals:

1. `killalot scan`, then `killalot projects`.
2. For each project, read its own records only: `program.md` next actions and decisions, the
   decision register, the last journal entries (anything recorded as not done or waiting for the
   owner's go), and open supervisor questions or prune candidates when the project has them.
3. `propose` each owed item with its evidence, `--owner me` where only the owner can act.
4. Never accept anything. Report how many proposals each project got; the owner reviews them.

## Hard limits

- Never edit the store by hand, never copy it, never work around a refusal from `killalot`.
- Never accept, reject or answer without the owner's explicit words; never approve at all.
- Never start an item without the owner's yes in this session.
- Never put secrets in an item, its evidence, or a dry-run filed for approval.
