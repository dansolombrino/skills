#!/usr/bin/env python3
"""Validate this repository's marketplace and plugins for Codex, Claude Code, and omp."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
LINK_RE = re.compile(r"\[[^]]*]\(([^)]+)\)")
SKILL_SIGIL_RE = re.compile(r"\$([a-z0-9]+(?:-[a-z0-9]+)+)\b")
# Host product names as written in prose. Lower-case commands and paths are not matched, except
# omp's, whose product name is its lower-case command.
HOST_NAME_RE = re.compile(r"\b(Claude|Codex|Anthropic|OpenAI|ChatGPT|oh-my-pi|omp)\b")
# Files where a host name identifies a real install target or per-host configuration.
HOST_NAME_ALLOWLIST = {
    "plugins/core/skills/project-init/SKILL.md",  # generates each host's settings
    "plugins/research/skills/sweep-supervisor/references/setup.md",  # per-host agent commands
    "plugins/core/skills/token-usage/SKILL.md",  # reads each host's session logs
    "plugins/core/skills/token-usage/references/setup.md",  # per-host log locations
}
SKILL_KEYS = {"name", "description"}
# A Claude pointer may add Claude-only guardrails. A distributed skill may not: the field would do
# nothing on Codex, so the two hosts would behave differently.
POINTER_KEYS = SKILL_KEYS | {"disallowed-tools"}
EVAL_CASE_KEYS = {
    "schema_version", "name", "description", "tags", "plugins", "runs", "expected_outcome",
    "model", "max_turns", "timeout_seconds", "allowed_tools", "append_system_prompt", "env",
}
EVAL_GRADER_TYPES = {"regex", "tool_used", "tool_order", "file_exists", "llm", "baseline"}
EVAL_SKILL_RE = re.compile(r"([a-z0-9]+(?:-[a-z0-9]+)+)\"'?$")
FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---(?:\n|\Z)", re.DOTALL)


def parse_frontmatter(
    path: Path, errors: list[str], allowed: set[str] = SKILL_KEYS
) -> dict[str, str]:
    text = path.read_text()
    match = FRONTMATTER_RE.match(text)
    if not match:
        errors.append(f"{path}: missing YAML frontmatter")
        return {}

    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if not line.strip():
            continue
        key, separator, value = line.partition(":")
        if not separator or not value.strip():
            errors.append(f"{path}: unsupported or empty frontmatter line: {line!r}")
            continue
        fields[key.strip()] = value.strip().strip('"')
    if not SKILL_KEYS <= set(fields) <= allowed:
        errors.append(
            f"{path}: frontmatter keys must be name and description, "
            f"and at most {sorted(allowed)}; got {sorted(fields)}"
        )
    return fields


def validate_skill(skill_dir: Path, errors: list[str], *, shared: bool = True) -> None:
    """Validate one skill. `shared` skills ship to every host; repo-local ones stay in this repo."""
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        errors.append(f"{skill_dir}: missing SKILL.md")
        return

    fields = parse_frontmatter(skill_md, errors)
    name = fields.get("name", "")
    if name != skill_dir.name:
        errors.append(f"{skill_md}: name {name!r} does not match folder {skill_dir.name!r}")
    if name and not NAME_RE.fullmatch(name):
        errors.append(f"{skill_md}: name is not lowercase kebab-case")
    description = fields.get("description", "")
    if len(description) < 40:
        errors.append(f"{skill_md}: description is too short to define useful triggering")

    metadata = skill_dir / "agents" / "openai.yaml"
    if not metadata.is_file():
        errors.append(f"{skill_dir}: missing agents/openai.yaml")
    else:
        yaml = metadata.read_text()
        short = re.search(r'^\s*short_description:\s*"([^"]+)"\s*$', yaml, re.MULTILINE)
        prompt = re.search(r'^\s*default_prompt:\s*"([^"]+)"\s*$', yaml, re.MULTILINE)
        display = re.search(r'^\s*display_name:\s*"([^"]+)"\s*$', yaml, re.MULTILINE)
        if not display:
            errors.append(f"{metadata}: missing quoted interface.display_name")
        if not short:
            errors.append(f"{metadata}: missing quoted interface.short_description")
        elif not 25 <= len(short.group(1)) <= 64:
            errors.append(f"{metadata}: short_description must be 25–64 characters")
        if not prompt:
            errors.append(f"{metadata}: missing quoted interface.default_prompt")
        elif name and f"${name}" not in prompt.group(1):
            errors.append(f"{metadata}: default_prompt must explicitly mention ${name}")

    for markdown in skill_dir.rglob("*.md"):
        for target in LINK_RE.findall(markdown.read_text()):
            target = target.split("#", 1)[0]
            if not target or "://" in target or target.startswith("mailto:"):
                continue
            resolved = (markdown.parent / target).resolve()
            if not resolved.exists():
                errors.append(f"{markdown}: dead relative link {target!r}")
            elif shared and not resolved.is_relative_to(skill_dir.parent.parent.resolve()):
                # Each plugin installs on its own, so a link into a sibling plugin dangles there.
                errors.append(f"{markdown}: relative link {target!r} leaves its plugin")

    # Skills are shared verbatim by every supported host, so instructions must not carry a
    # host-specific invocation sigil. Host product names are checked by validate_host_names.
    if not shared:
        return
    # Claude's skill listing shows angle brackets in a description as &lt; and &gt;.
    if "<" in description or ">" in description:
        errors.append(f"{skill_md}: description must not contain angle brackets")
    for markdown in skill_dir.rglob("*.md"):
        for match in SKILL_SIGIL_RE.finditer(markdown.read_text()):
            errors.append(
                f"{markdown}: host-specific skill invocation {match.group(0)!r}; "
                f"write {match.group(1)!r} without the leading '$'"
            )


def validate_host_names(root: Path, plugin_root: Path, errors: list[str]) -> None:
    """Prose in distributed skills and agents stays host-neutral. evals/ is exempt: it is not read
    by the agent, and `claude plugin eval` is Claude-only."""
    for markdown in [*plugin_root.glob("skills/**/*.md"), *plugin_root.glob("agents/*.md")]:
        if markdown.relative_to(root).as_posix() in HOST_NAME_ALLOWLIST:
            continue
        for match in HOST_NAME_RE.finditer(markdown.read_text()):
            errors.append(
                f"{markdown}: host name {match.group(0)!r}; describe the capability generically, "
                f"or add the file to HOST_NAME_ALLOWLIST if it names a real install target"
            )


def load_json(path: Path, errors: list[str]) -> dict:
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"{path}: invalid or unreadable JSON: {exc}")
        return {}
    if not isinstance(payload, dict):
        errors.append(f"{path}: root must be an object")
        return {}
    return payload


HOOK_SCRIPT_RE = re.compile(r"\$\{CLAUDE_PLUGIN_ROOT\}/([^\"'\s]+)")


def validate_hooks(plugin_root: Path, manifest_paths: list[Path], errors: list[str]) -> None:
    """Hooks run in every session, so a broken path or file would fail silently everywhere. Check
    hooks/hooks.json and every hook file or inline hook object a manifest's `hooks` key declares:
    valid JSON, and every `${CLAUDE_PLUGIN_ROOT}/...` file a command runs exists."""
    configs: list[tuple[Path, object]] = []
    default = plugin_root / "hooks" / "hooks.json"
    if default.exists():
        configs.append((default, load_json(default, errors)))
    for manifest_path in manifest_paths:
        declared = load_json(manifest_path, []).get("hooks") if manifest_path.is_file() else None
        if declared is None:
            continue
        for item in declared if isinstance(declared, list) else [declared]:
            if isinstance(item, dict):
                configs.append((manifest_path, item))
                continue
            target = (plugin_root / item).resolve() if isinstance(item, str) else None
            if not isinstance(item, str) or not item.startswith("./"):
                errors.append(f"{manifest_path}: hooks entry {item!r} must be a './' path or an object")
            elif not target.is_relative_to(plugin_root.resolve()):
                errors.append(f"{manifest_path}: hooks file {item!r} leaves its plugin")
            elif not target.is_file():
                errors.append(f"{manifest_path}: hooks file {item!r} does not exist")
            elif target != default.resolve():
                configs.append((target, load_json(target, errors)))
    for source, data in configs:
        hooks = data.get("hooks") if isinstance(data, dict) else None
        if not isinstance(hooks, dict) or not hooks:
            errors.append(f"{source}: must hold a non-empty 'hooks' object")
            continue
        for event, groups in hooks.items():
            for group in groups if isinstance(groups, list) else []:
                for hook in group.get("hooks", []) if isinstance(group, dict) else []:
                    command = hook.get("command", "") if isinstance(hook, dict) else ""
                    if not command:
                        errors.append(f"{source}: {event} hook without a command")
                    for relative in HOOK_SCRIPT_RE.findall(command):
                        if not (plugin_root / relative).is_file():
                            errors.append(f"{source}: {event} hook runs missing file {relative}")


def validate_package(plugin_root: Path, errors: list[str]) -> None:
    """omp loads the extension modules a plugin's package.json lists under `omp.extensions` (the
    omp counterpart of a Claude hook). Each must exist inside the plugin, or it fails at session
    start in every project. No `version`: the two plugin manifests are the only version source, and
    omp already reads the Claude one first."""
    path = plugin_root / "package.json"
    if not path.exists():
        return
    package = load_json(path, errors)
    if "version" in package:
        errors.append(f"{path}: must not set version; the plugin manifests hold it")
    omp = package.get("omp", {})
    extensions = omp.get("extensions", []) if isinstance(omp, dict) else None
    if not isinstance(extensions, list):
        errors.append(f"{path}: omp.extensions must be a list of './' paths")
        return
    for entry in extensions:
        target = (plugin_root / entry).resolve() if isinstance(entry, str) else None
        if target is None or not entry.startswith("./"):
            errors.append(f"{path}: omp.extensions entry {entry!r} must be a './' path")
        elif not target.is_relative_to(plugin_root.resolve()):
            errors.append(f"{path}: omp.extensions entry {entry!r} leaves its plugin")
        elif not target.exists():
            errors.append(f"{path}: omp.extensions entry {entry!r} does not exist")


def validate_agents(plugin_root: Path, errors: list[str]) -> None:
    """A plugin's agents/*.md need a name, a useful description, and skills that exist here."""
    for agent in sorted((plugin_root / "agents").glob("*.md")):
        match = FRONTMATTER_RE.match(agent.read_text())
        if not match:
            errors.append(f"{agent}: missing YAML frontmatter")
            continue
        block = match.group(1)
        name = re.search(r"^name:[ \t]*(.*)$", block, re.MULTILINE)
        if not name or name.group(1).strip() != agent.stem:
            errors.append(f"{agent}: name must match the file name {agent.stem!r}")
        description = re.search(r"^description:[ \t]*(.*)$", block, re.MULTILINE)
        if not description or len(description.group(1)) < 40:
            errors.append(f"{agent}: description is too short to define useful delegation")
        skills = re.search(r"^skills:[ \t]*(.*(?:\n[ \t]+-.*)*)", block, re.MULTILINE)
        for skill in re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)*", skills.group(1)) if skills else []:
            if not (plugin_root / "skills" / skill / "SKILL.md").is_file():
                errors.append(f"{agent}: skills lists {skill!r}, which this plugin does not have")


def validate_evals(plugin_root: Path, errors: list[str]) -> None:
    """`claude plugin eval` has no free format check, so hold cases to its documented layout."""
    evals = plugin_root / "evals"
    for prompt in sorted(evals.rglob("prompt.md")) if evals.is_dir() else []:
        if "results" in prompt.relative_to(evals).parts:
            continue
        match = FRONTMATTER_RE.match(prompt.read_text())
        if not match or not prompt.read_text()[match.end():].strip():
            errors.append(f"{prompt}: needs frontmatter and a prompt body")
            continue
        keys = set(re.findall(r"^([A-Za-z_]+):", match.group(1), re.MULTILINE))
        if keys - EVAL_CASE_KEYS:
            errors.append(f"{prompt}: unknown frontmatter keys {sorted(keys - EVAL_CASE_KEYS)}")
        graders = sorted((prompt.parent / "graders").glob("*.md"))
        if not graders:
            errors.append(f"{prompt.parent}: a case needs at least one graders/*.md")
        for grader in graders:
            head = FRONTMATTER_RE.match(grader.read_text())
            block = head.group(1) if head else ""
            kind = re.search(r"^type:\s*(\S+)", block, re.MULTILINE)
            if not kind or kind.group(1) not in EVAL_GRADER_TYPES:
                errors.append(f"{grader}: type must be one of {sorted(EVAL_GRADER_TYPES)}")
            pattern = re.search(r"^input_match:\s*(.+)$", block, re.MULTILINE)
            named = EVAL_SKILL_RE.search(pattern.group(1).strip()) if pattern else None
            if named and not (plugin_root / "skills" / named.group(1) / "SKILL.md").is_file():
                errors.append(f"{grader}: names skill {named.group(1)!r}, not in this plugin")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument(
        "--agents-source",
        type=Path,
        help="Use an alternate .agents directory, useful in read-only mounted workspaces.",
    )
    args = parser.parse_args()
    root = args.root.resolve()
    agents_root = (args.agents_source or root / ".agents").resolve()
    errors: list[str] = []

    marketplace_path = agents_root / "plugins" / "marketplace.json"
    marketplace = load_json(marketplace_path, errors)
    if marketplace.get("name") != "dansolombrino-skills":
        errors.append(f"{marketplace_path}: name must be 'dansolombrino-skills'")
    entries = marketplace.get("plugins", [])
    if not isinstance(entries, list) or not entries:
        errors.append(f"{marketplace_path}: plugins must be a non-empty array")
        entries = []

    # Claude Code and omp read the Claude catalog. All hosts share one skill tree, so the two
    # catalogs must agree on which plugins exist and where they live.
    claude_marketplace_path = root / ".claude-plugin" / "marketplace.json"
    claude_marketplace = load_json(claude_marketplace_path, errors)
    if claude_marketplace.get("name") != marketplace.get("name"):
        errors.append(
            f"{claude_marketplace_path}: name must match the Codex catalog "
            f"{marketplace.get('name')!r}"
        )
    if not (claude_marketplace.get("owner") or {}).get("name"):
        errors.append(f"{claude_marketplace_path}: owner.name is required")
    claude_entries = {
        entry["name"]: entry
        for entry in claude_marketplace.get("plugins", [])
        if isinstance(entry, dict) and isinstance(entry.get("name"), str)
    }

    seen_plugins: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            errors.append(f"{marketplace_path}: every plugin entry must be an object")
            continue
        name = entry.get("name")
        source = entry.get("source", {})
        policy = entry.get("policy", {})
        if not isinstance(name, str) or not NAME_RE.fullmatch(name):
            errors.append(f"{marketplace_path}: invalid plugin name {name!r}")
            continue
        seen_plugins.add(name)
        expected_path = f"./plugins/{name}"
        if source != {"source": "local", "path": expected_path}:
            errors.append(f"{marketplace_path}: {name} must use local source {expected_path}")
        if policy.get("installation") not in {"AVAILABLE", "INSTALLED_BY_DEFAULT", "NOT_AVAILABLE"}:
            errors.append(f"{marketplace_path}: {name} has invalid installation policy")
        if policy.get("authentication") not in {"ON_INSTALL", "ON_USE"}:
            errors.append(f"{marketplace_path}: {name} has invalid authentication policy")
        if not entry.get("category"):
            errors.append(f"{marketplace_path}: {name} is missing category")

        plugin_root = root / "plugins" / name
        manifest_path = plugin_root / ".codex-plugin" / "plugin.json"
        manifest = load_json(manifest_path, errors)
        if manifest.get("name") != name:
            errors.append(f"{manifest_path}: manifest name must match marketplace name {name!r}")
        if not SEMVER_RE.fullmatch(str(manifest.get("version", ""))):
            errors.append(f"{manifest_path}: version must be x.y.z semantic version")
        if manifest.get("skills") != "./skills/":
            errors.append(f"{manifest_path}: skills must point to './skills/'")

        claude_entry = claude_entries.pop(name, None)
        if claude_entry is None:
            errors.append(f"{claude_marketplace_path}: missing entry for plugin {name!r}")
        elif claude_entry.get("source") != expected_path:
            errors.append(f"{claude_marketplace_path}: {name} source must be {expected_path!r}")

        claude_manifest_path = plugin_root / ".claude-plugin" / "plugin.json"
        claude_manifest = load_json(claude_manifest_path, errors)
        # Anti-drift guard: a version bump must land in both manifests or neither.
        for field in ("name", "version", "description"):
            if claude_manifest.get(field) != manifest.get(field):
                errors.append(
                    f"{claude_manifest_path}: {field} must match {manifest_path} "
                    f"({claude_manifest.get(field)!r} != {manifest.get(field)!r})"
                )
        # The two manifests are the only version source: Claude reads the manifest's version first
        # and the Codex catalog has none, so a catalog copy could only drift.
        if claude_entry is not None and "version" in claude_entry:
            errors.append(
                f"{claude_marketplace_path}: {name} must not set version; its manifests hold it"
            )
        # Off on Claude from a plugin's first install. Codex has no such field: there, off comes
        # from the user's config, which scripts/release.sh keeps and checks.
        if name != "core" and claude_manifest.get("defaultEnabled") is not False:
            errors.append(f"{claude_manifest_path}: a non-core plugin must set defaultEnabled: false")
        if "interface" in claude_manifest:
            errors.append(
                f"{claude_manifest_path}: 'interface' is Codex-only and warns under "
                f"'claude plugin validate'"
            )
        if "skills" in claude_manifest:
            errors.append(
                f"{claude_manifest_path}: omit 'skills'; Claude auto-discovers ./skills/"
            )

        validate_hooks(plugin_root, [manifest_path, claude_manifest_path], errors)
        validate_package(plugin_root, errors)
        validate_agents(plugin_root, errors)
        validate_evals(plugin_root, errors)
        validate_host_names(root, plugin_root, errors)

        skill_root = plugin_root / "skills"
        skill_dirs = sorted(path for path in skill_root.iterdir() if path.is_dir()) if skill_root.is_dir() else []
        if not skill_dirs:
            errors.append(f"{skill_root}: plugin contains no skills")
        for skill_dir in skill_dirs:
            validate_skill(skill_dir, errors)

    disk_plugins = {path.name for path in (root / "plugins").iterdir() if path.is_dir()}
    if disk_plugins != seen_plugins:
        errors.append(
            f"marketplace/plugin directory mismatch: catalog={sorted(seen_plugins)}, disk={sorted(disk_plugins)}"
        )

    meta_root = agents_root / "skills"
    meta_dirs = sorted(path for path in meta_root.iterdir() if path.is_dir()) if meta_root.is_dir() else []
    expected_meta = {"marketplace-skill-creator", "marketplace-skill-reviewer"}
    if {path.name for path in meta_dirs} != expected_meta:
        errors.append(
            f"{meta_root}: expected exactly {sorted(expected_meta)}, got {sorted(path.name for path in meta_dirs)}"
        )
    for skill_dir in meta_dirs:
        validate_skill(skill_dir, errors, shared=False)

    # Claude reads project skills from .claude/skills/. Those are pointers, not copies: each one
    # must link to its canonical .agents counterpart so the two can never drift.
    claude_meta_root = root / ".claude" / "skills"
    claude_meta_dirs = (
        sorted(path for path in claude_meta_root.iterdir() if path.is_dir())
        if claude_meta_root.is_dir()
        else []
    )
    if {path.name for path in claude_meta_dirs} != expected_meta:
        errors.append(
            f"{claude_meta_root}: expected exactly {sorted(expected_meta)}, "
            f"got {sorted(path.name for path in claude_meta_dirs)}"
        )
    for skill_dir in claude_meta_dirs:
        pointer = skill_dir / "SKILL.md"
        if not pointer.is_file():
            errors.append(f"{skill_dir}: missing SKILL.md")
            continue
        fields = parse_frontmatter(pointer, errors, POINTER_KEYS)
        if fields.get("name") != skill_dir.name:
            errors.append(f"{pointer}: name must match folder {skill_dir.name!r}")
        canonical = agents_root / "skills" / skill_dir.name / "SKILL.md"
        if f".agents/skills/{skill_dir.name}/SKILL.md" not in pointer.read_text():
            errors.append(f"{pointer}: must point at {canonical}")
        elif not canonical.is_file():
            errors.append(f"{pointer}: points at missing {canonical}")
        elif fields.get("description") != parse_frontmatter(canonical, []).get("description"):
            errors.append(f"{pointer}: description must equal the one in {canonical}")

    for leftover in sorted(claude_entries):
        errors.append(f"{claude_marketplace_path}: {leftover!r} is absent from the Codex catalog")

    if errors:
        print(f"Validation failed with {len(errors)} error(s):", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    distributed_count = sum(
        1 for path in (root / "plugins").glob("*/skills/*/SKILL.md") if path.is_file()
    )
    print(
        f"Validation passed: {len(seen_plugins)} plugin(s) on Codex, Claude Code, and omp, "
        f"{distributed_count} distributed skill(s), {len(meta_dirs)} repo-local skill(s)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
