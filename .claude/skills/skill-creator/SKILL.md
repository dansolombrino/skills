---
name: skill-creator
description: Scaffold a new skill in this repo. Use when the user wants to create a new skill, add a skill to the marketplace, or start a new plugin. Handles both distributable skills (plugins/) and meta-skills (.claude/skills/).
---

# skill-creator

Scaffold a new skill in this repository. There are two kinds:

- **Distributable skill** (default): lives under `plugins/<name>/`, published via the plugin marketplace, installed into other projects.
- **Meta-skill**: lives under `.claude/skills/<name>/`, active only inside this repo, used to support skill development itself.

Ask the user which kind they want if not obvious from context.

## Inputs to gather

1. **Name** — kebab-case, short, verb-or-noun phrase (e.g. `commit-helper`, `latex-paper`).
2. **Description** — one or two sentences. Must state WHAT the skill does and WHEN to use it, with concrete trigger phrasing ("Use when the user asks to X, mentions Y..."). This is the only text Claude sees when deciding whether to load the skill, so triggers matter more than elegance.
3. **Purpose/content** — what the skill's instructions should actually say. If the user only has a rough idea, draft the body for them and iterate.

## Scaffolding a distributable skill

Create this structure:

```
plugins/<name>/
├── .claude-plugin/
│   └── plugin.json
└── skills/
    └── <name>/
        └── SKILL.md
```

`plugin.json`:

```json
{
  "name": "<name>",
  "version": "0.1.0",
  "description": "<same description as SKILL.md frontmatter>"
}
```

`SKILL.md` frontmatter must have `name` and `description`. Body: concise instructions, imperative voice, no filler. If the skill needs long reference material (templates, API docs, examples), put it in `skills/<name>/references/*.md` and have SKILL.md point to those files instead of inlining them (progressive disclosure — keep SKILL.md itself short).

Then **register it in the marketplace**: append to the `plugins` array in `.claude-plugin/marketplace.json`:

```json
{
  "name": "<name>",
  "source": "./plugins/<name>",
  "description": "<description>"
}
```

## Scaffolding a meta-skill

Create `.claude/skills/<name>/SKILL.md` with the same frontmatter rules. Do NOT register meta-skills in marketplace.json — they are repo-local only.

## After scaffolding

1. Validate `marketplace.json` is still valid JSON.
2. Suggest running `/skill-reviewer` on the new SKILL.md.
3. Remind the user of the release loop for distributable skills: bump `version` in plugin.json when the skill changes, commit, `claude plugin tag --push`, then push.
