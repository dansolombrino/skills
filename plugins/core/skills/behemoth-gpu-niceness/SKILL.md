---
name: behemoth-gpu-niceness
description: Run and share the niceness counter for the shared behemoth GPU box - a sampler that records, every minute, which user holds which GPU, and a read-only page behind service-hub that ranks users by the GPU-hours they used, with each one's share of all GPU time, over 24 h, 7 d, 30 d or everything kept. Use when the user asks who used behemoth's GPUs the most, wants usage stats or a ranking across behemoth users, wants to share that page with other people, or to install, troubleshoot or reconfigure the sampler. Do not use for the user's own lane claims or wave progress (rig-board), or to change anyone's jobs.
---

# behemoth-gpu-niceness

`scripts/niceness.py` is a stdlib service on the hub machine. It probes the shared machine over ssh
every `every` seconds (`nvidia-smi` plus the owner of each GPU process), stores per sample and GPU
the utilization, memory, and the memory each user holds (SQLite), and serves a read-only page on
loopback that service-hub proxies.

Setup, configuration and the hub guest: [references/setup.md](references/setup.md).

## What it counts

- A user **uses** a GPU while one of their processes holds memory on it; a GPU shared by several
  users is split by the memory each holds. Each sample is credited with the time since the
  previous good one (capped at three periods), so outages are gaps, not guesses.
- The page ranks users by GPU-hours used in the window, shows each one's share of all the
  machine's GPU time (GPUs × time sampled), and the unused remainder. Memory in use with no
  visible process is `(unattributed)`, listed last and unranked.
- Windows: 24 h, 7 d, 30 d, all kept (`retention_days`, default 400).

## Commands

```bash
python3 scripts/niceness.py sample --dry-run        # probe once and print, store nothing
python3 scripts/niceness.py report [--window 7d] [--json]
python3 scripts/niceness.py serve [--no-sample]     # what the service runs
```

Answer usage questions from `report` (or `/api/stats?window=…`), not from a one-off
`nvidia-smi`. The page has no live view by design. Share the page only with `hub.py share <guest>` from `service-hub`.

## Safety contract

- Only usernames, GPU numbers, memory and utilization are ever stored: process names, command
  lines, paths and PIDs are dropped while parsing, so no page or API can leak them.
- The service binds loopback only (a non-loopback `bind` is refused), answers GET/HEAD only, and
  sends a strict Content-Security-Policy, `nosniff`, `no-referrer` and `frame-ancestors 'none'`.
  The page uses no external scripts or fonts and builds its DOM without parsing HTML.
- Other people get a **service-hub guest token** scoped to this one service: read-only, no
  landing page, no service switcher, and 404 on every other path. Never hand out the hub's own
  token. Revoke a guest by deleting its table and restarting the hub.
- The page reports; it never contacts users or touches their processes.
