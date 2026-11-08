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
writes only these files and touches nothing else in the project:

| File | What the script owns |
|---|---|
| `.project.toml` | the whole file |
| `.claude/settings.json` | the `enabledPlugins` entry of each known category, explicitly `true` or `false`; in a `research` project, the `Read(./.env)` rule in `permissions.deny`; every other key is preserved |
| `.codex/config.toml` | one managed block holding a `[plugins."<id>"]` table per known category; everything outside the block is preserved |
| `.omp/plugins/` | with `omp` on PATH: an enabled project-scope omp install of each tagged category and none of the others, made through `omp plugin install\|enable\|uninstall --scope project`, plus a self-ignoring `.gitignore` |

omp has no per-project setting a file could carry: a domain plugin is on there only through a
project-scope install, recorded in `.omp/plugins/installed_plugins.json`. That registry, its lock
file and the symlink into the user's plugin cache are machine-local, so the `.gitignore` keeps the
whole directory out of Git and every clone runs `apply` to recreate them. Without `omp` on PATH, or
when omp has no `dansolombrino-skills` marketplace, `apply` still writes the files and prints the
omp step as skipped with its fix, `omp plugin marketplace add dansolombrino/skills`.

The script never edits the user's global host configuration; omp's own bookkeeping of a
project-scope install (its plugin cache and registry list) is the only state it causes outside the
project.

The deny rule keeps the project's secrets out of the agent's file tools and recognized read
commands on the host that reads this file. It also blocks that host's edit and write tools on
`.env`, including creating it, so a script or the user creates and changes the file. Scripts that
read `.env` in a subprocess are not affected. Removing the `research` tag leaves the rule in place.

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
   trusted, tell the user to open the project in Codex and accept the trust prompt. Report each
   `omp:` line too; a skipped omp step names the command that fixes it, after which `apply` is
   rerun with the same categories.
5. Tell the user that a host loads plugins at session start, so the new tag takes effect after
   they start a new session, or use the host's plugin reload command if it has one.
6. Leave the three tracked files uncommitted unless the user asks for a commit, in which case
   commit only those files; `.omp/plugins/` is ignored and never committed.

## Hand off to a domain scaffold

Tagging never scaffolds. When the user also wants a fresh Research 2.0 project and the directory
is new or empty, tag it with `research` first, then tell the user to start a new session, or use
the host's plugin reload command if it has one, and use `research-project-init` there. It accepts a directory that holds only the files this skill
writes, and requires the `research` tag.

## Check a project

Run `python3 "$PROJECT_INIT" check`. It recomputes the host settings from the
marker and reports:

- `in sync`, exit 0;
- `drift <host>: ...` lines, where a generated setting disagrees with the marker, exit 1;
- `mismatch <host>: ...` lines, where the files agree with the marker but the host would still do
  something else, exit 1;
- `untagged`, exit 2.

Each result includes the Codex trust line and the plugin's state in the user's global Codex config.
With `claude` on PATH it also reports what `claude plugin list --json` says for this project; a
machine without it skips that part. With `omp` on PATH it compares the marker with the project-scope
installs in `.omp/plugins/installed_plugins.json` (a missing or disabled install of a tagged plugin,
or any install of an untagged one, is drift) and reports a user-scope install of a category plugin,
which loads in every project, from `omp plugin list --json`; without `omp` it says so and skips the
part. For drift, show the lines and offer to rerun `apply` with the
marker's categories; never change the marker to match the settings without the user's say-so. A
mismatch is not fixed by `apply`: show the line and let the user change the setting it names, since
it lives outside the project.

When a domain skill does not load where the user expects it, or loads where it should not, run
`check` first. The usual causes:

- a missing tag, or drift;
- a session started before the tag;
- an untrusted project in Codex, or the plugin left on in the global Codex config, which reinstalling
  the plugin there does;
- on Claude, a `.claude/settings.local.json`, user or managed setting overriding the project;
- on omp, a fresh clone that has not run `apply` yet, or a user-scope install of the domain plugin,
  which `omp plugin uninstall --scope user <plugin>` removes;
- the plugin is not installed on this machine, or its marketplace is not registered. A cloned tag
  does not bring either one; follow the plugin repository's install steps. On Claude, the `/plugin`
  Errors tab shows load failures.
