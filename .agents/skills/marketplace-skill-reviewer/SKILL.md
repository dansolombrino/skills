---
name: marketplace-skill-reviewer
description: Audit a Codex marketplace skill and its containing plugin for trigger quality, instruction design, metadata, references, manifests, validation, and release readiness. Use when the user asks to review, check, lint, or improve a skill in this repository. Report findings without editing unless fixes are explicitly requested.
---

# Marketplace skill reviewer

Review the requested `SKILL.md`, its complete skill folder, the containing plugin manifest, and
the marketplace entry. Do not edit files unless the user asks for fixes.

## Review

- Confirm the folder and frontmatter names match, use kebab-case, and frontmatter contains only
  `name` and `description`.
- Confirm the description says what the workflow does and gives concrete positive triggers and
  useful boundaries for implicit Codex activation.
- Require concise imperative instructions with explicit inputs, decisions, outputs, stop
  conditions, and non-inference rules appropriate to the workflow's risk.
- Flag duplicated detail that belongs in `references/`, repeated deterministic work that belongs
  in `scripts/`, missing files, dead relative links, and unnecessary auxiliary documentation.
- Confirm `agents/openai.yaml` matches the skill, its short description is 25–64 characters, and
  its default prompt explicitly names `$<skill>`.
- For distributed skills, confirm `.codex-plugin/plugin.json`, the root marketplace entry, topical
  fit, and a version bump relative to the latest `<plugin>--v<version>` tag.
- Confirm active instructions are Codex-native and do not depend on paths, commands, or
  terminology from the retired host.

Run `python3 scripts/validate_repo.py` and include its result. Report findings in severity order as
`blocker`, `should-fix`, or `nit`, each with a location, issue, and concise fix. End with either
`ready to release` or the exact blockers that remain.
