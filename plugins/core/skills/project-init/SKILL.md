---
name: project-init
description: Tag a new or existing project with its categories in .project.toml and generate each host's per-project plugin enablement from it, so domain plugins such as research load only in the projects that use them. Use when the user asks to set up, initialize, tag, or categorize a project, to make a repo a research project or stop it being one, or asks why a domain plugin's skills are or are not loading in a project; also to check that a project's tag and host settings agree. Do not use to scaffold a Research 2.0 project's contents; hand that to research-project-init once the research tag is in place.
---

# project-init

Every project carries one marker at its root, `.project.toml`, naming its categories:

```toml
categories = ["research"]
```

The `core` plugin is always on and is never listed. Each other category is a domain plugin that is
off by default and enabled only in projects tagged with it. The marker is the single source of
truth; the host settings are generated from it and are never edited by hand for this purpose.

Known categories: `research` (plugin `research@dansolombrino-skills`).

**Resolving this skill's own files.** `scripts/` here means *this skill's installed directory* —
the folder holding the SKILL.md you are reading — not the project being tagged. Use that
directory's absolute path; the project's own `scripts/`, if any, has no `project_init.py`.

Run `scripts/project_init.py` from anywhere inside the project; it resolves the Git root. It
writes only three files and touches nothing else:

| File | What the script owns |
|---|---|
| `.project.toml` | the whole file |
| `.claude/settings.json` | the `enabledPlugins` entry of each known category, explicitly `true` or `false`; every other key is preserved |
| `.codex/config.toml` | one managed block holding a `[plugins."<id>"]` table per known category; everything outside the block is preserved |

The script never edits the user's global host configuration.

## Tag a project

1. Determine the project root and read any existing `.project.toml`. State the current
   categories, or that the project is untagged.
2. Ask which categories apply, offering only the known ones; `none` means a core-only project.
   Confirm the exact set before writing.
3. Run `python3 "$PROJECT_INIT" apply --categories <names>`, where `PROJECT_INIT` is the absolute
   path to this skill's `scripts/project_init.py`. Pass
   `--categories` with no names for a core-only project. If the script refuses (invalid
   settings file, or a hand-written table for a managed plugin already in `.codex/config.toml`),
   report its message verbatim and stop; do not edit the file around it.
4. Report the Codex trust line. Codex silently ignores a project's `.codex/config.toml` until that
   exact project root is trusted; a trusted parent directory does not count. If it is not
   trusted, tell the user to open the project in Codex and accept the trust prompt.
5. Tell the user that a host loads plugins at session start, so the new tag takes effect in the
   next session.
6. Leave the three files uncommitted unless the user asks for a commit, in which case commit only
   those files.

## Hand off to a domain scaffold

Tagging never scaffolds. When the user also wants a fresh Research 2.0 project and the directory
is new or empty, tag it with `research` first, then tell the user to start a new session and use
`research-project-init` there. It accepts a directory that holds only the files this skill
writes, and requires the `research` tag.

## Check a project

Run `python3 "$PROJECT_INIT" check`. It recomputes the host settings from the
marker and reports:

- `in sync`, exit 0;
- `drift <host>: <plugin> is <state>, marker says <want>` lines, exit 1;
- `untagged`, exit 2.

Each result includes the Codex trust line. For drift, show the lines and offer to rerun `apply`
with the marker's categories; never change the marker to match the settings without the user's
say-so.

When a domain skill does not load where the user expects it, run `check` first. The usual causes
are a missing tag, drift, an untrusted project in Codex, or a session started before the tag.
