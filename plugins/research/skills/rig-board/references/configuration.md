# rig-board configuration

## Registry `[board]` table

The board is declared once, in the rigsync user registry `~/.config/rigsync/machines.toml`
(not committed, per user). Every skill discovers the board through it; no path is hard-coded.

```toml
[board]
root   = "/mnt/<big volume>/Resources/rig_board"    # outside every project tree, on the big volume
rigs   = ["rig-4090", "rig-3090-ti", "rig-3080-ti", "behemoth"]  # tmux/GPU machines only
shared = ["behemoth"]                                # cards used by other people show as "foreign"
port   = 8765                                        # viewer
bind   = "0.0.0.0"                                   # all interfaces: LAN and the private overlay
```

- `rigs` must name existing `[machines.<rig>]` entries; each needs `ssh`. The entry whose
  `hostname` matches the current machine is probed locally, without self-SSH.
- Never list a Slurm target. The cluster has jobs, not lanes.
- `root` may be created empty; the script makes `lanes/`, `rigs/` and `history.jsonl`.

## Board layout

```
<root>/
  lanes/<rig>/gpu<ids>.json   # one held lane; absent = free
  rigs/<rig>.json             # last probe: reachable, boot_at, sessions, gpus, foreign
  history.jsonl               # claim / reclaim / refresh / release / adopt / interrupted / reconcile
  .lock                       # advisory lock shared by chats and the viewer
```

Lane file (`schema_version` 1):

```json
{
  "schema_version": 1, "rig": "rig-4090", "gpu": "0",
  "project": "grokking", "experiment": "000_grokking", "wave_id": "20260908-101500",
  "tmux_session": "grokking_000_grokking_20260908-101500_rig-4090_gpu0",
  "hub_project_root": "/mnt/<big volume>/Projects/grokking",
  "claimed_at": "2026-09-08T10:15:32+02:00",
  "runs_total": 6, "runs_done": 2, "active_run": "model=mlp__lr=0.001__seed=1",
  "progress": "epoch 40/100", "eta": "2026-09-08T12:05:00+02:00", "eta_basis": "exact-run history",
  "updated_at": "2026-09-08T11:25:02+02:00",
  "observed": {"at": "2026-09-08T11:26:00+02:00", "session_alive": true, "state": "running", "orchestrator_silent_s": 58}
}
```

`observed.state` is one of `claimed` (never probed yet), `running`, `interrupted` (rig rebooted
after the claim; kept for recovery), `unreachable` (last probe failed; kept as last known).
Claim fields belong to the orchestrator; `observed` belongs to `reconcile`.

## Reconcile rules

For each configured rig, one bounded probe (`tmux ls`, `nvidia-smi`, `uptime -s`; SSH with
`BatchMode=yes`, ten-second connect timeout, run in parallel across rigs):

| board says | rig says | result |
|---|---|---|
| lane held | its session alive | keep; record orchestrator silence (`updated_at` age) |
| lane held | session gone, rig booted after the claim | mark `interrupted`, keep for recovery |
| lane held | session gone, no reboot | release, reason `reconcile: session gone` |
| no lane | live session matching `<project>_<NNN_exp>_<wave>_<rig>_gpu<ids>` | adopt as a lane (no ETA) |
| any | GPU with ≥ 1 GiB used that no lane covers | list under `foreign`; never claimable |
| any | probe failed | keep every lane as last known, flag the rig unreachable |

Session names are parsed from the right (gpu, rig, wave are fixed formats); the remainder splits
at the first `_NNN_` boundary into project and experiment, so a project name must not itself
contain `_<three digits>_`.

## Viewer as a user service on the hub

`assets/rig-board.service` is a systemd user unit template. Install it on the hub:

```bash
mkdir -p ~/.config/systemd/user && cp <skill>/assets/rig-board.service ~/.config/systemd/user/rig-board.service
sed -i "s|__BOARD_PY__|<absolute path to this skill's scripts/board.py>|" ~/.config/systemd/user/rig-board.service
systemctl --user daemon-reload && systemctl --user enable --now rig-board.service
loginctl enable-linger "$USER"     # keep it running with no login session
systemctl --user status rig-board.service --no-pager
```

Then open `http://<hub LAN address>:8765` (or the hub's private overlay address). The page is
read-only and unauthenticated; keep the port off the public internet.

Point the unit at a **stable** copy of `board.py` (for example the currently installed skill
path) and re-point it after a skill release that changes the script.

## Environment overrides

- `RIGSYNC_REGISTRY` or `--registry`: alternate registry (tests use a temporary one).
- `serve --port/--bind/--reconcile-every`: override the registry values for one process.
