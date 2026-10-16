---
name: research-journal
description: Keep a Research 2.0 JOURNAL.md as the append-only chronological story of what was done, why, and what was learned. Use after meaningful scientific or engineering events and whenever the user asks to journal, log, or write up the work; append each entry without a prompt in either engineering mode and show it afterwards, never as factual run state.
---

# research-journal

JOURNAL.md is the project's **story** — prose explaining what was done, why, and what was learned (state lives in EXPERIMENTS.md). Append-only: never rewrite or delete past entries — except that a run_id migration (`run-identity`, always user-approved) replaces old run hashes with their new ones in past entries, and nothing else. Canon: `../research-project-init/references/conventions.md`, including "Directives are closed" — journal only this project, from this project's own evidence, and never model its format on another project on disk.

## When

- Appending is **do, then report** in both engineering modes (conventions, "Approval tiers"):
  append a concise factual entry in the same turn as the event, without a per-entry prompt, then
  show the entry in the reply so the user can correct it. A correction is a new entry, never an
  edit of the old one. Trivial mechanical edits do not journal.
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
- Agent-decided choices carry their reasoning here: a run_id election names the elected params and
  why; a plot's entry names its communication specification, every resolved export path, and why.
- **A one-off GPU authorization belongs in the journal.** If the user granted extra cards on the shared `behemoth` for a wave ("Marco lent me gpu 3 for tonight"), the launch entry says so — the grant is per-wave and never persisted anywhere else, so the journal is its only prose record.
- **Launch entries name the wave id**, so JOURNAL.md is the prose index into waves: given a wave id from EXPERIMENTS.md or `scripts/`, the journal says why it was dispatched.

## Enforcement backstop

The project's `.githooks/pre-commit` blocks commits touching `code/ config/ scripts/ evaluations/ visualizations/` without a JOURNAL.md change. If blocked, append the entry as above, show it, then commit. Never bypass the hook (`--no-verify`) without explicit user instruction.
