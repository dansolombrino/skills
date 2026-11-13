# skills

Personal skills distributed as a private plugin marketplace for **Codex, Claude Code, and omp**.

Skills are organized as domain plugins: one plugin per domain, each containing related skills
installed as a unit. Two plugins exist:

- **core** — always on in every project: `progress-updates`, `project-init`, `service-hub`,
  `behemoth-gpu-niceness`, `token-usage`, `agent-fleet`, and its Orca adapter `agent-fleet-orca`.
- **research** — Research 2.0 GPU experiment workflow. Off by default; enabled per project.

All three hosts read the same `SKILL.md` files. Only the manifests differ, so a skill is written
once and stays identical everywhere.

## Layout

```text
.agents/plugins/marketplace.json          # Codex marketplace catalog
.claude-plugin/marketplace.json           # Claude marketplace catalog; omp reads it too
.agents/skills/                           # repo-local maintenance skills (canonical body)
.claude/skills/                           # thin pointers so Claude sees the same two skills
plugins/<plugin>/
├── .codex-plugin/plugin.json             # Codex manifest and version
├── .claude-plugin/plugin.json            # Claude manifest and version (lockstep); omp reads it
├── package.json                          # omp extensions (`omp.extensions`), where a plugin has them
├── hooks/                                # Claude hooks.json and the omp extension mirroring it
├── agents/<name>.md                      # subagents for Claude Code and omp, where a plugin has them
├── evals/<case>/                         # trigger evals, run by hand with `claude plugin eval`
└── skills/<skill>/
    ├── SKILL.md                          # workflow instructions, shared by every host
    ├── agents/openai.yaml                # Codex UI metadata; ignored by the other hosts
    └── references/                       # detailed material loaded as needed
```

omp needs no catalog or manifest of its own. It reads `.claude-plugin/marketplace.json` (its
fallback when there is no `.omp-plugin/` catalog) and takes a plugin's version from its Claude
manifest. omp does not run Claude's command hooks, so a plugin that ships `hooks/hooks.json` also
ships an omp extension doing the same work, listed under `omp.extensions` in its `package.json`.
That file never carries a `version`.

## Install on a new machine

The repository is private, so the machine must have GitHub access: SSH for the Codex and Claude
Code commands below, and git's HTTPS credentials (for example through `gh auth setup-git`) for omp,
which clones the `owner/repo` shorthand over HTTPS.

Codex:

```bash
codex plugin marketplace add git@github.com:dansolombrino/skills.git
codex plugin add core@dansolombrino-skills
codex plugin add research@dansolombrino-skills
```

Claude Code:

```bash
/plugin marketplace add git@github.com:dansolombrino/skills.git
/plugin install core@dansolombrino-skills
/plugin install research@dansolombrino-skills
```

omp:

```bash
omp plugin marketplace add dansolombrino/skills
omp plugin install core@dansolombrino-skills
```

Do not install `research` at omp user scope: a user-scope install loads in every project, and omp
has no per-project switch that hides it. omp gets `research` only through a project-scope install
in each tagged project, which `project-init` makes (see below).

`research` must be off globally, so it loads only in projects tagged with it. Claude installs it
off, through `defaultEnabled: false` in its manifest; omp never has it globally. Codex has no such
field and `codex plugin add` always turns a plugin on, so turn it off yourself:

```bash
python3 scripts/codex_plugin_state.py set research@dansolombrino-skills false
```

or set `enabled = false` under `[plugins."research@dansolombrino-skills"]` in `~/.codex/config.toml`.

Start a new session after installation so the bundled skills are loaded; `/reload-plugins` in
Claude Code or omp also applies them to a running session (omp loads a new extension only in a new
session). Claude namespaces them as `core:<skill>` and `research:<skill>`; Codex exposes them as
`$<skill>`.

## Per-project plugins

Each project declares its categories in `.project.toml` at its root (`categories = ["research"]`;
`core` is implied). Ask for `project-init` in the project to write it: the skill's script generates
the matching `enabledPlugins` entry in `.claude/settings.json` and a managed
`[plugins."research@dansolombrino-skills"]` block in `.codex/config.toml`, preserving everything
else in both files. With `omp` on PATH it also makes an enabled project-scope omp install
(`omp plugin install --scope project research@dansolombrino-skills`) of each tagged plugin, recorded
in the project's `.omp/plugins/installed_plugins.json`, and removes those of untagged ones. That
directory is machine-local and Git-ignored, so each clone runs `project-init` once. `project-init`
can also check that the marker, both files, and the omp installs agree.

Codex reads a project's `.codex/config.toml` only when that exact project root is trusted — a
trusted parent does not count — and says nothing otherwise; `project-init` reports the trust
state. Plugins load at session start, so a new tag takes effect in a new session, or on Claude Code
and omp after `/reload-plugins`.

`project-init` writes no marketplace into a project, so a tagged clone on a new machine still needs
the install steps above. In a `research` project it also writes the deny rule `Read(./.env)` to
`.claude/settings.json`, which blocks Claude Code's own edit and write tools on `.env` as well;
scripts that read the file in a subprocess keep working.

A fresh Research 2.0 project is tagged first, then scaffolded by `research-project-init` in a new
session or after a reload.

Cross-rig workflows require the bundled `rig-sync` and `environment-sync` contracts plus GitHub
access on every rig; the plugin stops before remote dispatch when source or runtime parity cannot
be proven.

Research 2.0 supports fresh projects only. Init creates the project's empty private GitHub
repository with `gh` and moves the project's secrets to the rigs the safe way (owner-only, never
printed, never replacing a different file). The skills carry facts, commands, and taste rules;
hard limits such as `behemoth` being gpu0-only without a per-wave grant are enforced by the
scripts. New experiments identify runs by a versioned `rid-<32 hex>` hash (`hashed-v2`) that
`run-identity` looks up and migrates; older layouts are documented in its `legacy-layouts`
reference. `EXPERIMENTS.md` holds run state and `JOURNAL.md` holds the narrative.

A dispatched wave needs no chat. The hub's `sweep-supervisor` service feeds and recovers it, waits
for GPUs another wave holds instead of leaving them out, pushes notices through
`notify_command` (finished, failures, rig down, a Leonardo certificate about to expire), and when
the wave finishes prunes it and runs an unattended follow-up that renders its plots under the full
plot contract and journals the results. A deterministic engineering error found mid-wave is fixed
and its runs dispatched again by a follow-up too (at most two automatic rounds). Running from the
installed plugin, the service moves itself onto each new release.

Progress is at a glance in the `rig-board` viewer's Experiments tab. A chat shows the status table
on request, and a background `watch` brings each new warning and the wave's end into the chat
without timed printouts.

Scaffolded projects get `AGENTS.md` as the canonical project doc plus a thin `CLAUDE.md` that
points at it, so one set of notes serves every host.

## Update an installation

Codex:

```bash
codex plugin marketplace upgrade dansolombrino-skills
codex plugin remove <plugin>@dansolombrino-skills
codex plugin add <plugin>@dansolombrino-skills
```

Codex has no `update`, and `add` turns the plugin on globally. After adding a domain plugin such as
`research`, turn it off again as in the install steps. `scripts/release.sh` does this for you.

Claude Code:

```bash
claude plugin marketplace update dansolombrino-skills
claude plugin update <plugin>@dansolombrino-skills --scope user
```

Do not uninstall and reinstall: a user-scope install rewrites the plugin's enabled entry. With
`autoUpdate` on for the marketplace, Claude Code also updates by itself in the background.

Restart the desktop app or start a new CLI session after updating; Claude Code's `/reload-plugins`
also applies an update to a running session.

omp:

```bash
omp plugin marketplace update dansolombrino-skills
omp plugin upgrade --scope user core@dansolombrino-skills
# in each project with research installed:
omp plugin upgrade --scope project research@dansolombrino-skills
```

`omp plugin upgrade` acts only on the project of its working directory. omp lists every project with
a project-scope install in `project_registries.json` in its user plugins directory;
`python3 scripts/omp_plugin_state.py projects research@dansolombrino-skills` prints them.
Restart omp sessions after updating, or run `/reload-plugins`; a changed extension loads only in a
new session.

## Develop skills in this repository

- `marketplace-skill-creator` adds skills or plugins while maintaining repository metadata.
- `marketplace-skill-reviewer` audits a skill without editing it.
  (Codex invokes both with a `$` prefix.) Edit only the `.agents/skills/` copy — `.claude/skills/`
  just points at it.
- `python3 scripts/validate_repo.py` validates both catalogs, both manifests, each plugin's omp
  `package.json`, and every skill.
- `TMPDIR=/tmp python3 -m unittest discover tests` runs the contract, host, and script suites.
- `git config core.hooksPath .githooks` once per clone makes every commit run the validator and
  `scripts/check_version_bump.py`.
- From a plugin directory, `claude plugin eval . --ablation none --no-publish --max-cost-usd 5` runs
  its trigger suite by hand. It costs model usage and is not part of the release gate.

Distributed skill content must be host-neutral — see `AGENTS.md`. Any change to a distributed
skill requires a version bump in `plugins/<plugin>/.codex-plugin/plugin.json` and
`plugins/<plugin>/.claude-plugin/plugin.json`; validation fails if they drift.

## Release

Bump the version, commit, then run once per changed plugin:

```bash
scripts/release.sh --plugin core
scripts/release.sh --plugin research
```

One command validates, tags, pushes, and installs the new version on all three hosts. It **fails
unless every host ends up reporting the released version**, so publishing and installing cannot
drift apart. Before pushing it runs `claude plugin validate --strict` and `claude plugin tag
--dry-run`, so a forgotten bump stops it there. It keeps each plugin's `enabled` state in the
global Codex config across Codex's remove-then-add, and fails if a domain plugin ends up on there.
On omp it adds the marketplace from `dansolombrino/skills` if omp has none (and only warns if omp
has it from another source), installs or upgrades `core` at user scope, and upgrades a domain
plugin in every project that has it installed at project scope; it never installs a domain plugin
at user scope and fails if one is there. A domain plugin that no project has installed in omp
shows as `none`, which is not drift. Restart Claude Code, Codex, and omp afterwards; running
sessions keep the version they launched with.

Check for drift at any time without tagging, pushing, or touching any host:

```bash
scripts/release.sh --dry-run --plugin <plugin>
```

This prints the target version against what each host actually has installed, warns if the plugin
changed since the tag of that version, and exits non-zero if the hosts disagree, a domain plugin is
on globally in Codex, or a domain plugin is installed at omp user scope. It does run
`git fetch origin main`, which updates `origin/main`, and the full test suite.

A version bump goes in `plugins/<plugin>/.codex-plugin/plugin.json` and
`plugins/<plugin>/.claude-plugin/plugin.json`, the only two places a version lives (omp reads the
Claude one; a plugin's `package.json` must not carry one).

Manual fallback, if the script cannot run: validate and test, `git push origin main`,
`claude plugin tag plugins/<plugin> --push`, then the update commands above for each host.

## History

`claude-final-v0.7.0` (commit `3b3fdf1`) is the final Claude-only marketplace state from before the
Codex migration. It is kept for history only — dual-host support returned in `research` 3.1.0 on
the shared skill tree, so there is no reason to branch from that tag.
