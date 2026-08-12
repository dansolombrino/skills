#!/usr/bin/env python3
"""Deploy exact Git revisions and synchronize research artifacts over SSH."""

from __future__ import annotations

import argparse
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
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
    storage_root: Path | None = None
    quota_fs: str | None = None
    min_free_gb: float | None = None
    caches: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class GitSettings:
    remote: str
    branch: str


@dataclass(frozen=True)
class Config:
    root: Path
    artifacts: dict[str, dict]
    machines: dict[str, Machine]
    git: GitSettings | None = None


WAVE_RE = re.compile(r"^\d{8}-\d{6}$")
REVISION_RE = re.compile(r"^[0-9a-f]{40}$")
DEPENDENCY_FILE_RE = re.compile(
    r"^(?:pyproject\.toml|uv\.toml|uv\.lock|\.python-version|sync\.toml|"
    r"poetry\.lock|setup\.cfg|setup\.py|"
    r"Pipfile(?:\.lock)?|Dockerfile(?:\..+)?|requirements[^/]*\.txt|"
    r"environment[^/]*\.ya?ml)$"
)
ALLOWED_IGNORED_PARTS = {
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
}


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
    raw_git = project.get("git")
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
        raw_storage = registry_entry.get("storage_root")
        if raw_storage is not None and (not isinstance(raw_storage, str) or not raw_storage):
            raise RigSyncError(
                f"{registry_path}: machines.{name}.storage_root must be a non-empty string"
            )
        storage_root = Path(raw_storage) if raw_storage is not None else None
        if storage_root is not None and not storage_root.is_absolute():
            raise RigSyncError(
                f"{registry_path}: machines.{name}.storage_root must be absolute"
            )
        quota_fs = registry_entry.get("quota_fs")
        if quota_fs is not None and (not isinstance(quota_fs, str) or not quota_fs):
            raise RigSyncError(
                f"{registry_path}: machines.{name}.quota_fs must be a non-empty string"
            )
        min_free_gb = registry_entry.get("min_free_gb")
        if min_free_gb is not None:
            if isinstance(min_free_gb, bool) or not isinstance(min_free_gb, (int, float)):
                raise RigSyncError(
                    f"{registry_path}: machines.{name}.min_free_gb must be a number"
                )
            if min_free_gb < 0:
                raise RigSyncError(
                    f"{registry_path}: machines.{name}.min_free_gb must not be negative"
                )
        raw_caches = registry_entry.get("caches", {})
        if not isinstance(raw_caches, dict):
            raise RigSyncError(f"{registry_path}: machines.{name}.caches must be a table")
        caches: list[tuple[str, str]] = []
        for var, value in raw_caches.items():
            if not ENV_NAME_RE.fullmatch(var):
                raise RigSyncError(
                    f"{registry_path}: machines.{name}.caches key {var!r} is not a valid env name"
                )
            if not isinstance(value, str) or not value:
                raise RigSyncError(
                    f"{registry_path}: machines.{name}.caches.{var} must be a non-empty string"
                )
            if not PurePosixPath(value).is_absolute():
                raise RigSyncError(
                    f"{registry_path}: machines.{name}.caches.{var} must be an absolute path"
                )
            caches.append((var, value))
        local = name == current or hostname == current
        machines[name] = Machine(
            name,
            ssh,
            hostname,
            repo_path,
            local,
            storage_root,
            quota_fs,
            float(min_free_gb) if min_free_gb is not None else None,
            tuple(sorted(caches)),
        )

    for group, entry in artifacts.items():
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise RigSyncError(f"{config_path}: artifacts.{group}.path must be set")
        rel = PurePosixPath(entry["path"])
        if rel.is_absolute() or ".." in rel.parts:
            raise RigSyncError(f"{config_path}: artifacts.{group}.path must be repo-relative")
    git = None
    if raw_git is not None:
        if not isinstance(raw_git, dict):
            raise RigSyncError(f"{config_path}: [git] must be a table")
        remote_name = raw_git.get("remote")
        branch = raw_git.get("branch")
        if not isinstance(remote_name, str) or not remote_name:
            raise RigSyncError(f"{config_path}: git.remote must be set")
        if not isinstance(branch, str) or not branch:
            raise RigSyncError(f"{config_path}: git.branch must be set")
        git = GitSettings(remote_name, branch)
    return Config(root=root, artifacts=artifacts, machines=machines, git=git)


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


def require_git(config: Config) -> GitSettings:
    if config.git is None:
        raise RigSyncError("sync.toml needs [git] with remote and branch for revision deployment")
    return config.git


def git_command(machine: Machine, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return remote(machine, ["git", "-C", str(machine.repo_path), *args], check=check)


def canonical_remote_url(config: Config) -> str:
    git = require_git(config)
    result = run(["git", "-C", str(config.root), "remote", "get-url", git.remote])
    url = result.stdout.decode(errors="replace").strip()
    if not url:
        raise RigSyncError(f"hub remote {git.remote!r} has no URL")
    return url


def validate_wave_revision(wave: str, revision: str) -> str:
    if not WAVE_RE.fullmatch(wave):
        raise RigSyncError(f"invalid wave id: {wave!r}")
    if not REVISION_RE.fullmatch(revision):
        raise RigSyncError("revision must be a full lowercase 40-character commit SHA")
    return f"wave--{wave}"


def verify_remote_revision(config: Config, tag: str, revision: str) -> None:
    git = require_git(config)
    branch_ref = f"refs/heads/{git.branch}"
    tag_ref = f"refs/tags/{tag}^{{}}"
    result = run(
        [
            "env",
            "GIT_TERMINAL_PROMPT=0",
            "GIT_SSH_COMMAND=ssh -o BatchMode=yes -o ConnectTimeout=10",
            "git",
            "-C",
            str(config.root),
            "ls-remote",
            "--exit-code",
            git.remote,
            branch_ref,
            tag_ref,
        ]
    )
    refs = {}
    for line in result.stdout.decode(errors="replace").splitlines():
        sha, separator, ref = line.partition("\t")
        if separator:
            refs[ref] = sha
    problems = []
    if refs.get(branch_ref) != revision:
        problems.append(f"{branch_ref}={refs.get(branch_ref, 'missing')}")
    if refs.get(tag_ref) != revision:
        problems.append(f"{tag_ref}={refs.get(tag_ref, 'missing')}")
    if problems:
        raise RigSyncError(
            f"Git remote does not expose approved revision {revision}: " + ", ".join(problems)
        )


def execution_pathspecs(config: Config, revision: str) -> list[str]:
    result = run(
        ["git", "-C", str(config.root), "ls-tree", "-r", "--name-only", revision]
    )
    root_files = []
    for path in result.stdout.decode(errors="replace").splitlines():
        if "/" not in path and DEPENDENCY_FILE_RE.fullmatch(path):
            root_files.append(path)
    return ["code", "config", "scripts", *sorted(root_files)]


def ignored_runtime_path(path: str) -> bool:
    clean = path.rstrip("/")
    parts = PurePosixPath(clean).parts
    return (
        any(part in ALLOWED_IGNORED_PARTS for part in parts)
        or clean.endswith(".pyc")
        or clean.endswith(".pyo")
    )


def execution_drift(machine: Machine, pathspecs: list[str]) -> list[str]:
    result = git_command(
        machine,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
        "--ignored=matching",
        "--",
        *pathspecs,
    )
    drift: list[str] = []
    entries = [entry for entry in result.stdout.split(b"\0") if entry]
    skip_rename_source = False
    for raw in entries:
        if skip_rename_source:
            skip_rename_source = False
            continue
        text = raw.decode(errors="replace")
        if len(text) < 4:
            drift.append(text)
            continue
        status, path = text[:2], text[3:]
        if "R" in status or "C" in status:
            skip_rename_source = True
        if status == "!!" and ignored_runtime_path(path):
            continue
        drift.append(f"{status} {path}")
    return drift


def project_activity(config: Config, machine: Machine) -> list[str]:
    activity: list[str] = []
    project_prefix = f"{config.root.name}_"
    sessions = remote(
        machine,
        ["tmux", "list-sessions", "-F", "#{session_name}"],
        check=False,
    )
    if sessions.returncode == 0:
        activity.extend(
            f"tmux:{name}"
            for name in sessions.stdout.decode(errors="replace").splitlines()
            if name.startswith(project_prefix)
        )
    elif sessions.returncode != 1:
        stderr = sessions.stderr.decode(errors="replace").strip()
        raise RigSyncError(
            f"{machine.name}: cannot inspect tmux activity: {stderr or 'tmux failed'}"
        )
    evaluations = config.artifacts.get("evaluations", {}).get("path")
    if evaluations:
        status_root = machine.repo_path / evaluations
        exists = remote(machine, ["test", "-d", str(status_root)], check=False)
        if exists.returncode == 1:
            return activity
        if exists.returncode:
            raise RigSyncError(f"{machine.name}: cannot inspect status root {status_root}")
        running = remote(
            machine,
            [
                "sh",
                "-c",
                "find \"$1\" -name .status.json -type f -exec "
                "grep -l '\"state\"[[:space:]]*:[[:space:]]*\"running\"' {} + 2>/dev/null",
                "sh",
                str(status_root),
            ],
            check=False,
        )
        if running.returncode:
            stderr = running.stderr.decode(errors="replace").strip()
            raise RigSyncError(
                f"{machine.name}: cannot inspect running statuses: {stderr or 'find failed'}"
            )
        activity.extend(
            f"status:{path}"
            for path in running.stdout.decode(errors="replace").splitlines()
            if path
        )
    return activity


def git_repo_facts(config: Config, machine: Machine) -> tuple[str, str, str]:
    git = require_git(config)
    inside = git_command(machine, "rev-parse", "--is-inside-work-tree", check=False)
    if inside.returncode or inside.stdout.decode().strip() != "true":
        raise RigSyncError(f"{machine.name}: {machine.repo_path} is not a Git working tree")
    head = git_command(machine, "rev-parse", "HEAD").stdout.decode().strip()
    branch = git_command(machine, "branch", "--show-current").stdout.decode().strip()
    remote_url = git_command(machine, "remote", "get-url", git.remote).stdout.decode().strip()
    return head, branch, remote_url


def reject_unsupported_git_layout(machine: Machine) -> None:
    submodules = git_command(machine, "cat-file", "-e", "HEAD:.gitmodules", check=False)
    if submodules.returncode == 0:
        raise RigSyncError(f"{machine.name}: Git submodules are not supported by revision deployment")
    lfs = git_command(
        machine,
        "grep",
        "-q",
        "filter=lfs",
        "HEAD",
        "--",
        ".gitattributes",
        check=False,
    )
    if lfs.returncode == 0:
        raise RigSyncError(f"{machine.name}: Git LFS needs an explicit checkout policy")


def reject_unsupported_revision(config: Config, revision: str) -> None:
    submodules = run(
        ["git", "-C", str(config.root), "cat-file", "-e", f"{revision}:.gitmodules"],
        check=False,
    )
    if submodules.returncode == 0:
        raise RigSyncError("approved revision uses unsupported Git submodules")
    lfs = run(
        [
            "git",
            "-C",
            str(config.root),
            "grep",
            "-q",
            "filter=lfs",
            revision,
            "--",
            ".gitattributes",
        ],
        check=False,
    )
    if lfs.returncode == 0:
        raise RigSyncError("approved revision needs an explicit Git LFS checkout policy")


def verify_revision(
    config: Config,
    machines: list[Machine],
    wave: str,
    revision: str,
) -> None:
    tag = validate_wave_revision(wave, revision)
    verify_remote_revision(config, tag, revision)
    reject_unsupported_revision(config, revision)
    git = require_git(config)
    expected_url = canonical_remote_url(config)
    pathspecs = execution_pathspecs(config, revision)
    failures: list[str] = []
    for machine in machines:
        try:
            head, branch, remote_url = git_repo_facts(config, machine)
            reject_unsupported_git_layout(machine)
            tag_result = git_command(machine, "rev-parse", f"refs/tags/{tag}^{{commit}}", check=False)
            tag_revision = tag_result.stdout.decode(errors="replace").strip()
            drift = execution_drift(machine, pathspecs)
            problems = []
            if branch != git.branch:
                problems.append(f"branch={branch or '(detached)'} expected={git.branch}")
            if remote_url != expected_url:
                problems.append(f"remote={remote_url!r} expected={expected_url!r}")
            if head != revision:
                problems.append(f"HEAD={head} expected={revision}")
            if tag_result.returncode or tag_revision != revision:
                problems.append(f"tag {tag} does not resolve to {revision}")
            if drift:
                problems.append("execution tree dirty: " + ", ".join(drift[:8]))
            if problems:
                failures.append(f"{machine.name}: " + "; ".join(problems))
                print(f"FAIL {machine.name} revision: " + "; ".join(problems))
            else:
                print(f"OK {machine.name} revision: {revision} ({tag})")
        except RigSyncError as exc:
            failures.append(str(exc))
            print(f"FAIL {machine.name} revision: {exc}")
    if failures:
        raise RigSyncError(f"revision verification failed on {len(failures)} machine(s)")


def deploy_revision(
    config: Config,
    machines: list[Machine],
    wave: str,
    revision: str,
    dry_run: bool,
    confirmed: bool,
) -> None:
    require_confirmation(dry_run, confirmed)
    tag = validate_wave_revision(wave, revision)
    verify_remote_revision(config, tag, revision)
    reject_unsupported_revision(config, revision)
    git = require_git(config)
    expected_url = canonical_remote_url(config)
    pathspecs = execution_pathspecs(config, revision)
    preflight: list[tuple[Machine, str]] = []
    for machine in machines:
        head, branch, remote_url = git_repo_facts(config, machine)
        reject_unsupported_git_layout(machine)
        problems = []
        if branch != git.branch:
            problems.append(f"branch={branch or '(detached)'} expected={git.branch}")
        if remote_url != expected_url:
            problems.append(f"remote={remote_url!r} expected={expected_url!r}")
        drift = execution_drift(machine, pathspecs)
        if drift:
            problems.append("execution tree dirty: " + ", ".join(drift[:8]))
        if head != revision:
            activity = project_activity(config, machine)
            if activity:
                problems.append("active work blocks revision change: " + ", ".join(activity[:8]))
        if problems:
            raise RigSyncError(f"{machine.name}: " + "; ".join(problems))
        preflight.append((machine, head))

    for machine, head in preflight:
        action = "verify/fetch tag" if head == revision else f"fast-forward {head} -> {revision}"
        print(f"[{'dry-run' if dry_run else 'deploy-revision'}] {machine.name}: {action} ({tag})")
        if dry_run:
            continue
        if machine.local and machine.repo_path.resolve() == config.root.resolve():
            continue
        git_command(machine, "fetch", "--no-tags", git.remote, git.branch)
        fetched = git_command(machine, "rev-parse", "FETCH_HEAD").stdout.decode().strip()
        if fetched != revision:
            raise RigSyncError(
                f"{machine.name}: fetched {git.remote}/{git.branch} at {fetched}, expected {revision}"
            )
        git_command(
            machine,
            "fetch",
            "--no-tags",
            git.remote,
            f"refs/tags/{tag}:refs/tags/{tag}",
        )
        tag_revision = git_command(machine, "rev-parse", f"refs/tags/{tag}^{{commit}}").stdout.decode().strip()
        if tag_revision != revision:
            raise RigSyncError(f"{machine.name}: fetched tag {tag} resolves to {tag_revision}")
        if head != revision:
            fast_forward = git_command(
                machine, "merge-base", "--is-ancestor", head, revision, check=False
            )
            if fast_forward.returncode:
                raise RigSyncError(
                    f"{machine.name}: {head} cannot fast-forward to approved revision {revision}"
                )
            git_command(machine, "merge", "--ff-only", revision)
    if not dry_run:
        verify_revision(config, machines, wave, revision)


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


def prepare(config: Config, machine: Machine, dry_run: bool, confirmed: bool) -> None:
    require_confirmation(dry_run, confirmed)
    if config.git is None:
        print(f"[{'dry-run' if dry_run else 'prepare'}] {machine.name}: mkdir -p {machine.repo_path}")
        if not dry_run:
            remote(machine, ["mkdir", "-p", str(machine.repo_path)])
        return

    existing_git = remote(machine, ["test", "-d", str(machine.repo_path / ".git")], check=False)
    if existing_git.returncode == 0:
        print(f"[existing] {machine.name}: Git working tree already exists at {machine.repo_path}")
        return
    existing_entries = remote(
        machine,
        ["find", str(machine.repo_path), "-mindepth", "1", "-maxdepth", "1", "-print", "-quit"],
        check=False,
    )
    if existing_entries.returncode == 0 and existing_entries.stdout.strip():
        raise RigSyncError(
            f"{machine.name}: refusing to convert non-empty non-Git path {machine.repo_path}"
        )
    remote_url = canonical_remote_url(config)
    print(
        f"[{'dry-run' if dry_run else 'prepare'}] {machine.name}: "
        f"git clone --branch {config.git.branch} {remote_url} {machine.repo_path}"
    )
    if not dry_run:
        remote(machine, ["mkdir", "-p", str(machine.repo_path.parent)])
        remote(
            machine,
            [
                "env",
                "GIT_TERMINAL_PROMPT=0",
                "GIT_SSH_COMMAND=ssh -o BatchMode=yes -o ConnectTimeout=10",
                "git",
                "clone",
                "--branch",
                config.git.branch,
                "--single-branch",
                remote_url,
                str(machine.repo_path),
            ],
        )


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


DEFAULT_MIN_FREE_GB = 25.0
KIB_PER_GB = 1024 * 1024
ENV_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
MANAGED_BEGIN = "# --- BEGIN rig-sync managed environment ---"
MANAGED_END = "# --- END rig-sync managed environment ---"
MANAGED_ENV_FILE = ".config/rigsync/env.sh"

# Variables that name a machine's shared caches or scratch. A project .env must
# never assign these: .env is loaded after the shell environment and silently
# overrides it, so one stale line sends every download to the wrong volume. They
# are also useless in .env for tools that run before Python reads it -- uv being
# the one that matters, since its cache is usually the largest of them all.
MACHINE_ENV_VARS = frozenset(
    {
        "HF_HOME",
        "HF_HUB_CACHE",
        "HF_DATASETS_CACHE",
        "HUGGINGFACE_HUB_CACHE",
        "TORCH_HOME",
        "UV_CACHE_DIR",
        "TRITON_CACHE_DIR",
        "XDG_CACHE_HOME",
        "TMPDIR",
        "WANDB_CACHE_DIR",
        "WANDB_DIR",
    }
)


def render_env_sh(machine: Machine) -> str:
    """The machine's managed environment file: POSIX sh, sourced by every shell."""
    lines = [
        "# Generated by rig-sync. Do not hand-edit -- run `rigsync provision-env`.",
        f"# Machine: {machine.name}",
        "",
    ]
    lines.extend(f"export {var}={shlex.quote(value)}" for var, value in machine.caches)
    return "\n".join(lines) + "\n"


def _managed_block() -> str:
    return "\n".join(
        [
            MANAGED_BEGIN,
            f'[ -f "$HOME/{MANAGED_ENV_FILE}" ] && . "$HOME/{MANAGED_ENV_FILE}"',
            MANAGED_END,
        ]
    )


def provision_env_script(machine: Machine) -> str:
    """Shell program that installs the managed env file and wires it into both shells.

    Wiring matters as much as the values. `~/.zshenv` is read by *every* zsh --
    interactive or not -- which is what dispatch needs, since waves run under
    nohup, `ssh <cmd>`, and generated scripts. `~/.bashrc` returns early for
    non-interactive shells on Debian-ish systems, so the block is prepended
    above that guard rather than appended below it. Both edits are idempotent:
    an existing managed block is stripped before the new one is written.
    """
    env_body = render_env_sh(machine)
    return f"""
set -eu
umask 022
mkdir -p "$HOME/$(dirname {shlex.quote(MANAGED_ENV_FILE)})"
cat > "$HOME/{MANAGED_ENV_FILE}" <<'RIGSYNC_ENV_EOF'
{env_body}RIGSYNC_ENV_EOF
chmod 0644 "$HOME/{MANAGED_ENV_FILE}"

strip_managed() {{
  awk '
    /^# --- BEGIN rig-sync managed environment ---$/ {{ skip = 1; next }}
    /^# --- END rig-sync managed environment ---$/   {{ skip = 0; next }}
    skip != 1 {{ print }}
  ' "$1"
}}

wire() {{
  target="$HOME/$1"
  mode="$2"
  [ -e "$target" ] || : > "$target"
  [ -e "$target.rigsync.bak" ] || cp -p "$target" "$target.rigsync.bak"
  strip_managed "$target" > "$target.rigsync.tmp"
  if [ "$mode" = prepend ]; then
    {{ printf '%s\\n\\n' {shlex.quote(_managed_block())}; cat "$target.rigsync.tmp"; }} > "$target.rigsync.new"
  else
    {{ cat "$target.rigsync.tmp"; printf '\\n%s\\n' {shlex.quote(_managed_block())}; }} > "$target.rigsync.new"
  fi
  mv "$target.rigsync.new" "$target"
  rm -f "$target.rigsync.tmp"
  echo "wired $target ($mode)"
}}

wire .zshenv append
wire .bashrc prepend
echo "wrote $HOME/{MANAGED_ENV_FILE}"
""".strip()


def provision_env(
    config: Config, machines: list[Machine], dry_run: bool, confirm: bool
) -> None:
    targets = [m for m in machines if m.caches]
    skipped = [m.name for m in machines if not m.caches]
    for name in skipped:
        print(f"skip {name}: no [machines.{name}.caches] in the registry")
    if not targets:
        raise RigSyncError("no selected machine declares caches")
    for machine in targets:
        print(f"--- {machine.name} ---")
        print(render_env_sh(machine).rstrip())
    if dry_run or not confirm:
        print("[dry-run] re-run with --confirm to write these files")
        if not dry_run and not confirm:
            raise RigSyncError("provision-env modifies shell startup files; pass --confirm")
        return
    for machine in targets:
        result = remote(machine, ["sh", "-c", provision_env_script(machine)], check=False)
        output = result.stdout.decode(errors="replace").strip()
        if result.returncode:
            stderr = result.stderr.decode(errors="replace").strip()
            raise RigSyncError(f"{machine.name}: provision-env failed: {stderr or output}")
        for line in output.splitlines():
            print(f"{machine.name}: {line}")


def check_machine_env(machine: Machine) -> tuple[int, list[str]]:
    """Confirm a NON-interactive shell on the rig sees the declared cache values.

    Verifying through `zsh -c` rather than reading the file is the whole point:
    an export that only a login or interactive shell can see is absent exactly
    when a dispatched wave needs it.
    """
    if not machine.caches:
        return 0, []
    probe = "; ".join(f"printf '%s\\n' \"${var}\"" for var, _ in machine.caches)
    result = remote(machine, ["zsh", "-c", probe], check=False)
    if result.returncode:
        return 1, [
            f"FAIL {machine.name} machine env: cannot probe a non-interactive zsh "
            "(run `rigsync provision-env`)"
        ]
    observed = result.stdout.decode(errors="replace").splitlines()
    failures, lines = 0, []
    for index, (var, expected) in enumerate(machine.caches):
        actual = observed[index].strip() if index < len(observed) else ""
        if actual == expected:
            continue
        failures += 1
        lines.append(
            f"FAIL {machine.name} machine env: {var}={actual or '(unset)'}, expected {expected}"
        )
    if not failures:
        lines.append(f"OK {machine.name} machine env: {len(machine.caches)} cache var(s) match")
    return failures, lines


def parse_env_assignments(text: str) -> dict[str, str]:
    """Keys assigned a non-empty value in a .env file (comments/blanks ignored)."""
    found: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, separator, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        if separator and value and ENV_NAME_RE.fullmatch(key):
            found[key] = value
    return found


def check_project_env(machine: Machine) -> tuple[int, list[str]]:
    """Reject a project .env that re-declares machine-level cache variables.

    Only meaningful once the registry declares that this rig owns those values:
    without a declared machine environment there is nothing for .env to shadow,
    and .env remains a legitimate place to set them.
    """
    if not machine.caches:
        return 0, []
    result = remote(
        machine,
        ["sh", "-c", f'cat {shlex.quote(str(machine.repo_path / ".env"))} 2>/dev/null || true'],
        check=False,
    )
    if result.returncode:
        return 0, []
    assignments = parse_env_assignments(result.stdout.decode(errors="replace"))
    offenders = sorted(set(assignments) & MACHINE_ENV_VARS)
    if not offenders:
        return 0, []
    return 1, [
        f"FAIL {machine.name} project .env overrides machine environment: "
        f"{', '.join(offenders)} -- remove them; the rig's environment owns these"
    ]


def _parse_quota(text: str, quota_fs: str) -> tuple[int, int] | None:
    """Return (free_kib, allowance_kib) for quota_fs, or None if not quota-limited.

    Parses `quota -w` output, whose space columns are 1K blocks:
    filesystem, used, soft, hard, grace, files, ... A used value may carry a
    trailing '*' when the soft limit is already exceeded. A limit of 0 means
    unlimited, so the hard limit is preferred and the soft one is the fallback.
    """
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 4 or fields[0] != quota_fs:
            continue
        used, soft, hard = fields[1].rstrip("*"), fields[2], fields[3]
        if not used.isdigit():
            return None
        for candidate in (hard, soft):
            if candidate.isdigit() and int(candidate) > 0:
                allowance = int(candidate)
                return max(allowance - int(used), 0), allowance
        return None
    return None


def _parse_df(text: str) -> tuple[int, int] | None:
    """Return (free_kib, total_kib) from `df -P` output, or None if unparseable."""
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        return None
    fields = lines[-1].split()
    if len(fields) < 4:
        return None
    try:
        return int(fields[3]), int(fields[1])
    except ValueError:
        return None


def _remote_stdout(machine: Machine, argv: list[str]) -> str | None:
    result = remote(machine, argv, check=False)
    if result.returncode != 0:
        return None
    return result.stdout.decode(errors="replace")


def check_storage(machine: Machine) -> tuple[int, list[str]]:
    """Verify repo_path sits on the declared volume and that headroom remains.

    Returns (failure_count, lines_to_print). Machines with no storage_root are
    skipped entirely rather than being measured against an invented default.
    """
    if machine.storage_root is None:
        return 0, []

    failures = 0
    lines: list[str] = []

    # Resolve both sides: a convenience symlink in $HOME can point at the large
    # volume, so comparing the declared strings would pass while the declaration
    # is still wrong -- and would break the day that symlink becomes a real dir.
    resolved = _remote_stdout(
        machine,
        [
            "sh",
            "-c",
            f"readlink -f {shlex.quote(str(machine.repo_path))}; "
            f"readlink -f {shlex.quote(str(machine.storage_root))}",
        ],
    )
    resolved_lines = resolved.split() if resolved else []
    if len(resolved_lines) != 2:
        lines.append(f"FAIL {machine.name} storage: cannot resolve repo_path or storage_root")
        return failures + 1, lines
    real_repo, real_root = PurePosixPath(resolved_lines[0]), PurePosixPath(resolved_lines[1])
    inside = real_repo == real_root or real_root in real_repo.parents
    if inside:
        lines.append(f"OK {machine.name} storage root: {real_repo}")
    else:
        lines.append(
            f"FAIL {machine.name} storage root: repo_path resolves to {real_repo}, "
            f"outside declared storage_root {real_root}"
        )
        failures += 1

    # A quota is not free space: df reports the filesystem, not the user's
    # allowance, and can show terabytes free where the next write fails with
    # "Disk quota exceeded". Ask the quota system first when one is declared.
    measure, source = None, ""
    if machine.quota_fs is not None:
        quota_text = _remote_stdout(machine, ["quota", "-w"])
        if quota_text is not None:
            measure = _parse_quota(quota_text, machine.quota_fs)
            source = f"quota({machine.quota_fs})"
        if measure is None:
            lines.append(
                f"WARN {machine.name} storage: no quota reading for {machine.quota_fs}, "
                "falling back to df"
            )
    if measure is None:
        df_text = _remote_stdout(machine, ["df", "-P", str(machine.storage_root)])
        if df_text is not None:
            measure = _parse_df(df_text)
            source = f"df({machine.storage_root})"

    if measure is None:
        lines.append(f"FAIL {machine.name} storage: cannot determine free space")
        return failures + 1, lines

    free_kib, total_kib = measure
    free_gb, total_gb = free_kib / KIB_PER_GB, total_kib / KIB_PER_GB
    floor_gb = machine.min_free_gb if machine.min_free_gb is not None else DEFAULT_MIN_FREE_GB
    headroom_ok = free_gb >= floor_gb
    status = "OK" if headroom_ok else "FAIL"
    lines.append(
        f"{status} {machine.name} storage free: {free_gb:.1f}G of {total_gb:.1f}G "
        f"via {source} (floor {floor_gb:.1f}G)"
    )
    failures += not headroom_ok
    return failures, lines


def doctor(config: Config, machines: list[Machine]) -> None:
    failures = 0
    for binary in ("rsync", "ssh", "git"):
        found = shutil.which(binary)
        print(f"{'OK' if found else 'FAIL'} local {binary}: {found or 'missing'}")
        failures += not bool(found)
    expected_url = None
    if config.git is not None:
        try:
            expected_url = canonical_remote_url(config)
        except RigSyncError as exc:
            print(f"FAIL hub Git remote: {exc}")
            failures += 1
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
        required = "command -v rsync >/dev/null && command -v find >/dev/null"
        if config.git is not None:
            required += " && command -v git >/dev/null && command -v tmux >/dev/null"
        result = remote(
            machine,
            ["sh", "-c", f"{required} && test -d {shlex.quote(str(machine.repo_path))}"],
            check=False,
        )
        ok = result.returncode == 0
        print(f"{'OK' if ok else 'FAIL'} {machine.name}: {'local' if machine.local else machine.ssh} -> {machine.repo_path}")
        failures += not ok
        if ok:
            for check in (check_storage, check_machine_env, check_project_env):
                check_failures, check_lines = check(machine)
                for line in check_lines:
                    print(line)
                failures += check_failures
        if config.git is not None and ok and expected_url is not None:
            try:
                _head, branch, remote_url = git_repo_facts(config, machine)
                reject_unsupported_git_layout(machine)
                identity_ok = branch == config.git.branch and remote_url == expected_url
                print(
                    f"{'OK' if identity_ok else 'FAIL'} {machine.name} Git: "
                    f"branch={branch or '(detached)'} remote={remote_url}"
                )
                failures += not identity_ok
                auth = remote(
                    machine,
                    [
                        "env",
                        "GIT_TERMINAL_PROMPT=0",
                        "GIT_SSH_COMMAND=ssh -o BatchMode=yes -o ConnectTimeout=10",
                        "git",
                        "-C",
                        str(machine.repo_path),
                        "ls-remote",
                        "--exit-code",
                        config.git.remote,
                        "HEAD",
                    ],
                    check=False,
                )
                auth_ok = auth.returncode == 0
                print(f"{'OK' if auth_ok else 'FAIL'} {machine.name} Git remote access")
                failures += not auth_ok
            except RigSyncError as exc:
                print(f"FAIL {machine.name} Git: {exc}")
                failures += 1
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
    env_p = sub.add_parser("provision-env")
    env_p.add_argument("--machines")
    env_p.add_argument("--dry-run", action="store_true")
    env_p.add_argument("--confirm", action="store_true")
    status_p = sub.add_parser("status")
    status_p.add_argument("--machines")
    status_p.add_argument("--group")
    source_p = sub.add_parser("push-source")
    source_p.add_argument("--to", required=True)
    source_p.add_argument("--dry-run", action="store_true")
    source_p.add_argument("--confirm", action="store_true")
    deploy_p = sub.add_parser("deploy-revision")
    deploy_p.add_argument("--wave", required=True)
    deploy_p.add_argument("--revision", required=True)
    deploy_p.add_argument("--branch", required=True)
    deploy_p.add_argument("--machines", required=True)
    deploy_p.add_argument("--dry-run", action="store_true")
    deploy_p.add_argument("--confirm", action="store_true")
    verify_p = sub.add_parser("verify-revision")
    verify_p.add_argument("--wave", required=True)
    verify_p.add_argument("--revision", required=True)
    verify_p.add_argument("--branch", required=True)
    verify_p.add_argument("--machines", required=True)
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
        elif args.command == "provision-env":
            provision_env(
                config,
                selected_machines(config, args.machines),
                args.dry_run,
                args.confirm,
            )
        elif args.command == "status":
            status(config, selected_machines(config, args.machines), args.group)
        elif args.command == "push-source":
            if args.to not in config.machines:
                raise RigSyncError(f"unknown machine: {args.to}")
            push_source(config, config.machines[args.to], args.dry_run, args.confirm)
        elif args.command in {"deploy-revision", "verify-revision"}:
            git = require_git(config)
            if args.branch != git.branch:
                raise RigSyncError(
                    f"requested branch {args.branch!r} does not match sync.toml branch {git.branch!r}"
                )
            machines = selected_machines(config, args.machines)
            if args.command == "deploy-revision":
                deploy_revision(
                    config,
                    machines,
                    args.wave,
                    args.revision,
                    args.dry_run,
                    args.confirm,
                )
            else:
                verify_revision(config, machines, args.wave, args.revision)
        elif args.command == "prepare":
            if args.machine not in config.machines:
                raise RigSyncError(f"unknown machine: {args.machine}")
            prepare(config, config.machines[args.machine], args.dry_run, args.confirm)
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
