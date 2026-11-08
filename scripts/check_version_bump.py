#!/usr/bin/env python3
"""Fail when a plugin changed since the tag of its current version: changed and not bumped.

`<plugin>--v<version>` marks what shipped as that version, so a plugin whose manifest still names a
tagged version must match that tag. A version with no tag yet has been bumped and is free to keep
changing until it is released. `evals/` is exempt: it is run by hand and changes no behavior.

Compares HEAD by default, and the index with --staged (the pre-commit hook). A git failure is
reported but does not block: this guards against a forgotten bump, not against a broken clone.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)


def check(plugin: str, staged: bool) -> tuple[bool, str]:
    """(ok, message)."""
    manifest = f"plugins/{plugin}/.codex-plugin/plugin.json"
    shown = git("show", f":{manifest}" if staged else f"HEAD:{manifest}")
    if shown.returncode != 0:
        return True, f"{plugin}: no committed manifest yet"
    try:
        version = json.loads(shown.stdout)["version"]
    except (json.JSONDecodeError, KeyError) as exc:
        return True, f"{plugin}: cannot read the manifest version ({exc}); validate_repo.py reports it"
    tag = f"{plugin}--v{version}"
    if git("rev-parse", "-q", "--verify", f"refs/tags/{tag}").returncode != 0:
        return True, f"{plugin}: {version} has no tag yet, so it counts as bumped"
    paths = ["--", f"plugins/{plugin}", f":(exclude)plugins/{plugin}/evals"]
    diff = git("diff", "--quiet", "--cached", tag, *paths) if staged else git("diff", "--quiet", tag, "HEAD", *paths)
    if diff.returncode == 0:
        return True, f"{plugin}: unchanged since {tag}"
    if diff.returncode == 1:
        return False, (
            f"{plugin}: changed since {tag} but still at {version}; bump the version in both "
            f"plugins/{plugin}/.codex-plugin/plugin.json and .claude-plugin/plugin.json"
        )
    return True, f"{plugin}: could not compare with {tag}: {diff.stderr.strip()}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--staged", action="store_true", help="compare the index instead of HEAD")
    parser.add_argument("--plugin", help="check one plugin instead of all of them")
    args = parser.parse_args(argv)
    plugins = [args.plugin] if args.plugin else sorted(
        path.name for path in (ROOT / "plugins").iterdir() if path.is_dir()
    )
    results = [check(plugin, args.staged) for plugin in plugins]
    for ok, message in results:
        print(message, file=sys.stdout if ok else sys.stderr)
    return 0 if all(ok for ok, _ in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
