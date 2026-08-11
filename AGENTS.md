# skills repository conventions

This repository is a private plugin marketplace of personal skills, organized as domain plugins
and published to **both Codex and Claude Code**. The current distributed plugin is `research`.

One skill tree serves both hosts. There is no per-host copy of a skill and no generated tree.

## Structure

- Distributed skills: `plugins/<plugin>/skills/<skill>/SKILL.md` — shared verbatim by both hosts.
- Plugin manifests: `plugins/<plugin>/.codex-plugin/plugin.json` and
  `plugins/<plugin>/.claude-plugin/plugin.json`.
- Marketplace catalogs: `.agents/plugins/marketplace.json` (Codex) and
  `.claude-plugin/marketplace.json` (Claude).
- Per-skill Codex metadata: `plugins/<plugin>/skills/<skill>/agents/openai.yaml`; Claude ignores it.
- Repo-local maintenance skills: `.agents/skills/<skill>/SKILL.md` is the canonical body;
  `.claude/skills/<skill>/SKILL.md` is a thin pointer to it. Never register either in the
  marketplace. Edit only the `.agents` copy; these are exempt from the host-neutral rule below.

## Rules

- Use `$marketplace-skill-creator` for new skills or plugins and `$marketplace-skill-reviewer`
  before release.
- Keep distributed skill content **host-neutral**: refer to another skill as `` `skill-name` ``
  with no `$` sigil, and describe host capabilities generically ("the host's parallel
  background-subagent capability"), not by product name. Name a host only where it identifies a
  real install target, as in the Flywheel per-host setup docs.
- A distributed skill change requires a version bump in **both** plugin manifests and in the
  Claude catalog entry. The validator fails if they drift.
- Confirm with the user before adding a new domain plugin; register it in both catalogs.
- Keep each skill's `agents/openai.yaml` synchronized with its `SKILL.md`. The `$<skill>` form
  stays valid there — it is Codex-only metadata.
- Run `python3 scripts/validate_repo.py` and `python3 -m unittest discover tests` after
  structural or skill changes.
- Release by committing, creating the annotated `<plugin>--v<version>` tag, and pushing both.
- `claude-final-v0.7.0` is the pre-Codex Claude-native state, kept for history only. Dual-host
  support was reintroduced in `research` 3.1.0 on the shared tree; do not restore that old layout.
