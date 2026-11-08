#!/usr/bin/env python3
"""Render a fresh Research 2.0 project from this skill's `assets/project/` tree.

Dry run by default: prints what it would write. `--confirm` writes. Writes nothing when any target
already exists, so it never overwrites and a partly scaffolded path is reported, not merged.

In `assets/project/`, a path component starting `dot_` becomes `.` and a `.template` suffix is
dropped, so the shipped tree holds no live `.gitignore`, `.env`, hook, or host notes file that
would act on this repository. Files under `code/` are copied byte for byte: `run_id.py` must
keep hashing exactly as `run-identity` does. Every other file gets its `{{PLACEHOLDER}}`s replaced.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
ASSETS = SKILL_DIR / "assets" / "project"
# `environment-sync` owns this helper; both skills ship in one plugin.
ENVIRONMENT_HELPER = SKILL_DIR.parent / "environment-sync" / "assets" / "environment.py"
TAXONOMY = (
    "checkpoints", "code", "config", "evaluations", "logs", "plots",
    "scripts", "tests", "visualizations", "shitpads", "references",
)
PLACEHOLDER_RE = re.compile(r"\{\{[A-Z_]+\}\}")
VERSION_RE = re.compile(r"\d+\.\d+\.\d+")
PROJECT_NAME_RE = re.compile(r"[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?")
ENVIRONMENT_NAME_RE = re.compile(r"[A-Za-z0-9._-]+")
# Remote and branch land inside sync.toml strings, so nothing that could end or escape one.
GIT_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*")
EXECUTABLE = {".githooks/pre-commit"}
SECRET = {".env"}


class ScaffoldError(Exception):
    pass


def target_of(asset: Path) -> str:
    target = "/".join(
        "." + part.removeprefix("dot_") if part.startswith("dot_") else part
        for part in asset.relative_to(ASSETS).parts
    )
    return target.removesuffix(".template")


def plan_files(values: dict[str, str]) -> dict[str, bytes]:
    """{project-relative path: exact bytes} for everything the scaffold writes."""
    files: dict[str, bytes] = {}
    for asset in sorted(path for path in ASSETS.rglob("*") if path.is_file()):
        if "__pycache__" in asset.parts:
            continue
        target = target_of(asset)
        if target.startswith("code/"):
            files[target] = asset.read_bytes()
            continue
        text = asset.read_text()
        for name, value in values.items():
            text = text.replace("{{" + name + "}}", value)
        leftover = PLACEHOLDER_RE.search(text)
        if leftover:
            raise ScaffoldError(f"{asset}: no value for {leftover.group(0)}")
        files[target] = text.encode()
    if not ENVIRONMENT_HELPER.is_file():
        raise ScaffoldError(f"missing {ENVIRONMENT_HELPER}: install environment-sync from the same plugin")
    files["code/common/environment.py"] = ENVIRONMENT_HELPER.read_bytes()
    for directory in TAXONOMY:
        if not any(path.startswith(f"{directory}/") for path in files):
            files[f"{directory}/.gitkeep"] = b""
    return files


def write_file(root: Path, relative: str, content: bytes) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    # O_EXCL: a file that appeared since the conflict check is still never overwritten.
    mode = 0o600 if relative in SECRET else 0o755 if relative in EXECUTABLE else 0o644
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(descriptor, "wb") as handle:
        os.fchmod(handle.fileno(), mode)
        handle.write(content)


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("path", type=Path, help="the project root: an existing Git repository's top level")
    parser.add_argument("--python", required=True, metavar="X.Y.Z", help="exact Python for .python-version")
    parser.add_argument("--uv", required=True, metavar="X.Y.Z", help="exact uv for [tool.uv].required-version")
    parser.add_argument("--project-name", help="default: the directory name")
    parser.add_argument("--environment-name", default=".venv", help="hub development environment directory")
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--branch", default="main")
    parser.add_argument("--confirm", action="store_true", help="write; without it, only print the plan")
    return parser.parse_args(argv)


def scaffold(args: argparse.Namespace) -> None:
    root = args.path.resolve()
    if not (root / ".git").exists():
        raise ScaffoldError(f"{root} is not the top level of a Git repository: run `git init -b {args.branch}` first")
    project_name = args.project_name or root.name
    if not PROJECT_NAME_RE.fullmatch(project_name):
        raise ScaffoldError(f"{project_name!r} is not a valid project name: pass --project-name")
    if args.environment_name in {".", ".."} or not ENVIRONMENT_NAME_RE.fullmatch(args.environment_name):
        raise ScaffoldError(f"{args.environment_name!r} is not a single safe directory name")
    for label, version in (("--python", args.python), ("--uv", args.uv)):
        if not VERSION_RE.fullmatch(version):
            raise ScaffoldError(f"{label} must be an exact X.Y.Z, not {version!r}")
    for label, name in (("--remote", args.remote), ("--branch", args.branch)):
        if not GIT_NAME_RE.fullmatch(name):
            raise ScaffoldError(f"{label} {name!r} is not a plain Git remote or branch name")

    files = plan_files(
        {
            "PROJECT_NAME": project_name,
            "ENVIRONMENT_NAME": args.environment_name,
            "PYTHON_VERSION": args.python,
            "UV_VERSION": args.uv,
            "REMOTE": args.remote,
            "BRANCH": args.branch,
        }
    )
    conflicts = sorted(path for path in files if (root / path).exists())
    if conflicts:
        raise ScaffoldError(f"{root} already holds, nothing written: " + ", ".join(conflicts))

    verb = "wrote" if args.confirm else "would write"
    for relative, content in files.items():
        if args.confirm:
            write_file(root, relative, content)
        print(f"{verb} {relative}")
    if args.confirm:
        subprocess.run(["git", "-C", str(root), "config", "core.hooksPath", ".githooks"], check=True)
    print(f"{'set' if args.confirm else 'would set'} core.hooksPath=.githooks")
    if not args.confirm:
        print("dry run: nothing changed; pass --confirm to write")


def main(argv: list[str] | None = None) -> int:
    try:
        scaffold(parse_args(argv))
    except (ScaffoldError, OSError, subprocess.CalledProcessError) as error:
        print(f"scaffold: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
