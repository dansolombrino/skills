# skills

Personal skills distributed as a private plugin marketplace for **Codex and Claude Code**.

Skills are organized as domain plugins: one plugin per domain, each containing related skills
installed as a unit. Two plugins exist:

- **core** — always on in every project: intent gating (`intent-mirror`, `intent-gate`,
  `brainstorm-hold`), `integrate-reference-code`, `project-init`, and `resirch-killalot` (ReSirch
  Killalot: one cross-project list of work that agents propose into and only the owner accepts,
  with a session-start hook and a phone-ready web app; setup in its `references/setup.md`).
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

Then turn `research` off globally, so it loads only in projects tagged with it:

- `~/.claude/settings.json`: `"enabledPlugins": {"core@dansolombrino-skills": true, "research@dansolombrino-skills": false}`
- `~/.codex/config.toml`: `[plugins."research@dansolombrino-skills"]` with `enabled = false`
  (and `core` with `enabled = true`)

Start a new session after installation so the bundled skills are loaded. Claude namespaces them
as `core:<skill>` and `research:<skill>`; Codex exposes them as `$<skill>`.

## Per-project plugins

Each project declares its categories in `.project.toml` at its root (`categories = ["research"]`;
`core` is implied). Ask for `project-init` in the project to write it: the skill's script generates
the matching `enabledPlugins` entry in `.claude/settings.json` and a managed
`[plugins."research@dansolombrino-skills"]` block in `.codex/config.toml`, preserving everything
else in both files. `project-init` can also check that the marker and both files agree.

Codex reads a project's `.codex/config.toml` only when that exact project root is trusted — a
trusted parent does not count — and says nothing otherwise; `project-init` reports the trust
state. Plugins load at session start, so a new tag takes effect in the next session.

A fresh Research 2.0 project is tagged first, then scaffolded by `research-project-init` in a new
session.

Cross-rig workflows require the bundled `rig-sync` and `environment-sync` contracts plus GitHub
access on every rig; the plugin stops before remote dispatch when source or runtime parity cannot
be proven.

Research 2.0 supports fresh projects only. Every scaffolded project carries an explicit
engineering execution agreement with a manual/auto mode and an approved envelope; the engineering
skills read it before any material action and stop when it is absent. `EXPERIMENTS.md` holds run
state and `JOURNAL.md` holds the narrative. Unsupported legacy layouts stop without migration.

Scaffolded projects get `AGENTS.md` as the canonical project doc plus a thin `CLAUDE.md` that
points at it, so one set of notes serves both hosts.

## Update an installation

Codex:

```bash
codex plugin marketplace upgrade dansolombrino-skills
codex plugin remove <plugin>@dansolombrino-skills
codex plugin add <plugin>@dansolombrino-skills
```

Claude Code:

```bash
/plugin marketplace update dansolombrino-skills
/plugin uninstall <plugin>@dansolombrino-skills
/plugin install <plugin>@dansolombrino-skills
```

Restart the desktop app or start a new CLI session after updating.

## Develop skills in this repository

- `marketplace-skill-creator` adds skills or plugins while maintaining repository metadata.
- `marketplace-skill-reviewer` audits a skill without editing it.
  (Codex invokes both with a `$` prefix.) Edit only the `.agents/skills/` copy — `.claude/skills/`
  just points at it.
- `python3 scripts/validate_repo.py` validates both marketplaces, both manifests, and every skill.
- `python3 -m unittest discover tests` runs the contract, dual-host, and script suites.

Distributed skill content must be host-neutral — see `AGENTS.md`. Any change to a distributed
skill requires a version bump in `plugins/<plugin>/.codex-plugin/plugin.json`,
`plugins/<plugin>/.claude-plugin/plugin.json`, and the `.claude-plugin/marketplace.json` entry;
validation fails if they drift.

## Release

Bump the version, commit, then run once per changed plugin:

```bash
scripts/release.sh --plugin core
scripts/release.sh --plugin research
```

One command validates, tags, pushes, and installs the new version on both hosts. It **fails unless
both hosts end up reporting the released version**, so publishing and installing cannot drift
apart. Restart Claude Code and Codex afterwards; running sessions keep the version they launched
with.

Check for drift at any time without changing anything:

```bash
scripts/release.sh --dry-run --plugin <plugin>
```

This prints the target version against what each host actually has installed, and exits non-zero if
they disagree.

A version bump must land in **four** places, all checked before the release proceeds:

- `plugins/<plugin>/.codex-plugin/plugin.json`
- `plugins/<plugin>/.claude-plugin/plugin.json`
- the `.claude-plugin/marketplace.json` entry
- the pin in `tests/test_<plugin>_contracts.py`

Manual fallback, if the script cannot run: validate and test, `git push origin main`,
`claude plugin tag plugins/<plugin> --push`, then the update commands above for each host.

## History

`claude-final-v0.7.0` (commit `3b3fdf1`) is the final Claude-only marketplace state from before the
Codex migration. It is kept for history only — dual-host support returned in `research` 3.1.0 on
the shared skill tree, so there is no reason to branch from that tag.
