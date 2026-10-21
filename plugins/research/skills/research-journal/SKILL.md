---
name: research-journal
description: Keep a Research 2.0 JOURNAL.md as the append-only chronological story of what was done, why, and what was learned. Use after meaningful scientific or engineering events and whenever the user asks to journal, log, or write up the work; append and commit each entry without a prompt and without echoing it, never as factual run state.
---

# research-journal

JOURNAL.md is the project's **story** — prose explaining what was done, why, and what was learned (state lives in EXPERIMENTS.md). Append-only: never rewrite or delete past entries — except that a run_id migration (`run-identity`) replaces old run hashes with their new ones in past entries, and nothing else. Canon: `../research-project-init/references/conventions.md`, including "Directives are closed" — journal only this project, from this project's own evidence, and never model its format on another project on disk.

## When

- Appending is the agent's (conventions, "Autonomy"): append a concise factual entry in the same
  turn as the event and commit it with the work it describes. Never ask before appending or
  committing, and never paste the entry into the chat; the user reads JOURNAL.md when they want
  to. A correction is a new entry, never an edit of the old one. Trivial mechanical edits do not
  journal.
- On demand, anytime the user asks.

## Format

Free prose, chronological (append at the end), under date **and time** headers:

```markdown
## 2026-07-19, 14:32 — launched lr sweep for 000_grokking (wave 20260719-143210)

Launched the 12-run lr sweep across all four rigs, one lane each. Went with
lr ∈ {1e-3, 3e-4, 1e-4} because the pilot diverged at 3e-3. Waiting on results before
deciding the weight-decay grid (see EXPERIMENTS.md §000).
```

- Title is optional; keep entries short and honest — rationale over ceremony.
- Reference experiments by `NNN_name` and runs by their run_id (the `rid-<32 hex>` hash under
  `hashed-v2`, with its label when it helps the reader).
- Engineering decisions and their reasoning (a run_id election, a plot's communication
  specification and export paths) belong in `program/DECISIONS.md`, not here; the journal keeps the
  scientific why and what was learned, and may point to the decision log.
- **A one-off GPU authorization belongs in the journal.** If the user granted extra cards on the shared `behemoth` for a wave ("Marco lent me gpu 3 for tonight"), the launch entry says so — the grant is per-wave and never persisted as configuration, so the journal and the wave manifest's `gpu_grants` are its only records.
- **Launch entries name the wave id**, so JOURNAL.md is the prose index into waves: given a wave id from EXPERIMENTS.md or `scripts/`, the journal says why it was dispatched.

## Enforcement backstop

The project's `.githooks/pre-commit` blocks commits touching `code/ config/ scripts/ evaluations/ visualizations/` without a JOURNAL.md change. If blocked, append the entry as above, then commit. Never bypass the hook (`--no-verify`) without explicit user instruction.
