---
name: research-project-init
description: Scaffold a new or empty Research 2.0 repository with the standard experiment taxonomy, an explicit engineering execution agreement with manual/auto modes and approval tiers, exact uv environment, rig configuration, status helpers, journal guard, and host guidance, all under one consolidated approval. Use when starting, initializing, bootstrapping, or scaffolding a fresh research project; do not use to upgrade, migrate, or retrofit an existing project.
---

# Research Project Init

Scaffold only the Research 2.0 architecture. Read
[references/conventions.md](references/conventions.md) and
[references/templates.md](references/templates.md) before writing.

**These directives are the complete specification.** Scaffold from them alone. Never list, read,
or copy from another project on disk — sibling projects, prior research checkouts, home-directory
clones — to decide layout, file contents, dependencies, or defaults, and never treat an existing
project as a template or precedent however similar it looks. Where a directive is missing or
ambiguous, stop and ask the user; never close the gap by imitation. The only reads outside this
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
3. Gather project name, path, GitHub remote, dispatch branch, exact Python patch, exact uv
   version, bounded GPU smoke command, and whether evaluations/plots are committed. A one-line
   description is optional: without one, `{{PROJECT_DESCRIPTION}}` falls back to the project name.
   The hub development environment's single directory name (waves use lock-keyed environments
   under `.envs/` instead) and the framework questions are asked in the consolidated approval
   below, never in a round of their own. Suggest `.venv` only as an option, and never infer or
   silently default the name. Reject project-surface collisions.
4. Confirm the rig set this project will ever dispatch to, and require an entry in the user
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
5. Require an explicit `engineering_mode: manual|auto`. Never infer a default. Capture the
   engineering repo/rig/GPU/budget envelope, approved destinations, and — when `leonardo` is in
   the rig set — its account and the **Leonardo core-hour ceiling** (conventions, "Approval
   tiers").

## Consolidated approval

Ask **once**, in one round, after admission and before writing anything. The single approval
covers, listed explicitly:

- the scaffold preview: the tree, every templated file, and the execution agreement;
- the first commit and its push to the approved remote and branch;
- the hub environment: the staged-lock provision, `verify`, and GPU smoke, plus the hub
  development environment;
- rig bring-up for every intended rig, including `rig-sync provision-env` (it edits shell startup
  files; on a shared machine say that it affects that account's every future shell) and
  `rig-sync push-env` (it moves secrets);
- the open questions, asked together: the hub development environment name, the framework, and
  anything else admission left open.

Both engineering modes ask this round; `engineering_mode` itself is still stated explicitly by the
user, never inferred. Once approved, every step below runs without another prompt and is reported
in one line each. A step that turns out to need something the approval did not cover — a new
destination, a non-empty path, a rig without a registry entry — stops and asks.

## Scaffold

1. Create tracked empty directories for `checkpoints/`, `code/`, `config/`, `evaluations/`,
   `logs/`, `plots/`, `scripts/`, `tests/`, `visualizations/`, `shitpads/`, and `references/`.
   Put a `.gitkeep` in every directory that would otherwise be empty so the initial
   commit actually preserves the complete taxonomy.
2. Create `program/00-execution-agreement.md`, the current engineering contract, under 200 lines.
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
   committed; `research-journal` appends the entry and shows it afterwards, in both modes.
5. Create `code/common/run_id.py`, `code/common/status.py`, `code/common/paths.py` (which resolves
   the repo-relative storage values in `.env` against the shared project root, honouring the
   `RESEARCH_PROJECT_ROOT` a wave exports, and tracked inputs against the running source tree), and
   `code/common/environment_smoke.py`
   (the default `gpu_smoke` target named in `sync.toml`), and copy the canonical
   `assets/environment.py` from the environment-sync skill's own installed directory (the folder
   holding its SKILL.md, not this project) to `code/common/environment.py`.

## Git, environment, and rigs

1. Initialize Git, configure only the approved remote/branch, then make the first commit and
   push it as the consolidated approval covers. Never force-push or overwrite remote history.
2. Configure `environment-sync`, run doctor, and require the exact environment plus GPU smoke
   gate on the hub for the staged lock (`provision --staged`, then `verify --staged` with the hub
   lane). Create the hub development environment named in `[environment].name` with the pinned uv
   (`UV_PROJECT_ENVIRONMENT=<name> uv sync --frozen`); it is the user's, and waves never use it.
   The consolidated approval covers it; report the result.
3. Bring up every intended rig in this order; each step's failure stops the ones after it. The
   consolidated approval covers every step, `provision-env` and `push-env` included; show each dry
   run in the report, not as a new question.
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
      quota'd volume on a shared machine. It edits shell startup files: outside this bundle it
      always asks, and here the consolidated approval named it.
   5. Run `rig-sync prepare` for each peer. Preserve non-empty paths and report them blocked.
   6. Run `rig-sync push-env` for each peer. `.env` is ignored by Git, so a fresh clone has none
      and nothing else will put one there. It moves secrets: outside this bundle it always asks,
      and here the consolidated approval named it.
   7. Run `rig-sync doctor` and the `environment-sync` gates for every intended peer.
4. Finish the local scaffold when remote setup is unavailable, but report remote execution as
   blocked until its independent gates pass.

Never deviate from the taxonomy, create compatibility shims, or silently weaken a gate. Never take
structure, file contents, or defaults from another project on disk.
