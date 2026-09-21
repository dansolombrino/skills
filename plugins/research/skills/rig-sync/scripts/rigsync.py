#!/usr/bin/env python3
"""Deploy exact Git revisions and synchronize research artifacts over SSH."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath


class RigSyncError(RuntimeError):
    pass


@dataclass(frozen=True)
class StorageRoot:
    """One volume a machine sets aside for project checkouts, with that volume's own rules.

    `quota_fs` names the filesystem whose per-user quota bounds this root, `min_free_gb` is its
    headroom floor, and `system_disk` opts the root out of the check that it is not the
    filesystem of `/` -- the check that catches an unmounted disk's empty mount point.
    """

    path: PurePosixPath
    quota_fs: str | None = None
    min_free_gb: float | None = None
    system_disk: bool = False

    @property
    def floor_gb(self) -> float:
        return self.min_free_gb if self.min_free_gb is not None else DEFAULT_MIN_FREE_GB


@dataclass(frozen=True)
class Machine:
    name: str
    ssh: str
    hostname: str | None
    repo_path: Path
    local: bool
    # Every volume this machine hosts project checkouts on. A project's root is never declared
    # per project: it is whichever of these contains its resolved repo_path (`root_for`).
    storage_roots: tuple[StorageRoot, ...] = ()
    caches: tuple[tuple[str, str], ...] = ()
    # `gpus = "job"` in the registry: the ssh target (a Slurm login node) has no GPU; GPUs exist
    # only inside scheduled jobs. environment-sync skips its GPU probe and refuses lanes there.
    gpus_in_job: bool = False
    # `transfer_ssh` in the registry: a second ssh alias reaching the same filesystem, used by
    # `pull`/`push` artifact rsync only (a data mover); git, doctor, and probes keep using `ssh`.
    transfer_ssh: str | None = None


GPU_ACCESS_VALUES = ("ssh", "job")


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
WAVES_DIR = ".waves"
ENVS_DIR = ".envs"
ENV_KEY_RE = re.compile(r"^[0-9a-f]{16}$")
ENV_KEY_FILES = ("uv.lock", ".python-version", "pyproject.toml")
SESSION_WAVE_RE = re.compile(r"_(\d{8}-\d{6})_")
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


STORAGE_ROOT_KEYS = frozenset({"path", "quota_fs", "min_free_gb", "system_disk"})


def _registry_quota_fs(value: object, where: str) -> str | None:
    if value is not None and (not isinstance(value, str) or not value):
        raise RigSyncError(f"{where} must be a non-empty string")
    return value


def _registry_floor(value: object, where: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RigSyncError(f"{where} must be a number")
    if value < 0:
        raise RigSyncError(f"{where} must not be negative")
    return float(value)


def _registry_root_path(value: object, where: str) -> PurePosixPath:
    if not isinstance(value, str) or not value:
        raise RigSyncError(f"{where} must be a non-empty string")
    path = PurePosixPath(value)
    if not path.is_absolute() or ".." in path.parts:
        raise RigSyncError(f"{where} must be an absolute path without '..'")
    return path


def _parse_storage_roots(entry: dict, where: str) -> tuple[StorageRoot, ...]:
    """Read a registry entry's storage roots, accepting the single-root legacy form.

    `storage_root = "..."` with machine-level `quota_fs`/`min_free_gb` is one root carrying those
    rules. `storage_roots` lists several, each a path or a table with its own rules; there a
    machine-level `min_free_gb` is the default floor, and a machine-level `quota_fs` is refused
    because it cannot say which root it backs. Roots must not repeat or nest, so a path is
    contained by at most one of them.
    """
    quota_fs = _registry_quota_fs(entry.get("quota_fs"), f"{where}.quota_fs")
    floor = _registry_floor(entry.get("min_free_gb"), f"{where}.min_free_gb")
    legacy = entry.get("storage_root")
    listed = entry.get("storage_roots")
    if legacy is not None and listed is not None:
        raise RigSyncError(f"{where}: declare storage_roots or storage_root, not both")
    if legacy is not None:
        return (StorageRoot(_registry_root_path(legacy, f"{where}.storage_root"), quota_fs, floor),)
    if listed is None:
        return ()
    if quota_fs is not None:
        raise RigSyncError(
            f"{where}.quota_fs is ambiguous next to storage_roots; "
            "set quota_fs on the root whose filesystem it names"
        )
    if not isinstance(listed, list) or not listed:
        raise RigSyncError(f"{where}.storage_roots must be a non-empty array")
    roots: list[StorageRoot] = []
    for index, item in enumerate(listed):
        item_where = f"{where}.storage_roots[{index}]"
        if isinstance(item, str):
            item = {"path": item}
        if not isinstance(item, dict):
            raise RigSyncError(f"{item_where} must be a path string or a table")
        unknown = sorted(set(item) - STORAGE_ROOT_KEYS)
        if unknown:
            raise RigSyncError(f"{item_where} has unknown key(s): {', '.join(unknown)}")
        system_disk = item.get("system_disk", False)
        if not isinstance(system_disk, bool):
            raise RigSyncError(f"{item_where}.system_disk must be true or false")
        root_floor = _registry_floor(item.get("min_free_gb"), f"{item_where}.min_free_gb")
        roots.append(
            StorageRoot(
                _registry_root_path(item.get("path"), f"{item_where}.path"),
                _registry_quota_fs(item.get("quota_fs"), f"{item_where}.quota_fs"),
                floor if root_floor is None else root_floor,
                system_disk,
            )
        )
    for first in roots:
        for second in roots:
            if first is second:
                continue
            if first.path == second.path:
                raise RigSyncError(f"{where}.storage_roots lists {first.path} twice")
            if first.path in second.path.parents:
                raise RigSyncError(
                    f"{where}.storage_roots nests {second.path} inside {first.path}; "
                    "declare only one of them"
                )
    return tuple(roots)


def root_for(
    roots: tuple[StorageRoot, ...],
    path: PurePosixPath,
    real_roots: list[PurePosixPath] | None = None,
) -> StorageRoot | None:
    """The storage root containing `path`, or None.

    This is the one place a project's volume is decided, so every consumer agrees on it.
    `real_roots`, parallel to `roots`, compares against resolved root paths instead of the
    declared ones; the deepest match wins should two resolved roots nest.
    """
    candidates = real_roots if real_roots is not None else [root.path for root in roots]
    best: tuple[int, StorageRoot] | None = None
    for root, candidate in zip(roots, candidates):
        if path == candidate or candidate in path.parents:
            depth = len(candidate.parts)
            if best is None or depth > best[0]:
                best = (depth, root)
    return best[1] if best else None


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
        storage_roots = _parse_storage_roots(registry_entry, f"{registry_path}: machines.{name}")
        raw_gpus = registry_entry.get("gpus", "ssh")
        if raw_gpus not in GPU_ACCESS_VALUES:
            raise RigSyncError(
                f"{registry_path}: machines.{name}.gpus must be one of "
                + ", ".join(repr(value) for value in GPU_ACCESS_VALUES)
            )
        transfer_ssh = registry_entry.get("transfer_ssh")
        if transfer_ssh is not None and (not isinstance(transfer_ssh, str) or not transfer_ssh):
            raise RigSyncError(
                f"{registry_path}: machines.{name}.transfer_ssh must be a non-empty string"
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
            storage_roots,
            tuple(sorted(caches)),
            raw_gpus == "job",
            transfer_ssh,
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


def git_command(
    machine: Machine, *args: str, check: bool = True, tree: Path | None = None
) -> subprocess.CompletedProcess:
    return remote(machine, ["git", "-C", str(tree or machine.repo_path), *args], check=check)


def wave_tree(machine: Machine, wave: str) -> Path:
    """The detached worktree a wave executes from: `<repo_path>/.waves/<wave_id>`."""
    return machine.repo_path / WAVES_DIR / wave


def env_key(lock: bytes, python_version: bytes, pyproject: bytes) -> str:
    """Key of a wave environment: the inputs that decide what `uv sync --frozen` installs.

    Two revisions with the same lock, interpreter pin, and uv pin share one environment under
    `.envs/<key>`; any change to one of them yields a new key and therefore a new environment.
    """
    try:
        parsed = tomllib.loads(pyproject.decode())
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise RigSyncError(f"cannot parse pyproject.toml for the environment key: {exc}") from exc
    uv_pin = parsed.get("tool", {}).get("uv", {}).get("required-version", "")
    digest = hashlib.sha256()
    for label, value in (
        (b"uv.lock", lock),
        (b".python-version", python_version.strip()),
        (b"required-version", str(uv_pin).encode()),
    ):
        digest.update(label + b"\0" + str(len(value)).encode() + b"\0" + value)
    return digest.hexdigest()[:16]


def env_key_for_revision(root: Path, revision: str) -> str:
    files = []
    for name in ENV_KEY_FILES:
        result = run(["git", "-C", str(root), "show", f"{revision}:{name}"], check=False)
        if result.returncode:
            raise RigSyncError(f"revision {revision} has no {name}; the environment key needs it")
        files.append(result.stdout)
    return env_key(*files)


def env_key_for_tree(machine: Machine, tree: Path) -> str:
    files = []
    for name in ENV_KEY_FILES:
        result = remote(machine, ["cat", str(tree / name)], check=False)
        if result.returncode:
            raise RigSyncError(f"{machine.name}: {tree} has no {name}")
        files.append(result.stdout)
    return env_key(*files)


def canonical_remote_url(config: Config) -> str:
    git = require_git(config)
    result = run(["git", "-C", str(config.root), "remote", "get-url", git.remote])
    url = result.stdout.decode(errors="replace").strip()
    if not url:
        raise RigSyncError(f"hub remote {git.remote!r} has no URL")
    return url


def validate_wave_revision(wave: str, revision: str) -> str:
    validate_wave(wave)
    if not REVISION_RE.fullmatch(revision):
        raise RigSyncError("revision must be a full lowercase 40-character commit SHA")
    return f"wave--{wave}"


def validate_wave(wave: str) -> None:
    if not WAVE_RE.fullmatch(wave):
        raise RigSyncError(f"invalid wave id: {wave!r}")


def verify_remote_revision(config: Config, tag: str, revision: str) -> None:
    """The remote tag must name `revision`, and the remote branch must contain it.

    The branch may have moved on since dispatch (tracking and journal commits, later waves): a
    wave's worktree is pinned by its tag, so recovery must still find its revision acceptable.
    """
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
    branch_sha = refs.get(branch_ref)
    if branch_sha is None:
        problems.append(f"{branch_ref}=missing")
    elif branch_sha != revision:
        contained = run(
            ["git", "-C", str(config.root), "merge-base", "--is-ancestor", revision, branch_sha],
            check=False,
        )
        if contained.returncode:
            problems.append(
                f"{branch_ref}={branch_sha} does not contain {revision} "
                "(or that commit is unknown on the hub: fetch the branch there first)"
            )
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


def execution_drift(
    machine: Machine, pathspecs: list[str], tree: Path | None = None
) -> list[str]:
    result = git_command(
        machine,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
        "--ignored=matching",
        "--",
        *pathspecs,
        tree=tree,
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


UNATTRIBUTED = "?"


def wave_activity(config: Config, machine: Machine) -> dict[str, list[str]]:
    """Active work of this project on `machine`, grouped by wave id.

    Evidence: project tmux sessions, `running` statuses, and — on a Slurm login node — queued or
    running jobs submitted from this project root. Evidence that names no wave is grouped under
    `UNATTRIBUTED`; callers must treat it as blocking.
    """
    activity: dict[str, list[str]] = {}

    def add(wave: str | None, evidence: str) -> None:
        activity.setdefault(wave if wave and WAVE_RE.fullmatch(wave) else UNATTRIBUTED, []).append(
            evidence
        )

    project_prefix = f"{config.root.name}_"
    sessions = remote(
        machine,
        ["tmux", "list-sessions", "-F", "#{session_name}"],
        check=False,
    )
    if sessions.returncode == 0:
        for name in sessions.stdout.decode(errors="replace").splitlines():
            if name.startswith(project_prefix):
                match = SESSION_WAVE_RE.search(name[len(project_prefix):])
                add(match.group(1) if match else None, f"tmux:{name}")
    elif sessions.returncode != 1:
        stderr = sessions.stderr.decode(errors="replace").strip()
        raise RigSyncError(
            f"{machine.name}: cannot inspect tmux activity: {stderr or 'tmux failed'}"
        )
    evaluations = config.artifacts.get("evaluations", {}).get("path")
    if evaluations:
        status_root = machine.repo_path / evaluations
        exists = remote(machine, ["test", "-d", str(status_root)], check=False)
        if exists.returncode not in (0, 1):
            raise RigSyncError(f"{machine.name}: cannot inspect status root {status_root}")
        if exists.returncode == 0:
            for path, wave in running_statuses(machine, status_root):
                add(wave, f"status:{path}")
    if machine.gpus_in_job:
        queue = remote(
            machine, ["squeue", "--me", "--noheader", "--format=%i|%j|%Z"], check=False
        )
        if queue.returncode:
            stderr = queue.stderr.decode(errors="replace").strip()
            raise RigSyncError(
                f"{machine.name}: cannot inspect the Slurm queue: {stderr or 'squeue failed'}"
            )
        # Slurm may report the physical submit directory; accept the declared and resolved forms.
        roots = {str(machine.repo_path).rstrip("/")}
        if path_exists(machine, machine.repo_path):
            roots.add(resolved_dir(machine, machine.repo_path))
        for line in queue.stdout.decode(errors="replace").splitlines():
            job_id, _, rest = line.strip().partition("|")
            name, _, workdir = rest.rpartition("|")
            if workdir.rstrip("/") not in roots:
                continue
            wave, separator, _run = name.partition("__")
            add(wave if separator else None, f"slurm:{job_id}:{name}")
    return activity


def running_statuses(machine: Machine, status_root: Path) -> list[tuple[str, str | None]]:
    running = remote(
        machine,
        [
            "sh",
            "-c",
            # `{} \;` (not `{} +`): with `+`, find propagates grep's exit
            # status, and grep -l exits 1 whenever no file matches, so a rig
            # holding only done/failed statuses would look like a probe failure.
            "find \"$1\" -name .status.json -type f -exec "
            "grep -l '\"state\"[[:space:]]*:[[:space:]]*\"running\"' {} \\; 2>/dev/null",
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
    paths = [path for path in running.stdout.decode(errors="replace").splitlines() if path]
    if not paths:
        return []
    waves = remote(
        machine,
        ["grep", "-H", "-o", '"wave_id"[[:space:]]*:[[:space:]]*"[^"]*"', *paths],
        check=False,
    )
    by_path: dict[str, str] = {}
    for line in waves.stdout.decode(errors="replace").splitlines():
        path, _, match = line.rpartition(':"wave_id"')
        value = match.rpartition(":")[2].strip().strip('"')
        if path:
            by_path.setdefault(path, value)
    return [(path, by_path.get(path)) for path in paths]


def project_activity(config: Config, machine: Machine) -> list[str]:
    return [
        evidence for entries in wave_activity(config, machine).values() for evidence in entries
    ]


def git_repo_facts(config: Config, machine: Machine) -> tuple[str, str, str]:
    git = require_git(config)
    inside = git_command(machine, "rev-parse", "--is-inside-work-tree", check=False)
    if inside.returncode or inside.stdout.decode().strip() != "true":
        raise RigSyncError(f"{machine.name}: {machine.repo_path} is not a Git working tree")
    head = git_command(machine, "rev-parse", "HEAD").stdout.decode().strip()
    branch = git_command(machine, "branch", "--show-current").stdout.decode().strip()
    remote_url = git_command(machine, "remote", "get-url", git.remote).stdout.decode().strip()
    return head, branch, remote_url


def reject_unsupported_git_layout(machine: Machine, tree: Path | None = None) -> None:
    submodules = git_command(machine, "cat-file", "-e", "HEAD:.gitmodules", check=False, tree=tree)
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
        tree=tree,
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


def require_wave_isolation_ignores(config: Config) -> None:
    """The hub must ignore `.waves/` and `.envs/`, or worktrees leak into source listings."""
    for name in (WAVES_DIR, ENVS_DIR):
        probe = run(
            ["git", "-C", str(config.root), "check-ignore", "-q", f"{name}/probe"], check=False
        )
        if probe.returncode == 1:
            raise RigSyncError(
                f"wave-isolation contract missing: `{name}/` is not ignored by the project .gitignore"
            )
        if probe.returncode:
            raise RigSyncError(f"cannot check whether `{name}/` is ignored")


def path_exists(machine: Machine, path: Path) -> bool:
    result = remote(machine, ["test", "-e", str(path)], check=False)
    if result.returncode not in (0, 1):
        raise RigSyncError(f"{machine.name}: cannot inspect {path}")
    return result.returncode == 0


def resolved_dir(machine: Machine, path: Path, relative_to_git: bool = False) -> str:
    """Physical path of `path`, or of the Git common dir of the worktree at `path`."""
    script = (
        'cd "$1" && cd "$(git rev-parse --git-common-dir)" && pwd -P'
        if relative_to_git
        else 'cd "$1" && pwd -P'
    )
    result = remote(machine, ["sh", "-c", script, "sh", str(path)], check=False)
    if result.returncode:
        raise RigSyncError(f"{machine.name}: cannot resolve {path}")
    return result.stdout.decode(errors="replace").strip()


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
        tree = wave_tree(machine, wave)
        try:
            _head, branch, remote_url = git_repo_facts(config, machine)
            problems = []
            if branch != git.branch:
                problems.append(f"main checkout branch={branch or '(detached)'} expected={git.branch}")
            if remote_url != expected_url:
                problems.append(f"remote={remote_url!r} expected={expected_url!r}")
            if not path_exists(machine, tree):
                problems.append(f"missing wave worktree {tree}")
            else:
                common = resolved_dir(machine, tree, relative_to_git=True)
                if common != resolved_dir(machine, machine.repo_path / ".git"):
                    problems.append(f"{tree} is not a worktree of {machine.repo_path}")
                reject_unsupported_git_layout(machine, tree)
                head = git_command(machine, "rev-parse", "HEAD", tree=tree).stdout.decode().strip()
                if head != revision:
                    problems.append(f"worktree HEAD={head} expected={revision}")
                tag_result = git_command(
                    machine, "rev-parse", f"refs/tags/{tag}^{{commit}}", check=False
                )
                if tag_result.returncode or tag_result.stdout.decode().strip() != revision:
                    problems.append(f"tag {tag} does not resolve to {revision}")
                drift = execution_drift(machine, pathspecs, tree)
                if drift:
                    problems.append("execution tree dirty: " + ", ".join(drift[:8]))
            if problems:
                failures.append(f"{machine.name}: " + "; ".join(problems))
                print(f"FAIL {machine.name} revision: " + "; ".join(problems))
            else:
                print(f"OK {machine.name} revision: {revision} ({tag}) at {tree}")
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
    """Materialize `wave--<wave>` as the detached worktree `.waves/<wave>` on every machine.

    The main checkout is never moved, so waves of other revisions keep running untouched. An
    existing worktree is only verified, never repaired; a missing one (first dispatch, or a
    pruned wave being recovered) is created.
    """
    require_confirmation(dry_run, confirmed)
    tag = validate_wave_revision(wave, revision)
    verify_remote_revision(config, tag, revision)
    reject_unsupported_revision(config, revision)
    require_wave_isolation_ignores(config)
    git = require_git(config)
    expected_url = canonical_remote_url(config)
    preflight: list[tuple[Machine, bool]] = []
    for machine in machines:
        _head, branch, remote_url = git_repo_facts(config, machine)
        problems = []
        if branch != git.branch:
            problems.append(f"main checkout branch={branch or '(detached)'} expected={git.branch}")
        if remote_url != expected_url:
            problems.append(f"remote={remote_url!r} expected={expected_url!r}")
        if problems:
            raise RigSyncError(f"{machine.name}: " + "; ".join(problems))
        preflight.append((machine, path_exists(machine, wave_tree(machine, wave))))

    for machine, exists in preflight:
        tree = wave_tree(machine, wave)
        action = "verify existing worktree" if exists else f"add worktree at {revision}"
        print(f"[{'dry-run' if dry_run else 'deploy-revision'}] {machine.name}: {action} {tree} ({tag})")
        if dry_run:
            continue
        hub = machine.local and machine.repo_path.resolve() == config.root.resolve()
        if not hub:
            git_command(
                machine,
                "fetch",
                "--no-tags",
                git.remote,
                f"refs/tags/{tag}:refs/tags/{tag}",
            )
        tag_revision = git_command(
            machine, "rev-parse", f"refs/tags/{tag}^{{commit}}"
        ).stdout.decode().strip()
        if tag_revision != revision:
            raise RigSyncError(f"{machine.name}: tag {tag} resolves to {tag_revision}")
        if not exists:
            git_command(machine, "worktree", "add", "--detach", str(tree), revision)
    if not dry_run:
        verify_revision(config, machines, wave, revision)


def worktrees(machine: Machine) -> dict[str, Path]:
    """Wave worktrees registered in this machine's repository, by wave id."""
    listing = git_command(machine, "worktree", "list", "--porcelain")
    base = machine.repo_path / WAVES_DIR
    found: dict[str, Path] = {}
    for line in listing.stdout.decode(errors="replace").splitlines():
        if not line.startswith("worktree "):
            continue
        path = Path(line[len("worktree "):])
        if path.parent == base and WAVE_RE.fullmatch(path.name):
            found[path.name] = path
    return found


def environments(machine: Machine) -> list[str]:
    base = machine.repo_path / ENVS_DIR
    if not path_exists(machine, base):
        return []
    listing = remote(
        machine, ["find", str(base), "-mindepth", "1", "-maxdepth", "1", "-type", "d"]
    )
    return sorted(
        PurePosixPath(line).name
        for line in listing.stdout.decode(errors="replace").splitlines()
        if line
    )


def activity(config: Config, machines: list[Machine]) -> None:
    for machine in machines:
        active = wave_activity(config, machine)
        for wave, evidence in sorted(active.items()):
            print(f"ACTIVE {machine.name} {wave}: " + ", ".join(evidence[:8]))
        trees = worktrees(machine)
        print(f"WAVES {machine.name}: " + (" ".join(sorted(trees)) or "(none)"))
        print(f"ENVS {machine.name}: " + (" ".join(environments(machine)) or "(none)"))


def prune(
    config: Config,
    machines: list[Machine],
    waves: list[str],
    dry_run: bool,
    confirmed: bool,
) -> None:
    """Remove the named terminal waves' worktrees, then environments no worktree still uses."""
    require_confirmation(dry_run, confirmed)
    if not waves:
        raise RigSyncError("prune needs at least one wave id")
    for wave in waves:
        validate_wave(wave)
    plans = []
    for machine in machines:
        active = wave_activity(config, machine)
        if UNATTRIBUTED in active:
            raise RigSyncError(
                f"{machine.name}: activity without a wave id blocks pruning: "
                + ", ".join(active[UNATTRIBUTED][:8])
            )
        busy = sorted(set(waves) & set(active))
        if busy:
            raise RigSyncError(f"{machine.name}: active wave(s) cannot be pruned: {', '.join(busy)}")
        trees = worktrees(machine)
        doomed = {wave: trees[wave] for wave in waves if wave in trees}
        for wave, tree in doomed.items():
            dirty = git_command(
                machine, "status", "--porcelain", "--untracked-files=all", tree=tree
            ).stdout.decode(errors="replace").strip()
            if dirty:
                raise RigSyncError(f"{machine.name}: worktree {tree} is dirty; not pruning it")
        kept = {env_key_for_tree(machine, tree) for wave, tree in trees.items() if wave not in doomed}
        unused = []
        for name in environments(machine):
            if not ENV_KEY_RE.fullmatch(name):
                print(f"[prune] {machine.name}: leaving unmanaged {ENVS_DIR}/{name}")
            elif name not in kept:
                unused.append(name)
        plans.append((machine, doomed, unused))

    for machine, doomed, unused in plans:
        label = "dry-run" if dry_run else "prune"
        absent = sorted(set(waves) - set(doomed))
        if absent:
            print(f"[{label}] {machine.name}: no worktree for {', '.join(absent)}")
        for wave, tree in sorted(doomed.items()):
            print(f"[{label}] {machine.name}: remove worktree {tree}")
            if not dry_run:
                git_command(machine, "worktree", "remove", str(tree))
        for name in unused:
            path = machine.repo_path / ENVS_DIR / name
            print(f"[{label}] {machine.name}: remove unused environment {path}")
            if not dry_run:
                remove_environment(machine, path)
        if not dry_run:
            git_command(machine, "worktree", "prune")


def remove_environment(machine: Machine, path: Path) -> None:
    if path.parent != machine.repo_path / ENVS_DIR or not ENV_KEY_RE.fullmatch(path.name):
        raise RigSyncError(f"{machine.name}: refusing to remove unexpected path {path}")
    remote(machine, ["rm", "-rf", "--", str(path)])


def source_manifest(root: Path) -> list[str]:
    result = run(
        ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"]
    )
    paths = sorted({part.decode() for part in result.stdout.split(b"\0") if part})
    return [path for path in paths if path != ".git" and not path.startswith(".git/")]


def rsync_remote(machine: Machine, path: Path, *, alias: str | None = None) -> str:
    return f"{alias or machine.ssh}:{shlex.quote(str(path))}/"


def rsync_flags(machine: Machine) -> list[str]:
    flags = ["rsync", "-a", "--itemize-changes"]
    if not machine.local:
        flags += ["-e", "ssh -o BatchMode=yes -o ConnectTimeout=10"]
    return flags


def transfer_alias(machine: Machine) -> str:
    """The ssh alias artifact rsync goes through: `transfer_ssh` when declared, else `ssh`."""
    return machine.transfer_ssh or machine.ssh


# rsync exit codes that mean the connection dropped mid-transfer rather than that the transfer
# itself was wrong: 12 (protocol data stream, what a killed remote rsync produces) and 255 (ssh
# died). A data mover or a login node with a CPU-time limit can produce either on a large tree.
RSYNC_RETRY_CODES = {12: "error in rsync protocol data stream", 255: "ssh connection lost"}
RSYNC_ATTEMPTS = 3
# ssh also exits 255 when it never connected; retrying those only delays the same answer.
RSYNC_NO_RETRY_MARKERS = ("Permission denied", "Could not resolve hostname", "Host key verification failed")


def run_rsync_with_retry(command: list[str], *, attempts: int = RSYNC_ATTEMPTS) -> None:
    """Run an artifact rsync, retrying a bounded number of times when the connection drops.

    rsync is idempotent, so a retry resumes from what already landed; `--partial` keeps a
    half-copied large file for the delta pass instead of discarding it.
    """
    for attempt in range(1, attempts + 1):
        result = run(command, check=False, show=True)
        if result.returncode == 0:
            return
        reason = RSYNC_RETRY_CODES.get(result.returncode)
        stderr = result.stderr.decode(errors="replace").strip()
        if any(marker in stderr for marker in RSYNC_NO_RETRY_MARKERS):
            reason = None
        if reason is None or attempt == attempts:
            suffix = f" after {attempts} attempts" if reason is not None else ""
            raise RigSyncError(
                f"command failed ({result.returncode}){suffix}: {shlex.join(command)}\n{stderr}"
            )
        print(
            f"[retry] rsync exited {result.returncode} ({reason}); "
            f"attempt {attempt + 1}/{attempts} resumes the transfer",
            file=sys.stderr,
        )


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
    alias = transfer_alias(machine)
    flags = rsync_flags(machine) + ["--partial"]
    if dry_run:
        flags.append("--dry-run")
    if direction == "push":
        if not local_path.exists():
            raise RigSyncError(f"source does not exist: {local_path}")
        if not dry_run:
            # The parent is created over the login alias: `transfer_ssh` is for bulk rsync only.
            remote(machine, ["mkdir", "-p", str(remote_path.parent)])
        source = str(local_path)
        destination = (
            str(remote_path.parent) + "/"
            if machine.local
            else rsync_remote(machine, remote_path.parent, alias=alias)
        )
    else:
        if not dry_run:
            local_path.parent.mkdir(parents=True, exist_ok=True)
        source = str(remote_path) if machine.local else f"{alias}:{shlex.quote(str(remote_path))}"
        destination = str(local_path.parent) + "/"
    via = f" via {alias}" if not machine.local and alias != machine.ssh else ""
    print(
        f"[{ 'dry-run' if dry_run else direction }] {selector} "
        f"{'to' if direction == 'push' else 'from'} {machine.name}{via}"
    )
    command = flags + [source, destination]
    print(f"[rsync] {shlex.join(command)}")
    run_rsync_with_retry(command)


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
    # Every declared directory must exist before anything exports a path to it.
    # TMPDIR is the sharp edge: pointing it at a missing directory breaks tools
    # far away from here, in ways that do not name the cause.
    mkdirs = "\n".join(
        f"mkdir -p {shlex.quote(value)}" for _var, value in machine.caches
    )
    return f"""
set -eu
umask 022
{mkdirs}
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


def machine_env_probe(
    machine: Machine, probe: str
) -> tuple[subprocess.CompletedProcess | None, str]:
    """Run `probe` through a shell that reads what `provision-env` wired, or give up.

    `zsh -c` is the first choice because `~/.zshenv` is read by every zsh, so it
    proves the value is present for a dispatched wave and not merely for a human
    at a prompt. On a rig with no zsh the equivalent is the bare SSH command:
    sshd invokes the login shell as the remote shell, and bash reads `~/.bashrc`
    in exactly that case -- which is what `provision-env`'s prepend targets. That
    substitute does not exist for a local machine, where nothing wraps the call
    in a shell at all, so the probe is reported as impossible rather than faked.
    """
    has_zsh = remote(machine, ["sh", "-c", "command -v zsh >/dev/null"], check=False)
    if has_zsh.returncode == 0:
        return remote(machine, ["zsh", "-c", probe], check=False), "zsh"
    if not machine.local:
        return run(ssh_base(machine) + [probe], check=False), "login shell"
    return None, ""


def check_machine_env(machine: Machine) -> tuple[int, list[str]]:
    """Confirm a NON-interactive shell on the rig sees the declared cache values.

    Verifying through a shell rather than reading the file is the whole point:
    an export that only a login or interactive shell can see is absent exactly
    when a dispatched wave needs it.
    """
    if not machine.caches:
        return 0, []
    probe = "; ".join(f"printf '%s\\n' \"${var}\"" for var, _ in machine.caches)
    result, shell = machine_env_probe(machine, probe)
    if result is None:
        return 1, [
            f"FAIL {machine.name} machine env: no zsh on this machine, and it is local, "
            "so there is no non-interactive shell to probe -- verify the exports by hand"
        ]
    if result.returncode:
        return 1, [
            f"FAIL {machine.name} machine env: cannot probe a non-interactive {shell} "
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


def absolute_env_assignments(assignments: dict[str, str]) -> list[str]:
    """Keys whose value is an absolute path, machine-level variables excluded.

    Those are reported separately and more harshly; everything else that names an
    absolute path is a portability problem rather than an override.
    """
    return sorted(
        key
        for key, value in assignments.items()
        if key not in MACHINE_ENV_VARS and PurePosixPath(value).is_absolute()
    )


def check_project_env(machine: Machine) -> tuple[int, list[str]]:
    """Inspect the project's .env on the rig under two separate rules.

    Re-declaring a machine-level cache variable FAILs, and is only meaningful
    once the registry declares that this rig owns those values: without a
    declared machine environment there is nothing for .env to shadow, and .env
    remains a legitimate place to set them.

    Any other absolute path WARNs, whatever the registry says. Project-scoped
    storage is repo-relative precisely so that one .env is correct on every rig;
    an absolute value is right on the machine it was written on and points at a
    nonexistent mount on the next one.
    """
    result = remote(
        machine,
        ["sh", "-c", f'cat {shlex.quote(str(machine.repo_path / ".env"))} 2>/dev/null || true'],
        check=False,
    )
    if result.returncode:
        return 0, []
    assignments = parse_env_assignments(result.stdout.decode(errors="replace"))
    failures, lines = 0, []
    if machine.caches:
        offenders = sorted(set(assignments) & MACHINE_ENV_VARS)
        if offenders:
            failures += 1
            lines.append(
                f"FAIL {machine.name} project .env overrides machine environment: "
                f"{', '.join(offenders)} -- remove them; the rig's environment owns these"
            )
    absolute = absolute_env_assignments(assignments)
    if absolute:
        lines.append(
            f"WARN {machine.name} project .env sets absolute path(s): {', '.join(absolute)} "
            "-- project-scoped storage is repo-relative so one .env is correct on every rig"
        )
    return failures, lines


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


def _df_rows(text: str) -> list[tuple[str, int, int, str]]:
    """Return (filesystem, total_kib, free_kib, mount_point) per `df -P` row; [] if unparseable."""
    rows = []
    for line in [line for line in text.splitlines() if line.strip()][1:]:
        fields = line.split()
        if len(fields) < 6:
            return []
        try:
            rows.append((fields[0], int(fields[1]), int(fields[3]), " ".join(fields[5:])))
        except ValueError:
            return []
    return rows


def _parse_df(text: str) -> tuple[int, int] | None:
    """Return (free_kib, total_kib) of the first `df -P` row, or None if unparseable."""
    rows = _df_rows(text)
    return (rows[0][2], rows[0][1]) if rows else None


def _remote_stdout(machine: Machine, argv: list[str]) -> str | None:
    result = remote(machine, argv, check=False)
    if result.returncode != 0:
        return None
    return result.stdout.decode(errors="replace")


def _resolve_remote_paths(machine: Machine, paths: list[PurePosixPath]) -> list[PurePosixPath] | None:
    """`readlink -f` every path on the machine in one call; None unless all of them resolve."""
    script = " && ".join(f"readlink -f {shlex.quote(str(path))}" for path in paths)
    resolved = _remote_stdout(machine, ["sh", "-c", script])
    lines = [line.strip() for line in resolved.splitlines() if line.strip()] if resolved else []
    if len(lines) != len(paths):
        return None
    return [PurePosixPath(line) for line in lines]


def _declared_roots(machine: Machine) -> str:
    return ", ".join(str(root.path) for root in machine.storage_roots)


def check_storage(machine: Machine) -> tuple[int, list[str]]:
    """Verify repo_path sits on a declared, mounted volume and that its headroom remains.

    Returns (failure_count, lines_to_print). A machine with no storage roots fails
    rather than being measured against an invented default: silence here reads as
    a pass, and the fallback on a quota'd rig is $HOME -- the small volume.
    """
    if not machine.storage_roots:
        return 1, [
            f"FAIL {machine.name} storage: no storage root declared in the registry "
            f"(storage_roots); nothing to measure repo_path against"
        ]

    failures = 0
    lines: list[str] = []

    # Resolve every side: a convenience symlink in $HOME can point at the large
    # volume, so comparing the declared strings would pass while the declaration
    # is still wrong -- and would break the day that symlink becomes a real dir.
    resolved = _resolve_remote_paths(
        machine, [PurePosixPath(machine.repo_path), *(root.path for root in machine.storage_roots)]
    )
    if resolved is None:
        lines.append(f"FAIL {machine.name} storage: cannot resolve repo_path or a storage root")
        return failures + 1, lines
    real_repo, real_roots = resolved[0], resolved[1:]
    root = root_for(machine.storage_roots, real_repo, real_roots)
    if root is None:
        lines.append(
            f"FAIL {machine.name} storage root: repo_path resolves to {real_repo}, "
            f"outside every declared storage root ({_declared_roots(machine)})"
        )
        return failures + 1, lines
    # Naming the root makes a checkout that moved to another declared volume visible.
    lines.append(f"OK {machine.name} storage root: {real_repo} on {root.path}")

    # One df answers three questions: which filesystem backs the root, whether that
    # is the system filesystem, and -- absent a quota -- how much room is left.
    df_rows = _df_rows(_remote_stdout(machine, ["df", "-P", str(root.path), "/"]) or "")
    if len(df_rows) != 2:
        lines.append(f"FAIL {machine.name} storage: cannot read the filesystem of {root.path}")
        return failures + 1, lines
    (root_source, root_total, root_free, root_mount), (system_source, *_, system_mount) = df_rows

    # An unmounted disk leaves its mount point behind as an empty directory on the
    # system disk, where every path check still passes.
    if (root_source, root_mount) == (system_source, system_mount) and not root.system_disk:
        lines.append(
            f"FAIL {machine.name} storage volume: {root.path} is on the system filesystem "
            f"({root_source} at {root_mount}) -- is its volume mounted?"
        )
        failures += 1
    else:
        lines.append(f"OK {machine.name} storage volume: {root.path} on {root_source} at {root_mount}")

    if root.quota_fs is not None and root_source != root.quota_fs:
        lines.append(
            f"FAIL {machine.name} storage quota_fs: {root.path} is backed by {root_source}, "
            f"not the declared quota_fs {root.quota_fs}"
        )
        failures += 1

    # A quota is not free space: df reports the filesystem, not the user's
    # allowance, and can show terabytes free where the next write fails with
    # "Disk quota exceeded". Ask the quota system first when one is declared.
    measure, source = None, ""
    if root.quota_fs is not None:
        quota_text = _remote_stdout(machine, ["quota", "-w"])
        if quota_text is not None:
            measure = _parse_quota(quota_text, root.quota_fs)
            source = f"quota({root.quota_fs})"
        if measure is None:
            lines.append(
                f"WARN {machine.name} storage: no quota reading for {root.quota_fs}, "
                "falling back to df"
            )
    if measure is None:
        measure, source = (root_free, root_total), f"df({root.path})"

    free_kib, total_kib = measure
    free_gb, total_gb = free_kib / KIB_PER_GB, total_kib / KIB_PER_GB
    floor_gb = root.floor_gb
    headroom_ok = free_gb >= floor_gb
    status = "OK" if headroom_ok else "FAIL"
    lines.append(
        f"{status} {machine.name} storage free: {free_gb:.1f}G of {total_gb:.1f}G "
        f"via {source} (floor {floor_gb:.1f}G)"
    )
    failures += not headroom_ok
    return failures, lines


def check_paths(config: Config, config_path: Path, registry_path: Path) -> None:
    """Validate declared project locations against declared machine storage, offline.

    `doctor` already makes this comparison, but only after the repo_path
    existence probe passes -- so a path that is absolute, mistyped, and outside
    the rig's large volume is caught only once something has been cloned into
    it. This runs the same comparison before anything remote happens, which is
    what a scaffold needs: absoluteness and the presence of a registry entry are
    enforced when the config loads, so what remains is whether each declared
    location actually sits on one of the volumes that rig set aside for work --
    and whether the registry declares any volume at all, since an entry carrying
    only `ssh` would otherwise satisfy this gate vacuously.

    The comparison here is lexical. `doctor` resolves both sides with
    `readlink -f` on the rig, which this cannot do without touching it; a
    declaration that passes here can still fail there through a symlink, and
    that is the intended division of labour.
    """
    registry_machines = _load_toml(registry_path).get("machines", {})
    failures = 0
    for machine in config.machines.values():
        if not machine.storage_roots:
            # Not a pass. This is the only gate that catches a repo_path off the
            # rig's large volumes before anything is cloned into it, so a registry
            # that cannot answer the question fails it.
            print(
                f"FAIL {machine.name} path: no storage root declared for {machine.name} "
                f"in {registry_path} (storage_roots); the storage check cannot run"
            )
            failures += 1
            continue
        repo = PurePosixPath(machine.repo_path)
        root = root_for(machine.storage_roots, repo)
        if root is not None:
            print(f"OK {machine.name} path: {repo} under {root.path}")
            continue
        print(
            f"FAIL {machine.name} path: repo_path {repo} is not under any declared "
            f"storage root ({_declared_roots(machine)})"
        )
        failures += 1
    if isinstance(registry_machines, dict):
        for name in sorted(set(registry_machines) - set(config.machines)):
            print(f"note {name}: in the registry but not in {config_path.name}")
    if failures:
        raise RigSyncError(f"check-paths failed with {failures} declaration(s)")


def print_repo_path(machine: Machine) -> None:
    """The one declared project location for this rig, for dispatch to substitute."""
    print(machine.repo_path)


def storage_root_of_project(machine: Machine) -> StorageRoot | None:
    """The root this project's checkout lives on; None when the registry declares none.

    A single root needs no probe. With several, the rules that apply are those of the root
    containing the resolved repo_path, so resolve it on the machine rather than guess.
    """
    if len(machine.storage_roots) <= 1:
        return machine.storage_roots[0] if machine.storage_roots else None
    resolved = _resolve_remote_paths(
        machine, [PurePosixPath(machine.repo_path), *(root.path for root in machine.storage_roots)]
    )
    if resolved is None:
        raise RigSyncError(f"{machine.name}: cannot resolve repo_path or a storage root")
    root = root_for(machine.storage_roots, resolved[0], resolved[1:])
    if root is None:
        raise RigSyncError(
            f"{machine.name}: repo_path resolves to {resolved[0]}, outside every declared "
            f"storage root ({_declared_roots(machine)}); run doctor"
        )
    return root


def print_storage_env(machine: Machine) -> None:
    """Shell-assignable storage facts, straight from the registry.

    A wave script guards on headroom before it writes checkpoints. Typing these
    values a second time into that script is how the guard ends up interrogating
    a filesystem nothing is being written to, which reads as reassuring and
    means nothing. `MIN_FREE_KIB` is the registry floor: a wave may raise it for
    its own checkpoint footprint, but must not sink below it.
    """
    root = storage_root_of_project(machine)
    floor_gb = root.floor_gb if root is not None else DEFAULT_MIN_FREE_GB
    quota_fs = root.quota_fs if root is not None else None
    print(f"QUOTA_FS={shlex.quote(quota_fs or '')}")
    print(f"MIN_FREE_GB={floor_gb:g}")
    print(f"MIN_FREE_KIB={int(floor_gb * KIB_PER_GB)}")


def push_env(
    config: Config, machine: Machine, dry_run: bool, confirmed: bool, overwrite: bool
) -> None:
    """Place the hub's .env on a peer rig.

    `.env` is ignored by Git, so a freshly `prepare`d peer has none at all and
    every run there starts without the project's secrets and settings. It is
    copied rather than regenerated because it is rig-independent by
    construction: machine-level caches live in the registry and project-scoped
    storage is repo-relative, so the same file is correct everywhere.

    This moves secrets, so it is a protected write that says so, and it refuses
    to overwrite a peer's existing file without being told to.
    """
    require_confirmation(dry_run, confirmed)
    source = config.root / ".env"
    if not source.is_file():
        raise RigSyncError(f"no .env at {source}")
    if machine.local and machine.repo_path.resolve() == config.root.resolve():
        print(f"[local] {machine.name}: .env already at {source}")
        return
    assignments = parse_env_assignments(source.read_text(errors="replace"))
    offenders = sorted(set(assignments) & MACHINE_ENV_VARS)
    if offenders:
        raise RigSyncError(
            f"refusing to push a .env that assigns machine-level variables: "
            f"{', '.join(offenders)} -- the rig's environment owns these"
        )
    absolute = absolute_env_assignments(assignments)
    if absolute:
        print(
            f"WARN {machine.name}: .env sets absolute path(s): {', '.join(absolute)} "
            "-- these are unlikely to be correct on another rig"
        )
    destination_file = machine.repo_path / ".env"
    existing = remote(machine, ["test", "-f", str(destination_file)], check=False)
    if existing.returncode == 0 and not overwrite:
        raise RigSyncError(
            f"{machine.name}: {destination_file} already exists; "
            "pass --overwrite to replace it"
        )
    print(
        f"[{'dry-run' if dry_run else 'push-env'}] {machine.name}: "
        f"{source} -> {destination_file} (this file carries secrets)"
    )
    flags = rsync_flags(machine)
    if dry_run:
        flags.append("--dry-run")
    destination = (
        str(machine.repo_path) + "/" if machine.local else rsync_remote(machine, machine.repo_path)
    )
    run(flags + [str(source), destination], show=True)


# ───────────────────────────── offload ─────────────────────────────
#
# A rig with little disk still has a GPU worth using. `offload` is one pass of a continuous
# hub-ward copy of a wave's outputs, followed by the only deletion this script performs besides
# `prune`: a checkpoint is removed from the rig when, and only when, every clause of
# `offload_deletable` holds, and a receipt is written next to it first. Evaluations are copied and
# always kept: they are small, and they are what reconciliation and the run's self-guard read.

STATE_DIR = "_state"
OFFLOAD_RECEIPT_SUFFIX = ".offloaded.json"
OFFLOAD_MIN_AGE_S = 120
OFFLOAD_BATCH = 100


def wave_queue(config: Config, wave: str) -> dict:
    validate_wave(wave)
    path = config.root / WAVES_DIR / STATE_DIR / wave / "queue.json"
    try:
        queue = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise RigSyncError(f"wave {wave} is not registered with the supervisor on this hub: {path}") from exc
    if not isinstance(queue, dict) or not isinstance(queue.get("runs"), list):
        raise RigSyncError(f"{path}: not a supervisor queue")
    return queue


def checkpoints_root(config: Config) -> PurePosixPath:
    group = config.artifacts.get("checkpoints")
    if not isinstance(group, dict) or not group.get("path"):
        raise RigSyncError("sync.toml declares no [artifacts.checkpoints]; offload has nothing it may delete")
    return PurePosixPath(str(group["path"]))


def _safe_relative(value: object, where: str) -> PurePosixPath:
    path = PurePosixPath(str(value))
    if not value or path.is_absolute() or ".." in path.parts:
        raise RigSyncError(f"unsafe path in {where}: {value!r}")
    return path


def offload_inventory_script(runs: list[dict]) -> str:
    parts = ['cd "$1" || exit 3; echo "__NOW__ $(date +%s)"; ']
    for run in runs:
        parts.append(f"echo __RUN__ {shlex.quote(run['token'])}; tr -d '\\n' < {shlex.quote(run['status_path'])} 2>/dev/null; echo; ")
        for label, key in (("C", "checkpoint_dir"), ("E", "eval_dir")):
            directory = shlex.quote(run[key])
            parts.append(
                f"[ -d {directory} ] && find {directory} -type f ! -name '*{OFFLOAD_RECEIPT_SUFFIX}' ! -name '*.tmp' ! -name '*.tmp.*' "
                f"-printf '{label}\\t%s\\t%T@\\t%p\\n'; "
            )
    parts.append("echo __END__")
    return "".join(parts)


def parse_offload_inventory(text: str) -> tuple[float, dict[str, dict]]:
    if "__END__" not in text:
        raise RigSyncError("offload inventory was cut short; nothing is copied or deleted from a partial listing")
    rig_now = 0.0
    runs: dict[str, dict] = {}
    current: dict | None = None
    expect_status = False
    for line in text.splitlines():
        if line.startswith("__NOW__ "):
            rig_now = float(line.split()[1])
        elif line.startswith("__RUN__ "):
            current = runs.setdefault(line.split(" ", 1)[1].strip(), {"status": {}, "files": []})
            expect_status = True
        elif line == "__END__":
            break
        elif expect_status and current is not None:
            expect_status = False
            with contextlib.suppress(json.JSONDecodeError):
                payload = json.loads(line) if line.strip() else {}
                current["status"] = payload if isinstance(payload, dict) else {}
        elif current is not None and line[:2] in ("C\t", "E\t"):
            kind, size, mtime, path = line.split("\t", 3)
            current["files"].append({"kind": kind, "size": int(size), "mtime": float(mtime), "path": path})
    return rig_now, runs


def offload_deletable(
    *,
    file: dict,
    run: dict,
    status: dict,
    wave: str,
    rig_now: float,
    newest_checkpoint: str | None,
    checkpoint_root: PurePosixPath,
    verified: bool,
) -> str | None:
    """Return None when the rig copy may be deleted, else the clause that protects it."""
    path = PurePosixPath(file["path"])
    directory = PurePosixPath(run["checkpoint_dir"])
    if file["kind"] != "C":
        return "only checkpoints are ever deleted; evaluations stay on the rig"
    if ".." in path.parts or path.is_absolute() or directory not in path.parents:
        return "outside the run's recorded checkpoint directory"
    if checkpoint_root != directory and checkpoint_root not in directory.parents:
        return "the run's checkpoint directory is outside [artifacts.checkpoints]"
    if status.get("wave_id") != wave:
        return "the run's status does not carry this wave id: not produced by this wave"
    if rig_now - file["mtime"] < OFFLOAD_MIN_AGE_S:
        return f"written less than {OFFLOAD_MIN_AGE_S}s ago: may still be open"
    if status.get("state") != "done" and file["path"] == newest_checkpoint:
        return "newest checkpoint of a run that is not done: a resume needs it"
    if not verified:
        return "hub copy is not checksum-verified"
    return None


def _sha256_local(path: Path) -> str | None:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1 << 20), b""):
                digest.update(block)
    except OSError:
        return None
    return digest.hexdigest()


def _sha256_remote(machine: Machine, paths: list[str]) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for start in range(0, len(paths), OFFLOAD_BATCH):
        batch = paths[start : start + OFFLOAD_BATCH]
        result = remote(machine, ["sh", "-c", 'cd "$1" && shift && sha256sum -- "$@"', "rigsync", str(machine.repo_path), *batch], check=False)
        for line in result.stdout.decode(errors="replace").splitlines():
            digest, _, name = line.partition("  ")
            if len(digest) == 64 and name:
                hashes[name] = digest
    return hashes


def hub_machine(config: Config) -> Machine:
    hubs = [machine for machine in config.machines.values() if machine.local]
    if len(hubs) != 1:
        raise RigSyncError("offload runs on the hub: exactly one configured machine must be this host")
    return hubs[0]


def offload(config: Config, machine: Machine, wave: str, dry_run: bool, confirmed: bool) -> None:
    require_confirmation(dry_run, confirmed)
    queue = wave_queue(config, wave)
    checkpoint_root = checkpoints_root(config)
    hub = hub_machine(config)
    if machine.local:
        print(f"[local] {machine.name} is the hub; there is nothing to offload")
        return
    failures, lines = check_storage(hub)
    if failures:
        raise RigSyncError("hub storage is under its floor; offload would only move the problem:\n" + "\n".join(lines))
    runs = []
    for entry in queue["runs"]:
        for key in ("status_path", "checkpoint_dir", "eval_dir"):
            _safe_relative(entry.get(key), f"queue run {entry.get('id')!r}.{key}")
        runs.append(entry)
    result = remote(machine, ["sh", "-c", offload_inventory_script(runs), "rigsync", str(machine.repo_path)], check=False)
    if result.returncode != 0:
        raise RigSyncError(f"{machine.name}: offload inventory failed ({result.returncode})")
    rig_now, inventory = parse_offload_inventory(result.stdout.decode(errors="replace"))
    label = "dry-run" if dry_run else "offload"
    by_token = {entry["token"]: entry for entry in runs}
    to_copy: list[dict] = []
    candidates: list[tuple[dict, dict, dict, str | None]] = []
    for token, found in inventory.items():
        entry = by_token[token]
        checkpoints = [f for f in found["files"] if f["kind"] == "C"]
        newest = max(checkpoints, key=lambda f: f["mtime"])["path"] if checkpoints else None
        for file in found["files"]:
            _safe_relative(file["path"], f"{machine.name} inventory")
            if rig_now - file["mtime"] < OFFLOAD_MIN_AGE_S:
                continue
            local = config.root / file["path"]
            if not local.is_file() or local.stat().st_size != file["size"] or int(local.stat().st_mtime) != int(file["mtime"]):
                to_copy.append(file)
            if file["kind"] == "C":
                candidates.append((file, entry, found["status"], newest))
    copied_bytes = sum(f["size"] for f in to_copy)
    if to_copy:
        alias = transfer_alias(machine)
        command = rsync_flags(machine) + ["--partial", "--from0", "--files-from=-"]
        if dry_run:
            command.append("--dry-run")
        command += [rsync_remote(machine, machine.repo_path, alias=alias), str(config.root) + "/"]
        print(f"[{label}] {machine.name}: copy {len(to_copy)} file(s), {copied_bytes / 1e6:.1f} MB, to the hub")
        payload = b"\0".join(f["path"].encode() for f in to_copy) + b"\0"
        for attempt in range(1, RSYNC_ATTEMPTS + 1):
            copy = run(command, input_bytes=payload, check=False)
            if copy.returncode == 0 or copy.returncode not in RSYNC_RETRY_CODES or attempt == RSYNC_ATTEMPTS:
                break
        if copy.returncode != 0:
            raise RigSyncError(f"offload copy failed ({copy.returncode}): {copy.stderr.decode(errors='replace').strip()[-400:]}")
    if dry_run:
        for file, entry, status, newest in candidates:
            reason = offload_deletable(file=file, run=entry, status=status, wave=wave, rig_now=rig_now, newest_checkpoint=newest, checkpoint_root=checkpoint_root, verified=True)
            print(f"[dry-run] {machine.name}: {'delete after verification' if reason is None else 'keep'} {file['path']}" + (f" ({reason})" if reason else ""))
        print(f"[offload-summary] {machine.name}: would copy {len(to_copy)}, {len(candidates)} checkpoint file(s) examined")
        return
    unverified = [c for c in candidates if offload_deletable(file=c[0], run=c[1], status=c[2], wave=wave, rig_now=rig_now, newest_checkpoint=c[3], checkpoint_root=checkpoint_root, verified=True) is None]
    remote_hashes = _sha256_remote(machine, [c[0]["path"] for c in unverified])
    deleted = 0
    deleted_bytes = 0
    doomed: list[tuple[str, str]] = []
    for file, entry, status, newest in unverified:
        local_hash = _sha256_local(config.root / file["path"])
        verified = local_hash is not None and remote_hashes.get(file["path"]) == local_hash
        if offload_deletable(file=file, run=entry, status=status, wave=wave, rig_now=rig_now, newest_checkpoint=newest, checkpoint_root=checkpoint_root, verified=verified) is not None:
            continue
        receipt = json.dumps(
            {
                "path": file["path"], "size": file["size"], "sha256": local_hash, "wave_id": wave, "rig": machine.name,
                "hub": hub.name, "hub_path": str(config.root / file["path"]), "offloaded_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            },
            sort_keys=True,
        )
        doomed.append((file["path"], receipt))
        deleted += 1
        deleted_bytes += file["size"]
    # receipt first, atomically, then the unlink; a pass cut short leaves a file or a receipt, never neither
    unlink = 'cd "$1" || exit 3; shift; while [ "$#" -gt 1 ]; do f=$1; r=$2; shift 2; printf %s "$r" > "$f' + OFFLOAD_RECEIPT_SUFFIX + '.tmp" && mv "$f' + OFFLOAD_RECEIPT_SUFFIX + '.tmp" "$f' + OFFLOAD_RECEIPT_SUFFIX + '" && rm -f -- "$f" || exit 4; done'
    for start in range(0, len(doomed), OFFLOAD_BATCH):
        flat = [item for pair in doomed[start : start + OFFLOAD_BATCH] for item in pair]
        remote(machine, ["sh", "-c", unlink, "rigsync", str(machine.repo_path), *flat])
        for path, _receipt in doomed[start : start + OFFLOAD_BATCH]:
            print(f"[offload] {machine.name}: receipt written, removed {path}")
    pending = len(candidates) - deleted
    print(f"[offload-summary] {machine.name}: copied {len(to_copy)} ({copied_bytes / 1e6:.0f} MB), freed {deleted} ({deleted_bytes / 1e6:.0f} MB), {pending} checkpoint file(s) kept")


def restore(config: Config, machine: Machine, wave: str, run_id: str, dry_run: bool, confirmed: bool) -> None:
    """Hub -> rig copy of one run's checkpoints, for a run that resumes on a rig that never had them."""
    require_confirmation(dry_run, confirmed)
    queue = wave_queue(config, wave)
    run_entry = next((r for r in queue["runs"] if r.get("id") == run_id or r.get("token") == run_id), None)
    if run_entry is None:
        raise RigSyncError(f"no run {run_id!r} in wave {wave}")
    relative = _safe_relative(run_entry["checkpoint_dir"], "queue checkpoint_dir")
    source = config.root / relative
    if not source.is_dir():
        raise RigSyncError(f"the hub holds no checkpoints for {run_id}: {source}")
    if machine.local:
        print(f"[local] {relative} already resides on {machine.name}")
        return
    destination = machine.repo_path / relative
    command = rsync_flags(machine) + ["--partial", f"--exclude=*{OFFLOAD_RECEIPT_SUFFIX}"]
    if dry_run:
        command.append("--dry-run")
    else:
        remote(machine, ["mkdir", "-p", str(destination)])
    command += [str(source) + "/", rsync_remote(machine, destination, alias=transfer_alias(machine))]
    print(f"[{'dry-run' if dry_run else 'restore'}] {relative} to {machine.name}")
    print(f"[rsync] {shlex.join(command)}")
    run_rsync_with_retry(command)


def sync_cache(config: Config, machine: Machine, variable: str, subpath: str, dry_run: bool, confirmed: bool) -> None:
    """Hub -> rig copy of one subtree of a declared shared cache. Never deletes, and never two at once."""
    require_confirmation(dry_run, confirmed)
    if variable not in MACHINE_ENV_VARS:
        raise RigSyncError(f"{variable} is not a machine-level cache variable")
    hub = hub_machine(config)
    relative = _safe_relative(subpath, "--subpath")
    roots = {}
    for side in (hub, machine):
        declared = dict(side.caches).get(variable)
        if not declared:
            raise RigSyncError(f"{side.name} declares no {variable} in the registry; a cache path is never guessed")
        roots[side.name] = Path(declared)
    source = roots[hub.name] / relative
    if not source.exists():
        raise RigSyncError(f"the hub cache holds no {relative} under {variable}")
    if machine.local:
        print(f"[local] {variable}/{relative} already resides on {machine.name}")
        return
    destination = roots[machine.name] / relative
    lock = shlex.quote(str(roots[machine.name] / ".rigsync-cache.lock"))
    command = rsync_flags(machine) + ["--partial", f"--rsync-path=flock {lock} rsync"]
    if dry_run:
        command.append("--dry-run")
    else:
        remote(machine, ["mkdir", "-p", str(destination.parent if source.is_file() else destination)])
    tail = "" if source.is_file() else "/"
    command += [str(source) + tail, f"{transfer_alias(machine)}:{shlex.quote(str(destination))}{tail}"]
    print(f"[{'dry-run' if dry_run else 'sync-cache'}] {variable}/{relative} to {machine.name}")
    print(f"[rsync] {shlex.join(command)}")
    run_rsync_with_retry(command)


MIN_GIT = (2, 17)


def worktree_capable(version_text: str) -> bool:
    """`git worktree remove`, which pruning relies on, arrived in Git 2.17."""
    match = re.search(r"(\d+)\.(\d+)", version_text)
    return bool(match) and (int(match.group(1)), int(match.group(2))) >= MIN_GIT


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
                version = remote(machine, ["git", "--version"], check=False)
                version_ok = worktree_capable(version.stdout.decode(errors="replace"))
                print(
                    f"{'OK' if version_ok else 'FAIL'} {machine.name} Git worktrees: "
                    f"{version.stdout.decode(errors='replace').strip() or 'unknown version'}"
                    + ("" if version_ok else f" (need >= {'.'.join(map(str, MIN_GIT))})")
                )
                failures += not version_ok
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
    sub.add_parser("check-paths")
    path_p = sub.add_parser("repo-path")
    path_p.add_argument("--machine", required=True)
    storage_p = sub.add_parser("storage-env")
    storage_p.add_argument("--machine", required=True)
    push_env_p = sub.add_parser("push-env")
    push_env_p.add_argument("--machine", required=True)
    push_env_p.add_argument("--dry-run", action="store_true")
    push_env_p.add_argument("--confirm", action="store_true")
    push_env_p.add_argument("--overwrite", action="store_true")
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
    activity_p = sub.add_parser("activity")
    activity_p.add_argument("--machines", required=True)
    prune_p = sub.add_parser("prune")
    prune_p.add_argument("--machines", required=True)
    prune_p.add_argument("--waves", required=True)
    prune_p.add_argument("--dry-run", action="store_true")
    prune_p.add_argument("--confirm", action="store_true")
    prepare_p = sub.add_parser("prepare")
    prepare_p.add_argument("--machine", required=True)
    prepare_p.add_argument("--dry-run", action="store_true")
    prepare_p.add_argument("--confirm", action="store_true")
    offload_p = sub.add_parser("offload")
    offload_p.add_argument("--machine", required=True)
    offload_p.add_argument("--wave", required=True)
    offload_p.add_argument("--dry-run", action="store_true")
    offload_p.add_argument("--confirm", action="store_true")
    restore_p = sub.add_parser("restore")
    restore_p.add_argument("--machine", required=True)
    restore_p.add_argument("--wave", required=True)
    restore_p.add_argument("--run", required=True)
    restore_p.add_argument("--dry-run", action="store_true")
    restore_p.add_argument("--confirm", action="store_true")
    cache_p = sub.add_parser("sync-cache")
    cache_p.add_argument("--to", dest="machine", required=True)
    cache_p.add_argument("--var", required=True)
    cache_p.add_argument("--subpath", required=True)
    cache_p.add_argument("--dry-run", action="store_true")
    cache_p.add_argument("--confirm", action="store_true")
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
        elif args.command == "check-paths":
            check_paths(config, config_path, registry_path)
        elif args.command in {"repo-path", "storage-env", "push-env"}:
            if args.machine not in config.machines:
                raise RigSyncError(f"unknown machine: {args.machine}")
            machine = config.machines[args.machine]
            if args.command == "repo-path":
                print_repo_path(machine)
            elif args.command == "storage-env":
                print_storage_env(machine)
            else:
                push_env(config, machine, args.dry_run, args.confirm, args.overwrite)
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
        elif args.command == "activity":
            activity(config, selected_machines(config, args.machines))
        elif args.command == "prune":
            require_git(config)
            prune(
                config,
                selected_machines(config, args.machines),
                [part for part in args.waves.split(",") if part],
                args.dry_run,
                args.confirm,
            )
        elif args.command in {"offload", "restore", "sync-cache"}:
            if args.machine not in config.machines:
                raise RigSyncError(f"unknown machine: {args.machine}")
            machine = config.machines[args.machine]
            if args.command == "offload":
                offload(config, machine, args.wave, args.dry_run, args.confirm)
            elif args.command == "restore":
                restore(config, machine, args.wave, args.run, args.dry_run, args.confirm)
            else:
                sync_cache(config, machine, args.var, args.subpath, args.dry_run, args.confirm)
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
