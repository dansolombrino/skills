---
name: research-project-init
description: Scaffold a new or empty Research 2.0 repository - the standard experiment taxonomy, exact uv environment, rig configuration, status and run_id helpers, journal guard, host notes, and the project's private GitHub repository. Use when starting, initializing, bootstrapping, or scaffolding a fresh research project; do not use to upgrade, migrate, or retrofit an existing project.
---

# Research Project Init

Scaffold a fresh Research 2.0 project with `scripts/scaffold.py` (this skill's installed directory),
per [references/conventions.md](references/conventions.md) and
[references/templates.md](references/templates.md); this project's templates are the reference,
not other projects on disk.

## Admission

Never ask what this skill resolves (defaults, rigs, re-init, GitHub repo, tagging): act, then report
it. Stop only for a missing registry entry, a failed `gh auth status`, or existing content.

1. The path is new, an empty directory, or an empty Git repository; one whose commits hold no
   files counts as empty: delete `.git` and `git init -b <branch>`. A path holding an existing
   project, research content, or commits with files is unsupported. The project tag written by
   `project-init` does not count as content: `.project.toml`, `.claude/settings.json`, and
   `.codex/config.toml` are allowed when they hold only what `project-init` writes; leave them
   untouched. Require `.project.toml` to list `research`; otherwise hand off to `project-init`
   to tag it, in this run (step 1 below).
2. Defaults, each overridden by anything the user states: project name = directory name; branch
   `main`; Python = the newest stable patch `uv python list` offers for the newest minor torch
   supports; uv = the installed `uv --version`; hub development environment `.venv`; the template's
   standard dependencies; evaluations and plots not committed. There is no project description.
3. Rigs: the ones the user names, else every `board.rigs` machine in the user registry, never
   asked; `leonardo` only when named. A rig's `repo_path` mirrors the hub's under its storage
   root. Each needs a complete entry in `~/.config/rigsync/machines.toml` (`rig-sync` →
   `references/configuration.md`): `ssh`, `hostname`, storage roots, `quota_fs`/`min_free_gb` on a
   quota'd rig, and `[machines.<rig>.caches]` where the rig owns shared caches. A missing entry
   or field is a once-per-machine setup step to do with the user, never something to infer.
   So is a project path under none of the hub's declared roots: stop before writing anything and
   report the two fixes, a path under a declared root or the volume added to the registry.

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

## Order

One unattended run. No step waits for the user or a new session: a tag only decides
which plugins the next session loads, and everything here runs by path.

1. `git init -b <branch>` unless the path is a repository already, since tagging needs one. If
   untagged, run `project-init`'s `apply --categories research`; this request answers its questions.
2. `python3 scripts/scaffold.py <path> --python X.Y.Z --uv X.Y.Z` (other overridden defaults: its
   `--help` flags), then again with `--confirm`.
   It writes the taxonomy; root files such as `AGENTS.md`, `CLAUDE.md`, `.env` (0600), `sync.toml`,
   and `pyproject.toml`; `code/common/` (with `environment_smoke.py`, the default `gpu_smoke`
   target); and the journal guard, with `core.hooksPath` set. `AGENTS.md` holds the notes and
   `CLAUDE.md` only points at it. Never duplicate the notes across the two. `uv.lock`
   is not templated: step 4 generates it.
3. Append a `[machines.<rig>]` `repo_path` to `sync.toml` for the hub (its real path) and each rig.
4. `git remote add` the resolved remote, run `uv lock`, then make the first commit with everything:
   tag files, scaffold, `sync.toml`, `uv.lock`. Asking for a scaffold is asking for that commit.
   Push it.
5. On the hub: `environment-sync` doctor, `provision --staged`, and `verify --staged`; then the
   development environment (`UV_PROJECT_ENVIRONMENT=<name> uv sync --frozen`).
6. Bring up every intended rig in this order; a failed step stops the later ones for that rig:
   1. `rig-sync check-paths` must pass: it catches a path off the rig's large volume.
   2. `rig-sync provision-env --dry-run`, then `--confirm`, for each rig that declares caches
      (otherwise the rig falls back to `~/.cache`).
   3. `rig-sync prepare` for each peer.
   4. `rig-sync push-env` for each peer: a fresh clone has no `.env`.
   5. `rig-sync doctor` and the `environment-sync` gates for every peer.
7. Report what is ready, what is blocked, the defaults taken, and that the plugin loads from the
   next session if the tag is new.
