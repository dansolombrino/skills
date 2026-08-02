#!/usr/bin/env python3
"""Safe, additive research-project synchronization over rsync and SSH."""

from __future__ import annotations

import argparse
import os
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


class RigSyncError(RuntimeError):
    pass


@dataclass(frozen=True)
class Machine:
    name: str
    ssh: str
    hostname: str | None
    repo_path: Path
    local: bool


@dataclass(frozen=True)
class Config:
    root: Path
    artifacts: dict[str, dict]
    machines: dict[str, Machine]


def _load_toml(path: Path) -> dict:
    try:
        with path.open("rb") as handle:
            value = tomllib.load(handle)
    except FileNotFoundError as exc:
        raise RigSyncError(f"missing config: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise RigSyncError(f"invalid TOML in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RigSyncError(f"invalid config root: {path}")
    return value


def load_config(root: Path, config_path: Path, registry_path: Path) -> Config:
    project = _load_toml(config_path)
    registry = _load_toml(registry_path)
    project_machines = project.get("machines", {})
    registry_machines = registry.get("machines", {})
    artifacts = project.get("artifacts", {})
    if not isinstance(project_machines, dict) or not project_machines:
        raise RigSyncError(f"{config_path}: [machines] must be non-empty")
    if not isinstance(registry_machines, dict) or not registry_machines:
        raise RigSyncError(f"{registry_path}: [machines] must be non-empty")
    if not isinstance(artifacts, dict) or not artifacts:
        raise RigSyncError(f"{config_path}: [artifacts] must be non-empty")

    current = socket.gethostname()
    machines: dict[str, Machine] = {}
    for name, project_entry in project_machines.items():
        registry_entry = registry_machines.get(name)
        if not isinstance(project_entry, dict):
            raise RigSyncError(f"{config_path}: machines.{name} must be a table")
        if not isinstance(registry_entry, dict):
            raise RigSyncError(f"{registry_path}: missing machines.{name}")
        ssh = registry_entry.get("ssh")
        hostname = registry_entry.get("hostname")
        raw_repo = project_entry.get("repo_path")
        if not isinstance(ssh, str) or not ssh:
            raise RigSyncError(f"{registry_path}: machines.{name}.ssh must be set")
        if hostname is not None and not isinstance(hostname, str):
            raise RigSyncError(f"{registry_path}: machines.{name}.hostname must be a string")
        if not isinstance(raw_repo, str) or not raw_repo:
            raise RigSyncError(f"{config_path}: machines.{name}.repo_path must be set")
        repo_path = Path(raw_repo)
        if not repo_path.is_absolute():
            raise RigSyncError(f"{config_path}: machines.{name}.repo_path must be absolute")
        local = name == current or hostname == current
        machines[name] = Machine(name, ssh, hostname, repo_path, local)

    for group, entry in artifacts.items():
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise RigSyncError(f"{config_path}: artifacts.{group}.path must be set")
        rel = PurePosixPath(entry["path"])
        if rel.is_absolute() or ".." in rel.parts:
            raise RigSyncError(f"{config_path}: artifacts.{group}.path must be repo-relative")
    return Config(root=root, artifacts=artifacts, machines=machines)


def selected_machines(config: Config, raw: str | None) -> list[Machine]:
    names = list(config.machines) if not raw else [part for part in raw.split(",") if part]
    unknown = [name for name in names if name not in config.machines]
    if unknown:
        raise RigSyncError(f"unknown machine(s): {', '.join(unknown)}")
    return [config.machines[name] for name in names]


def ssh_base(machine: Machine) -> list[str]:
    return ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", machine.ssh]


def run(
    command: list[str],
    *,
    input_bytes: bytes | None = None,
    check: bool = True,
    show: bool = False,
) -> subprocess.CompletedProcess:
    result = subprocess.run(command, input=input_bytes, capture_output=True)
    if show:
        if result.stdout:
            print(result.stdout.decode(errors="replace"), end="")
        if result.stderr:
            print(result.stderr.decode(errors="replace"), end="", file=sys.stderr)
    if check and result.returncode:
        stderr = result.stderr.decode(errors="replace").strip()
        raise RigSyncError(f"command failed ({result.returncode}): {shlex.join(command)}\n{stderr}")
    return result


def remote(machine: Machine, argv: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    if machine.local:
        return run(argv, check=check)
    return run(ssh_base(machine) + [shlex.join(argv)], check=check)


def source_manifest(root: Path) -> list[str]:
    result = run(
        ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"]
    )
    paths = sorted({part.decode() for part in result.stdout.split(b"\0") if part})
    return [path for path in paths if path != ".git" and not path.startswith(".git/")]


def rsync_remote(machine: Machine, path: Path) -> str:
    return f"{machine.ssh}:{shlex.quote(str(path))}/"


def rsync_flags(machine: Machine) -> list[str]:
    flags = ["rsync", "-a", "--itemize-changes"]
    if not machine.local:
        flags += ["-e", "ssh -o BatchMode=yes -o ConnectTimeout=10"]
    return flags


def require_confirmation(dry_run: bool, confirmed: bool) -> None:
    if not dry_run and not confirmed:
        raise RigSyncError("refusing write without --confirm; run --dry-run and obtain approval first")


def prepare(machine: Machine, dry_run: bool, confirmed: bool) -> None:
    require_confirmation(dry_run, confirmed)
    print(f"[{'dry-run' if dry_run else 'prepare'}] {machine.name}: mkdir -p {machine.repo_path}")
    if not dry_run:
        remote(machine, ["mkdir", "-p", str(machine.repo_path)])


def push_source(config: Config, machine: Machine, dry_run: bool, confirmed: bool) -> None:
    require_confirmation(dry_run, confirmed)
    manifest = source_manifest(config.root)
    if machine.local and machine.repo_path.resolve() == config.root.resolve():
        print(f"[local] {machine.name}: source already at {config.root}")
        return
    if not manifest:
        raise RigSyncError("source manifest is empty")
    flags = rsync_flags(machine) + ["--from0", "--files-from=-"]
    if dry_run:
        flags.append("--dry-run")
    destination = str(machine.repo_path) + "/" if machine.local else rsync_remote(machine, machine.repo_path)
    command = flags + [str(config.root) + "/", destination]
    print(f"[{ 'dry-run' if dry_run else 'push-source' }] {machine.name}: {len(manifest)} files")
    run(command, input_bytes=("\0".join(manifest) + "\0").encode(), show=True)


def parse_selector(config: Config, selector: str) -> tuple[str, Path]:
    group, _, suffix = selector.partition("/")
    if group not in config.artifacts:
        raise RigSyncError(f"unknown artifact group: {group}")
    relative = PurePosixPath(suffix) if suffix else PurePosixPath()
    if relative.is_absolute() or ".." in relative.parts:
        raise RigSyncError(f"unsafe selector: {selector}")
    base = Path(config.artifacts[group]["path"])
    return group, base / Path(*relative.parts)


def transfer(
    config: Config,
    machine: Machine,
    selector: str,
    direction: str,
    dry_run: bool,
    confirmed: bool,
) -> None:
    require_confirmation(dry_run, confirmed)
    _group, relative = parse_selector(config, selector)
    local_path = config.root / relative
    remote_path = machine.repo_path / relative
    if machine.local and machine.repo_path.resolve() == config.root.resolve():
        print(f"[local] {selector} already resides on {machine.name}")
        return
    flags = rsync_flags(machine)
    if dry_run:
        flags.append("--dry-run")
    if direction == "push":
        if not local_path.exists():
            raise RigSyncError(f"source does not exist: {local_path}")
        if not dry_run:
            remote(machine, ["mkdir", "-p", str(remote_path.parent)])
        source = str(local_path)
        destination = str(remote_path.parent) + "/" if machine.local else rsync_remote(machine, remote_path.parent)
    else:
        if not dry_run:
            local_path.parent.mkdir(parents=True, exist_ok=True)
        source = str(remote_path) if machine.local else f"{machine.ssh}:{shlex.quote(str(remote_path))}"
        destination = str(local_path.parent) + "/"
    print(f"[{ 'dry-run' if dry_run else direction }] {selector} {'to' if direction == 'push' else 'from'} {machine.name}")
    run(flags + [source, destination], show=True)


def list_group(config: Config, machine: Machine, group: str) -> set[str]:
    entry = config.artifacts[group]
    depth = int(entry.get("depth", 2))
    root = machine.repo_path / entry["path"]
    command = ["find", str(root), "-mindepth", str(depth), "-maxdepth", str(depth), "-printf", "%P\\n"]
    result = remote(machine, command, check=False)
    if result.returncode:
        stderr = result.stderr.decode(errors="replace").strip()
        raise RigSyncError(f"inventory failed for {machine.name}/{group}: {stderr or 'find failed'}")
    return {line for line in result.stdout.decode(errors="replace").splitlines() if line}


def status(config: Config, machines: list[Machine], group_filter: str | None) -> None:
    groups = [group_filter] if group_filter else list(config.artifacts)
    unknown = [group for group in groups if group not in config.artifacts]
    if unknown:
        raise RigSyncError(f"unknown artifact group: {unknown[0]}")
    for group in groups:
        inventory = {machine.name: list_group(config, machine, group) for machine in machines}
        entries = sorted(set().union(*inventory.values()))
        print(f"[{group}]")
        print("entry\t" + "\t".join(machine.name for machine in machines))
        for entry in entries:
            print(entry + "\t" + "\t".join("yes" if entry in inventory[m.name] else "." for m in machines))
        if not entries:
            print("(empty)")


def doctor(config: Config, machines: list[Machine]) -> None:
    failures = 0
    for binary in ("rsync", "ssh", "git"):
        found = shutil.which(binary)
        print(f"{'OK' if found else 'FAIL'} local {binary}: {found or 'missing'}")
        failures += not bool(found)
    for machine in machines:
        if machine.hostname is not None:
            hostname_result = remote(machine, ["hostname"], check=False)
            observed = hostname_result.stdout.decode(errors="replace").strip()
            hostname_ok = hostname_result.returncode == 0 and observed == machine.hostname
            if hostname_ok:
                print(f"OK {machine.name} hostname: {observed}")
            else:
                actual = observed or "unavailable"
                print(
                    f"FAIL {machine.name} hostname: expected {machine.hostname}, got {actual}"
                )
            failures += not hostname_ok
        result = remote(machine, ["sh", "-c", f"command -v rsync >/dev/null && command -v find >/dev/null && test -d {shlex.quote(str(machine.repo_path))}"], check=False)
        ok = result.returncode == 0
        print(f"{'OK' if ok else 'FAIL'} {machine.name}: {'local' if machine.local else machine.ssh} -> {machine.repo_path}")
        failures += not ok
    if failures:
        raise RigSyncError(f"doctor failed with {failures} check(s)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--registry", type=Path, default=None)
    sub = parser.add_subparsers(dest="command", required=True)
    doctor_p = sub.add_parser("doctor")
    doctor_p.add_argument("--machines")
    status_p = sub.add_parser("status")
    status_p.add_argument("--machines")
    status_p.add_argument("--group")
    source_p = sub.add_parser("push-source")
    source_p.add_argument("--to", required=True)
    source_p.add_argument("--dry-run", action="store_true")
    source_p.add_argument("--confirm", action="store_true")
    prepare_p = sub.add_parser("prepare")
    prepare_p.add_argument("--machine", required=True)
    prepare_p.add_argument("--dry-run", action="store_true")
    prepare_p.add_argument("--confirm", action="store_true")
    for name in ("push", "pull"):
        transfer_p = sub.add_parser(name)
        transfer_p.add_argument("selector")
        transfer_p.add_argument("--to" if name == "push" else "--from", dest="machine", required=True)
        transfer_p.add_argument("--dry-run", action="store_true")
        transfer_p.add_argument("--confirm", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = args.root.resolve()
    config_path = (args.config or Path(os.environ.get("RIGSYNC_CONFIG", root / "sync.toml"))).resolve()
    registry_path = (args.registry or Path(os.environ.get("RIGSYNC_REGISTRY", "~/.config/rigsync/machines.toml")).expanduser()).resolve()
    try:
        config = load_config(root, config_path, registry_path)
        if args.command == "doctor":
            doctor(config, selected_machines(config, args.machines))
        elif args.command == "status":
            status(config, selected_machines(config, args.machines), args.group)
        elif args.command == "push-source":
            if args.to not in config.machines:
                raise RigSyncError(f"unknown machine: {args.to}")
            push_source(config, config.machines[args.to], args.dry_run, args.confirm)
        elif args.command == "prepare":
            if args.machine not in config.machines:
                raise RigSyncError(f"unknown machine: {args.machine}")
            prepare(config.machines[args.machine], args.dry_run, args.confirm)
        else:
            if args.machine not in config.machines:
                raise RigSyncError(f"unknown machine: {args.machine}")
            transfer(
                config,
                config.machines[args.machine],
                args.selector,
                args.command,
                args.dry_run,
                args.confirm,
            )
    except RigSyncError as exc:
        print(f"rigsync: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
