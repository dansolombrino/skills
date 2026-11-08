# skills repository conventions

This repository is a private plugin marketplace of personal skills, organized as domain plugins
and published to **three hosts: Codex, Claude Code, and omp**. The distributed plugins are `core`
(always on in every project) and `research` (off by default, enabled per project by its
`.project.toml` tag).

One skill tree serves every host. There is no per-host copy of a skill and no generated tree.

## Structure

- Distributed skills: `plugins/<plugin>/skills/<skill>/SKILL.md` — shared verbatim by every host.
- Plugin manifests: `plugins/<plugin>/.codex-plugin/plugin.json` and
  `plugins/<plugin>/.claude-plugin/plugin.json`. omp has none of its own: it takes the version
  from the Claude manifest.
- Marketplace catalogs: `.agents/plugins/marketplace.json` (Codex) and
  `.claude-plugin/marketplace.json` (Claude, and omp through its fallback). Never add an
  `.omp-plugin/` catalog: omp would prefer it and it could drift from the shared one.
- omp extensions: `plugins/<plugin>/package.json` lists them under `omp.extensions`; omp loads
  them from a marketplace install. omp runs no Claude command hooks, so a plugin that ships
  `hooks/hooks.json` also ships an omp extension doing the same work (research's is
  `hooks/omp_session_start.ts`). The validator checks the file is valid JSON, carries no
  `version`, and that every listed extension exists inside the plugin.
- omp install scopes: `core` is a user-scope install. A domain plugin is only ever a project-scope
  install, in each project tagged with it, made by `project-init`; a user-scope install would load
  it in every project, and omp has no per-project switch that hides one.
- Per-skill Codex metadata: `plugins/<plugin>/skills/<skill>/agents/openai.yaml`; the other hosts
  ignore it.
- Plugin subagents: `plugins/<plugin>/agents/<name>.md`, loaded by Claude Code and omp, not Codex.
  They add to a skill's work and never replace it, so a host without them still has the full
  workflow. The validator checks each one's name, description, and that the skills it lists exist
  in its plugin.
- Trigger evals: `plugins/<plugin>/evals/`, one directory per `claude plugin eval` case. Run by hand
  when a description changes (see the rule below), never in the release gate. The host-neutral
  rule and the version-bump check exempt this folder.
- Repo-local maintenance skills: `.agents/skills/<skill>/SKILL.md` is the canonical body;
  `.claude/skills/<skill>/SKILL.md` is a thin pointer to it. Never register either in the
  marketplace. Edit only the `.agents` copy; these are exempt from the host-neutral rule below.

## Rules

- Use `$marketplace-skill-creator` for new skills or plugins and `$marketplace-skill-reviewer`
  before release.
- Keep distributed skill content **host-neutral**: refer to another skill as `` `skill-name` ``
  with no `$` sigil, and describe host capabilities generically ("the host's parallel
  background-subagent capability"), not by product name. Name a host only where it identifies a
  real install target, as in the Flywheel per-host setup docs.
- A distributed skill change requires a version bump of its plugin in **both** plugin manifests,
  the only place a version lives. The validator fails if they drift, or if the Claude catalog or a
  plugin's `package.json` grows a `version`. `scripts/check_version_bump.py` fails when a plugin
  changed since the tag of its current version.
- Enable the versioned hook once per clone: `git config core.hooksPath .githooks`. It runs the
  validator and the bump check on every commit, from any host; `git commit --no-verify` skips it.
- Confirm with the user before adding a new domain plugin; register it in both catalogs, set
  `defaultEnabled: false` in its Claude manifest, and add it to `CATEGORIES` in
  `plugins/core/skills/project-init/scripts/project_init.py` (and that skill's known-categories
  line) so projects can opt into it (on omp, through a project-scope install).
- Host names in distributed `.md` files are limited to `HOST_NAME_ALLOWLIST` in
  `scripts/validate_repo.py`: files naming a real install target or a per-host configuration.
- After changing a skill's description, run its plugin's trigger suite by hand, from the plugin
  directory: `claude plugin eval . --ablation none --no-publish --max-cost-usd 5`. It costs model
  usage, measures Claude only, and loads one plugin, so it cannot test boundaries across plugins.
- A distributed skill never links to a file in another plugin: each plugin installs on its own.
  Name a skill from another plugin in backticks instead.
- Keep each skill's `agents/openai.yaml` synchronized with its `SKILL.md`. The `$<skill>` form
  stays valid there — it is Codex-only metadata.
- Run `python3 scripts/validate_repo.py` and `python3 -m unittest discover tests` after
  structural or skill changes.
- Release each changed plugin with `scripts/release.sh --plugin <plugin>`, which validates,
  creates and pushes the annotated `<plugin>--v<version>` tag, installs the release on all three
  hosts, and fails unless every host reports the new version, no domain plugin is on in the global
  Codex config, and no domain plugin is installed at omp user scope. On omp it upgrades `core` at
  user scope and a domain plugin in every project that has it at project scope (none is not
  drift). `scripts/release.sh --dry-run --plugin <plugin>` checks for drift without tagging,
  pushing, or touching a host; it does fetch `origin/main` and run the test suite. Never treat
  installing on the hosts as a separate follow-up step — that is exactly the step that gets skipped.
- Experiment with omp only in a throwaway `OMP_PROFILE=<name>` (its state lives under
  `~/.omp/profiles/<name>/`), never in the default profile, and delete the profile afterwards.
- `claude-final-v0.7.0` is the pre-Codex Claude-native state, kept for history only. Multi-host
  support was reintroduced in `research` 3.1.0 on the shared tree; do not restore that old layout.

## Rig fleet network setup

The private repo `dansolombrino/sapienza-vpn` (cloned on `rig-4090` at `~/sapienza-vpn-staging/`)
is the network layer under this repo's multi-machine skills. It holds how each rig reaches
`behemoth` through the Sapienza VPN, the reverse SSH tunnels `behemoth` uses to call back into
the rigs, the ssh multiplexing every rig needs, and each rig's systemd units.

Its role here: `rig-sync`, `sweep-dispatch`, `sweep-supervisor`, `rig-board` and
`environment-sync` assume plain `ssh <machine>` and `rsync` work in both directions between the
machines in `~/.config/rigsync/machines.toml`; that repo is what makes them work. When those skills
fail with ssh timeouts or refused connections, look there first (its README covers known
failure modes, including the VPN gateway blocking new connections for about an hour), not in
the skill code.

It is personal infrastructure, so distributed skills never reference it.
