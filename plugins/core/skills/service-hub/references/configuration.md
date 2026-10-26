# service-hub configuration

## Registry `[hub]` table

The hub reads the `[hub]` table of the machines registry `~/.config/rigsync/machines.toml` (per
user, not committed; `RIGSYNC_REGISTRY` or `--registry` overrides the path) on the machine that
runs it.

```toml
[hub]
port       = 40970                        # default 40970
bind       = "0.0.0.0"                    # default 127.0.0.1 (this machine only)
token      = "<output of hub.py token>"   # required whenever bind is not loopback
title      = "rig-4090"                   # landing-page title; default: the hostname
public_url = "http://<public address>:40970"   # optional; `url` also prints the public URL

[hub.services.board]                      # served at /board/
name        = "Rig board"
description = "GPU lanes across the fleet, the Slurm queue, and every supervised wave."
icon        = "🖥️"                         # an emoji or a short text; default: the name's initial
url         = "http://127.0.0.1:40971"    # the backend, on loopback
health      = "/healthz"                  # probed for the up/down dot; default /healthz, "" disables

[hub.services.ide]                        # link-only: a card that opens its own URL
name = "IDE"
link = "http://<address>:6769"
```

- Services appear on the page in registry order.
- A service name is lowercase letters, digits and `-`, at most 32 characters, and not one of the
  hub's own paths (`api`, `healthz`, `hub`, `login`, `index.html`, `favicon.ico`).
- Each service sets exactly one of `url` (a proxied `http://host:port` backend with no path) or
  `link` (any `http(s)://` URL, opened in a new tab; its dot shows whether the host's port accepts a
  connection).
- A health answer below HTTP 500 counts as up, so a backend that answers 401 is up.

## Backend contract

A backend serves correctly under `/<service>/` when:

- its pages use **relative** URLs for their own requests (`fetch("api/x")`, `src="app.js"`), not
  root-absolute ones (`fetch("/api/x")`), so they resolve under the prefix;
- its redirects use root-absolute or relative `Location` values (the hub prefixes root-absolute
  ones and rewrites its own absolute backend URL);
- it listens on `127.0.0.1` once behind the hub. It sees every request from `127.0.0.1`, with
  `X-Forwarded-For` (the real client), `X-Forwarded-Host`, `X-Forwarded-Proto` and
  `X-Forwarded-Prefix` (`/<service>`) set.

Single-page apps that hard-code root-absolute asset paths usually break under a prefix; list them
as `link` services.

## Install as a user service

`assets/service-hub.service` is a systemd user unit template. Point it at a **stable** copy of
the script (the installed skill path, or a checkout of this repository on the machine):

```bash
python3 <skill>/scripts/hub.py token      # paste the output into [hub] token
mkdir -p ~/.config/systemd/user && cp <skill>/assets/service-hub.service ~/.config/systemd/user/service-hub.service
sed -i "s|__HUB_PY__|<absolute path to this skill's scripts/hub.py>|" ~/.config/systemd/user/service-hub.service
systemctl --user daemon-reload && systemctl --user enable --now service-hub.service
loginctl enable-linger "$USER"            # keep it running with no login session
python3 <skill>/scripts/hub.py status
```

Open `http://<address>:<port>/?token=<token>` once per browser; later visits need nothing. Restart
the service after editing `[hub]`: `systemctl --user restart service-hub.service`. The page
(`assets/hub.html`) is read on every request, so keep it next to `scripts/`.

## Access log

Each request is one stderr line, in the journal of the unit:
`<client> <method> <path> <status> auth=<loopback|token|missing|REJECTED|open>`. The query
string is never logged, so a `?token=` never lands there.

```bash
journalctl --user -u service-hub -f
journalctl --user -u service-hub | grep -E 'auth=(REJECTED|missing)'   # refused network requests
```

## Troubleshooting

- **Card says down, `connection refused`:** the backend is not running or listens on another
  port; check its own unit, then `url` in its table.
- **Page loads but its data does not, or assets 404:** the backend uses root-absolute URLs; make
  them relative or turn the service into a `link`.
- **502 on a page:** the backend closed the connection or timed out (five seconds to connect,
  five minutes per read).
- **Port already in use at start:** another service still holds the hub port; move it to its
  loopback port first.
