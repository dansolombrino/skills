#!/usr/bin/env python3
"""Provision and verify one uv environment across configured research rigs."""

from __future__ import annotations

import argparse
import importlib.util
import os
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path


def _load_rigsync():
    path = Path(__file__).resolve().parents[2] / "rig-sync" / "scripts" / "rigsync.py"
    spec = importlib.util.spec_from_file_location("environment_sync_rigsync", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load rig-sync helper: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


rigsync = _load_rigsync()


class EnvironmentSyncError(RuntimeError):
    pass


EXACT_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
REVISION_RE = re.compile(r"^[0-9a-f]{40}$")
FINGERPRINT_RE = re.compile(r"^[0-9a-f]{64}$")
GPU_SET_RE = re.compile(r"^\d+(?:,\d+)*$")
URL_CREDENTIAL_RE = re.compile(r"([a-z][a-z0-9+.-]*://)([^\s/@:]+):([^\s/@]+)@", re.I)
SECRET_PARAMETER_RE = re.compile(
    r"(?i)(token|password|passwd|secret|api[_-]?key)(=|%3[dD])([^&\s]+)"
)


@dataclass(frozen=True)
class EnvironmentSettings:
    root: Path
    uv_version: str
    python_version: str
    gpu_smoke: tuple[str, ...]

    @property
    def uv_path(self) -> Path:
        return self.root / ".rigsync_cache" / "tools" / "uv" / self.uv_version / "uv"


@dataclass(frozen=True)
class HostFacts:
    system: str
    architecture: str
    libc: str
    gpus: tuple[str, ...]


@dataclass(frozen=True)
class Lane:
    machine: str
    gpus: str


def redact(value: str) -> str:
    value = URL_CREDENTIAL_RE.sub(r"\1***:***@", value)
    return SECRET_PARAMETER_RE.sub(r"\1\2***", value)


def _load_toml(path: Path) -> dict:
    try:
        with path.open("rb") as handle:
            value = tomllib.load(handle)
    except FileNotFoundError as exc:
        raise EnvironmentSyncError(f"missing contract file: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise EnvironmentSyncError(f"invalid TOML in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise EnvironmentSyncError(f"invalid TOML root: {path}")
    return value


def load_environment_settings(root: Path, config_path: Path) -> EnvironmentSettings:
    project_config = _load_toml(config_path)
    raw_environment = project_config.get("environment")
    if not isinstance(raw_environment, dict):
        raise EnvironmentSyncError(f"{config_path}: missing [environment]")
    if raw_environment.get("manager") != "uv":
        raise EnvironmentSyncError(f'{config_path}: environment.manager must be "uv"')
    raw_smoke = raw_environment.get("gpu_smoke")
    if (
        not isinstance(raw_smoke, list)
        or not raw_smoke
        or not all(isinstance(token, str) and token for token in raw_smoke)
        or raw_smoke[0] != "python"
    ):
        raise EnvironmentSyncError(
            f'{config_path}: environment.gpu_smoke must be a non-empty argv array starting with "python"'
        )

    pyproject_path = root / "pyproject.toml"
    pyproject = _load_toml(pyproject_path)
    raw_tool = pyproject.get("tool", {})
    if not isinstance(raw_tool, dict):
        raise EnvironmentSyncError(f"{pyproject_path}: [tool] must be a table")
    raw_uv = raw_tool.get("uv", {})
    if not isinstance(raw_uv, dict):
        raise EnvironmentSyncError(f"{pyproject_path}: missing [tool.uv]")
    required_uv = raw_uv.get("required-version")
    if not isinstance(required_uv, str) or not required_uv.startswith("=="):
        raise EnvironmentSyncError(
            f'{pyproject_path}: tool.uv.required-version must be an exact "==X.Y.Z" pin'
        )
    uv_version = required_uv[2:]
    if not EXACT_VERSION_RE.fullmatch(uv_version):
        raise EnvironmentSyncError(
            f'{pyproject_path}: tool.uv.required-version must be an exact "==X.Y.Z" pin'
        )

    python_path = root / ".python-version"
    try:
        python_version = python_path.read_text().strip()
    except FileNotFoundError as exc:
        raise EnvironmentSyncError(f"missing contract file: {python_path}") from exc
    if not EXACT_VERSION_RE.fullmatch(python_version):
        raise EnvironmentSyncError(f"{python_path}: expected one exact X.Y.Z Python version")
    for required in (root / "uv.lock", root / "code" / "common" / "environment.py"):
        if not required.is_file():
            raise EnvironmentSyncError(f"missing contract file: {required}")
    return EnvironmentSettings(root, uv_version, python_version, tuple(raw_smoke))


def parse_lanes(values: list[str] | None) -> list[Lane]:
    lanes: list[Lane] = []
    for raw in values or []:
        name, separator, gpus = raw.partition(":")
        if not separator or not name or not GPU_SET_RE.fullmatch(gpus):
            raise EnvironmentSyncError(f"invalid lane {raw!r}; expected <rig>:<gpu_ids>")
        ids = gpus.split(",")
        if len(ids) != len(set(ids)):
            raise EnvironmentSyncError(f"invalid lane {raw!r}; GPU ids must be unique")
        lanes.append(Lane(name, gpus))
    return lanes


def remote(machine, argv: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    return rigsync.remote(machine, argv, check=check)


def remote_text(machine, argv: list[str], *, check: bool = True) -> str:
    result = remote(machine, argv, check=check)
    return result.stdout.decode(errors="replace").strip()


def machine_path(settings: EnvironmentSettings, machine, relative: Path) -> Path:
    return machine.repo_path / relative


def uv_path(settings: EnvironmentSettings, machine) -> Path:
    relative = settings.uv_path.relative_to(settings.root)
    return machine_path(settings, machine, relative)


def venv_python(machine) -> Path:
    return machine.repo_path / ".venv" / "bin" / "python"


def host_facts(machine) -> HostFacts:
    system = remote_text(machine, ["uname", "-s"])
    architecture = remote_text(machine, ["uname", "-m"])
    libc_result = remote(machine, ["getconf", "GNU_LIBC_VERSION"], check=False)
    libc = (
        libc_result.stdout.decode(errors="replace").strip()
        if libc_result.returncode == 0
        else "unavailable"
    )
    gpu_result = remote(
        machine,
        ["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"],
        check=False,
    )
    if gpu_result.returncode:
        stderr = redact(gpu_result.stderr.decode(errors="replace").strip())
        raise EnvironmentSyncError(
            f"{machine.name}: nvidia-smi unavailable: {stderr or 'command failed'}"
        )
    gpus = tuple(
        line.strip()
        for line in gpu_result.stdout.decode(errors="replace").splitlines()
        if line.strip()
    )
    if not gpus:
        raise EnvironmentSyncError(f"{machine.name}: nvidia-smi reported no GPUs")
    return HostFacts(system, architecture, libc, gpus)


def uv_version(machine, executable: Path) -> str | None:
    exists = remote(machine, ["test", "-x", str(executable)], check=False)
    if exists.returncode:
        return None
    result = remote(machine, [str(executable), "--version"], check=False)
    if result.returncode:
        return None
    output = result.stdout.decode(errors="replace").strip()
    match = re.search(r"\b(\d+\.\d+\.\d+)\b", output)
    return match.group(1) if match else None


def require_revision(revision: str) -> None:
    if not REVISION_RE.fullmatch(revision):
        raise EnvironmentSyncError("revision must be a full lowercase 40-character commit SHA")


def verify_source(config, machine, revision: str) -> None:
    try:
        head, _branch, _remote_url = rigsync.git_repo_facts(config, machine)
        pathspecs = [
            ".python-version",
            "pyproject.toml",
            "uv.toml",
            "uv.lock",
            "sync.toml",
            "code/common/environment.py",
        ]
        drift = rigsync.execution_drift(machine, pathspecs)
    except rigsync.RigSyncError as exc:
        raise EnvironmentSyncError(str(exc)) from exc
    if head != revision:
        raise EnvironmentSyncError(f"{machine.name}: HEAD={head}, expected {revision}")
    if drift:
        raise EnvironmentSyncError(
            f"{machine.name}: environment contract dirty: " + ", ".join(drift[:8])
        )


def install_uv(settings: EnvironmentSettings, machine) -> None:
    target = uv_path(settings, machine).parent
    url = f"https://astral.sh/uv/{settings.uv_version}/install.sh"
    script = (
        'set -eu\n'
        'installer=$(mktemp)\n'
        'trap \'rm -f "$installer"\' EXIT HUP INT TERM\n'
        'curl --proto "=https" --tlsv1.2 -fLsS "$1" -o "$installer"\n'
        'mkdir -p "$2"\n'
        'env UV_UNMANAGED_INSTALL="$2" UV_NO_MODIFY_PATH=1 sh "$installer"\n'
    )
    remote(machine, ["sh", "-c", script, "sh", url, str(target)])
    observed = uv_version(machine, uv_path(settings, machine))
    if observed != settings.uv_version:
        raise EnvironmentSyncError(
            f"{machine.name}: installed uv {observed or 'unavailable'}, expected {settings.uv_version}"
        )


def sync_command(settings: EnvironmentSettings, machine, *, dry_run: bool) -> list[str]:
    command = [
        "env",
        "UV_NO_MODIFY_PATH=1",
        str(uv_path(settings, machine)),
        "sync",
        "--project",
        str(machine.repo_path),
        "--frozen",
        "--exact",
        "--managed-python",
        "--python",
        settings.python_version,
    ]
    if dry_run:
        command.append("--dry-run")
    return command


def environment_fingerprint(settings: EnvironmentSettings, machine) -> str:
    helper = machine.repo_path / "code" / "common" / "environment.py"
    lock = machine.repo_path / "uv.lock"
    exists = remote(machine, ["test", "-x", str(venv_python(machine))], check=False)
    if exists.returncode:
        raise EnvironmentSyncError(f"{machine.name}: missing project environment .venv")
    result = remote(
        machine,
        [str(venv_python(machine)), str(helper), "fingerprint", "--lock", str(lock)],
        check=False,
    )
    if result.returncode:
        stderr = redact(result.stderr.decode(errors="replace").strip())
        raise EnvironmentSyncError(
            f"{machine.name}: cannot fingerprint environment: {stderr or 'command failed'}"
        )
    value = result.stdout.decode(errors="replace").strip()
    if not FINGERPRINT_RE.fullmatch(value):
        raise EnvironmentSyncError(f"{machine.name}: invalid environment fingerprint {value!r}")
    return value


def lock_check(settings: EnvironmentSettings, machine) -> None:
    result = remote(
        machine,
        [
            str(uv_path(settings, machine)),
            "lock",
            "--check",
            "--project",
            str(machine.repo_path),
        ],
        check=False,
    )
    if result.returncode:
        stderr = redact(result.stderr.decode(errors="replace").strip())
        raise EnvironmentSyncError(
            f"{machine.name}: uv.lock is stale or invalid: {stderr or 'uv lock --check failed'}"
        )


def local_hub(config):
    for machine in config.machines.values():
        if machine.local and machine.repo_path.resolve() == config.root.resolve():
            return machine
    raise EnvironmentSyncError("run environment-sync from the configured local hub project root")


def doctor(settings: EnvironmentSettings, config, machines: list) -> None:
    try:
        rigsync.doctor(config, machines)
    except rigsync.RigSyncError as exc:
        raise EnvironmentSyncError(str(exc)) from exc
    hub = local_hub(config)
    hub_facts = host_facts(hub)
    failures: list[str] = []
    for machine in machines:
        try:
            required = remote(
                machine,
                [
                    "sh",
                    "-c",
                    "command -v curl >/dev/null && command -v git >/dev/null && "
                    "command -v tmux >/dev/null && test -d \"$1\"",
                    "sh",
                    str(machine.repo_path),
                ],
                check=False,
            )
            if required.returncode:
                raise EnvironmentSyncError("missing curl/git/tmux or project root")
            facts = host_facts(machine)
            if facts.system != hub_facts.system or facts.architecture != hub_facts.architecture:
                raise EnvironmentSyncError(
                    f"host={facts.system}/{facts.architecture}, expected {hub_facts.system}/{hub_facts.architecture}"
                )
            observed_uv = uv_version(machine, uv_path(settings, machine))
            uv_state = "ready" if observed_uv == settings.uv_version else "needs provision"
            print(
                f"OK {machine.name}: host={facts.system}/{facts.architecture} libc={facts.libc}; "
                f"gpus={len(facts.gpus)}; uv={observed_uv or 'missing'} ({uv_state})"
            )
        except (EnvironmentSyncError, rigsync.RigSyncError) as exc:
            failures.append(f"{machine.name}: {exc}")
            print(f"FAIL {machine.name}: {exc}")
    if failures:
        raise EnvironmentSyncError(f"doctor failed on {len(failures)} machine(s)")


def provision(
    settings: EnvironmentSettings,
    config,
    machines: list,
    revision: str,
    *,
    dry_run: bool,
    confirmed: bool,
) -> None:
    require_revision(revision)
    if not dry_run and not confirmed:
        raise EnvironmentSyncError("refusing write without --confirm; run --dry-run and obtain approval first")
    for machine in machines:
        verify_source(config, machine, revision)
        observed_uv = uv_version(machine, uv_path(settings, machine))
        if dry_run:
            print(
                f"[dry-run] {machine.name}: uv {settings.uv_version}, Python {settings.python_version}, "
                f"environment {machine.repo_path / '.venv'}"
            )
            if observed_uv != settings.uv_version:
                print(
                    f"[dry-run] {machine.name}: install uv {settings.uv_version} from "
                    f"https://astral.sh/uv/{settings.uv_version}/install.sh"
                )
                continue
            result = remote(machine, sync_command(settings, machine, dry_run=True), check=False)
            if result.stdout:
                print(redact(result.stdout.decode(errors="replace")), end="")
            if result.stderr:
                print(redact(result.stderr.decode(errors="replace")), end="", file=sys.stderr)
            if result.returncode:
                raise EnvironmentSyncError(f"{machine.name}: uv sync dry-run failed")
            continue

        try:
            activity = rigsync.project_activity(config, machine)
        except rigsync.RigSyncError as exc:
            raise EnvironmentSyncError(str(exc)) from exc
        if activity:
            raise EnvironmentSyncError(
                f"{machine.name}: active work blocks environment mutation: " + ", ".join(activity[:8])
            )
        if observed_uv != settings.uv_version:
            install_uv(settings, machine)
        remote(
            machine,
            [
                "env",
                "UV_NO_MODIFY_PATH=1",
                str(uv_path(settings, machine)),
                "python",
                "install",
                "--managed-python",
                settings.python_version,
            ],
        )
        sync_result = remote(
            machine, sync_command(settings, machine, dry_run=False), check=False
        )
        if sync_result.returncode:
            stderr = redact(sync_result.stderr.decode(errors="replace").strip())
            raise EnvironmentSyncError(
                f"{machine.name}: uv sync failed: {stderr or 'command failed'}"
            )
        print(f"OK {machine.name}: provisioned exact environment")
    if not dry_run:
        verify_environments(settings, config, machines, revision, [])


def run_gpu_smoke(settings: EnvironmentSettings, machine, lane: Lane) -> None:
    command = [
        "env",
        f"CUDA_VISIBLE_DEVICES={lane.gpus}",
        str(venv_python(machine)),
        *settings.gpu_smoke[1:],
    ]
    result = remote(machine, command, check=False)
    if result.returncode:
        stderr = redact(result.stderr.decode(errors="replace").strip())
        raise EnvironmentSyncError(
            f"{machine.name}: GPU smoke failed on {lane.gpus}: {stderr or 'command failed'}"
        )
    print(f"OK {machine.name}: GPU smoke passed on {lane.gpus}")


def verify_environments(
    settings: EnvironmentSettings,
    config,
    machines: list,
    revision: str,
    lanes: list[Lane],
) -> str:
    require_revision(revision)
    hub = local_hub(config)
    all_machines = {machine.name: machine for machine in machines}
    all_machines[hub.name] = hub
    for lane in lanes:
        if lane.machine not in config.machines:
            raise EnvironmentSyncError(f"unknown machine in lane: {lane.machine}")
        all_machines[lane.machine] = config.machines[lane.machine]

    hub_facts = host_facts(hub)
    fingerprints: dict[str, str] = {}
    for machine in all_machines.values():
        verify_source(config, machine, revision)
        facts = host_facts(machine)
        if facts.system != hub_facts.system or facts.architecture != hub_facts.architecture:
            raise EnvironmentSyncError(
                f"{machine.name}: host={facts.system}/{facts.architecture}, "
                f"expected {hub_facts.system}/{hub_facts.architecture}"
            )
        observed_uv = uv_version(machine, uv_path(settings, machine))
        if observed_uv != settings.uv_version:
            raise EnvironmentSyncError(
                f"{machine.name}: uv={observed_uv or 'missing'}, expected {settings.uv_version}"
            )
        lock_check(settings, machine)
        fingerprints[machine.name] = environment_fingerprint(settings, machine)

    expected = fingerprints[hub.name]
    mismatched = {
        name: value for name, value in fingerprints.items() if value != expected
    }
    if mismatched:
        detail = ", ".join(f"{name}={value}" for name, value in mismatched.items())
        raise EnvironmentSyncError(
            f"environment fingerprint mismatch; hub {hub.name}={expected}; {detail}"
        )
    for machine in machines:
        print(f"OK {machine.name} environment: {expected}")
    for lane in lanes:
        run_gpu_smoke(settings, config.machines[lane.machine], lane)
    print(f"ENVIRONMENT_FINGERPRINT={expected}")
    return expected


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--registry", type=Path, default=None)
    sub = parser.add_subparsers(dest="command", required=True)
    doctor_p = sub.add_parser("doctor")
    doctor_p.add_argument("--machines", required=True)
    provision_p = sub.add_parser("provision")
    provision_p.add_argument("--revision", required=True)
    provision_p.add_argument("--machines", required=True)
    provision_p.add_argument("--dry-run", action="store_true")
    provision_p.add_argument("--confirm", action="store_true")
    verify_p = sub.add_parser("verify")
    verify_p.add_argument("--revision", required=True)
    verify_p.add_argument("--machines")
    verify_p.add_argument("--lane", action="append")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = args.root.resolve()
    config_path = (
        args.config or Path(os.environ.get("RIGSYNC_CONFIG", root / "sync.toml"))
    ).resolve()
    registry_path = (
        args.registry
        or Path(os.environ.get("RIGSYNC_REGISTRY", "~/.config/rigsync/machines.toml")).expanduser()
    ).resolve()
    try:
        settings = load_environment_settings(root, config_path)
        config = rigsync.load_config(root, config_path, registry_path)
        if args.command == "doctor":
            doctor(settings, config, rigsync.selected_machines(config, args.machines))
        elif args.command == "provision":
            provision(
                settings,
                config,
                rigsync.selected_machines(config, args.machines),
                args.revision,
                dry_run=args.dry_run,
                confirmed=args.confirm,
            )
        else:
            lanes = parse_lanes(args.lane)
            if not args.machines and not lanes:
                raise EnvironmentSyncError("verify requires --machines and/or at least one --lane")
            machines = rigsync.selected_machines(config, args.machines) if args.machines else []
            verify_environments(settings, config, machines, args.revision, lanes)
    except (EnvironmentSyncError, rigsync.RigSyncError) as exc:
        print(f"envsync: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
