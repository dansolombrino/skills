# skills

Personal skills distributed as a private plugin marketplace for **Codex and Claude Code**.

Skills are organized as domain plugins: one plugin per domain, each containing related skills
installed as a unit. The current plugin is **research**.

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
codex plugin add research@dansolombrino-skills
```

Claude Code:

```bash
/plugin marketplace add git@github.com:dansolombrino/skills.git
/plugin install research@dansolombrino-skills
```

Start a new session after installation so the bundled skills are loaded. Claude namespaces them
as `research:<skill>`; Codex exposes them as `$<skill>`.

Cross-rig workflows require the bundled `rig-sync` and `environment-sync` contracts plus GitHub
access on every rig; the plugin stops before remote dispatch when source or runtime parity cannot
be proven.

Research 2.0 supports fresh projects only. Its scientific orchestrator requires an explicit
scientific manual/auto mode and an independent engineering manual/auto mode, keeps current program
records separate from `EXPERIMENTS.md` and `JOURNAL.md`, and hands concrete execution to the
reproducible experiment stack. Unsupported legacy layouts stop without migration.

Scaffolded projects get `AGENTS.md` as the canonical project doc plus a thin `CLAUDE.md` that
points at it, so one set of notes serves both hosts.

The same plugin includes the complete Flywheel skill family. Flywheel MCP authentication is
machine-local; follow the bundled `flywheel` setup guidance for your host and verify a canonical
project root before any graph write. Flywheel is the curated lineage authority, while `index.md`
is only its local mirror.

## Update an installation

Codex:

```bash
codex plugin marketplace upgrade dansolombrino-skills
codex plugin remove research@dansolombrino-skills
codex plugin add research@dansolombrino-skills
```

Claude Code:

```bash
/plugin marketplace update dansolombrino-skills
/plugin uninstall research@dansolombrino-skills
/plugin install research@dansolombrino-skills
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

Bump the version, commit, then run:

```bash
scripts/release.sh
```

One command validates, tags, pushes, and installs the new version on both hosts. It **fails unless
both hosts end up reporting the released version**, so publishing and installing cannot drift
apart. Restart Claude Code and Codex afterwards; running sessions keep the version they launched
with.

Check for drift at any time without changing anything:

```bash
scripts/release.sh --dry-run
```

This prints the target version against what each host actually has installed, and exits non-zero if
they disagree.

A version bump must land in **four** places, all checked before the release proceeds:

- `plugins/<plugin>/.codex-plugin/plugin.json`
- `plugins/<plugin>/.claude-plugin/plugin.json`
- the `.claude-plugin/marketplace.json` entry
- the pin in `tests/test_research_contracts.py`

Manual fallback, if the script cannot run: validate and test, `git push origin main`,
`claude plugin tag plugins/<plugin> --push`, then the update commands above for each host.

## History

`claude-final-v0.7.0` (commit `3b3fdf1`) is the final Claude-only marketplace state from before the
Codex migration. It is kept for history only — dual-host support returned in `research` 3.1.0 on
the shared skill tree, so there is no reason to branch from that tag.
