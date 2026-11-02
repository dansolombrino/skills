# behemoth-gpu-niceness setup

## Registry `[niceness]` table

Read from the machines registry `~/.config/rigsync/machines.toml` on the hub machine
(`RIGSYNC_REGISTRY` or `--registry` overrides). Every key is optional except the table itself.

```toml
[niceness]
machine          = "behemoth"     # a [machines.<name>] entry; its ssh alias is probed
port             = 40977          # loopback port the page listens on
bind             = "127.0.0.1"    # must be loopback; the hub is the only way in
every            = 60             # seconds between samples (>= 10)
retention_days   = 400            # samples older than this are deleted; 0 keeps everything
db               = "~/.local/share/behemoth-gpu-niceness/samples.sqlite"
title            = "behemoth GPU niceness"
```

The sampler needs non-interactive `ssh <alias>` to the machine (`BatchMode=yes`), and `ps` there
must show other users' processes (no `hidepid`).

## Install as a user service

```bash
mkdir -p ~/.config/systemd/user && cp <skill>/assets/behemoth-gpu-niceness.service ~/.config/systemd/user/
sed -i "s|__NICENESS_PY__|<absolute path to this skill's scripts/niceness.py>|" ~/.config/systemd/user/behemoth-gpu-niceness.service
systemctl --user daemon-reload && systemctl --user enable --now behemoth-gpu-niceness.service
curl -s 'http://127.0.0.1:40977/api/stats?window=24h' | head -c 300
```

Point the unit at a stable copy of the script (the installed skill path, or a checkout of this
repository). The page assets are read from `assets/` next to `scripts/` on every request.

## Put it behind service-hub and share it

```toml
[hub.services.niceness]
name        = "GPU niceness"
description = "Who used the most of behemoth's GPU time."
icon        = "😇"
url         = "http://127.0.0.1:40977"

[hub.guests.behemoth-users]     # what other people get: this service only, read-only
token    = "<output of hub.py token>"
services = ["niceness"]
```

Restart the hub (`systemctl --user restart service-hub.service`), then print the link to share
with `hub.py share behemoth-users`. The first visit trades the token in the URL for an HttpOnly
cookie and removes it from the address bar. The guest token opens nothing but `/niceness/`.

The token travels in clear over plain HTTP, like the hub's own: share the link over a private
channel, and rotate it (new `token`, restart the hub) whenever it may have leaked or someone
should lose access. Every guest request is logged with `auth=guest:<name>` in the hub journal.

## Troubleshooting

- **Page says stale:** `journalctl --user -u behemoth-gpu-niceness -n 20`; an `ssh timed out`
  or `ssh failed` line is the network path to the machine, not this service.
- **A user is missing:** their processes run in another PID namespace; their memory shows as
  `(unattributed)`.
- **`sample --dry-run` lists every GPU free while `nvidia-smi` shows memory:** `ps` cannot see
  the processes (`hidepid` on `/proc`).
