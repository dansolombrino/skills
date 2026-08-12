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

`[environment]` is owned by `environment-sync`; it lives here so the same project and machine
selection drive both revision and runtime parity. `rigsync.py` preserves but does not execute it.

## User registry

Keep SSH identity in `~/.config/rigsync/machines.toml`:

```toml
[machines.rig-4090]
ssh = "rig-4090"
hostname = "rig-4090"
storage_root = "/absolute/large-volume/path"

[machines.rig-3090-ti]
ssh = "rig-3090-ti"
hostname = "rig-3090-ti"
storage_root = "/absolute/large-volume/path"

[machines.behemoth]
ssh = "behemoth"
hostname = "behemoth"
storage_root = "/absolute/large-volume/path"
quota_fs = "/dev/disk-or-mount-of-the-small-volume"
min_free_gb = 50
```

The `ssh` value is an alias from `~/.ssh/config`; ports, users, and keys remain there. Preserve
legacy aliases that other projects may still reference. When `hostname` is present, `doctor`
compares it exactly with the machine's `hostname` command and stops on a mismatch. Omit the field
only when hostname identity is intentionally unmanaged.

### Storage fields

These live in the **user registry**, not in a project's `sync.toml`, because where a rig keeps its
large volume is a property of the machine and is the same for every project on it. Keeping them
here also keeps machine-specific absolute paths out of committed project files.

`storage_root` is the volume that project checkouts, artifacts, and caches belong on. When it is
set, `doctor` resolves each project's `repo_path` with `readlink -f` and stops when the result
falls outside the root. Resolving matters: a convenience symlink in `$HOME` can point at the large
volume, so an unresolved string comparison passes while the declaration is still wrong — and stays
wrong the day the symlink is replaced by a real directory.

`quota_fs` names the filesystem to interrogate when the machine enforces **per-user quotas**. A
quota is not free space, and `df` does not see it: `df` can report terabytes available on a volume
where the user's next write fails with `Disk quota exceeded`. When `quota_fs` is present, `doctor`
reads the user's allowance for that filesystem and reports headroom against it; when it is absent,
`doctor` falls back to `df -P <storage_root>`.

`min_free_gb` is the hard floor below which `doctor` fails instead of warning. It defaults to a
conservative value; raise it on rigs that run checkpoint-heavy waves. Sizing it is a judgement
about the *wave*, not the rig — a run writing a 350 MB checkpoint every 30 s consumes headroom
far faster than the number suggests.

Omit all three on rigs with a single volume and no quotas; `doctor` then skips the storage check
rather than inventing a default.

## Environment overrides

- `RIGSYNC_CONFIG` changes the project config path; default: `./sync.toml`.
- `RIGSYNC_REGISTRY` changes the user registry path; default:
  `~/.config/rigsync/machines.toml`.
