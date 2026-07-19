# skills repo conventions

This repo is a private Claude Code plugin marketplace of personal skills, organized as domain bundles.

## Structure

- Distributable skills: `plugins/<bundle>/skills/<skill>/SKILL.md` — one plugin per domain, installed as a unit. Current bundles: `research`.
- Meta-skills (repo-local tooling): `.claude/skills/<name>/SKILL.md` — never registered in `.claude-plugin/marketplace.json`.

## Rules

- Create new skills via `/skill-creator` (never hand-create the folder structure); review with `/skill-reviewer` before publishing.
- Any change to any skill inside a bundle → bump that bundle's `version` in `plugins/<bundle>/.claude-plugin/plugin.json`.
- New bundles must be registered in `.claude-plugin/marketplace.json`; confirm with the user before creating one.
- After structural changes, run `claude plugin validate .`.
- Release: commit → `claude plugin tag --push` from the bundle directory (creates the `<bundle>--v<version>` tag) → `git push`.
