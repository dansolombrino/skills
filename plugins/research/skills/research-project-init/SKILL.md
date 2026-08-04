---
name: research-project-init
description: Scaffold a new or empty Research 2.0 repository with the standard experiment taxonomy, scientific program records, explicit manual/auto modes, Flywheel integration surfaces, exact uv environment, rig configuration, status helpers, journal guard, and Codex guidance. Use when starting, initializing, bootstrapping, or scaffolding a fresh research project; do not use to upgrade, migrate, or retrofit an existing project.
---

# Research Project Init

Scaffold only the Research 2.0 architecture. Read
[references/conventions.md](references/conventions.md) and
[references/templates.md](references/templates.md) before writing.

## Admission

1. Resolve the requested project path exactly.
2. Require a new path, an empty directory, or an empty Git repository with no research content or
   remote history. If the path contains an existing project, legacy research layout, commits that
   would need rewriting, or nontrivial files, stop as unsupported. Do not offer migration.
3. Confirm project name, path, one-line description, GitHub remote, dispatch branch, exact Python
   patch, exact uv version, bounded GPU smoke command, and whether evaluations/plots are committed.
4. Require explicit `scientific_mode: manual|auto` and `engineering_mode: manual|auto`. Never
   infer a default. Capture the initial scientific scope, engineering repo/rig/GPU/budget envelope,
   approved destinations, and protected choices.
5. Ask whether a canonical Flywheel root already exists. Verify an explicit root id/title before
   creating `.flywheel.json`; otherwise leave Flywheel setup incomplete and make its write gate
   visible without blocking the local scaffold.

## Scaffold

1. Create tracked empty directories for `checkpoints/`, `code/`, `config/`, `evaluations/`,
   `logs/`, `plots/`, `scripts/`, `visualizations/`, `shitpads/`, and `references/`.
   Put a `.gitkeep` in every directory that would otherwise be empty so the initial
   commit actually preserves the complete taxonomy.
2. Create the scientific records:
   - `program.md` current index;
   - `program/00-execution-agreement.md` current contract under 200 lines;
   - `program/decision-register.md` decision history;
   - `index.md` local Flywheel mirror;
   - ignored `orchestration/control/events.jsonl` and phase traces as needed.
3. Create `README.md`, `AGENTS.md`, `JOURNAL.md`, `EXPERIMENTS.md`, `.env.example`, `.env`,
   `.gitignore`, `sync.toml`, `pyproject.toml`, `uv.lock`, and `.python-version` from the templates.
   `.env` contains only secrets and machine-varying paths. Put Flywheel root placeholders in
   `.env.example`; create `.flywheel.json` only from a verified root.
4. Install `.githooks/pre-commit`. It requires a journal update when experiment-relevant files are
   committed; the active layer's mode determines whether the entry is user-approved or delegated.
5. Create `code/common/run_id.py` and `code/common/status.py`, and copy
   `$environment-sync`'s canonical `assets/environment.py` to `code/common/environment.py`.

## Git, environment, and rigs

1. Initialize Git, configure only the approved remote/branch, and preview the scaffold. In
   engineering-manual mode, wait before the first commit/push. In engineering-auto mode, commit and
   push only when the approved envelope explicitly covers that remote and branch. Never force-push
   or overwrite remote history.
2. Configure `$environment-sync`, run doctor, and require the exact environment plus GPU smoke
   gate. Manual mode requires its preview approval; auto mode may confirm inside the envelope.
3. Configure `$rig-sync`, prepare only approved empty rig paths, and run doctor for every intended
   peer. Preserve non-empty paths and report them blocked. Manual mode requires preview approval;
   auto mode may confirm inside the envelope.
4. Finish the local scaffold when remote setup is unavailable, but report remote execution and
   Flywheel publication as blocked until their independent gates pass.

Never deviate from the taxonomy, create compatibility shims, or silently weaken a gate.
