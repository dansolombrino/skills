# skills repository conventions

This repository is a private Codex plugin marketplace of personal skills, organized as domain
plugins. The current distributed plugin is `research`.

## Structure

- Distributed skills: `plugins/<plugin>/skills/<skill>/SKILL.md`.
- Plugin manifests: `plugins/<plugin>/.codex-plugin/plugin.json`.
- Marketplace catalog: `.agents/plugins/marketplace.json`.
- Repo-local maintenance skills: `.agents/skills/<skill>/SKILL.md`; never register these in the
  marketplace.

## Rules

- Use `$marketplace-skill-creator` for new skills or plugins and `$marketplace-skill-reviewer`
  before release.
- A distributed skill change requires a version bump in its plugin manifest.
- Confirm with the user before adding a new domain plugin.
- Keep each skill's `agents/openai.yaml` synchronized with its `SKILL.md`.
- Run `python3 scripts/validate_repo.py` after structural or skill changes.
- Release by committing, creating the annotated `<plugin>--v<version>` tag, and pushing both.
- The final Claude-native state is the pushed tag `claude-final-v0.7.0`; do not restore Claude
  files into the Codex line.
