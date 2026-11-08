#!/usr/bin/env python3
"""Read where omp has this marketplace's plugins installed, and at which version.

omp installs `core` at user scope and every domain plugin at project scope, once per project that
opted in. `omp plugin list` and `omp plugin upgrade` only see the project of their working directory,
so release.sh needs omp's own index of project installs: `project_registries.json` in the user
plugins root, `{"version": 1, "registries": ["<project>/.omp/plugins/installed_plugins.json"]}`.
A project-scope install adds its registry there and an uninstall removes it. Verified on omp 18.18.1
with a throwaway OMP_PROFILE, as was the `omp plugin list --json` shape read below.

    plugins-root                   print the user plugins root omp resolves in this environment
    projects <plugin-id>           print each project root holding a project-scope install
    version <plugin-id>            core: its user-scope version; a domain plugin: the version of
                                   every project install, comma-joined if they differ, or `none`.
                                   `not-installed` or `unknown` when there is nothing to read
    version <plugin-id> --project DIR
                                   that project's project-scope version
    check-user [--root R]          exit 1 listing each non-core catalog plugin installed at user
                                   scope, 2 if omp's list cannot be read, 0 otherwise
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

CORE = "core"


def profile() -> str | None:
    """The named profile omp would use: OMP_PROFILE, else PI_PROFILE; empty or `default` is none."""
    raw = os.environ["OMP_PROFILE"] if "OMP_PROFILE" in os.environ else os.environ.get("PI_PROFILE")
    name = (raw or "").strip()
    return None if name in ("", "default") else name


def plugins_root() -> Path:
    """omp's user plugins data root, resolved as omp 18.18.1 does: `~/.omp[/profiles/<name>]`, or the
    matching `$XDG_DATA_HOME/omp[/profiles/<name>]` when that directory exists. An agent-dir override
    without a profile turns the XDG routing off."""
    name = profile()
    config_root = Path(os.path.normpath(f"{Path.home()}/{os.environ.get('PI_CONFIG_DIR') or '.omp'}"))
    if name:
        config_root = config_root / "profiles" / name
    data_root = config_root
    agent_override = os.environ.get("PI_CODING_AGENT_DIR")
    if name or not agent_override or Path(agent_override).resolve() == (config_root / "agent").resolve():
        xdg = os.environ.get("XDG_DATA_HOME")
        if xdg:
            candidate = Path(xdg) / "omp"
            if name:
                candidate = candidate / "profiles" / name
            if candidate.exists():
                data_root = candidate
    return data_root / "plugins"


def read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def projects(plugin: str) -> list[Path]:
    """Project roots whose registry still exists and lists the plugin. A deleted project leaves a
    stale registry path behind; it has nothing to upgrade, so it is skipped."""
    index = read_json(plugins_root() / "project_registries.json")
    registries = index.get("registries") if isinstance(index, dict) else None
    found: list[Path] = []
    for registry in registries if isinstance(registries, list) else []:
        data = read_json(Path(registry)) if isinstance(registry, str) else None
        installed = data.get("plugins") if isinstance(data, dict) else None
        if isinstance(installed, dict) and installed.get(plugin):
            root = Path(registry).parents[2]
            if root not in found:
                found.append(root)
    return found


def listed(cwd: Path | None) -> dict | None:
    """`omp plugin list --json`, run in `cwd`; None when it fails or is not the expected shape."""
    try:
        result = subprocess.run(
            ["omp", "plugin", "list", "--json"],
            cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True, text=True, check=True,
        )
        data = json.loads(result.stdout)
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("marketplace"), list) else None


def scoped_version(data: dict | None, plugin: str, scope: str) -> str:
    if data is None:
        return "unknown"
    for item in data["marketplace"]:
        if not isinstance(item, dict) or item.get("id") != plugin:
            continue
        for entry in item.get("entries", []):
            if isinstance(entry, dict) and entry.get("scope") == scope:
                return str(entry.get("version", "unknown"))
    return "not-installed"


def version(plugin: str, project: Path | None) -> str:
    if project is not None:
        return scoped_version(listed(project), plugin, "project")
    if plugin.split("@", 1)[0] == CORE:
        return scoped_version(listed(None), plugin, "user")
    versions = sorted({scoped_version(listed(root), plugin, "project") for root in projects(plugin)})
    return ",".join(versions) if versions else "none"


def non_core_plugins(root: Path) -> list[str]:
    """omp reads the Claude catalog; the validator keeps it in step with the Codex one."""
    catalog = json.loads((root / ".claude-plugin/marketplace.json").read_text())
    return sorted(
        f"{entry['name']}@{catalog['name']}" for entry in catalog["plugins"] if entry["name"] != CORE
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("plugins-root")
    find = sub.add_parser("projects")
    find.add_argument("plugin")
    show = sub.add_parser("version")
    show.add_argument("plugin")
    show.add_argument("--project", type=Path)
    check = sub.add_parser("check-user")
    check.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)

    if args.command == "plugins-root":
        print(plugins_root())
        return 0
    if args.command == "projects":
        for root in projects(args.plugin):
            print(root)
        return 0
    if args.command == "version":
        print(version(args.plugin, args.project))
        return 0

    data = listed(None)
    if data is None:
        print("cannot read `omp plugin list --json`", file=sys.stderr)
        return 2
    offenders = [
        plugin
        for plugin in non_core_plugins(args.root)
        if scoped_version(data, plugin, "user") != "not-installed"
    ]
    for plugin in offenders:
        print(plugin)
    return 1 if offenders else 0


if __name__ == "__main__":
    raise SystemExit(main())
