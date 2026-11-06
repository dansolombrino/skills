"""Resolve project storage and tracked inputs, for the hub checkout and for wave worktrees.

Two roots exist. The *source root* holds the code being imported: the hub checkout
during development, or a wave's detached worktree (<project>/.waves/<wave_id>) when a
wave runs. The *project root* holds everything that is not source: .env, storage/,
checkpoints/, evaluations/, logs/. A wave script exports RESEARCH_PROJECT_ROOT so the
two resolve differently; without it they coincide.

.env keeps project-scoped storage relative (CACHE_DIR=storage/cache) so that one
file is correct on every rig: rig-sync already requires each rig's repo_path to
sit on that rig's large volume, so anything under the project root inherits that
guarantee without naming a mount point. Absolute values are left alone -- they
are a warned-about exception, not the shape to build against.

Use project_path() for every storage location an experiment reads or writes, and
source_path() for tracked inputs (checked-in tables, prompts, fixtures) so a wave
reads the exact revision it runs. Never build a storage path from os.getcwd() or
Path(__file__): the first silently writes elsewhere when launched from another
directory, the second writes into a wave worktree that is pruned later.

Importing this module loads <project root>/.env into os.environ, so import it first:
a value set there replaces the shell's, and an empty value is treated as unset.
"""

import os
import shutil
from pathlib import Path

_MARKER = "pyproject.toml"
ROOT_ENV = "RESEARCH_PROJECT_ROOT"


def source_root() -> Path:
    """The directory holding pyproject.toml, found by walking up from this file."""
    for candidate in Path(__file__).resolve().parents:
        if (candidate / _MARKER).is_file():
            return candidate
    raise RuntimeError(f"no {_MARKER} above {__file__}: cannot locate the source root")


def project_root() -> Path:
    """The shared project root: $RESEARCH_PROJECT_ROOT inside a wave, else the source root."""
    value = os.environ.get(ROOT_ENV)
    if not value:
        return source_root()
    root = Path(value)
    if not root.is_absolute() or not (root / ".git").exists():
        raise RuntimeError(f"{ROOT_ENV}={value!r} is not an absolute project root with .git")
    return root


def project_path(value: str | os.PathLike) -> Path:
    """An absolute storage path: value as-is when absolute, else under the project root."""
    path = Path(value)
    return path if path.is_absolute() else project_root() / path


def source_path(value: str | os.PathLike) -> Path:
    """A tracked input of the running revision, relative to the source root."""
    path = Path(value)
    if path.is_absolute():
        raise ValueError(f"source_path takes a repo-relative path, got {value!r}")
    return source_root() / path


def storage_path(var: str, default: str) -> Path:
    """Resolve an .env storage variable, falling back to its documented default."""
    return project_path(os.environ.get(var) or default)


def output_root(tree: str, experiment_path: str, *, smoke: bool = False) -> Path:
    """The experiment's directory under evaluations/ or checkpoints/: guard_run_config's experiment_root.

    A smoke run's outputs go to storage/smoke/<tree>/<experiment_path> instead: gitignored, with
    the same run directory layout, and outside everything that scans or syncs real runs
    (check_run_id_plan, RUN_ID_MAP.json, migrations, collect, plotters).
    """
    if tree not in ("evaluations", "checkpoints"):
        raise ValueError(f"output_root takes evaluations or checkpoints, got {tree!r}")
    return project_path(f"storage/smoke/{tree}/{experiment_path}" if smoke else f"{tree}/{experiment_path}")


def reset_smoke(experiment_path: str) -> None:
    """Empty this experiment's smoke outputs, so a repeat smoke never trips guard_run_config."""
    for tree in ("evaluations", "checkpoints"):
        shutil.rmtree(output_root(tree, experiment_path, smoke=True), ignore_errors=True)


def load_env() -> None:
    """Load KEY=VALUE lines from <project root>/.env; no file is fine, a fresh rig has none yet."""
    path = project_root() / ".env"
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.removeprefix("export ").split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        if value:
            os.environ[key.strip()] = value


load_env()
