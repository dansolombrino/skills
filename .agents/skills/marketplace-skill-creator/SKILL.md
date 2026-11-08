---
name: marketplace-skill-creator
description: Create or update skills and plugins in this Codex, Claude Code, and omp marketplace while preserving its Codex and Claude manifests, omp package.json, metadata, validation, and release conventions. Use when the user asks to add, scaffold, create, or substantially update a marketplace skill or domain plugin in this repository.
---

# Marketplace skill creator

Maintain this repository's skills and plugins. One skill tree ships to Codex, Claude Code, and omp,
so distributed content must stay host-neutral (`omp` counts as a host name). Read the root
`AGENTS.md` and the target plugin manifest before changing anything. Also follow the built-in
`$skill-creator` guidance for skill authoring and `$plugin-creator` guidance when creating a
plugin.

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
- Keep the content host-neutral: reference another skill as `` `skill-name` `` with no `$` sigil,
  and describe host capabilities generically rather than by product name. Name a host only where
  it identifies a real install target.
- Create or refresh `agents/openai.yaml` with `display_name`, a 25–64 character
  `short_description`, and a one-sentence `default_prompt` that names `$<skill>`. The `$` form is
  correct here — this file is Codex-only metadata that the other hosts never read.
- Bump the plugin version in `.codex-plugin/plugin.json` and `.claude-plugin/plugin.json`, to the
  same value: patch for compatible fixes, minor for a new skill or backward-compatible capability,
  major for breaking behavior. These two manifests are the only place the version lives; omp reads
  the Claude one. Any change under the plugin directory, including `package.json` and an omp
  extension, needs the bump.

## New plugins

- Create `plugins/<plugin>/.codex-plugin/plugin.json` and `plugins/<plugin>/skills/` using the
  built-in plugin creator's schema.
- Create `plugins/<plugin>/.claude-plugin/plugin.json` mirroring the Codex manifest's `name`,
  `version`, `description`, `author`, `repository`, and `keywords`. Omit `interface` and `skills`:
  the first is Codex-only, and Claude auto-discovers `./skills/`. Unless the plugin belongs in every
  project like `core`, set `"defaultEnabled": false` so it installs off on Claude; on Codex, off
  comes from the user's config, which `scripts/release.sh` keeps and checks; on omp, off means it is
  never installed at user scope, only at project scope by `project-init` in each tagged project.
- Add the plugin to `.agents/plugins/marketplace.json` with a local `./plugins/<plugin>` source,
  explicit installation/authentication policies, and a category.
- Add the plugin to `.claude-plugin/marketplace.json` with a `"./plugins/<plugin>"` string source
  and a category, and no `version`. omp reads this catalog too; never add an `.omp-plugin/` one.
- Create `tests/test_<plugin>_contracts.py` for the plugin's own contracts.
- Unless the plugin belongs in every project like `core`, add it to `CATEGORIES` in
  `plugins/core/skills/project-init/scripts/project_init.py` and to the known-categories line of
  `project-init`'s `SKILL.md`, then bump `core`.
- Never link from one plugin's skills into another plugin's files; each installs on its own.
- Fill real metadata and remove unused placeholders and component paths.

## Hooks and omp extensions

omp runs no Claude command hooks; it loads the extension modules a plugin's `package.json` lists
under `omp.extensions`. When a plugin ships Claude hooks (`hooks/hooks.json`, or a manifest
`hooks` key), give omp the same behavior in the same change:

- Write an omp extension next to the hooks, for example `hooks/omp_<event>.ts`, that subscribes
  to the matching omp event (`pi.on("session_start", …)` for `SessionStart`) and runs the same
  scripts with the same inputs, so the behavior has one implementation. Follow omp's extension
  docs (`omp://extensions.md`, `omp://skills/authoring-extensions.md`) and copy the shape of
  `plugins/research/hooks/omp_session_start.ts`.
- List it in `plugins/<plugin>/package.json` as `{"private": true, "omp": {"extensions":
  ["./hooks/omp_<event>.ts"]}}`. Each entry is a `./` path inside the plugin. Never put a `version`
  in this file; the validator rejects one.
- Cover the extension in the plugin's tests (`tests/test_hooks.py` shows the pattern), then try it
  in a throwaway `OMP_PROFILE` with the marketplace added from the working tree, never in the
  default profile.

## Finish

Run `python3 scripts/validate_repo.py` and `python3 -m unittest discover tests`. Report the
version change, files created, validation result, and the release commands
(`scripts/release.sh --plugin <plugin>`, which installs on all three hosts), but do not commit,
tag, push, or install unless the user also asks for those actions.
