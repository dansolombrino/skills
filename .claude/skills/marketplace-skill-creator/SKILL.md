---
name: marketplace-skill-creator
description: Create or update skills and plugins in this dual-host marketplace while preserving its Codex and Claude manifests, metadata, validation, and release conventions. Use when the user asks to add, scaffold, create, or substantially update a marketplace skill or domain plugin in this repository.
---

# Marketplace skill creator

The instructions live in [.agents/skills/marketplace-skill-creator/SKILL.md](../../../.agents/skills/marketplace-skill-creator/SKILL.md).
Read that file and follow it exactly; it is the single canonical copy for every host.

Two host notes while following it:

- It cites the Codex built-ins `$skill-creator` and `$plugin-creator`. Claude Code has no
  equivalents — apply the general skill-authoring conventions in this repository instead.
- `$marketplace-skill-reviewer` is this repository's other maintenance skill; invoke it here as
  `marketplace-skill-reviewer`.
