---
name: research-project-init
description: Scaffold a new or empty Research 2.0 repository - the standard experiment taxonomy, exact uv environment, rig configuration, status and run_id helpers, journal guard, host notes, and the project's private GitHub repository. Use when starting, initializing, bootstrapping, or scaffolding a fresh research project; do not use to upgrade, migrate, or retrofit an existing project.
---

# Research Project Init

Scaffold a fresh Research 2.0 project from
[references/conventions.md](references/conventions.md) and
[references/templates.md](references/templates.md); this project's templates are the reference,
not other projects on disk.

## Admission

Never ask what this skill resolves (defaults, rigs, re-init, GitHub repo): act, then report
it. Stop only for a missing registry entry, a failed `gh auth status`, or existing content.

1. The path is new, an empty directory, or an empty Git repository; one whose commits hold no
   files counts as empty: delete `.git` and `git init -b main`. A path holding an existing
   project, research content, or commits with files is unsupported. The project tag written by
   `project-init` does not count as content: `.project.toml`, `.claude/settings.json`, and
   `.codex/config.toml` are allowed when they hold only what `project-init` writes; leave them
   untouched and include them in the first commit. Require `.project.toml` to list `research`; otherwise hand off to
   `project-init` to tag the project first.
2. Defaults, each overridden by anything the user states: project name = directory name; branch
   `main`; Python = the newest stable patch `uv python list` offers for the newest minor torch
   supports; uv = the installed `uv --version`; hub development environment `.venv`; torch as the
   only dependency; evaluations and plots not committed. There is no project description.
3. Rigs: the ones the user names, else every `board.rigs` machine in the user registry, never
   asked; `leonardo` only when named. A rig's `repo_path` mirrors the hub's under its storage
   root, like the user's other projects. Each needs a complete entry in
   `~/.config/rigsync/machines.toml` (`rig-sync` → `references/configuration.md`): `ssh`,
   `hostname`, storage roots (`storage_roots` or `storage_root`), `quota_fs`/`min_free_gb` on a
   quota'd rig, and `[machines.<rig>.caches]` where the rig owns shared caches. A missing entry
   or field is a once-per-machine setup step to do with the user, never something to infer.

## GitHub repository

Use the remote the user names; otherwise:

1. `gh auth status` must pass (a login is the user's: suggest `gh auth login`, finish the local
   scaffold, and report the remote steps as blocked).
2. The repository is `<owner>/<project name>`, `<owner>` being the named owner or org, else
   `gh api user --jq .login`.
3. `gh repo view <owner>/<name> --json visibility,isEmpty`: an existing empty repository is used;
   one with history is unsupported. A missing one is created empty and private:
   `gh repo create <owner>/<name> --private`, no README, license, or gitignore. Never create a
   public repository, never create one in an org the user did not name, and never change an
   existing repository's visibility.

## Scaffold

1. Create `checkpoints/`, `code/`, `config/`, `evaluations/`, `logs/`, `plots/`, `scripts/`,
   `tests/`, `visualizations/`, `shitpads/`, and `references/`, with a `.gitkeep` in every
   directory that would otherwise be empty.
2. From the templates: `README.md`, `AGENTS.md`, `CLAUDE.md`, `JOURNAL.md`, `EXPERIMENTS.md`,
   `.env.example`, `.env`, `.gitignore`, `.vscode/settings.json`, `sync.toml`, `pyproject.toml`,
   and `.python-version`. `AGENTS.md` holds the notes and `CLAUDE.md` only points at it.
   Never duplicate the notes across the two. `uv.lock`
   is not templated: the environment gate generates it. `.env` holds secrets (empty unless the
   user supplies them), repo-relative storage, and machine-local settings only.
3. Install `.githooks/pre-commit` (the journal guard) with `git config core.hooksPath .githooks`.
4. Create `code/common/run_id.py`, `status.py`, `paths.py`, and `environment_smoke.py` (the
   default `gpu_smoke` target), and copy `assets/environment.py` from the `environment-sync`
   skill's installed directory to `code/common/environment.py`.

## Git, environment, and rigs

1. Initialize Git with the resolved remote and branch, make the first commit, and push it.
2. Run `environment-sync` doctor, then `provision --staged` and `verify --staged` on the hub, and
   create the hub development environment (`UV_PROJECT_ENVIRONMENT=<name> uv sync --frozen`).
   Commit `uv.lock`.
3. Bring up every intended rig in this order; a failed step stops the later ones for that rig:
   1. `sync.toml` gets one absolute `repo_path` per rig, under one of its declared storage roots.
   2. `rig-sync check-paths` must pass: it catches a path off the rig's large volume before
      anything is cloned there.
   3. `rig-sync provision-env --dry-run`, then `--confirm`, for each rig that declares caches
      (otherwise the rig falls back to `~/.cache`; the script backs up and verifies the shell
      files and rolls itself back on failure).
   4. `rig-sync prepare` for each peer.
   5. `rig-sync push-env` for each peer: a fresh clone has no `.env`.
   6. `rig-sync doctor` and the `environment-sync` gates for every peer.
4. Report what is ready, what is blocked, and the defaults taken.
