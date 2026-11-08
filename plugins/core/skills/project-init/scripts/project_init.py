#!/usr/bin/env python3
"""project-init: tag a project with its categories and generate per-host plugin enablement.

The marker `.project.toml` at the project root is the single source of truth:

    categories = ["research"]

`core` is implied everywhere and never listed. From the marker this script generates:

    .claude/settings.json   enabledPlugins entry for every known category (true/false), and
                            for a project with secrets the permissions.deny rule for .env;
                            every other key in the file is preserved
    .codex/config.toml      a managed block holding one [plugins."<id>"] table per known
                            category; everything outside the block is preserved

    .omp/plugins/           a project-scope omp install of every known category the marker
                            lists, and none of the others, made through the omp CLI; plus a
                            .gitignore keeping that machine-local state out of Git

`apply` writes the files and reconciles the omp installs; `check` recomputes them and reports
drift plus what each host would actually do. Codex ignores a project's .codex/config.toml
unless the project is trusted, and says nothing about it, so `check` and `apply` both report
the trust state and the user's global Codex state. A settings file does not prove a plugin is
on: a local file can override it, or the plugin may not be installed, so with `claude` on PATH
`check` also compares the marker with `claude plugin list --json`. omp keeps no per-project
setting a file could hold: a plugin is on in a project when it has an enabled project-scope
install there, recorded in .omp/plugins/installed_plugins.json. That registry holds absolute
cache paths, so it is machine state the marker regenerates, never committed. Without `omp` on
PATH, or without this marketplace configured in omp, `apply` skips the omp step and says how
to fix it. This script never edits the user's global configuration.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

MARKETPLACE = "dansolombrino-skills"
# category -> plugin id. Every distributed plugin except `core` is a category.
CATEGORIES = {"research": f"research@{MARKETPLACE}"}
# Categories whose projects hold a .env. A Read deny also blocks Claude's Edit and Write of the
# file, so the file is created and changed by scripts or the user, never by the agent's file tools.
ENV_DENY_CATEGORIES = {"research"}
ENV_DENY_RULE = "Read(./.env)"

MARKER = ".project.toml"
CLAUDE_SETTINGS = Path(".claude/settings.json")
CODEX_CONFIG = Path(".codex/config.toml")
MANAGED_BEGIN = "# --- BEGIN project-init managed plugins ---"
MANAGED_END = "# --- END project-init managed plugins ---"
OMP_PLUGINS = Path(".omp/plugins")
OMP_REGISTRY = OMP_PLUGINS / "installed_plugins.json"
OMP_GITIGNORE = OMP_PLUGINS / ".gitignore"
# Ignores itself too, so it never shows up as an untracked file.
OMP_GITIGNORE_TEXT = (
    "# Written by project-init: omp's project-scope plugin state is machine-local (absolute cache\n"
    "# paths, a symlink into the user cache, timestamps). .project.toml is what Git carries; run\n"
    "# project-init's apply on each machine.\n"
    "*\n"
)
OMP_MARKETPLACE_FIX = "omp plugin marketplace add dansolombrino/skills"


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
    # Added, never removed: dropping a tag does not make the project's secrets go away, and the
    # rule may be the user's own.
    if ENV_DENY_CATEGORIES & set(categories) and not env_deny_present(data):
        permissions = data.setdefault("permissions", {})
        if not isinstance(permissions, dict) or not isinstance(permissions.setdefault("deny", []), list):
            raise InitError(f"{CLAUDE_SETTINGS} permissions.deny must be a list")
        permissions["deny"].append(ENV_DENY_RULE)
    return json.dumps(data, indent=2) + "\n"


def env_deny_present(data: dict) -> bool:
    permissions = data.get("permissions")
    deny = permissions.get("deny") if isinstance(permissions, dict) else None
    return isinstance(deny, list) and ENV_DENY_RULE in deny


def claude_state(root: Path) -> dict[str, bool | None]:
    enabled = read_claude(root).get("enabledPlugins", {})
    if not isinstance(enabled, dict):
        enabled = {}
    return {plugin: enabled.get(plugin) for plugin in CATEGORIES.values()}


def claude_effective(root: Path, expected: dict[str, bool]) -> tuple[list[str], list[str]]:
    """Notes and mismatches from what Claude itself reports here; ([], []) without `claude`.

    `enabled` is the merged settings, which the tracked file alone cannot show: a local file or
    managed settings can override it, and only the listing knows whether the plugin is installed.
    """
    claude = shutil.which("claude")
    if claude is None:
        return [], []
    try:
        out = subprocess.run(
            [claude, "plugin", "list", "--json"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=60,
            stdin=subprocess.DEVNULL,
            check=True,
        )
        entries = json.loads(out.stdout)
        by_id = {entry["id"]: entry for entry in entries}
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        return ["claude effective: unknown (`claude plugin list --json` failed)"], []
    notes, mismatches = [], []
    for plugin, want in sorted(expected.items()):
        entry = by_id.get(plugin)
        if entry is None:
            notes.append(f"claude effective: {plugin} is not installed")
            if want:
                mismatches.append(
                    f"mismatch claude: {plugin} is not installed, marker says True; "
                    "install it from the marketplace, adding the marketplace first if it is missing"
                )
            continue
        enabled = entry.get("enabled") is True
        errors = entry.get("errors") or []
        notes.append(f"claude effective: {plugin} is {'on' if enabled else 'off'}")
        if enabled != want:
            mismatches.append(
                f"mismatch claude: {plugin} is {'on' if enabled else 'off'} in merged settings, "
                f"marker says {want}; a local, user or managed setting overrides the project"
            )
        elif want and errors:
            mismatches.append(f"mismatch claude: {plugin} failed to load: {'; '.join(map(str, errors))}")
    return notes, mismatches


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


def codex_user_config_path() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "config.toml"


def codex_user_config() -> dict | None:
    """The user's global Codex config, or None when it is missing or cannot be read."""
    try:
        data = tomllib.loads(codex_user_config_path().read_text())
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return None
    return data


def codex_trust(root: Path) -> str:
    """trusted | untrusted | unknown. Only an exact entry for the project root counts."""
    config = codex_user_config()
    if config is None:
        return "unknown"
    projects = config.get("projects")
    entry = projects.get(str(root)) if isinstance(projects, dict) else None
    if isinstance(entry, dict) and entry.get("trust_level") == "trusted":
        return "trusted"
    return "untrusted"


def codex_global_state() -> dict[str, bool | None] | None:
    """Each category plugin's global `enabled`, None when unset; None if the config is unreadable."""
    config = codex_user_config()
    if config is None:
        return None
    plugins = config.get("plugins")
    plugins = plugins if isinstance(plugins, dict) else {}
    state = {}
    for plugin in CATEGORIES.values():
        entry = plugins.get(plugin)
        enabled = entry.get("enabled") if isinstance(entry, dict) else None
        state[plugin] = enabled if isinstance(enabled, bool) else None
    return state


# ───────────────────────────── omp ─────────────────────────────

OMP_STATE_TEXT = {
    "on": "installed at project scope",
    "off": "installed at project scope but disabled",
    "absent": "not installed at project scope",
}


def omp_state(root: Path) -> dict[str, str]:
    """Each category plugin's project-scope install here: "on", "off" (disabled) or "absent"."""
    path = root / OMP_REGISTRY
    try:
        data = json.loads(path.read_text()) if path.exists() else {}
    except json.JSONDecodeError as exc:
        raise InitError(f"{OMP_REGISTRY} is not valid JSON: {exc}") from exc
    plugins = data.get("plugins") if isinstance(data, dict) else None
    if not isinstance(plugins, dict):
        plugins = {}
    state = {}
    for plugin in CATEGORIES.values():
        entries = plugins.get(plugin)
        project = [
            entry
            for entry in (entries if isinstance(entries, list) else [])
            if isinstance(entry, dict) and entry.get("scope") == "project"
        ]
        if not project:
            state[plugin] = "absent"
        else:
            state[plugin] = "on" if any(entry.get("enabled") is not False for entry in project) else "off"
    return state


def omp_plan(state: dict[str, str], categories: list[str]) -> list[tuple[str, str]]:
    """The (action, plugin) omp CLI calls that make the project-scope installs match the marker."""
    actions = []
    for name, plugin in sorted(CATEGORIES.items()):
        have = state[plugin]
        if name in categories and have == "absent":
            actions.append(("install", plugin))
        elif name in categories and have == "off":
            actions.append(("enable", plugin))
        elif name not in categories and have != "absent":
            actions.append(("uninstall", plugin))
    return actions


def run_omp(omp: str, root: Path, *args: str) -> subprocess.CompletedProcess:
    # cwd is the project root: omp resolves the project scope from it, and the .omp/plugins
    # directory apply creates first anchors it there rather than at an ancestor's .omp.
    return subprocess.run(
        [omp, "plugin", *args],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=300,
        stdin=subprocess.DEVNULL,
    )


def omp_marketplace_configured(omp: str, root: Path) -> bool:
    """Without it omp would read `<plugin>@<marketplace>` as an npm package spec."""
    try:
        return run_omp(omp, root, "discover", MARKETPLACE).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def omp_marketplace_line(plugin: str) -> str:
    return (
        f"omp: skipped installing {plugin}: marketplace {MARKETPLACE} is not configured in omp; "
        f"run `{OMP_MARKETPLACE_FIX}`, then rerun apply"
    )


def omp_apply(omp: str | None, root: Path, actions: list[tuple[str, str]]) -> tuple[list[str], bool]:
    """Report lines and whether an omp call failed. A missing omp or marketplace is a skip."""
    if omp is None:
        return [
            f"omp: skipped, omp is not on PATH; once it is, run `{OMP_MARKETPLACE_FIX}` if the "
            "marketplace is not configured there, then rerun apply"
        ], False
    if not actions:
        return ["omp: project-scope installs already match"], False
    lines, failed = [], False
    if any(action == "install" for action, _ in actions) and not omp_marketplace_configured(omp, root):
        lines += [omp_marketplace_line(plugin) for action, plugin in actions if action == "install"]
        actions = [(action, plugin) for action, plugin in actions if action != "install"]
    done = {"install": "installed", "enable": "enabled", "uninstall": "uninstalled"}
    for action, plugin in actions:
        args = (action, "--scope", "project", plugin)
        try:
            out = run_omp(omp, root, *args)
            error = None if out.returncode == 0 else (out.stderr.strip() or out.stdout.strip() or "no output")
        except (OSError, subprocess.SubprocessError) as exc:
            error = str(exc)
        if error is None:
            lines.append(f"omp: {done[action]} {plugin} at project scope")
        else:
            lines.append(f"omp: `omp plugin {' '.join(args)}` failed: {error}")
            failed = True
    return lines, failed


def omp_effective(omp: str, root: Path, expected: dict[str, bool]) -> tuple[list[str], list[str]]:
    """Notes and mismatches from a user-scope install, which loads in every project."""
    try:
        out = run_omp(omp, root, "list", "--json")
        entries = json.loads(out.stdout)["marketplace"]
        user = {
            entry["id"]
            for entry in entries
            if entry.get("scope") == "user"
            and any(item.get("enabled") is not False for item in entry.get("entries", []))
        }
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError, AttributeError):
        return ["omp user scope: unknown (`omp plugin list --json` failed)"], []
    notes, mismatches = [], []
    for plugin, want in sorted(expected.items()):
        if plugin not in user:
            notes.append(f"omp user scope: {plugin} is not installed")
            continue
        fix = f"omp plugin uninstall --scope user {plugin}"
        notes.append(
            f"omp user scope: {plugin} is installed -- it loads in every project without an enabled "
            f"project-scope install; a domain plugin belongs at project scope only: run `{fix}`"
        )
        if not want:
            mismatches.append(
                f"mismatch omp: {plugin} is installed at user scope, so it loads here; marker says False"
            )
    return notes, mismatches


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


def codex_global_notes() -> list[str]:
    state = codex_global_state()
    if state is None:
        return ["codex global: unknown (no readable user config)"]
    notes = []
    for plugin, enabled in sorted(state.items()):
        if enabled:
            notes.append(
                f"codex global: {plugin} is on -- it loads in every project that does not turn it "
                f"off and is trusted; a domain plugin should be off here: set enabled = false in "
                f"{codex_user_config_path()}"
            )
        else:
            notes.append(f"codex global: {plugin} is {'unset' if enabled is None else 'off'}")
    return notes


def codex_mismatches(root: Path, expected: dict[str, bool]) -> list[str]:
    """An untrusted project's own config is ignored, so the global state decides there."""
    state = codex_global_state()
    if state is None or codex_trust(root) != "untrusted":
        return []
    return [
        f"mismatch codex: {plugin} is {'on' if state[plugin] else 'off'} globally and this project "
        f"is not trusted, so its config is ignored; marker says {want}"
        for plugin, want in sorted(expected.items())
        if state[plugin] is not None and state[plugin] != want
    ]


def cmd_apply(root: Path, categories: list[str]) -> int:
    # Render everything before writing anything, so a refusal leaves the project untouched.
    outputs = {
        root / MARKER: render_marker(categories),
        root / CLAUDE_SETTINGS: render_claude(root, categories),
        root / CODEX_CONFIG: render_codex(root, categories),
    }
    omp = shutil.which("omp")
    omp_actions = []
    if omp is not None:
        omp_actions = omp_plan(omp_state(root), categories)
        # Written before any omp call, so the project scope resolves to this root.
        outputs[root / OMP_GITIGNORE] = OMP_GITIGNORE_TEXT
    for path, text in outputs.items():
        if not path.exists() or path.read_text() != text:
            write_atomic(path, text)
            print(f"wrote {path.relative_to(root)}")
        else:
            print(f"unchanged {path.relative_to(root)}")
    omp_lines, omp_failed = omp_apply(omp, root, omp_actions)
    print(f"categories: {', '.join(categories) or '(none; core only)'}")
    for line in omp_lines:
        print(line)
    print(trust_note(root))
    for line in codex_global_notes():
        print(line)
    print("start a new session, or use the host's plugin reload command if it has one")
    return 1 if omp_failed else 0


def cmd_check(root: Path) -> int:
    categories = read_marker(root)
    if categories is None:
        print(f"untagged: no {MARKER} in {root}")
        print(trust_note(root))
        for line in codex_global_notes():
            print(line)
        return 2
    expected = {plugin: name in categories for name, plugin in CATEGORIES.items()}
    drift = []
    for host, state in (("claude", claude_state(root)), ("codex", codex_state(root))):
        for plugin, want in sorted(expected.items()):
            if state[plugin] != want:
                drift.append(f"{host}: {plugin} is {state[plugin]}, marker says {want}")
    if ENV_DENY_CATEGORIES & set(categories) and not env_deny_present(read_claude(root)):
        drift.append(f"claude: permissions.deny lacks {ENV_DENY_RULE}, marker lists a project with secrets")
    claude_notes, mismatches = claude_effective(root, expected)
    mismatches += codex_mismatches(root, expected)
    omp = shutil.which("omp")
    omp_notes = ["omp: not on PATH; skipped"]
    if omp is not None:
        state = omp_state(root)
        actions = omp_plan(state, categories)
        drift += [
            f"omp: {plugin} is {OMP_STATE_TEXT[state[plugin]]}, marker says {expected[plugin]}"
            for _, plugin in actions
        ]
        omp_notes, omp_mismatches = omp_effective(omp, root, expected)
        mismatches += omp_mismatches
        if any(action == "install" for action, _ in actions) and not omp_marketplace_configured(omp, root):
            omp_notes.append(
                f"omp: marketplace {MARKETPLACE} is not configured in omp; run `{OMP_MARKETPLACE_FIX}` before apply"
            )
    print(f"categories: {', '.join(categories) or '(none; core only)'}")
    for line in drift:
        print(f"drift {line}")
    for line in mismatches:
        print(line)
    print(trust_note(root))
    for line in (*codex_global_notes(), *claude_notes, *omp_notes):
        print(line)
    if drift:
        print("run `project_init.py apply` with the marker's categories to fix")
    if drift or mismatches:
        return 1
    print("in sync")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--path", type=Path, default=Path.cwd(), help="any path inside the project")
    sub = parser.add_subparsers(dest="command", required=True)
    apply = sub.add_parser("apply", help="write the marker and every host's plugin enablement")
    apply.add_argument(
        "--categories",
        nargs="*",
        required=True,
        help="space- or comma-separated categories; pass none for a core-only project",
    )
    sub.add_parser("check", help="report drift between the marker, host configs, omp installs and Codex trust")
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
