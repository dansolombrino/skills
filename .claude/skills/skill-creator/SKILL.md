---
name: skill-creator
description: Scaffold a new skill in this repo. Use when the user wants to create a new skill, add a skill to a bundle, or create a new bundle/plugin. Handles distributable skills (inside bundle plugins under plugins/) and meta-skills (.claude/skills/).
---

# skill-creator

Scaffold a new skill in this repository. Distributable skills are organized as **domain bundles**: one plugin per domain (e.g. `research`), each containing multiple skills, installed as a unit into other projects.

Three flows — pick based on what the user asked:

1. **Add a skill to an existing bundle** (default): list bundles in `plugins/*/`, ask which one if more than one exists or if unclear.
2. **Create a new bundle**: only when the skill belongs to a new domain that no existing bundle covers.
3. **Create a meta-skill**: repo-local tooling for skill development itself → `.claude/skills/`.

## Inputs to gather

1. **Skill name** — kebab-case, short (e.g. `literature-review`, `experiment-log`).
2. **Description** — must state WHAT the skill does and WHEN to use it, with concrete trigger phrasing ("Use when the user asks to X, mentions Y..."). Claude decides whether to load the skill from the description alone, so triggers matter more than elegance.
3. **Body content** — what the instructions should say. If the user only has a rough idea, draft it and iterate.

## Flow 1: add a skill to an existing bundle

Create `plugins/<bundle>/skills/<skill-name>/SKILL.md` with `name` and `description` frontmatter. Body: concise, imperative instructions. Long reference material (templates, schemas, exhaustive examples) goes in `plugins/<bundle>/skills/<skill-name>/references/*.md`, pointed to from SKILL.md — keep SKILL.md itself short.

Remove `plugins/<bundle>/skills/.gitkeep` if present. Then bump the **bundle's** version in `plugins/<bundle>/.claude-plugin/plugin.json` (minor bump for a new skill).

## Flow 2: create a new bundle

```
plugins/<bundle>/
├── .claude-plugin/plugin.json   # { "name": "<bundle>", "version": "0.1.0", "description": "..." }
└── skills/<skill-name>/SKILL.md
```

Register it in `.claude-plugin/marketplace.json`, appending to `plugins`:

```json
{ "name": "<bundle>", "source": "./plugins/<bundle>", "description": "..." }
```

The bundle description should describe the domain, not one skill. Confirm with the user before creating a new bundle — prefer fitting skills into existing bundles.

## Flow 3: create a meta-skill

Create `.claude/skills/<name>/SKILL.md` with the same frontmatter rules. Do NOT register meta-skills in marketplace.json and do not touch any plugin.json — they are repo-local only.

## After scaffolding

1. Run `claude plugin validate .` (fall back to `python3 -m json.tool` on the JSON files if unavailable).
2. Suggest running `/skill-reviewer` on the new SKILL.md.
3. Remind the user of the release loop: commit, `claude plugin tag --push` from the bundle directory (creates the `<bundle>--v<version>` tag), `git push`. Consuming projects then run `/plugin marketplace update` and `claude plugin update <bundle>`.
