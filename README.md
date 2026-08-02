# skills

Personal Codex skills distributed as a private plugin marketplace.

Skills are organized as domain plugins: one plugin per domain, each containing related skills
installed as a unit. The current plugin is **research**.

## Layout

```text
.agents/plugins/marketplace.json          # Codex marketplace catalog
.agents/skills/                           # repo-local marketplace maintenance skills
plugins/<plugin>/
├── .codex-plugin/plugin.json             # plugin manifest and version
└── skills/<skill>/
    ├── SKILL.md                          # workflow instructions
    ├── agents/openai.yaml                # Codex UI metadata
    └── references/                       # detailed material loaded as needed
```

## Install on a new machine

The repository is private, so the machine must have GitHub SSH access.

```bash
codex plugin marketplace add git@github.com:dansolombrino/skills.git
codex plugin add research@dansolombrino-skills
```

Start a new Codex CLI or desktop session after installation so the bundled skills are loaded.
Cross-rig workflows additionally require the separately maintained `rig-sync` setup; the plugin
stops before remote dispatch when that prerequisite is unavailable.

## Update an installation

```bash
codex plugin marketplace upgrade dansolombrino-skills
codex plugin remove research@dansolombrino-skills
codex plugin add research@dansolombrino-skills
```

Restart the desktop app or start a new CLI session after updating.

## Develop skills in this repository

- `$marketplace-skill-creator` adds skills or plugins while maintaining repository metadata.
- `$marketplace-skill-reviewer` audits a skill without editing it.
- `python3 scripts/validate_repo.py` validates the marketplace, plugin, and every skill.

Any change to a distributed skill requires a version bump in
`plugins/<plugin>/.codex-plugin/plugin.json`.

## Release

1. Run `python3 scripts/validate_repo.py` and test representative skill requests.
2. Commit the changes.
3. Create the release tag: `git tag -a <plugin>--v<version> -m "<summary>"`.
4. Push the commit and tag.
5. Refresh and reinstall the plugin on consuming machines using the update commands above.

## Claude rollback point

`claude-final-v0.7.0` is the final Claude-native marketplace state, at commit `3b3fdf1`.
To resume Claude development without disturbing this Codex line, branch from it:

```bash
git switch -c claude-revival claude-final-v0.7.0
```
