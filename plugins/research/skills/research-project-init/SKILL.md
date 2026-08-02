---
name: research-project-init
description: Scaffold a research repository with the standard taxonomy (checkpoints/code/config/evaluations/logs/plots/scripts/visualizations/shitpads/references + README/AGENTS/JOURNAL/EXPERIMENTS md files, .env, hooks). Use when the user asks to start, initialize, bootstrap, or scaffold a research project, or to bring an existing research repository up to the standard structure.
---

# research-project-init

Scaffold the standard research-project structure. The full canon (taxonomy, run_id discipline, rig fleet, invariants) is in [references/conventions.md](references/conventions.md) — read it first. File templates are in [references/templates.md](references/templates.md).

## Steps

1. **Confirm the project name, location, one-line description, GitHub remote, and dispatch branch**
   with the user before creating anything. Also ask whether `evaluations/` and `plots/` should be
   committed; propose ignoring both, but the user decides.
2. **Create the taxonomy** (empty dirs tracked with `.gitkeep`):
   `checkpoints/ code/ config/ evaluations/ logs/ plots/ scripts/ visualizations/ shitpads/ references/`
3. **Create the root files** from the templates:
   - `README.md` — static scaffold: taxonomy, setup steps, how running/tracking works, pointers to JOURNAL.md and EXPERIMENTS.md stating their purposes. Only ever touched again when the structure itself changes.
   - `AGENTS.md` — thin: project-specific facts/quirks plus a light awareness map (what exists, purpose of the md files, how to deepen awareness when needed). Never duplicate skill content into it.
   - `JOURNAL.md`, `EXPERIMENTS.md` — headers only.
   - `.env.example` (seed keys) and `.env` (copied, gitignored). `.env` holds secrets + machine-varying paths ONLY.
   - `.gitignore` — ignores `.env`, `shitpads/*`, `references/*`, checkpoints/evaluations/plots content per template (keep `.gitkeep`s).
4. **Install the journal commit guard**: `.githooks/pre-commit` from the template + `git config core.hooksPath .githooks`. It blocks commits touching `code/ config/ scripts/ evaluations/ visualizations/` without touching `JOURNAL.md`.
5. **Create the shared helpers** `code/common/run_id.py` and `code/common/status.py` from the templates.
6. Initialize Git if needed, configure the approved remote/branch, and create/push the first
   commit only after the user approves the scaffold. Never force-push or overwrite an existing
   remote history.
7. **Configure `$rig-sync`**: create `sync.toml` with the same Git remote/branch, preview and
   approve `rigsync.py prepare` to clone new empty rig paths, then run `doctor` for every intended
   peer. Git distributes launch source; references and artifacts move only through `$rig-sync`;
   `shitpads/` and `logs/` remain rig-local. Preserve non-empty non-Git peer paths and report them
   blocked rather than converting them. If doctor fails, finish the local scaffold but report
   remote execution as blocked.

Ask the user before deviating from the taxonomy in any way.
