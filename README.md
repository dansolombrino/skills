# skills

Personal skills distributed as a private plugin marketplace for **Codex and Claude Code**.

Skills are organized as domain plugins: one plugin per domain, each containing related skills
installed as a unit. Two plugins exist:

- **core** — always on in every project: `progress-updates`, `project-init`, `service-hub`, and
  `behemoth-gpu-niceness`.
- **research** — Research 2.0 GPU experiment workflow. Off by default; enabled per project.

Both hosts read the same `SKILL.md` files. Only the manifests differ, so a skill is written once
and stays identical everywhere.

## Layout

```text
.agents/plugins/marketplace.json          # Codex marketplace catalog
.claude-plugin/marketplace.json           # Claude marketplace catalog
.agents/skills/                           # repo-local maintenance skills (canonical body)
.claude/skills/                           # thin pointers so Claude sees the same two skills
plugins/<plugin>/
├── .codex-plugin/plugin.json             # Codex manifest and version
├── .claude-plugin/plugin.json            # Claude manifest and version (kept in lockstep)
├── agents/<name>.md                      # Claude subagents, where a plugin has them
├── evals/<case>/                         # trigger evals, run by hand with `claude plugin eval`
└── skills/<skill>/
    ├── SKILL.md                          # workflow instructions, shared by both hosts
    ├── agents/openai.yaml                # Codex UI metadata; ignored by Claude
    └── references/                       # detailed material loaded as needed
```

## Install on a new machine

The repository is private, so the machine must have GitHub SSH access.

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

`research` must be off globally, so it loads only in projects tagged with it. Claude installs it
off, through `defaultEnabled: false` in its manifest. Codex has no such field and `codex plugin add`
always turns a plugin on, so turn it off yourself:

```bash
python3 scripts/codex_plugin_state.py set research@dansolombrino-skills false
```

or set `enabled = false` under `[plugins."research@dansolombrino-skills"]` in `~/.codex/config.toml`.

Start a new session after installation so the bundled skills are loaded; Claude Code's
`/reload-plugins` also applies them to a running session. Claude namespaces them as `core:<skill>`
and `research:<skill>`; Codex exposes them as `$<skill>`.

## Per-project plugins

Each project declares its categories in `.project.toml` at its root (`categories = ["research"]`;
`core` is implied). Ask for `project-init` in the project to write it: the skill's script generates
the matching `enabledPlugins` entry in `.claude/settings.json` and a managed
`[plugins."research@dansolombrino-skills"]` block in `.codex/config.toml`, preserving everything
else in both files. `project-init` can also check that the marker and both files agree.

Codex reads a project's `.codex/config.toml` only when that exact project root is trusted — a
trusted parent does not count — and says nothing otherwise; `project-init` reports the trust
state. Plugins load at session start, so a new tag takes effect in a new session, or on Claude Code
after `/reload-plugins`.

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
points at it, so one set of notes serves both hosts.

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

## Develop skills in this repository

- `marketplace-skill-creator` adds skills or plugins while maintaining repository metadata.
- `marketplace-skill-reviewer` audits a skill without editing it.
  (Codex invokes both with a `$` prefix.) Edit only the `.agents/skills/` copy — `.claude/skills/`
  just points at it.
- `python3 scripts/validate_repo.py` validates both marketplaces, both manifests, and every skill.
- `TMPDIR=/tmp python3 -m unittest discover tests` runs the contract, dual-host, and script suites.
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

One command validates, tags, pushes, and installs the new version on both hosts. It **fails unless
both hosts end up reporting the released version**, so publishing and installing cannot drift
apart. Before pushing it runs `claude plugin validate --strict` and `claude plugin tag --dry-run`,
so a forgotten bump stops it there. It keeps each plugin's `enabled` state in the global Codex
config across Codex's remove-then-add, and fails if a domain plugin ends up on there. Restart
Claude Code and Codex afterwards; running sessions keep the version they launched with.

Check for drift at any time without tagging, pushing, or touching either host:

```bash
scripts/release.sh --dry-run --plugin <plugin>
```

This prints the target version against what each host actually has installed, warns if the plugin
changed since the tag of that version, and exits non-zero if the hosts disagree or a domain plugin
is on globally in Codex. It does run `git fetch origin main`, which updates `origin/main`, and the
full test suite.

A version bump goes in `plugins/<plugin>/.codex-plugin/plugin.json` and
`plugins/<plugin>/.claude-plugin/plugin.json`, the only two places a version lives.

Manual fallback, if the script cannot run: validate and test, `git push origin main`,
`claude plugin tag plugins/<plugin> --push`, then the update commands above for each host.

## History

`claude-final-v0.7.0` (commit `3b3fdf1`) is the final Claude-only marketplace state from before the
Codex migration. It is kept for history only — dual-host support returned in `research` 3.1.0 on
the shared skill tree, so there is no reason to branch from that tag.
