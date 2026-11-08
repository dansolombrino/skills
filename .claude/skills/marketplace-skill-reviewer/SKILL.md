---
name: marketplace-skill-reviewer
description: Audit a marketplace skill and its containing plugin for trigger quality, instruction design, host neutrality, metadata, references, Codex and Claude manifests, validation, and release readiness. Use when the user asks to review, check, lint, or improve a skill in this repository. Report findings without editing unless fixes are explicitly requested.
disallowed-tools: Edit, Write, NotebookEdit
---

# Marketplace skill reviewer

The instructions live in [.agents/skills/marketplace-skill-reviewer/SKILL.md](../../../.agents/skills/marketplace-skill-reviewer/SKILL.md).
Read that file and follow it exactly; it is the single canonical copy for every host.

Reviewing is read-only: do not edit files unless the user asks for fixes. The edit tools are off
while this skill runs, until the user's next message, so a later request for fixes still works.
This is a guardrail, not a guarantee: it does not stop Bash from editing.
