# rig-board configuration

## Registry `[board]` table

The board is declared once, in the rigsync user registry `~/.config/rigsync/machines.toml`
(not committed, per user). Every skill discovers the board through it; no path is hard-coded.

```toml
[board]
root   = "/mnt/<big volume>/Resources/rig_board"    # outside every project tree, on the big volume
rigs   = ["rig-4090", "rig-3090-ti", "rig-3080-ti", "behemoth"]  # tmux/GPU machines only
shared = ["behemoth"]                                # cards used by other people show as "foreign"
slurm  = ["leonardo"]                                # Slurm targets: queue shown read-only, never lanes
port   = 8765                                        # viewer
bind   = "0.0.0.0"                                   # all interfaces: LAN and the private overlay
token  = "<output of board.py token>"                # optional; required for any exposure beyond the LAN
reconcile_every = 120                                # seconds between rig probes by `serve`; 0 disables, minimum 10
```

- `rigs` must name existing `[machines.<rig>]` entries; each needs `ssh`. The entry whose
  `hostname` matches the current machine is probed locally, without self-SSH.
- A Slurm target goes under `slurm`, never under `rigs`. The cluster has jobs, not lanes: the
  board shows `squeue --me` (running and pending jobs with reason, partition, node, elapsed and
  time left) plus `sacct` for the last three days (finished jobs as completed / failed /
  cancelled / timeout, so a sweep's total and its finished share are known) under
  `rigs/<name>.json` with `kind = "slurm"`, and nothing on it can be claimed.
  A job leaves the card when it leaves the queue. Job names that follow the lane session
  convention are split into project, experiment and wave. When the login node refuses the
  connection the card says why (typically an expired cluster certificate) and keeps the last
  known queue.
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

For each configured rig, one bounded probe (SSH with `BatchMode=yes`, ten-second connect
timeout, run in parallel across rigs): `tmux ls`; `nvidia-smi --query-gpu` (index, uuid, name,
total and used memory, utilization, temperature, power) and `--query-compute-apps` (per-process
pid, memory, executable) joined with `ps` for the owning user and process age; `/proc/loadavg`
and `nproc`; for every lane-looking tmux session (wave id and `gpu<ids>` suffix) its pane's
working directory, the last visible line of the pane (`tmux capture-pane`), and every
`.status.json` under `<working directory>/evaluations/` heartbeaten in the last 30 minutes (at
most 200); `uptime -s`. A rig whose `nvidia-smi` fails is stored with `gpu_probe_ok = false`
and an empty GPU list: its cards are unknown, never free. Rigs still answering the pre-5.8
three-column GPU form parse unchanged.

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
contain `_<three digits>_`. A live session that carries a wave id but does not parse (an old
naming convention, a hand-made session) is reported as a *stray session*: shown, never adopted.

Slurm queue diffs are folded when more than five jobs change the same way in one probe
(`leonardo: 140 jobs appeared (PENDING) [id…id]`), so an array submission is one history line.
Jobs are grouped for display: lane-convention names by project / experiment / wave, `<wave>__k=v,…`
sweep names by project and `<wave>` prefix (the tokens shared by every job become the group's
`common`, each job keeps only its `variant`), anything else by project and the name with a
trailing index stripped. For names that carry no project, the project is the basename of the
job's Slurm working directory (`squeue %Z`, `sacct WorkDir`, normally the project root on the
cluster) and the experiment is the `scripts/NNN_…/NNN_…` chain in the job's command path. Each job also
carries its Slurm account (the cluster project id that is charged) and QOS; the group and the
card list the accounts in use.

## Live run signal on a lane

`reconcile` matches the lane (wave id + GPU set) against the status files found under the
project its tmux pane sits in, prefers a `running` one and otherwise reports the latest finished
one (the lane is between runs), and stores under `observed.run`: the run path relative to
`evaluations/`, state, progress text and numbers, elapsed, heartbeat and its age, a
`heartbeat_stale` flag (running and no heartbeat for over 180 s: possible hang), and an `eta`
that is a linear extrapolation of the run's own progress (`eta_basis` says so). The pane's last
line lands in `observed.last_output`, raw. These are copies of the run's own signals and never
change what the orchestrator wrote (`active_run`, `progress`, `eta` remain its ten-minute
report). The bulky status list itself is not persisted in `rigs/<rig>.json`.

## Cluster budgets

The Slurm probe also runs `saldo -b -n` (CINECA's budget report). Each account row keeps its
validity window, total / consumed / monthly local hours, and gets `remaining_h`,
`month_remaining_h`, `days_left`, `in_use` (an account charged by a queued or recently finished
job) and a `status`: `ok`, `low` (≥ 90 % of the total or of the month), `expiring` (≤ 30 days),
`month_exhausted`, `exhausted`, `expired`. The monthly rule outranks expiry because it blocks
submissions first. Budgets of accounts in use that are not `ok` or `low` become
`summary.budget_alerts`, counted under the viewer's issues tile. `saldo` is recomputed by
CINECA overnight, so consumed hours lag by up to a day; the viewer says so in its footnotes and
shows local hours verbatim, never converted.

## History

`history.jsonl` keeps every event. `board.py history` and `/api/history` read it newest first,
within a window (`--hours`, default 24), dropping reconcile ticks that changed nothing, the lines
that duplicate an `adopt` / `release` / `interrupted` event, and the ten-minute orchestrator
`refresh` ticks unless asked for (`--refresh`, `?refresh=1`).

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
read-only.

### Access token

With `[board] token` set, every path except `/healthz` answers 401 unless the request carries
the token as `?token=<token>`, an `X-Board-Token` header, or `Authorization: Bearer <token>`.
Opening the page once with `?token=` sets an HttpOnly cookie for a year, so the bookmark is
`http://<address>:<port>/?token=<token>` and later visits need nothing. Generate one with
`board.py token` and restart the service after changing the registry. Without a token the
server prints `OPEN` at startup; never forward such a port from a router. The token travels in
clear over plain HTTP, so treat it as a shared secret for your own devices, and rotate it by
replacing the registry value.

Point the unit at a **stable** copy of `board.py` (for example the currently installed skill
path) and re-point it after a skill release that changes the script. The page is
`assets/viewer.html` next to `scripts/`, read on every request, so keep the two together;
without it the server still answers `/api/board` and says the page is missing.

The viewer keeps its own preferences (filter chips, free-first sort, theme, notifications,
history panel, refresh period) in the browser's local storage; nothing is written on the hub.

### Two timers

- **Page refresh** (`refresh:` selector in the toolbar, 1 s to 5 min, default 5 s; `?every=<s>`
  in the URL sets it for one visit). Each refresh is one `/api/board` request that reads the
  JSON already on disk under `root`. It is cheap at any setting, but it only makes ages,
  countdowns and ETAs tick more smoothly: the facts change only when the server probes.
- **Rig probe** (`[board] reconcile_every`, or `serve --reconcile-every`; default 120 s,
  minimum 10 s, 0 disables). Each tick opens an SSH session to every rig (nvidia-smi, tmux,
  status files) and to every Slurm login node (`squeue`, `sacct`, `saldo`), then waits the
  configured time before the next one. Values below the minimum are refused: a 1 s tick would
  keep an SSH session permanently open to each machine, spam their auth logs, and hammer the
  cluster's shared login node and accounting database, which CINECA treats as abuse.

## Environment overrides

- `RIGSYNC_REGISTRY` or `--registry`: alternate registry (tests use a temporary one).
- `serve --port/--bind/--reconcile-every`: override the registry values (`port`, `bind`,
  `reconcile_every`) for one process.
