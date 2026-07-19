# skills

Personal Claude Code skills, distributed as a private [plugin marketplace](https://code.claude.com/docs/en/plugin-marketplaces).

Skills are organized as **domain bundles**: one plugin per domain, each containing multiple skills, installed as a unit. Current bundles:

- **research** — skills for research work

## Layout

```
.claude-plugin/marketplace.json      # marketplace manifest — lists the bundles
plugins/<bundle>/                    # one plugin per domain (e.g. research)
├── .claude-plugin/plugin.json       #   bundle name, version, description
└── skills/<skill>/SKILL.md          #   the skills in the bundle
.claude/skills/                      # meta-skills, active only inside this repo
├── skill-creator/                   #   scaffolds new skills/bundles
└── skill-reviewer/                  #   audits a SKILL.md against best practices
```

## Setting up a new machine / rig

The repo is private; installs clone it over SSH with your git credentials (make sure the
rig's SSH key has GitHub access). Run once per machine:

```bash
claude plugin marketplace add dansolombrino/skills        # registers the marketplace in USER settings
claude plugin install research@dansolombrino-skills       # user scope → every project on the machine
```

User scope means all projects on that rig get every skill in the bundle; new Claude sessions
pick them up automatically. Shortcut: just tell Claude *"add my marketplace dansolombrino/skills
and install the research bundle"* and it will run these two commands for you.

To pull a new release later (auto-update is off by default for personal marketplaces; it can be
enabled in the `/plugin` UI):

```bash
claude plugin marketplace update dansolombrino-skills
claude plugin update research
```

### Per-project alternative

To install into a single project only, use the interactive commands from inside that project:

```
/plugin marketplace add dansolombrino/skills
/plugin install research@dansolombrino-skills
```

One install brings in every skill in the bundle. Browse all bundles interactively with `/plugin`.

## Developing skills (in this repo)

- `/skill-creator` — add a skill to a bundle, create a new bundle, or create a meta-skill.
- `/skill-reviewer` — audit a SKILL.md for trigger quality, conciseness, and structure before publishing.

## Release / update loop

1. Edit skills; bump the **bundle's** `version` in `plugins/<bundle>/.claude-plugin/plugin.json` (any change to any skill in the bundle).
2. Commit, then `claude plugin tag --push` from the bundle directory (creates the `<bundle>--v<version>` tag that version resolution uses) and `git push`.
3. In consuming projects: `/plugin marketplace update` then `claude plugin update <bundle>`.
   Auto-update is off by default for personal marketplaces; it can be enabled in the `/plugin` UI.
