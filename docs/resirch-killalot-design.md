# Design: ReSirch Killalot — a cross-project work layer (items, inbox, overview)

Name: **ReSirch Killalot** (Research + Sir Killalot). Machine name: skill `resirch-killalot`, command `killalot`.

Status (2026-10-05):
- Design (§1–§12), tool selection (§13) and the implementation plan (§14–§21): approved.
- **R1 is live** (core 1.1.0 → 1.2.2): store, CLI, session-start hook on both hosts, web app, seeding.
  Access switched from Tailscale to public HTTPS after go-live; see §22.
- **R3 (core 1.3.0)**: priorities, links, comments; a hub assistant the owner talks to in plain
  words from the web app and Telegram; the Telegram bot (R2's notifications folded in); a remote
  MCP connector for chat apps. See §23.
- **Still open from R2**: supervisor questions, prune approvals (research 9.1.0), rig-board
  compute column.

Code: `plugins/core/skills/resirch-killalot/`. Setup and ports: its `references/setup.md`.

## Context

The skills handle single projects well (experiments, waves, rigs, plots, journal), but two layers
are missing:

- **Inside a project:** there is no proper home for tasks, todos, decisions and reminders.
- **Across projects:** there is no holistic view of everything that is going on.

What exploration found:

- **8 repos carry a `.project.toml` tag, across `/mnt/KS_2TB` and `/mnt/WD_4TB_1`.** Two of them
  are checkouts of the same project (qat-transfer v2 and v3), and two are near-empty
  (ptq-retrieval, efficiency-benchmark).
- **Open work already exists, but only as prose nobody revisits:**
  - ladder `program.md`: "Exact next action: owner … picks among (a)–(d)", idle since 2026-09-04.
  - zip-aware journal: "Not done: EXPERIMENTS.md is not yet updated".
  - qat-transfer journal: "Pruning … waits for the user's go".
  - lottery-qat: an open "next decision", idle for 6 weeks.

  The problem is not creating work. It is that work gets buried and forgotten.
- **"Waiting for your go" is scattered across separate mechanisms**, none of them visible from
  outside its project: supervisor `ask`/`answer`, `rig-sync prune` dry-runs, plot spec approvals,
  manual-mode journal entries, and decision registers.

Intended outcome: one place, reachable from desk and phone, holding everything you owe and
everything agents propose, across all tagged projects. Your explicit greenlight is the only way
anything enters it.

## Decisions taken (and why)

| # | Decision | Why |
|---|---|---|
| D1 | Scope is tagged projects only (`.project.toml`); no project-less items for now | Use it daily first, then see what's missing |
| D2 | You and agents create items; agents can only *propose*, and only you accept | Your greenlight is the only gate |
| D3 | Accepting means "add it to the list", nothing more | Execution is a separate concern (D9) |
| D4 | Design needs first, choose tools later | Avoid being shaped by current connectors |
| D5 | Same capabilities on phone and desk; no device-based restrictions | Risk comes from missing evidence, not from the device |
| D6 | Every approval shows its full evidence wherever it is made; if it can't be shown in full, it can't be approved there | Same as D5 |
| D7 | Notifications: interrupt / digest / silent; no quiet hours; digest on demand | Your choice |
| D8 | Staleness is pull-only; parking a project is manual and optional | A forgotten project shows up whenever you look |
| D9 | Execution happens only in a session, with your yes; existing gates stay in force | Nothing runs on its own |
| D10 | Security: authenticated, approval tied to the exact evidence, approving never executes, approvals expire | An exposed approve button must be safe |
| D11 | Day-one seeding through proposals; anti-noise rules; minimal fields; v1 inbox fed by supervisor questions + prunes | Real list on day one without clutter |

## 1. The item (one record covers every kind of work)

| Field | Meaning |
|---|---|
| `id` | stable identifier |
| `project` | project identity (see §9, open point P1) |
| `kind` | `task` · `decision` · `approval` · `question` · `reminder` |
| `title` | one line |
| `owner` | `me` or `agent` |
| `state` | see §2 |
| `origin` | `me`, an agent session (host + session ref), or a system source (`supervisor`, `prune`) |
| `evidence` | links: journal entry, wave id, plot, commit, dry-run (with fingerprint for approvals) |
| `due` | optional; when set on a reminder, the reminder fires at that time |
| `history` | every transition: who, when, from which device or session, and the reason |

No priorities, tags or dependencies in v1. The inbox order (§3) does the job of priority. Add
more fields only if daily use shows they're needed.

## 2. Lifecycle

```
proposed ──accept/edit-accept──▶ accepted ──▶ in progress ──▶ done
    │                               │              │
    └─reject (reason kept)          ├─▶ snoozed     └─▶ waiting (reason → becomes an inbox question)
                                    └─▶ dropped
```

- Agents can only create items in `proposed`. Only you can accept, edit-and-accept, or reject.
- A rejection keeps its reason, and the same proposal must not come back (§7).
- Items you create yourself start in `accepted`.
- Approvals and questions coming from system sources (supervisor, prune) enter as items that are
  waiting on you. Answering one records the answer through that source's own existing command;
  for example, a supervisor question resolves via `supervisor.py answer`.

## 3. Views

- **Inbox: everything waiting on you, across all projects.** Ordered by cost of delay:
  1. blocked on you (supervisor questions, pending prunes)
  2. decisions
  3. new proposals
  4. due reminders
- **Project overview:** one line per project, derived automatically and never maintained by hand:
  - phase and last activity (commits, journal, waves, item changes)
  - live compute, taken from the rig-board
  - number of open items, and the next action
  - stale or parked marker
- **Project view:** every item of one project. This is also what an agent working in that project
  reads.
- **Digest (on demand):** everything since the last digest you asked for. It covers what's
  waiting on you, new proposals, finished waves, items due, and stale projects.

## 4. Parking and staleness

- **Stale** means no activity for 14 or more days while the project still has open items. It is
  shown only in the digest and the overview, never pushed.
- **Parking** is manual, with an optional reason and an optional revisit date. The revisit date is
  a reminder, so it follows the interrupt rule.
- A parked project never counts as stale and is listed under "parked".
- **Unparking** is manual. If activity resumes in a parked project, the system *suggests*
  unparking but never does it on its own.

## 5. Notifications

| Level | What | Behavior |
|---|---|---|
| Interrupt | something is blocked on you (agent or supervisor question, pending approval that idles work); a reminder you timed | buzzes immediately, at any hour |
| Digest | everything else worth knowing | only when you ask for it |
| Silent | state transitions, board refreshes, journal entries | recorded, visible when you look |

- A failed wave is not its own trigger. If the supervisor needs you, it asks, and that question
  is the interrupt.
- No nagging: an unanswered interrupt doesn't buzz again. It stays at the top of the inbox and of
  your next digest.
- Grouping: events from the same project and wave within 15 minutes become one notification.
- Every notification is actionable: it carries the evidence and the buttons (accept, reject,
  answer, snooze).
- Review after about two weeks of daily use: which notifications did you act on?

## 6. Security (for acting from anywhere)

- Nothing is reachable without authentication, with a separate login per device that you can
  revoke on its own.
- **An approval is tied to the exact evidence you saw.** The system stores a fingerprint of the
  dry-run you approved. If the action would now do anything different, the approval no longer
  counts and you are asked again.
- **Approving never executes anything.** It records who, when, which device and what exactly.
  Whatever performs the work checks that record. The internet-facing part therefore cannot
  delete anything.
- Approvals expire, by default after 24 hours.
- Risky actions (deletions, prune, moving secrets) take a second confirmation tap on the screen
  that shows the full dry-run.

## 7. Agents: proposing (harvesting) and executing

**Proposing.** Agents propose at natural moments:
- the end of a session
- a journal entry containing "Not done"
- a prune proposal
- a new open decision in a program or decision file

Anti-noise rules:
- check existing items before proposing (no duplicates)
- never re-propose something you rejected
- every proposal states its evidence: why it exists and where it came from

**Executing (D9).**
- Accepted items are picked up only in a session, either because you say "do item X" or because
  the agent, at session start in that project, shows the accepted items and proposes one. It
  waits for your yes.
- The agent moves the item to `in progress`, and when finished to `done` with links to the
  evidence. If it gets stuck, it moves the item to `waiting` with the reason, which shows up as a
  question in your inbox.
- An accepted item says *what* to do, never that the *how* is already approved. The engineering
  mode, plot communication approval, dry-runs and journal rules all still apply.

**Both hosts.** Claude and Codex agents use the same interface. All writes go through one script
(the same pattern as `board.py`), and items are never edited by hand.

## 8. Seeding (first day)

A one-time pass over every tagged project turns existing open work into **proposals**:
- `program.md` next-action and next-decision lines
- journal entries ending with "Not done"
- pending prunes
- open supervisor questions

On day one you go through them and accept or reject each one.

## 9. Structure (tool-agnostic, but these choices shape it)

- **Single source of truth:** one central store on the hub, outside every project tree (like the
  board root `[board] root`), plus an append-only event log. Not a tracked file per repo: phone
  approvals and parallel agent sessions writing to the same file would conflict in git.
- **Placement:** a capability in the `core` plugin, since it applies to every tagged project
  regardless of category. That means no new domain plugin and a `core` version bump on release.
- **Project discovery:** walk the project roots declared in ReSirch Killalot's own `core`
  config (initially both `PARA/Projects` folders) for `.project.toml`. This reuses the
  `project-init` marker. It does not read the rigsync registry's `[plots] roots`, which is
  research configuration that `core` must not depend on.

## 10. Patterns to reuse (do not reinvent)

| Need | Existing pattern |
|---|---|
| script-only writes, append-only history | `plugins/research/skills/rig-board/scripts/board.py` (`history.jsonl`, never edit lane files by hand) |
| always-on hub service + systemd user unit | `rig-board/assets/rig-board.service`, `sweep-supervisor/assets/sweep-supervisor.service` |
| token-protected web view reachable remotely | `board.py serve` (`[board] token`), `visualizations/scripts/plot_server.py` (`public_url`, sandboxed variant) |
| questions to the user that don't block | `sweep-supervisor` `ask` / `answer` |
| dry-run then confirm | `rig-sync prune --dry-run` / `--confirm` |
| unattended agent invocation (later, if ever needed) | `[supervisor.agent]` command in `~/.config/rigsync/machines.toml` |
| project tagging | `plugins/core/skills/project-init/scripts/project_init.py` |

## 11. v1 scope

In:
- items, lifecycle and history
- inbox, project overview and project view
- on-demand digest
- parking and staleness
- interrupt notifications
- authenticated phone and desk access with the security rules above
- agent proposing and executing in sessions, on both hosts
- the seeding pass
- inbox fed by supervisor questions and pending prunes

Out (revisit after daily use):
- items with no project
- priorities, tags, dependencies
- plot approvals and manual journal entries in the inbox
- any unattended execution
- external connectors

## 12. Open points for your review

- **P1. Project identity — resolved:** each tagged checkout is its own project (qat-transfer v2
  and v3 count separately). No `project-init` change.
- **P2. Stale threshold.** 14 days is a starting value; adjust after daily use.

## 13. Tool selection (approved)

| Capability | Choice |
|---|---|
| Store | SQLite (stdlib, WAL) in a directory outside every project, plus an append-only `events` table |
| Rules + agent interface | One stdlib Python CLI `killalot` in the `core` skill `resirch-killalot`, linked onto `PATH`; the web server and the bot import the same library |
| Session-start awareness | Session-start hook on both hosts (Claude `hooks`, Codex `hooks.json` `session_start`) listing the project's accepted and waiting items |
| Push + quick actions | Telegram bot (outbound long-polling, inline buttons, `/digest`); only the owner's chat id is accepted. Item text passing through Telegram is accepted |
| Full views + full-evidence approvals | Custom stdlib web server + one responsive HTML page, installable on the phone home screen |
| Transport | Tailscale `tailscale serve` (HTTPS, no public port); tailnet owned by a friend's account, accepted |
| Login | Per-device paired tokens revocable in the app, plus the Tailscale identity as a second check |
| Evidence-bound approvals | sha256 of the dry-run + expiry; the executor recomputes the dry-run and refuses on mismatch |
| Supervisor questions | Read from the board's exposed `supervised_waves` data; answers go back via the supervisor's `answer` command called by name on `PATH`, never by a cross-plugin file link |

## Next steps (as planned at approval time)

1. **Tool selection:** for each capability (store, phone surface + push, auth, host interface),
   pick the tools that fit this design and justify each against it.
2. **Implementation plan:** files, skill body, scripts, service, tests, and the release through
   `scripts/release.sh --plugin core`.

## Verification of this design

- Every decision D1–D11 is traceable to the conversation.
- Walk the four real examples (ladder, zip-aware, qat-transfer prune, lottery-qat) through the
  design end to end: seeded as proposals → accepted → shown in the overview → executed in a
  session → done with evidence. Each step must be covered by a section above.
- After implementation: two weeks of daily use, then review notifications (§5) and fields (§1).

---

# Implementation plan (v1)

## 14. Decisions added during planning

- **D12. Code location.** Everything lives in this repo, inside the skill folder
  `plugins/core/skills/resirch-killalot/` (`scripts/`, `assets/`, `references/`), the same way
  rig-board and the plot server do. Tests go in `tests/`.
- **D13. The service runs from the installed release, never from the repo working tree.**
- **D14. Owner actions inside a session.** In a session, accept, reject, edit, park and answer
  can be relayed by an agent only on your explicit words in that turn. Each is logged as
  `via=session:<host>/<session_id>`. **Evidence-bound approvals of risky actions (prune,
  deletions) can be given only from a paired device** (web, or Telegram leading to the web page),
  so an agent can never approve its own dry-run. The CLI refuses `approve` outright.
- **D15. Two releases.**
  - **R1** (core 1.1.0): store, CLI, skill, session-start hooks, web app, seeding. Usable day to
    day from desk and from the phone browser.
  - **R2** (core 1.2.0 + research 9.1.0): Telegram bot and notifications, supervisor questions,
    prune approvals.

## 15. Files

```
plugins/core/skills/resirch-killalot/
  SKILL.md                     rules for agents: when to propose, anti-noise, D9 execution, D14, seeding procedure
  agents/openai.yaml           display "ReSirch Killalot", default_prompt with $resirch-killalot
  scripts/killalot.py          single stdlib file (like board.py): config, store, rules, CLI, hook, web server, (R2) bot + scanners
  assets/app.html              the one responsive page (inbox, projects, project, item, digest, devices)
  assets/manifest.webmanifest  + assets/icon.svg   (home-screen install)
  assets/resirch-killalot.service   systemd user unit template (__KILLALOT_PY__)
  references/setup.md          config, deploy, tailscale serve, device pairing, (R2) BotFather + chat id
plugins/core/hooks/hooks.json  SessionStart → python3 "${CLAUDE_PLUGIN_ROOT}/skills/resirch-killalot/scripts/killalot.py" hook session-start
tests/test_killalot.py         new
tests/test_core_contracts.py   skill set + version pin + SKILL.md phrase contracts
scripts/validate_repo.py       + validate plugins/*/hooks/hooks.json (valid JSON, every referenced script exists)
scripts/release.sh             + after a successful core release, redeploy the service if one is installed
plugins/core/.codex-plugin/plugin.json, .claude-plugin/plugin.json, .claude-plugin/marketplace.json
                               version, description, keywords, Codex interface text (+ hooks declaration if Codex needs it, §20 step 0)
```

R2 also touches research text only:
- `sweep-dispatch`, `experiments-tracking` and `rig-sync` prune proposals: "also file it with
  `resirch-killalot` as an approval". The skill is named in backticks; no file link.
- Version bump research 9.0.1 → 9.1.0 in four places.

## 16. `killalot.py` internals

**Config.** `~/.config/resirch-killalot/config.toml`, or `$KILLALOT_CONFIG`, read with `tomllib`
like `board.py`'s `load_config`:

```toml
[store]    root = "/mnt/KS_2TB/PARA/Resources/resirch_killalot"   # killalot.db, backups/, app/
[projects] roots = ["/mnt/KS_2TB/PARA/Projects", "/mnt/WD_4TB_1/PARA/Projects"]; max_depth = 5; stale_days = 14
[web]      bind = "127.0.0.1"; port = 40980; owner_login = "dansolombrino@..."; public_base = "https://rig-4090.tail48a89.ts.net"
[approvals] ttl_hours = 24
# R2
[telegram] bot_token_file = "~/.config/resirch-killalot/telegram.token"; chat_id = 0
[supervisor] board_root = "/mnt/KS_2TB/PARA/Resources/rig_board"; answer_command = ["python3", "<abs supervisor.py>", "--root", "{project_root}", "answer", "--wave", "{wave}", "--id", "{qid}", "--text", "{text}"]
```

**Store.** SQLite in WAL mode with `busy_timeout`. Each write runs in one transaction that also
inserts its `events` row. A `meta.schema_version` value plus an ordered list of migrations
handles schema changes.
- `projects(path PK, name, categories, parked_at, park_reason, revisit_item, first_seen, last_seen, missing)`.
  `name` is the folder name; a duplicate name gets a parent suffix.
- `items(id, project, kind, title, body, owner, state, origin, via, evidence JSON, due_at, snooze_until, source_key UNIQUE, dedupe_key, fingerprint, approved_until, notified_at, created_at, updated_at)`
- `events(id, at, item, project, actor, via, action, from_state, to_state, payload JSON)`.
  Append-only; nothing updates or deletes rows.
- `devices(id, name, token_sha256, created_at, last_used_at, revoked_at)`
- `meta(key, value)`: `schema_version`, `last_digest_at`, and in R2 `telegram_offset`.
- Daily backup with the sqlite3 `backup()` API into `backups/`, keeping 14.

**Rules (one place, used by the CLI, web and bot).**
- A transition table enforces §2.
- An `actor` of `agent` can only create `proposed` items and move its own accepted items through
  `in progress`, `waiting` and `done`.
- Owner verbs require `actor=me` together with a `via` value: `device:<id>` or `session:…`.
- `approve` requires `via=device`.
- Dedupe: `dedupe_key` is the normalized `project + kind + title`. A proposal is refused when an
  open item or a rejected item has the same key, and the refusal prints the existing id and the
  rejection reason.

**Approvals.**
- `request-approval --evidence-file F` stores the full evidence and its
  `sha256(normalize(F))`. The normalization sorts lines that are order-free (prune output is
  already deterministic).
- `approve` (device only) sets `approved_until = now + ttl`.
- `check-approval --item N --evidence-file F2` exits 0 only when the item is approved, not
  expired, and the fingerprint matches. Otherwise it exits 1 and states the reason. This is what
  the executor runs before `--confirm`.

**CLI (agents and you).**
- `projects [--json]`
- `list [--project P|.] [--state …]`
- `inbox`
- `show N`
- `propose --project . --kind --title [--body] --evidence …`
- `add` (owner)
- `accept/reject/edit/drop/snooze/park/unpark/answer --via session:<id>`
- `start/wait/done N --evidence …`
- `request-approval`, `check-approval`
- `digest [--peek]`
- `device add/list/revoke`
- `hook session-start`
- `serve`, `deploy`, `doctor`

`--json` everywhere. Errors print `error: …` and exit 2, like `board.py`.

**Project scan.** Runs every 10 minutes in the service and on demand from the CLI.
- It walks the configured roots up to `max_depth` looking for `.project.toml`.
- **Last activity** is the maximum of: the last `git log -1 --format=%ct`, the `JOURNAL.md`
  mtime, the newest `.waves/_state/*` mtime, and the last item event.
- **Stale** = not parked, has open items, and last activity is older than `stale_days`.
- Activity in a parked project creates a silent "suggest unpark" flag. It is never pushed.

**Hook `session-start`.**
- Reads the stdin JSON `cwd`, finds the git root, and requires a `.project.toml` there.
- Reads the DB **read-only** with a hard 1-second budget.
- Prints `{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext": "..."}}`
  holding up to about 500 characters: counts plus up to 5 titles of the project's accepted,
  waiting and in-progress items, and a pointer to `killalot list --project .`.
- In every other case (untagged project, no DB, error, timeout) it prints `{}` and exits 0, so it
  never breaks or slows a session in any project. This matters because `core` is on everywhere.

**Web (`serve`).**
- Uses `ThreadingHTTPServer` on `127.0.0.1` with a daemon worker thread (the scan, backups, and
  in R2 the bot and scanners), the same pattern as `board.py` `serve`.
- Exposed **only** through `tailscale serve --bg --https=443 http://127.0.0.1:40980`.
- **Authentication on every request:**
  - the `Tailscale-User-Login` header must equal `owner_login`, **and**
  - a valid device cookie must be present (`killalot_device`, HttpOnly, Secure,
    SameSite=Strict, compared as `sha256` against `devices`).
- **Pairing:** `killalot device add "iphone"` prints a one-time link that expires in 10 minutes.
  Opening it sets the cookie.
- Writes are `POST` JSON only. Each must carry an `X-Killalot` header and a same-origin
  `Origin`, as CSRF protection.
- `/healthz` needs no authentication.
- Reused patterns from board.py: `_send` with `no-store`, `__file__`-relative `assets/`, and
  `hmac.compare_digest`.

**Page (`app.html`).**
- Tabs: Inbox (ordered as in §3), Projects (overview with stale and parked markers), and a
  project view with its items.
- Item detail shows the full evidence and the buttons.
- A risky approval needs two taps on the screen that shows the full dry-run.
- Also: a Digest button, an add-item form, and a Devices page (list and revoke).
- Light and dark themes; built for phone width first.

**Deploy (D13).** `killalot deploy`, run from the installed plugin copy:
1. Copies `scripts/` and `assets/` into `<store>/app/<version>/` and atomically moves the
   `current` symlink to it.
2. Writes `~/.local/bin/killalot`, a wrapper that runs `<store>/app/current/scripts/killalot.py`.
3. Renders the unit from the template, then runs `daemon-reload`, `enable` and `restart`.
4. Polls `/healthz`.

`release.sh` calls the newly installed copy's `deploy` after a core release, but only when the
unit already exists.

## 17. SKILL.md content (host-neutral)

- **What it is and when to use it:** items, inbox, projects, parking, digest, "what's on my plate".
- **Resolving this skill's own files:** use the `killalot` command on `PATH`, falling back to
  this skill's `scripts/killalot.py` in its installed directory.
- **Proposing:**
  - when to do it: at the end of a session, on a journal "Not done", on a new open decision, on
    a prune
  - the evidence each proposal must carry
  - the dedupe and rejection rules
- **Executing (D9).**
  - Show the accepted items at session start, which the hook provides.
  - Start an item only on the owner's yes.
  - Move it through `start`, then `done` with evidence, or `wait` with a reason.
  - An accepted item never bypasses engineering mode, plot approval, dry-runs or journal rules.
- **Owner relay (D14).** Run owner verbs only on the owner's explicit words in the current turn,
  always passing `--via session:<id>`. Never `approve`; send the owner to the web page instead.
- **Seeding procedure (§8)**, run once:
  1. List the projects.
  2. For each one, read `program.md`, the last journal entries, the decision register, and
     `rig-sync activity` / prune candidates.
  3. Propose, with evidence.
  4. Never accept.
- **Stop conditions:** no config or unreachable store → say so and point to
  `references/setup.md`. Never edit the DB by hand.

## 18. R2 additions

- **Telegram worker** (a thread in `serve`):
  - Long-polls `getUpdates` through `urllib`. Accepts only the configured `chat_id`; everything
    else is ignored and logged.
  - **Interrupt sender:** every 30 s it picks interrupt-worthy items that haven't been notified
    yet (system questions and approvals, items moved to `waiting`, due reminders). It groups them
    by (project, wave) within 15 minutes and sends one message each, with inline buttons:
    - Accept/Reject for proposals
    - Answer (a ForceReply prompt) for questions
    - Snooze 1h/1d
    - Open for approvals: a link to the web item page. Approval itself never happens inside
      Telegram, because the dry-run may exceed the 4096-character limit and two taps on the full
      evidence are required.
  - Marks `notified_at`. No re-sends.
  - Commands: `/digest`, `/inbox`, `/projects`.
- **Supervisor scanner** (every 60 s, read-only):
  - Globs `<board_root>/supervisor/*.json`, reads each wave's `questions.json`, and upserts
    `kind=question, origin=supervisor` items with `source_key=sup:<root>:<wave>:<qid>`.
  - When the supervisor marks a question answered or the pointer disappears, the item closes
    automatically, with an event.
  - Answering from Killalot runs the configured `answer_command` (an argv list, no shell). The
    item closes only on exit 0.
- **Prune flow:**
  1. An agent runs the dry-run and calls `request-approval`, attaching the evidence and the
     confirm command.
  2. You approve on the web.
  3. In a later session the agent re-runs the dry-run, runs `check-approval`, then `--confirm`,
     then `done`.
- **Research text edits** (R2, research 9.1.0): the prune-proposal sections in `sweep-dispatch`,
  `experiments-tracking` and `rig-sync` gain one rule: file the proposal with
  `resirch-killalot`, and execute only after `check-approval` passes or the owner says yes in the
  session for a non-risky case. The existing rule stays: prune always asks.

## 19. Tests (`tests/test_killalot.py`)

They follow the `test_plot_server.py` idioms: load with `spec_from_file_location` plus
`sys.modules`, `TemporaryDirectory` fixtures, and `main([...])` with captured stdout and stderr.

- **Rules:**
  - every allowed and forbidden transition
  - an agent can't accept or approve
  - the CLI `approve` is refused
  - dedupe and rejection memory
  - snooze and due reminders
  - parking and staleness, including that a parked project is never stale
- **Store:**
  - a migration from an empty DB
  - two processes writing at once (multiprocessing) leave no lost events
  - events are append-only
- **Approvals:** fingerprint match, mismatch, expiry, and a missing approval.
- **Scan:** a fake roots tree with tagged and untagged repos, duplicate names, and a missing
  project.
- **Hook:** tagged with items gives context; untagged, a missing DB and a corrupt DB each give
  `{}` with exit 0; the size cap holds.
- **Web:** a live `ThreadingHTTPServer` on `127.0.0.1:0`.
  - Rejected: no Tailscale header, the wrong login, no cookie, a revoked device, POST without the
    CSRF header, and an expired pairing link.
  - Accepted: a full accept flow and an approval with two-step confirmation.
- **Deploy:** into a temp store and home it builds the `app/<version>` copy, flips `current`,
  writes the wrapper, and renders the unit. `systemctl` is mocked.
- **R2:**
  - the Telegram client against a fake HTTP Telegram (chat-id filtering, buttons, grouping, no
    re-send)
  - the supervisor scanner against a fake `board_root` plus `questions.json`
  - `answer_command` with argv substitution and failure handling
- **Contracts:** `test_core_contracts.py` covers the skill set, the pin 1.1.0 (R2: 1.2.0), and
  SKILL.md phrases (proposed-only for agents, never `approve`, D9 gate wording).

## 20. Execution order

0. **Spike (throwaway, nothing released).** Confirm that a plugin-shipped
   `hooks/hooks.json` SessionStart loads on **both** hosts:
   - Claude: auto-discovered, `${CLAUDE_PLUGIN_ROOT}`.
   - Codex: the `plugin_hooks` feature, possibly needing `[features] plugin_hooks = true` and a
     `hooks` entry in `.codex-plugin/plugin.json`, plus the one-time trust review.

   If Codex can't load it, the fallback is `killalot hook install-codex`, which adds a
   user-level `~/.codex/hooks.json` entry next to the existing `continuity-context.mjs` without
   touching it. I'll report the result before continuing.
1. Store, rules and CLI, with their tests.
2. Project scan, staleness, parking, digest, with their tests.
3. The `session-start` hook, `hooks/hooks.json`, and the validator extension, with tests.
4. Web server, `app.html`, device pairing, with tests.
5. `deploy`, the unit template, the `release.sh` step, `references/setup.md`.
6. SKILL.md, `openai.yaml`, manifests, version bump to 1.1.0 in four places, contract tests.
7. Run `python3 scripts/validate_repo.py` and `python3 -m unittest discover tests`. Then
   `scripts/release.sh --dry-run --plugin core`. **Ask you before the real release.**
8. **Go-live R1, with you:**
   - write the config
   - run `killalot deploy`
   - run `tailscale serve`
   - pair the desk browser and the phone (the phone needs Tailscale signed in to the tailnet)
   - run the seeding pass in a session
9. R2: the Telegram worker, the supervisor scanner, the prune flow, the research text edits,
   tests, versions 1.2.0 / 9.1.0, release with your OK, then create the bot with BotFather
   together.

## 21. Verification (end to end)

- **Automated:** the validator plus the full unittest suite pass, and the release dry-run shows no
  drift.
- **R1 live:**
  1. Open a Claude session and a Codex session in `ladder`. Both get the hook context (empty at
     first).
  2. Seeding proposes the four known items (ladder (a)–(d), zip-aware EXPERIMENTS.md,
     qat-transfer prune, lottery-qat decision).
  3. On the phone, open `https://rig-4090.tail48a89.ts.net`, accept two items and reject one
     with a reason.
  4. Re-run seeding. The rejected item is not proposed again.
  5. A new session in that project shows the accepted items. Say "do item N"; it goes
     `in progress`, then `done` with a commit link visible on the phone.
  6. Park lottery-qat: it disappears from stale. Commit something in it: you get a silent
     unpark suggestion.
  7. Revoke the phone's device: it gets 401 on the next request.
- **R2 live:**
  1. `supervisor.py ask` on a test wave: one Telegram message arrives within 60 s. Answer it
     from Telegram and the supervisor's `questions.json` shows it answered.
  2. Prune: dry-run, `request-approval`, approve on the phone (two taps), `check-approval` passes.
  3. Change the remote state: `check-approval` now fails because the fingerprint no longer
     matches.
  4. `/digest` returns everything since the last digest.


---

# 22. Changes after go-live (2026-10-05)

- **D16. Public access instead of Tailscale.** The owner's iPhone could join the tailnet and reach
  the hub (direct `tailscale ping`), but Safari never resolved `*.ts.net` names, even with AdGuard
  removed and iCloud Private Relay off. Rather than keep debugging the phone, the owner chose to use
  the home IP. Built as `[web] access = "public"` (core 1.2.0):
  - Caddy owns `0.0.0.0:49147` and serves `https://dansolombrino.duckdns.org:49147` with a
    Let's Encrypt certificate obtained through the DuckDNS DNS challenge, so only 49147 is
    forwarded on the router.
  - The app stays on `127.0.0.1:49149`. 49148 was taken by another server on the hub.
  - A five-minute timer keeps the DuckDNS name on the current home IP. The token lives only in
    `~/.config/resirch-killalot/duckdns.env` (mode 600).
  - Without a network identity, the paired device cookie is the only key. Ten failed
    authentications in ten minutes lock a client out for fifteen. HSTS is sent.
  - The app refuses any non-localhost bind in every mode. Tailscale access remains available as
    the default mode; `tailscale serve` on 49147 was turned off.
- **D13 confirmed in practice.** `scripts/release.sh` redeploys the service after every core
  release (1.2.0, 1.2.1, 1.2.2 each went live this way).
- **Fixes found in use.**
  - `request-approval --command` overwrote argparse's subcommand slot; it is now `dest=confirm_command`.
  - The page printed `[object HTMLDivElement]` for nested lists of cards; views are flattened now
    (1.2.2).
- **Observed:** the last-activity signal counts every commit, so a fleet-wide config commit
  (`sync.toml: declare rig-3080-ti`, 2026-10-04) made several projects look active. Left as is
  until daily use says otherwise.
- **Devices paired:** the owner's iPhone and Mac.
- **Follow-ups** are tracked as ReSirch Killalot items on this repository, not here.

---

# 23. R3: talk to the list (core 1.3.0, 2026-10-05)

The owner asked to manage the list in plain words, with back-and-forth, from three places: a chat
app (claude.ai or ChatGPT), Telegram, and the web app on phone and desk. They also asked for
priorities, links between items, comments and editing.

- **D17. Priorities, links and comments.** Daily use showed the need that L72 left open
  ("Add more fields only if daily use shows they're needed").
  - `priority` is P0–P3 with a default of P2. The inbox keeps the §3 buckets and sorts by priority,
    then due, then age inside each.
  - `links(src, dst, type)` has three types: `depends_on`, `parent_of` and `relates_to`. Links may
    cross projects and are soft-removed. Cycles, self-links and a second parent are refused. An
    item with an open `depends_on` target is *blocked*: it shows a chip and is skipped as a
    project's next action.
  - Comments are `comment` events, append-only like all history. Agents may link and comment;
    neither decides anything.
  - Edits now go through an allow-list (`EDITABLE`). Before this, `POST /api/action` passed any
    key into an `UPDATE`, which could reach `state` or the approval columns.
- **D18. The hub assistant acts for the owner, on a tap.** Telegram and the web chat hand the
  owner's words to a headless Claude Code (`claude -p --resume`, or `codex exec` by config).
  - Its only tools are the operation registry (`OPS`). It reaches them through
    `killalot mcp-stdio --stage`. Built-in tools are off and no other MCP servers are loaded.
  - Every write is staged in `pending` and applies only on the owner's Confirm tap, with
    `via=assistant:<channel>/<conversation>`. This enforces "confirm before writing" in code, not
    in the prompt. Text that an agent planted in a proposal cannot change anything on its own.
  - Staged items can link to each other (`change:N`); the link resolves when its target is
    confirmed.
  - The assistant manages the list only. It never approves and never starts or executes project
    work, so D9 and D14 stand.
  - Core 1.4.0 confines it for the case where someone else holds the channel. It sees and stages
    only `ASSISTANT_OPS` (read, add, edit, link, unlink, comment); the owner's decisions stay on
    the buttons and the web app, and a change staged under older rules fails on Confirm. Claude
    runs with `--setting-sources ""` (none of the owner's settings, hooks, plugins, CLAUDE.md or
    bypass mode), `--system-prompt` replacing the default and `--max-turns`. Codex runs under its
    own `CODEX_HOME` with `--ignore-user-config`, a read-only sandbox and every non-MCP feature off
    except the code-mode isolate it needs to call tools. Off-topic messages get one fixed refusal
    line; messages are capped at 2000 characters and `[assistant] daily_limit` a day. Conversations
    are tagged with `ASSISTANT_PROFILE`, so none resumes under older rules.
- **D19. Chat apps connect through MCP with OAuth bound to a paired device.**
  - `/mcp` is Streamable HTTP with JSON responses and is on only in public access.
  - OAuth 2.1 runs with dynamic registration limited to `[mcp] redirect_uris`, mandatory PKCE
    S256, one-hour access tokens and rotating refresh tokens.
  - The consent page answers only a paired device's cookie, or pairs the browser with a code from
    `device add`. Tokens act with `via=mcp:<id>` and are listed and revocable; revoking their
    device ends them.
  - The device cookie moved from `SameSite=Strict` to `Lax`, because the consent page is reached
    by a cross-site redirect. API writes still need the `X-Killalot` header and a JSON body. Old
    cookies are re-issued as Lax when the app opens.
  - Writes from a chat app apply at once; the app's own tool-approval prompt is the confirmation.
- **D20. R2's Telegram bot was folded into R3.**
  - It runs as a long-polling thread in `serve`, bound to one chat with `telegram link` plus
    `/start <code>`.
  - Notices are sent once per dedupe key (`notices` table): proposals, waiting items, due items
    and approvals. More than five at once become one summary. Items open when the chat is linked
    are not sent.
  - Buttons accept, reject (the reason comes through a ForceReply), answer, mark done, snooze and
    open. Approvals only open the web page.
  - Commands: `/inbox`, `/projects`, `/digest`, `/new`. Any other text goes to the assistant.
  - Supervisor questions and the prune flow remain R2 items.
- **1.3.1:** claude.ai checks a new connector from the browser, so the connector endpoints
  (`/mcp`, `/oauth/register`, `/oauth/token`, `/.well-known/oauth-*`) answer CORS preflights and
  carry `Access-Control-Allow-Origin` for the chat apps' origins only, never with credentials. The
  rest of the app stays same-origin.
- **1.3.2:** claude.ai's connector check never reached port 49147: it connects only to 443. The
  owner's ISP (iliad) shares one IPv4 among customers and gives each a port block starting at
  40960, so 443 cannot be forwarded. The connector therefore gets its own origin
  (`[mcp] public_base`), served on 443 by Tailscale Funnel (`https://rig-4090.tail48a89.ts.net`).
  The web app and Telegram stay on DuckDNS. The earlier Tailscale trouble (§22, D16) was the
  iPhone not resolving `*.ts.net` inside the tailnet; Funnel names resolve through public DNS, and
  only the chat app's servers use them. Asking iliad for a full-stack IPv4 would allow 443 on the
  router later.
