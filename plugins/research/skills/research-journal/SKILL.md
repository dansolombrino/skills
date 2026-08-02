---
name: research-journal
description: Keep JOURNAL.md alive — suggest entries after meaningful research events and write them on approval. Use after designing an experiment, launching a sweep, analyzing results, making a decision, hitting a dead end or bug — and whenever the user says to journal, log, or write up what happened.
---

# research-journal

JOURNAL.md is the project's **story** — prose explaining what was done, why, and what was learned (state lives in EXPERIMENTS.md). Append-only: never rewrite or delete past entries.

## When

- After any meaningful event — experiment designed, sweep launched, results analyzed, decision taken, dead end hit, bug found — **suggest** an entry in the SAME turn: propose the text, the user has the last word on whether/what gets written. Trivial mechanical edits don't journal.
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
- Reference experiments by `NNN_name` and runs by their run_id.
- **A one-off GPU authorization belongs in the journal.** If the user granted extra cards on the shared `behemoth` for a wave ("Marco lent me gpu 3 for tonight"), the launch entry says so — the grant is per-wave and never persisted anywhere else, so the journal is its only prose record.
- **Launch entries name the wave id**, so JOURNAL.md is the prose index into waves: given a wave id from EXPERIMENTS.md or `scripts/`, the journal says why it was dispatched.

## Enforcement backstop

The project's `.githooks/pre-commit` (installed at init) blocks commits touching `code/ config/ scripts/ evaluations/ visualizations/` without a JOURNAL.md change. If a commit gets blocked, that's the cue: propose the missing entry, then commit. Never bypass the hook (`--no-verify`) without explicit user instruction.
