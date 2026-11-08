#!/usr/bin/env python3
"""Render a wave's scripts and supervisor manifest from one JSON input.

`scripts` writes every run's wave.sh and README.md and the wave's lane.sh; run it before the
dispatch commit. `manifest` writes the supervisor manifest from the same input; run it after the
wave tag exists, because the manifest names the tag's commit.

The script bodies are read from references/templates.md and references/cineca-slurm.md, so those
stay the single statement of what a wave script does. Input format: references/templates.md.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import shlex
import subprocess
import sys
import tomllib
from pathlib import Path, PurePosixPath


class RenderError(RuntimeError):
    pass


REFERENCES = Path(__file__).resolve().parents[1] / "references"
WAVE_RE = re.compile(r"^\d{8}-\d{6}$")
EXPERIMENT_RE = re.compile(r"^\d{3}_[A-Za-z0-9._-]+$")
HASHED_RUN_RE = re.compile(r"^rid-[0-9a-f]{32}$")
FINGERPRINT_RE = re.compile(r"^[0-9a-f]{64}$")
ENVIRONMENT_DIR_RE = re.compile(r"^\.envs/[0-9a-f]{16}$")
# Paths reach bash in double quotes and tmux lines: plain characters only, so none needs quoting.
PLAIN_PATH_RE = re.compile(r"^[A-Za-z0-9._/=+,:@-]+$")
PLACEHOLDER_RE = re.compile(r"<[A-Za-z0-9\"'_][^<>\n]*>")
LEGACY_LAYOUTS = ("segments-v2", "segments-v1", "nested", "collapsed-v1", "hashed-v1")
HASHED = "hashed-v2"
SCRATCH = "shitpads"
SLURM_MAX_WALLTIME_S = 24 * 3600
SLURM_WALLTIME_STEP_S = 15 * 60

# The wording of templates.md's README; a test keeps the two equal.
STANDING_AUTHORIZATION = (
    "Standing authorization (inside this pool, for this wave's own runs): start, move, requeue, and\n"
    "steal runs; resubmit and cancel its own not-yet-started Slurm jobs; fetch data into declared\n"
    "caches; free checksum-verified checkpoints on offload rigs."
)


def _fence(path: Path, heading: str) -> str:
    """The first ```bash block after a `## ` heading of a reference file."""
    text = path.read_text()
    try:
        start = text.index(f"\n{heading}")
        body = text[text.index("```bash\n", start) + len("```bash\n") :]
        return body[: body.index("\n```\n")] + "\n"
    except ValueError as exc:
        raise RenderError(f"{path}: no bash block under {heading!r}") from exc


def _require(table: dict, key: str, kind: type, where: str):
    value = table.get(key)
    if not isinstance(value, kind) or isinstance(value, bool) or value == "":
        raise RenderError(f"{where}: `{key}` must be a non-empty {kind.__name__}")
    return value


def _text(value: object, where: str) -> str:
    """Free text bound for a comment or quoted string; a line break would end the comment."""
    if not isinstance(value, str) or not value or any(ord(char) < 32 for char in value):
        raise RenderError(f"{where} must be one non-empty line of text")
    return value


def _path(value: object, where: str) -> str:
    if not isinstance(value, str) or not PLAIN_PATH_RE.fullmatch(value):
        raise RenderError(f"{where} must be a relative path of plain characters, got {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise RenderError(f"{where} must stay inside the project, got {value!r}")
    return value


def _double_quoted(value: str) -> str:
    return re.sub(r'([\\"$`])', r"\\\1", value)


def _token(value: object, where: str) -> str:
    """One array token exactly as `hydra_override_arg` returned it: shell-safe and `param=value`."""
    if not isinstance(value, str):
        raise RenderError(f"{where} must be a string from hydra_override_arg")
    try:
        parts = shlex.split(value)
    except ValueError as exc:
        raise RenderError(f"{where}: {value!r} is not shell-safe: {exc}") from exc
    if len(parts) != 1 or "=" not in parts[0] or shlex.quote(parts[0]) != value:
        raise RenderError(f"{where}: {value!r} is not the output of hydra_override_arg")
    return value


def slurm_rigs(wave: dict) -> list[str]:
    return [rig for rig, entry in wave["pool"].items() if isinstance(entry, dict) and "max_jobs" in entry]


def load_input(path: Path) -> dict:
    try:
        wave = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise RenderError(f"cannot read {path}: {exc}") from exc
    if not isinstance(wave, dict):
        raise RenderError(f"{path}: expected one JSON object")
    wave_id = _require(wave, "wave_id", str, "input")
    if not WAVE_RE.fullmatch(wave_id):
        raise RenderError(f"input: invalid wave id {wave_id!r}")
    experiment = _require(wave, "experiment", str, "input")
    if not EXPERIMENT_RE.fullmatch(experiment):
        raise RenderError(f"input: invalid experiment {experiment!r}; expected NNN_name")
    for key in ("purpose", "why", "hub"):
        _text(wave.get(key), f"input: `{key}`")
    _path(wave.get("entrypoint"), "input: `entrypoint`")
    if not FINGERPRINT_RE.fullmatch(_require(wave, "environment_fingerprint", str, "input")):
        raise RenderError("input: `environment_fingerprint` must be the 64-hex value `verify --staged` printed")
    if not ENVIRONMENT_DIR_RE.fullmatch(_require(wave, "environment_dir", str, "input")):
        raise RenderError("input: `environment_dir` must be the `.envs/<env_key>` value `verify --staged` printed")
    layout = wave.setdefault("run_id_layout", HASHED)
    if layout not in (HASHED, *LEGACY_LAYOUTS):
        raise RenderError(f"input: unknown run_id_layout {layout!r}")
    if layout == HASHED:
        _require(wave, "identity_version", int, "input")
    if layout.startswith("segments-"):
        _text(wave.get("run_id_segments"), "input: `run_id_segments`")
    if not _require(wave, "pool", dict, "input"):
        raise RenderError("input: `pool` names no rig")
    for rig in slurm_rigs(wave):
        _text(wave.get("slurm_account"), f"input: `slurm_account` (pool rig {rig} runs Slurm jobs)")
        _require(wave["pool"][rig], "default_walltime_s", int, f"input: pool.{rig}")
    grants = wave.get("gpu_grants") or {}
    if not isinstance(grants, dict):
        raise RenderError("input: `gpu_grants` must be an object of rig -> grant")
    for rig, grant in grants.items():
        spot = f"input: gpu_grants.{rig}"
        gpus = grant.get("gpus") if isinstance(grant, dict) else None
        if not isinstance(gpus, list) or not gpus or not all(
            isinstance(gpu, int) and not isinstance(gpu, bool) and gpu >= 0 for gpu in gpus
        ):
            raise RenderError(f"{spot}.gpus must list GPU indices")
        _text(grant.get("user_words"), f"{spot}.user_words")
        _text(grant.get("date"), f"{spot}.date")
    runs = _require(wave, "runs", list, "input")
    seen: set[str] = set()
    for position, run in enumerate(runs):
        spot = f"input: runs[{position}]"
        if not isinstance(run, dict):
            raise RenderError(f"{spot} must be an object")
        run_id = _require(run, "id", str, spot)
        if run_id in seen:
            raise RenderError(f"{spot}: duplicate run id {run_id!r}")
        seen.add(run_id)
        if layout == HASHED and not HASHED_RUN_RE.fullmatch(run_id):
            raise RenderError(f"{spot}: under {HASHED} `id` is the rid-<32 hex> hash, got {run_id!r}")
        _text(run_id, f"{spot}: `id`")
        _text(run.get("label"), f"{spot}: `label`")
        folder = run.setdefault("folder", run_id) if layout == HASHED else run.get("folder")
        _path(folder, f"{spot}: `folder`")
        for key in ("checkpoint_dir", "eval_dir", "artifact"):
            _path(run.get(key), f"{spot}: `{key}`")
        artifact = PurePosixPath(run["artifact"])
        if not {PurePosixPath(run["checkpoint_dir"]), PurePosixPath(run["eval_dir"])} & set(artifact.parents):
            raise RenderError(f"{spot}: `artifact` must lie under checkpoint_dir or eval_dir")
        footprint = run.get("footprint_kib")
        if not isinstance(footprint, int) or isinstance(footprint, bool) or footprint < 0:
            raise RenderError(f"{spot}: `footprint_kib` must be this run's retained checkpoint footprint in KiB")
        overrides = _require(run, "overrides", list, spot)
        for index, token in enumerate(overrides):
            _token(token, f"{spot}: overrides[{index}]")
    return wave


def gpu_smoke(root: Path) -> list[str]:
    try:
        with (root / "sync.toml").open("rb") as handle:
            argv = tomllib.load(handle).get("environment", {}).get("gpu_smoke")
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise RenderError(f"cannot read {root / 'sync.toml'}: {exc}") from exc
    if not isinstance(argv, list) or len(argv) < 2 or argv[0] != "python":
        raise RenderError(f"{root / 'sync.toml'}: environment.gpu_smoke must be [\"python\", \"<script>\", ...]")
    _path(argv[1], "environment.gpu_smoke[1]")
    return argv[1:]


def behemoth_grant(wave: dict) -> tuple[str, str]:
    """The GPU set the shared rig's guard allows and the audit line recording why."""
    grant = (wave.get("gpu_grants") or {}).get("behemoth")
    if not grant:
        return "0", '"default (gpu0 only)"'
    gpus = sorted({0, *grant["gpus"]})
    granted = ",".join(str(gpu) for gpu in grant["gpus"])
    words = _text(grant["user_words"], "input: gpu_grants.behemoth.user_words")
    return (
        ",".join(str(gpu) for gpu in gpus),
        f"\"user-granted {grant['date']} for wave {wave['wave_id']}: gpu {granted}\" ({words})",
    )


def slurm_time(seconds: int) -> str:
    rounded = math.ceil(seconds / SLURM_WALLTIME_STEP_S) * SLURM_WALLTIME_STEP_S
    bounded = min(rounded, SLURM_MAX_WALLTIME_S)
    return f"{bounded // 3600:02d}:{bounded % 3600 // 60:02d}:00"


def _with_slurm_header(body: str) -> str:
    """Swap the bare opening of the standard body for the job script's header."""
    header = _fence(REFERENCES / "cineca-slurm.md", "## The job script")
    kept = [line for line in header.splitlines() if not line.startswith("# ...then")]
    start = body.index('export WAVE_ID="')
    return "\n".join(kept) + "\n" + body[start:]


def render_wave_script(wave: dict, run: dict, smoke: list[str]) -> str:
    body = _fence(REFERENCES / "templates.md", "## wave.sh")
    if slurm_rigs(wave):
        body = _with_slurm_header(body)
    layout = wave["run_id_layout"]
    experiment, wave_id = wave["experiment"], wave["wave_id"]
    run_id, label, folder = run["id"], run["label"], run["folder"]
    lines = []
    in_behemoth_block = False
    for line in body.splitlines():
        if line.startswith("# ---- behemoth in the pool ONLY"):
            in_behemoth_block = True
        if in_behemoth_block and "behemoth" not in wave["pool"]:
            in_behemoth_block = not line.startswith("# ---- end behemoth block")
            continue
        if line.startswith("# ---- end behemoth block"):
            in_behemoth_block = False
        if line.startswith("# RUN_ID_VERSION:"):
            if layout == HASHED:
                lines.append(f"# RUN_ID_VERSION: {wave['identity_version']}")
            continue
        if line.startswith("# RUN_ID_SEGMENTS:"):
            if layout.startswith("segments-"):
                lines.append(f"# RUN_ID_SEGMENTS: {wave['run_id_segments']}")
            continue
        if line.startswith("# GPU auth:"):
            line = f"# GPU auth: {behemoth_grant(wave)[1]}"
        elif line.startswith("#SBATCH --account="):
            line = f"#SBATCH --account={wave['slurm_account']}"
        elif line.startswith("#SBATCH --time="):
            walltime = next(wave["pool"][rig]["default_walltime_s"] for rig in slurm_rigs(wave))
            line = f"#SBATCH --time={slurm_time(walltime)}"
        lines.append(line)
    text = "\n".join(lines) + "\n"
    smoke_argv = f'"$WAVE_TREE/{smoke[0]}"' + "".join(f" {shlex.quote(token)}" for token in smoke[1:])
    entry = PurePosixPath(wave["entrypoint"])
    exact = (
        # whole quoted strings first, escaped for double quotes; what is left sits in comments
        ('RUN_ID_NAME="<run_id_name: the hash under hashed-v2; the rendered run_id under a segments pin; the flat run_id under another legacy pin>"', f'RUN_ID_NAME="{_double_quoted(run_id)}"'),
        ('RUN_ID_LABEL="<run_id_label under hashed-v2; the flat run_id under a legacy pin>"', f'RUN_ID_LABEL="{_double_quoted(label)}"'),
        ('"<hashed-v2, or a legacy segments-v2 | segments-v1 | nested | collapsed-v1 | hashed-v1, copied from the experiment record>"', f'"{layout}"'),
        ('"<exact checkpoint directory resolved by canonical run_id_path>"', f'"{run["checkpoint_dir"]}"'),
        ('"<exact evaluation directory resolved by canonical run_id_path>"', f'"{run["eval_dir"]}"'),
        ('"<exact expected final artifact path under CHECKPOINT_DIR or EVAL_DIR>"', f'"{run["artifact"]}"'),
        ('"<ENVIRONMENT_DIR printed by environment-sync verify: .envs/<env_key>>"', f'"{wave["environment_dir"]}"'),
        ('"<64-char fingerprint verified on every assigned rig>"', f'"{wave["environment_fingerprint"]}"'),
        ('"<0 | the granted set>"', f'"{behemoth_grant(wave)[0]}"'),
        ("<this run's retained checkpoint footprint in KiB, from experiment-design>", str(run["footprint_kib"])),
        ('("$WAVE_TREE/<gpu_smoke script>" <shell-quoted remaining gpu_smoke tokens>)', f"({smoke_argv})"),
        ("(<tokens produced by hydra_override_arg; one per override>)", "(" + " ".join(run["overrides"]) + ")"),
        ("code/<NNN_exp>/<script>.py", f"code/{experiment}/{entry}"),
        ("<run_id_label under hashed-v2; the flat run_id under a legacy pin>", label),
        ("<run_id folder>", folder),
        ("<run_id_name>", run_id),
        ("<NNN_exp>", experiment),
        ("<wave_id>", wave_id),
    )
    for placeholder, value in exact:
        text = text.replace(placeholder, value)
    leftover = PLACEHOLDER_RE.findall(text)
    if leftover:
        raise RenderError(f"the wave.sh template has placeholders this renderer does not fill: {leftover}")
    return text


def render_lane_script(wave: dict) -> str:
    text = _fence(REFERENCES / "templates.md", "## lane.sh").replace("<wave_id>", wave["wave_id"])
    leftover = PLACEHOLDER_RE.findall(text)
    if leftover:
        raise RenderError(f"the lane.sh template has placeholders this renderer does not fill: {leftover}")
    return text


def _pool_line(wave: dict) -> str:
    parts = []
    for rig, entry in wave["pool"].items():
        lanes = entry.get("lanes") if isinstance(entry, dict) else None
        parts.append(f"{rig} ({', '.join(f'gpu {lane}' for lane in lanes)})" if lanes else f"{rig} (Slurm jobs)")
    return ", ".join(parts)


def offload_rigs(wave: dict) -> list[str]:
    """Every non-hub lane rig unless the input lists its own (conventions: a short rig stays busy)."""
    if "offload" in wave:
        return list(wave["offload"])
    return [rig for rig in wave["pool"] if rig != wave["hub"] and rig not in slurm_rigs(wave)]


def _slurm_readme_line(wave: dict) -> str:
    """The extra README fields cineca-slurm.md lists for a wave with jobs."""
    header = _fence(REFERENCES / "cineca-slurm.md", "## The job script")
    partition = re.search(r"--partition=(\S+)", header).group(1)
    qos = re.search(r"--qos=(\S+)", header).group(1)
    walltime_s = next(wave["pool"][rig]["default_walltime_s"] for rig in slurm_rigs(wave))
    core_hours = math.ceil(len(wave["runs"]) * walltime_s / 3600 * 8)
    return (
        f"Slurm: account `{wave['slurm_account']}`, partition `{partition}`, QOS `{qos}`, one GPU, 8 cores, 128 GB "
        f"per job; fallback walltime {slurm_time(walltime_s)} from `default_walltime_s`, resized by the supervisor "
        f"once it has a basis; budget estimate runs × walltime_h × 8 = {core_hours} core-hours."
    )


def render_readme(wave: dict, run: dict) -> str:
    wave_id = wave["wave_id"]
    dispatched = f"{wave_id[:4]}-{wave_id[4:6]}-{wave_id[6:8]} {wave_id[9:11]}:{wave_id[11:13]}"
    layout = wave["run_id_layout"]
    identity = f", identity version {wave['identity_version']}" if layout == HASHED else ""
    lines = [
        f"# wave {wave_id} — {wave['purpose']}",
        "",
        f"Dispatched {dispatched} from {wave['hub']}.",
        "",
        f"Source tag: `wave--{wave_id}` (the annotated tag and configured branch must resolve to the",
        "same commit on every assigned rig).",
        "",
        f"Environment fingerprint: `{wave['environment_fingerprint']}` (verified with the project GPU smoke on every",
        "assigned lane before launch).",
        "",
        f"Why this wave: {wave['why']}",
        "",
        f"This run: `{run['id']}` → claimed by the first free lane of the pool.",
        f"Label: `{run['label']}`.",
        "",
        f"Run path layout: `{layout}`{identity}.",
        "",
        f"Checkpoint directory: `{run['checkpoint_dir']}`.",
        f"Evaluation directory: `{run['eval_dir']}`.",
        f"Status path: `{run['eval_dir']}/.status.json`.",
        f"Expected final artifact: `{run['artifact']}`.",
        "",
        f"Pool: {_pool_line(wave)}; offload: {', '.join(offload_rigs(wave)) or 'none'}. Full wave: {len(wave['runs'])} runs.",
    ]
    for rig, grant in (wave.get("gpu_grants") or {}).items():
        gpus = ", ".join(f"gpu {gpu}" for gpu in grant["gpus"])
        lines.append(f"Grant on {rig}: {gpus}, user-authorized {grant['date']} for this wave only: \"{grant['user_words']}\".")
    if slurm_rigs(wave):
        lines.append(_slurm_readme_line(wave))
    lines += ["", STANDING_AUTHORIZATION, ""]
    return "\n".join(lines)


def wave_files(wave: dict, root: Path) -> dict[Path, str]:
    smoke = gpu_smoke(root)
    files = {root / lane_script(wave): render_lane_script(wave)}
    for run in wave["runs"]:
        script = root / run_script(wave, run)
        files[script] = render_wave_script(wave, run, smoke)
        files[script.with_name("README.md")] = render_readme(wave, run)
    return files


def lane_script(wave: dict) -> str:
    return f"scripts/{wave['experiment']}/_lanes/wave_{wave['wave_id']}/lane.sh"


def run_script(wave: dict, run: dict) -> str:
    return f"scripts/{wave['experiment']}/{run['folder']}/wave_{wave['wave_id']}/wave.sh"


def write_scripts(wave: dict, root: Path, dry_run: bool) -> None:
    files = wave_files(wave, root)
    # A wave file is written once; changing a generated one means a new wave id.
    conflicts = [path for path, text in files.items() if path.exists() and path.read_text() != text]
    if conflicts:
        names = ", ".join(str(path.relative_to(root)) for path in conflicts[:5])
        raise RenderError(f"refusing to overwrite wave files that differ: {names}")
    for path, text in files.items():
        print(f"[{'dry-run' if dry_run else 'write'}] {path.relative_to(root)}")
        if not dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)


def tag_revision(root: Path, wave_id: str) -> str:
    tag = f"refs/tags/wave--{wave_id}"
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--verify", "-q", f"{tag}^{{commit}}"], capture_output=True, text=True
    )
    if result.returncode:
        raise RenderError(f"{tag} does not exist in {root}; commit and tag the wave before its manifest")
    return result.stdout.strip()


def build_manifest(wave: dict, revision: str) -> dict:
    runs = []
    for run in wave["runs"]:
        entry = {
            "id": run["id"],
            "label": run["label"],
            "script": run_script(wave, run),
            "status_path": f"{run['eval_dir']}/.status.json",
            "artifact": run["artifact"],
            "checkpoint_dir": run["checkpoint_dir"],
            "eval_dir": run["eval_dir"],
            "resumes": bool(run.get("resumes", False)),
            "needs": list(run.get("needs", [])),
        }
        for key in ("cost_s", "min_vram_mib"):
            if run.get(key) is not None:
                entry[key] = run[key]
        runs.append(entry)
    # Longest first; a run with no estimate keeps its place after them.
    runs.sort(key=lambda entry: -entry.get("cost_s", 0))
    manifest = {
        "wave_id": wave["wave_id"],
        "experiment": wave["experiment"],
        "revision": revision,
        "lane_script": lane_script(wave),
        "pool": wave["pool"],
        "weights": wave.get("weights", {}),
        "offload": offload_rigs(wave),
        "fix_depth": wave.get("fix_depth", 0),
        "runs": runs,
    }
    for key in ("gpu_grants", "fixes_wave"):
        if wave.get(key):
            manifest[key] = wave[key]
    return manifest


def write_manifest(wave: dict, root: Path, output: Path | None) -> Path:
    manifest = build_manifest(wave, tag_revision(root, wave["wave_id"]))
    path = output or root / SCRATCH / f"wave_{wave['wave_id']}" / "manifest.json"
    if (root / "scripts") in path.resolve().parents:
        raise RenderError("the manifest is launch input, not an execution file: never under scripts/")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"[write] {path}")
    return path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("scripts", "manifest"):
        command = sub.add_parser(name)
        command.add_argument("--input", type=Path, required=True)
        command.add_argument("--root", type=Path, default=Path.cwd())
    sub.choices["scripts"].add_argument("--dry-run", action="store_true")
    sub.choices["manifest"].add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        wave = load_input(args.input)
        root = args.root.resolve()
        if args.command == "scripts":
            write_scripts(wave, root, args.dry_run)
        else:
            write_manifest(wave, root, args.output)
    except RenderError as exc:
        print(f"render_wave: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
