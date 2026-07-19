---
name: skill-reviewer
description: Audit a SKILL.md against best practices. Use when the user asks to review, check, lint, or improve a skill, or after scaffolding a new one. Reports findings; does not edit files.
---

# skill-reviewer

Review a skill (a SKILL.md and its surrounding folder) and report concrete findings. Do NOT apply fixes unless the user asks — output a findings list, most important first.

If the user didn't name a skill, list the skills in `plugins/<bundle>/skills/*/SKILL.md` (distributable skills live inside domain bundles like `research`) and `.claude/skills/*/SKILL.md` (meta-skills) and ask which to review.

## Checklist

### Frontmatter
- Has `name` (kebab-case, matches folder name) and `description`.
- Description states both WHAT the skill does and WHEN to trigger it, with concrete trigger phrases a model can match against ("use when the user asks to X", "triggers on: ..."). A description that only describes the topic ("Helpers for LaTeX") is a finding.
- Description is self-contained: Claude decides whether to load the skill from the description ALONE, without seeing the body.

### Body
- Imperative, instruction-style prose addressed to Claude, not documentation addressed to a human.
- Concise: every paragraph should change behavior. Flag filler, restated obvious facts, and duplicated instructions.
- Token-efficient via progressive disclosure: long reference material (templates, schemas, exhaustive examples) belongs in `references/*.md` files that SKILL.md points to, not inline. Flag SKILL.md bodies over ~150 lines that could be split.
- Concrete over abstract: prefer exact commands, exact paths, exact formats over vague guidance ("handle errors appropriately" is a finding).
- No contradictions, no dead references (files or paths mentioned that don't exist in the skill folder).

### Distributable skills only (plugins/<bundle>/skills/)
- The bundle's `plugin.json` exists with `name`, `version`, `description` describing the domain (not a single skill).
- The bundle is registered in `.claude-plugin/marketplace.json` with a matching `source` path.
- The BUNDLE version was bumped if any skill inside changed since the last git tag `<bundle>--v<version>`.
- The skill topically belongs in its bundle; flag skills that look like they belong to a different domain.

## Output format

For each finding: **severity** (blocker / should-fix / nit), **location** (file, section), **issue**, **suggested fix** (one sentence or a short rewrite). End with an overall verdict: ready to publish, or what must change first.
