#!/usr/bin/env python3
"""Read and restore a plugin's `enabled` state in the user's global Codex config.

Codex has no `update`, and `codex plugin add` always writes `enabled = true` for the plugin it
adds; `codex plugin remove` drops the table. Tested on Codex 0.163.0 with a throwaway CODEX_HOME.
So release.sh's remove-then-add turns an off-by-default plugin on globally unless the state is
recorded first and put back afterwards. Codex has no command for this, so `set` edits the file,
changing only the one `enabled` line and refusing anything it cannot re-read as intended.

    get <plugin-id>           print true | false | absent | unknown; always exit 0
    set <plugin-id> <bool>    write the state; exit 1 if the file cannot be safely edited
    check-global [--root R]   exit 1 listing each non-core catalog plugin enabled globally,
                              2 if the config cannot be read, 0 otherwise
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tomllib
from pathlib import Path

CORE = "core"


def config_path() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "config.toml"


def read_config(path: Path) -> dict | None:
    """The parsed config; {} when there is no file; None when it cannot be read."""
    if not path.exists():
        return {}
    try:
        return tomllib.loads(path.read_text())
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return None


def state_of(config: dict | None, plugin: str) -> str:
    if config is None:
        return "unknown"
    plugins = config.get("plugins")
    entry = plugins.get(plugin) if isinstance(plugins, dict) else None
    if entry is None:
        return "absent"
    enabled = entry.get("enabled") if isinstance(entry, dict) else None
    # A table without `enabled` is unusual and its meaning is Codex's call, so do not guess.
    return {True: "true", False: "false"}.get(enabled, "unknown")


def rendered(text: str, plugin: str, value: bool) -> str:
    """`text` with the plugin's `enabled` line set, leaving every other line as it was."""
    literal = str(value).lower()
    lines = text.splitlines(keepends=True)
    quoted = re.escape(plugin)
    header = re.compile(rf"""^\s*\[\s*plugins\s*\.\s*(["']){quoted}\1\s*\]\s*(#.*)?$""")
    start = next((i for i, line in enumerate(lines) if header.match(line.rstrip("\r\n"))), None)
    if start is None:
        prefix = text + ("\n" if text and not text.endswith("\n") else "") + ("\n" if text else "")
        return f'{prefix}[plugins."{plugin}"]\nenabled = {literal}\n'
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].lstrip().startswith("[")), len(lines)
    )
    for i in range(start + 1, end):
        match = re.match(r"^(\s*enabled\s*=\s*)(true|false)(.*)$", lines[i], re.DOTALL)
        if match:
            lines[i] = f"{match.group(1)}{literal}{match.group(3)}"
            return "".join(lines)
    lines.insert(start + 1, f"enabled = {literal}\n")
    return "".join(lines)


def without(config: dict, plugin: str) -> dict:
    """The config minus this plugin's table, dropping `plugins` when that leaves it empty."""
    plugins = {k: v for k, v in config.get("plugins", {}).items() if k != plugin}
    rest = {k: v for k, v in config.items() if k != "plugins"}
    return {**rest, "plugins": plugins} if plugins else rest


def set_state(path: Path, plugin: str, value: bool) -> str | None:
    """Write the state atomically. Returns an error message, or None on success."""
    try:
        current = path.read_text() if path.exists() else ""
        before = tomllib.loads(current)
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        return f"{path} cannot be read as TOML ({exc}); fix it by hand"
    text = rendered(current, plugin, value)
    # Forms the line edit cannot reach (an inline table, a dotted key) fail here, untouched.
    try:
        after = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return f"{path} defines {plugin} in a form this script cannot edit; set enabled by hand"
    if state_of(after, plugin) != str(value).lower():
        return f"{path} defines {plugin} in a form this script cannot edit; set enabled by hand"
    if without(after, plugin) != without(before, plugin):
        return f"{path}: editing {plugin} would change other settings; set enabled by hand"
    if text == current:
        return None
    target = path.resolve() if path.exists() else path
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(text)
    if target.exists():
        shutil.copymode(target, tmp)
    os.replace(tmp, target)
    return None


def non_core_plugins(root: Path) -> list[str]:
    catalog = json.loads((root / ".agents/plugins/marketplace.json").read_text())
    return sorted(
        f"{entry['name']}@{catalog['name']}"
        for entry in catalog["plugins"]
        if entry["name"] != CORE
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    get = sub.add_parser("get")
    get.add_argument("plugin")
    put = sub.add_parser("set")
    put.add_argument("plugin")
    put.add_argument("value", choices=("true", "false"))
    check = sub.add_parser("check-global")
    check.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    path = config_path()

    if args.command == "get":
        print(state_of(read_config(path), args.plugin))
        return 0
    if args.command == "set":
        error = set_state(path, args.plugin, args.value == "true")
        if error:
            print(error, file=sys.stderr)
            return 1
        return 0

    config = read_config(path)
    if config is None:
        print(f"cannot read {path}", file=sys.stderr)
        return 2
    offenders = [p for p in non_core_plugins(args.root) if state_of(config, p) == "true"]
    for plugin in offenders:
        print(plugin)
    return 1 if offenders else 0


if __name__ == "__main__":
    raise SystemExit(main())
