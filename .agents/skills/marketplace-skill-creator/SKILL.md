---
name: marketplace-skill-creator
description: Create or update skills and plugins in this Codex marketplace while preserving its manifests, metadata, validation, and release conventions. Use when the user asks to add, scaffold, create, or substantially update a marketplace skill or domain plugin in this repository.
---

# Marketplace skill creator

Maintain this repository's Codex skills and plugins. Read the root `AGENTS.md` and the target
plugin manifest before changing anything. Also follow the built-in `$skill-creator` guidance for
skill authoring and `$plugin-creator` guidance when creating a plugin.

## Choose the flow

1. Add or update a distributed skill under `plugins/<plugin>/skills/<skill>/` when it belongs to
   an existing domain plugin.
2. Create a new domain plugin only after the user confirms that no existing plugin fits.
3. Create a repo-local maintenance skill under `.agents/skills/<skill>/`; never register it in the
   marketplace or bump a distributed plugin version.

## Distributed skills

- Use lowercase kebab-case and make the folder name match the frontmatter `name`.
- Put only `name` and a self-contained trigger-focused `description` in frontmatter.
- Keep `SKILL.md` imperative and concise. Put detailed policies or templates in `references/`,
  deterministic helpers in `scripts/`, and output materials in `assets/`.
- Create or refresh `agents/openai.yaml` with `display_name`, a 25–64 character
  `short_description`, and a one-sentence `default_prompt` that names `$<skill>`.
- Bump the plugin version in `.codex-plugin/plugin.json`: patch for compatible fixes, minor for a
  new skill or backward-compatible capability, major for breaking behavior.

## New plugins

- Create `plugins/<plugin>/.codex-plugin/plugin.json` and `plugins/<plugin>/skills/` using the
  built-in plugin creator's schema.
- Add the plugin to `.agents/plugins/marketplace.json` with a local `./plugins/<plugin>` source,
  explicit installation/authentication policies, and a category.
- Fill real metadata and remove unused placeholders and component paths.

## Finish

Run `python3 scripts/validate_repo.py`. Report the version change, files created, validation
result, and the release commands, but do not commit, tag, push, or install unless the user also
asks for those actions.
