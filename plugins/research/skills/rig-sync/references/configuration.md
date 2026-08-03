# Configuration

## Project `sync.toml`

Keep artifact definitions and per-project locations in the project root:

```toml
version = 1

[git]
remote = "origin"
branch = "main"

[environment]
manager = "uv"
gpu_smoke = ["python", "code/common/environment_smoke.py"]

[artifacts.checkpoints]
path = "checkpoints"
depth = 2

[artifacts.evaluations]
path = "evaluations"
depth = 2

[artifacts.plots]
path = "plots"
depth = 2

[artifacts.references]
path = "references"
depth = 1

[machines.rig-4090]
repo_path = "/absolute/path/on/rig-4090"

[machines.rig-3090-ti]
repo_path = "/absolute/path/on/rig-3090-ti"

[machines.behemoth]
repo_path = "/absolute/path/on/behemoth"
```

`repo_path` must be absolute. Machine keys must match the canonical names used by dispatch.
`git.remote` names the GitHub remote already configured on the hub; every rig must expose the
same URL under that name. `git.branch` is the branch that dispatch commits are pushed to and that
rigs may fast-forward. Revision deployment stops rather than switching branches or rewriting a
working tree.

`[environment]` is owned by `$environment-sync`; it lives here so the same project and machine
selection drive both revision and runtime parity. `rigsync.py` preserves but does not execute it.

## User registry

Keep SSH identity in `~/.config/rigsync/machines.toml`:

```toml
[machines.rig-4090]
ssh = "rig-4090"
hostname = "rig-4090"

[machines.rig-3090-ti]
ssh = "rig-3090-ti"
hostname = "rig-3090-ti"

[machines.behemoth]
ssh = "behemoth"
hostname = "behemoth"
```

The `ssh` value is an alias from `~/.ssh/config`; ports, users, and keys remain there. Preserve
legacy aliases that other projects may still reference. When `hostname` is present, `doctor`
compares it exactly with the machine's `hostname` command and stops on a mismatch. Omit the field
only when hostname identity is intentionally unmanaged.

## Environment overrides

- `RIGSYNC_CONFIG` changes the project config path; default: `./sync.toml`.
- `RIGSYNC_REGISTRY` changes the user registry path; default:
  `~/.config/rigsync/machines.toml`.
