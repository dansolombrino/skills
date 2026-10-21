---
name: research-project-init
description: Scaffold a new or empty Research 2.0 repository with the standard experiment taxonomy, an engineering execution agreement with its envelope, exact uv environment, rig configuration, status helpers, journal guard, decision log, and host guidance, asking nothing: it finds or creates the project's private GitHub repository with `gh` and brings up rigs and secrets the safe way on its own. Use when starting, initializing, bootstrapping, or scaffolding a fresh research project; do not use to upgrade, migrate, or retrofit an existing project.
---

# Research Project Init

Scaffold only the Research 2.0 architecture. Read
[references/conventions.md](references/conventions.md) and
[references/templates.md](references/templates.md) before writing.

**These directives are the complete specification.** Scaffold from them alone. Never list, read,
or copy from another project on disk — sibling projects, prior research checkouts, home-directory
clones — to decide layout, file contents, dependencies, or defaults, and never treat an existing
project as a template or precedent however similar it looks. Where a directive is silent, take the
conservative reversible default and log it (conventions, "Autonomy"); never close the gap by
imitation. The only reads outside this
project are the ones a directive names: the installed skill directories, the literal template
values, and material the user supplies explicitly. See "Directives are closed" in
[references/conventions.md](references/conventions.md).

## Admission

1. Resolve the requested project path exactly.
2. Require a new path, an empty directory, or an empty Git repository with no research content or
   remote history. If the path contains an existing project, legacy research layout, commits that
   would need rewriting, or nontrivial files, stop as unsupported. Do not offer migration. The
   project tag written by `project-init` does not count as content: `.project.toml`,
   `.claude/settings.json`, and `.codex/config.toml` are allowed when they hold only what
   `project-init` writes. Require `.project.toml` to list `research`; when the tag is missing or
   omits `research`, stop and hand off to `project-init` to tag the project first. Never write or
   edit these three files here; the scaffold leaves them untouched and includes them in the first
   commit.
3. Take everything from the request and these defaults, never from a questionnaire: project name
   = the directory name; dispatch branch `main`; Python = the newest stable patch `uv python list`
   offers for the newest minor torch supports; uv = the installed `uv --version`; GPU smoke = the
   templated `code/common/environment_smoke.py` target; evaluations and plots are not committed;
   hub development environment `.venv` (waves use lock-keyed environments under `.envs/`); torch
   is the fixed framework. The description is optional: without one, `{{PROJECT_DESCRIPTION}}`
   falls back to the project name. Anything the user states overrides its default. Reject
   project-surface collisions.
4. The rig set is every rig the request names, else every `board.rigs` machine in the user
   registry; `leonardo` joins only when the user names it, with its account. Require an entry in the user
   registry `~/.config/rigsync/machines.toml`
   (`rig-sync` → `references/configuration.md`) for each one before scaffolding: `ssh`, `hostname`,
   its storage roots (`storage_roots`, or the single-root `storage_root`), plus
   `quota_fs`/`min_free_gb` on a quota'd rig and `[machines.<rig>.caches]`
   wherever the rig owns its shared caches. Read the entry and confirm those fields are actually
   present; an entry carrying only `ssh` is not admission, because a registry that never declares a
   volume leaves the storage checks with nothing to measure against. The registry is written once
   per machine and reused by every project, so a missing entry or field is a setup step, not a
   project decision. When one is absent, walk the user through declaring it and never infer a
   volume, never default a cache or `TMPDIR` to `$HOME` or `~/.cache`, and never copy another
   project's values.
5. Capture the engineering repo/rig/GPU/budget envelope, destinations, and — when `leonardo` is
   in the rig set — its account in the execution agreement. There is no manual/auto split.

## No questions

Init asks nothing: every step below runs without a prompt, is logged to `program/DECISIONS.md`,
and the chat gets one final outcome that lists the defaults taken so the user can correct any of
them later. A step that needs something only the user has — a rig without a registry entry, a
non-empty path, a `gh` login, a missing token — stops that step, says exactly what is missing, and
the rest carries on.

## GitHub repository

The remote is the one the request names; otherwise the agent resolves it with the `gh` and `git`
CLIs:

1. `gh auth status` must pass. When it does not, the login is the user's (suggest `gh auth
   login`); finish everything local and report the remote steps as blocked.
2. The repository is `<owner>/<project name>`, with `<owner>` the request's owner or org if one is
   named, else `gh api user --jq .login`.
3. `gh repo view <owner>/<name> --json visibility,isEmpty`: when it exists and `isEmpty` is true,
   use it. When it exists with history, stop as unsupported (admission). When it does not exist,
   create it empty and **private** — `gh repo create <owner>/<name> --private` with no README,
   license, or gitignore — and log it. Never create a public repository, never one in an org the
   request did not name, and never change an existing repository's visibility.
4. The dispatch branch is `main` unless the request names another.

## Scaffold

1. Create tracked empty directories for `checkpoints/`, `code/`, `config/`, `evaluations/`,
   `logs/`, `plots/`, `scripts/`, `tests/`, `visualizations/`, `shitpads/`, and `references/`.
   Put a `.gitkeep` in every directory that would otherwise be empty so the initial
   commit actually preserves the complete taxonomy.
2. Create `program/00-execution-agreement.md`, the current engineering contract, under 200 lines,
   and `program/DECISIONS.md` with its header and the init decisions as its first lines.
3. Create `README.md`, `AGENTS.md`, `CLAUDE.md`, `JOURNAL.md`, `EXPERIMENTS.md`, `.env.example`, `.env`,
   `.gitignore`, `.vscode/settings.json`, `sync.toml`, `pyproject.toml`, and `.python-version` from the
   templates. `.vscode/settings.json` is committed project policy, not machine-local state: it is the
   only thing that keeps an editor from watching, searching, and indexing the artifact trees, which the
   taxonomy guarantees will outnumber tracked source by orders of magnitude. `.gitignore` does not do
   this. Write it at scaffold time and substitute `{{ENVIRONMENT_NAME}}` throughout. `uv.lock`
   is not templated: it is generated by the environment gate below and committed then, so a
   scaffold that stops before that gate legitimately has no lock file yet. Never hand-write one.
   `AGENTS.md` holds the project notes; `CLAUDE.md` only points at it, so both hosts read one
   canonical file. Never duplicate the notes across the two.
   `.env` contains only secrets, repo-relative project-scoped storage, and machine-local runtime
   settings. Never write an absolute path into it and never write a machine-level cache variable:
   the rig's shared caches are the registry's to declare and `provision-env`'s to install, and a
   `.env` line silently overrides them. Written this way `.env` holds nothing rig-specific, which
   is what lets `rig-sync push-env` copy one file to every peer. Keep token
   values empty unless the user supplies them through an authorized secret source. Mirror every
   key in `.env.example` with empty values and explanatory comments.
4. Install `.githooks/pre-commit`. It requires a journal update when experiment-relevant files are
   committed; `research-journal` appends the entry without asking.
5. Create `code/common/run_id.py`, `code/common/status.py`, `code/common/paths.py` (which resolves
   the repo-relative storage values in `.env` against the shared project root, honouring the
   `RESEARCH_PROJECT_ROOT` a wave exports, and tracked inputs against the running source tree), and
   `code/common/environment_smoke.py`
   (the default `gpu_smoke` target named in `sync.toml`), and copy the canonical
   `assets/environment.py` from the environment-sync skill's own installed directory (the folder
   holding its SKILL.md, not this project) to `code/common/environment.py`.

## Git, environment, and rigs

1. Initialize Git, configure only the resolved remote/branch, then make the first commit and
   push it. Never force-push or overwrite remote history.
2. Configure `environment-sync`, run doctor, and require the exact environment plus GPU smoke
   gate on the hub for the staged lock (`provision --staged`, then `verify --staged` with the hub
   lane). Create the hub development environment named in `[environment].name` with the pinned uv
   (`UV_PROJECT_ENVIRONMENT=<name> uv sync --frozen`); it is the user's, and waves never use it.
   Log the result.
3. Bring up every intended rig in this order; each step's failure stops the ones after it for
   that rig. Every step, `provision-env` and `push-env` included, runs without a prompt
   (conventions, "Secrets"); each dry run goes to the decision log, not to a question.
   1. Confirm the user registry declares every intended rig, storage roots included (admission
      above). A rig with no entry cannot be declared in `sync.toml` at all, and one with no
      storage root fails the path gate below.
   2. Write `sync.toml` with one hand-supplied absolute `repo_path` per rig, each under one of
      that rig's declared storage roots. Declare every rig the project will dispatch to, not only the hub —
      adding one later is a hand edit that no gate prompts for.
   3. Run `rig-sync check-paths` and require it to pass. This is the only check that catches a
      `repo_path` off the rig's large volume *before* something is cloned into it. It fails, rather
      than skipping, on a rig whose registry entry declares no storage root.
   4. Run `rig-sync provision-env --dry-run` then `--confirm` for each rig that declares caches.
      Without this a rig has no `HF_HOME`/`UV_CACHE_DIR` and falls back to `~/.cache` — the small
      quota'd volume on a shared machine. It edits shell startup files, backs them up, verifies a
      non-interactive shell, and rolls itself back on failure; on a shared machine, log that it
      affects that account's every future shell.
   5. Run `rig-sync prepare` for each peer. Preserve non-empty paths and report them blocked.
   6. Run `rig-sync push-env` for each peer. `.env` is ignored by Git, so a fresh clone has none
      and nothing else will put one there. It moves secrets the safe way (0600, no value printed,
      never replaces a different peer file); a refusal for a differing file is reported with the
      key names and the rest carries on.
   7. Run `rig-sync doctor` and the `environment-sync` gates for every intended peer.
4. Finish the local scaffold when remote setup is unavailable, but report remote execution as
   blocked until its independent gates pass. Report to the chat once, at the end: what is ready,
   what is blocked and why.

Never deviate from the taxonomy, create compatibility shims, or silently weaken a gate. Never take
structure, file contents, or defaults from another project on disk.
