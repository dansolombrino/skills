# skills

Personal Claude Code skills, distributed as a private [plugin marketplace](https://code.claude.com/docs/en/plugin-marketplaces).

## Layout

```
.claude-plugin/marketplace.json   # marketplace manifest — lists installable plugins
plugins/<name>/                   # distributable skills, one plugin each
├── .claude-plugin/plugin.json    #   name, version, description
└── skills/<name>/SKILL.md        #   the skill itself
.claude/skills/                   # meta-skills, active only inside this repo
├── skill-creator/                #   scaffolds new skills
└── skill-reviewer/               #   audits a SKILL.md against best practices
```

## Installing a skill in another project

The repo is private; installs clone it over SSH with your git credentials.

```
/plugin marketplace add dansolombrino/skills
/plugin install <name>@dansolombrino-skills
```

## Developing skills (in this repo)

- `/skill-creator` — scaffold a new distributable skill (or meta-skill) with correct structure and marketplace registration.
- `/skill-reviewer` — audit a SKILL.md for trigger quality, conciseness, and structure before publishing.

## Release / update loop

1. Edit the skill; bump `version` in its `plugin.json`.
2. Commit, then `claude plugin tag --push` (creates the `<name>--v<version>` tag that version resolution uses) and `git push`.
3. In consuming projects: `/plugin marketplace update` then `claude plugin update <name>`.
   Auto-update is off by default for personal marketplaces; it can be enabled in the `/plugin` UI.
