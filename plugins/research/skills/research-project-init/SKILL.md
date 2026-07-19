---
name: research-project-init
description: Scaffold a new research project with the standard taxonomy (checkpoints/code/config/evaluations/plots/scripts/visualizations/shitpads/references + README/CLAUDE/JOURNAL/EXPERIMENTS md files, .env, hooks). Use when the user asks to start, init, bootstrap, or scaffold a new research project or repo, or to bring an existing project up to the standard structure.
---

# research-project-init

Scaffold the standard research-project structure. The full canon (taxonomy, run_id discipline, rig fleet, invariants) is in [references/conventions.md](references/conventions.md) — read it first. File templates are in [references/templates.md](references/templates.md).

## Steps

1. **Confirm the project name and location** with the user before creating anything.
2. **Create the taxonomy** (empty dirs tracked with `.gitkeep`):
   `checkpoints/ code/ config/ evaluations/ plots/ scripts/ visualizations/ shitpads/ references/`
3. **Create the root files** from the templates:
   - `README.md` — static scaffold: taxonomy, setup steps, how running/tracking works, pointers to JOURNAL.md and EXPERIMENTS.md stating their purposes. Only ever touched again when the structure itself changes.
   - `CLAUDE.md` — thin: project-specific facts/quirks plus a light awareness map (what exists, purpose of the md files, how to deepen awareness when needed). Never duplicate skill content into it.
   - `JOURNAL.md`, `EXPERIMENTS.md` — headers only.
   - `.env.example` (seed keys) and `.env` (copied, gitignored). `.env` holds secrets + machine-varying paths ONLY.
   - `.gitignore` — ignores `.env`, `shitpads/*`, `references/*`, checkpoints/evaluations/plots content per template (keep `.gitkeep`s).
4. **Install the journal commit guard**: `.githooks/pre-commit` from the template + `git config core.hooksPath .githooks`. It blocks commits touching `code/ config/ scripts/ evaluations/ visualizations/` without touching `JOURNAL.md`.
5. **Create the shared run_id helper** `code/common/run_id.py` from the template.
6. **Point the user to rig-sync** for cross-machine syncing setup (references/ synced; shitpads/ rig-local; checkpoints movement is rig-sync's job).
7. Initialize git if needed; first commit only after the user approves the scaffold.

Ask the user before deviating from the taxonomy in any way.
