#!/usr/bin/env python3
"""project-init: tag a project with its categories and generate per-host plugin enablement.

The marker `.project.toml` at the project root is the single source of truth:

    categories = ["research"]

`core` is implied everywhere and never listed. From the marker this script generates:

    .claude/settings.json   enabledPlugins entry for every known category (true/false);
                            every other key in the file is preserved
    .codex/config.toml      a managed block holding one [plugins."<id>"] table per known
                            category; everything outside the block is preserved

`apply` writes all three; `check` recomputes them and reports drift plus Codex trust.
Codex ignores a project's .codex/config.toml unless the project is trusted, and says
nothing about it, so `check` and `apply` both report the trust state. This script never
edits the user's global configuration.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

MARKETPLACE = "dansolombrino-skills"
# category -> plugin id. Every distributed plugin except `core` is a category.
CATEGORIES = {"research": f"research@{MARKETPLACE}"}

MARKER = ".project.toml"
CLAUDE_SETTINGS = Path(".claude/settings.json")
CODEX_CONFIG = Path(".codex/config.toml")
MANAGED_BEGIN = "# --- BEGIN project-init managed plugins ---"
MANAGED_END = "# --- END project-init managed plugins ---"


class InitError(Exception):
    pass


def project_root(path: Path) -> Path:
    path = path.resolve()
    try:
        out = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        # git can refuse a repo it considers foreign-owned; fall back to finding `.git` ourselves
        # rather than silently tagging a subdirectory.
        for candidate in (path, *path.parents):
            if (candidate / ".git").exists():
                return candidate
        return path
    return Path(out.stdout.strip()).resolve()


def normalize(categories: list[str]) -> list[str]:
    names = set()
    for item in categories:
        names.update(part.strip() for part in item.split(",") if part.strip())
    names.discard("core")
    unknown = sorted(names - CATEGORIES.keys())
    if unknown:
        raise InitError(
            f"unknown categories: {', '.join(unknown)} (known: {', '.join(sorted(CATEGORIES))})"
        )
    return sorted(names)


# ───────────────────────────── marker ─────────────────────────────


def render_marker(categories: list[str]) -> str:
    listed = ", ".join(json.dumps(name) for name in categories)
    return (
        "# Project categories. Written by project-init; change them with project-init so the\n"
        "# per-host plugin enablement stays in sync.\n"
        f"categories = [{listed}]\n"
    )


def read_marker(root: Path) -> list[str] | None:
    path = root / MARKER
    if not path.exists():
        return None
    try:
        data = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as exc:
        raise InitError(f"{MARKER} is not valid TOML: {exc}") from exc
    categories = data.get("categories")
    if not isinstance(categories, list) or not all(isinstance(c, str) for c in categories):
        raise InitError(f"{MARKER} must define categories as a list of strings")
    return normalize(categories)


# ───────────────────────────── claude ─────────────────────────────


def read_claude(root: Path) -> dict:
    path = root / CLAUDE_SETTINGS
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise InitError(f"{CLAUDE_SETTINGS} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise InitError(f"{CLAUDE_SETTINGS} must hold a JSON object")
    return data


def render_claude(root: Path, categories: list[str]) -> str:
    data = read_claude(root)
    enabled = data.get("enabledPlugins", {})
    if not isinstance(enabled, dict):
        raise InitError(f"{CLAUDE_SETTINGS} enabledPlugins must be an object")
    for name, plugin in sorted(CATEGORIES.items()):
        enabled[plugin] = name in categories
    data["enabledPlugins"] = enabled
    return json.dumps(data, indent=2) + "\n"


def claude_state(root: Path) -> dict[str, bool | None]:
    enabled = read_claude(root).get("enabledPlugins", {})
    if not isinstance(enabled, dict):
        enabled = {}
    return {plugin: enabled.get(plugin) for plugin in CATEGORIES.values()}


# ───────────────────────────── codex ─────────────────────────────


def strip_managed(text: str) -> str:
    kept, skip = [], False
    for line in text.splitlines(keepends=True):
        if line.rstrip("\n") == MANAGED_BEGIN:
            skip = True
            continue
        if line.rstrip("\n") == MANAGED_END:
            skip = False
            continue
        if not skip:
            kept.append(line)
    return "".join(kept)


def parse_toml(text: str, label: str) -> dict:
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise InitError(f"{label} is not valid TOML: {exc}") from exc


def render_codex(root: Path, categories: list[str]) -> str:
    path = root / CODEX_CONFIG
    current = path.read_text() if path.exists() else ""
    outside = strip_managed(current).rstrip("\n")
    foreign = parse_toml(outside, str(CODEX_CONFIG)).get("plugins", {})
    clash = sorted(set(foreign) & set(CATEGORIES.values()))
    if clash:
        raise InitError(
            f"{CODEX_CONFIG} already defines {', '.join(clash)} outside the managed block; "
            "remove those tables and rerun"
        )
    block = [MANAGED_BEGIN]
    for name, plugin in sorted(CATEGORIES.items()):
        block += [f"[plugins.{json.dumps(plugin)}]", f"enabled = {str(name in categories).lower()}"]
    block.append(MANAGED_END)
    text = (outside + "\n\n" if outside else "") + "\n".join(block) + "\n"
    parse_toml(text, f"generated {CODEX_CONFIG}")
    return text


def codex_state(root: Path) -> dict[str, bool | None]:
    path = root / CODEX_CONFIG
    plugins = parse_toml(path.read_text(), str(CODEX_CONFIG)).get("plugins", {}) if path.exists() else {}
    state = {}
    for plugin in CATEGORIES.values():
        entry = plugins.get(plugin)
        state[plugin] = entry.get("enabled") if isinstance(entry, dict) else None
    return state


def codex_trust(root: Path) -> str:
    """trusted | untrusted | unknown. Only an exact entry for the project root counts."""
    home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    config = home / "config.toml"
    if not config.exists():
        return "unknown"
    try:
        projects = tomllib.loads(config.read_text()).get("projects", {})
    except tomllib.TOMLDecodeError:
        return "unknown"
    entry = projects.get(str(root))
    if isinstance(entry, dict) and entry.get("trust_level") == "trusted":
        return "trusted"
    return "untrusted"


# ───────────────────────────── commands ─────────────────────────────


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def trust_note(root: Path) -> str:
    trust = codex_trust(root)
    if trust == "trusted":
        return "codex trust: trusted"
    return (
        f"codex trust: {trust} -- Codex ignores {CODEX_CONFIG} until this project is trusted; "
        "open it in Codex and accept the trust prompt"
    )


def cmd_apply(root: Path, categories: list[str]) -> int:
    # Render everything before writing anything, so a refusal leaves the project untouched.
    outputs = {
        root / MARKER: render_marker(categories),
        root / CLAUDE_SETTINGS: render_claude(root, categories),
        root / CODEX_CONFIG: render_codex(root, categories),
    }
    for path, text in outputs.items():
        if not path.exists() or path.read_text() != text:
            write_atomic(path, text)
            print(f"wrote {path.relative_to(root)}")
        else:
            print(f"unchanged {path.relative_to(root)}")
    print(f"categories: {', '.join(categories) or '(none; core only)'}")
    print(trust_note(root))
    print("start a new session so each host reloads its plugins")
    return 0


def cmd_check(root: Path) -> int:
    categories = read_marker(root)
    if categories is None:
        print(f"untagged: no {MARKER} in {root}")
        print(trust_note(root))
        return 2
    expected = {plugin: name in categories for name, plugin in CATEGORIES.items()}
    drift = []
    for host, state in (("claude", claude_state(root)), ("codex", codex_state(root))):
        for plugin, want in sorted(expected.items()):
            if state[plugin] != want:
                drift.append(f"{host}: {plugin} is {state[plugin]}, marker says {want}")
    print(f"categories: {', '.join(categories) or '(none; core only)'}")
    for line in drift:
        print(f"drift {line}")
    print(trust_note(root))
    if drift:
        print("run `project_init.py apply` with the marker's categories to fix")
        return 1
    print("in sync")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--path", type=Path, default=Path.cwd(), help="any path inside the project")
    sub = parser.add_subparsers(dest="command", required=True)
    apply = sub.add_parser("apply", help="write the marker and both hosts' plugin enablement")
    apply.add_argument(
        "--categories",
        nargs="*",
        required=True,
        help="space- or comma-separated categories; pass none for a core-only project",
    )
    sub.add_parser("check", help="report drift between the marker, host configs and Codex trust")
    args = parser.parse_args(argv)
    root = project_root(args.path)
    try:
        if args.command == "apply":
            return cmd_apply(root, normalize(args.categories))
        return cmd_check(root)
    except InitError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
