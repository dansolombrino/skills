---
name: research-journal
description: Keep a Research 2.0 JOURNAL.md as the append-only chronological story of what was done, why, and what was learned. Use after meaningful scientific or engineering events, when a commit is blocked by the journal guard, and whenever the user asks to journal, log, or write up the work; never as factual run state.
---

# research-journal

JOURNAL.md is the project's **story** — what was done, why, and what was learned (state lives in
EXPERIMENTS.md). Append-only: past entries are never rewritten or deleted, except that a run_id
migration (`run-identity`) replaces old run hashes with their new ones. A correction is a new
entry.

## When

- With the work it describes: append a short factual entry and commit it together with that
  work. A commit the guard covers always carries an entry; for a mechanical edit, one line is
  enough. Other commits, such as `tests/` or docs alone, need none.
- Whenever the user asks.

## Format

Free prose, chronological (append at the end), under date **and time** headers:

```markdown
## 2026-07-19, 14:32 — launched lr sweep for 000_grokking (wave 20260719-143210)

Launched the 12-run lr sweep across all four rigs, one lane each. Went with
lr ∈ {1e-3, 3e-4, 1e-4} because the pilot diverged at 3e-3. Waiting on results before
deciding the weight-decay grid (see EXPERIMENTS.md §000).
```

- Keep entries short: rationale over ceremony. Reference experiments by `NNN_name` and
  runs by their `rid-<32 hex>` hash, with the label when it helps.
- Launch entries name the wave id.
- A one-off `behemoth` GPU grant goes in that wave's launch
  entry: grants are never stored as configuration, so the journal and the manifest's `gpu_grants`
  are its only records.

## The guard

The project's `.githooks/pre-commit` blocks commits touching `code/ config/ scripts/ evaluations/
visualizations/` without a JOURNAL.md change, and any removed JOURNAL.md line unless it comes back
with only its `rid-<32 hex>` hashes changed, and added storage paths from `Path(__file__)` or the
working directory. Fix that and commit again; bypass it (`--no-verify`) only on the user's explicit instruction.

A fresh clone has no guard until `git config core.hooksPath` prints `.githooks`. An existing project
adopts this hook (`<skills>` is this skill's parent directory) with `install -D -m 755 <skills>/research-project-init/assets/project/dot_githooks/pre-commit .githooks/pre-commit && git config core.hooksPath .githooks`.
