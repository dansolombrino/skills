---
name: marketplace-skill-reviewer
description: Audit a marketplace skill and its containing plugin for trigger quality, instruction design, host neutrality, metadata, references, Codex and Claude manifests, validation, and release readiness. Use when the user asks to review, check, lint, or improve a skill in this repository. Report findings without editing unless fixes are explicitly requested.
---

# Marketplace skill reviewer

Review the requested `SKILL.md`, its complete skill folder, the containing plugin manifest, and
the marketplace entry. Do not edit files unless the user asks for fixes.

## Review

- Confirm the folder and frontmatter names match, use kebab-case, and frontmatter contains only
  `name` and `description`.
- Confirm the description says what the workflow does and gives concrete positive triggers and
  useful boundaries for implicit activation on either host.
- Require concise imperative instructions with explicit inputs, decisions, outputs, stop
  conditions, and non-inference rules appropriate to the workflow's risk.
- Flag duplicated detail that belongs in `references/`, repeated deterministic work that belongs
  in `scripts/`, missing files, dead relative links, and unnecessary auxiliary documentation.
- Confirm `agents/openai.yaml` matches the skill, its short description is 25–64 characters, and
  its default prompt explicitly names `$<skill>`.
- For distributed skills, confirm `.codex-plugin/plugin.json`, `.claude-plugin/plugin.json`, both
  marketplace entries, topical fit, and a matching version bump across all three relative to the
  latest `<plugin>--v<version>` tag.
- Confirm distributed instructions are host-neutral: no `$<skill>` sigil in any `.md`, no host
  product name outside a genuine per-host install target, and no capability described in terms
  only one host provides.

Run `python3 scripts/validate_repo.py` and `python3 -m unittest discover tests`, and include their
results. Report findings in severity order as
`blocker`, `should-fix`, or `nit`, each with a location, issue, and concise fix. End with either
`ready to release` or the exact blockers that remain.
