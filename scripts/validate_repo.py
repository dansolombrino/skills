#!/usr/bin/env python3
"""Validate this repository's Codex marketplace, plugins, and skills."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
LINK_RE = re.compile(r"\[[^]]*]\(([^)]+)\)")


def parse_frontmatter(path: Path, errors: list[str]) -> dict[str, str]:
    text = path.read_text()
    match = re.match(r"\A---\n(.*?)\n---(?:\n|\Z)", text, re.DOTALL)
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
    if set(fields) != {"name", "description"}:
        errors.append(
            f"{path}: frontmatter keys must be exactly name and description; got {sorted(fields)}"
        )
    return fields


def validate_skill(skill_dir: Path, errors: list[str]) -> None:
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

    active_text = "\n".join(path.read_text() for path in skill_dir.rglob("*.md"))
    if re.search(r"\bClaude\b|CLAUDE\.md|\.claude-plugin", active_text):
        errors.append(f"{skill_dir}: contains retired Claude-specific instructions")


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
        validate_skill(skill_dir, errors)

    legacy_files = [
        root / "CLAUDE.md",
        root / ".claude-plugin" / "marketplace.json",
        root / "plugins" / "research" / ".claude-plugin" / "plugin.json",
    ]
    legacy_files.extend((root / ".claude" / "skills").glob("*/SKILL.md"))
    for path in legacy_files:
        if path.is_file():
            errors.append(f"{path}: active Claude-era file must be removed")

    if errors:
        print(f"Validation failed with {len(errors)} error(s):", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    distributed_count = sum(
        1 for path in (root / "plugins").glob("*/skills/*/SKILL.md") if path.is_file()
    )
    print(
        f"Validation passed: {len(seen_plugins)} plugin(s), "
        f"{distributed_count} distributed skill(s), {len(meta_dirs)} repo-local skill(s)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
